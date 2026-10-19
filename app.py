"""Olist E-commerce SQL Quiz — Streamlit front end.

Run locally:   streamlit run app.py
Config comes from environment variables (.env) or Streamlit secrets:
  SUPABASE_URL, SUPABASE_KEY            -> Supabase Auth (publishable/anon key)
  TAKER_DATABASE_URL, APP_DATABASE_URL  -> Postgres via the Session pooler
"""
from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import pandas as pd
import streamlit as st
from supabase import create_client

from quiz import db
from quiz.grading import grade
from quiz.questions import QUESTIONS, QUIZ_TITLE, SCHEMA_REFERENCE, TOTAL_POINTS

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

st.set_page_config(page_title=QUIZ_TITLE, page_icon="🛒", layout="wide")


# ------------------------------------------------------------------ config
def cfg(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        try:
            val = st.secrets.get(name)
        except Exception:  # no secrets.toml at all
            val = None
    if not val:
        st.error(f"Server is missing configuration value `{name}`. See README → Deploy.")
        st.stop()
    return val


SUPABASE_URL = cfg("SUPABASE_URL")
SUPABASE_KEY = cfg("SUPABASE_KEY")
TAKER_URL = cfg("TAKER_DATABASE_URL")
APP_URL = cfg("APP_DATABASE_URL")


# ------------------------------------------------------------------- auth
def auth_client():
    # One client PER BROWSER SESSION. Never cache it globally
    # (st.cache_resource), or every visitor would share one login.
    if "sb" not in st.session_state:
        st.session_state.sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    return st.session_state.sb


def current_user() -> dict | None:
    return st.session_state.get("user")


def audit(**kw) -> None:
    """Best-effort audit write; a logging hiccup must not break the quiz."""
    user = current_user()
    try:
        db.log_event(APP_URL, user_id=user["id"] if user else None,
                     user_email=user["email"] if user else None, **kw)
    except Exception as e:  # noqa: BLE001
        st.toast(f"Audit log write failed: {e}", icon="⚠️")


def sign_in(email: str, password: str) -> None:
    try:
        res = auth_client().auth.sign_in_with_password({"email": email, "password": password})
    except Exception as e:  # supabase_auth.errors.AuthApiError and friends
        st.sidebar.error(f"Sign-in failed: {getattr(e, 'message', e)}")
        return
    st.session_state.user = {"id": str(res.user.id), "email": res.user.email}
    audit(action="sign_in", success=True)
    st.rerun()


def sign_up(email: str, password: str) -> None:
    try:
        res = auth_client().auth.sign_up({"email": email, "password": password})
    except Exception as e:
        st.sidebar.error(f"Sign-up failed: {getattr(e, 'message', e)}")
        return
    if res.session is None:
        st.sidebar.success("Account created. Check your email to confirm it, then sign in.")
    else:
        st.session_state.user = {"id": str(res.user.id), "email": res.user.email}
        audit(action="sign_in", success=True, detail="after sign-up")
        st.rerun()


def sign_out() -> None:
    audit(action="sign_out", success=True)
    try:
        auth_client().auth.sign_out()
    except Exception:
        pass
    for k in ("user", "sb"):
        st.session_state.pop(k, None)
    # on_click callbacks trigger a rerun automatically


# ---------------------------------------------------------------- helpers
def to_frame(columns, rows) -> pd.DataFrame:
    seen, cols = {}, []
    for c in columns:                       # SELECT 1, 1 -> two "?column?"
        seen[c] = seen.get(c, 0) + 1
        cols.append(c if seen[c] == 1 else f"{c}_{seen[c]}")

    def disp(v):
        if v is None or isinstance(v, (bool, int, float, str, dt.date, dt.datetime)):
            return v
        if isinstance(v, Decimal):
            return float(v)
        return str(v)                       # intervals, uuids, json, arrays…

    return pd.DataFrame([[disp(v) for v in r] for r in rows], columns=cols)


# Streamlit deletes a widget's state when the widget isn't rendered (e.g.
# after switching to another question), so answers live in their own dict.
st.session_state.setdefault("answers", {})


def remember_answer(qid: str) -> None:
    st.session_state.answers[qid] = st.session_state.get(f"sql_{qid}", "")


def answer_for(qid: str) -> str:
    return st.session_state.answers.get(qid, "")


def run_for_question(q) -> None:
    remember_answer(q.qid)
    user = current_user()
    if not user:                            # server-side guard, not just a greyed button
        st.error("Sign in to run queries.")
        return
    text = answer_for(q.qid)
    res = db.run_taker_query(TAKER_URL, text)
    verdict = grade(q, res.columns, res.rows, res.truncated) if res.ok else (False, res.error)
    st.session_state[f"res_{q.qid}"] = (res, verdict)
    audit(action="run_query", question_id=q.qid, sql_text=db.clean_sql(text)[:5000],
          row_count=len(res.rows) if res.ok else None, success=res.ok,
          detail=None if res.ok else res.error)


def save_quiz() -> None:
    user = current_user()
    if not user:
        st.error("Sign in to save your quiz.")
        return
    answers, score = {}, 0
    for q in QUESTIONS:
        text = db.clean_sql(answer_for(q.qid))
        correct = False
        if text:                            # re-run so the saved score matches the saved SQL
            res = db.run_taker_query(TAKER_URL, text)
            correct = res.ok and grade(q, res.columns, res.rows, res.truncated)[0]
        pts = q.points if correct else 0
        score += pts
        answers[q.qid] = {"sql": text, "correct": correct, "points": pts}
    try:
        sid = db.save_submission(APP_URL, user_id=user["id"], user_email=user["email"],
                                 answers=answers, score=score)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not save: {e}")
        return
    audit(action="save_quiz", success=True, detail=f"submission {sid}, score {score}/{TOTAL_POINTS}")
    st.session_state.last_save = (sid, score)


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Account")
    user = current_user()
    if user:
        st.success(f"Signed in as **{user['email']}**")
        st.button("Sign out", on_click=sign_out, width="stretch")
    else:
        tab_in, tab_up = st.tabs(["Sign in", "Create account"])
        with tab_in, st.form("signin"):
            em = st.text_input("Email")
            pw = st.text_input("Password", type="password")
            if st.form_submit_button("Sign in", width="stretch"):
                sign_in(em.strip(), pw)
        with tab_up, st.form("signup"):
            em2 = st.text_input("Email", key="su_email")
            pw2 = st.text_input("Password (min 6 chars)", type="password", key="su_pw")
            if st.form_submit_button("Create account", width="stretch"):
                sign_up(em2.strip(), pw2)

    if user:
        st.divider()
        st.subheader("My saved attempts")
        try:
            subs = db.my_submissions(APP_URL, user["id"])
            if subs:
                st.dataframe(pd.DataFrame(subs, columns=["#", "score", "saved at"]),
                             hide_index=True, width="stretch")
            else:
                st.caption("Nothing saved yet.")
        except Exception as e:  # noqa: BLE001
            st.caption(f"Couldn't load: {e}")

        with st.expander("My activity (audit log)"):
            try:
                acts = db.my_recent_activity(APP_URL, user["id"])
                st.dataframe(pd.DataFrame(acts, columns=["when", "action", "question", "ok", "rows"]),
                             hide_index=True, width="stretch")
                ok, n, bad = db.verify_audit_chain(APP_URL)
                if ok:
                    st.caption(f"🔒 Hash chain verified over {n} log rows.")
                else:
                    st.error(f"Audit chain broken at row {bad}!")
            except Exception as e:  # noqa: BLE001
                st.caption(f"Couldn't load: {e}")


# ------------------------------------------------------------------- main
st.title(f"🛒 {QUIZ_TITLE}")
st.caption(f"{len(QUESTIONS)} questions · {TOTAL_POINTS} points · live PostgreSQL data. "
           "You are graded on the result set, not on matching the hint's SQL text.")

if not current_user():
    st.info("👋 You can read the questions, but **sign in** (left sidebar) to run SQL and save your quiz.")

with st.expander("Tables (Olist e-commerce dataset, schema `public`)", expanded=False):
    st.code(SCHEMA_REFERENCE, language="text")
    st.caption("Queries run read-only, time out after 10 s, and show at most 500 rows "
               "(e.g. `SELECT * FROM orders` shows the first 500 of 99,441).")

labels = {q.qid: f"Q{i} · {q.title} ({q.points} pts)" for i, q in enumerate(QUESTIONS, 1)}
qid = st.radio("Question", list(labels), format_func=labels.get, horizontal=True,
               label_visibility="collapsed")
q = next(x for x in QUESTIONS if x.qid == qid)

st.subheader(labels[qid])
st.markdown(q.prompt)

signed_in = current_user() is not None
if f"sql_{q.qid}" not in st.session_state:          # restore after switching questions
    st.session_state[f"sql_{q.qid}"] = answer_for(q.qid)
st.text_area("Your SQL", key=f"sql_{q.qid}", height=170,
             placeholder="SELECT ...", disabled=not signed_in,
             on_change=remember_answer, args=(q.qid,))
st.button("▶ Run query", type="primary", disabled=not signed_in,
          on_click=run_for_question, args=(q,))

if signed_in and f"res_{q.qid}" in st.session_state:
    res, (correct, why) = st.session_state[f"res_{q.qid}"]
    if res.ok:
        st.dataframe(to_frame(res.columns, res.rows), hide_index=True, width="stretch")
        st.caption("Showing the first 500 rows (the query returned more)." if res.truncated
                   else f"{len(res.rows)} row(s)")
        (st.success if correct else st.warning)(("✅ " if correct else "❌ ") + why)
    else:
        st.error(f"Query error: {res.error}")

with st.expander("💡 Hint — expected SQL and expected result"):
    st.code(q.hint_sql, language="sql")
    st.dataframe(pd.DataFrame(list(q.expected_rows), columns=list(q.expected_columns)),
                 hide_index=True, width="stretch")
    st.caption("Row order matters for this question." if q.ordered
               else "Row order does not matter for this question.")

st.divider()
st.subheader("Save your quiz")
st.caption("Saving re-runs every answer you've typed, scores it, and stores the result.")
st.button("💾 Save quiz", disabled=not signed_in, on_click=save_quiz)
if "last_save" in st.session_state:
    sid, score = st.session_state.last_save
    st.success(f"Saved as submission #{sid} — score **{score} / {TOTAL_POINTS}**.")
