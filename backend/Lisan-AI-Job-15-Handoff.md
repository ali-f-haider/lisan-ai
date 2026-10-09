# Lisan AI — Job 15 handoff

Ali: this delivery prepares an API so another developer can upload an English
clip or long video, review its lines, approve each price, and download the Arabic
dub or separate tracks. The design uses your existing website prices and credit
balance; uploads, reading, local editing and downloads remain free. **It does not
make the API live.** Claude must connect the routes, account key screen, durable
records and existing billing before anyone can use it. Version 1 uses polling,
as you approved. No existing source file, version, price, SQL, commit or push was
changed by this job.

## Scope and evidence

Starting APP_VERSION was 1.82.34; another developer changed it to 1.82.35 during
this job (**read from files**). Existing routes, models,
pages, service functions and credit-operation SQL were read without modification.
No credentials were read and no paid/live service was called. No Job 13 or
speaker-vote module was imported by the new modules/tests. Full suite discovery
naturally runs their pre-existing tests; they were not changed.

The initial source hash snapshot covered 246 existing source files (**measured
here**). A later comparison found concurrent changes in config.py,
longdub_service.py and longdub_edits.py. This job did not write them and excludes
them from the patch, archive and commands. The other 243 snapshot files were
unchanged at that check. The full suite ran against a shared working directory;
the protected code was not frozen while another developer worked.

This is a design plus six standard-library-only modules, not a live billing or
security fix. `api_webhooks.py` was intentionally omitted: Ali selected polling
only. The future capability is a setting but remains unavailable until a future
contract/implementation exists. Customer docs contain no AI company/model names.

Evidence labels: **proven** means a listed offline test proves a local property;
**documented** means the design or cited primary source specifies a control;
**likely** means an integration risk inferred from the current code, not an
observed exploit; **estimated** means a proposed limit or effort, not measured.
Protocol lengths/TTL are **read from the brief**; existing engine ceilings are
**read from files**. Test counts/times are **measured here**. No throughput or
production RAM claim was measured in Job 15.

## New files and responsibilities

| File | What it provides |
|---|---|
| api_keys.py | Key creation, safe dashboard prefix, HMAC storage/constant-time verification, state/scope checks and header-only parsing |
| api_limits.py | Pure per-key token bucket, explicit concurrency acquire/release, gross daily spend reservations and receipt-based settlement |
| api_idempotency.py | Canonical request fingerprint and new/replay/conflict decision; unresolved work never expires into a second execution |
| api_errors.py | Closed neutral errors, HTTP/retry mapping, exact known-message mapping and safe body builder |
| api_usage.py | Account-authorized ledger projection into per-key paid-operation calls/spent/refunded/net/by-day totals; no content |
| api_schema.py | Every JSON body in the contract plus exact binary chunk shape; first field problem, no exception on garbage |
| tests/test_api_*.py (seven files) | Pure logic, contract/reference consistency, body coverage, error mapping, docs privacy and client syntax checks |
| docs/api/openapi.yaml | OpenAPI 3.1.0 contract, JSON syntax valid as YAML 1.2; 21 operations (**measured here**) |
| docs/api/README.md | Money/status/error/retry/consent/limits rules, exactly 15-line resumable examples in three languages |
| docs/api/quickstart.md | Complete text-only curl/Python/JavaScript clients for both workflows, persistent paid requests and upload resume |

## Endpoint wiring — existing targets, not implemented adapters

All paths below start with `/v1`. These are proposed routes; do not register them
by simply aliasing browser handlers. Return the contract DTOs, not raw handler
dicts. Use the functions named here under their existing account/plan/storage
checks. Function names and behavior are **read from files**; the new adapter
work is **documented design**. No protected block was edited.

| Endpoint | Existing function(s) to reuse | Adapter work required / money |
|---|---|---|
| GET /settings | main._get_pricing_config, longdub_config, _ld_pricing, _storage_summary, active account plan | Whitelist neutral pricing/limit fields; do not expose raw config/secrets. Clamp to engine, plan and key settings. Free. |
| GET /account/balance | main.get_credits plus profile bucket read used by billing | Return permanent/subscription/total; missing balance is 503, not zero. Do not copy account_summary's `or 0` fallback. Free. |
| GET /usage | existing lisan_credit_operations receipts joined through API operation associations | Filter uid/key/date in the database; project to api_usage. Legacy credit_spends lacks key/operation ids. Free. |
| GET /voices | main._library_voices and my_voices | Neutral DTO; enforce account ownership for saved voices. Do not accept external engine credentials. Free. |
| POST /jobs | main.longdub_init -> longdub_service.init_upload (long); new staging wrapper around short upload setup | New durable owner registry, free shared bounded upload transport for short. Existing transcribe allocates a different job id and starts paid work, so cannot be used as free create. |
| GET /jobs | main.longdub_list / longdub_service.list_jobs_for_uid plus API short registry | Account-filtered pagination; do not fetch all users then filter. Website list can union API registry, without fake credit-spend rows. Free. |
| GET /jobs/{id} | main.longdub_status -> longdub_service.public_view; main.progress/generate_progress/merge_progress and job_status (short) | Read the recorded current operation, normalize exact states/percent, never expose raw diagnostics or unknown-owner short jobs. Free. |
| DELETE /jobs/{id} | main.longdub_delete -> longdub_service.delete_job; main.abandon_job for idle short cleanup | Durable operation check; reject running/uncertain charges. Keep receipt tombstones. No implied refund. Free. |
| GET /jobs/{id}/upload | longdub_status receipt indices / new short staging metadata | Return exact chunk size, declared size and durably acknowledged indices. Free. |
| PUT /jobs/{id}/upload/chunks/{index} | longdub_service.write_chunk; same transport for short staging | Bounded streaming before write_chunk; persist per-index digest under lock. Existing main.longdub_chunk reads request.body and is not enough on its own. Free. |
| POST /jobs/{id}/upload/finish | main.longdub_finish -> longdub_service.finish_upload; short transcribe setup -> transcribe_worker plus _watch_and_deduct | Estimate quote only. Persist accepted maximum/operation before worker. Long finish charges fee; short transcribe charges after its worker. Both must share one existing debit with a stable API operation id, never add a wrapper debit. |
| GET /jobs/{id}/estimate | longdub job.estimate / compute_estimate result; short normalized transcription estimate | Read only; short exact total is not ready until translated text and voices exist. No extra charge. |
| POST /jobs/{id}/quotes | long compute_estimate/dub_price/music_quote; main.generate_quote/_short_quote, merge_video_quote/_short_merge_price, clone fee selection in clone | Build durable snapshot of revision/terms/pricing/options/voice selection. Same calculators, no new rate list. Do not call longdub_preview's ensure_tashkeel side effect from a quote; prepare within approved analysis. |
| POST /jobs/{id}/accept | longdub_accept -> accept (analysis), longdub_confirm -> confirm (dub); main.translate (short analysis), clone, generate, merge_video | Only quoted step; long clone+merge already bundled. Pass current exact due through existing accepted_credits/expected_due/music_budget. Whole maximum includes music. Persist one business operation and enforce customer cap before any debit/work. |
| GET /jobs/{id}/segments | main.longdub_get_segments -> read_segments; stored short transcription/translation rows | Whitelist public fields, default waqf auto, include shared revision. Free. |
| PUT /jobs/{id}/segments | main.longdub_put_segments -> update_segments; new durable short row save | Existing long update uses edits, not full replacement: diff validated rows by id; handle inserts/deletes through existing line functions, preserving engine fields. Serialize as one revision-checked edit; do not partially apply on failure. Free. |
| GET /jobs/{id}/speakers | job.speaker_list from longdub_status/get_segments; short saved assignments | Normalize id/name/voice_mode/voice_id; do not expose private clone credentials. Free. |
| PUT /jobs/{id}/speakers | main.longdub_put_speakers -> set_speakers; short persisted assignment save | Existing long schema must be adapted to id/name/voice choice; original is supported by long. Library/saved modes for long need an engine adapter before enabled. Do not claim those choices work today. Free save; no clone count or charge. |
| GET /jobs/{id}/results | longdub_service.result_file/track_file; existing short output registry | Whitelist mixed/dialogue/background, sizes/media types/expiry. Missing tracks simply absent. Free. |
| GET /jobs/{id}/results/{kind} | main.longdub_download/longdub_track; main.download after registry mapping (short) | Owner before path resolution; map dialogue→voices, background→effects. Stream authenticated bytes, no arbitrary filenames, no key URLs. Short background track export needs a local delivery adapter. Free. |
| GET /jobs/{id}/subtitles | main.longdub_subtitles / existing subs_export routines | Export short saved rows through same local formatting routines; no AI work. Allow ar/en and srt/vtt only; free. |

Some names above are route targets, not reusable service APIs with identical
arguments. Claude must add explicit adapters for public DTOs. In particular,
do not overwrite internal long speaker/segment metadata with the smaller public
objects. The spec currently permits long library/saved choices for the target
design, but rollout must reject unsupported modes with invalid_request until
the engine adapter passes a same-quality test. V1 can launch original-only long
voices with that limitation documented rather than inventing new behavior.

## One authentication dependency

Add a dedicated APIRouter for `/v1` with one `require_api_principal` dependency
(new file for Claude). It rejects query/body/cookie credentials, duplicate
Authorization headers, unsupported media/body sizes and malformed paths.
Parse only Bearer; HMAC the key with each allowed pepper version, find a hash
record by `(pepper_version, digest)`, constant-time verify, check active/expiry,
owner profile, owner terms and required scope. Prefix is display only and is
not unique; never authorize from prefix. Use a separate server pepper, not an
existing session secret. Store only its version in the database; keep pepper
in secure server configuration, support overlap during rotation.

Put a server-created principal on request.state (uid, key_id, allowed scopes,
configuration version), never trust a uid header/body. Make **one** change to
main._current_uid so it recognizes this trusted principal before browser
sessions. Existing _paid_uid/_ld_job then use the same uid and billing readiness
check. Do not fabricate a cookie or put the principal in _session_users.
The browser-auth middleware needs a narrow `/v1` branch that returns API errors
and delegates all these routes to the router dependency; do not make `/api`
public or weaken its checks. Set no permissive browser CORS for secret keys.

For EVERY job/upload/quote/result, query API registry by BOTH job id and uid.
Unknown owner is 404; never fall through to short _job_guard's current unknown-
owner allowance. Ownership reads failing means 503, not "no owner". Scope checks
run before idempotency replay; a revoked key cannot replay an old download or
response. A read key intentionally sees account API jobs across the owner's keys.

## Approval and durable operation order

This order is required before enabling API billing (**documented design**):

1. Authenticate and validate syntax; load the owned job and durable revision.
2. Atomically claim `(uid,key_id,Idempotency-Key)` plus request fingerprint.
   A concurrent claim sees in-progress; it never launches another worker.
3. Acquire the shared job-operation lock and the per-key concurrency slot.
   Recheck state, quote expiry, owner terms, plan, voices, file and storage.
4. Read live website prices through the same calculators. Compare the saved
   snapshot and requested quoted_credits. Above max_credits => fail no-charge;
   any other quote change => fail no-charge. Pending analysis/quote preparation
   must never start unapproved paid work. Do not mutate global pricing config.
5. Reserve the whole maximum against the customer's daily cap, atomically with
   the business operation record. Persist quote consumption, request state and
   the operation's stable id before calling the existing workflow. A crash here
   leaves a recoverable reservation, never another allowance.
6. Use the EXISTING debit path once. Carry the operation id into its existing
   receipt helper: short clone currently creates a fresh random debit_id;
   short generation charges after work; transcribe watches in a background
   helper; short merge has individual merge/music charges. These require
   carefully scoped hook changes by Claude, not an extra debit at the API layer.
   Long uses existing _charge and _ld_charge receipt ids. Associate every child
   debit/refund with the API operation and reserve their total maximum.
7. Persist accepted/queued state before HTTP 202, then run through the same
   queues/model lifecycle. All optional repair spending stays within the
   accepted ceiling. Prevent changes to revision/text/voice selection while
   the operation uses its approved snapshot.
8. On completion reconcile durable gross debit/refund receipts. Settle unused
   reservations, release concurrency, record the final response and allow
   idempotent replay. Errors/timeouts alone never prove zero spending.

`api_limits` does not debit/refund anything. Give it one key's bounded state row,
not all customers' maps; its deepcopy is for purity, not a shared-memory store.
An atomic database lock/CAS must cover read→decision→write, even on one process.
Prune only old settled daily summaries after durable operation tombstones make
replay safe; never remove pending reservations because they are old. Zero-cost
steps do not need credit reservations or ledger writes; normal request/running
job limits still protect server work. Local validation/edit/format work is free.

For an operation with child debits on multiple UTC days, the small helper keeps
its whole maximum pending across every day, then conservatively attributes its
gross settled total to the last child debit's day. It may block earlier than an
exact per-child daily ledger would; it cannot create extra allowance. `/usage`
still reports the actual receipt days. Claude can use per-child reservations
plus the same parent ceiling if exact daily accounting is needed; do not clear
the pending parent without reconciliation. Use trusted server time, never a
caller-supplied timestamp, for all limiter/idempotency decisions.

The existing long analysis gate requires enough balance for the remaining whole
estimate, though it debits only analysis+flat+speaker check now. Keep that rule;
expose required_balance separately. Long confirm's due excludes a separate
music maximum: API quoted_credits must include BOTH. Short dub's studio_quote
includes unsettled analysis usage; do not bill it a second time. Short clone is
a total per request, with current rounded-up failed-speaker refunds; do not
convert it to a per-speaker API charge. Subscription/permanent refund splits
remain in the existing receipt system. No rate or clone count was changed here.

## Required persisted columns and rules — NO SQL

Names below are proposals for Claude to adapt to the existing schema.
All tables are server-only/RLS protected; owner queries are uid-filtered.
No plaintext key, transcript, source filename or HTTP body in these tables,
except existing project storage and sanitized response DTOs where explicitly noted.

| Table | Columns / types | Rules and purpose |
|---|---|---|
| api_settings | id text PK; enabled boolean; eligibility text; pricing_policy text; pricing_profile_id nullable text; jobs_visible boolean; webhooks_enabled boolean; limits jsonb; updated_at timestamptz | Settings-only product decisions. Start disabled; polling false for webhooks. Website profile is default, alternative rates require Ali's separate approval and central pricing profile, never a second API calculator. Effective limits are the minimum of engine/account/key policy. |
| api_owner_acceptances | uid uuid; terms_version text; rights_version text; accepted_at timestamptz; evidence_id nullable uuid | Owner-session/CSRF-protected acceptance before first key; unique(uid,versions); cannot be submitted by API key. If evidence such as IP is retained, owner-visible retention/privacy policy applies. |
| api_keys | id uuid PK; uid uuid FK; label text; prefix char(8); digest char(64); pepper_version text; state text(active/revoked); scopes text[]; expires_at nullable timestamptz; created_at/revoked_at/last_used_at timestamptz; rate_config jsonb; concurrency_cap integer; daily_cap integer | Unique(pepper_version,digest), prefix nonunique. Scope allowlist. Caps nonnegative, concurrency positive. New key shown once, Cache-Control:no-store; no endpoint to retrieve it. Retain revoked ids to attribute history. |
| api_jobs | job_id uuid PK; uid uuid FK; creator_key_id uuid FK; kind text; engine_job_id text unique; revision bigint; current_step text; normalized_status/stage text; accepted_terms/rights text; created_at/updated_at/expires_at timestamptz; deleted_at nullable timestamptz | Durable owner before upload/paid work; engine job ids assigned server-side; never accept owner or engine path from client. Stored source/text remains in existing per-job storage. Shared revision includes website edits as well as API edits. |
| api_uploads | job_id uuid PK/FK; size_bytes bigint; chunk_bytes integer; total_chunks integer; state text; received_count integer; last_activity_at timestamptz; expires_at timestamptz | Declared source quota/incomplete upload quota charged to storage reservations, not credits. Existing disk and account reservations remain authoritative. Finish cannot run until all exact chunks verified. |
| api_upload_chunks | job_id uuid FK; chunk_index integer; size_bytes integer; sha256 char(64); acknowledged_at timestamptz | Unique(job_id,index); exact size and immutable digest. No receipt before durable bytes, no partial overwrite on conflicting retry. Resume reads this, not only a process-local set. |
| api_quotes | id uuid PK; uid/key_id/job_id uuid; step text; revision bigint; price_version text; snapshot_hash char(64); terms_version text; options jsonb; fixed_credits/additional_max_credits/required_balance integers; expires_at timestamptz; consumed_operation_id nullable uuid unique | Sanitized options only, explicit speaker ids, actual quote calculators. Quoted maximum = fixed+additional; snapshot covers text hash/voices/settings. One consumed operation; quotes are account-owned and not authority without acceptance. |
| api_requests | uid/key_id uuid; key_hash char(64); fingerprint char(64); created_at/completed_at/response_expires_at timestamptz; state text; operation_id nullable uuid; response_status nullable integer; response jsonb | Unique(uid,key_id,key_hash). Store hash of non-secret idempotency key; reconstruct helper record using supplied key after lookup. Response is sanitized DTO, no bytes/paths/keys. Only complete responses expire at 24h; unresolved records/tombstones persist. |
| api_operations | id uuid PK; uid/key_id/job_id/quote_id uuid; step text; revision bigint; selection_hash char(64); state text; maximum_credits/gross_debited/refunded integers; created_at/updated_at timestamptz | Unique(job,step,revision,selection_hash) for each accepted business operation; unique quote_id. Parent of existing debit/refund ids, not a parallel money ledger. Keep failed/uncertain records until reconciled. Child music debits share the same maximum. |
| api_operation_receipts | operation_id uuid; credit_operation_id uuid unique FK | Associate every existing authoritative child debit/refund once. Join uses owner, kinds and done status; never guess attribution from filename/job only. Refunds inherit the original key even after rotation. |
| api_limit_state | key_id uuid PK; token_state jsonb; concurrency_state jsonb; budget_state jsonb; version bigint; updated_at timestamptz | Lock/CAS for pure helper inputs/outputs; plain JSON finite numbers only. No expiry of uncertain active reservations. Daily sums in UTC, gross not net. Bound/prune settled state from retained receipts, not HTTP timeouts. |
| api_request_counters (optional) | key_id uuid; day date; route_id text; status_class integer; calls bigint | All-HTTP counts, including free calls, kept separately. api_usage.calls is explicitly paid_operations because a credit ledger cannot count free GETs. No query strings, filename, text or credentials. |

Claude writes SQL and migration/rollback/verification steps; Ali runs them.
No SQL file or schema mutation is delivered here.

## Logs and restart/resume

Use the existing signature `_ev(job, step, status="ok", detail="", credits=None)`.
After durable job creation write `api_job_created`; after each acknowledged
chunk write `api_upload_chunk` with index/count/byte count only (sample/aggregate
if log volume is high); after receipt-verified finish `api_upload_finished`;
after saved quote `api_quote_created` with step/max/revision; after accepted
claim `api_operation_accepted`; after final reconciliation
`api_operation_settled` with gross/refunded counts. Download uses
`api_result_downloaded` with kind/bytes only. Rejects before an owned job exists
go to content-free API counters, not a customer job log.

Detail strings must be built from whitelisted numeric counts, step enums and
opaque record ids, not request strings. Never log Authorization, idempotency
key, raw path/query, source filename, transcript, accepted body, exception text
or audio. Use existing _ev for long; short needs an equivalent durable API log
wrapper because there is no long job object. Do not fabricate a long engine job
to make logs fit. Sanitize proxy/APM/error monitoring as well as app logs.

Startup reconciliation runs before reopening paid acceptance. Long
longdub_service.resume_all/start_worker can resume its persisted checkpoints;
reconcile API claims/reservations against those jobs and existing receipts first.
Short progress dictionaries/usage buckets are currently process-local. Persist
the approved snapshot, current step and receipt links in the adapter; if a
restart leaves an uncertain short worker, do NOT automatically re-run it. Mark
payment_pending, reconcile outputs/receipts and apply existing failure/refund
rules. Provider work without a durable checkpoint cannot be recovered by a
pure helper: this is a release blocker, not a guarantee supplied by Job 15.
Upload resume retains exact chunks; expiry cleans incomplete data and releases
disk reservations only after worker checks. Output retention follows plan;
known expired outputs give 410, missing/unowned ids give 404.

## Website-rule bypass checklist for integration

| Rule | How it is kept |
|---|---|
| Account/plan/long-dub eligibility | Same _paid_uid, _ld_allowed / _ld_studio_only and current subscription checks; API setting can further restrict, never elevate. Recheck before every paid operation, not just key creation. |
| Engine length, upload and source type | Same validated media formats, actual finite probed duration and per-kind limits. Current short 50 MiB / 4–30 s; current long 2 GiB / 20–3600 s (**read from files**); no short trim/lip-sync path in v1. Reject unreadable probe instead of short transcribe's current fail-open behavior. |
| Consent/terms | Owner acceptance once per terms version before key creation, per-job rights affirmation, current offer terms with accept; reuse _record_voice_consent and long terms record, preserving audit without pretending an API request is a checkbox click. |
| Clone ownership/slots/count | Same _authorize_new_clones, ownership checks, successful-delivery count and existing clone refunds. No arbitrary voice ids or deleted transient clone reuse. Long speaker hint remains a hint. |
| Credits/prices/reserve/music budget | Same quote and receipt paths, explicit max on every paid step, unsettled analysis included once, no price change. Existing billing unavailable => API 503 before paid work. |
| Output watermark | Same delivery and plan eligibility; API does not accept a watermark-disable field. Verify both video and audio rules from the selected workflow. |
| Storage/expiry/server disk | Same _storage_block/account_lock/_ld_capacity/disk_guard and retention; source, scratch and output estimates reserved before paid acceptance. Incomplete uploads also bounded per account; API is not free unlimited storage. |
| Queue/RAM/concurrency | Reuse existing bounded chunk flow, job locks and model queue. No second model worker pool or per-key parallel engine instances. Per-key cap supplements, never replaces account/global caps. |
| Editor revision/working files | Both website and API changes bump one durable revision and invalidate quotes. Retain per-job isolation; never merge rows from client-supplied job/path ids. |
| Results/subtitles/account history | Registry owner check precedes all file reads; no arbitrary filename route exposure. Free jobs shown through registry without invented billing rows. |

## Security review (primary sources; not a penetration test)

| Threat | Evidence and control | Remaining limit |
|---|---|---|
| Leaked key | **Proven locally:** secrets-based 32-character suffix (192 random bits, **read from implementation**), HMAC hash, constant-time verification, explicit expiry/revocation/scopes. **Documented:** HTTPS and short-lived/scoped token precautions from [RFC 6750 §§5.2–5.3](https://www.rfc-editor.org/rfc/rfc6750#section-5.3). | A bearer key still authorizes its holder. Dashboard revocation, server pepper protection and cache invalidation are integration work. No claim of theft detection. |
| Key in browser code/URL | **Proven locally:** parser accepts a single Bearer header. **Documented:** server-only clients; no query credentials or browser CORS. [RFC 6750](https://www.rfc-editor.org/rfc/rfc6750#section-5.3) explains URL history/log disclosure. | Cannot prevent an owner from embedding a key in their own browser bundle; warn in account UI, rotate immediately. Local OS/process/environment access is outside these helpers. |
| Replayed paid request | **Proven locally:** canonical conflict/replay logic and in-flight retention. **Documented:** atomic claims + enduring receipt id required. HTTP method semantics alone do not make a POST safe to retry: [RFC 9110 §9.2.2](https://httpwg.org/specs/rfc9110.html#idempotent.methods). | No database race/restart test yet; a pure decision is not a lock or exactly-once provider delivery. Launch blocked until integration tests prove this. |
| Job enumeration / IDOR | **Likely current risk:** short _job_guard allows missing owner; random ids are not authorization. **Documented:** uid+job registry lookup for every resource, including result and voice ids, identical 404. [OWASP API1](https://api-security.owasp.org/editions/2023/en/0xa1-broken-object-level-authorization/) calls for per-object authorization. | New adapters/RLS not implemented; must test two accounts and two scopes on every id-bearing endpoint. |
| Oversized/slow upload | **Proven locally:** exact chunk size/index and bounded syntax validator. **Documented:** stream byte/time ceilings, content probe, upload/storage reservations, incomplete-job caps. [OWASP API4](https://api-security.owasp.org/editions/2023/en/0xa4-unrestricted-resource-consumption/) recommends explicit resource limits. | Helper sees already-buffered bytes. Proxy/server idle/total timeouts, compressed-body rejection and bounded ingress are not implemented or load-tested. |
| Cost abuse / stolen key | **Proven locally:** token/concurrency/daily reservation helpers, midnight/pending/retry/gross-cap tests. **Documented:** user max per paid step and atomic whole-maximum reservation before work. [OWASP API4](https://api-security.owasp.org/editions/2023/en/0xa4-unrestricted-resource-consumption/) includes financial resource limits. | Cannot stop an authorized spender within their cap. Customers with multiple keys still need account/global limits. Live all-child-debit ceiling tests remain a launch gate. |
| Webhook spoof / SSRF | **Documented:** polling only, no callback fields, no arbitrary remote media URLs. | No webhook sender or verifier exists. A future webhook release needs signed timestamped payloads, replay tolerance, outbound destination controls and retry policy before the setting can be enabled. |
| Log leakage | **Proven locally:** fixed error messages and usage output contain no supplied filenames/text. **Documented:** secret/content exclusion in all app, proxy and monitoring logs, following [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html#data-to-exclude). | Reverse-proxy/APM log configuration not inspected. No live log-leak test. Do not report that infrastructure is safe because these pure functions are safe. |

No DDoS mitigation, distributed performance measurement, media-decoder sandbox,
malware scanner, third-party client audit, formal legal terms, production RLS
verification or end-to-end paid test is supplied. API feature stays disabled
until account/owner/retry/money/upload tests pass. Edge/proxy errors may not reach
the app's JSON error handler; document that operational limit for clients.

Contract conventions are **cited** from the primary
[OpenAPI 3.1 specification](https://spec.openapis.org/oas/v3.1.1.html#security-scheme-object).
The offline tests resolve local references and match request schemas; they are
not a claim that a third-party conformance suite or live server was tested.

## Questions for Ali — each remains a setting

1. **Eligibility:** recommend existing website plan gates with a credit-bearing
   account (`eligibility=website_plans`). No additional plan billing work;
   resource cost remains your normal per-job work. Credits-only broadens access
   and may require revised plan rules; a separate API plan adds subscription/UI
   administration. These are **estimated qualitative costs**, not measured fees.
2. **Pricing:** recommend exactly website credits (`pricing_policy=website`),
   matching the brief. No pricing changes were made. If you later choose different
   rates, use an approved profile in the SAME central pricing configuration and
   calculators (`pricing_profile_id`), with quotes/receipts still authoritative.
   That changes revenue/margins and needs your explicit rate decision; no numbers
   or alternate calculator are invented here.
3. **Limits:** recommend a sustained 60 requests/minute with burst 10 and one active
   operation per key (**estimated starting settings**), plus the website's current
   per-kind file/duration ceilings above (**read from files**). Require the owner
   to choose a daily whole-credit cap before key creation; no silent unlimited
   spend default. Higher limits increase queue/disk/CPU risk; measured load and
   one-hour end-to-end memory checks are needed before raising them. Smaller
   settings save capacity but slow integrations. Limits clamp to website/engine.
4. **Website visibility:** recommend `jobs_visible_on_website=true`. It gives users
   the existing editor/results list, with ordinary storage/retention cost and
   one registry-list integration. Hiding them reduces UI integration but makes
   review/support harder; it does not reduce storage by itself. Costs **estimated**.
5. **Notifications:** **answered by Ali: polling only for v1**.
   `webhooks_enabled=false`. Polling adds small status-read traffic; future
   signed callbacks add sender/retry/security work. No webhook fee proposed.

These pending product answers do not change the delivered pure modules. Claude
must persist and expose effective settings before enabling the feature. Alternative
pricing/eligibility must not secretly override a website rule during integration.

## Validation and launch gates

| Check | Result / provenance |
|---|---|
| Baseline, before adding API files | 805 tests in 425.739 s: 804 passed, 1 environmental error (**measured here**). This differs from the brief's 736 / 11. |
| New API tests, final focused run | 54 tests in 0.183 s: all passed, no skips (**measured here**). Seven new test modules. |
| Full `python -m unittest discover -s tests` | 881 tests in 406.063 s: 880 passed, 1 error, no failures (**measured here**). Same pre-existing Git-access error as baseline. |
| Concurrent test changes | Final has 76 more tests than baseline: 54 from this job and 22 from another developer (**measured here**); see modules below. |
| Client syntax | Python compile and JavaScript `node --check` passed for both complete and compact examples, without execution (**measured here**). |
| Shell syntax | Blocked: bundled shell cannot create its sandbox namespace (0xC0000022). Shell clients were reviewed but not executed or syntax-validated; no live examples ran. |
| Contract | Local references, unique operation ids, scope/idempotency/body/error coverage and 15-line examples all pass (**measured here**). No independent OpenAPI/JSON Schema conformance package is installed; no external conformance claim. |

The existing error is
`test_repository_hygiene.BackupRulesTests.test_no_currently_tracked_backend_file_matches_an_ignore_rule`:
Git exits 128 because it cannot change to the repository root under this sandbox.
It occurs identically before and after the new files. The offline harness uses
the project's installed dependencies, local audio tools and scratch DATA_DIR,
blanks external credentials, avoids loading .env files and blocks external URL
calls. It still runs actual unittest discovery from backend. This does not
replace a final run by Ali in his normal terminal before merging.

Concurrent test additions (not in this delivery):
- `test_dub_timing`: 13 tests (**measured here**).
- `test_no_cut_wiring`: 9 tests (**measured here**).


New tests cover every public helper (private helpers are exercised through them),
request schema coverage including raw chunks, malformed inputs, clock regression,
UTC midnight, conflicting receipts, unresolved retries, explicit scopes and
neutral output. Python/JavaScript examples are compiled/parsed without executing
their client code. No network or credit-consuming examples run in tests.

Before launch Claude still needs router/database tests: simultaneous identical
accepts charge once; same key/different body conflicts; lost response/restart
does not rerun work; quote/multi-child music maximum cannot be exceeded; refunds
retain their original key and split; fresh key cannot repeat a completed step;
owner/scopes/voice ids are enforced across accounts; uploads stop at actual byte
and time limits; disk/account caps include incomplete files; website edits bump
the API revision; outputs/watermark/expiry match website; original/library/saved
voice choices are accepted only where the engine actually supports them.

## Exact commands for Ali (Command Prompt, from backend)

Only this job's new files are staged/committed; config.py and other developers'
work are excluded. No push command is provided. Review and merge with Claude
before enabling the API; merely committing these files does not add live routes.

```cmd
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"
python -m unittest discover -s tests
git --no-pager add -- api_keys.py api_limits.py api_idempotency.py api_errors.py api_usage.py api_schema.py tests/test_api_keys.py tests/test_api_limits.py tests/test_api_idempotency.py tests/test_api_errors.py tests/test_api_usage.py tests/test_api_schema.py tests/test_api_contract.py docs/api/openapi.yaml docs/api/README.md docs/api/quickstart.md Lisan-AI-Job-15-Handoff.md
git --no-pager commit --only -m "Add public API contract and tested pure building blocks" -- api_keys.py api_limits.py api_idempotency.py api_errors.py api_usage.py api_schema.py tests/test_api_keys.py tests/test_api_limits.py tests/test_api_idempotency.py tests/test_api_errors.py tests/test_api_usage.py tests/test_api_schema.py tests/test_api_contract.py docs/api/openapi.yaml docs/api/README.md docs/api/quickstart.md Lisan-AI-Job-15-Handoff.md
```

Final comparison of the initial source snapshot found these concurrently changed
existing files (excluded from this delivery): `config.py`, `longdub_edits.py`, `longdub_service.py`.
