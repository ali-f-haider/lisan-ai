-- JOB 7 / OPTIONAL STEP 4: run last, independently; this is the only replacement.
-- Reuses the existing auth trigger. No live grants are backfilled.
BEGIN;
DO $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t
      WHERE t.tgrelid='auth.users'::regclass AND t.tgname='on_auth_user_created'
        AND t.tgfoid=to_regprocedure('public.handle_new_user()') AND NOT t.tgisinternal)
      OR to_regclass('public.credit_spends') IS NULL THEN
        RAISE EXCEPTION 'The existing signup trigger or credit history table is missing. Installation stopped.';
    END IF;
END $$;
CREATE FUNCTION public.lisan_signup_credit_amount()
RETURNS integer LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE raw text; amount numeric;
BEGIN
    -- Matches _get_pricing_config: singleton, newest updated_at descending.
    IF to_regclass('public.pricing_config') IS NULL THEN RETURN 100; END IF;
    SELECT to_jsonb(c)->>'free_credits' INTO raw FROM public.pricing_config c
      WHERE c.id='singleton' ORDER BY c.updated_at DESC LIMIT 1;
    IF raw IS NULL THEN RETURN 100; END IF;
    amount:=raw::numeric;
    IF amount::text NOT IN ('NaN','Infinity','-Infinity')
      AND amount BETWEEN 1 AND 1000 AND trunc(amount)=amount THEN RETURN amount::integer; END IF;
    RETURN 100;
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range OR undefined_column THEN
    RETURN 100;
END $$;
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE amount integer;
BEGIN
    amount:=public.lisan_signup_credit_amount();
    INSERT INTO public.profiles(id,display_name,credits)
      VALUES(new.id,coalesce(new.raw_user_meta_data->>'display_name',new.email),amount)
      ON CONFLICT(id) DO NOTHING;
    IF FOUND THEN
        INSERT INTO public.credit_spends(uid,action,credits) VALUES(new.id,'signup_grant',-amount);
    END IF;
    RETURN new;
END $$;
REVOKE ALL ON FUNCTION public.lisan_signup_credit_amount() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.lisan_signup_credit_amount() TO postgres,service_role;
NOTIFY pgrst,'reload schema';
COMMIT;

-- VERIFY (read-only): compare configured value with bounded effective signup allowance.
-- SELECT id,free_credits,updated_at FROM public.pricing_config WHERE id='singleton' ORDER BY updated_at DESC LIMIT 1;
-- SELECT public.lisan_signup_credit_amount() AS effective_signup_credits;
-- SELECT t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t WHERE t.tgrelid='auth.users'::regclass AND t.tgname='on_auth_user_created';
-- SELECT has_function_privilege('anon','public.lisan_signup_credit_amount()','EXECUTE') AS anon,
--        has_function_privilege('authenticated','public.lisan_signup_credit_amount()','EXECUTE') AS member,
--        has_function_privilege('service_role','public.lisan_signup_credit_amount()','EXECUTE') AS server;
-- Expected false / false / true. No real signup is needed to verify installation.
-- UNDO: restore the EXACT supplied function below; keep all profiles and history.
-- BEGIN;
-- CREATE OR REPLACE FUNCTION public.handle_new_user()
-- RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $function$
-- begin
--   insert into public.profiles (id, display_name, credits)
--   values (new.id, coalesce(new.raw_user_meta_data->>'display_name', new.email), 100);
--   return new;
-- end;
-- $function$;
-- DROP FUNCTION IF EXISTS public.lisan_signup_credit_amount();
-- NOTIFY pgrst,'reload schema';
-- COMMIT;
-- The unchanged existing trigger then grants the old fixed 100 again, without new signup history.
