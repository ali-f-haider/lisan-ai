# Lisan AI public API — proposed v1 contract

**Not deployed yet.** This delivery provides pure logic, a contract and examples.
The server adapters, durable storage and account key screen still need integration.
The examples work against that completed contract; they are not instructions to
call the current website routes. Keep `api_enabled` false until acceptance tests pass.

The API accepts English audio/video and produces Arabic dubbing using the same
workflow, prices, account balance and output rules as the website. Version 1 uses
polling only. Lip-sync, line regeneration, glossary operations,
subtitle import and correction exports are deferred; use the website for those.
Reading, local edits, upload, subtitle export and downloads cost no credits.
Calls that invoke analysis can create costs included in the later dub quote,
exactly as on the website; no quote grants authority for a later debit.

`openapi.yaml` is OpenAPI 3.1.0, written in JSON syntax, which is valid YAML 1.2.
Body schemas are checked against `api_schema.BODY_SCHEMAS` by offline tests.
Additional semantic checks are documented per operation. This is a contract,
not a router, SDK or replacement billing system.

Number provenance: required key lengths, replay duration and example lengths are
**read from the brief**; file, speaker, project-name and line-count ceilings are
**read from the current files**. Suggested timeouts, polling, quote lifetime,
15,000-character line-text and 366-day report bounds are **estimated design
defaults**. HTTP meanings and schema conventions are **cited** in the handoff.
Test counts are **measured here**, never production reliability measurements.

## Authentication and keys

Create a key in your signed-in Lisan AI account. The account owner accepts the
current API terms and rights statement before creating the first key. Each job
also carries `rights_confirmed: true` and the current `terms_version`.
Changed terms require owner acceptance again. A key cannot accept terms for its owner.

Use HTTPS and `Authorization: Bearer <key>`, from your server only. Keys are never
accepted in query strings, filenames, cookies or JSON. Do not put them in browser
JavaScript, mobile bundles or a repository. Never disable TLS verification.
The full key appears once on creation; thereafter the dashboard shows only its
eight-character prefix. Revocation/expiry is checked even on a replay.

Scopes are independent: `read` reads jobs, voices and settings; `dub` uploads,
edits and approves jobs; `account` reads balance and this key's usage. Use all
three for the quickstart. Keys are account-wide, not project-scoped: a read key
can read this account's API jobs, including jobs created by another key. The
website can show these same projects if that setting is enabled. No access to
another account or to unregistered legacy jobs is implied.

## Money and approval

1. `POST /v1/jobs` reserves an upload, free. Upload chunks, free.
2. `POST /v1/jobs/{id}/quotes` with `step: estimate`, free. The quote gives the
   estimate/transcription fee, copied from the website configuration.
3. `POST /v1/jobs/{id}/upload/finish` explicitly accepts that quote, charges at
   most the accepted maximum, validates media and starts the estimate step.
4. Poll until `estimated`; read `/estimate`. Request and accept an `analysis`
   quote through `/accept`. The long workflow charges its existing analysis
   amount now; short translation records costs that the existing dub price
   settles later. Poll until `editing`; review lines and speakers.
5. For short original voices, request/accept `clone` with explicit `speaker_ids`;
   cloning uses the website's total request fee/refund rules. Long clones are
   already included in `dub`; never charge them again.
6. Request and explicitly accept `dub`. Short dub returns to `editing` with
   dialogue available; request/accept `merge` to produce the full mix. Long dub
   includes the merge and finishes at `done`. Download available outputs.

Every paid request includes `quote_id`, `quoted_credits`, `max_credits` and
`terms_version`. `quoted_credits` equals the quote's whole maximum:
`fixed_credits + additional_max_credits`, including optional music repair.
`max_credits` is a customer-chosen ceiling for **that step**, not for the entire
job. Set separate budgets per step; the sum of approved step maxima is your
job ceiling. `required_balance` may be higher because of the website's reserve
or full-estimate affordability rules; it is not an additional debit.

Under one durable job-operation lock, reload the revision, settings, ownership,
terms, storage and live price. If live cost is above `max_credits`, return
`max_credits_exceeded`; otherwise if the snapshot or price changed, return
`quote_changed`. Both refuse the operation before any debit or new paid work.
Never silently approve a new quote, even if a larger budget would cover it.
Quotes expire after a configurable lifetime (recommended ten minutes,
**estimated product default**, not measured). Edited lines invalidate old quotes.
No HTTP GET performs paid work. Quote creation must not trigger fresh analysis.
Finish consumes only estimate quotes; `/accept` consumes the other quote steps.

Existing debit/refund receipts are authoritative. A daily cap uses gross debits;
refunds do not replenish a stolen key's daily allowance. Unused reserved maxima
are released only when receipts prove actual spending. Pending reservations
survive midnight and restarts. If a late optional debit cannot remain within
the accepted maximum and daily reservation, stop before that call; settle the
existing failure/refund policy without inventing a new charge. No credit prices
or refund splits are changed by these modules.

## Idempotency, retries and restart

Every mutation, including free ones, requires `Idempotency-Key`: 16–128 ASCII
letters, digits, `_` or `-`. Persist it with the exact body before sending.
The namespace is `(account id, API key id, idempotency key)` across all methods
and paths. A key reused for a different request returns `idempotency_conflict`.

The fingerprint is SHA-256 of compact UTF-8 JSON `[uppercase method, canonical
/v1/path, body]`, with sorted object keys and finite JSON numbers. Reject
duplicate JSON keys, invalid Unicode and ambiguous route encodings at ingress.
Array order matters; `1` and `1.0` retain distinct spelling. No mutation has
query parameters; chunk index is in the path. A binary chunk fingerprint uses
the JSON body `{sha256: <hex>, size_bytes: <length>}` in that same envelope.

Atomically claim the record before starting any worker or debit. A matching
completed record replays the original status/body; in-flight matching work
returns its operation's current job, HTTP 202, without starting again.
Completed response replay lasts 24 hours (**brief requirement**); unresolved
operations do not expire into new execution. Validation failures before a claim
are not cached; a capacity/balance failure after a claim must be marked safely
retryable with proved no-debit, or replayed until reconciled. Never leave a
failed claim in a state that can start new work while payment is uncertain.

Business receipts and a unique `(job, step, revision, selection)` operation
outlive response replay and remain valid across key rotation. A new quote id,
new key or expired response cache must never repeat an already completed stage.
An accepted quote has at most one operation. Keep minimal receipt tombstones
after response payload expiry; deletion must not allow rebilling that operation.

On 429/503/network loss, wait at least `Retry-After`, then retry the **same** key
and body or poll the job. A 500 is uncertain: poll/reconcile before retrying;
never generate a fresh request key to resolve uncertainty. Re-authenticate and
recheck owner/scopes on replay. Do not follow an authentication redirect.
`X-Request-ID` identifies the current HTTP request; a cached body may retain
its original error request id. `Idempotency-Replayed` is true for replays.

## Exact workflow states and progress

Public status values: `uploading`, `estimated`, `accepted`, `analyzing`,
`editing`, `confirmed`, `dubbing`, `payment_pending`, `done`, `failed`.
The long engine already uses these working states (payment_pending is also
guarded by its cleanup/deletion logic). Engine cleanup-only records are not API
jobs. An expired output keeps `done` with `result_available: false`; downloading
it returns 410. Idle job deletion returns its snapshot then subsequent reads
return 404. An unknown engine state must fail closed; do not guess `done`.

Stage values: `upload`, `estimate`, `queued`, `extract`, `plan`, `separate`,
`speakers`, `transcribe`, `build`, `translate`, `review`, `clone`, `speak`, `mix`,
`finish`, `done`, `failed`. The page already displays these long-dub stages.
No lip-sync stage is exposed in this version. For short dub, map the current
operation's processing/done/error to analyzing/editing/failed or dubbing/done,
using the durable step, not the first progress dictionary found. Transcription
completion maps to estimated, translation and clone/dub completion to editing,
merge completion to done. Queue/accept states must be persisted before HTTP 202.

`percent` is an integer from 0 to 100 within the current operation; it can reset
at the next operation. Poll no faster than once per five seconds (recommended
client interval, **estimated**), honoring longer `Retry-After`. Error details
are the uniform safe error object, not raw engine messages.

`current_operation` is null before acceptance; otherwise it identifies the
quote/step and durable accepted/running/reconciling/succeeded/failed state,
approved maximum and receipt-confirmed debited/refunded credits. Inspect it
before replacing an expired or rejected saved approval. A reconciling operation
is uncertain; zero counters alone never authorize starting it again.

## Resumable uploads

Read `chunk_bytes`, `total_chunks`, and `received` from `/jobs/{id}/upload`.
Indices start at zero. Each chunk has exactly `chunk_bytes` bytes except the
last. Current long website constants are 8 MiB chunks and a 2 GiB file ceiling
(**read from files**); use returned settings instead of embedding them in a
client. Short API uploads use this same bounded transport, then pass the source
to the existing short pipeline; this adapter is still missing. No trim mode in v1.

On reconnect, fetch the receipt indices and send only the missing ones. Persist
an index digest with its receipt. A repeat with the same bytes is safe; different
bytes for an index is a conflict even with a new request key. An upload is bound
to its declared size and account. A chunk is acknowledged only after the bytes
and receipt are durably written. Finish is retryable using the saved paid request.
The server must bound bytes while streaming, enforce idle/total upload timeouts,
limit parallel incomplete uploads, and probe media before charging its estimate
fee. A declared Content-Length alone is insufficient protection.

The three compact examples below are exactly 15 lines each. They assume a job
has already been created, credentials are in a server environment, and the
specified standard tools are installed. They resume the upload; the complete
estimate/approve/dub/download examples are in quickstart.md.

### curl (POSIX shell, jq, openssl, dd)
```bash
set -eu
: "${LISAN_API_KEY:?}" "${JOB:?}" "${FILE:?}"
BASE=https://lisanai.org/v1
META=$(curl --fail-with-body -sS -H "Authorization: Bearer $LISAN_API_KEY" "$BASE/jobs/$JOB/upload")
CHUNK=$(printf '%s' "$META" | jq -r .chunk_bytes)
COUNT=$(printf '%s' "$META" | jq -r .total_chunks)
TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
i=0
while [ "$i" -lt "$COUNT" ]; do
  if ! printf '%s' "$META" | jq -e --argjson i "$i" '.received | index($i)' >/dev/null; then
    dd if="$FILE" of="$TMP" bs="$CHUNK" skip="$i" count=1 2>/dev/null
    KEY=$(openssl rand -hex 16)
    curl --fail-with-body -sS -X PUT -H "Authorization: Bearer $LISAN_API_KEY" -H "Idempotency-Key: $KEY" -H 'Content-Type: application/octet-stream' --data-binary "@$TMP" "$BASE/jobs/$JOB/upload/chunks/$i"
  fi; i=$((i+1))
done
```

### Python (standard library)
```python
import json, os, urllib.request, uuid
base = "https://lisanai.org/v1"
job, filename = os.environ["JOB"], os.environ["FILE"]
auth = {"Authorization": "Bearer " + os.environ["LISAN_API_KEY"]}
request = urllib.request.Request(f"{base}/jobs/{job}/upload", headers=auth)
with urllib.request.urlopen(request, timeout=30) as response:
    upload = json.load(response)
with open(filename, "rb") as source:
    for index in range(upload["total_chunks"]):
        source.seek(index * upload["chunk_bytes"])
        if index in upload["received"]: continue
        data = source.read(upload["chunk_bytes"])
        headers = dict(auth, **{"Content-Type": "application/octet-stream", "Idempotency-Key": uuid.uuid4().hex})
        request = urllib.request.Request(f"{base}/jobs/{job}/upload/chunks/{index}", data=data, headers=headers, method="PUT")
        with urllib.request.urlopen(request, timeout=120) as response: response.read()
```

### JavaScript (Node.js with fetch; server only)
```javascript
const fs = require("node:fs/promises");
const {randomUUID} = require("node:crypto");
(async () => { const base = "https://lisanai.org/v1", job = process.env.JOB;
const headers = {Authorization: "Bearer " + process.env.LISAN_API_KEY};
const meta = await fetch(`${base}/jobs/${job}/upload`, {headers, signal: AbortSignal.timeout(30000)});
if (!meta.ok) throw new Error(`Upload metadata failed: ${meta.status}`);
const upload = await meta.json(), source = await fs.open(process.env.FILE, "r");
try { for (let i = 0; i < upload.total_chunks; i++) {
  if (upload.received.includes(i)) continue;
  const data = Buffer.alloc(Math.min(upload.chunk_bytes, upload.size_bytes-i*upload.chunk_bytes));
  let n=0; while(n<data.length) { const r=await source.read(data,n,data.length-n,i*upload.chunk_bytes+n); if(!r.bytesRead) throw new Error("File changed"); n+=r.bytesRead; }
  const response = await fetch(`${base}/jobs/${job}/upload/chunks/${i}`, {method:"PUT", headers:{...headers,"Content-Type":"application/octet-stream","Idempotency-Key":randomUUID()}, body:data, signal:AbortSignal.timeout(120000)});
  if (!response.ok) throw new Error(`Upload failed: ${response.status}`);
} } finally { await source.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
```

## Errors (closed list)

Body: `{"error":{"code":"...","message":"...","request_id":"..."}}`.
Field-specific validation problems can be shown in the neutral message after
validation; never substitute raw exception text. The supplied builder uses
fixed messages. No vendor, path, key, filename or transcript is returned in an error.

| Code | HTTP | Safe retry policy |
|---|---:|---|
| `invalid_request` | 400 | Resolve cause; do not repeat unchanged |
| `invalid_key` | 401 | Resolve cause; do not repeat unchanged |
| `scope_required` | 403 | Resolve cause; do not repeat unchanged |
| `plan_required` | 403 | Resolve cause; do not repeat unchanged |
| `consent_required` | 403 | Resolve cause; do not repeat unchanged |
| `not_found` | 404 | Resolve cause; do not repeat unchanged |
| `insufficient_credits` | 402 | Resolve cause; do not repeat unchanged |
| `idempotency_conflict` | 409 | Resolve cause; do not repeat unchanged |
| `job_not_ready` | 409 | Resolve cause; do not repeat unchanged |
| `revision_conflict` | 409 | Resolve cause; do not repeat unchanged |
| `quote_changed` | 409 | Resolve cause; do not repeat unchanged |
| `max_credits_exceeded` | 409 | Resolve cause; do not repeat unchanged |
| `upload_incomplete` | 409 | Wait/poll; same request key and body only |
| `result_expired` | 410 | Resolve cause; do not repeat unchanged |
| `upload_too_large` | 413 | Resolve cause; do not repeat unchanged |
| `unsupported_media` | 415 | Resolve cause; do not repeat unchanged |
| `rate_limited` | 429 | Wait/poll; same request key and body only |
| `concurrency_limited` | 429 | Wait/poll; same request key and body only |
| `daily_cap_exceeded` | 429 | Wait/poll; same request key and body only |
| `storage_full` | 409 | Resolve cause; do not repeat unchanged |
| `service_unavailable` | 503 | Wait/poll; same request key and body only |
| `internal_error` | 500 | Wait/poll; same request key and body only |

`upload_incomplete` requires missing chunks first. `daily_cap_exceeded` may stay
blocked by pending reservations after UTC midnight; check account settings/status.
`storage_full` refers to account quota; server disk pressure is 503. Revoked,
expired and unknown keys all receive the same invalid_key response. Unowned and
missing resources both return 404. Invalid HTTP JSON, duplicate keys, unsupported
content types, oversized JSON, invalid queries and framework exceptions must
all be adapted into this envelope (not the framework's default detail array).

## Pricing and limits — one source

`GET /v1/settings` is the one-page source. Its `pricing` and `limits` dictionaries
must be produced from the current website settings and the current account/key,
not from duplicated constants. `pricing.source` is `website`. `rates` entries
have neutral `code`, `title`, `credits` and `per` fields. Quotes remain authoritative
for engine-specific choices, rounding, analysis settlement and optional repair.

The quickstart prints every available rate and these effective limits from that
dictionary: upload bytes and duration per kind, chunk bytes, requests/minute,
concurrent jobs, daily credit cap and retention days. It also prints the configured
credit value in dollars. Do not promise a static price per minute or a universal
total before the text and speakers exist. A missing/unavailable balance is 503,
never zero. A zero-cost operation must not consume credits or a daily allowance.

## Settings that remain owner decisions

Persist settings for eligibility (`website_plans` / `credits_only` /
`api_plan`), pricing policy (website rates for v1), request rate, per-key
concurrency, upload/time ceilings (clamped to engine/plan limits), daily cap,
website project visibility and polling/webhook capability. Version 1 requires
`webhooks_enabled: false`; enabling a setting alone cannot introduce a callback
endpoint without a future contract and security implementation. Do not accept
callback URLs in this version. Defaults are recommendations, not production approvals.

## Implementation notes for version 1.82.37 (long dubbing)

These notes describe what the server does today. Where they are stricter or simpler than the sections above, these notes win.

* **What is live.** `POST /v1/jobs`, upload chunks, `GET .../estimate`, quotes for the steps `estimate`, `analysis` and `dub`, approval (`POST .../upload/finish` for the estimate step, `POST .../accept` for the others), job status, listing, results, downloads and delete. Short dubs, segment and speaker editing, voices, subtitles, usage and `GET /v1/settings` come in a later release; asking for the steps `clone` or `merge` returns `invalid_request`.
* **Same rules as the website.** The plan check, the billing pause, storage and disk checks, the one-payment-per-step record, refunds and the price calculators are the website's own code. The API adds only the quote ceiling, the daily cap and the retry safety described here.
* **Price.** The owner's price percentage is stored on each job when it is created and applied to the whole long-dub price list for that job. It never changes the limits (length, size, speakers).
* **Daily cap.** When a step is approved, its whole maximum (for the dub: the price plus the music-repair maximum) counts against the key's cap for that UTC day, even if less is finally charged. A refund does not give the allowance back. A step that is refused before any charge gives its reservation back immediately.
* **One approval per step.** A step of a job can be approved once for a given text revision and choices. A second quote, another key or a retry can only continue the same approval and can never charge again.
* **Quote lifetime.** 600 seconds (an estimated product default, not measured).
* **Uncertain payments.** If a payment answer is lost, the call returns `service_unavailable`, the approval stays recorded, and a retry with a new `Idempotency-Key` and the same quote continues it; the website's payment record makes a second debit impossible.
* **Active jobs.** The per-key limit counts jobs that are being analysed or dubbed (not jobs waiting for upload, estimate or your review of the text); it is checked when the analysis or the dub is approved.
