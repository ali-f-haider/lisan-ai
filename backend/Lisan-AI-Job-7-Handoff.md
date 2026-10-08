# Lisan AI — Job 7 handoff

Status: Job 7 implementation and offline validation complete. All nine audit proofs pass; all ten safety assertions are unchanged. This is a reviewable local delivery. **Install SQL first, then publish the complete file list below.** No commit, push, hosted SQL or paid service call was performed by ChatGPT.

Starting reviewed checkout: `1aa9ce200305dafaabe7b0a1d7887df11b1e2ea7`. Final reviewed checkout: `47734692bd17bec9fc59eebc41bda20a584ca8d1` plus the listed local changes. While this job was in progress, Ali's separate commit `4773469` included the Job 7 main.py/app.js edits together with other credits-display work. Those files therefore partly exist in HEAD already, but their new helper and SQL files must also be included. Do not publish only main.py or app.js. All unrelated owner edits are preserved.

Version: Claude owns the version when merging. The observed local version is 1.82.19; the completed batch should receive the version Claude chooses. config.py is excluded from the delivery and commands. Every protected audio source, dub_long.html, protected main.py long-dub function and voice_match_routes.register block remains untouched by this job.

## 7a — Admin adjustments

The permanent balance, actual signed change, spending row, audit reason and fulfilled receipt are written together under a row lock. The saved UUID binds the account, delta and reason. A replay returns the saved outcome; a conflict is refused. Deducting beyond the balance still clamps to zero. The browser saves the request before sending, keeps it over network failure/reload and shows the actual change and receipt balance. Deliberate subsequent adjustments receive a new UUID. Inputs are checked before a local pending intent is stored. Missing SQL returns 503, with no old setter fallback.

Files: main.py, credit_actions.py, admin.html, SQL 01; tests/billing_action_fixtures.py, tests/test_credit_actions.py, tests/test_credit_audit.py, tests/test_shortdub_operations.py, tests/test_job7_ui.js, tests/test_job7_sql.mjs. Turns proofs 3, 4, 5 green. Validation: all 21 credit-action unit tests (shared with 7b), all 11 Job 7 browser tests and all 21 new SQL integration groups pass, including admin rollback, replay and clamp checks.

## 7b — Subscription invoices

A new receipt binds invoice ID, account, whole allowance and both service-period boundaries. The transaction replaces only expiring credits, resets cycle clone usage and writes the full new allowance plus a separate expiry of unused credits. It never adds a subscription allowance to permanent credits. Intent recording does not prove fulfillment: only a completed transaction does. Webhooks and sync acknowledge no unconfirmed grant. Checkout/upgrade pause if new subscription delivery is unavailable. Pending plan changes and recovered subscription metadata are saved only after a confirmed allowance.

Invoices ending before the current period become stale, with no replacement/reset/history grant. Old unsafe invoice rows remain review-only: receipt existence is not delivery evidence. Conflicting details create a durable review event. Billing sync checks the latest paid subscription invoice, without replaying a backlog that would repeatedly erase remaining credits.

Use subscription line service periods, not invoice accrual dates: [invoice line object](https://docs.stripe.com/api/invoice-line-item/object), [invoice object](https://docs.stripe.com/api/invoices/object). Missing/ambiguous/paginated line periods pause delivery rather than guess.

Files: main.py, credit_actions.py, billing_health.py, admin.html, account.html, SQL 02/03; caller, privacy and SQL tests. Turns proofs 1, 2, 8 green. Validation: all 21 shared credit-action tests and all 21 new SQL integration groups pass, including renewal replay, mismatch, stale/legacy review, expiry and permissions. Approved history presentation: full allowance plus separate expiry.

## 7c — Assistant debt

Each account has an atomically replaced, fsynced owed-credit checkpoint. The exact debit UUID and amount are saved before money moves; lost replies and a lost checkpoint replay that UUID. Fractional debt survives. Existing saved debt migrates without the previous five-credit clipping. After recovery the current balance is read again; the old receipt balance cannot authorize another paid question. An interrupted provider request with unknown cost pauses for human review, instead of making another paid call. A confirmed insufficient debit preserves debt and permits a later funded settlement under a new ID, since its refused ID is terminal.

Files: assistant_billing.py, main.py, tests/test_assistant_billing.py, audit proof fixture. Turns proof 6 green. Validation: all nine assistant-billing tests pass, including lost replies/checkpoints, restart recovery and current-balance checks. Storage is DATA_DIR/assistant_payments/<account UUID>.json. Keep DATA_DIR on the existing persistent Railway volume. The account locks assume the site's current single Python process; no multiple-replica claim is made.

## 7d — Lip-sync

The page stores one durable UUID per deliberate take. Under the existing project lock, the backend excludes another intent while charging, starting or running. A retry returns the original job. A new UUID may buy a distinct take after the previous result is known. The receipt fingerprints actual input files, resolution and watermark; replaced/missing completed outputs do not trigger new generation. A server restart with an unknown running result stops with a review message, without another charge/provider. Checkpoints use fsync, atomic replacement and directory fsync on Linux. Existing failure refund policy is unchanged.

Files: lipsync_operations.py, main.py, app.js, tests/test_lipsync_operations.py, browser/proof fixtures. Turns proof 7 green. Validation: all 12 lip-sync operation tests and all 11 Job 7 browser tests pass, including concurrent intent exclusion, reload/retry and unknown outcomes. Receipts: OUTPUT_DIR/<job>_lipsync_requests/<UUID>.json; active pointer: OUTPUT_DIR/<job>_lipsync_active.json. Automatic expiry is not invented: these small intent records are retained pending an owner policy. Completed media keeps the existing storage retention policy.

## 7e — Signup credits follow the saved admin setting

The missing `sql/2026-10-08-04-signup-credits.sql` is now present. It is separate and optional, and replaces only `public.handle_new_user`. The existing trigger is preserved. The amount is read from the actual saved admin column `public.pricing_config.free_credits`, for `id='singleton'`, ordered by updated_at descending, matching main.py::_get_pricing_config and _save_pricing_config. Only whole values 1–1,000 are accepted. Missing, invalid, zero, nonfinite or excessive settings use the required 100-credit fallback. The 1,000 ceiling comes from the existing admin field; the field/save validator now agree on whole numbers 1–1,000. The public/admin default and invalid stored setting also show 100, matching the trigger rather than showing the previous 150 fallback.

Profile insertion and signup history share the auth transaction. An existing profile receives neither a second allowance nor duplicate history. If history cannot be written, the profile/auth insertion rolls back; no unlogged signup balance is created. Existing users are not backfilled or overwritten. The exact original fixed-100 function is embedded as the rollback from tests/fixtures/signup_credit_trigger.sql. Install this step last, or explicitly skip it and keep fixed-100 signup with no new signup history. Extended Billing health reports whether the configured signup trigger is active; this optional flag is not a required paid-billing readiness flag.

Files: signup_credits.py, credit_billing.py::validate_pricing, main.py::_get_pricing_config/_save_pricing_config, admin.html signup field, account.html bilingual signup label, SQL 04, optional health flag in SQL 03/billing_health.py, tests/test_signup_credits.py, tests/test_credit_billing.py, tests/test_credit_audit.py and tests/test_job7_sql.mjs. Turns proof 9 green. Validation: all six signup unit tests pass; the 21 new SQL integration groups include the actual configured signup, fallback, existing-profile, history-failure and exact rollback checks. The old free-pricing test was updated only to exclude zero signup credits; zero reserve, helper price/budget and intentionally waived long-analysis/processing remain supported.

## SQL installation and removal

Run SQL first, then deploy code. Open the correct project in Supabase, click SQL Editor, create a new query, copy the entire numbered file, click Run. Success should show no errors. Open another new query and run the file's commented VERIFY queries with the '-- ' prefixes removed. Each step's verification is read-only and needs no real purchase/signup. Do not run any file's UNDO block during installation.

### Step 1 — Admin adjustments

Open [2026-10-08-01-admin-adjust.sql](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/sql/2026-10-08-01-admin-adjust.sql>). Copy the whole file into a new Supabase SQL Editor query and click Run. It adds admin operation receipts, the shared mismatch-review table, and lisan_admin_begin/lisan_admin_adjust. Existing SQL functions are unchanged. The file uses a transaction: if an installation prerequisite is missing, stop and send the error for review; do not continue to step 2.

In another new query, run the following read-only check. It should show false / false / true; saved_adjustments may be zero.

```sql
SELECT has_function_privilege('anon','public.lisan_admin_adjust(uuid,uuid,integer,text)','EXECUTE') AS anon,
       has_function_privilege('authenticated','public.lisan_admin_adjust(uuid,uuid,integer,text)','EXECUTE') AS member,
       has_function_privilege('service_role','public.lisan_admin_adjust(uuid,uuid,integer,text)','EXECUTE') AS server;
SELECT count(*) AS saved_adjustments FROM public.lisan_admin_adjustments;
```

To undo later: first undo step 3 if installed, then copy step 1's bottom UNDO block into a new query, remove each `-- ` prefix and run it. This drops only the two new functions; keep tables, receipts, review entries and history. New-code adjustments then return 503. Never use CASCADE or delete receipts to make an ID reusable.

### Step 2 — Subscription delivery

Open [2026-10-08-02-subscription-grant.sql](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/sql/2026-10-08-02-subscription-grant.sql>). Run its whole file after step 1. It adds subscription receipts and new begin/grant/readiness functions. No existing subscription function is replaced, and the old permanent-credit fallback is not used.

Run this check in a new query. The first result must contain ready=true; permissions must be false / false / true.

```sql
SELECT public.lisan_subscription_ready();
SELECT has_function_privilege('anon','public.lisan_subscription_grant(text,uuid,integer,timestamptz,timestamptz)','EXECUTE') AS anon,
       has_function_privilege('authenticated','public.lisan_subscription_grant(text,uuid,integer,timestamptz,timestamptz)','EXECUTE') AS member,
       has_function_privilege('service_role','public.lisan_subscription_grant(text,uuid,integer,timestamptz,timestamptz)','EXECUTE') AS server;
SELECT status,count(*) FROM public.lisan_subscription_receipts GROUP BY status;
```

To undo: undo step 3 first if installed; run the commented UNDO block at the bottom of step 2 after removing `-- ` prefixes. Only its new functions are removed. Subscription checkout/upgrades and unconfirmed delivery then pause. Receipts/history remain; retry after reinstalling with the same identities. Never mark a historical invoice fulfilled solely because an old receipt exists.

### Step 3 — Extended Billing health

Open [2026-10-08-03-billing-health-v2.sql](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/sql/2026-10-08-03-billing-health-v2.sql>). Run the whole file after steps 1 and 2. It adds a new read-only function and safe timestamp parser; the installed Job 6 health/readiness functions remain unchanged.

```sql
SELECT public.lisan_billing_health_v2();
SELECT has_function_privilege('anon','public.lisan_billing_health_v2()','EXECUTE') AS anon,
       has_function_privilege('authenticated','public.lisan_billing_health_v2()','EXECUTE') AS member,
       has_function_privilege('service_role','public.lisan_billing_health_v2()','EXECUTE') AS server;
```

The JSON version and ready.version must be 2. Required debit/pack/refund/cancel/admin_adjust/subscription_grant flags should be true if all their SQL is installed. signup_credits is normally false before optional step 4; that is allowed. Counts may be nonzero; read the lists instead of assuming an installation failed. Each list shows at most the newest 20 opaque references; full counts remain. Undated old invoices honestly have unknown age. Permissions must be false / false / true.

To undo: run its bottom commented UNDO block with the `-- ` prefixes removed. It drops only its two new read-only functions. The admin page falls back to original Job 6 diagnostics and notes that extended checks are unavailable; no money is moved.

### Step 4 — Optional signup setting (run last, or deliberately skip)

Open [2026-10-08-04-signup-credits.sql](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/sql/2026-10-08-04-signup-credits.sql>). Read the documented 1–1,000 limit and 100 fallback. If you choose configured signup credits, run the whole file in a new query after steps 1–3. This is the one explicit exception to additive SQL: it replaces handle_new_user while retaining on_auth_user_created. It also adds a private amount helper. It makes no changes to already-existing accounts.

```sql
SELECT id,free_credits,updated_at FROM public.pricing_config
WHERE id='singleton' ORDER BY updated_at DESC LIMIT 1;
SELECT public.lisan_signup_credit_amount() AS effective_signup_credits;
SELECT t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t
WHERE t.tgrelid='auth.users'::regclass AND t.tgname='on_auth_user_created';
SELECT has_function_privilege('anon','public.lisan_signup_credit_amount()','EXECUTE') AS anon,
       has_function_privilege('authenticated','public.lisan_signup_credit_amount()','EXECUTE') AS member,
       has_function_privilege('service_role','public.lisan_signup_credit_amount()','EXECUTE') AS server;
SELECT public.lisan_billing_health_v2()->'ready'->'signup_credits' AS configured_signup_active;
```

The effective amount must equal the saved whole value 1–1,000; missing/invalid settings show 100. The same original trigger should still be shown. Permissions are false / false / true; configured_signup_active should be true. These checks do not create a real account or consume credits.

To undo only signup: open this file's bottom UNDO block. Copy from its `-- BEGIN;` through `-- COMMIT;`, remove each `-- ` prefix and run that block in a new query. It restores the exact supplied original fixed-100 function, then removes the new helper. Keep the existing trigger, all profiles and all grant history; there is no clawback. The configured signup flag becomes false. Steps 1–3 remain installed.


Undoing 01–03 preserves paid identities; tested reinstall reuses receipts and cannot repeat a prior adjustment/grant. Functions are deliberately not silently overwritten: a duplicate installation aborts; use the stated undo/reinstall sequence. No CASCADE and no deletion of financial receipts.

| Missing SQL | Paused or unchanged behavior |
| --- | --- |
| 01 | Admin adjustments return 503; 02 cannot be installed before its shared review table exists. Other existing billing is unchanged. |
| 02 | New subscription checkout/upgrades and unconfirmed delivery pause; webhook/sync return non-success for retry. No permanent fallback. |
| 03 | Original read-only health remains available; extended lists/readiness are marked unavailable. It does not disable unrelated existing billing. |
| Optional 04 | Old fixed-100 signup remains, without new signup history. No existing user is backfilled. |

## Final validation

| Check | Run / passed | Skipped | Failed |
| --- | ---: | ---: | ---: |
| Full Python discovery | 472 / 472 | 0 | 0 |
| Original audit proofs (included above) | 9 / 9 | 0 | 0 |
| Every tests/*.js browser test | 84 / 84 | 0 | 0 |
| Actual new SQL integration groups | 21 / 21 | 0 | 0 |
| Original atomic-credit SQL integration groups | 21 / 21 | 0 | 0 |
| Original billing-health SQL integration groups | 9 / 9 | 0 | 0 |

Full Python runtime: 324.696s, with synthetic audio and local FFmpeg. The command was python -m unittest discover -s tests (verbose output added), using the available Python runtime/site-packages and an isolated DATA_DIR; external urllib requests were blocked, with individual tests supplying their mocks. Every .js file was run with Node's built-in test runner. Actual SQL ran in PGlite/PostgreSQL 18.3 in memory, without hosted database or paid provider calls. This does not claim verification of the live schema or simultaneous database sessions. SQL installation preflights/verification queries cover the deployed-schema check Ali performs.

All ten AST-extracted safety assertions in the nine original proofs are unchanged. Only setup/mocks changed with Ali's explicit approval, to exercise the new transactional contracts rather than old setters. No skips or expectedFailure were added. The real Git hygiene test passed; no hygiene exception is needed.

New test coverage: failed admin history/audit rollback, same intent/new intent, identity conflicts, missing functions, malformed receipts, lost renewal replies, immutable service periods, webhook/sync pending status, metadata changes after confirmation, full/expiry grant entries, newest-20 privacy, unknown legacy dates, fsynced assistant checkpoints/lost replies/restart/insufficient funds, historical balance refresh, lip-sync duplicate exclusion/missing assets/unknown outcome, page reload/browser storage failure, whole signup bounds/defaults and exact rollback. SQL tests execute all four delivered files, test uninstall/reinstall replay protection, and execute the actual signup trigger with configured 250 and history rollback.

Protected files and protected main blocks were checked against the starting snapshot. config.py's owner version change was preserved and excluded. Source syntax and Git whitespace checks passed. Existing mixed/CRLF/LF line endings are preserved; full files are supplied to preserve them on transfer. No whole-file reformatting was performed.

## Questions for Ali / Claude

- Review signup credits 1–1,000 with fallback 100. The maximum comes from the existing admin field, and the positive minimum/fallback implement Job 7; a different policy can be adjusted before optional SQL installation. No answer was assumed or represented as approval.
- Confirm implemented older-invoice rule: period end strictly before current period -> stale, no delivery, human review.
- Decide failed lip-sync refunds: full refund for a known failure, proportional, or none. For an unknown provider outcome, keep it awaiting human review until evidence establishes the outcome. Current refund policy is unchanged.
- Confirm whether the admin reason should remain immutable for the same request ID, as implemented; no audit reason is silently changed on replay.
- Choose receipt retention for assistant/lip-sync: presently retained; no paid identity is silently discarded. Database financial receipts/history are retained through SQL undo.
- Decide reconciliation of historical unsafe subscription rows only after checking invoice, balance and history evidence; no grant is inferred from the old receipt alone.

## Publish commands (Command Prompt, from backend)

SQL first as above, then the complete code batch. All files are already saved in the local project; there is no code to copy into them. The archive is a review/backup copy. Main/app include the owner’s current committed credits-display work; that is preserved. Existing unrelated login/long-dub edits, config.py and protected audio files are excluded. The scoped commit below includes only the named files even if other work is staged.

```bat
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"
git --no-pager add -- main.py admin.html app.js account.html billing_health.py credit_actions.py assistant_billing.py lipsync_operations.py sql/2026-10-08-01-admin-adjust.sql sql/2026-10-08-02-subscription-grant.sql sql/2026-10-08-03-billing-health-v2.sql tests/test_credit_audit.py tests/test_shortdub_operations.py tests/billing_action_fixtures.py tests/test_credit_actions.py tests/test_assistant_billing.py tests/test_lipsync_operations.py tests/test_job7_ui.js tests/test_job7_sql.mjs credit_billing.py tests/test_credit_billing.py signup_credits.py sql/2026-10-08-04-signup-credits.sql tests/test_signup_credits.py Lisan-AI-Job-7-Handoff.md
git --no-pager commit --only -m "Complete Job 7 billing safety and signup credit fixes" -- main.py admin.html app.js account.html billing_health.py credit_actions.py assistant_billing.py lipsync_operations.py sql/2026-10-08-01-admin-adjust.sql sql/2026-10-08-02-subscription-grant.sql sql/2026-10-08-03-billing-health-v2.sql tests/test_credit_audit.py tests/test_shortdub_operations.py tests/billing_action_fixtures.py tests/test_credit_actions.py tests/test_assistant_billing.py tests/test_lipsync_operations.py tests/test_job7_ui.js tests/test_job7_sql.mjs credit_billing.py tests/test_credit_billing.py signup_credits.py sql/2026-10-08-04-signup-credits.sql tests/test_signup_credits.py Lisan-AI-Job-7-Handoff.md
git --no-pager push origin main
```

Railway deploys the push automatically. After deployment, hard-refresh the browser so old pages do not submit requests without the new UUID fields, and read Billing health to confirm required readiness and the optional signup flag. No real purchase/generation is required for these read-only checks. If a required SQL function is absent, leave the action paused and install/retry safely; do not restore an unsafe old money fallback.

## Changed files and line endings

The ZIP contains full current versions of only the named delivery files. Newlines below are raw byte counts (CRLF / LF alone / CR alone); mixed existing files retain the endings of unchanged lines. The accompanying JSON manifest gives source path, size and SHA-256 per file. The handoff itself uses LF.

| File | CRLF | LF alone | CR alone |
| --- | ---: | ---: | ---: |
| main.py | 8752 | 0 | 0 |
| admin.html | 2627 | 13 | 206 |
| app.js | 8317 | 18 | 0 |
| account.html | 802 | 0 | 0 |
| billing_health.py | 0 | 60 | 0 |
| credit_actions.py | 0 | 129 | 0 |
| assistant_billing.py | 0 | 142 | 0 |
| lipsync_operations.py | 0 | 179 | 0 |
| sql/2026-10-08-01-admin-adjust.sql | 0 | 112 | 0 |
| sql/2026-10-08-02-subscription-grant.sql | 0 | 130 | 0 |
| sql/2026-10-08-03-billing-health-v2.sql | 0 | 79 | 0 |
| tests/test_credit_audit.py | 0 | 116 | 0 |
| tests/test_shortdub_operations.py | 0 | 299 | 0 |
| tests/billing_action_fixtures.py | 0 | 69 | 0 |
| tests/test_credit_actions.py | 0 | 205 | 0 |
| tests/test_assistant_billing.py | 0 | 99 | 0 |
| tests/test_lipsync_operations.py | 0 | 106 | 0 |
| tests/test_job7_ui.js | 0 | 97 | 0 |
| tests/test_job7_sql.mjs | 0 | 213 | 0 |
| credit_billing.py | 0 | 317 | 0 |
| tests/test_credit_billing.py | 0 | 466 | 0 |
| signup_credits.py | 30 | 0 | 0 |
| sql/2026-10-08-04-signup-credits.sql | 68 | 0 | 0 |
| tests/test_signup_credits.py | 0 | 61 | 0 |

## Changed main.py functions / class

- [LipSyncRequest](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:309>).
- [billing_subscribe](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:1699>).
- [billing_change_plan](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:1952>).
- [billing_sync](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:2192>).
- [_invoice_billing_period](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:2283>).
- [_subscription_credit_pause](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:2310>).
- [_subscription_grant_response](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:2317>).
- [stripe_webhook](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:2346>).
- [_grant_subscription_credits](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:3221>).
- [_cleanup_worker](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:3475>).
- [lipsync](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:4829>).
- [lipsync_progress](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:4911>).
- [_get_pricing_config](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:6528>).
- [_save_pricing_config](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:6798>).
- [admin_adjust_credits](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:7194>).
- [assistant_chat](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:8150>).

Page changes: adminAdjustmentUuid/adminAdjustmentSaved/submitAdminAdjustment, adjustCredits/setCredits/loadBillingHealth and signup number field in admin.html; lipsyncIntentKey/lipsyncIntent/saveLipsyncIntent/finishLipsyncIntent/runLipsync/checkLipsyncProgress in app.js; creditChange plus bilingual grant/expiry labels in account.html. Helper modules separate safe persistence and transactional RPC verification from main.py; no protected audio pipeline was changed.
