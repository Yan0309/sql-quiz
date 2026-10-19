"""End-to-end checks against the live database. Exits non-zero on failure.

Uses the same secrets as setup_db.py and derives the quiz_taker / quiz_app
connection URLs from DATABASE_URL (same host, pooler and sslmode).

Checks
  1. Points sum to 20.
  2. Every hint SQL, run as quiz_taker through the app's own query runner,
     returns exactly the expected result stored in quiz/questions.py.
  3. quiz_taker can read the Olist tables but cannot change them, and cannot
     read or write the audit log.
  4. quiz_app can append to the audit log but cannot UPDATE / DELETE it.
  5. The audit hash chain verifies.
"""
import os
import pathlib
import sys

import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quiz import db                         # noqa: E402
from quiz.grading import grade               # noqa: E402
from quiz.questions import QUESTIONS, TOTAL_POINTS  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

failures = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' — ' + detail if detail else ''}")
    if not ok:
        failures.append(name)


def expect_denied(url: str, statement: str) -> tuple:
    """Run a statement that MUST fail. Returns (failed_as_expected, message).

    The transaction is explicitly READ WRITE, so a denial proves the role
    lacks the privilege — not merely that the session default is read-only.
    """
    try:
        with psycopg.connect(url, prepare_threshold=None, connect_timeout=15) as conn:
            conn.read_only = False
            conn.execute(statement)
            conn.rollback()
        return False, "statement unexpectedly succeeded"
    except psycopg.Error as e:
        return True, (e.diag.message_primary or str(e)).strip()


def main() -> None:
    admin_url = os.environ["DATABASE_URL"]
    taker_url = db.role_url(admin_url, "quiz_taker", os.environ["QUIZ_TAKER_PASSWORD"])
    app_url = db.role_url(admin_url, "quiz_app", os.environ["QUIZ_APP_PASSWORD"])

    # 1
    check("points sum to 20", TOTAL_POINTS == 20, f"total = {TOTAL_POINTS}")

    # 2
    for q in QUESTIONS:
        res = db.run_taker_query(taker_url, q.hint_sql)
        if not res.ok:
            check(f"{q.qid} hint runs", False, res.error)
            continue
        ok, why = grade(q, res.columns, res.rows, res.truncated)
        check(f"{q.qid} hint matches expected result ({q.points} pts)", ok, why)
        if not ok:   # show what the live DB returned, so the hint can be fixed
            print("       expected:", list(q.expected_rows)[:10])
            print("       live DB :", res.rows[:10])

    # 3
    res = db.run_taker_query(taker_url, "SELECT * FROM order_items")
    check("big result is capped at 500 rows", res.ok and len(res.rows) == 500 and res.truncated,
          res.error or f"{len(res.rows)} rows shown, truncated={res.truncated}")

    for stmt in (
        "UPDATE public.orders SET order_status = order_status WHERE false",
        "DELETE FROM public.order_items WHERE false",
        "CREATE TABLE public.quiz_taker_was_here (x int)",
        "SELECT * FROM app.audit_log",
        "UPDATE app.audit_log SET detail = 'tampered'",
        "DELETE FROM app.audit_log",
    ):
        denied, msg = expect_denied(taker_url, stmt)
        check(f"quiz_taker denied: {stmt.split(' WHERE')[0][:45]}", denied, msg)

    res = db.run_taker_query(taker_url, "SELECT 1; SET statement_timeout = 0")
    check("multi-statement input rejected", not res.ok, res.error or "ran!")
    res = db.run_taker_query(taker_url, "SELECT pg_sleep(12)")
    check("long query cancelled by timeout", not res.ok, res.error or "ran!")

    # 4
    db.log_event(app_url, user_id=None, user_email="github-actions",
                 action="ci_check", detail=os.environ.get("GITHUB_RUN_ID", "local"))
    check("quiz_app can append to audit log", True)
    for stmt in ("UPDATE app.audit_log SET detail = 'tampered'",
                 "DELETE FROM app.audit_log",
                 "TRUNCATE app.audit_log"):
        denied, msg = expect_denied(app_url, stmt)
        check(f"quiz_app denied: {stmt}", denied, msg)

    # Even the admin/owner is stopped by the append-only trigger.
    denied, msg = expect_denied(admin_url, "UPDATE app.audit_log SET detail = 'tampered'")
    check("admin UPDATE blocked by append-only trigger", denied and "append-only" in msg, msg)

    # 5
    ok, n, bad = db.verify_audit_chain(app_url)
    check("audit hash chain intact", ok, f"{n} rows checked" + (f", first bad seq {bad}" if bad else ""))

    if failures:
        sys.exit(f"\n{len(failures)} check(s) failed.")
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
