-- JOB 7 / STEP 1: install before the new application code (2026-10-08).
-- Adds server-only receipts/functions. No existing function is replaced.
BEGIN;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM (VALUES ('profiles','id'),('profiles','credits'),
          ('credit_spends','uid'),('credit_spends','action'),('credit_spends','credits'),
          ('credit_audit','admin'),('credit_audit','target_uid'),('credit_audit','delta'),
          ('credit_audit','reason'),('credit_audit','created_at')) required(table_name,column_name)
        WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c
          WHERE c.table_schema='public' AND c.table_name=required.table_name
            AND c.column_name=required.column_name)
    ) THEN
        RAISE EXCEPTION 'Required balance/history/audit columns are missing. Installation stopped.';
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS public.lisan_admin_adjustments (
    operation_id uuid PRIMARY KEY,
    uid uuid NOT NULL,
    requested_delta integer NOT NULL CHECK (requested_delta <> 0 AND requested_delta BETWEEN -10000 AND 10000),
    reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 2000),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done')),
    balance_before integer,
    balance_after integer,
    actual_delta integer,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz
);
CREATE TABLE IF NOT EXISTS public.lisan_billing_review_events (
    event_id uuid PRIMARY KEY,
    reference_id uuid NOT NULL,
    uid uuid NOT NULL,
    amount integer NOT NULL CHECK (amount > 0),
    kind text NOT NULL CHECK (kind IN ('admin_mismatch','invoice_mismatch')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    reviewed_at timestamptz
);
ALTER TABLE public.lisan_admin_adjustments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lisan_billing_review_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.lisan_admin_adjustments,public.lisan_billing_review_events FROM PUBLIC,anon,authenticated;
GRANT SELECT ON public.lisan_admin_adjustments,public.lisan_billing_review_events TO postgres,service_role;

CREATE FUNCTION public.lisan_admin_begin(p_operation_id uuid,p_uid uuid,p_delta integer,p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE op public.lisan_admin_adjustments%ROWTYPE;
BEGIN
    IF p_operation_id IS NULL OR p_uid IS NULL OR p_delta IS NULL OR p_delta=0
       OR p_delta NOT BETWEEN -10000 AND 10000 OR p_reason IS NULL
       OR length(btrim(p_reason)) NOT BETWEEN 1 AND 2000 THEN
        RAISE EXCEPTION 'Invalid adjustment request.' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.lisan_admin_adjustments(operation_id,uid,requested_delta,reason)
    VALUES(p_operation_id,p_uid,p_delta,btrim(p_reason)) ON CONFLICT DO NOTHING;
    SELECT * INTO STRICT op FROM public.lisan_admin_adjustments WHERE operation_id=p_operation_id FOR UPDATE;
    IF op.uid<>p_uid OR op.requested_delta<>p_delta OR op.reason<>btrim(p_reason) THEN
        INSERT INTO public.lisan_billing_review_events(event_id,reference_id,uid,amount,kind)
        VALUES(md5('admin:'||p_operation_id::text||':'||p_uid::text||':'||p_delta::text||':'||md5(btrim(p_reason)))::uuid,
               p_operation_id,p_uid,abs(p_delta),'admin_mismatch') ON CONFLICT DO NOTHING;
        RETURN jsonb_build_object('status','mismatch','operation_id',p_operation_id);
    END IF;
    RETURN to_jsonb(op);
END $$;

CREATE FUNCTION public.lisan_admin_adjust(p_operation_id uuid,p_uid uuid,p_delta integer,p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
    result jsonb;
    op public.lisan_admin_adjustments%ROWTYPE;
    old_balance integer;
    new_balance bigint;
    audit_uid public.credit_audit.target_uid%TYPE;
BEGIN
    result:=public.lisan_admin_begin(p_operation_id,p_uid,p_delta,p_reason);
    IF result->>'status'='mismatch' THEN RETURN result; END IF;
    SELECT * INTO STRICT op FROM public.lisan_admin_adjustments WHERE operation_id=p_operation_id FOR UPDATE;
    IF op.status='done' THEN RETURN to_jsonb(op)||jsonb_build_object('replayed',true); END IF;
    SELECT credits INTO STRICT old_balance FROM public.profiles WHERE id=p_uid FOR UPDATE;
    IF old_balance IS NULL OR old_balance<0 THEN RAISE EXCEPTION 'Balance needs review.'; END IF;
    new_balance:=greatest(0,old_balance::bigint+p_delta);
    IF new_balance>2147483647 THEN RAISE EXCEPTION 'Balance would exceed its supported limit.'; END IF;
    UPDATE public.profiles SET credits=new_balance::integer WHERE id=p_uid;
    INSERT INTO public.credit_spends(uid,action,credits)
    VALUES(p_uid,'admin_adjustment',(old_balance-new_balance)::integer);
    audit_uid:=p_uid;
    INSERT INTO public.credit_audit(admin,target_uid,delta,reason,created_at)
    VALUES('admin',audit_uid,(new_balance-old_balance)::integer,op.reason,clock_timestamp());
    UPDATE public.lisan_admin_adjustments SET status='done',balance_before=old_balance,
      balance_after=new_balance::integer,actual_delta=(new_balance-old_balance)::integer,completed_at=clock_timestamp()
    WHERE operation_id=p_operation_id RETURNING * INTO op;
    RETURN to_jsonb(op)||jsonb_build_object('replayed',false);
END $$;
REVOKE ALL ON FUNCTION public.lisan_admin_begin(uuid,uuid,integer,text),
  public.lisan_admin_adjust(uuid,uuid,integer,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.lisan_admin_begin(uuid,uuid,integer,text),
  public.lisan_admin_adjust(uuid,uuid,integer,text) TO postgres,service_role;
NOTIFY pgrst,'reload schema';
COMMIT;

-- VERIFY separately (read-only): expected false / false / true.
-- SELECT has_function_privilege('anon','public.lisan_admin_adjust(uuid,uuid,integer,text)','EXECUTE') AS anon,
--        has_function_privilege('authenticated','public.lisan_admin_adjust(uuid,uuid,integer,text)','EXECUTE') AS member,
--        has_function_privilege('service_role','public.lisan_admin_adjust(uuid,uuid,integer,text)','EXECUTE') AS server;
-- SELECT count(*) AS saved_adjustments FROM public.lisan_admin_adjustments;
-- UNDO: disable adjustments by dropping only these new functions. KEEP money receipts/history.
-- First undo Job 7's extended-health function (step 3) if installed; do not use CASCADE.
-- BEGIN;
-- DROP FUNCTION IF EXISTS public.lisan_admin_adjust(uuid,uuid,integer,text);
-- DROP FUNCTION IF EXISTS public.lisan_admin_begin(uuid,uuid,integer,text);
-- NOTIFY pgrst,'reload schema';
-- COMMIT;
-- New code then returns 503; no old unsafe path is used. Receipts preserve paid history.
