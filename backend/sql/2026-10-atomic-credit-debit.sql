-- Apply SECOND (step 2 of 2): AFTER 2026-10-credit-operations.sql and BEFORE deploying the code.
-- It only adds new tables/functions; no existing balance or billing function is changed.
-- INSTALL only. VERIFY and ROLLBACK are separate commented selections.

-- INSTALL BEGIN
BEGIN;
-- Abort installation before enabling paid actions if the existing history
-- schema is incomplete. Ali must review a missing column, never guess it.
DO $$
BEGIN
    IF to_regclass('public.profiles') IS NULL OR to_regclass('public.credit_orders') IS NULL
       OR to_regclass('public.credit_spends') IS NULL THEN
        RAISE EXCEPTION 'Existing billing tables are missing; installation stopped.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM (VALUES ('profiles','id'),('profiles','credits'),('profiles','subscription_credits'),
          ('credit_orders','session_id'),('credit_orders','uid'),('credit_orders','credits'),
          ('credit_spends','uid'),('credit_spends','action'),('credit_spends','credits'),
          ('credit_spends','job_id'),('credit_spends','generated_seconds')) AS required(table_name,column_name)
        WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema = 'public'
                           AND c.table_name = required.table_name AND c.column_name = required.column_name)
    ) THEN
        RAISE EXCEPTION 'Existing billing columns are missing; installation stopped.';
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS public.lisan_credit_pack_receipts (
    receipt text PRIMARY KEY,
    uid uuid NOT NULL,
    amount integer NOT NULL CHECK (amount > 0),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done')),
    permanent_balance integer,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE public.lisan_credit_pack_receipts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.lisan_credit_pack_receipts FROM PUBLIC,anon,authenticated;
GRANT SELECT ON public.lisan_credit_pack_receipts TO postgres,service_role;

CREATE OR REPLACE FUNCTION public.lisan_atomic_debit(
    p_operation_id uuid, p_uid uuid, p_amount integer,
    p_action text DEFAULT 'deduction', p_job_id text DEFAULT NULL, p_generated_seconds double precision DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    op public.lisan_credit_operations%ROWTYPE;
    sub_balance integer;
    perm_balance integer;
    sub_take integer;
    perm_take integer;
    history_job public.credit_spends.job_id%TYPE;
BEGIN
    IF p_action IS NULL OR length(p_action) = 0 OR p_generated_seconds < 0
       OR p_generated_seconds = 'NaN'::double precision
       OR p_generated_seconds IN ('Infinity'::double precision,'-Infinity'::double precision) THEN
        RAISE EXCEPTION 'Invalid credit receipt details.' USING ERRCODE = '22023';
    END IF;
    PERFORM public.lisan_credit_begin(p_operation_id,p_uid,'debit',p_amount,NULL);
    SELECT * INTO STRICT op FROM public.lisan_credit_operations
    WHERE operation_id = p_operation_id FOR NO KEY UPDATE;
    IF op.action IS NOT NULL AND (op.action <> p_action OR op.job_id IS DISTINCT FROM p_job_id) THEN
        RAISE EXCEPTION 'Debit ID was reused for different work.' USING ERRCODE = '22023';
    END IF;
    IF op.status <> 'pending' THEN
        RETURN to_jsonb(op) || jsonb_build_object('replayed',true);
    END IF;
    SELECT coalesce(subscription_credits,0),coalesce(credits,0) INTO sub_balance,perm_balance
    FROM public.profiles WHERE id = p_uid FOR UPDATE;
    IF NOT FOUND THEN
        UPDATE public.lisan_credit_operations SET status = 'failed',reason = 'missing_profile',action = p_action,job_id = p_job_id
        WHERE operation_id = p_operation_id RETURNING * INTO op;
        RETURN to_jsonb(op);
    END IF;
    IF sub_balance < 0 OR perm_balance < 0 THEN
        RAISE EXCEPTION 'Balance needs review.';
    END IF;
    IF sub_balance::bigint+perm_balance < p_amount THEN
        UPDATE public.lisan_credit_operations SET status = 'failed',reason = 'insufficient',
            subscription_balance = sub_balance,permanent_balance = perm_balance,action = p_action,job_id = p_job_id,
            updated_at = clock_timestamp()
        WHERE operation_id = p_operation_id RETURNING * INTO op;
        RETURN to_jsonb(op);
    END IF;
    sub_take := least(sub_balance,p_amount);
    perm_take := p_amount-sub_take;
    UPDATE public.profiles SET subscription_credits = sub_balance-sub_take,credits = perm_balance-perm_take
    WHERE id = p_uid RETURNING subscription_credits,credits INTO sub_balance,perm_balance;
    history_job := p_job_id;
    INSERT INTO public.credit_spends(uid,action,credits,job_id,generated_seconds)
    VALUES(p_uid,p_action,p_amount,history_job,p_generated_seconds);
    UPDATE public.lisan_credit_operations SET status = 'done',taken_subscription = sub_take,
        taken_permanent = perm_take,subscription_balance = sub_balance,permanent_balance = perm_balance,
        action = p_action,job_id = p_job_id,updated_at = clock_timestamp()
    WHERE operation_id = p_operation_id RETURNING * INTO op;
    RETURN to_jsonb(op) || jsonb_build_object('replayed',false);
END $$;

CREATE OR REPLACE FUNCTION public.lisan_fulfill_pack(p_uid uuid,p_receipt text,p_amount integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    pack public.lisan_credit_pack_receipts%ROWTYPE;
    balance integer;
BEGIN
    IF p_uid IS NULL OR p_receipt IS NULL OR length(p_receipt) = 0 OR p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'Invalid credit purchase.' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.lisan_credit_pack_receipts(receipt,uid,amount)
    VALUES(p_receipt,p_uid,p_amount) ON CONFLICT(receipt) DO NOTHING;
    SELECT * INTO STRICT pack FROM public.lisan_credit_pack_receipts WHERE receipt = p_receipt FOR UPDATE;
    IF pack.uid <> p_uid OR pack.amount <> p_amount THEN
        RAISE EXCEPTION 'Purchase receipt was reused with different details.' USING ERRCODE = '22023';
    END IF;
    IF pack.status = 'done' THEN
        RETURN jsonb_build_object('uid',p_uid,'receipt',p_receipt,'amount',p_amount,'status','already_fulfilled',
                                  'permanent_balance',pack.permanent_balance);
    END IF;
    -- Legacy receipts have no reliable grant flag. Do NOT guess that an old
    -- receipt was paid or regrant it. Leave it recoverable for reconciliation.
    IF EXISTS(SELECT 1 FROM public.credit_orders WHERE session_id = p_receipt) THEN
        RETURN jsonb_build_object('uid',p_uid,'receipt',p_receipt,'amount',p_amount,'status','legacy_review');
    END IF;
    SELECT coalesce(credits,0) INTO STRICT balance FROM public.profiles WHERE id = p_uid FOR UPDATE;
    IF balance < 0 THEN RAISE EXCEPTION 'Balance needs review.'; END IF;
    INSERT INTO public.credit_orders(session_id,uid,credits) VALUES(p_receipt,p_uid,p_amount);
    UPDATE public.profiles SET credits = balance+p_amount WHERE id = p_uid RETURNING credits INTO balance;
    UPDATE public.lisan_credit_pack_receipts SET status = 'done',permanent_balance = balance WHERE receipt = p_receipt;
    RETURN jsonb_build_object('uid',p_uid,'receipt',p_receipt,'amount',p_amount,'status','done','permanent_balance',balance);
END $$;

-- A read-only capability check, never a speculative charge.
CREATE OR REPLACE FUNCTION public.lisan_billing_ready()
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT jsonb_build_object('version',1,
      'debit',to_regprocedure('public.lisan_atomic_debit(uuid,uuid,integer,text,text,double precision)') IS NOT NULL,
      'pack',to_regprocedure('public.lisan_fulfill_pack(uuid,text,integer)') IS NOT NULL,
      'refund',to_regprocedure('public.lisan_credit_refund(uuid,uuid,integer,uuid,text)') IS NOT NULL,
      'cancel',to_regprocedure('public.lisan_cancel_clone_debit(uuid,uuid,integer)') IS NOT NULL);
$$;
REVOKE ALL ON FUNCTION public.lisan_atomic_debit(uuid,uuid,integer,text,text,double precision) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.lisan_fulfill_pack(uuid,text,integer) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.lisan_billing_ready() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.lisan_atomic_debit(uuid,uuid,integer,text,text,double precision) TO postgres,service_role;
GRANT EXECUTE ON FUNCTION public.lisan_fulfill_pack(uuid,text,integer) TO postgres,service_role;
GRANT EXECUTE ON FUNCTION public.lisan_billing_ready() TO postgres,service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
-- INSTALL END

-- VERIFY: READ ONLY. Expected version 1, debit/pack/refund/cancel all true.
-- SELECT public.lisan_billing_ready();
-- SELECT p.oid::regprocedure AS function_name,
--        has_function_privilege('anon',p.oid,'EXECUTE') AS anon_can_run,
--        has_function_privilege('authenticated',p.oid,'EXECUTE') AS member_can_run,
--        has_function_privilege('service_role',p.oid,'EXECUTE') AS server_can_run
-- FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
-- WHERE n.nspname = 'public' AND p.proname LIKE 'lisan_%';
-- Expected: every row false, false, true.

-- VERIFY TRANSACTION: TEST ACCOUNT ONLY; all changes roll back at the end.
-- Replace TEST-ACCOUNT-UUID with a real disposable test profile UUID.
-- Run this entire block together, never stop before ROLLBACK.
-- BEGIN;
-- UPDATE public.profiles SET subscription_credits = 5,credits = 10 WHERE id = 'TEST-ACCOUNT-UUID'::uuid;
-- SELECT public.lisan_atomic_debit('00000000-0000-4000-8000-000000000501','TEST-ACCOUNT-UUID',8,'verification',NULL,NULL);
-- Expected done; taken_subscription=5, taken_permanent=3, new balances=0 and 7.
-- SELECT public.lisan_atomic_debit('00000000-0000-4000-8000-000000000501','TEST-ACCOUNT-UUID',8,'verification',NULL,NULL);
-- Expected same receipt, replayed=true, no new debit/history row.
-- SELECT public.lisan_atomic_debit('00000000-0000-4000-8000-000000000502','TEST-ACCOUNT-UUID',8,'verification',NULL,NULL);
-- Expected failed/insufficient; balance still 0+7.
-- SELECT public.lisan_credit_finish('00000000-0000-4000-8000-000000000501','failed',8);
-- Expected work_status=failed, refund_due=8; balance unchanged until refund.
-- SELECT public.lisan_credit_refund('00000000-0000-4000-8000-000000000503','TEST-ACCOUNT-UUID',8,
--   '00000000-0000-4000-8000-000000000501','proportional_subscription_up');
-- Expected done; 5 subscription and 3 permanent returned; balance 5+10.
-- SELECT public.lisan_credit_refund('00000000-0000-4000-8000-000000000503','TEST-ACCOUNT-UUID',8,
--   '00000000-0000-4000-8000-000000000501','proportional_subscription_up');
-- Expected replayed=true; still 5+10.
-- SELECT public.lisan_fulfill_pack('TEST-ACCOUNT-UUID','job5-offline-verification',100);
-- Expected done; permanent_balance=110.
-- SELECT public.lisan_fulfill_pack('TEST-ACCOUNT-UUID','job5-offline-verification',100);
-- Expected already_fulfilled; permanent_balance=110.
-- ROLLBACK;
-- Now the test account, receipts and history are exactly as they were before.

-- INVALID AMOUNT VERIFY: run separately, expect an error, no balance change.
-- SELECT public.lisan_atomic_debit(gen_random_uuid(),'TEST-ACCOUNT-UUID',0);
-- SELECT public.lisan_atomic_debit(gen_random_uuid(),'TEST-ACCOUNT-UUID',-1);

-- ROLLBACK INSTALLATION (after disabling paid work; keep receipt tables):
-- BEGIN;
-- DROP FUNCTION IF EXISTS public.lisan_billing_ready();
-- DROP FUNCTION IF EXISTS public.lisan_atomic_debit(uuid,uuid,integer,text,text,double precision);
-- DROP FUNCTION IF EXISTS public.lisan_fulfill_pack(uuid,text,integer);
-- NOTIFY pgrst, 'reload schema';
-- COMMIT;
-- Then use the operations-file ROLLBACK. No old billing function is dropped.
