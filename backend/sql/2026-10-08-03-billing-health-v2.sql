-- JOB 7 / STEP 3: run AFTER steps 1 and 2. Read-only diagnostics.
-- The installed Job 6 health/readiness functions are never replaced.
BEGIN;
CREATE FUNCTION public.lisan_billing_timestamp(p_value text)
RETURNS timestamptz LANGUAGE plpgsql STABLE SET search_path=pg_catalog,pg_temp SET timezone='UTC' AS $$
DECLARE value timestamptz;
BEGIN
    IF p_value IS NULL OR p_value='' THEN RETURN NULL; END IF;
    value:=p_value::timestamptz;
    RETURN CASE WHEN isfinite(value) THEN value ELSE NULL END;
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END $$;
CREATE FUNCTION public.lisan_billing_health_v2()
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    WITH base AS (SELECT public.lisan_billing_health() AS value), attention AS (
        SELECT 'admin_pending' AS category,operation_id AS reference_id,uid,
          abs(requested_delta)::bigint AS amount,created_at,'admin_adjust' AS kind
        FROM public.lisan_admin_adjustments WHERE status='pending'
        UNION ALL
        SELECT 'subscription_pending',reference_id,uid,amount::bigint,created_at,'subscription_grant'
        FROM public.lisan_subscription_receipts WHERE status='pending'
        UNION ALL
        SELECT 'subscription_stale',reference_id,uid,amount::bigint,created_at,'subscription_stale'
        FROM public.lisan_subscription_receipts WHERE status='stale'
        UNION ALL
        SELECT 'receipt_mismatches',reference_id,uid,amount::bigint,created_at,kind
        FROM public.lisan_billing_review_events WHERE reviewed_at IS NULL
        UNION ALL
        -- Never assume old receipt existence proves a grant. Old schemas may
        -- lack created_at; those dates remain honestly unknown, sorted last.
        SELECT 'subscription_legacy',md5('invoice:'||i.invoice_id)::uuid,i.uid,i.credits::bigint,
          public.lisan_billing_timestamp(to_jsonb(i)->>'created_at'),'subscription_legacy'
        FROM public.subscription_invoices i
        WHERE NOT EXISTS(SELECT 1 FROM public.lisan_subscription_receipts n WHERE n.invoice_id=i.invoice_id AND n.status='done')
    ), ranked AS (
        SELECT *,row_number() OVER(PARTITION BY category ORDER BY created_at DESC NULLS LAST,reference_id,uid) AS rank
        FROM attention
    ), grouped AS (
        SELECT category,count(*) AS count,
          coalesce(jsonb_agg(jsonb_build_object('operation_id',reference_id,'account_id',uid,
            'amount',amount,'age_seconds',CASE WHEN created_at IS NULL THEN NULL
              ELSE greatest(0,extract(epoch FROM now()-created_at)::bigint) END,'kind',kind)
            ORDER BY created_at DESC NULLS LAST,reference_id,uid) FILTER(WHERE rank<=20),'[]'::jsonb) AS rows
        FROM ranked GROUP BY category
    )
    SELECT base.value || jsonb_build_object('version',2,'ready',
      (base.value->'ready') || jsonb_build_object(
        'version',2,'admin_adjust',to_regprocedure('public.lisan_admin_begin(uuid,uuid,integer,text)') IS NOT NULL
          AND to_regprocedure('public.lisan_admin_adjust(uuid,uuid,integer,text)') IS NOT NULL,
        'subscription_grant',to_regprocedure('public.lisan_subscription_begin(text,uuid,integer,timestamptz,timestamptz)') IS NOT NULL
          AND to_regprocedure('public.lisan_subscription_grant(text,uuid,integer,timestamptz,timestamptz)') IS NOT NULL
          AND to_regprocedure('public.lisan_subscription_ready()') IS NOT NULL,
        'signup_credits',to_regprocedure('public.lisan_signup_credit_amount()') IS NOT NULL
          AND EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid=to_regprocedure('public.handle_new_user()')
            AND position('lisan_signup_credit_amount' IN p.prosrc)>0)),
      'lists',(base.value->'lists') || (
        SELECT jsonb_object_agg(k.category,jsonb_build_object('count',coalesce(g.count,0),'rows',coalesce(g.rows,'[]'::jsonb)))
        FROM (VALUES('admin_pending'),('subscription_pending'),('subscription_stale'),('receipt_mismatches'),('subscription_legacy')) k(category)
        LEFT JOIN grouped g USING(category)))
    FROM base;
$$;
REVOKE ALL ON FUNCTION public.lisan_billing_timestamp(text),public.lisan_billing_health_v2() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.lisan_billing_timestamp(text),public.lisan_billing_health_v2() TO postgres,service_role;
NOTIFY pgrst,'reload schema';
COMMIT;

-- VERIFY separately: JSON version 2; ready also has admin_adjust/subscription_grant.
-- SELECT public.lisan_billing_health_v2();
-- SELECT has_function_privilege('anon','public.lisan_billing_health_v2()','EXECUTE') AS anon,
--        has_function_privilege('authenticated','public.lisan_billing_health_v2()','EXECUTE') AS member,
--        has_function_privilege('service_role','public.lisan_billing_health_v2()','EXECUTE') AS server;
-- Expected false / false / true. At most 20 rows in each list; undated legacy rows have null age.
-- UNDO (no money/history changes):
-- BEGIN;
-- DROP FUNCTION IF EXISTS public.lisan_billing_health_v2();
-- DROP FUNCTION IF EXISTS public.lisan_billing_timestamp(text);
-- NOTIFY pgrst,'reload schema';
-- COMMIT;
-- The page falls back to the read-only Job 6 health card. Money paths still require their own confirmed receipts.
