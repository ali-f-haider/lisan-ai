# Lisan AI upgrade 1.81.1 — Claude handoff

Prepared on 5 October 2026 in `C:\Users\Ali Haider\Desktop\ai-dubbing-app`.
Baseline commit: `35729418a28aac4dd3ef7f23e0552058e221cdb3`, branch `main`.

## Status and scope

The changes below are already applied to the local source. They have not been committed, pushed, or deployed. Validation passed: 34 Python tests (including four real FastAPI HTTP integration tests), 10 JavaScript tests, Python/JavaScript syntax checks, account-page inline script syntax, and Git whitespace checks.

The user's rule is to avoid experimental changes and manual replacement of files. This is a focused repair, with small interface improvements using the existing dialog and colors. No broad visual redesign, new dependencies, provider/model changes, SQL migrations, or lip-sync changes were made. The full live server was not started: its imports launch cleanup/monitoring threads and can use production credentials.

Checkout fulfillment remains unresolved pending verification of the live database schema and stored functions. Successful offline tests are not a promise of complete production certainty. Provider behavior, real database transactions, deployment, and production startup still need validation in a safe environment.

## Every file changed

| File under `backend/` | Change and reason |
| --- | --- |
| `shortdub_paths.py` (new) | Centralizes job-specific raw/stretched filenames. SHA-256 hashes the segment ID so Unicode and Windows names stay safe. Validates job IDs and file suffixes. Cleanup removes only that job's matching line files. Provides thread-safe, per-job operation exclusion. |
| `eleven_service.py` | Replaces shared `seg_0_raw`/`seg_0_stretched` filenames in generation, regeneration, restretching, rebuilding, and remixing. Removes the global cleanup of every user's raw/stretched files. Replaces shared overlap/dead-space flag globals with operation-local dictionaries, including explicit empty settings. Finished MP3 names and provider requests remain the same. |
| `tts_service.py` | Updates the separate legacy generation worker to the same scoped paths and scoped cleanup. This worker is not the active studio-voice route; only source syntax and shared path behavior were validated for this legacy file. |
| `shortdub_billing.py` (new) | Pure calculation of a studio-voice quote. Counts each engine against its own admin-configured rate and rounds separately. Includes the job's unsettled Gemini analysis/translation usage using the existing cost formula and multiplier. Saves a usage snapshot after successful charging so later full dubs do not charge the same analysis again. Removes a phantom one-credit analysis fee when there is no usage. Defines confirmed debit outcomes: subscription `True`, or a nonnegative integer balance (including zero). |
| `main.py` | Adds quote endpoints and `accepted_credits` request fields. Resolves engine ownership on the server and counts the exact emotion-tagged text the worker submits. Rejects missing voices, blank translated text, and duplicate segment IDs before starting paid generation. Requires an authenticated account, a matching confirmed price, and sufficient verified credits for studio generation/regeneration. Balance lookup failure returns 503 and starts no provider call. Regeneration charges a successful take once and returns `credits_charged`/`balance_after`; free restretch/remix remain free. Studio generation charges the confirmed quote after successful completion, instead of all accumulated character counters. Serializes audio edits for the same job while allowing other jobs to run. Holds generation progress at 99% while billing completes. Reports unconfirmed debits as errors and does not settle analysis or claim a successful charge. Updates latest job usage after regeneration. Audio previews resolve only the requested job's files. The shared spend-history wrapper records only confirmed debit outcomes. |
| `app.js` | Replaces the misleading numeric generation badge with “Check price” (English/Arabic). Adds a server price check and native confirmation dialog for generation and line regeneration, showing voice cost, previously uncharged analysis/translation, total cost, and any minimum starting balance. Canceling starts no paid request. Freezes the submitted payload while the dialog is open and sends the accepted price. Removes the obsolete hardcoded 20-credit frontend generation guard; the server uses the configured reserve and current price. Displays actual regeneration credits returned by the server and refreshes the balance; the result is available in English/Arabic. Invalid project-file errors identify the file problem instead of triggering the network-error message. Existing duplicate legacy function definitions were retained; the effective later generation/regeneration implementations were changed. |
| `account.html` | Refund/negative spend records display a green `+3`, ordinary spends a red `−3`, zero a neutral `0`, and invalid values a neutral dash. Adds English/Arabic labels for line regeneration. |
| `config.py` | Bumps the displayed application version from `1.81.0` to `1.81.1`. A final newline was also added by the file editor; this has no runtime effect. |
| `tests/test_shortdub_upgrade.py` (new) | Offline tests for file isolation/cleanup, concurrent workers and independent settings, engine pricing, analysis settlement, authenticated/confirmed regeneration, failed balance/debit/provider requests, operation exclusion, HTTP model parsing/routing, and serving the correct preview bytes. Extracts actual functions/classes from source and replaces external services; never imports the production app/config. |
| `tests/test_shortdub_ui.js` (new) | Node's built-in tests cover refund formatting, quote/cancellation/error behavior, exact confirmed amounts, frozen payloads, Arabic confirmation/results, and malformed project JSON preserving the current project. |
| `UPGRADE_1.81.1.md` (new) | This handoff stored beside the source so Claude and future maintainers have the complete change record. |

## API contract

For the current studio-voice interface:

1. POST the intended generation payload to `/api/generate/quote`, or the regeneration payload to `/api/regenerate_line/quote`.
2. The response contains `credits`, `voice_credits`, `analysis_credits`, and `required_balance`. Quoting does not call a voice provider or deduct credits.
3. Ask the customer to confirm, then send the same frozen payload to `/api/generate` or `/api/regenerate_line`, with `accepted_credits` equal to the quoted `credits`.
4. The server recalculates the price. A different/missing accepted price returns 409 before a provider call; insufficient credits return 402; an unavailable balance returns 503.
5. Only successful output is charged. A debit that cannot be confirmed produces an error directing the customer to support before retrying. Do not automatically retry an ambiguous payment.

Full studio generation includes previously unsettled analysis/translation; individual regeneration buys only the line's new voice. Repeating a full dub charges its new voice text and any newly accumulated analysis, rather than historical voice counters or already settled analysis.

Legacy API-only `tts_provider="gemini"` retains variable billing through the existing watcher. The fixed quote endpoint refuses that mode rather than inventing a price. The current page's provider selector offers studio voices only. This is not a complete billing redesign for every legacy/API mode.

The existing minimum reserve still applies to full generation: it is a required starting balance, distinct from the confirmed charge. Regeneration requires its actual line price.

## Validation evidence and limits

Verified using Python 3.12.14, FastAPI 0.115.6, HTTPX 0.28.1, and Pydantic 2.10.4; the latter three match the project's requirements. Existing `.venv` libraries were loaded using the bundled Python because the old `.venv` executable points to a removed interpreter. No packages were installed.

The 34 Python checks exercise actual extracted route/worker code. Four register the real Pydantic models/routes on a small FastAPI app and use its HTTP test client. Provider audio, FFmpeg, database calls, authentication results, and background dispatch are mocked; the concurrency tests really run two workers in separate threads and inspect their files and mix inputs. Ten frontend checks execute the actual changed functions in Node with controlled page/network mocks.

These checks validate behavior and integration boundaries, not real audio quality, production authentication middleware, live Supabase functions, real AI generation, full application startup, or hosting configuration. No new live paid tests were performed for this upgrade. See `Lisan-AI-upgrade-tests.txt` for the captured results.

## Known remaining database work

`_fulfill_order` still inserts `credit_orders` and then grants credits through a separate RPC. If granting fails after the insertion, a retry can see the order and treat it as already fulfilled. Fix this with one verified database transaction that both records the unique paid session and grants its credits. First inspect actual columns, uniqueness constraints, existing functions, triggers, and permissions; then test concurrent retries and failures before deploying an RPC/caller update. Do not remove an order record after an ambiguous grant: it can cause a duplicate grant on retry.

The existing debit implementation also calls subscription and permanent-credit RPCs separately and falls back to permanent credits on an unreadable subscription response. Its live transaction/timeout behavior has not been verified, and this upgrade does not replace it. In an ambiguous network failure, reconciliation may be required; a quoted application amount is not proof that the underlying database primitives cannot partially or repeatedly debit. Verify these functions before declaring production billing fully correct. The new history guard only prevents logging a debit result that is visibly unconfirmed.

These are explicit outstanding limitations, not fixes included in this package.

## Deployment and compatibility

Deploy all runtime files together. Older clients without `accepted_credits` cannot start studio generation/regeneration until they reload; app assets are already served with no-cache headers.

Finish active jobs before restarting/deploying. Old shared line files are deliberately never used as a fallback. Existing completed final audio/video downloads retain their names. Old active projects need a new Generate run before line preview/restretch/remix can use the new scoped takes. Analysis settlement and operation locks are process-local, matching the application's existing in-memory job state and current single-worker Docker command; multi-worker/replica persistence was not added.

Do not delete old shared line files during deployment as a workaround. Normal retention handles their expiry. Do not stage the user's existing deleted demo videos, backups, uploads, `.env` files, or unrelated work.

## Delivery and publishing

Local source is already edited; no replacement code needs to be pasted into files.

The accompanying `Lisan-AI-upgrade.patch` contains only this change set. `Lisan-AI-upgrade-manifest.json` records each changed file's SHA-256. `Push-LisanAI-upgrade.ps1` verifies the baseline branch/commit, file hashes, empty staging area, and regression checks. Its default mode is a local preview; `-Publish` additionally checks the remote baseline, commits only the listed files, and pushes `main` without force. It does not migrate the database or verify a hosting deployment. A push may trigger automatic deployment if Railway is configured that way; production compatibility must be reviewed first.

The publishing script was validated in preview mode; no remote fetch, commit, or push was executed by Codex. If a push fails after a successful commit, keep that commit and resolve credentials/connectivity before retrying the push; do not rerun a baseline-only packaging script or reset the user's work.
