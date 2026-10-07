-- Apply THIRD (step 3 of 3), after 2026-10-credit-operations.sql and 2026-10-atomic-credit-debit.sql,
-- BEFORE deploying the code that uses it. It only ADDS one new function. No existing table or function changes.
-- INSTALL only. VERIFY and ROLLBACK are separate commented selections.
--
-- Why: a long video is paid once, but pieces of that payment can come back at different moments (a lip-sync clip
-- that fails, lines left silent, a repair that was not needed, the whole job failing). Every piece is its own
-- refund with its own ID, so a repeated request returns the earlier result and grants nothing twice.
-- lisan_credit_refund (already installed) refuses a refund larger than the "refund due" of the payment; this
-- function raises that limit to cover THIS refund only, never above the amount originally paid, and then
-- calls the existing refund function. Refunds of work already marked delivered are refused.

-- INSTALL BEGIN
BEGIN;
DO $$
BEGIN
    IF to_regprocedure('public.lisan_credit_refund(uuid,uuid,integer,uuid,text)') IS NULL
       OR to_regprocedure('public.lisan_credit_begin(uuid,uuid,text,integer,uuid)') IS NULL THEN
        RAISE EXCEPTION 'Install 2026-10-credit-operations.sql first; installation stopped.';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION public.lisan_credit_refund_part(
    p_operation_id uuid, p_uid uuid, p_amount integer, p_debit_id uuid, p_split_rule text
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    op public.lisan_credit_operations%ROWTYPE;
    original public.lisan_credit_operations%ROWTYPE;
    refunded bigint;
BEGIN
    PERFORM public.lisan_credit_begin(p_operation_id,p_uid,'refund',p_amount,p_debit_id);
    SELECT * INTO STRICT op FROM public.lisan_credit_operations
    WHERE operation_id = p_operation_id FOR NO KEY UPDATE;
    IF op.status = 'pending' THEN
        SELECT * INTO STRICT original FROM public.lisan_credit_operations
        WHERE operation_id = p_debit_id FOR NO KEY UPDATE;
        IF original.uid <> p_uid OR original.kind <> 'debit' OR original.status <> 'done' THEN
            RAISE EXCEPTION 'Refund requires a confirmed original debit.' USING ERRCODE = '22023';
        END IF;
        IF original.work_status = 'delivered' THEN
            RAISE EXCEPTION 'Delivered work cannot be refunded.' USING ERRCODE = '22023';
        END IF;
        SELECT coalesce(sum(amount),0) INTO refunded FROM public.lisan_credit_operations
        WHERE debit_id = p_debit_id AND kind = 'refund' AND status = 'done';
        IF refunded + p_amount > original.amount THEN
            RAISE EXCEPTION 'Refund exceeds the amount paid.' USING ERRCODE = '22023';
        END IF;
        IF original.refund_due < refunded + p_amount THEN
            UPDATE public.lisan_credit_operations
            SET refund_due = (refunded + p_amount)::integer,
                work_status = CASE WHEN work_status = 'pending' THEN 'partial' ELSE work_status END,
                updated_at = clock_timestamp()
            WHERE operation_id = p_debit_id;
        END IF;
    END IF;
    RETURN public.lisan_credit_refund(p_operation_id,p_uid,p_amount,p_debit_id,p_split_rule);
END $$;

REVOKE ALL ON FUNCTION public.lisan_credit_refund_part(uuid,uuid,integer,uuid,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.lisan_credit_refund_part(uuid,uuid,integer,uuid,text) TO postgres,service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
-- INSTALL END

-- VERIFY: READ ONLY. Expected: one row, false, false, true.
-- SELECT has_function_privilege('anon','public.lisan_credit_refund_part(uuid,uuid,integer,uuid,text)','EXECUTE') AS anon_can_run,
--        has_function_privilege('authenticated','public.lisan_credit_refund_part(uuid,uuid,integer,uuid,text)','EXECUTE') AS member_can_run,
--        has_function_privilege('service_role','public.lisan_credit_refund_part(uuid,uuid,integer,uuid,text)','EXECUTE') AS server_can_run;

-- ROLLBACK (disable the code first; this changes nothing else):
-- BEGIN;
-- DROP FUNCTION IF EXISTS public.lisan_credit_refund_part(uuid,uuid,integer,uuid,text);
-- NOTIFY pgrst, 'reload schema';
-- COMMIT;
