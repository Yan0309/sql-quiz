"""Generate every secret value this project needs — on YOUR computer.

    python3 scripts/make_secrets.py

It asks for (input hidden):
  * your Supabase database password
  * your Supabase publishable key (sb_publishable_...)
then it
  * generates random passwords for roles quiz_taker and quiz_app
  * URL-encodes everything correctly
  * tests the database connection (if psycopg is installed)
  * writes .env and .streamlit/secrets.toml (both git-ignored)
  * optionally uploads the 3 GitHub Actions secrets with the `gh` CLI

Nothing is sent anywhere except to Supabase (connection test) and, if you
say yes, to GitHub (secrets upload).
"""
import getpass
import pathlib
import secrets
import shutil
import subprocess
import sys
from urllib.parse import quote

ROOT = pathlib.Path(__file__).resolve().parents[1]

# ---- your project (from the Supabase "Connect → Direct → Session pooler" box)
PROJECT_REF = "qdgcuhfgrbpaftbtjedw"
POOLER_HOST = "aws-0-us-east-1.pooler.supabase.com"


def url(role: str, password: str) -> str:
    return (f"postgresql://{role}.{PROJECT_REF}:{quote(password, safe='')}"
            f"@{POOLER_HOST}:5432/postgres?sslmode=require")


def ask(prompt: str, check) -> str:
    while True:
        value = getpass.getpass(prompt).strip()
        problem = check(value)
        if not problem:
            return value
        print("  ✗", problem)


def test_connection(db_url: str) -> bool:
    try:
        import psycopg
    except ImportError:
        print("  (psycopg not installed — skipping connection test; "
              "`pip install -r requirements.txt` to enable it)")
        return True
    try:
        with psycopg.connect(db_url, connect_timeout=15) as conn:
            v = conn.execute("select current_user").fetchone()[0]
        print(f"  ✓ connected to Supabase as {v}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ could not connect: {str(e).strip().splitlines()[0]}")
        return False


def main() -> None:
    print(f"Supabase project: {PROJECT_REF}\n")
    db_pw = ask("Database password (hidden): ",
                lambda v: None if v else "empty")
    key = ask("Publishable key, sb_publishable_... (hidden): ",
              lambda v: None if v.startswith(("sb_publishable_", "eyJ"))
              else "should start with sb_publishable_ (never use the secret key)")

    database_url = url("postgres", db_pw)
    print("\nTesting the database password…")
    if not test_connection(database_url):
        sys.exit("Fix the password (Supabase → Project Settings → Database → "
                 "Reset database password) and run this again.")

    env_path = ROOT / ".env"
    if env_path.exists():
        # keep the role passwords stable if this script was run before
        old = dict(l.split("=", 1) for l in env_path.read_text().splitlines() if "=" in l)
        taker_pw = old.get("QUIZ_TAKER_PASSWORD") or secrets.token_urlsafe(24)
        app_pw = old.get("QUIZ_APP_PASSWORD") or secrets.token_urlsafe(24)
    else:
        taker_pw, app_pw = secrets.token_urlsafe(24), secrets.token_urlsafe(24)

    values = {
        "DATABASE_URL": database_url,
        "QUIZ_TAKER_PASSWORD": taker_pw,
        "QUIZ_APP_PASSWORD": app_pw,
        "SUPABASE_URL": f"https://{PROJECT_REF}.supabase.co",
        "SUPABASE_KEY": key,
        "TAKER_DATABASE_URL": url("quiz_taker", taker_pw),
        "APP_DATABASE_URL": url("quiz_app", app_pw),
    }

    env_path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    st_path = ROOT / ".streamlit" / "secrets.toml"
    st_path.parent.mkdir(exist_ok=True)
    st_keys = ("SUPABASE_URL", "SUPABASE_KEY", "TAKER_DATABASE_URL", "APP_DATABASE_URL")
    st_path.write_text("".join(f'{k} = "{values[k]}"\n' for k in st_keys))
    for p in (env_path, st_path):
        p.chmod(0o600)
    print(f"\n✓ wrote {env_path.relative_to(ROOT)} and {st_path.relative_to(ROOT)} "
          "(both git-ignored, readable only by you)")

    gh_keys = ("DATABASE_URL", "QUIZ_TAKER_PASSWORD", "QUIZ_APP_PASSWORD")
    if shutil.which("gh"):
        if input("\nUpload the 3 GitHub Actions secrets now with `gh`? [y/N] ").lower() == "y":
            for k in gh_keys:
                # value goes via stdin, never on the command line
                subprocess.run(["gh", "secret", "set", k], input=values[k],
                               text=True, check=True, cwd=ROOT)
                print(f"  ✓ GitHub secret {k} set")
    else:
        print("\n(`gh` CLI not found — add the 3 GitHub secrets by hand; "
              "copy the values from .env:", ", ".join(gh_keys) + ")")

    print("\nNext:\n"
          "  • GitHub → Actions → 'Database setup & quiz check' → Run workflow\n"
          "  • Streamlit Cloud → app Settings → Secrets: paste the contents of "
          ".streamlit/secrets.toml")


if __name__ == "__main__":
    main()
