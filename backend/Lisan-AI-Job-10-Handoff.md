# Lisan AI — Job 10 handoff to Ali and Claude

## Status and scope

The permitted Job 10 changes are implemented locally. Timing evidence is now conservative and speaker-aware, contradictory automatic slow tags are removed, and the new cached audio-listening core is ready for integration. **Automatic listening is not connected to the live dubbing workflow in this batch:** the brief reserves those processing files for Claude. Deploying these files alone activates the delivery safeguards, but does not activate the new listener.

No commit, push, version change, SQL change, billing change, UI change, or live AI request was made. `config.py` and all protected processing files are byte-identical to the snapshot taken before this job. Ali runs Git; Claude handles the version when merging.

### Files delivered

| File under `backend` | Change |
| --- | --- |
| `delivery.py` | Same-speaker measurements, stronger evidence thresholds, pause ambiguity guard, coherent speed-tag removal. |
| `emotion_listen.py` | New public `listen`, `merge`, and `summary` functions; bounded encoding, validation, usage recording, receipts and caching. |
| `tools/emotion_replay.py` | Offline transcript replay comparing the previous timing rules with the new ones. |
| `tests/test_delivery.py` | A slow-scene fixture now meets the five-word requirement; unknown pace now removes an unsupported rushed tag. All other assertions retained. |
| `tests/test_delivery_evidence.py` | Nine tests for speaker isolation, fallback, short/pause-heavy lines, timing bounds, coherence and unchanged caps. |
| `tests/test_emotion_listen.py` | Thirty-seven tests for merging, inputs, encoding, batching, context, malformed/partial answers, receipts, budgets, concurrency and logging. |
| `tests/test_emotion_replay.py` | One invented-scene replay test. |
| `tests/fixtures/emotion_scene.json` | Twenty-four invented English lines with two alternating speakers and word timestamps; no film dialogue. |
| `Lisan-AI-Job-10-Handoff.md` | This implementation and integration report. |

Existing retained lines keep their original line endings. New files use LF. The archive includes complete changed files, a unified diff, hashes and offline evidence. It contains no credentials, original video, or one-hour probe audio.

## Verification of Claude's six findings

1. **Agree — verified in source.** `gemini_service.translate_segments` explicitly demands exactly two tags, including a pacing/volume/manner tag. Its urgent example includes `rushed`. Requiring a second tag invites an unsupported delivery instruction. The prompt is protected and unchanged; a replacement is below.
2. **Agree, with a limit on what was measured.** The previous `delivery.line_rate` accepted three words and 0.8 seconds, retained gaps at or below 0.4 seconds, and could use the entire subtitle slot when word timing was insufficient. `moment_rate` mixed speakers. These are verified code behaviors. Their contribution to the particular film cannot be quantified without that job's actual rows and source audio.
3. **Agree — verified in source and covered by tests.** Previously `ground` removed fast tags for non-fast lines, but removed slow tags only for fast lines. It let `fearful, slowly` survive for normal, slow or unknown speech. It now requires slow evidence and rejects slow tags alongside urgent/high-arousal tags.
4. **Partly verified.** The slow/normal/fast/unknown caps are exactly **1.08 / 1.15 / 1.25 / 1.15**. `longdub_service` sends emotion-derived instructions to synthesis, then compares fitted tempo with the pace-specific cap when deciding whether to rephrase. This supports the proposed mechanism. The reported film counts of 87 lines, 9 parts and 22 trims/limit hits were supplied in the brief, not independently reproduced here. No cap or fitting code was changed.
5. **Agree as an engineering recommendation.** `_fit_line` and `_rephrase_to_fit` already handle timing. A speed instruction should be exceptional, independent evidence rather than a required text-derived emotion. The new merge function implements that policy. Better audible performance remains a listening-test question.
6. **Agree, with a qualification.** `ground_emotions` returns before logging when nothing changed; the current event records only a changed-tag count. `admin.html:exportLongDubLog` exports generic events and resource usage, so it can already carry a new `emotions` event. There is no systematic per-line listening/timing evidence in the existing flow. Claude must emit the new summary after every listening pass, including zero changes, and persist the full evidence separately if every line must remain inspectable.

The coarse proxy pace ranges quoted in the brief were not re-measured. They must not be presented as measurements of the actual 87-line job.

## What changed in timing and grounding

`delivery._line_stats` requires five timed, non-empty words and at least 1.5 seconds after removing internal gaps of **0.25 seconds or longer**. Missing word times no longer turn an artificially long subtitle slot into evidence of slow speech. Invalid or non-finite times yield unknown evidence. English syllables remain a heuristic; their formula and the numerical slow/fast thresholds are unchanged.

`line_rate` exposes the diagnostic voiced rate even when pauses make a line unsuitable for automatic pace classification. `pace` treats a line with at least 25% of its word span removed as large pauses as unknown. That 25% guard is a conservative policy, not a calibrated accuracy threshold.

`moment_rate` uses up to four preceding and four following lines from the same speaker, plus the current line. It needs at least three usable measurements. Only if that speaker lacks enough measurements does it fall back to the original local window across speakers; that fallback also needs three usable lines. Both the line and its surrounding median must be slow to label it slow, or fast to label it fast. Missing/short/pause-heavy evidence is unknown.

`ground` is total and does not infer a new emotion. It removes `rushed`/`very fast` unless pace is fast, and removes `slowly`/`drawn out` unless pace is slow and no urgent tag is present. Urgent tags include anxious, fearful, terrified, angry, shouting, yelling, screaming, commanding, pleading, excited, frustrated, appalled, surprised and rushed. Manual choices are still the caller's responsibility: the existing `_ground_rows` guard preserves `emotion_set`.

The caps remain unchanged. Reclassifying an unreliable slow measurement as unknown can change which existing cap applies; this is deliberate and covered by the timing policy. A genuinely slow but anxious speaker can still receive the slow fitting cap even though the slow *instruction* is removed. Whether that cap should also change is a separate fit-quality decision, not silently included here.

## Public listening contract

```python
evidence = emotion_listen.listen(
    job_id, rows, vocals_wav, api_key,
    scene_hint="", cache_dir=None, max_workers=1,
)
# {segment_id: {emotion, heard_speed, confidence, listened, reason}}

style = emotion_listen.merge(text_emotion, evidence.get(segment_id), measured_pace)
detail = emotion_listen.summary(merged_rows, evidence, fallback_ids)
```

`emotion` is one or two canonical non-pacing tags. `heard_speed` is slow/normal/fast/null. Confidence is high/medium/low. `listened` is true only for a validated answer. Missing ids mean no usable listening evidence. Returned reasons are controlled, neutral confidence/pace descriptions, at most 120 characters; raw service text and errors never enter these logs. `listen` never mutates the supplied rows.

Additional optional arguments support targeted edits and offline tests: `selected_ids`, `budget_seconds=240`, `request_timeout=45`, and injectable `call`, `cut`, `record`, `clock`. `selected_ids` limits paid work while retaining all rows for surrounding transcript context.

### Audio, context and requests

- Source: original separated vocals as **mono 16-bit PCM WAV**, never generated Arabic or the mixed soundtrack. Cuts add up to 0.15 seconds each side. Lines shorter than 0.3 seconds or longer than 30 seconds are skipped. Padding shrinks before any actual speech would be cut.
- Encoding: mono MP3 at 16 kHz and 32 kbps, through the public local `run_ffmpeg` helper. Audio is read only after its encoded size passes a 160 KB clip guard. The source is fingerprinted in 1 MiB chunks; no whole-hour waveform is loaded into Python.
- Batches: up to ten complete lines or 90 seconds of summed padded clip duration. Silent speaker turns are preferred as boundaries once a batch has five lines. Overlapping utterance groups stay together; a group too large for either cap is skipped instead of truncated.
- Context: the target's English text and speaker, three previous and three next text lines, and a bounded scene hint. Short neighbours remain in text context even if their own audio is skipped. Transcript fields are bounded to 2,000 characters and scene hint to 4,000 characters.
- Prompt: judge actual audible tone, loudness, urgency, breath, tremble and pace. Pauses, radio filtering and story context do not prove slowness or emotion. Overlap/unclear target voice should lower confidence. Neutral is valid; no second tag is required. No customer/admin/log wording names a provider or model.
- Validation: ignore unknown ids; reject duplicates and invalid schema/confidence/speed. Parse fenced or plain JSON and ignore thought text. Normalize exact canonical tags/synonyms through the public normalizer. Move any returned pacing tags into separate speed evidence; contradictory speed signals become unknown. Partial or malformed JSON retries only missing ids, once. Transport failures do not add another outer retry because the shared request helper already retries internally.
- Accounting: every invocation of the public request helper calls the public usage recorder afterwards, including `None` failures. Listening's own recordings are serialized to avoid races between its workers. Returned thinking-token usage remains included. A transport failure without a response has no reported token counts; this module cannot reconstruct upstream billing for a lost response.
- There is no credit debit in this module. The brief's policy is to absorb listening in the existing dubbing price. Claude must preserve that policy when wiring it, and finalize styles before the existing generation quote.

The official audio documentation lists MP3/MPEG as supported inputs: [audio input documentation](https://ai.google.dev/gemini-api/docs/audio). Local format compatibility was tested; successful live classification and confidence calibration were not tested in this job.

### Cache and duplicate-work protection

The cache is scoped by a hashed job id and a versioned hash of segment id, start/end, English text, speaker, audio size, full streamed audio fingerprint, neighbouring text and scene hint. This is stricter than size alone: replacing audio with different content of the same size invalidates it. Cache JSON contains the validated result and no API key, audio or transcript.

An exclusive pending receipt is written before a paid attempt. Successful results and final unjudged outcomes are persisted atomically. Concurrent invocations share the receipt. Repeating unchanged input does not pay again, including after malformed responses or a terminal failed attempt. A pending/interrupted or damaged receipt falls back instead of silently re-paying; an explicit future retry/reset control would be separate work. Free cuts that exhaust the budget do not leave paid receipts. Cache write failure prevents new requests and allows the dub to fall back.

Default scheduling is sequential; optional parallelism is capped at four, with no whole-job request queue. **Ali approved stopping new work after four minutes while finishing an in-flight request.** A partial-answer retry is also new work and does not start after the budget. The shared helper's timeout applies to individual network attempts and its internal retries/fallbacks can exceed it. This is not a hard four-minute completion deadline, and this batch does not add cancellation or change the protected helper. The local FFmpeg helper likewise has no total timeout; failed cuts are caught and skipped.

### Merge policy

1. High/medium confidence: use the listener's one or two non-pacing emotion tags.
2. Low confidence or missing/failed listening: keep the text-derived emotion, removing **all** pacing tags: slowly, drawn out, rushed, very fast, hesitant and stammering. If nothing remains, use neutral.
3. A speed instruction is allowed only with **high** confidence, heard slow/fast matching the independently measured pace, and the coherence rule passing. Medium confidence never adds speed. Hesitant/stammering are never automatically retained by this policy.
4. Callers skip `emotion_set` lines throughout translation, listening and application. They must not pass those choices through automatic merge or grounding.

`summary` is at most 600 characters and includes listened/fallback/skipped counts, per-tag counts, the total speed-tag line count, and speed ids/reasons. “Listened” counts available validated evidence, including cache hits, rather than requests made during this pass. Overflow uses explicit “…and N more” wording. Pass it rows **after merging** and an iterable of fallback ids; manual or deliberately ineligible lines can be left out of fallback ids to count as skipped. It is useful even when no style changed.

## Offline measurements from this job

These are local measurements, not estimates of the film or live service accuracy.

| Probe | Before | After / result |
| --- | --- | --- |
| Invented 24-line timing replay: slow tags | 12 | 6 |
| Its six anxious, pause-heavy second-half lines: slow tags | 6 | 0; pace becomes unknown, emotion stays anxious |
| Twelve fast-speaker lines | Generated at about 6.5 syllables/s | All classified fast by the new same-speaker rule |
| Six genuinely slow, thoughtful first-half lines | About 3.2 syllables/s | Keep slow evidence; the listening merge still requires corroboration before adding speed |
| 6 s encoded local synthetic audio | — | 24,336 bytes; 32,448 bytes after base64 |
| 30 s encoded local synthetic audio | — | 120,384 bytes; 160,512 bytes after base64 |
| 60 s encoded local synthetic audio | — | 240,336 bytes; 320,448 bytes after base64 |
| One-hour 44.1 kHz mono PCM source | 317,520,044 bytes on disk | 720 rows returned; 144 mocked requests and 144 recordings; peak traced Python allocations 2,931,199 bytes (about 2.8 MiB) |

The one-hour test used the real streaming hash and batching/cache logic, but mocked MP3 cutting and network calls. Its allocation figure excludes pre-existing rows/imports, native libraries, FFmpeg processes and server RSS. It is evidence against whole-source buffering, not proof of Railway's total RAM usage. The 60-second tone probe measures the actual encoder output, including MP3 packet overhead (about 32.045 kbps), not human speech quality. Base64 adds transport bytes; compressed byte size does **not** determine API token billing. No cost or emotion-accuracy benchmark is claimed.

Replay yourself, free and offline, from `backend`:

```cmd
python tools\emotion_replay.py tests\fixtures\emotion_scene.json
python tools\emotion_replay.py path\to\transcript.json --json
```

The input is a JSON row list, or an object containing `rows`. The report prints old/new line rate, mixed/new moment rate, pace and grounded tags. It does not listen or call an AI service. Its copied old rules are explicitly labelled baseline code for comparison, not a second production classifier.

## Protected translation prompt — exact proposed replacement for Claude

In `gemini_service.translate_segments`, replace the paragraph beginning “Detect the emotion AND speaking style…” through the paragraph beginning “Some segments carry…” with the following **inside its existing f-string**:

```python
Detect the primary emotion of each line. Return ONE primary emotion tag, with at most ONE optional non-pacing delivery tag only when clearly supported. A single tag is valid; use "neutral" when unclear. Never invent a second tag. Choose tags ONLY from this exact list:
{', '.join(t for t in CANONICAL_EMOTIONS if t not in ('slowly', 'drawn out', 'rushed', 'hesitant', 'stammering'))}
Example: a sad line may be "sad"; when quiet delivery is clearly supported it may be "sad, softly". An urgent, angry line may be "angry". Do not return speed instructions: slowly, drawn out, rushed, very fast, hesitant or stammering. Audio listening and independent timing evidence decide speed separately.
Some segments carry "measured_pace" for timing and fitting context only. It is not an emotion label or a confidence score, and must not override the meaning or force a delivery tag. Pauses or short subtitle lines do not prove slow speech.
```

Also change the example response's emotion from `"neutral, conversational"` to `"neutral"` so it no longer suggests a mandatory pair. This is a proposal only; `gemini_service.py` was not edited. Keep the surrounding translation, tashkeel, glossary and response-shape instructions intact.

### Existing normalizer: five demonstrated substring traps

Actual calls to the current public normalizer produced:

| Input | Current output | Why unsuitable as evidence |
| --- | --- | --- |
| `not slowly` | `slowly` | Negation reversed. |
| `no rushed delivery` | `rushed` | Negation reversed. |
| `not drawn out` | `drawn out` | Negation reversed. |
| `without stammering` | `stammering` | Absence becomes a tag. |
| `no hesitant delivery` | `hesitant` | Absence becomes a tag. |

Additional measured examples: `unrushed` → rushed, `quick-witted` → rushed, and `slowdown` → depressed (not slowly). Exact `calm` maps to **confident**, while exact `deliberate` maps to **neutral**. Do not claim either maps to slowly. Ordering of substring matches matters.

The new core admits exact canonical tags or exact declared synonyms before calling the public normalizer, so these substring traps are rejected there. Claude can separately tighten the protected normalizer to exact tokens/explicit phrases, preserving deliberate existing synonyms and testing other consumers. No global normalization behavior was silently changed here.

## Exact integration locations for Claude

Use function names and nearby statements as anchors; source line numbers can move.

1. **Initial analysis / `_translate_all(job, rows)`:** `_run_analysis` currently calls `_translate_all` and writes segments, then creates `wd / "vocals_mono.wav"` in its “8. done” cleanup stage from `vocals_all`. Move/reuse that existing mono conversion before the new listener needs it. Do not pass a nonexistent mono file or substitute the original music mix. Run listening **once after all translation batches**, not once per translation batch or once per individual row. Cache directory can be `wd / "emotion_listen_cache"`.
2. **Manual choices in `_translate_all`:** currently `r["arabic_text"], r["emotion"] = got[...]` is unconditional. Preserve the Arabic assignment, but guard the automatic emotion assignment with `not r.get("emotion_set")`; otherwise the manual choice can be lost before the listener even sees it.
3. **Apply and persist:** compute paces using `_row_paces(rows)`, skip manual rows, and call `merge` for every remaining row, including those without listening evidence. Persist the final rows through the existing writer before quote/TTS. A later `_ground_rows` pass may remain a local safeguard, but it must not restore text-derived speed guesses.
4. **`ground_emotions(job)`:** preserve its “before price is fixed” role. Do not turn a routine quote refresh into uncached paid listening. Reuse the analysis results; refresh only changed eligible inputs when required. Emit an `emotions` summary independently of the current `if not n: return 0` path so zero-change runs remain visible. Use the existing `_ev` hook and neutral detail text; do not add an admin column or vendor name.
5. **`retranslate_line(job, segment_id)`:** after `_translate_batch` gives the new text-derived emotion, listen/merge only the selected automatic line using `selected_ids={segment_id}` and the full row list for context. If English, timing, speaker, context and audio are unchanged, cached evidence is reused; an Arabic-only change does not invalidate listening. Use the same fallback when source audio is absent.
6. **`split_line(job, segment_id, position)`:** retain both `emotion_set` guards. After both translations, prepare the full row list containing the two new intervals and call with their two ids selected. Changed intervals/ids invalidate only the relevant evidence and context keys. Do not cut utterances further to satisfy request limits.
7. **Concurrent edits:** perform network work outside `_lock_for`. Before applying under the lock, re-read rows and verify id, English text, start/end, speaker and manual-choice state still match the snapshot. Preserve newly added/deleted/edited rows instead of overwriting the entire project with a stale list. Discard stale evidence for application; its usage receipt remains recorded.
8. **Scene hint source:** the current analysis/translation path has no persisted ready-made scene hint; `scene_context.scenes_for_job` is called later in `_run_dubbing`. For this integration use `scene_hint=""` or a bounded local summary of the original English transcript (for example a few excerpts across its beginning/middle/end). Do not insert a new paid scene-summary call or use the later background-scene generation as if it already existed during analysis. Each line already has local context.
9. **Audio lifetime / completed edits:** `_run_dubbing` cleanup deletes `vocals_mono.wav`. Persist validated listening evidence and its input identity alongside project rows before that cleanup. Do not keep full audio solely for emotion detection. For completed-project text/timing edits needing new evidence, use restored original isolated vocals if available; otherwise merge the text fallback without speed. Never substitute generated speech as evidence of the original performance. The module's file cache cannot validate audio that no longer exists; caller-side persisted evidence must retain its identity and only be reused for unchanged inputs.
10. **Log/export:** after application, always call `_ev(job, "emotions", "ok" or "partial", summary(...))`, including zero changes and all-fallback results. `_ev` persists up to 1,500 characters, so the 600-character summary fits; console output currently truncates at 400. Existing `exportLongDubLog` includes event details. Keep any complete per-line audit JSON in project data, rather than trying to fit every id into one log message.

Suggested application logic (illustrative wiring, not installed in a protected file):

```python
heard = emotion_listen.listen(job["id"], rows, wd / "vocals_mono.wav", GEMINI_API_KEY,
                             scene_hint="", cache_dir=wd / "emotion_listen_cache")
paces = _row_paces(rows)
for r in rows:
    if not r.get("emotion_set"):
        sid = r["segment_id"]
        r["emotion"] = emotion_listen.merge(r.get("emotion"), heard.get(sid), paces.get(sid, "unknown"))
fallbacks = {r["segment_id"] for r in rows if not r.get("emotion_set")
             and r["segment_id"] not in heard and 0.3 <= float(r["end"]) - float(r["start"]) <= 30}
_ev(job, "emotions", "partial" if fallbacks else "ok", emotion_listen.summary(rows, heard, fallbacks))
```

Use existing validated rows and lock/recheck discipline around this sketch. If hearing fails completely, merge must still run on automatic rows. Do not leave the old text-derived pacing tags merely because `heard` is empty.

## Validation and remaining limits

- **Final focused checks:** 38 listener/replay tests and 25 delivery tests, including the 16 existing delivery tests: **63 passed**. Forty-seven of these tests are new. Real local MP3 encoding is checked without an AI call.
- **Full requested Python discovery:** **618 tests run: 617 passed, one environment error; zero assertion failures** (424.895 seconds). The only error is `test_repository_hygiene.BackupRulesTests.test_no_currently_tracked_backend_file_matches_an_ignore_rule`: its read-only Git subprocess exits 128 because this Codex sandbox cannot access the repository root. It was not skipped or changed. Re-run from Ali's ordinary terminal; this result is not an all-green suite claim.
- The historical pricing and sound-level failures named in older jobs did not fail in this run. No unrelated tests or protected audio files were changed to obtain these results.
- External calls were blocked, credentials and environment files were excluded, and all inference was mocked. Cache, cost accounting calls, budgets, returned data and concurrency behavior are verified offline. Emotion accuracy, speaker identification in overlap, Arabic performance quality and confidence calibration still require representative listening tests after Claude's integration.
- No “100% accurate” or competitor-quality claim is justified. The safe failure behavior is deliberately neutral/non-pacing, not fabricated certainty.
- Claude should add integration tests for initial analysis, quote refresh, retranslation, split, manual-choice preservation, a concurrent edit during listening, zero-change log export, and source cleanup/recovery. The protected-file wiring is the remaining integration step; this batch does not pretend that it is live.

## Exact commands for Ali — Windows Command Prompt

Run after Claude merges the protected-file wiring/version as appropriate and after reviewing the changed-file list. These commands stage/commit **only this batch**, leaving other developers' changes out. They are supplied for Ali, not executed by ChatGPT.

```cmd
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"
python -m unittest discover -s tests
git --no-pager diff --check -- delivery.py emotion_listen.py tools/emotion_replay.py tests/test_delivery.py tests/test_delivery_evidence.py tests/test_emotion_listen.py tests/test_emotion_replay.py tests/fixtures/emotion_scene.json Lisan-AI-Job-10-Handoff.md
git --no-pager add -- delivery.py emotion_listen.py tools/emotion_replay.py tests/test_delivery.py tests/test_delivery_evidence.py tests/test_emotion_listen.py tests/test_emotion_replay.py tests/fixtures/emotion_scene.json Lisan-AI-Job-10-Handoff.md
git --no-pager commit --only -m "Ground delivery tags and add cached emotion listening core" -- delivery.py emotion_listen.py tools/emotion_replay.py tests/test_delivery.py tests/test_delivery_evidence.py tests/test_emotion_listen.py tests/test_emotion_replay.py tests/fixtures/emotion_scene.json Lisan-AI-Job-10-Handoff.md
git --no-pager push origin main
```

If tests show a real failure, stop before committing and send its final error block. No command above includes `config.py`. Claude's version change is separate from this batch.
