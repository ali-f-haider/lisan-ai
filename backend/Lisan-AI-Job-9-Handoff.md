# Lisan AI — Job 9 handoff

**Ali: the roughly 1.8 GB idle reading in your capture is plausible after heavy audio processing, but it is not proved to be an unavoidable baseline.** Startup with weights unloaded reached **334.379 MiB RSS (measured here, Windows)**. A separate local run reached **504.879 MiB RSS (measured here, Windows)** after importing the speaker-detection libraries and loading the packaged activity detector, with the larger processing weights still unloaded. These are comparisons, not promised Railway targets. The realistic Railway floor remains **unmeasured**: it is the steady memory of the same Linux image after an ordinary job, with no active workers or retained large model objects. Fix the three demonstrated release gaps first, then measure that floor before deciding on import delays or allocator settings. This batch adds measurement and diagnostics; it does **not** claim to have reduced production RAM.

Date: 2026-10-08. Started from the files identified as 1.82.25. During this shared-workspace session, another change set raised the version to 1.82.26 and added log messages in `longdub_service.py` and `r2_backup.py`. Those edits are preserved and excluded from this delivery. Git cannot inspect this checkout in the agent sandbox, so no HEAD or clean-worktree claim is made.

## 1. Scope and changed files

Backend directory: `C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend`.

| File | Change |
|---|---|
| `main.py` | Only `admin_mem_diag()` changed: add a `runtime` inventory, retain old fields and authorization, and correct a misleading disk-cache comment. |
| `memory_diagnostics.py` | New standard-library helper. Reads loaded-module state, counts containers/threads/GC-tracked objects, and reads Linux process rollup counters. |
| `tools/memory_baseline.py` | New standalone offline import/RSS measurement tool; not imported by the app. |
| `tests/test_memory_baseline.py` | Seven offline measurement-tool tests. |
| `tests/test_memory_diagnostics.py` | Eleven inventory/privacy/endpoint tests. |
| `Lisan-AI-Job-9-Handoff.md` | This report. |

No audio-pipeline, model-release, money, SQL, Dockerfile or version edits were made by this job. No commits or pushes. The AST of `main.py` outside `admin_mem_diag()` is identical to the initial snapshot, and retained source lines keep their exact original bytes and line endings. Other than the three separately observed shared-workspace changes above, all original top-level Python files match their starting SHA-256 checksums.

## 2. What your capture establishes

The existing diagnostic labels these figures MB, but its conversion divides bytes by 1024 twice: they are binary MiB. All values in this table are **read from Ali's capture**, not measured live by this audit.

| Captured quantity | Value from Ali's capture | Interpretation |
|---|---:|---|
| Container used | 1843.4 MiB | Container accounting, not identical to process RSS. |
| Container limit | 22888.2 MiB | A limit, not the amount used. |
| File cache | 17.5 MiB | Small compared with total usage at this reading. |
| Anonymous memory | 1795.1 MiB | Dominant component; includes live allocations and potentially retained allocator pages. It does not identify a model. |
| Single uvicorn RSS | 1898 MiB | Process residency. Shared/file-backed pages and sampling differences prevent treating it as the same metric as container usage. |
| Idle time | 416.5 minutes | Consistent with retained memory while idle; it does not prove the process is leak-free. |
| Downloaded-model disk cache | 5895.3 MiB, 17 files | Disk bytes; not a measurement of resident weights. |
| Other processing-model disk cache | 142.3 MiB, 19 files | Also disk bytes, not resident weights. |

I agree that page cache was not the main problem **at the captured moment**. The small cache is consistent with the existing flush mechanism working, but one reading cannot prove the flush caused it. I agree that a flat graph is consistent with a retained baseline. An accumulation from earlier jobs can also remain flat once work stops, so flatness alone does not rule that out. The capture shows no persistent separation child process; a stuck child is therefore not demonstrated in this capture.

Source anchors: [container reader](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:7765>), [admin endpoint](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:7842>), [idle flush](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/main.py:3574>), [release/cache helpers](<C:/Users/Ali Haider/Desktop/ai-dubbing-app/backend/whisper_service.py:38>).

## 3. Part A.1 — import measurements

**Every memory figure in the following two tables is measured here**, using current Windows working-set RSS, in MiB. Import seconds are also local measurements. Windows is not Railway's Linux allocator, loader or cgroup environment. No large transcription or speaker-detection weights were downloaded or loaded. No app startup workers, credentials, outbound calls or paid operations were used.

Environment: Windows 11 build 26200; Python 3.12.14; numpy 1.26.4; scipy 1.17.1; torch/torchaudio 2.5.1+cpu; faster-whisper 1.1.1; ctranslate2 4.8.2; pyannote.audio 3.1.1; transformers 4.38.2; demucs 4.0.1; fastapi 0.115.6; onnxruntime 1.30.0. Some transitive requirements are not pinned, so a Railway rebuild may have different versions.

### Eager imports, in source order

Fresh interpreter plus probe/AST-planning overhead: **46.527 MiB (measured here)**. The tool follows eager local-module imports recursively in source order. Function and conditional bodies are deferred; monitoring initialization is deliberately disabled by dummy credentials. Deltas include transitive imports and allocations left by earlier stages; they are not independent package sizes.

| Import | RSS MiB, measured here | Added MiB, measured here | Seconds, measured here |
|---|---:|---:|---:|
| fastapi.staticfiles | 56.496 | 9.965 | 0.3889 |
| numpy | 62.805 | 6.309 | 0.1411 |
| soundfile | 63.730 | 0.926 | 0.0101 |
| scipy.signal | 113.801 | 50.070 | 1.1522 |
| torch | 244.172 | 130.367 | 12.5771 |
| faster_whisper | 261.711 | 17.539 | 0.3444 |
| av | 261.719 | 0.004 | 0.0001 |
| urllib3 | 261.723 | 0.000 | 0.0000 |
| elevenlabs.client | 279.965 | 18.230 | 1.2000 |
| botocore.exceptions | 280.164 | 0.195 | 0.0164 |
| stripe | 322.590 | 42.426 | 1.6636 |
| main, after these imports | 334.379 | 11.781 | 0.9005 |

The tiny explicit `av`/`urllib3` additions reflect earlier transitive loading. Rounded RSS and rounded deltas may differ slightly when subtracted. All listed imports succeeded; there are no fabricated fallback figures. The tool records failures as unavailable and warns that their residual allocation is not a successful library cost.

### Deferred libraries, separate fresh process

Fresh interpreter plus probe: **29.324 MiB (measured here)**. This is a different process, so do not add these deltas to the eager table. In particular, the first row includes dependencies already present after normal app startup.

| Import | RSS MiB, measured here | Added MiB, measured here | Seconds, measured here |
|---|---:|---:|---:|
| pyannote.audio | 389.625 | 360.297 | 39.4080 |
| torchaudio, already loaded | 389.637 | 0.012 | 0.0001 |
| demucs.separate | 395.211 | 5.555 | 0.3763 |
| transformers, already loaded | 395.230 | 0.012 | 0.0001 |
| boto3 | 403.074 | 7.832 | 0.3926 |
| fal_client | 406.215 | 3.137 | 0.1380 |
| dashscope | 418.953 | 12.723 | 0.7910 |
| sentry_sdk | 421.375 | 2.414 | 0.1291 |

Importing `demucs.separate` here measures a library in a test process. The real server runs it in a child process; it is not an extra persistent server import to remove. Likewise, the monitoring SDK's import-only cost does not measure configured monitoring initialization.

### Extra check after normal startup

A second experiment imported the eager plan and `main`, then the speaker libraries, then the activity detector's **packaged local ONNX weights**. All figures in this paragraph are **measured here on Windows**, not a Railway prediction: `main` reached **337.582 MiB RSS**; importing `pyannote.audio` added **143.141 MiB** in **38.9107 seconds**, reaching **480.727 MiB**; loading the activity detector added **24.137 MiB** in **0.3932 seconds**, reaching **504.879 MiB**. The activity-detector delta includes its runtime library and session initialization; it is not just the weights' size, nor a proven saving from clearing its cache. None of these measurements loads the transcription or speaker-detection weights.

### How to reproduce

From `backend`, using an environment with the app's dependencies:

```cmd
python tools/memory_baseline.py --first-use --json memory-baseline.json
```

If needed, point it at an existing dependency directory with `--site-packages`. This audit used the bundled Python with `--site-packages .venv/Lib/site-packages`. The tool starts a fresh child for each profile, strips credential variables and inherited PYTHONPATH, hides the two config credential files from the config loader, blocks outbound socket connections and Python thread starts, and puts DATA_DIR in temporary storage. Native library threads may still be created. It does not modify app configuration or persistent project data. It never calls model loaders. Run a Linux copy in the same built image during a quiet period to obtain comparable Linux numbers; running a probe also temporarily consumes memory.

Evidence files in the delivery ZIP include `baseline.json`, `post-main-probe.json`, and the standalone extra probe used for the second experiment.

## 4. Part A.2 — heavy objects and lifetime

Library imports stay in `sys.modules`; setting a model variable to `None` does not unload native libraries or guarantee every native allocation returns to the OS. The following sizes are deliberately unknown where no weight-loading measurement was performed.

| Object / source | Creation | Release / retention | Size evidence |
|---|---|---|---|
| Tensor/numerical libraries: `whisper_service.py:8–9,34`, `audio_enhance.py:13–15` | Eager imports; thread configuration is applied during module import. | Libraries and native runtime state persist for the process lifetime. | Import deltas measured here in the eager table; not weight costs. |
| Transcription `_model`: `whisper_service.py:149,340–351` | Initially `None`; `_get_model()` creates it under a lock on first use. | `_release_model()` removes the global reference, collects and trims, subject to the gaps below. | Actual resident weights unmeasured. Download-cache disk size cannot establish this. |
| Speaker `diarization_pipelines`: `app_state.py:6`; `whisper_service.py:416–443` | Empty at import. First call imports libraries, creates a pipeline and stores it under the token. | Token entry popped after the speaker step; local/thread references and late insertion matter. Imports remain. | Weights unmeasured. Library-only incremental cost after main measured separately above. |
| Separation model: `ffmpeg_utils.py:135–145` | Child interpreter creates it when `separate_vocals()` runs. | OS reclaims child memory on child exit. No parent model cache. | Child weights/peak unmeasured; parent import table must not be used as the separation peak. |
| Activity detector: `vad_utils.py:22–42`; installed `faster_whisper/vad.py:247–275` | Loader function imported eagerly; cached ONNX sessions created on first activity check. | Zero-argument `lru_cache` retains one model; no application `cache_clear()` found. Arena disabled in these session options, but sessions still occupy memory. | Loading runtime plus sessions added 24.137 MiB measured here; actual recoverable session memory unknown. |
| Voice matching: `voice_match.py:20,79–90,248–277`; `voice_match_service.py:26–53` | Numpy acoustic profiles; metadata loaded into a single voice-list cache. | Profile arrays are local; acoustic profile cache is on disk. Voice list replaced/refreshed or explicitly reset. | No persistent embedding model was found in the reviewed implementation. Temporary-array peaks unmeasured. |
| Voice SDK client: `eleven_service.py:49,618–619` | `None` at import; first request creates client. | Reused for process lifetime. | SDK import measured; actual client size unmeasured. No module-level audio payload cache found here. |
| Backup client: `r2_backup.py:25,33–54` | `None` at import; lazy boto3 client when enabled and used. | One client retained. | Client size unmeasured; boto3 import-only cost measured separately. |
| Monitor aggregates: `resource_meter.py:176–229`; monitor modules | Small instances/dictionaries at import. | Current-hour/current-reading state replaced; history goes to disk, not an ever-growing in-memory history list. | Structurally bounded; no resident-size measurement. |

The whole-file speaker pass remains relevant to **peak**, not automatically to idle residency. `longdub_service._run_analysis()` calls speaker detection on the entire vocals file (`:1667–1687`); normalization is mono 16 kHz (`ffmpeg_utils.py:94–101`) and `sf.read(..., dtype='float32')` loads it (`whisper_service.py:430`). One hour of that array alone is **219.727 MiB estimated**, calculated as `3600 × 16000 × 4 / 1048576`. Model activations, temporary conversions and overlapping jobs add more. The tensor shares the numpy storage at creation; do not count it as a second full copy without evidence. Long transcription is piece-by-piece, but whole-file speaker detection is not. This batch does not change chunking or speaker behavior.

## 5. Part A.3 — container audit

Sizes below are not measured; entries contain metadata unless stated otherwise. An unbounded container is a lifetime defect, not proof that it explains the captured anonymous memory. Static sets/templates/one-record configuration caches are not per-job leaks. Source locations are relative to the backend directory stated above.

| Container / source | What grows it | What shrinks it | Verdict |
|---|---|---|---|
| `USER_GAINS`, `eleven_service.py:62,691,950,1026` | Generate, regenerate and remix, per-job line gains. | No purge found. | **Never purged; protected.** Needed while that project remains editable. |
| `VOICE_ANCHORS`, same file `:63,852` | Per-job speaker anchors. | No purge found. | **Never purged; protected.** |
| `ROOM_SETTINGS`, same file `:65,1043` | Saved per-job room choices. | No purge found. | **Never purged; protected.** |
| `ROOM_LAST`, same file `:64,110` | Last room result/assignments for a job. | No purge found. | **Never purged; protected.** |
| `jobs_progress`, `app_state.py:4`; `main.py:3548–3564` | Plain job IDs and `emotions_`, `generate_`, `lipsync_` entries. | Housekeeping removes all four forms for expired `_job_started` IDs. Abandon removes only plain progress immediately. | **Purged conditionally.** Orphan keys without tracked timestamps do not reach this sweep. Default short retention in current code is six hours (`main.py:2526`), not every request. |
| `USAGE`, `app_state.py:5,25–36` | One usage bucket per job; fallback `session` bucket. Long translation/tashkeel also pass the long project ID (`longdub_service.py:2109,2250`; `gemini_service.py:248`). | Housekeeping removes tracked expired short jobs. No matching long-project removal found. | **Short jobs purged conditionally; long-job buckets never purged by this sweep.** Single fallback bucket retained. Accounting state must not be evicted casually. |
| `diarization_pipelines`, `app_state.py:6` | First pipeline per token. | `_release_diarization_pipeline()` pops token. | **Purged with release/race gaps**, detailed below. |
| `_job_started`, `_job_charges`, `_abandoned_jobs`, `main.py:1253–1255` | Upload/attach, charges, abandon. | Six-hour timestamp sweep pops/discards all three. | **Tracked jobs purged**; protect active jobs and unsettled operations in any future change. |
| `_job_owner`, `main.py:1331–1337` | Registering job ownership. | No purge found. | **Never purged.** Ownership/security behavior must remain correct after any eviction. |
| `_job_owner_lookup`, `main.py:1332,1352–1359` | Database ownership lookups. | Cache clears when above 4000; TTL prevents stale reuse. | **Bounded**; TTL alone does not proactively delete keys. |
| `_sessions`, `_valid_tokens`, `_session_users`, `main.py:333–334,1252` | Login/restore/account lookup; verified access tokens also become keys in `_valid_tokens` (`:447–457`). | `_forget_session()` removes cookie entries (`:899–908`). | **Partially purged.** No sweep for abandoned sessions; separate access-token verification keys are not removed by cookie logout. Authentication expiry needs a coordinated audit, not a RAM-only deletion. |
| `_user_info_cache`, `main.py:808` | Account name lookup. | No purge found. | **Never purged**, small per user but unbounded users. |
| `_me_cache`, `main.py:969–1010` | Landing-page session/name results. | Clears above 2000; entries have five-minute validity. | **Bounded**, although idle expired keys remain until reuse/clear. |
| `_wm_cache`, `main.py:2646–2690` | One watermark decision per user. | TTL prevents reuse; no removal/cap found. | **Never proactively purged.** |
| `_login_fails`, `main.py:731,751–766` | Attempt timestamps per address. | Old timestamps trimmed when same address is checked. | **Address count unbounded.** Old untouched address keys remain. |
| `_contact_attempts`, `main.py:737,8326–8344` | Contact attempts per address. | Same-address timestamp pruning. | **Address count unbounded.** |
| `_rate_buckets`, `main.py:781–804` | Rate-limit caller keys within groups. | Same-caller timestamp pruning. | **Caller count unbounded.** Future pruning must not reset an active rate window. |
| `_ADMIN_TOKENS`, `main.py:6511,6582–6597` | Admin login/restore. | Expired token removed when that token is checked. | **Expired untouched tokens retained.** Small expected use, no lifetime bound. |
| `_admin_fail_all`, `main.py:7039–7061` | Admin failure timestamps. | Ten-minute rolling pruning on login; attempt limit. | **Small bounded rolling window.** |
| `_biz_cache`, `main.py:7178–7198` | Reports for validated day selections. | Entries overwritten; only fixed allowed selections. | **Bounded**, five report keys. Report payload sizes unmeasured. |
| `_assistant_acct_cache`, `main.py:8107–8201,8280` | Account/project status lookups. | Short validity plus clear above 500; chat invalidation. | **Bounded.** |
| `_assist_owed`, `main.py:8213` | Carried fractional chat balances per account. | Settled/replaced during charging; no account-key purge found. | **Money state: do not evict as a cache.** If durable retirement is needed, that is a separate billing decision. No money change proposed here. |
| `_ld_event_q`, `main.py:5412–5444` | Step/event records queued by workers. | One writer drains, retrying each record up to three times. | **Potential backlog** if production rate exceeds slow writes; queue has no bound. No loss/drop policy introduced. |
| `audio_enhance._jobs`, `audio_enhance.py:17,60–93` | Enhancement status/error records. | No purge found. | **Never purged.** Status metadata, not stored waveform arrays. |
| Long `_JOBS`, `longdub_service.py:129,308–323,4841–4903` | Creating/loading project JSON, including housekeeping loading projects from disk. | Delete/account-delete/media expiry/project expiry paths pop entries. | **Eventually purged**, but all retained project metadata can remain resident. Sweep itself warms cache. No inactive LRU bound. |
| Long `_JOB_LOCKS`, `longdub_service.py:130,185–191` | One lock per seen project. | No purge found. | **Never purged.** Deleting a lock while a waiter retains it can break mutual exclusion. |
| Long `_ACCOUNT_LOCKS`, same file `:131,194–196` | One storage/account lock per user. | No purge found. | **Never purged; same lock-lifetime constraint.** |
| Long `_PREVIEW_LOCKS`, same file `:2485,2507` | Preview generation per project. | No purge found. | **Never purged; same lock-lifetime constraint.** |
| Long `_RUNNING`, same file `:132,1191–1225` | Worker start. | Worker `finally` discards ID. | **Purged on ordinary worker exit.** If thread start itself fails before worker runs, the inserted marker can remain; this is not proof of retained model weights. |
| `shortdub_paths._active_operations`, `:9` | Operation begin. | Operation finish in worker `finally` paths. | **Active-only state**, existing isolation tests cover it. |
| `resource_meter._meters`, `:229,442,512` | Running job meter enters. | Meter exit removes itself. | **Active-only state**; `_hours` is one current record. |
| `subs_align._sim_cache`, `:216–230` | Normalized word-pair similarity results. | Clears above 200000 before a new insert. | **Bounded around 200001 entries**, no idle clearing. String/tuple metadata can matter at capacity; resident bytes unmeasured. |
| `voice_match_service._voices_cache`, `:26–53` | One engine-keyed voice list. | Replaced after ten-minute validity or explicit reset. | **One list**, not one list per project. |
| `inworld_service._library`, `:786` | One fetched voice list. | Replaced/refreshed; persisted disk cache is separate. | **One list**; no per-job audio cache. |
| `assistant_service._today`, `:64–92` | Per-account daily message counters. | Replaced on the first activity of a new day. | **Daily scope**, no proactive midnight timer; can retain yesterday while idle. |
| `assistant_service._kb`, `:117`; `arabic_waqf._special_cache`, `:60` | One knowledge file / one pronunciation settings cache. | Replaced on file change. | **Structurally bounded**; not one entry per audio line. |
| `r2_backup._register_sent`, `:363–389` | Hash per configured register path. | Updated/replaced as file changes. | **Small fixed register set**, not per-user audio. |
| Monitor `_latest` / service snapshots, `railway_monitor.py:76`, `disk_guard.py:69`, `service_usage_monitor.py:81,100,121`, `fal_usage.py:17` | Latest monitoring response. | Replaced on poll/read. | **Fixed snapshots**; no ever-growing in-process monitoring history found. |

There is no separate persistent audio model behind notification delivery in the reviewed code. Notification/event pressure is represented by the queue above; current monitoring snapshots remain bounded. Thread-local meter stacks and worker-local arrays are not per-job module caches, but a still-running worker can retain them.

### Exact purge work to hand to Claude

1. Add a locked `purge_job_state(job_id)` in the protected voice service, removing all four voice/room dictionaries together **only after** no generation/remix/regeneration holds that job. Persist anything needed to reopen retained projects before eviction. Call it from definitive expiry/deletion paths; do not purge on the first export because further edits use anchors and room choices.
2. Give enhancement status an expiry tied to completion; preserve active progress and an agreed result-polling window. This lies outside this batch's endpoint-only `main.py` scope, so it is proposed, not patched indirectly.
3. Expire unused rate-limit keys only after their full policy window ends; use existing locks. Audit authentication/account caches separately with revocation/restore behavior preserved. Do not treat owner records or `_assist_owed` as disposable entries.
4. Bound inactive long-project JSON caching after durable saves. Eviction must exclude active workers/corrections and ensure callers cannot keep mutating evicted objects. Lock-map removal requires leases/reference counts or another design preventing two locks for the same key.
5. Give long-project usage buckets an explicit lifecycle after accounting has been saved/settled and the project is definitively retired. They are not registered in the short upload timestamp map; the short-job cleanup cannot collect them. Do not discard needed accounting totals during editing.

## 6. Part A.4 — model release findings

Three controlled offline reproductions passed against **extracted current functions**, without loading real weights or processing media. They establish control-flow defects, not a diagnosis of the captured live process. Reproduction code and JSON are in the evidence folder of the ZIP; they are investigative scripts, not regression tests that require the bugs to remain.

### 1. Early short-transcription error skips model release — proven

`whisper_service.transcribe_worker()`, lines 783–813 and 981–991: language checking can load the model; the subsequent `_get_model().transcribe(...)` call and `info.duration` access are **outside** the `try/finally` that calls `_release_model()`. An immediate decoding error jumps to the error handler and releases the queue slot, but never the model. The global loaded model can remain until a later successful path releases it.

Proof: mock `transcribe()` to raise immediately. Current worker reports an error and releases its queue once; `_release_model()` is called **zero** times. Protect the whole model-use scope, including language probing, the call and generator consumption, with a correctly coordinated release. Do not change transcription parameters or swallow new errors.

### 2. Two overlapping workers can both skip the final release — proven

`whisper_service._release_model()`, lines 357–368, checks `running() > 1` before releasing. The short worker calls it at line 813 but drops its queue slot only at 991. Long analysis calls it before `slot.drop()` at `longdub_service.py:1739–1742`; yielding uses the same order at 1334–1342.

With concurrency allowing two jobs, both workers can finish decoding and each see two running queue slots. Both skip release; both later drop their slots. Nothing then retries release, so the queue reaches zero while `_model` remains set. This does not occur from that interleaving when concurrency is strictly one.

Proof: run the actual release function twice while the mocked queue reports two; then simulate both slot drops. Queue count is zero, cached model still present, trim calls zero. Correct this with coordinated model-use ownership/last-user release. **Do not simply move the call after queue release**: releasing wakes another worker, which may acquire/use the model before the old worker clears the cache. This needs short and protected long callers reviewed together.

### 3. Timed-out speaker thread can repopulate the cache after cleanup — proven

Short worker lines 735–773 wait at most 300 seconds, then call `_release_diarization_pipeline()` even if the daemon thread is alive. `get_speaker_turns()` creates the pipeline before inserting it at lines 425–426. If creation finishes after the timeout pop, it inserts a new cache entry that no later cleanup in the parent removes. If inference was already running, popping the cache cannot free its local model reference. The worker can also overlap with transcription despite the intended sequential-memory design.

Proof: hold the mocked factory on an event, perform the real release while the cache is empty, then let the factory return and the worker exit through a controlled read failure. One pipeline entry remains. Ownership and cleanup should belong to the speaker worker's completion, with synchronized creation/use accounting. An isolated worker process with a real terminate/join path is a stronger future design if a hard timeout is required. Python timeout waiting alone cannot stop arbitrary native work.

### Other paths checked

- **Long ordinary exceptions:** speaker detection has release in `finally` at 1685–1689; transcription call and iteration both lie inside its `try/finally` at 1710–1742. This covers ordinary exceptions there, subject to the shared release/concurrency issue above.
- **Abandon/cancel:** the short abandon endpoint removes progress/files, but does not cancel/join the transcription worker. It is not a guaranteed model-release operation. Error cleanup must still work after abandon. The reviewed long worker releases its slots and `_RUNNING` marker in `finally`; UI terminal statuses alone do not prove a native task stopped.
- **Thread/process deaths:** normal Python unwinding runs applicable `finally` blocks. A live/hung native call can retain its stack and model. If the entire process is killed, the OS reclaims its memory; Python cleanup is irrelevant at that point. Thread creation can fail before worker cleanup begins; do not mistake stale activity markers for proof a model is loaded.
- **Separation:** `ffmpeg_utils.separate_vocals()` uses `subprocess.run` without a timeout. Normal success or child failure exits the child and releases its model. A hung child can keep memory/process slots occupied indefinitely. Timeout/cancellation needs a subprocess-tree lifecycle change in a protected file; no change made. Ali's one-process idle capture does not demonstrate this case.
- **Activity detector:** its cached sessions stay resident after transcription model release. Clearing that cache safely requires ensuring no concurrent user holds the sessions. Its measured initialization cost is documented above; an actual Linux clear/reload comparison is needed before promising recoverable memory.
- **Residual libraries/allocators:** existing `_trim_memory()` asks glibc to release eligible free heap pages; it cannot remove live references, unload imported Python modules or reclaim allocations owned by other processes. These Windows measurements do not test glibc fragmentation or how much another idle trim would recover.

## 7. Part B — ranked reductions and decisions

**No memory-reduction patch is implemented in this batch.** Diagnostics are implemented. Release fixes need coordinated ownership changes; the most obvious per-job caches are protected or have live editing/accounting dependencies. None met all the brief's conditions for an isolated change with no new failure mode. Savings marked unknown must not be sold as guaranteed reductions.

| Rank | Idea / status | Memory saving evidence | Risk and first job after idle |
|---|---|---|---|
| 1 | Repair the three model-release paths — **proposed to Claude** | Potentially the retained model/native buffers; actual recoverable MiB **unmeasured**. New loaded flags establish whether relevant. | Medium implementation/concurrency risk. Keeps intended existing unload/reload behavior, with no quality or price change. Reload cost applies when a previously leaked model is now correctly released; actual Linux cost unmeasured. |
| 2 | Purge inactive job state + bound project metadata — **proposed to Claude** | Actual MiB **unmeasured**, scales with observed counts/project size; avoids assuming tiny gain tables explain all anonymous RAM. | Low-to-medium with lifetime tests; high if live anchors/locks/ownership are deleted blindly. Metadata may reload on next edit. No model-quality change. |
| 3 | Measure then tune glibc arenas/trim thresholds — **Ali decision, proposed** | MiB **unmeasured**; affects fragmentation/free allocator pages, not library baseline or live weights. | Medium deployment/performance risk. Test one setting at a time in the same isolated Linux image, e.g. arena cap before any trim-threshold tuning. Compare idle, peak, runtime and failures; no promised default values. |
| 4 | Isolate heavy processing in child workers that exit — **future proposal** if warm baseline still too costly | Can release process-owned imports/native caches together; persistent-server saving **unmeasured**. Container active-job peak still includes children. | Higher architectural risk; IPC, checkpoints, cancellation and model startup must preserve behavior. A new worker pays import/model load costs. |
| 5 | Defer eager tensor/transcription imports — **Ali decision, proposed** | Import components measured here: torch +130.367 MiB and faster_whisper +17.539 MiB. These are **not guaranteed savings**: transitive imports must also be avoided. Benefit primarily before first use after process start; imports then remain. | Local component import costs 12.5771 s and 0.3444 s measured here; Linux first-use latency unknown. First inference must not acquire a different thread configuration. Protected and shared imports need coordinated work. |
| 6 | Defer payment / voice SDK imports — **Ali decision, proposed** | +42.426 MiB and +18.230 MiB import components measured here; actual avoided RSS needs a fresh comparison. Monitoring can still require the voice client. | Local imports 1.6636 s / 1.2000 s measured here, shifted to first eligible operation. Payment behavior cannot change. This job makes no money-code edits. |
| 7 | Manage activity-detector cache after last user — **proposed, lower priority** | +24.137 MiB initialization measured here includes runtime and sessions; cache clear will not necessarily recover it all. | Concurrency ownership required; local reload initialization 0.3932 s measured here, Linux unknown. No inference parameter change. |
| — | Make speaker/separation/transformer libraries lazy — **already lazy or in child; no new fix** | No additional eager saving demonstrated from adding another lazy wrapper. Speaker imports remain after first use. | Existing first-use behavior already applies. |
| — | Add periodic idle GC/trim — **deferred for lack of evidence** | Additional reclaimed MiB **unmeasured**; existing per-job GC/trim already present. | Do not introduce another timer or collect during active work based only on an activity timestamp. Model/reference bugs must be fixed first. |
| — | Flush more downloaded-file cache — **not prioritized** | Only 17.5 MiB file cache **read from Ali's capture**; it cannot explain the dominant anonymous portion. | Extra disk rereads can slow later jobs. |
| — | Smaller transcription model / altered compute — **not recommended in this job** | MiB saving unmeasured; explicit quality/speed trade-off required. | No quality reduction is assumed or implemented. |

### Decisions for Ali

1. May Claude pursue lazy tensor/transcription imports, accepting a delay on the first relevant request after server startup? The local import times above are evidence, not a production latency promise.
2. May Claude separately defer the payment/voice SDK imports, subject to preserving monitoring and payment behavior and measuring the actual avoided imports?
3. May Claude compare glibc settings in an isolated copy of the same Linux image before proposing a deployment setting? No Dockerfile setting was changed here.

No choice about reducing audio quality is requested: that is not part of the recommendation. Worker-process isolation is a future option after measuring whether the cheaper release fixes are enough.

## 8. Part C — new read-only diagnostic

`GET /api/admin/mem_diag` retains existing authentication and old response fields. Only authorized requests reach the new helper. The new `runtime` object contains:

- `models`: cached transcription/speaker/activity-detector loaded flags, activity cache entry count, running/waiting queue counts. Separation is accurately labeled a separate process and its loaded flag is **null**, because parent state cannot establish a child's resident weights.
- `container_entries`: counts for the audited job, account, rate, session, voice-list and project-event state. It returns **no keys, values, scripts, user IDs or tokens**. A missing module/container is null; an empty known container is zero.
- `progress_entries_by_kind`: counts for plain transcription IDs and the existing emotion/generation/lip-sync prefixes.
- `threads`: Python live count and Linux OS thread count, so native threads are not silently treated as Python threads.
- `python_objects`: GC-tracked object count and top five actual types by count. This is not all Python objects, not a byte measurement, and excludes native buffers/weights. One linear count pass; no recursive sizing, tracing, forced collection or model loading.
- `process_rollup_mib`: RSS/PSS/private/anonymous counters from Linux `/proc/self/smaps_rollup` when available. They describe the parent process, not every container process. Unsupported readings remain unavailable.

The helper imports only standard-library modules and inspects already loaded workers. It adds no thread/timer or cache purge. A failed inventory leaves the existing diagnostic usable and returns a neutral unavailable message without exposing an exception. The inventory itself took **142.369, 145.598 and 142.276 milliseconds, measured here**, with **606578 GC-tracked objects** in the warmed local probe. It temporarily holds a list of tracked references; memory overhead and production latency are unmeasured. The existing endpoint's disk-directory walk is unchanged.

Loaded flags describe **global caches**, not every reference on a worker stack. A false flag is not proof that no worker still owns a model. Queue/thread/process counts should be considered alongside them. The existing authentication checker may restore session state as before; the new inventory itself is read-only.

### The next Railway readings needed

In a quiet period after deployment, capture the admin diagnostic shortly after a clean restart, after the next ordinary completed job, and again after more than 20 minutes idle. Keep process version, dependency versions and active workers clear. Do not restart an active job merely for this audit. No extra paid test is required by this handoff.

Interpretation:

1. Loaded transcription/speaker flag true with queue zero after idle: investigate the proven release paths.
2. All cached large-model flags false, no active worker/child, but anonymous memory remains high: investigate warm imported libraries/native allocators and lingering local references. Counts cannot assign anonymous memory to a particular native library.
3. Container counts grow across completed/expired jobs: implement the relevant lifetime fix with preservation tests.
4. A child process is still visible: investigate its lifecycle; parent-only GC cannot free it.

Run the import probe in the same Linux image as well. Compare like-for-like metrics rather than subtracting the Windows baseline from Railway's cgroup usage. That is how to establish a defensible floor and savings target.

## 9. Tests and validation

New tests: **18 passed**. Seven measurement tests cover eager/deferred source order, credential/environment isolation, blocked sockets, disabled app thread starts, honest unavailable imports and current RSS. Eleven inventory/endpoint tests cover loaded/unknown/empty state, no loader calls, privacy and non-mutation, all progress prefixes, queue/cache counts, GC type counting without sizing/collection, Linux rollup conversion, authorization before reads, old fields and safe failure.

Full Python discovery: **559 tests run in 429.481 seconds; 558 passed; 1 error; 0 assertion failures; 0 skips**. The sole error is the permitted environmental history/hygiene check, `test_repository_hygiene.BackupRulesTests.test_no_currently_tracked_backend_file_matches_an_ignore_rule`: Git exits 128 because the sandbox cannot change to the repository root. No test was disabled, weakened or marked expected-failure. Existing audio and billing tests otherwise passed.

Full browser suite: **126 tests; 126 passed; 0 failed; 0 skipped; 0 cancelled**, duration 548.4737 ms. All `tests/test_*.js` files were included. Argument-driven `.mjs` SQL integration scripts are a separate suite, not browser tests; no SQL was changed.

Discovery used `python -m unittest discover -s tests -v` through an offline launch wrapper: bundled Python plus the existing `.venv` packages, temporary DATA_DIR/TEMP, local ffmpeg/ffprobe, config credentials hidden/blanked, and external URL calls rejected unless a test supplied its own mock. Initial execution stalled in Windows asyncio's local socket-pair creation; granting loopback-capable network permissions resolved that environment issue. External calls remained blocked by the wrapper. Earlier temp-directory permission failures were resolved by using writable scratch space, not by changing assertions. The wrapper and logs are included in the evidence folder.

Additional validation: all five changed Python files compile; `main.py` AST outside the endpoint matches the initial snapshot; retained lines keep exact endings; protected-file edits by this job: none. Three investigative release-path proofs reproduced the reported defects. Git diff/status/history checks remain unavailable in this agent sandbox, so review the supplied unified diff and run the normal repository checks in your terminal before committing.

Normal local rerun commands from `backend` (using your configured development environment):

```cmd
python -m unittest discover -s tests
python -c "import glob,subprocess,sys; sys.exit(subprocess.call(['node','--test',*glob.glob('tests/test_*.js')]))"
```

The browser command explicitly enumerates the `.js` files, matching the suite run here and avoiding shell wildcard differences.

## 10. Commands for Ali — no push

These include only this batch's six delivered files. `--only` limits the commit to those paths; it still includes all changes within `main.py`, so coordinate any later edits to that file before running it. Config/version, protected audio files and the unrelated backup logging changes are excluded.

```cmd
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"
git --no-pager add -- main.py memory_diagnostics.py tools/memory_baseline.py tests/test_memory_baseline.py tests/test_memory_diagnostics.py Lisan-AI-Job-9-Handoff.md
git --no-pager commit --only -m "Add offline memory audit and admin runtime inventory" -- main.py memory_diagnostics.py tools/memory_baseline.py tests/test_memory_baseline.py tests/test_memory_diagnostics.py Lisan-AI-Job-9-Handoff.md
```

No push command is part of Job 9. Claude handles the version when merging.
