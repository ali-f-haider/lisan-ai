-- Ali applies this himself FIRST (step 1 of 2), BEFORE deploying the code. It only adds new tables/functions; old code ignores them.
-- Then run the debit file (step 2 of 2). Code that arrives before this SQL pauses paid actions until it is installed.
-- Run INSTALL only. VERIFY and ROLLBACK are separate, commented selections.
-- Prerequisites: public.profiles and public.credit_spends, as used by main.py.
-- All finance/history changes are in one PostgreSQL transaction.
-- No existing balance or legacy billing function is replaced.

-- INSTALL BEGIN
BEGIN;
CREATE TABLE IF NOT EXISTS public.lisan_credit_operations (
    operation_id uuid PRIMARY KEY,
    uid uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('debit', 'refund')),
    amount integer NOT NULL CHECK (amount > 0),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed')),
    reason text,
    debit_id uuid REFERENCES public.lisan_credit_operations(operation_id),
    taken_subscription integer NOT NULL DEFAULT 0 CHECK (taken_subscription >= 0),
    taken_permanent integer NOT NULL DEFAULT 0 CHECK (taken_permanent >= 0),
    subscription_balance integer,
    permanent_balance integer,
    action text,
    job_id text,
    work_status text NOT NULL DEFAULT 'pending' CHECK (work_status IN ('pending', 'delivered', 'partial', 'failed')),
    refund_due integer NOT NULL DEFAULT 0 CHECK (refund_due >= 0),
    split_rule text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (status <> 'done' OR taken_subscription::bigint + taken_permanent = amount),
    CHECK (kind <> 'refund' OR debit_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS lisan_credit_refunds_origin_idx
    ON public.lisan_credit_operations(debit_id) WHERE kind = 'refund' AND status = 'done';
CREATE INDEX IF NOT EXISTS lisan_credit_clone_pending_idx
    ON public.lisan_credit_operations(created_at)
    WHERE kind = 'debit' AND status = 'done' AND action IN ('clone','custom_voice') AND refund_due > 0;
ALTER TABLE public.lisan_credit_operations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.lisan_credit_operations FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.lisan_credit_operations TO postgres, service_role;

CREATE OR REPLACE FUNCTION public.lisan_credit_begin(
    p_operation_id uuid, p_uid uuid, p_kind text, p_amount integer, p_debit_id uuid DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE op public.lisan_credit_operations%ROWTYPE;
BEGIN
    IF p_operation_id IS NULL OR p_uid IS NULL OR p_kind NOT IN ('debit','refund')
       OR p_kind IS NULL OR p_amount IS NULL OR p_amount <= 0
       OR (p_kind = 'refund' AND p_debit_id IS NULL)
       OR (p_kind = 'debit' AND p_debit_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Invalid credit operation.' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.lisan_credit_operations(operation_id,uid,kind,amount,debit_id)
    VALUES(p_operation_id,p_uid,p_kind,p_amount,p_debit_id)
    ON CONFLICT(operation_id) DO NOTHING;
    SELECT * INTO STRICT op FROM public.lisan_credit_operations
    WHERE operation_id = p_operation_id FOR NO KEY UPDATE;
    IF op.uid <> p_uid OR op.kind <> p_kind OR op.amount <> p_amount
       OR op.debit_id IS DISTINCT FROM p_debit_id THEN
        RAISE EXCEPTION 'Credit operation ID was reused with different details.' USING ERRCODE = '22023';
    END IF;
    RETURN to_jsonb(op);
END $$;

-- This records the PROVIDER outcome; it never claims an unconfirmed debit
-- was paid, and never grants credits. The accounting status remains done.
CREATE OR REPLACE FUNCTION public.lisan_credit_finish(
    p_operation_id uuid, p_work_status text, p_refund_due integer
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE op public.lisan_credit_operations%ROWTYPE;
BEGIN
    SELECT * INTO STRICT op FROM public.lisan_credit_operations
    WHERE operation_id = p_operation_id FOR NO KEY UPDATE;
    IF op.kind <> 'debit' OR op.status <> 'done' OR p_work_status IS NULL
       OR p_work_status NOT IN ('delivered','partial','failed')
       OR p_refund_due IS NULL OR p_refund_due < 0 OR p_refund_due > op.amount
       OR (p_work_status = 'delivered' AND p_refund_due <> 0)
       OR (p_work_status = 'failed' AND p_refund_due <> op.amount) THEN
        RAISE EXCEPTION 'Invalid credit settlement.' USING ERRCODE = '22023';
    END IF;
    IF op.work_status <> 'pending' THEN
        IF op.work_status <> p_work_status OR op.refund_due <> p_refund_due THEN
            RAISE EXCEPTION 'Credit settlement already recorded.' USING ERRCODE = '22023';
        END IF;
        RETURN to_jsonb(op);
    END IF;
    UPDATE public.lisan_credit_operations
    SET work_status = p_work_status, refund_due = p_refund_due, updated_at = clock_timestamp()
    WHERE operation_id = p_operation_id RETURNING * INTO op;
    RETURN to_jsonb(op);
END $$;

-- Refunds restore the original buckets, including after a renewal; choosing
-- a different expiry policy requires Ali's separate decision before wiring
-- this into delayed long-dub refunds. A short clone is settled immediately.
-- p_split_rule is explicit. No hidden policy chooses between integer splits.
CREATE OR REPLACE FUNCTION public.lisan_credit_refund(
    p_operation_id uuid, p_uid uuid, p_amount integer, p_debit_id uuid, p_split_rule text
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    op public.lisan_credit_operations%ROWTYPE;
    original public.lisan_credit_operations%ROWTYPE;
    previous_sub bigint;
    previous_perm bigint;
    sub_take integer;
    perm_take integer;
    sub_balance integer;
    perm_balance integer;
    history_job public.credit_spends.job_id%TYPE;
BEGIN
    IF p_split_rule IS NULL OR p_split_rule NOT IN ('proportional_subscription_up','subscription_first') THEN
        RAISE EXCEPTION 'Choose a refund split rule.' USING ERRCODE = '22023';
    END IF;
    PERFORM public.lisan_credit_begin(p_operation_id,p_uid,'refund',p_amount,p_debit_id);
    SELECT * INTO STRICT op FROM public.lisan_credit_operations
    WHERE operation_id = p_operation_id FOR NO KEY UPDATE;
    IF op.status <> 'pending' THEN
        IF op.split_rule IS DISTINCT FROM p_split_rule THEN
            RAISE EXCEPTION 'Refund ID was reused with a different split rule.' USING ERRCODE = '22023';
        END IF;
        RETURN to_jsonb(op) || jsonb_build_object('replayed',true);
    END IF;
    SELECT * INTO STRICT original FROM public.lisan_credit_operations
    WHERE operation_id = p_debit_id FOR NO KEY UPDATE;
    IF original.uid <> p_uid OR original.kind <> 'debit' OR original.status <> 'done' THEN
        RAISE EXCEPTION 'Refund requires a confirmed original debit.' USING ERRCODE = '22023';
    END IF;
    SELECT coalesce(sum(taken_subscription),0),coalesce(sum(taken_permanent),0)
    INTO previous_sub,previous_perm FROM public.lisan_credit_operations
    WHERE debit_id = p_debit_id AND kind = 'refund' AND status = 'done';
    IF previous_sub + previous_perm + p_amount > original.amount
       OR previous_sub + previous_perm + p_amount > original.refund_due THEN
        RAISE EXCEPTION 'Refund exceeds the recorded settlement.' USING ERRCODE = '22023';
    END IF;
    IF p_split_rule = 'subscription_first' THEN
        sub_take := least(p_amount,original.taken_subscription-previous_sub);
    ELSE
        -- Cumulative rounding avoids over-refunding a bucket across several
        -- partial refunds. At a full refund both buckets are exact.
        sub_take := ceil((previous_sub+previous_perm+p_amount)::numeric
                         * original.taken_subscription / original.amount)::integer - previous_sub;
        sub_take := least(p_amount,greatest(0,sub_take));
        sub_take := least(sub_take,original.taken_subscription-previous_sub);
        sub_take := greatest(sub_take,p_amount-(original.taken_permanent-previous_perm));
    END IF;
    perm_take := p_amount-sub_take;
    SELECT coalesce(subscription_credits,0),coalesce(credits,0)
    INTO STRICT sub_balance,perm_balance FROM public.profiles WHERE id = p_uid FOR UPDATE;
    IF sub_balance < 0 OR perm_balance < 0 THEN
        RAISE EXCEPTION 'Balance needs review.';
    END IF;
    UPDATE public.profiles SET subscription_credits = sub_balance+sub_take,credits = perm_balance+perm_take
    WHERE id = p_uid RETURNING subscription_credits,credits INTO sub_balance,perm_balance;
    history_job := original.job_id;
    INSERT INTO public.credit_spends(uid,action,credits,job_id)
    VALUES(p_uid,coalesce(original.action,'deduction') || '_refund',-p_amount,history_job);
    UPDATE public.lisan_credit_operations SET status = 'done',taken_subscription = sub_take,
        taken_permanent = perm_take,subscription_balance = sub_balance,permanent_balance = perm_balance,
        action = coalesce(original.action,'deduction') || '_refund',job_id = original.job_id,
        split_rule = p_split_rule,updated_at = clock_timestamp()
    WHERE operation_id = p_operation_id RETURNING * INTO op;
    RETURN to_jsonb(op) || jsonb_build_object('replayed',false);
END $$;

-- Used only when the caller KNOWS no provider request was started.
-- Locking the ID also closes the race with a late debit HTTP request:
-- either it already committed and is marked for refund, or it is cancelled
-- before it can charge. This does not guess a running provider's outcome.
CREATE OR REPLACE FUNCTION public.lisan_cancel_clone_debit(
    p_operation_id uuid,p_uid uuid,p_amount integer
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE op public.lisan_credit_operations%ROWTYPE;
BEGIN
    PERFORM public.lisan_credit_begin(p_operation_id,p_uid,'debit',p_amount,NULL);
    SELECT * INTO STRICT op FROM public.lisan_credit_operations
    WHERE operation_id = p_operation_id FOR NO KEY UPDATE;
    IF op.status = 'done' THEN
        IF op.action NOT IN ('clone','custom_voice') THEN
            RAISE EXCEPTION 'Cancellation is only for an unstarted voice clone.' USING ERRCODE = '22023';
        END IF;
        RETURN public.lisan_credit_finish(p_operation_id,'failed',p_amount);
    END IF;
    IF op.status = 'pending' THEN
        UPDATE public.lisan_credit_operations SET status = 'failed',reason = 'cancelled',updated_at = clock_timestamp()
        WHERE operation_id = p_operation_id RETURNING * INTO op;
    END IF;
    RETURN to_jsonb(op);
END $$;

CREATE OR REPLACE FUNCTION public.lisan_pending_clone_refunds()
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT coalesce(jsonb_agg(to_jsonb(p)), '[]'::jsonb) FROM (
        SELECT o.operation_id,o.uid,o.refund_due FROM public.lisan_credit_operations o
        WHERE o.kind = 'debit' AND o.status = 'done' AND o.action IN ('clone','custom_voice') AND o.refund_due > 0
          AND o.refund_due > (SELECT coalesce(sum(r.amount),0) FROM public.lisan_credit_operations r
                             WHERE r.debit_id = o.operation_id AND r.kind = 'refund' AND r.status = 'done')
        ORDER BY o.created_at LIMIT 100
    ) p;
$$;

REVOKE ALL ON FUNCTION public.lisan_credit_begin(uuid,uuid,text,integer,uuid) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.lisan_credit_finish(uuid,text,integer) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.lisan_credit_refund(uuid,uuid,integer,uuid,text) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.lisan_cancel_clone_debit(uuid,uuid,integer) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.lisan_pending_clone_refunds() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.lisan_credit_begin(uuid,uuid,text,integer,uuid) TO postgres,service_role;
GRANT EXECUTE ON FUNCTION public.lisan_credit_finish(uuid,text,integer) TO postgres,service_role;
GRANT EXECUTE ON FUNCTION public.lisan_credit_refund(uuid,uuid,integer,uuid,text) TO postgres,service_role;
GRANT EXECUTE ON FUNCTION public.lisan_cancel_clone_debit(uuid,uuid,integer) TO postgres,service_role;
GRANT EXECUTE ON FUNCTION public.lisan_pending_clone_refunds() TO postgres,service_role;
COMMIT;
-- INSTALL END

-- VERIFY (read only, run separately after INSTALL):
-- SELECT has_function_privilege('anon','public.lisan_credit_refund(uuid,uuid,integer,uuid,text)','EXECUTE') AS anon_can_run,
--        has_function_privilege('authenticated','public.lisan_credit_refund(uuid,uuid,integer,uuid,text)','EXECUTE') AS member_can_run,
--        has_function_privilege('service_role','public.lisan_credit_refund(uuid,uuid,integer,uuid,text)','EXECUTE') AS server_can_run;
-- Expected: false, false, true. Use the debit-file VERIFY section for transactional examples.

-- ROLLBACK (disable code first, drop debit-file functions FIRST; preserve audit data):
-- BEGIN;
-- DROP FUNCTION IF EXISTS public.lisan_pending_clone_refunds();
-- DROP FUNCTION IF EXISTS public.lisan_cancel_clone_debit(uuid,uuid,integer);
-- DROP FUNCTION IF EXISTS public.lisan_credit_refund(uuid,uuid,integer,uuid,text);
-- DROP FUNCTION IF EXISTS public.lisan_credit_finish(uuid,text,integer);
-- DROP FUNCTION IF EXISTS public.lisan_credit_begin(uuid,uuid,text,integer,uuid);
-- COMMIT;
-- Do NOT drop lisan_credit_operations: it retains authoritative receipts.
-- No existing add_credits/deduct_credits/deduct_subscription_credits is changed.
