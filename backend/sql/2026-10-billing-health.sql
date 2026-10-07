-- Ali installs this BEFORE deploying Job 6. Read-only; no balances change.
-- Job 5 SQL must already be installed. Run the whole file for INSTALL only.
-- Existing functions are never replaced. A second install reports "already exists".
BEGIN;
CREATE FUNCTION public.lisan_billing_health()
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    WITH returned AS (
        SELECT debit_id, sum(amount::bigint) AS amount
        FROM public.lisan_credit_operations
        WHERE kind = 'refund' AND status = 'done' GROUP BY debit_id
    ), owed AS (
        SELECT o.*, greatest(0, o.refund_due - coalesce(r.amount, 0)) AS remaining
        FROM public.lisan_credit_operations o
        LEFT JOIN returned r ON r.debit_id = o.operation_id
        WHERE o.kind = 'debit' AND o.status = 'done'
          AND o.work_status IN ('failed', 'partial')
          AND o.refund_due > coalesce(r.amount, 0)
    ), attention AS (
        SELECT 'pending' AS category, operation_id::text, uid, amount::bigint,
               created_at, kind
        FROM public.lisan_credit_operations
        WHERE status = 'pending' AND created_at < now() - interval '10 minutes'
        UNION ALL
        SELECT 'refunds_owed', operation_id::text, uid, remaining, created_at, kind FROM owed
        UNION ALL
        -- Job 5 returns legacy_review but stores pending. Identify that
        -- same condition without altering the existing fulfillment function.
        -- Expose an opaque reference, never the checkout receipt/payment ID.
        SELECT 'legacy_packs', md5('pack:' || p.receipt)::uuid::text, p.uid,
               p.amount::bigint, p.created_at, 'pack'
        FROM public.lisan_credit_pack_receipts p
        WHERE p.status = 'legacy_review' OR (p.status = 'pending' AND EXISTS (
            SELECT 1 FROM public.credit_orders c WHERE c.session_id = p.receipt))
        UNION ALL
        -- Same pending-clone criteria as lisan_pending_clone_refunds(), but
        -- count ALL rows and show newest 20, not that worker's oldest 100.
        SELECT 'clone_refunds', operation_id::text, uid, remaining, created_at, kind
        FROM owed WHERE action IN ('clone', 'custom_voice')
    ), ranked AS (
        SELECT *, row_number() OVER (PARTITION BY category ORDER BY created_at DESC, operation_id) AS rank
        FROM attention
    ), groups AS (
        SELECT category, count(*) AS count,
               coalesce(jsonb_agg(jsonb_build_object(
                   'operation_id', operation_id, 'account_id', uid,
                   'amount', amount, 'age_seconds', greatest(0, extract(epoch FROM now() - created_at)::bigint),
                   'kind', kind) ORDER BY created_at DESC, operation_id) FILTER (WHERE rank <= 20), '[]'::jsonb) AS rows
        FROM ranked GROUP BY category
    )
    SELECT jsonb_build_object(
        'ready', public.lisan_billing_ready(),
        'lists', (SELECT jsonb_object_agg(k.category,
            jsonb_build_object('count', coalesce(g.count, 0), 'rows', coalesce(g.rows, '[]'::jsonb)))
            FROM (VALUES ('pending'), ('refunds_owed'), ('legacy_packs'), ('clone_refunds')) k(category)
            LEFT JOIN groups g USING (category)));
$$;
REVOKE ALL ON FUNCTION public.lisan_billing_health() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.lisan_billing_health() TO postgres, service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;

-- VERIFY: run these uncommented queries separately in SQL Editor.
-- SELECT public.lisan_billing_health();
-- Expected: ready version 1 and true debit/pack/refund/cancel flags;
-- lists pending/refunds_owed/legacy_packs/clone_refunds each has count and rows.
-- Each list has at most 20 rows, with IDs, amount, age_seconds and kind only.
-- SELECT has_function_privilege('anon', 'public.lisan_billing_health()', 'EXECUTE') AS anon_can_run,
--        has_function_privilege('authenticated', 'public.lisan_billing_health()', 'EXECUTE') AS member_can_run,
--        has_function_privilege('service_role', 'public.lisan_billing_health()', 'EXECUTE') AS server_can_run;
-- Expected: false, false, true.

-- UNDO: only the new read-only function is removed; no table or receipt is deleted.
-- BEGIN;
-- DROP FUNCTION IF EXISTS public.lisan_billing_health();
-- NOTIFY pgrst, 'reload schema';
-- COMMIT;
-- The Job 6 card then says "Not installed yet". Paid billing remains unchanged.
