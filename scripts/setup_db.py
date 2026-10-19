"""Create/refresh the quiz's roles, app schema and grants. Idempotent.

The quiz data is the Olist tables in schema `public` (loaded by load_olist.py)
(customers, orders, order_items, products). This script NEVER creates,
alters, truncates or deletes them; it only grants SELECT on them to
`quiz_taker` (plus a SELECT-only RLS policy for that role if RLS is on).

Reads (from the environment — GitHub Secrets in CI, .env locally):
  DATABASE_URL         admin connection (Supabase *Session pooler* URI,
                       user postgres.<project-ref>, ?sslmode=require)
  QUIZ_TAKER_PASSWORD  password to set for role quiz_taker
  QUIZ_APP_PASSWORD    password to set for role quiz_app

Everything runs in ONE transaction, so a failure leaves the database unchanged.
"""
import os
import pathlib
import sys

import psycopg
from psycopg import sql

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from quiz.questions import QUIZ_TABLES, REQUIRED_COLUMNS  # noqa: E402

SQL_FILES = ["01_app_schema.sql", "02_grants.sql"]

try:
    from dotenv import load_dotenv  # optional, for local runs
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def need(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        sys.exit(f"Missing environment variable {name}")
    return v


def preflight(cur) -> None:
    """Make sure the Olist tables/columns the quiz needs are present."""
    cur.execute(
        """SELECT table_name, column_name, data_type
           FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = ANY(%s)""",
        (list(QUIZ_TABLES),),
    )
    found: dict = {}
    for table, col, dtype in cur.fetchall():
        found.setdefault(table, {})[col] = dtype

    problems = []
    for table, cols in REQUIRED_COLUMNS.items():
        if table not in found:
            problems.append(f"table public.{table} is missing")
            continue
        missing = [c for c in cols if c not in found[table]]
        if missing:
            problems.append(f"public.{table} lacks column(s) {', '.join(missing)}; "
                            f"has: {', '.join(sorted(found[table]))}")
    if problems:
        print("Olist tables are not what the quiz expects:")
        for p in problems:
            print("  -", p)
        sys.exit("Run scripts/load_olist.py first. If a table with one of these names "
                 "already existed with different columns, drop it and re-run.")

    for table in QUIZ_TABLES:
        cur.execute(sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(table)))
        n = cur.fetchone()[0]
        types = ", ".join(f"{c}:{found[table][c]}" for c in REQUIRED_COLUMNS[table][:3])
        print(f"  public.{table}: {n:,} rows ({types})")


def ensure_role(cur, role: str, password: str) -> None:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    verb = "ALTER" if cur.fetchone() else "CREATE"
    cur.execute(
        sql.SQL("{} ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOINHERIT NOBYPASSRLS PASSWORD {}").format(
            sql.SQL(verb), sql.Identifier(role), sql.Literal(password))
    )
    print(f"  role {role}: {'updated' if verb == 'ALTER' else 'created'}")


def rls_read_policies(cur) -> None:
    """If RLS is enabled on an Olist table, quiz_taker would see 0 rows.
    Add a SELECT-only policy for quiz_taker (and nobody else)."""
    for table in QUIZ_TABLES:
        cur.execute(
            """SELECT c.relrowsecurity
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND c.relname = %s""",
            (table,),
        )
        if not cur.fetchone()[0]:
            continue
        cur.execute(
            "SELECT 1 FROM pg_policies WHERE schemaname = 'public' "
            "AND tablename = %s AND policyname = 'quiz_taker_read'",
            (table,),
        )
        if cur.fetchone():
            print(f"  public.{table}: RLS on, policy quiz_taker_read already present")
            continue
        cur.execute(sql.SQL(
            "CREATE POLICY quiz_taker_read ON public.{} "
            "FOR SELECT TO quiz_taker USING (true)").format(sql.Identifier(table)))
        print(f"  public.{table}: RLS on, added SELECT-only policy for quiz_taker")


def main() -> None:
    admin_url = need("DATABASE_URL")
    taker_pw = need("QUIZ_TAKER_PASSWORD")
    app_pw = need("QUIZ_APP_PASSWORD")

    with psycopg.connect(admin_url, prepare_threshold=None, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, version()")
            user, version = cur.fetchone()
            print(f"Connected as {user}: {version.split(',')[0]}")
            preflight(cur)
            ensure_role(cur, "quiz_taker", taker_pw)
            ensure_role(cur, "quiz_app", app_pw)
            for name in SQL_FILES:
                cur.execute((ROOT / "sql" / name).read_text())
                print(f"  applied sql/{name}")
            rls_read_policies(cur)
        # leaving the `with` block commits; any exception rolls back
    print("Setup complete.")


if __name__ == "__main__":
    main()
