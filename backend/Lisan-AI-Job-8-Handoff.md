# Lisan AI — Job 8 handoff

Implemented and validated locally against the pushed 1.82.23 checkout: `fd041e971d62b512cbe980f33608c52489ef1f60`. Ali supplied the actual 34-column pricing_config types on 2026-10-08 and confirmed the release was pushed. No commit, push, SQL change, live database request or paid call was performed. Claude owns the version; config.py is untouched and excluded from the commands. The files are already saved in Ali's local backend folder. During this job a separate developer changed the local version to 1.82.24 and updated notification login ownership in main.py/app.js/tests/test_notify_history.js. Those concurrent edits are preserved. The full-file archive and commands include their matching test so the shared files do not land with stale tests; Job 8 did not author or edit that test. The unified diff contains only Job 8 changes, excluding these concurrent notification/version edits.

## Part A — Refused replies cannot become successful saves

The shared admin API reader now rejects HTTP failures, ok:false, error-only replies and unreadable replies. Maintenance and pricing saves require explicit ok:true; credit adjustments retain their existing ok:true and operation_complete:true contract; backup retains its status:done contract and also honors refusal. Errors keep the server's reason, including structured detail, instead of treating HTTP 200 as success. Error toasts last 10 seconds and can be dismissed by clicking; success toasts retain 3.5 seconds and their existing behavior. A failed pricing reload also invalidates pricingLoaded so stale fields cannot be saved as freshly loaded settings. The memory-diagnostics failure path still clears stale numbers and explains unavailability after the central guard was strengthened.

The account page checks HTTP and the endpoint's actual positive result: ok:true for renewal/account removal/saved voices, a URL for the portal, a URL or already_canceling:true for cancellation, and status:success for finished-file deletion. A failed deletion leaves the file visible. A refused saved-voice edit hides an earlier Saved marker. Plain server reasons are shown in error dialogs; technical/provider details use the existing translated generic fallback. Subscription refresh no longer silently hides an ok:false response. This does not change subscription, deletion, refund or credit rules.

The main app now shares a refusal guard for payment/sync callbacks and the existing local mix callbacks. Checkout also requires a URL; plan changes require ok:true and a known mode. All four custom-voice upload variants honor ok:false. Manual cleanup does not report green success when cleanup is partial or the saved-voice list could not be verified. Hash fragments alone no longer announce completed payment/subscription activation: they say the status is being checked; existing fulfillment/sync callbacks announce only confirmed results. No extra payment/provider call or automatic retry was added. Earlier overridden handlers were kept and guarded too, rather than being rewritten or deleted.

### Complete admin call-site inventory

The table includes every direct API/fetch site in admin.html, including reads and the binary download. Line numbers are logical source lines; function names and endpoint names are the stable identifiers. Read rows do not announce successful mutations. Their shared parser now rejects a refused body; the normal nested diagnostic flags remain readable.

| File / line | Function | Endpoint | Verdict |
| --- | --- | --- | --- |
| admin.html:860 | doLogin | `/api/admin/login` | Fixed: token alone cannot override HTTP/refusal; failed sign-in is red. |
| admin.html:910 | loadBillingHealth | `/api/admin/billing_health` | Read: shared body guard plus installed check; detailed status/action names added in Part C. |
| admin.html:958 | loadMaintenance | `/api/admin/maintenance` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |
| admin.html:976 | saveMaintenance | `/api/admin/maintenance` | Fixed: HTTP plus explicit ok:true; otherwise red server reason. |
| admin.html:986 | clearMaintenance | `/api/admin/maintenance` | Fixed: HTTP plus explicit ok:true; otherwise red server reason. |
| admin.html:994 | loadUsers | `/api/admin/users?q=` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |
| admin.html:1048 | submitAdminAdjustment | `/api/admin/adjust_credits` | Already required ok:true and operation_complete:true; shared parser now also rejects error/refusal. |
| admin.html:1141 | loadAssistantToday | `/api/admin/assistant` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |
| admin.html:1149 | loadAssistantLog | `/api/admin/assistant` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |
| admin.html:1168 | loadConcInfo | `/api/admin/resource_usage` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |
| admin.html:1262 | renderPriceCheck | `/api/admin/resource_usage` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |
| admin.html:1307 | loadPricing | `/api/admin/pricing` | Read: additionally invalidates old loaded state and shows failed reload reason. |
| admin.html:1474 | savePricing | `/api/admin/pricing` | Strengthened the prior fix: requires ok:true; error-only/empty replies cannot be green. |
| admin.html:1531 | loadAudit | `/api/admin/audit` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |
| admin.html:1983 | loadLongDubLog | `/api/admin/longdub_log?q=` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |
| admin.html:2005 | exportLongDubLog | `/api/admin/longdub_log?q=` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |
| admin.html:2008 | exportLongDubLog | `/api/admin/resource_usage` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |
| admin.html:2034 | downloadLipDebug | `/api/admin/lipdebug/` | Binary read. HTTP check is appropriate: this route serves a ZIP or a non-200 error, not a JSON save receipt. Unchanged. |
| admin.html:2046 | loadHealth | `/api/admin/health` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |
| admin.html:2087 | runDbBackupNow | `/api/admin/db_backup_now` | Already required status:done; shared refusal guard prevents contradictory done/error success. |
| admin.html:2104 | loadStorage | `/api/admin/storage` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |
| admin.html:2130 | loadResourceUsage | `/api/admin/resource_usage` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |
| admin.html:2154 | loadRailwayMemory | `/api/admin/railway_memory` | Read: preserved unavailable rendering/cleared values when body ok:false is rejected. |
| admin.html:2186 | loadServiceUsage | `/api/admin/service_usage` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |
| admin.html:2312 | loadMemDiag | `/api/admin/mem_diag` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |
| admin.html:2857 | loadBusiness | `/api/admin/business?days=` | Read-only; shared parser rejects HTTP/ok:false/error. No successful-save announcement. |

### Complete account call-site inventory

| File / line | Function | Endpoint | Verdict |
| --- | --- | --- | --- |
| account.html:108 | logout button | `/api/logout` | Session navigation; no saved/charged confirmation, unchanged. Logout has no ok:false success branch. |
| account.html:513 | loadUserInfo | `/api/user/info` | Read-only rendering, not a successful-save decision; unchanged. |
| account.html:519 | refreshSubscriptionThenLoad | `/api/billing/refresh_subscription` | Fixed: HTTP plus ok:true; refusal shows safe server reason, no success/removal. |
| account.html:526 | manageSubscription | `/api/billing/portal` | Fixed: HTTP and valid URL; only cancellation accepts already_canceling:true. Refusal never redirects. |
| account.html:538 | cancelSubscription | `/api/billing/cancel` | Fixed: HTTP and valid URL; only cancellation accepts already_canceling:true. Refusal never redirects. |
| account.html:556 | resumeSubscription | `/api/billing/resume` | Fixed: HTTP plus ok:true; refusal shows safe server reason, no success/removal. |
| account.html:572 | confirmDeleteAccount | `/api/account/delete` | Fixed: HTTP plus ok:true; refusal shows safe server reason, no success/removal. |
| account.html:577 | page summary loader | `/api/account/summary` | Read-only history rendering, not a successful-save decision; unchanged. |
| account.html:693 | loadMyJobs | `/api/my_jobs` | Read-only rendering, not a successful-save decision; unchanged. |
| account.html:705 | deleteMyJob | `/api/my_jobs/` | Fixed: HTTP plus actual status:success; refuses error/ok:false before removing row. |
| account.html:766 | loadMyVoices | `/api/my_voices` | Read-only rendering, not a successful-save decision; unchanged. |
| account.html:784 | saveVoice | `/api/my_voices/` | Fixed: HTTP plus ok:true; refusal shows safe server reason, no success/removal. |
| account.html:801 | deleteVoice | `/api/my_voices/` | Fixed: HTTP plus ok:true; refusal shows safe server reason, no success/removal. |

### Main-app money/settings call-site inventory

There are three existing credit-sync callbacks (one background helper and two successive syncCreditsNow definitions), seven billing callbacks in total, five local-mix callbacks, four custom-voice upload variants, and four cleanup sites. Keeping the earlier overridden handlers guarded avoids bringing the defect back when scripts are reorganized.

| File / function / occurrence | Endpoint | Verdict |
| --- | --- | --- |
| app.js:1911 / confirmTimeline | `/api/remix_audio` | Already required HTTP plus status:success (async handler); unchanged. |
| app.js:4223 / openBuyModal | `/api/billing/packs` | Read-only catalog, no saved/charged confirmation; unchanged. |
| app.js:4370 / buyPack | `/api/billing/checkout` | Fixed: body refusal guard and positive URL required; button unlocks on refusal. |
| app.js:4423 / changeSubscriptionPlan | `/api/billing/change_plan?plan_key=` | Fixed: body refusal guard plus ok:true and known upgraded/scheduled/reverted mode. |
| app.js:4454 / subscribeMonthly | `/api/billing/subscribe?plan_key=` | Fixed: body refusal guard and positive URL required; button unlocks on refusal. |
| app.js:4557 / syncCredits | `/api/billing/sync` | Fixed: refusal guard before checking added credits; all three callbacks guarded. |
| app.js:4576 / syncCreditsNow | `/api/billing/sync` | Fixed: refusal guard before checking added credits; all three callbacks guarded. |
| app.js:4601 / fulfill | `/api/billing/fulfill` | Fixed: refusal guard before confirmed added-credit announcement. |
| app.js:4618 / syncCreditsNow | `/api/billing/sync` | Fixed: refusal guard before checking added credits; all three callbacks guarded. |
| app.js:4846 / applyVolumes | `/api/remix_audio` | Already required HTTP plus status:success (async handler); unchanged. |
| app.js:4959 / applyVolumesV2 | `/api/remix_audio` | Existing status:success check retained; added HTTP/body refusal guard to this callback. |
| app.js:5047 / onloadedmetadata | `/api/upload_custom_voice` | Fixed/strengthened: HTTP/refusal plus voice ID required; safe error message. Includes earlier overridden UI. |
| app.js:5232 / applyVolumesV2 | `/api/remix_audio` | Existing status:success check retained; added HTTP/body refusal guard to this callback. |
| app.js:5328 / cleanOldClones | `/api/cleanup_voices` | Fixed: manual cleanup refuses HTTP/body/partial errors before green result. |
| app.js:5344 / confirmCloning | `/api/cleanup_voices` | Automatic best-effort cleanup, no success announcement or charge; unchanged. |
| app.js:5925 / cleanOldClones | `/api/cleanup_voices` | Fixed: manual cleanup refuses HTTP/body/partial errors before green result. |
| app.js:5943 / resetWorkspace | `/api/cleanup_voices` | Automatic best-effort cleanup, no success announcement or charge; unchanged. |
| app.js:6159 / onloadedmetadata | `/api/upload_custom_voice` | Fixed/strengthened: HTTP/refusal plus voice ID required; safe error message. Includes earlier overridden UI. |
| app.js:6332 / onloadedmetadata | `/api/upload_custom_voice` | Fixed/strengthened: HTTP/refusal plus voice ID required; safe error message. Includes earlier overridden UI. |
| app.js:7887 / applyVolumesV2 | `/api/remix_audio` | Existing status:success check retained; added HTTP/body refusal guard to this callback. |
| app.js / uploadCustomVoice → tryUrl | `/api/upload_custom_voice`, fallback `/api/upload_custom_voice2` (dynamic URL) | Already checked HTTP/error/voice ID; now also rejects ok:false and sanitizes the displayed server reason. Existing fallback behavior unchanged. |
| app.js / credits-purchased hash handler | No server call | Fixed: information only until the existing sync/fulfillment confirms delivery. |
| app.js / subscription-active hash handler | No server call | Fixed: information only, no claim that a hash proves activation. |

Other app reads (pricing, user info, saved-voice list and voice listing) do not announce successful settings saves or credits delivery. Dubbing/transcription/translation/line-processing actions retain their existing job/result contracts; this is not a pipeline redesign. Local storage project/preferences writes do not call the server and are outside the HTTP-refusal pattern.

## Part B — Pricing matches the supplied database schema

Ali's exact 34-column type list is saved in tests/fixtures/pricing_config_columns.json as the test's source of truth. The test executes the actual savePricing form builder from admin.html in a synthetic DOM, captures its JSON body, runs the real validate_pricing and _save_pricing_config extracted from main.py, and mocks every database request. It verifies all eight request bodies: the main save and seven follow-ups. It covers every column written (32); price_per_min and markup are legacy columns that the current form does not write.

The four unvalidated integer limits—maxVideoMin, inworldSlotLimit, longDubMaxMin and longDubLipsyncMaxMin—now accept whole numeric strings/floats as integers and reject fractions, nonfinite values and invalid types before any write. This also prevents existing int() truncation in request construction from changing an invalid fractional limit silently. The existing integer lip-sync price fix is retained. Wrong text/switch types are refused instead of being silently stored/coerced; nested JSON remains finite and serializable. Blank/whitespace/null optional clones/storage values mean no cap; numeric zero remains zero in server validation. Signup 1–1,000, positive merge/transcription/cloning, whole-cent packs/plans and all existing pricing limits remain unchanged.

A newly found save-reporting defect was also fixed: each follow-up used to swallow its failure and the function still returned success. Now it returns ok:false with a precise partial-save explanation naming the failed groups and actual server reasons. The writes are still independent: earlier successful groups may already be saved. The message says this honestly, asks the admin to review/save again, and does not claim a rollback. Valid values/defaults and the existing database request structure are preserved. A wholly unreadable admin JSON request now returns HTTP 400 before _save_pricing_config can substitute defaults.

No SQL is required. Types were verified against the supplied schema offline, not against hosted constraints, permissions or database availability. The tests do not claim that eight separate writes are one atomic transaction.

## Part C — Readable, truthful Billing health

The Logs card now has separate “Detailed checks: active” / “Detailed checks: not installed yet” and paused-action lines. Paused actions are named in plain words: credit deductions, credit packs, refunds, cloning cancellations, credit adjustments and subscription credits. Unknown checks say “Not verified” instead of claiming an action is paused. On an outage the card says detailed checks could not be verified and clears stale details.

Signup is an important exception: the optional configured-signup flag being false does **not** pause signup; the original fixed allowance still works. The card preserves its separate fixed-allowance explanation rather than falsely listing signup as paused. No signup behavior or grant rule was changed.

billing_health.py exposes only seven whitelisted boolean/null action readiness values already present in the existing SQL reply. Existing RPC names, fallback behavior, row privacy/counts, aggregate ready calculation and read-only behavior remain unchanged. Tests verify no additional RPC and no private fields escape.

## Files and changed functions

- [admin.html](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/admin.html>)
- [account.html](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/account.html>)
- [app.js](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/app.js>)
- [main.py](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py>)
- [credit_billing.py](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/credit_billing.py>)
- [billing_health.py](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/billing_health.py>)
- [tests/test_admin_save_ui.js](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/tests/test_admin_save_ui.js>)
- [tests/test_job8_ui.js](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/tests/test_job8_ui.js>)
- [tests/test_pricing_schema.py](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/tests/test_pricing_schema.py>)
- [tests/fixtures/pricing_config_columns.json](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/tests/fixtures/pricing_config_columns.json>)
- [tests/test_notify_history.js](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/tests/test_notify_history.js>)

- admin.html: api, toast, doLogin, loadBillingHealth, saveMaintenance, clearMaintenance, loadPricing, savePricing, loadRailwayMemory, and two added status paragraphs.
- account.html: safeMsg, new accountResponse, subscription refresh/manage/cancel/resume, confirmDeleteAccount, deleteMyJob, saveVoice and deleteVoice.
- app.js: new settingsErrorMessage/settingsResponse, buyPack/changeSubscriptionPlan/subscribeMonthly, all three sync callbacks, fulfill, two return-hash notices, three promise-based local-mix callbacks, all four upload variants, and both manual cleanup variants.
- Job 8 changes in main.py: _save_pricing_config and admin_save_pricing only. The shared full file also retains the concurrent owner’s user_info login ID change. No long-dub _ld_*/longdub_* function or voice_match_routes.register block changed.
- credit_billing.py: validate_pricing only. billing_health.py: load_health's output whitelist only.
- Existing admin pricing UI test was strengthened from “not false” to explicit true. New tests execute actual page functions and request builders; no audit proof assertion was weakened, skipped or marked expectedFailure.

## Validation

| Check | Run | Passed | Skipped | Failed |
| --- | ---: | ---: | ---: | ---: |
| Full Python discovery | 538 | 538 | 0 | 0 |
| All tests/*.js | 122 | 122 | 0 | 0 |
| New pricing/schema/readiness Python tests (included above) | 11 | 11 | 0 | 0 |
| New Job 8 browser tests (included above) | 26 | 26 | 0 | 0 |

Python command: `python -m unittest discover -s tests` (verbose added); full runtime 377.664s. Node command: `node --test <every tests/*.js file>`. Python used the available runtime/site-packages, local FFmpeg, an isolated DATA_DIR and a guard against external urllib requests. Actual form building uses local Node; no live main import, cleanup worker or paid service was started. The real repository hygiene test passed; no environmental exception is needed here. Validation logs are inside the delivery ZIP.

Source syntax, Git whitespace, all protected file hashes and protected main blocks were checked. Existing CRLF/LF/CR endings of unchanged lines were compared with the starting snapshot, and no whole-file reformat occurred. SQL files are untouched. config.py was not edited by Job 8; the owner’s sole 1.82.23 → 1.82.24 version edit was preserved and excluded. Full current files are included in the archive; no code copying is needed because they are already saved locally.

## Questions / limits for Ali and Claude

No new charging/refund/credit amount policy was needed for this job. One existing input contract remains for a future decision: should a **valid but empty/partial** admin JSON settings object be rejected, or should the current default-fill behavior remain? This batch rejects unreadable JSON and invalid supplied values, while preserving the existing valid-object/default behavior; it does not invent a required-field/partial-update rule.

Independent follow-up writes can partially save if a database error occurs. This batch reports that failure accurately. An all-or-nothing save would be a separate database/request design change; no SQL was created for it. The supplied column types do not prove the deployed schema's constraints or availability, so a real save can still be refused and will now report the reason.

## Literal commands for Ali — Command Prompt, from backend

Review the handoff/diff first. No SQL installation is needed for Job 8. Claude handles the version separately. These commands exclude config.py, the protected pipeline, unrelated login/long-dub edits and the brief. The scoped commit includes only the named files even if other work is staged. Because app.js/main.py also contain concurrent notification changes, their unchanged matching tests/test_notify_history.js is explicitly included; keep that test with those shared files. The Job 8-only unified diff is provided separately for Claude to merge if he wants separate commits. As requested for Job 8, **no push command is included**.

```bat
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"
git --no-pager add -- admin.html account.html app.js main.py credit_billing.py billing_health.py tests/test_admin_save_ui.js tests/test_job8_ui.js tests/test_pricing_schema.py tests/fixtures/pricing_config_columns.json tests/test_notify_history.js Lisan-AI-Job-8-Handoff.md
git --no-pager commit --only -m "Make admin saves truthful and validate pricing database types" -- admin.html account.html app.js main.py credit_billing.py billing_health.py tests/test_admin_save_ui.js tests/test_job8_ui.js tests/test_pricing_schema.py tests/fixtures/pricing_config_columns.json tests/test_notify_history.js Lisan-AI-Job-8-Handoff.md
```

## Raw line-ending inventory

| File | CRLF | LF alone | CR alone |
| --- | ---: | ---: | ---: |
| admin.html | 2653 | 13 | 206 |
| account.html | 815 | 0 | 0 |
| app.js | 8347 | 25 | 0 |
| main.py | 8831 | 0 | 0 |
| credit_billing.py | 0 | 331 | 0 |
| billing_health.py | 0 | 63 | 0 |
| tests/test_admin_save_ui.js | 0 | 9 | 0 |
| tests/test_job8_ui.js | 0 | 222 | 0 |
| tests/test_pricing_schema.py | 0 | 187 | 0 |
| tests/fixtures/pricing_config_columns.json | 36 | 0 | 0 |
| tests/test_notify_history.js | 0 | 112 | 0 |
