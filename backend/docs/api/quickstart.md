# Lisan AI API quickstart (contract preview)

**These /v1 routes are not live yet.** They must be integrated and tested
before these examples can connect. Examples below are complete clients for
the proposed contract; tests check their syntax without contacting any server.

Create an account key with `read`, `dub` and `account` scopes after the owner
accepts the current terms/rights statement. Put the key in the server environment
as `LISAN_API_KEY`. Never embed it in browser code, paste it into a URL, enable
shell tracing, or store it in a client state file. Use a small clip you may dub.

All clients take the same arguments:

`short|long FILE STATE_PATH MAX_ESTIMATE MAX_ANALYSIS MAX_CLONE MAX_DUB MAX_MERGE`

Every maximum is a whole credit budget **you choose**, not a price in these docs.
Running the client explicitly approves each step within those budgets. To inspect
a quote without approving it, use a zero budget for a paid step; the client stops
and shows the quoted maximum. Short analysis can have zero immediate charge:
its recorded analysis costs are still included in the later dub quote. Long dub
includes cloning and merge in MAX_DUB; MAX_CLONE/MAX_MERGE are unused there.
The sum of accepted step maxima is your total ceiling; no step is auto-funded
from unused budget in another. Required website balance/reserve rules still apply.

The clients use the automatically prepared transcript and speaker choices. Review
in the website or call GET/PUT segments and speakers before approving dub if you
need changes. A new local state is a new job. Reuse the same state, original file
and arguments to resume; never create a new key to bypass a failed paid request.
Protect the state directory: it contains job ids and quotes, although no API key.

## Pricing and limits in one page

The clients read `settings = GET /v1/settings`, then print the `pricing` and
`limits` dictionaries. Those numbers must be read live from the website/account
configuration: rates (`credits` per `per`), credit value, short/long upload size
and duration, chunk size, request rate, concurrent jobs, daily cap and retention.
No credit rates are hard-coded below. Static protocol/client numbers (timeouts,
poll interval and idempotency-key length) are implementation recommendations,
**estimated defaults**, not measured job durations. All fee amounts shown at run
time are returned by settings/quotes; a quote is the authoritative price.

## Python — standard library, server side

Save as `lisan_client.py`, then run `python lisan_client.py` with the arguments
above. STATE_PATH is a JSON filename in a private directory. Python 3.10 or newer
is a recommended client environment (**estimated compatibility floor**).

```python
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "https://lisanai.org/v1"
mode, filename, state_name, *values = sys.argv[1:]
if mode not in ("short", "long") or len(values) != 5:
    raise SystemExit("Use: short|long FILE STATE_PATH MAX_ESTIMATE MAX_ANALYSIS MAX_CLONE MAX_DUB MAX_MERGE")
budgets = dict(zip(("estimate", "analysis", "clone", "dub", "merge"), map(int, values)))
if min(budgets.values()) < 0:
    raise SystemExit("Budgets must be nonnegative whole credits.")
source, state_file = Path(filename), Path(state_name)
auth = {"Authorization": "Bearer " + os.environ["LISAN_API_KEY"]}
digest = hashlib.sha256()
with source.open("rb") as handle:
    for block in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(block)
identity = {"sha256": digest.hexdigest(), "kind": mode, "size_bytes": source.stat().st_size}
state = json.loads(state_file.read_text()) if state_file.exists() else {"source": identity, "done": []}
if state["source"] != identity:
    raise SystemExit("Use the same original file and workflow when resuming.")

def save():
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_file.with_suffix(state_file.suffix + ".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(state_file)

def request(method, path, body=None, key=None, binary=False):
    headers = dict(auth)
    data = body if binary else (json.dumps(body).encode() if body is not None else None)
    if data is not None:
        headers["Content-Type"] = "application/octet-stream" if binary else "application/json"
    if key:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120 if binary else 30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = json.load(error)["error"]
            code, request_id = detail["code"], detail["request_id"]
        except (ValueError, KeyError, TypeError):
            code, request_id = "http_error", "unavailable"
        raise SystemExit(f"HTTP {error.code}: {code}; request {request_id}. Resume with the same state after resolving it.")

def completed(name):
    if name not in state["done"]:
        state["done"].append(name)
    state.pop("pending_" + name, None)
    save()

def poll(target):
    deadline = time.monotonic() + 6 * 60 * 60
    while time.monotonic() < deadline:
        job = request("GET", f"/jobs/{state['job_id']}")
        if job["status"] == "failed":
            raise SystemExit("Job failed. Check its status and receipt before retrying.")
        if job["status"] == target:
            return job
        time.sleep(5)
    raise SystemExit("Still processing. Resume with the same state later.")

settings = request("GET", "/settings")
print("Pricing and limits:", json.dumps({k: settings[k] for k in ("pricing", "limits")}, indent=2))
print("Balance:", request("GET", "/account/balance")["credits"], "credits")
if "job_id" not in state:
    pending = state.setdefault("pending_create", {"key": uuid.uuid4().hex, "body": {
        "kind": mode, "filename": source.name, "size_bytes": identity["size_bytes"],
        "rights_confirmed": True, "terms_version": settings["terms_version"]}})
    save()
    state["job_id"] = request("POST", "/jobs", pending["body"], pending["key"])["id"]
    state.pop("pending_create", None)
    save()
job_path = "/jobs/" + state["job_id"]
if "upload" not in state["done"]:
    upload = request("GET", job_path + "/upload")
    with source.open("rb") as handle:
        for index in range(upload["total_chunks"]):
            if index in upload["received"]:
                continue
            handle.seek(index * upload["chunk_bytes"])
            block = handle.read(upload["chunk_bytes"])
            key = hashlib.sha256((state["job_id"] + ":" + str(index)).encode() + block).hexdigest()
            request("PUT", job_path + f"/upload/chunks/{index}", block, key, binary=True)
    completed("upload")

def approve(step, target, options=None):
    if step in state["done"]:
        return
    name = "pending_" + step
    if name not in state:
        quote = request("POST", job_path + "/quotes", dict(step=step, **(options or {})), uuid.uuid4().hex)
        print(step, "maximum:", quote["quoted_credits"], "credits")
        if quote["quoted_credits"] > budgets[step]:
            raise SystemExit("Quote exceeds your budget. No approval was sent.")
        state[name] = {"key": uuid.uuid4().hex, "body": {"quote_id": quote["id"],
            "quoted_credits": quote["quoted_credits"], "max_credits": budgets[step],
            "terms_version": quote["terms_version"]}}
        save()
    pending = state[name]
    if pending["body"]["max_credits"] > budgets[step]:
        raise SystemExit("Saved approval exceeds the new budget. Check the job before changing it.")
    path = job_path + ("/upload/finish" if step == "estimate" else "/accept")
    request("POST", path, pending["body"], pending["key"])
    poll(target)
    completed(step)

approve("estimate", "estimated")
approve("analysis", "editing")
if mode == "short" and "clone" not in state["done"]:
    speakers = request("GET", job_path + "/speakers")["speakers"]
    originals = [row["id"] for row in speakers if row["voice_mode"] == "original"]
    if originals or "pending_clone" in state:
        approve("clone", "editing", {"speaker_ids": originals})
    else:
        completed("clone")
approve("dub", "done" if mode == "long" else "editing", {"keep_music": True, "tracks": True})
if mode == "short":
    approve("merge", "done", {"keep_music": True, "tracks": True})
results = request("GET", job_path + "/results")["results"]
if not any(result["kind"] == "mixed" for result in results):
    raise SystemExit("No mixed result is available. Check job status and retention.")
output = state_file.with_suffix(".dub")
req = urllib.request.Request(BASE + job_path + "/results/mixed", headers=auth)
with urllib.request.urlopen(req, timeout=120) as response, output.open("wb") as destination:
    while block := response.read(1024 * 1024):
        destination.write(block)
print("Downloaded:", output, "— use the result media_type to choose its extension.")
```

## JavaScript — Node.js, never browser code

Save as `lisan_client.cjs`; run `node lisan_client.cjs` with the same arguments.
STATE_PATH is a private JSON filename. Use a Node.js release with built-in fetch
and AbortSignal.timeout; Node.js 18+ is an **estimated compatibility floor**.

```javascript
const fs = require("node:fs/promises");
const path = require("node:path");
const {createHash, randomUUID} = require("node:crypto");
const {createReadStream} = require("node:fs");
const {Readable} = require("node:stream");
const {pipeline} = require("node:stream/promises");
const {createWriteStream} = require("node:fs");

(async () => {
  const [mode, filename, stateName, ...values] = process.argv.slice(2);
  if (!["short", "long"].includes(mode) || values.length !== 5 || values.some(v => !/^\d+$/.test(v)))
    throw new Error("Use: short|long FILE STATE_PATH MAX_ESTIMATE MAX_ANALYSIS MAX_CLONE MAX_DUB MAX_MERGE");
  const budgets = Object.fromEntries(["estimate", "analysis", "clone", "dub", "merge"].map((s, i) => [s, Number(values[i])]));
  if (Object.values(budgets).some(v => !Number.isSafeInteger(v) || v > 2147483647)) throw new Error("Invalid credit budget");
  if (!process.env.LISAN_API_KEY) throw new Error("Set LISAN_API_KEY on your server");
  const base = "https://lisanai.org/v1", headers = {Authorization: "Bearer " + process.env.LISAN_API_KEY};
  const digest = createHash("sha256");
  for await (const block of createReadStream(filename)) digest.update(block);
  const identity = {sha256: digest.digest("hex"), kind: mode, size_bytes: (await fs.stat(filename)).size};
  let state;
  try { state = JSON.parse(await fs.readFile(stateName, "utf8")); }
  catch (e) { if (e.code !== "ENOENT") throw e; state = {source: identity, done: []}; }
  if (JSON.stringify(state.source) !== JSON.stringify(identity)) throw new Error("Use the same original file and workflow when resuming");
  async function save() {
    await fs.mkdir(path.dirname(stateName), {recursive: true});
    await fs.writeFile(stateName + ".tmp", JSON.stringify(state));
    await fs.rename(stateName + ".tmp", stateName);
  }
  async function request(method, route, body, key, binary = false) {
    const response = await fetch(base + route, {method, headers: {...headers,
      ...(key ? {"Idempotency-Key": key} : {}),
      ...(body !== undefined ? {"Content-Type": binary ? "application/octet-stream" : "application/json"} : {})},
      body: body === undefined ? undefined : binary ? body : JSON.stringify(body),
      redirect: "error", signal: AbortSignal.timeout(binary ? 120000 : 30000)});
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(`HTTP ${response.status}: ${error.error?.code || "http_error"}; resume with the same state after resolving it`);
    }
    return response.json();
  }
  async function completed(step) {
    if (!state.done.includes(step)) state.done.push(step);
    delete state["pending_" + step];
    await save();
  }
  async function poll(target) {
    const deadline = Date.now() + 6 * 60 * 60 * 1000;
    while (Date.now() < deadline) {
      const job = await request("GET", "/jobs/" + state.job_id);
      if (job.status === "failed") throw new Error("Job failed; check its receipt before retrying");
      if (job.status === target) return;
      await new Promise(resolve => setTimeout(resolve, 5000));
    }
    throw new Error("Still processing; resume with the same state later");
  }
  const settings = await request("GET", "/settings");
  console.log("Pricing and limits:", JSON.stringify({pricing: settings.pricing, limits: settings.limits}, null, 2));
  console.log("Balance:", (await request("GET", "/account/balance")).credits, "credits");
  if (!state.job_id) {
    state.pending_create ||= {key: randomUUID(), body: {kind: mode, filename: path.basename(filename),
      size_bytes: identity.size_bytes, rights_confirmed: true, terms_version: settings.terms_version}};
    await save();
    state.job_id = (await request("POST", "/jobs", state.pending_create.body, state.pending_create.key)).id;
    delete state.pending_create;
    await save();
  }
  const jobPath = "/jobs/" + state.job_id;
  if (!state.done.includes("upload")) {
    const upload = await request("GET", jobPath + "/upload"), source = await fs.open(filename, "r");
    try { for (let i = 0; i < upload.total_chunks; i++) {
      if (upload.received.includes(i)) continue;
      const data = Buffer.alloc(Math.min(upload.chunk_bytes, upload.size_bytes - i * upload.chunk_bytes));
      let n = 0;
      while (n < data.length) {
        const part = await source.read(data, n, data.length - n, i * upload.chunk_bytes + n);
        if (!part.bytesRead) throw new Error("Source file changed");
        n += part.bytesRead;
      }
      const key = createHash("sha256").update(state.job_id + ":" + i).update(data).digest("hex");
      await request("PUT", jobPath + "/upload/chunks/" + i, data, key, true);
    } } finally { await source.close(); }
    await completed("upload");
  }
  async function approve(step, target, options = {}) {
    if (state.done.includes(step)) return;
    const name = "pending_" + step;
    if (!state[name]) {
      const quote = await request("POST", jobPath + "/quotes", {step, ...options}, randomUUID());
      console.log(step, "maximum:", quote.quoted_credits, "credits");
      if (quote.quoted_credits > budgets[step]) throw new Error("Quote exceeds your budget; no approval sent");
      state[name] = {key: randomUUID(), body: {quote_id: quote.id, quoted_credits: quote.quoted_credits,
        max_credits: budgets[step], terms_version: quote.terms_version}};
      await save();
    }
    if (state[name].body.max_credits > budgets[step]) throw new Error("Saved approval exceeds new budget; check job first");
    await request("POST", jobPath + (step === "estimate" ? "/upload/finish" : "/accept"), state[name].body, state[name].key);
    await poll(target);
    await completed(step);
  }
  await approve("estimate", "estimated");
  await approve("analysis", "editing");
  if (mode === "short" && !state.done.includes("clone")) {
    const speakers = (await request("GET", jobPath + "/speakers")).speakers;
    const originals = speakers.filter(row => row.voice_mode === "original").map(row => row.id);
    if (originals.length || state.pending_clone) await approve("clone", "editing", {speaker_ids: originals});
    else await completed("clone");
  }
  await approve("dub", mode === "long" ? "done" : "editing", {keep_music: true, tracks: true});
  if (mode === "short") await approve("merge", "done", {keep_music: true, tracks: true});
  const results = (await request("GET", jobPath + "/results")).results;
  if (!results.some(r => r.kind === "mixed")) throw new Error("Mixed result is unavailable; check retention");
  const response = await fetch(base + jobPath + "/results/mixed", {headers, redirect: "error", signal: AbortSignal.timeout(120000)});
  if (!response.ok || !response.body) throw new Error(`Download failed: ${response.status}`);
  await pipeline(Readable.fromWeb(response.body), createWriteStream(stateName + ".dub"));
  console.log("Downloaded:", stateName + ".dub", "— choose its extension from result media_type");
})().catch(error => { console.error(error.message); process.exitCode = 1; });
```

## curl — POSIX shell with jq, openssl, dd and sha256sum

Save as `lisan_client.sh`; run `bash lisan_client.sh` with the same arguments.
For this client STATE_PATH is a private directory. Use a separate directory for
each new job; retain it when resuming. curl must support `--fail-with-body`.

```bash
#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 8 ]; then echo 'Use: short|long FILE STATE_DIR MAX_ESTIMATE MAX_ANALYSIS MAX_CLONE MAX_DUB MAX_MERGE'; exit 1; fi
MODE=$1; FILE=$2; STATE=$3
MAX_ESTIMATE=$4; MAX_ANALYSIS=$5; MAX_CLONE=$6; MAX_DUB=$7; MAX_MERGE=$8
[[ "$MODE" == short || "$MODE" == long ]] || exit 1
for budget in "$MAX_ESTIMATE" "$MAX_ANALYSIS" "$MAX_CLONE" "$MAX_DUB" "$MAX_MERGE"; do
  [[ "$budget" =~ ^[0-9]+$ ]] || { echo 'Use whole nonnegative budgets'; exit 1; }
done
: "${LISAN_API_KEY:?Set LISAN_API_KEY on your server}"
BASE=https://lisanai.org/v1
mkdir -p "$STATE"
IDENTITY="$MODE:$(sha256sum "$FILE" | cut -d ' ' -f 1)"
if [ -f "$STATE/source" ]; then
  [ "$(cat "$STATE/source")" = "$IDENTITY" ] || { echo 'Use the same source when resuming'; exit 1; }
else printf '%s' "$IDENTITY" > "$STATE/source"; fi
random_key() { openssl rand -hex 16; }
api() { curl --fail-with-body -sS --connect-timeout 15 --max-time 120 -H "Authorization: Bearer $LISAN_API_KEY" "$@"; }
api "$BASE/settings" > "$STATE/settings.json"
jq '{pricing,limits}' "$STATE/settings.json"
api "$BASE/account/balance"
if [ ! -f "$STATE/job" ]; then
  if [ ! -f "$STATE/create.json" ]; then
    SIZE=$(wc -c < "$FILE" | tr -d '[:space:]')
    jq -n --arg kind "$MODE" --arg filename "$(basename "$FILE")" --argjson size "$SIZE" --arg terms "$(jq -r .terms_version "$STATE/settings.json")"       '{kind:$kind,filename:$filename,size_bytes:$size,rights_confirmed:true,terms_version:$terms}' > "$STATE/create.json"
    random_key > "$STATE/create.key"
  fi
  api -X POST -H "Idempotency-Key: $(cat "$STATE/create.key")" -H 'Content-Type: application/json'     --data-binary "@$STATE/create.json" "$BASE/jobs" > "$STATE/created.json"
  jq -er .id "$STATE/created.json" > "$STATE/job"
fi
JOB=$(cat "$STATE/job"); JOB_PATH="$BASE/jobs/$JOB"
if [ ! -f "$STATE/upload.done" ]; then
  api "$JOB_PATH/upload" > "$STATE/upload.json"
  CHUNK=$(jq -r .chunk_bytes "$STATE/upload.json"); COUNT=$(jq -r .total_chunks "$STATE/upload.json")
  for ((i=0;i<COUNT;i++)); do
    if jq -e --argjson i "$i" '.received | index($i)' "$STATE/upload.json" >/dev/null; then continue; fi
    dd if="$FILE" of="$STATE/chunk.bin" bs="$CHUNK" skip="$i" count=1 2>/dev/null
    KEY=$(printf '%s' "$JOB:$i:$(sha256sum "$STATE/chunk.bin" | cut -d ' ' -f 1)" | sha256sum | cut -d ' ' -f 1)
    api -X PUT -H "Idempotency-Key: $KEY" -H 'Content-Type: application/octet-stream'       --data-binary "@$STATE/chunk.bin" "$JOB_PATH/upload/chunks/$i" > "$STATE/upload-response.json"
  done
  rm -f "$STATE/chunk.bin"; touch "$STATE/upload.done"
fi
poll() {
  local target=$1 current
  for ((n=0;n<4320;n++)); do
    api "$JOB_PATH" > "$STATE/status.json"; current=$(jq -r .status "$STATE/status.json")
    [ "$current" != failed ] || { echo 'Job failed; check its receipt before retrying'; exit 1; }
    [ "$current" != "$target" ] || return 0
    sleep 5
  done
  echo 'Still processing; resume with the same state'; exit 1
}
approve() {
  local step=$1 budget=$2 target=$3 options=$4 route
  [ ! -f "$STATE/$step.done" ] || return 0
  if [ ! -f "$STATE/$step.accept.json" ]; then
    printf '%s' "$options" | jq --arg step "$step" '. + {step:$step}' > "$STATE/quote-request.json"
    api -X POST -H "Idempotency-Key: $(random_key)" -H 'Content-Type: application/json'       --data-binary "@$STATE/quote-request.json" "$JOB_PATH/quotes" > "$STATE/quote.json"
    jq '{step,quoted_credits,required_balance}' "$STATE/quote.json"
    jq -e --argjson budget "$budget" '.quoted_credits <= $budget' "$STATE/quote.json" >/dev/null || { echo 'Quote exceeds budget; no approval sent'; exit 1; }
    random_key > "$STATE/$step.key"
    jq --argjson budget "$budget" '{quote_id:.id,quoted_credits,max_credits:$budget,terms_version}' "$STATE/quote.json" > "$STATE/$step.accept.tmp"
    mv "$STATE/$step.accept.tmp" "$STATE/$step.accept.json"
  fi
  jq -e --argjson budget "$budget" '.max_credits <= $budget' "$STATE/$step.accept.json" >/dev/null || { echo 'Saved approval exceeds new budget; check job first'; exit 1; }
  route=accept; [ "$step" != estimate ] || route=upload/finish
  api -X POST -H "Idempotency-Key: $(cat "$STATE/$step.key")" -H 'Content-Type: application/json'     --data-binary "@$STATE/$step.accept.json" "$JOB_PATH/$route" > "$STATE/accepted.json"
  poll "$target"; touch "$STATE/$step.done"
}
approve estimate "$MAX_ESTIMATE" estimated '{}'
approve analysis "$MAX_ANALYSIS" editing '{}'
if [ "$MODE" = short ] && [ ! -f "$STATE/clone.done" ]; then
  api "$JOB_PATH/speakers" > "$STATE/speakers.json"
  OPTIONS=$(jq '{speaker_ids:[.speakers[] | select(.voice_mode=="original") | .id]}' "$STATE/speakers.json")
  if [ -f "$STATE/clone.accept.json" ] || [ "$(printf '%s' "$OPTIONS" | jq '.speaker_ids | length')" -gt 0 ]; then
    approve clone "$MAX_CLONE" editing "$OPTIONS"
  else touch "$STATE/clone.done"; fi
fi
TARGET=done; [ "$MODE" != short ] || TARGET=editing
approve dub "$MAX_DUB" "$TARGET" '{"keep_music":true,"tracks":true}'
if [ "$MODE" = short ]; then approve merge "$MAX_MERGE" done '{"keep_music":true,"tracks":true}'; fi
api "$JOB_PATH/results" > "$STATE/results.json"
jq -e '.results | any(.kind=="mixed")' "$STATE/results.json" >/dev/null || { echo 'Result unavailable; check retention'; exit 1; }
api "$JOB_PATH/results/mixed" -o "$STATE/result.dub"
echo "Downloaded $STATE/result.dub — choose its extension from result media_type"
```

## Resume, errors and review

All three clients persist each paid request's exact quote/body/idempotency key
**before** approval. If a request times out or a 429/503 occurs, keep the state,
wait at least `Retry-After`, and rerun. They stop on HTTP errors rather than
silently changing a budget, accepting a new price or generating a new paid key.
If an unconsumed quote expires or is rejected after edits, inspect job status and
confirm that no operation/debit exists before clearing that step's pending
approval. Then rerun to obtain a new quote. The server must supply a stable
business operation so this cannot charge a stage twice after the replay TTL.

Do not run multiple clients with the same state file/directory concurrently.
Durable server claims, not client files, prevent duplicate payment. Downloads
stream to disk and can be restarted without credits; these examples do not
resume a partial download. A job can still fail after approval; website refund
rules and the operation receipt determine the charge. Inspect `/usage` for
positive spent/refunded amounts and the current balance for the final result.

Read-only usage example: `GET /v1/usage?start_day=YYYY-MM-DD&end_day=YYYY-MM-DD`.
Subtitles: `GET /v1/jobs/{id}/subtitles?language=ar&format=srt`. Separate tracks:
list `/results`, then download `/results/dialogue` or `/results/background` only
if available. These paths use the same Authorization header and account checks.
