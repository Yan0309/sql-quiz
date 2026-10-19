-- =====================================================================
-- 01_app_schema.sql  —  app tables, audit log, integrity triggers
-- Safe to re-run: every statement is idempotent.
-- Run as the admin role (`postgres` on Supabase) via scripts/setup_db.py.
-- =====================================================================

-- The quiz DATA is the Olist e-commerce tables in schema `public`
-- (loaded by scripts/load_olist.py). This file never creates,
-- alters or deletes them.
--
-- Everything the app writes goes into its own schema `app`, deliberately
-- NOT `public`: Supabase's Data API (PostgREST) exposes only `public` and
-- `graphql_public` by default, so nothing here is reachable with the
-- anon/publishable key.
CREATE SCHEMA IF NOT EXISTS app;

-- ---------------------------------------------------------------------
-- Saved quiz attempts (one row per "Save quiz" click)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.quiz_submissions (
    submission_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        uuid        NOT NULL,     -- Supabase Auth user id
    user_email     text        NOT NULL,
    answers        jsonb       NOT NULL,     -- {question_id: {sql, correct, points}}
    score          integer     NOT NULL CHECK (score BETWEEN 0 AND 20),
    saved_at       timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Audit log (extra credit): append-only + SHA-256 hash chain
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.audit_log (
    audit_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chain_seq    bigint      NOT NULL UNIQUE,   -- assigned by trigger, gap-free
    occurred_at  timestamptz NOT NULL,          -- forced to now() by trigger
    user_id      uuid,
    user_email   text,
    action       text        NOT NULL
                 CHECK (action IN ('sign_in','sign_out','run_query','save_quiz','ci_check')),
    question_id  text,
    sql_text     text,
    row_count    integer,
    success      boolean,
    detail       text,
    prev_hash    text        NOT NULL,
    row_hash     text        NOT NULL
);

-- Canonical text that gets hashed. jsonb's text form is deterministic,
-- and the timestamp is rendered in UTC so the session TimeZone setting
-- cannot change the hash.
CREATE OR REPLACE FUNCTION app.audit_payload(r app.audit_log)
RETURNS text
LANGUAGE sql IMMUTABLE
AS $$
    SELECT jsonb_build_array(
        r.chain_seq,
        to_char(r.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        r.user_id, r.user_email, r.action, r.question_id, r.sql_text,
        r.row_count, r.success, r.detail, r.prev_hash
    )::text
$$;

-- BEFORE INSERT: serialize writers, link to the previous row, compute hash.
-- Whatever the client sends for chain_seq / occurred_at / hashes is
-- overwritten, so a client cannot forge a chain position.
CREATE OR REPLACE FUNCTION app.audit_chain()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    last_seq  bigint;
    last_hash text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('app.audit_log.chain'));

    SELECT chain_seq, row_hash INTO last_seq, last_hash
    FROM app.audit_log
    ORDER BY chain_seq DESC
    LIMIT 1;

    NEW.chain_seq   := COALESCE(last_seq, 0) + 1;
    NEW.occurred_at := now();
    NEW.prev_hash   := COALESCE(last_hash, repeat('0', 64));
    NEW.row_hash    := '';
    NEW.row_hash    := encode(sha256(convert_to(app.audit_payload(NEW), 'UTF8')), 'hex');
    RETURN NEW;
END;
$$;

-- Any UPDATE, DELETE or TRUNCATE is rejected — for every role, including
-- the table owner, unless the owner deliberately drops the trigger.
CREATE OR REPLACE FUNCTION app.audit_block_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'app.audit_log is append-only (% blocked)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE OR REPLACE TRIGGER audit_log_chain
    BEFORE INSERT ON app.audit_log
    FOR EACH ROW EXECUTE FUNCTION app.audit_chain();

CREATE OR REPLACE TRIGGER audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON app.audit_log
    FOR EACH ROW EXECUTE FUNCTION app.audit_block_change();

CREATE OR REPLACE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON app.audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION app.audit_block_change();

-- Recompute the whole chain. Returns ok = false and the first bad
-- chain_seq if any row was altered, removed or reordered.
CREATE OR REPLACE FUNCTION app.verify_audit_chain(
    OUT ok boolean, OUT rows_checked bigint, OUT first_bad_seq bigint)
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    r         app.audit_log;
    expected  text := repeat('0', 64);
    want_seq  bigint := 1;
    recomputed text;
BEGIN
    ok := true; rows_checked := 0; first_bad_seq := NULL;
    FOR r IN SELECT * FROM app.audit_log ORDER BY chain_seq LOOP
        rows_checked := rows_checked + 1;
        recomputed := r.row_hash;
        r.row_hash := '';
        IF r.chain_seq <> want_seq
           OR r.prev_hash <> expected
           OR recomputed <> encode(sha256(convert_to(app.audit_payload(r), 'UTF8')), 'hex')
        THEN
            ok := false; first_bad_seq := r.chain_seq;
            RETURN;
        END IF;
        expected := recomputed;
        want_seq := want_seq + 1;
    END LOOP;
END;
$$;
