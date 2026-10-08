-- JOB 7 / STEP 2: run AFTER step 1, BEFORE deploying new application code.
-- Only new receipts/functions. Historical invoice rows are evidence needing review, not grant proof.
BEGIN;
DO $$
BEGIN
    IF to_regclass('public.lisan_billing_review_events') IS NULL THEN
        RAISE EXCEPTION 'Install Job 7 step 1 first.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM (VALUES ('profiles','id'),('profiles','subscription_credits'),
          ('profiles','subscription_clones_used'),('profiles','subscription_current_period_end'),
          ('subscription_invoices','invoice_id'),('subscription_invoices','uid'),('subscription_invoices','credits')) required(table_name,column_name)
        WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c
          WHERE c.table_schema='public' AND c.table_name=required.table_name AND c.column_name=required.column_name)
    ) THEN RAISE EXCEPTION 'Required subscription columns are missing. Installation stopped.'; END IF;
END $$;
CREATE TABLE IF NOT EXISTS public.lisan_subscription_receipts (
    invoice_id text PRIMARY KEY CHECK (length(invoice_id) BETWEEN 1 AND 255),
    reference_id uuid NOT NULL UNIQUE,
    uid uuid NOT NULL,
    amount integer NOT NULL CHECK (amount>0),
    period_start timestamptz NOT NULL,
    period_end timestamptz NOT NULL CHECK (period_end>period_start),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','stale','legacy_review')),
    subscription_before integer,
    subscription_after integer,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz
);
CREATE INDEX IF NOT EXISTS lisan_subscription_receipts_user_period ON public.lisan_subscription_receipts(uid,period_end DESC) WHERE status='done';
ALTER TABLE public.lisan_subscription_receipts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.lisan_subscription_receipts FROM PUBLIC,anon,authenticated;
GRANT SELECT ON public.lisan_subscription_receipts TO postgres,service_role;

CREATE FUNCTION public.lisan_subscription_begin(p_invoice_id text,p_uid uuid,p_amount integer,p_period_start timestamptz,p_period_end timestamptz)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE op public.lisan_subscription_receipts%ROWTYPE;
BEGIN
    IF p_invoice_id IS NULL OR length(p_invoice_id) NOT BETWEEN 1 AND 255 OR p_uid IS NULL
       OR p_amount IS NULL OR p_amount<=0 OR p_period_start IS NULL OR p_period_end IS NULL
       OR NOT isfinite(p_period_start) OR NOT isfinite(p_period_end) OR p_period_end<=p_period_start THEN
        RAISE EXCEPTION 'Invalid subscription receipt.' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.lisan_subscription_receipts(invoice_id,reference_id,uid,amount,period_start,period_end,status)
    VALUES(p_invoice_id,md5('invoice:'||p_invoice_id)::uuid,p_uid,p_amount,p_period_start,p_period_end,
      CASE WHEN EXISTS(SELECT 1 FROM public.subscription_invoices WHERE invoice_id=p_invoice_id)
        THEN 'legacy_review' ELSE 'pending' END) ON CONFLICT DO NOTHING;
    SELECT * INTO STRICT op FROM public.lisan_subscription_receipts WHERE invoice_id=p_invoice_id FOR UPDATE;
    IF op.uid<>p_uid OR op.amount<>p_amount OR op.period_start<>p_period_start OR op.period_end<>p_period_end THEN
        INSERT INTO public.lisan_billing_review_events(event_id,reference_id,uid,amount,kind)
        VALUES(md5('invoice:'||p_invoice_id||':'||p_uid::text||':'||p_amount::text||':'||
                    extract(epoch FROM p_period_start)::text||':'||extract(epoch FROM p_period_end)::text)::uuid,
               op.reference_id,p_uid,p_amount,'invoice_mismatch') ON CONFLICT DO NOTHING;
        RETURN jsonb_build_object('status','mismatch','reference_id',op.reference_id);
    END IF;
    RETURN to_jsonb(op);
END $$;

CREATE FUNCTION public.lisan_subscription_grant(p_invoice_id text,p_uid uuid,p_amount integer,p_period_start timestamptz,p_period_end timestamptz)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
    result jsonb;
    op public.lisan_subscription_receipts%ROWTYPE;
    profile public.profiles%ROWTYPE;
    old_allowance integer;
    current_end timestamptz;
    receipt_end timestamptz;
    profile_end public.profiles.subscription_current_period_end%TYPE;
BEGIN
    result:=public.lisan_subscription_begin(p_invoice_id,p_uid,p_amount,p_period_start,p_period_end);
    IF result->>'status'='mismatch' THEN RETURN result; END IF;
    SELECT * INTO STRICT op FROM public.lisan_subscription_receipts WHERE invoice_id=p_invoice_id FOR UPDATE;
    IF op.status<>'pending' THEN RETURN to_jsonb(op)||jsonb_build_object('replayed',true); END IF;
    SELECT * INTO STRICT profile FROM public.profiles WHERE id=p_uid FOR UPDATE;
    old_allowance:=coalesce(profile.subscription_credits,0);
    IF old_allowance<0 THEN RAISE EXCEPTION 'Subscription balance needs review.'; END IF;
    IF nullif(to_jsonb(profile)->>'subscription_current_period_end','') IS NOT NULL THEN
        BEGIN
            current_end:=(to_jsonb(profile)->>'subscription_current_period_end')::timestamptz;
            IF NOT isfinite(current_end) THEN RAISE EXCEPTION 'Invalid subscription period.'; END IF;
        EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'Current subscription period needs review.';
        END;
    END IF;
    SELECT max(period_end) INTO receipt_end FROM public.lisan_subscription_receipts WHERE uid=p_uid AND status='done';
    current_end:=greatest(current_end,receipt_end);
    IF current_end IS NOT NULL AND p_period_end<current_end THEN
        UPDATE public.lisan_subscription_receipts SET status='stale',completed_at=clock_timestamp()
        WHERE invoice_id=p_invoice_id RETURNING * INTO op;
        RETURN to_jsonb(op)||jsonb_build_object('replayed',false);
    END IF;
    profile_end:=p_period_end;
    UPDATE public.profiles SET subscription_credits=p_amount,subscription_clones_used=0,
      subscription_current_period_end=profile_end WHERE id=p_uid;
    -- Ali chose the full new allowance and a separate expiry entry.
    IF old_allowance>0 THEN
        INSERT INTO public.credit_spends(uid,action,credits) VALUES(p_uid,'subscription_expiry',old_allowance);
    END IF;
    INSERT INTO public.credit_spends(uid,action,credits) VALUES(p_uid,'subscription_grant',-p_amount);
    UPDATE public.lisan_subscription_receipts SET status='done',subscription_before=old_allowance,
      subscription_after=p_amount,completed_at=clock_timestamp() WHERE invoice_id=p_invoice_id RETURNING * INTO op;
    RETURN to_jsonb(op)||jsonb_build_object('replayed',false);
END $$;
REVOKE ALL ON FUNCTION public.lisan_subscription_begin(text,uuid,integer,timestamptz,timestamptz),
  public.lisan_subscription_grant(text,uuid,integer,timestamptz,timestamptz) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.lisan_subscription_begin(text,uuid,integer,timestamptz,timestamptz),
  public.lisan_subscription_grant(text,uuid,integer,timestamptz,timestamptz) TO postgres,service_role;
CREATE FUNCTION public.lisan_subscription_ready()
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT jsonb_build_object('ready',
      to_regprocedure('public.lisan_subscription_begin(text,uuid,integer,timestamptz,timestamptz)') IS NOT NULL
      AND to_regprocedure('public.lisan_subscription_grant(text,uuid,integer,timestamptz,timestamptz)') IS NOT NULL);
$$;
REVOKE ALL ON FUNCTION public.lisan_subscription_ready() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.lisan_subscription_ready() TO postgres,service_role;
NOTIFY pgrst,'reload schema';
COMMIT;

-- VERIFY separately (read-only): expected false / false / true.
-- SELECT has_function_privilege('anon','public.lisan_subscription_grant(text,uuid,integer,timestamptz,timestamptz)','EXECUTE') AS anon,
--        has_function_privilege('authenticated','public.lisan_subscription_grant(text,uuid,integer,timestamptz,timestamptz)','EXECUTE') AS member,
--        has_function_privilege('service_role','public.lisan_subscription_grant(text,uuid,integer,timestamptz,timestamptz)','EXECUTE') AS server;
-- SELECT status,count(*) FROM public.lisan_subscription_receipts GROUP BY status;
-- UNDO: first undo the Job 7 extended health function (step 3), if installed. KEEP receipts.
-- BEGIN;
-- DROP FUNCTION IF EXISTS public.lisan_subscription_ready();
-- DROP FUNCTION IF EXISTS public.lisan_subscription_grant(text,uuid,integer,timestamptz,timestamptz);
-- DROP FUNCTION IF EXISTS public.lisan_subscription_begin(text,uuid,integer,timestamptz,timestamptz);
-- NOTIFY pgrst,'reload schema';
-- COMMIT;
-- New code returns 503 and waits for retry. No old permanent-credit fallback is restored.
