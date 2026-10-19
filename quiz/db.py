"""Database access for the quiz app.

Two separate Postgres logins, on purpose:

* TAKER_DATABASE_URL -> role `quiz_taker`: runs whatever SQL the student
  types. That SQL cannot be parameterised (it *is* the query), so safety
  comes from the role: SELECT on the four Olist tables only, read-only
  transaction, 10 s timeout, always rolled back. It has no privileges on schema `app`,
  so it cannot see or touch the audit log.
* APP_DATABASE_URL -> role `quiz_app`: writes submissions and audit rows.
  Every one of those statements uses bound parameters (%s), never
  f-strings.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import psycopg
import psycopg.conninfo

MAX_SQL_CHARS = 5000
MAX_ROWS = 500
_LEADING_COMMENTS = re.compile(r"^\s*(?:--[^\n]*\n\s*|/\*.*?\*/\s*)*", re.S)


@dataclass
class QueryResult:
    ok: bool
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    truncated: bool = False
    error: str | None = None


def _connect(url: str, **kw) -> psycopg.Connection:
    # prepare_threshold=None: never create server-side prepared statements
    # (safe with any pooler mode). connect_timeout keeps the UI responsive.
    return psycopg.connect(url, prepare_threshold=None, connect_timeout=10, **kw)


def clean_sql(text: str) -> str:
    s = (text or "").strip()
    while s.endswith(";"):
        s = s[:-1].rstrip()
    return s


def precheck(text: str) -> str | None:
    """Friendly validation. NOT the security boundary — the role is."""
    s = clean_sql(text)
    if not s:
        return "Type a query first."
    if len(s) > MAX_SQL_CHARS:
        return f"Query is longer than {MAX_SQL_CHARS} characters."
    first = _LEADING_COMMENTS.sub("", s).split(None, 1)
    if not first or first[0].lower() not in ("select", "with", "values", "table"):
        return "Only read queries are allowed (SELECT / WITH ... SELECT)."
    return None


def run_taker_query(taker_url: str, text: str, max_rows: int = MAX_ROWS) -> QueryResult:
    problem = precheck(text)
    if problem:
        return QueryResult(ok=False, error=problem)
    query = clean_sql(text)
    try:
        with _connect(taker_url) as conn:
            conn.read_only = True          # BEGIN READ ONLY
            with conn.cursor() as setup:
                # Belt and braces: the role already has these as defaults.
                setup.execute("SET LOCAL statement_timeout = '10s'")
                setup.execute("SET LOCAL search_path = public")
            # A *server-side* cursor: psycopg sends
            #   DECLARE quiz_cursor CURSOR FOR <student SQL>
            # which (1) only accepts a single SELECT / VALUES / TABLE query —
            # the server rejects ';'-chained or write statements — and
            # (2) lets us fetch just the first rows, so
            # `SELECT * FROM order_items` (112k rows) doesn't flood memory.
            # Student SQL is sent as-is with no parameters.
            with conn.cursor(name="quiz_cursor") as cur:
                cur.execute(query)
                cols = [d.name for d in cur.description]
                rows = cur.fetchmany(max_rows + 1)
            conn.rollback()                # never keep anything
    except psycopg.Error as e:
        msg = (e.diag.message_primary if getattr(e, "diag", None) and e.diag.message_primary
               else str(e)).strip()
        return QueryResult(ok=False, error=msg)
    truncated = len(rows) > max_rows
    return QueryResult(ok=True, columns=cols, rows=[tuple(r) for r in rows[:max_rows]],
                       truncated=truncated)


# ----------------------------------------------------------------- app role
def log_event(app_url: str, *, user_id, user_email, action, question_id=None,
              sql_text=None, row_count=None, success=None, detail=None) -> None:
    with _connect(app_url) as conn:
        conn.execute(
            """INSERT INTO app.audit_log
                   (user_id, user_email, action, question_id, sql_text,
                    row_count, success, detail)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            # chain_seq, occurred_at, prev_hash, row_hash are set by the
            # BEFORE INSERT trigger (NOT NULL is checked after it runs).
            (user_id, user_email, action, question_id, sql_text,
             row_count, success, detail),
        )


def save_submission(app_url: str, *, user_id, user_email, answers: dict, score: int) -> int:
    with _connect(app_url) as conn:
        row = conn.execute(
            """INSERT INTO app.quiz_submissions (user_id, user_email, answers, score)
               VALUES (%s, %s, %s::jsonb, %s)
               RETURNING submission_id""",
            (user_id, user_email, json.dumps(answers), score),
        ).fetchone()
    return row[0]


def my_submissions(app_url: str, user_id, limit: int = 10) -> list:
    with _connect(app_url) as conn:
        return conn.execute(
            """SELECT submission_id, score, saved_at
               FROM app.quiz_submissions
               WHERE user_id = %s
               ORDER BY saved_at DESC
               LIMIT %s""",
            (user_id, limit),
        ).fetchall()


def my_recent_activity(app_url: str, user_id, limit: int = 15) -> list:
    with _connect(app_url) as conn:
        return conn.execute(
            """SELECT occurred_at, action, question_id, success, row_count
               FROM app.audit_log
               WHERE user_id = %s
               ORDER BY chain_seq DESC
               LIMIT %s""",
            (user_id, limit),
        ).fetchall()


def verify_audit_chain(app_url: str) -> tuple:
    with _connect(app_url) as conn:
        return conn.execute("SELECT * FROM app.verify_audit_chain()").fetchone()


# ------------------------------------------------------------- url helpers
def role_url(admin_url: str, role: str, password: str) -> str:
    """Derive a URL for `role` from the admin URL, keeping host/port/options.

    Supabase pooler usernames look like `postgres.<project-ref>`; for a
    custom role it becomes `<role>.<project-ref>`. Plain Postgres: `<role>`.
    """
    info = psycopg.conninfo.conninfo_to_dict(admin_url)
    user = info.get("user", "postgres")
    suffix = user.split(".", 1)[1] if "." in user else None
    info["user"] = f"{role}.{suffix}" if suffix else role
    info["password"] = password
    return psycopg.conninfo.make_conninfo(**info)


__all__ = ["QueryResult", "run_taker_query", "log_event", "save_submission",
           "my_submissions", "my_recent_activity", "verify_audit_chain",
           "role_url", "precheck", "clean_sql"]
