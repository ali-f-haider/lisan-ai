-- =====================================================================
-- Lisan AI -- Supabase lock-down  (run in: Supabase dashboard > SQL Editor)
--
-- WHY: your website's server talks to the database with the secret
-- SERVICE key, which always works no matter what is locked. The browser
-- only ever uses Supabase to log in (sign-in / sign-up) and never reads
-- a table directly. So nothing a visitor's browser holds (the public
-- "anon" key, or a logged-in user's token) needs ANY table access or ANY
-- database function. This script removes all of it, so a user can never
-- read another user's profile or credits, edit their own credits, run
-- add_credits(), or read session / admin tokens.
--
-- SAFE TO RUN MORE THAN ONCE.  Order:
--   1. Run PART 1 (read-only) and look at the result.
--   2. Run PART 2 (the lock-down).
--   3. Run PART 1 again: every "anon_can_*" / "authed_can_*" column must
--      now be false.
--   4. Run  python supabase_rls_audit.py  from the backend folder.
--   5. Open the live site: log in, open Account, run one test dub.
--      (If anything breaks, tell me what you saw -- the undo is at the
--      very bottom of this file.)
-- =====================================================================


-- ---------------------------------------------------------------------
-- PART 1 -- look (changes nothing)
-- ---------------------------------------------------------------------
-- 1a. Tables: RLS on/off and what the two browser roles may do.
SELECT c.relname                                         AS table_name,
       c.relrowsecurity                                  AS rls_on,
       has_table_privilege('anon', c.oid, 'SELECT')          AS anon_can_read,
       has_table_privilege('anon', c.oid, 'INSERT')          AS anon_can_insert,
       has_table_privilege('authenticated', c.oid, 'SELECT') AS authed_can_read,
       has_table_privilege('authenticated', c.oid, 'UPDATE') AS authed_can_update
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY c.relname;

-- 1b. Functions: who may run them (add_credits etc. must be false, false).
SELECT p.oid::regprocedure                               AS function_name,
       has_function_privilege('anon', p.oid, 'EXECUTE')          AS anon_can_run,
       has_function_privilege('authenticated', p.oid, 'EXECUTE') AS authed_can_run
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' AND p.prokind = 'f'
ORDER BY 1;

-- 1c. Does a paid order get credited only once?  The webhook checks "have I
--     seen this Stripe session" and then inserts; a UNIQUE rule on
--     credit_orders.session_id is what makes a double delivery harmless.
SELECT conname, contype FROM pg_constraint
WHERE conrelid = 'public.credit_orders'::regclass;
SELECT indexname, indexdef FROM pg_indexes
WHERE schemaname = 'public' AND tablename = 'credit_orders';


-- ---------------------------------------------------------------------
-- PART 2 -- the lock-down
-- ---------------------------------------------------------------------
-- 2a. Row-level security ON for every table in the public schema.
--     (With no policies, RLS means "nobody but the service key".)
DO $$
DECLARE t record;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.tablename);
  END LOOP;
END $$;

-- 2b. Take every table / sequence privilege away from the browser roles.
REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;

-- 2c. Nobody but the server may run database functions (add_credits,
--     deduct_credits, deduct_subscription_credits, ...). Postgres lets
--     everyone run a new function by default; this turns that off.
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, anon, authenticated;

-- 2d. The server's own (service) role keeps full access.
GRANT ALL     ON ALL TABLES    IN SCHEMA public TO service_role;
GRANT ALL     ON ALL SEQUENCES IN SCHEMA public TO service_role;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO service_role;

-- 2e. Same rules for tables / functions you create in the future.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES    FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES    TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO service_role;

-- 2f. A paid Stripe order can only be credited once, even if Stripe delivers
--     the webhook twice at the same moment. (If this errors with "duplicate
--     key", two rows share a session_id: tell me and I'll give you the
--     clean-up query -- do NOT delete rows by hand.)
CREATE UNIQUE INDEX IF NOT EXISTS credit_orders_session_id_uq
  ON public.credit_orders (session_id);


-- ---------------------------------------------------------------------
-- UNDO (only if the live site broke right after PART 2 -- it should not).
-- This gives the browser roles their old open access back, so prefer
-- telling me what broke instead of running it.
-- ---------------------------------------------------------------------
-- GRANT ALL ON ALL TABLES    IN SCHEMA public TO anon, authenticated;
-- GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated;
-- GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO PUBLIC, anon, authenticated;
