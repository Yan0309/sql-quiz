-- =====================================================================
-- 02_grants.sql  —  least-privilege access. Roles are created (with
-- passwords from GitHub Secrets) by scripts/setup_db.py before this runs.
--
--   quiz_taker : runs the students' SQL. SELECT on the four Olist tables
--                in `public`, nothing else. No access to schema `app`
--                => cannot read or edit the audit log.
--   quiz_app   : the web app's writer. INSERT/SELECT on app tables,
--                no UPDATE/DELETE anywhere, no access to Olist tables.
--
-- The Olist tables themselves are not modified. The only change to them
-- is the GRANT SELECT to quiz_taker below.
-- =====================================================================

-- Start from zero so re-runs never leave stale privileges behind.
REVOKE ALL ON ALL TABLES IN SCHEMA app FROM PUBLIC, quiz_taker, quiz_app;
REVOKE ALL ON SCHEMA app FROM PUBLIC, quiz_taker, quiz_app;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA app FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM quiz_taker, quiz_app;

-- Supabase's API roles get nothing in schema app (skipped if the roles
-- don't exist, e.g. on a non-Supabase Postgres).
DO $$
DECLARE r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA app FROM %I', r);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA app FROM %I', r);
            EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA app FROM %I', r);
        END IF;
    END LOOP;
END $$;

-- quiz_taker: read the four Olist tables, nothing else.
GRANT USAGE  ON SCHEMA public TO quiz_taker;
GRANT SELECT ON public.customers, public.orders, public.order_items, public.products
    TO quiz_taker;
ALTER ROLE quiz_taker SET search_path = public;
ALTER ROLE quiz_taker SET default_transaction_read_only = on;
ALTER ROLE quiz_taker SET statement_timeout = '10s';
ALTER ROLE quiz_taker SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE quiz_taker CONNECTION LIMIT 10;

-- quiz_app: append submissions and audit rows, read them back.
GRANT USAGE ON SCHEMA app TO quiz_app;
GRANT SELECT, INSERT ON app.quiz_submissions TO quiz_app;
GRANT SELECT, INSERT ON app.audit_log        TO quiz_app;
GRANT EXECUTE ON FUNCTION app.audit_payload(app.audit_log) TO quiz_app;
GRANT EXECUTE ON FUNCTION app.audit_chain()                TO quiz_app;
GRANT EXECUTE ON FUNCTION app.audit_block_change()         TO quiz_app;
GRANT EXECUTE ON FUNCTION app.verify_audit_chain()         TO quiz_app;
ALTER ROLE quiz_app SET statement_timeout = '10s';
ALTER ROLE quiz_app CONNECTION LIMIT 10;
