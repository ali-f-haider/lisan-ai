# Integration status (2026-10-11)

The plan below is retained as the original foundation handoff. The engine, pages and API have now been wired in the local project; see ../../Lisan-AI-Job-16-Integration-Handoff.md for the applied changes and verification limits. The old proposal patch is superseded by the current files; do not apply it again. Nothing has been committed, pushed or deployed by this integration.

# Claude integration plan — not applied

The new modules are pure and not imported by production. This file specifies the protected-file changes; none has been made. Apply the API contract proposal **with** the engine forwarding, never alone: accepting a Spanish job while still forcing English is a wrong-result bug. Preserve CRLF/LF in every existing file. All line references in inventory.md are **read from the files** and may move during Claude's merge.

## E05 — job ownership, persistence and lifecycle

Use one canonical `source_language` on the job. UI language remains the existing en/ar locale. Internal normalisation is `languages.get(value).code`; missing, empty, unknown or malformed legacy values fall back to en. At a NEW external API/form boundary, reject explicit unsupported values rather than silently treating them as English. Normalise case at web/form boundaries; the documented public enum stays lower-case and rejects `ES` unless the contract is explicitly extended. Old API calls without the field remain valid.

In `main.LongDubInit`, add `source_language: str = "en"`; validate against supported settings. Change `longdub_service.init_upload(..., lip_res=None)` to `init_upload(..., lip_res=None, source_language="en")`. In its job literal insert `"source_language": languages.get(source_language).code`. At the route call append `source_language=body.source_language`. After `_save(job)` add `_ev(job, "language_set", "ok", job["source_language"])`: code only, no transcript, filename, audio, names or customer content in this new event. Do not change existing pricing calls or logs as part of this job.

`longdub_service.public_view`: leave all old keys; explicitly add `v["source_language"] = languages.get(job.get("source_language")).code` after its existing comprehension. Do not mutate an old job just by reading it. Add source_language to any required public field allow-list or return this explicit field once; not both.

`redo_project` builds a new dictionary instead of copying all keys. Add `"source_language": languages.get(job.get("source_language")).code` to the `new` literal. In `longdub_edits.start`, add `"source_language": languages.get(parent.get("source_language")).code` to the correction job. Any missing field in an old correction resolves through its already-owned parent before default en. `restore_init`, `finish_restore`, parking and restart retain the existing source; reattached media must not change it from the current page selection. Project copy uses the source job, not localStorage.

Short dub has progress in `app_state.jobs_progress` and a downloadable project JSON; it does **not** have the long job.json record. Add source_language to the `/api/transcribe` Form arguments with default en, normalise/validate before starting work, keep it in the progress response, and forward it in worker kwargs. For restart/reattach, Claude must add a small owner-checked JSON sidecar in DATA_DIR (one validated UUID per file, atomic replacement, included in cleanup/backup) rather than inventing a database column. Persist only the language code and format version. Do not trust a later translation request's language over a known job. An old project/sidecar missing it resolves to en. If no sidecar exists for an imported text-only project, establish its source from the validated imported project before translation and keep it thereafter. This new sidecar's cleanup/backup wiring is still required, not supplied by the pure modules.

In `app.js saveProject`, add `source_language: currentSourceLanguage` alongside `job_id`. In `loadProjectFile`, set it only after successful validation, using `data.source_language || "en"` and the supported-code validator. When initialising an upload, append `formData.append("source_language", currentSourceLanguage)`. On progress and project status reloads restore the server's source. Long upload/init includes the same field in its JSON body. UI source selection is independent of currentLang/LANG/lang, interface cookies and tutorial language. Persist as per-job state, not a global setting that changes open projects.

## E01 — recogniser and timed rows

Exact signature fragment in `whisper_service.py`:

```diff
-def transcribe_worker(job_id: str, input_path: str, hf_token: str, speaker_count, lipsync_wanted: bool = False):
+def transcribe_worker(job_id: str, input_path: str, hf_token: str, speaker_count, lipsync_wanted: bool = False, source_language="en"):
```

Inside this worker normalise once: `source_language = languages.get(source_language).code`. Exact argument fragment at the short recogniser call and at `longdub_service._run_analysis`'s per-piece call:

```diff
-                    language="en",
+                    language=languages.get(source_language).whisper_code,
```

For the long worker, `source_language = languages.get(job.get("source_language")).code` comes from the job before decoding. Keep beam size, VAD, word timestamps, batch/piece limits, model release and diarisation order unchanged. Do not run another recogniser or separation pass just to guess language. A unit fake must inspect **both** recogniser call arguments for all supported sources and missing/en.

The short final row expression `merge_mid_sentence_rows(smooth_speaker_islands(result))` and long `rows_from_raw` expression `ws.merge_mid_sentence_rows(ws.smooth_speaker_islands(split_rows_by_sentence(result)))` must have **explicit en branches containing the current expressions verbatim**. New-source branches forward the source to each lexical splitter. The long sentence splitter has an extended abbreviation list and the short smoother uses another ending regex; neither is equivalent to either approved profile. Keep those English branches intact rather than globally installing `lang_text.split_sentences`.

For a non-English timed splitter, decide cuts from actual word timestamps and `lang_text.ends_sentence(accumulated_surface, source_language, profile="tidy")` so multi-word abbreviations such as z. B. are considered together. Preserve total duration, speaker ids, no word loss, pause evidence and overlap handling. The new string splitter is lossless surface spans, **not** timed rows. Do not generate timings by counting its strings.

## E02 — language check, mismatch and switch

`judge_chunk` currently returns `{lang, prob, en, speech}`. Retain these exact values for the English branch. Add an expected-code argument to `judge_file`, `is_flagged`, `summarize`, and call sites; use detected code versus expected code for non-English. Preserve English thresholds and message bytes in the old summary path. `looks_like` supplies accent-aware script/function-word **evidence**, not a definitive choice among Latin languages. Keep `lang_check.text_check`'s current en branch; new-source text checks can attach advisory evidence, never a fictitious confidence percentage.

Ali's revised rule requires a user-visible gate for a confidently detected source mismatch before using a suspect transcript for translation. Proposed whole-video gate: use existing confident audio stretches (probability at least 0.75, **read from files**) with adequate speech; recommend a switch only if one supported different language dominates judged speech. A dominance cutoff of 60% is an **estimated safety proposal**, not a measured detector accuracy. A minority/mixed region gives C06 review text and affected times, not a whole-video relabel. Unsupported detected languages show the supported choices and retain the upload. Inconclusive detection shows C06 “could not confirm” and requires review; do not silently call a Latin-looking transcript verified English.

On mismatch, keep the original upload/separation/checkpoints, show C06 and `Switch to {detected_language}`. This is an explicit user action; never silently switch. Implement the route with existing ownership, operation locks, revision and state checks. It may change source only before translation/dubbing; keep existing paid-operation identities and credit code. Invalidate stale source-derived ASR, subtitle matches, translation, gender/people and emotion/context checkpoints before re-running with the new code. Do not re-use English ASR output with a Spanish label. Any new paid quote remains subject to existing approval/ceiling controls; the local language/label check itself costs no credits. Completed/dubbing projects keep their source; a changed-source re-analysis needs a new/redo workflow, not correction-line dubbing.

This gate is an approved **new mismatch interaction**; it is not a claim of byte-identical English error/warning UI when a mismatched non-English file was previously treated as English. Correct English decoding/prompt output remains unchanged. Calibrate detector dominance/mixed cases on labelled audio before claiming reliability.

## E03 — tidy and planner text

For `line_tidy._ends_sentence`, prepend this conditional while keeping its old body:

```python
if languages.get(source_language).code != "en":
    return lang_text.ends_sentence(text, source_language, profile="tidy")
# Existing English body remains verbatim.
```

Add `source_language="en"` as an optional final/keyword argument to `_ends_sentence`, `merge_split_phrases`, `_tail_start`, `move_unfinished_beginnings`, `flag_doubtful_cuts`, and `tidy_lines`; forward it through all internal calls and long analysis/planner calls. Use `starts_sentence` only in the non-English case-test branch. Do not change MAX_TAIL_WORDS, voice-onset/cut detection, adjacency or speaker rules. Keep whitespace tokens when moving original words; matching elisions are not permission to split the original word or change its audio timing. `_tokens` must not be changed globally to matching forms.

`line_planner.plan_lines(..., source_language="en")` forwards it to the builder and final tidy call. Its original word-equality/invariant checks remain unchanged; operations never rewrite source words. Old English build_prompt becomes `language_prompts.line_planner_prompt(window, before, after, source_language)`. The new module's exact English string is tested against the current function, including context and suspect-cut markers.

## E04 — subtitles

Add source_language to `check_file`, `parse_subtitles`/`_cue`, `_clean_line`, `build_stream`, `_row_tokens`, `correct_rows` and call sites (`main.subtitle_import`, `longdub_service.attach_subtitle`, `_apply_subtitle`). Keep their existing default/en bodies. New-source cleanup uses `lang_text.clean_subtitle_line`; preserve unknown parenthetical dialogue, remove recognised descriptions/music, and consider explicit speaker-label changes plus leading dashes. A quote alone does not imply a speaker change.

For new-source matching, use `lang_text.tokenize` word tokens on **both** transcript and subtitle. The stream `norm` uses `token.matching`; use character offsets into the cleaned ORIGINAL text to reconstruct output, not `' '.join(matching)`. Keep stream unit, cue and timestamp ownership, original separators, and all current alignment bounds. Elisions can create more matching tokens; distribute their original spans without duplicate/missing apostrophes and map them to the parent timed word. Use per-language dialogue fixtures, full subtitle import roundtrips and locked/missed/two-speaker tests. Accent folding must never alter displayed/saved source text.

Exact helper choice for end checks:

```python
lang_text.ends_sentence(token, source_language, profile="subtitle")
```

Use it in the **subtitle** call sites; the approved `tidy` profile belongs only to line cleanup/planning. Keep `legacy_surface_tokens` and original row tokenisation on en; they are intentionally different around em dashes. Do not assume `tokenize(en)` objects are the old `_surface_tokens` list.

Reject a clearly detected subtitle in another language with C03, preserving current rows. Accent-aware Latin/Arabic shares fix script counting but cannot certify Spanish versus Portuguese, especially short subtitles. Require sufficient same-language lexical alignment/audio expectation; when evidence is inconclusive ask the user to check the subtitle, without claiming certainty. Implement the same rule for file attach and inline import; changing only the front end leaves a bypass. Match longer subtitle files to the uploaded slice as before.

Subtitle download selectors keep `ar` for target and expose the **stored source code** plus a bilingual choice. `main.longdub_subtitles` and export helpers must accept the source code, using source rows, while retaining `en` as the old English-job behaviour. New-source filenames include their source code; old English filenames remain byte-identical. Short export already prefers Arabic and falls back to original; preserve that behaviour unless Ali separately requests a new export option. Old glossary `{en, ar}` fields and source text `english_text` stay as compatibility keys.

## P01–P06 — prompt adapters

Add an optional `source_language="en"` to each wrapper/caller. For stored jobs it is resolved from the job, not user content. Keep request generation options, audio/frame attachments, response formats, retries, usage accounting and shortening/fitting safety checks unchanged.

| ID | Current location (read from files) | Exact builder / replacement |
|---|---|---|
| P01 | gemini_service.py:206 translate_segments; prompt currently at 246 | Replace only prompt construction with `language_prompts.translation_prompt(segments, source_language, glossary=glossary, paces=paces, gender=gender, policy=language_policy)`; en builder keeps every current key/space, source texts are never string-replaced. |
| P01 | gemini_service.py:191 GENDER_RULES | `language_prompts.gender_rules(speakers, source_language)`; translation builder already includes it, do not append it twice. |
| P02 | emotion_listen.py:150 _payload | Replace only local prompt expression with `language_prompts.emotion_prompt(scene_hint, source_language)`; attach the same clips/context as today. |
| P03 | scene_context.py:164 _prompt | Replace prompt text with `language_prompts.scene_prompt(duration, has_frames, source_language)`; transcript/frames untouched. |
| P04 | speaker_vote_live.py:54 prompt_for | `language_prompts.speaker_prompt(kind, rows, source_language)` at payload construction; no voter logic, memory, weights or speaker ids change. |
| P05 | line_planner.py:67 build_prompt | `language_prompts.line_planner_prompt(window, before, after, source_language)`; no change to operation validation. |
| P06 | gender_context.py:180 PEOPLE_PROMPT / 233 concatenation | `language_prompts.people_rules(source_language)` in place of PEOPLE_PROMPT; concatenate the existing speaker/transcript data verbatim. |
| P06 | gender_context.py:128/144 speaker_table/build_context | `language_prompts.gender_context(rows_all, ids, genders, source_language, roles=roles)` returns the existing combined shape; new-source context drops English-only addressee regex evidence, en includes roles and sure/probable addressee hints exactly. |

Long calls to `_translate_batch` need `source_language` in that function's optional args and all retries; `_translate_all`, retranslate_line, speaker/gender retranslation and shortening must get it from the parent job. Short `main.translate` passes it at its existing translator call, and main's `_short_gender_args` uses the matching context/people rules. New-language title/name cues are not supplied as deterministic proof: use source context and native-approved evidence rules before asserting certainty. Keep audio-derived gender and user choices unchanged.

For shorten_arabic_line, preserve the original English prompt branch verbatim; in the new-source branch change its static original-language heading to `languages.names(source_language)`, retain the source text unchanged, and preserve Arabic meaning/length/number checks. Do not copy the old English “20% longer” heuristic into new-source requests. Descriptive source syllable rates are not target Arabic word budgets.

## E06 — clone sample versus target speech

The long clone sample is currently labelled with `CLONE_SAMPLE_LANGUAGE` (read from files); target synthesize stays `language="ar"`. Exact signature fragment:

```diff
-def _clone_with_retry(name, wav):
+def _clone_with_retry(name, wav, source_language="en"):
```

Exact local selection:

```diff
-    lang = inworld_service.CLONE_SAMPLE_LANGUAGE
+    code = languages.get(source_language).code
+    lang = inworld_service.CLONE_SAMPLE_LANGUAGE if code == "en" else languages.get(code).whisper_code
```

Forward the job source at initial long clone calls and **parent** source at `longdub_edits.run`'s recreation call. The old en environment override and retry fallback remain intact. Check the clone endpoint's supported language labels in staging; a recogniser code's presence does not prove clone-label support. Short library speech remains Arabic. For any short custom/clone sample path in `inworld_service.clone_voices`, add the same optional source argument and forward it to clone_voice_from_file; keep the en CLONE_SAMPLE_LANGUAGE behaviour. Do not change target voices, clone counts, price or permission logic.

## E07 — source pace and short timeline estimates

`delivery.py` counts English vowel groups; app.js estimates timeline widths from English character rates. These are language-specific approximations. Keep the en calculation exactly. New sources must use validated acoustic/time evidence where available and mark a text-only timeline width approximate; never report an English vowel-count estimate as measured German/French syllables. Until native audio calibration exists, use the registry's conservative timing budget only for translation concision; preserve user timings and fitting rules. C04 timeline/fit copy describes the **original line** without making a rate accuracy claim.

## A01 — API contract proposal

See api-contract.patch, an unapplied unified diff for api_schema.py and docs/api/openapi.yaml. It adds optional `source_language` with lower-case enum en/es/fr/pt/it/de and default en in CreateJob, and a source_language Job response property. Apply it only with these forwarding fragments:

`api_dub.create_job`: append `"source_language": body.get("source_language", "en")` to the dictionary passed to ld.create. `main._ApiLongDub.create`: append `source_language=params.get("source_language", "en")` to LongDubInit. `api_jobs.job_view`: include the canonical stored source in the allow-list response; missing=en. Existing API paths, scopes, idempotency fingerprints, upload limits, pricing and key/owner checks stay intact. Do not weaken the old explicit unknown-field rejection beyond the new field.

Public docs: optional language field, same-language subtitles and always-Arabic target; use A01 copy. Calls without the field remain exactly the same. Old idempotency bodies must not have an injected default before hashing, or old retries can conflict. New explicit source fields are included in the original request fingerprint. Test replay omission, each enum, invalid codes, propagation into job and recogniser, source in list/status, and unchanged quote/credit amounts. Current public API supports long creation; do not advertise short API merely because the schema mentions short kind.

## Customer text patches and invariants

For every UI/help/legal location in inventory.md, apply the linked copy ID to the source-language claim only. Selector names use registry names in the interface locale. `English`/`الإنجليزية` editor headings become a selected-source label only for non-en; keep en wording and key identifiers. Subtitle labels/notices use C03, timing/row actions C04, exports/glossary C05, mismatch C06, marketing C07, help/assistant/FAQ/admin C08 and service descriptions C09. Do not relabel a known English demo or English-language interface tutorial. Old page regeneration scripts must receive the same changes if retained, but must not be run over current pages.

## Safety matrix and migration evidence

| Claim / case | Status | What is established / still required |
|---|---|---|
| New text and prompt modules have no network/audio imports | proven by added tests | Pure calls only; live trial's transport is lazy and never reached in dry/tests. |
| Existing English ending, cleaning, normalisation and prompt strings are unchanged | proven for fixture/comparison inputs | Pinned profiles + current-source comparisons; full live workflow not rewired here. |
| New token roundtrip/offsets preserve originals | proven on fixture/fuzz cases | Does not prove Claude's future subtitle stream mapping; integration tests required. |
| Correct English whole workflow remains byte-identical after integration | documented requirement, not yet proven | Run both old and wired workflow on identical saved raw inputs; compare rows, translated request bytes, subtitles and filenames. External generated audio is not deterministic enough for byte equality claims. |
| Missing/unknown/mixed-case internal code | proven registry behaviour | Missing/unknown=en; recognised case/space normalised. Explicit invalid new API/form choices must be rejected by adapter. |
| Old long resume/restart | likely until wiring tests | Source resolves en, existing checkpoints unchanged. |
| Long redo/project copy | documented required patch | New dictionary must copy canonical source; omission creates wrong-language retranslation. |
| Corrections/recloning/reattach | documented required patch | Parent source, retained voices, copied metadata; selector must not reset source. |
| Old short JSON load/save | documented required patch | Missing=en; preserve original text; sidecar ownership/restart/cleanup tests required. |
| No SQL required for long jobs | proven storage shape by source read | _save serialises the whole dictionary and load_job reads job.json; new field fits without a table column. |
| No SQL for short source metadata | documented design | New atomic sidecar plus downloadable JSON; cleanup/backup still to integrate. Existing owner/billing tables unchanged. |
| No SQL for new language event | proven log signature by source read | _ev accepts arbitrary step/detail and Hooks.log_event stores existing event fields. |
| Policy persistence and future variants need no SQL | likely, not claimed deployed | Check existing settings schema before persisting flags; no schema was modified in this delivery. |
| New-language translation quality / speaking rate | unmeasured / estimated | Dry trials only. Native source/Arabic review and audio trials still required; no ranking claimed. |

Required integration cases: each source through short/long upload, transcription args, rows/two-speaker subtitle, translation, original clone label, target ar, download; missing/en parity; uppercase/unknown boundary handling; UI locale independent; JSON save/load; restart/redo/copy/reattach/correction; mismatch switch/mixed/inconclusive/unsupported speech; subtitle wrong language and short inconclusive cues; quote/billing unchanged; source-only logs with no content. Do not silently enable a language on a page before all its consumers accept and retain it.

## Dutch and Polish — Job17 proposals, UNAPPLIED

Use this section rather than the historical Job16 wiring above: the current engine already forwards source_language generically. These patches were made against the **current** files after detecting concurrent work, not against old Job16 copies. `job17-wiring/manifest.json` includes current hashes and literal old→new replacements. The files themselves are unified diffs; do not stage them as if they had changed their target files. The delivered foundations keep nl/pl disabled. Ali alone runs git; Claude owns integration and version.

| Patch | Exact targets | Apply order / status |
| --- | --- | --- |
| 01-api-and-interface.patch | API request/result/mismatch enums, source_language.js, API documentation, assistant list/caveat, help source labels/caveat, pricing list, landing bilingual copy/metadata/eight badges/layout and historical badge notes | Prepare in an integration checkout; do not deploy separately. Every Arabic addition needs Ali's native check. New eight-card visual layout is unmeasured and must be reviewed. |
| 02-new-source-safety.patch | lang_check.py, subs_align.py, dub_long.html | Prepare new-code-only warning/mismatch/subtitle Unicode branches; do not change English or the other five checks. |
| 03-activate-last.patch | languages.py plus explicit-count/list tests and staged-vs-enabled tests | Single ENABLED_LANGUAGES switch, last. No additions to NEW_LANGUAGES. Check all preceding wiring/tests/native review first, release together after Ali's approval. |

All other listed engine consumers (short/long/corrections, speech recognition, line tidy/planner, people, scene, listening, cloning, API adapters) **need no textual patch**: current non-English branches already read the registry. Never change them to get_record; that would bypass staging. Names for Dutch and Polish already exist in the audio mismatch display map, which is not a list of authorised jobs. Any new enum or UI entry must ship with the single activation switch, never in advance on the live site.

### Prompt builder map

| Builder | File:line/function | Difference for nl/pl |
| --- | --- | --- |
| language_notes | language_prompts.py:134 | get_record selects staged data; shared nl/pl notes or source heading apply; old six strings unchanged. |
| gender_rules | language_prompts.py:146 | get_record selects staged data; shared nl/pl notes or source heading apply; old six strings unchanged. |
| glossary_block | language_prompts.py:154 | get_record selects staged data; shared nl/pl notes or source heading apply; old six strings unchanged. |
| translation_prompt | language_prompts.py:163 | get_record selects staged data; shared nl/pl notes or source heading apply; old six strings unchanged. |
| emotion_prompt | language_prompts.py:219 | get_record selects staged data; shared nl/pl notes or source heading apply; old six strings unchanged. |
| scene_prompt | language_prompts.py:228 | get_record selects staged data; shared nl/pl notes or source heading apply; old six strings unchanged. |
| speaker_prompt | language_prompts.py:233 | get_record selects staged data; shared nl/pl notes or source heading apply; old six strings unchanged. |
| line_planner_prompt | language_prompts.py:241 | get_record selects staged data; shared nl/pl notes or source heading apply; old six strings unchanged. |
| gender_context | language_prompts.py:249 | Stable context/legacy keys; no deterministic NL/PL morphology inferred. |
| people_rules | language_prompts.py:290 | get_record selects staged data; shared nl/pl notes or source heading apply; old six strings unchanged. |

The shared Dutch notes preserve actual address/gender, IJ names, compound/separable meanings, time expressions and contextual idioms. Polish notes preserve inflected-name identity, omitted subjects, past person/gender, quoted speech, nonhuman/nonvirile groups and polite third-person address. Every Arabic form stays provisional. Text templates cannot prove acoustic understanding or correct translation.

### Specific hazards and release checks

- English text evidence accepts every tested Latin-script source, and the old German screen accepts three Dutch paragraphs. **Do not patch these checks in Job17.** Ali explicitly requested preserving legacy behavior. See every false-match row in job17-confusion.json and the handoff. The new nl/pl screen is exclusive on this invented set only. Word presence is neither audio language detection nor a calibrated probability; short, proper-name, numeric and mixed input can stay inconclusive.
- lang_check._LATIN's legacy À-ɏ range already covers Polish letters, including ł. Leave it unchanged. 02 reports inconclusive text for nl/pl with a neutral review warning, rather than treating lack of mismatch as language confirmation. Audio mismatch uses the existing detected-language evidence and user switch; no extra recogniser pass proposed.
- subs_align already uses Unicode Latin share and scoped tokenisation. Script share alone accepts other Latin languages. 02 rejects a lexical mismatch for nl/pl before correction; inconclusive text still proceeds through existing word-alignment trust rules so a one-word name cue is not rejected as proven wrong. This is a residual limit: short/ambiguous wrong-language subtitles cannot reliably be detected by these word lists. Add tests for English/German vs Dutch and Dutch/German/English vs Polish long texts, short valid cues, all-diacritic Polish and mixed scripts. No automatic case stemming.
- dub_long onSubPicked has an ASCII client-side Latin count: the proposed new-source-only branch uses Unicode Script=Latin so letters such as ł are not lost. Preserve the old six count exactly. app.js's ASCII-looking safety regex recognises paths/errors; it is not transcript-language evidence, so leave it. ASCII encoding of a two-letter source code in cache keys is safe. Opaque ids and English regex branches stay unchanged.
- Re-run the enabled eight-source unit/lifecycle/UI/API tests and unchanged-output proof in an isolated integration checkout. Test upload, stored code, transcription arguments, mismatch/switch, subtitle cleaning/alignment, translation/listening/context, shorten, clone language, corrections, redo/reload, old/missing/unknown/cased source, API enums and result labels. No paid/unit calls; actual consented audio and native review remain separate release checks. Compare copied-voice behavior without promising a native accent.
- At 1440/1180/1024/768/390/320 widths in both interface locales, verify all eight badges, no overlap/overflow, correct labels, selectors outside upload buttons, saved-choice restoration, keyboard focus, and reduced motion. The fourth badge per side adds room; these are proposed, not screenshots tested here. Remove stale six-card measured claims rather than reusing them for eight cards.
- Existing six-only fixture assertions stay untouched in this delivery. The activation patch extends production enumerations and adapts release-state tests; the frozen baseline still compares only original six records/text/prompt/subtitle results. No pricing, clone-count, credits, SQL or config changes.

### Safety claims

| Claim | Status | Evidence / limit |
| --- | --- | --- |
| Existing six frozen records, 2,754 full returned text structures, 72 prompt/context outputs, original subtitle outputs identical | Proven on captured test inputs | test_lang_job17_legacy.py; not a proof over every possible string. |
| nl/pl cannot be accepted by public registry/short saved-code setter today | Proven | StagedRegistryTests; get/names default en, validate rejects. Metadata-only get_record is deliberately available. |
| Old/missing/garbage code resolves to English; case/space supported codes normalise | Proven | Existing + new registry and saved-job tests. Malicious or corrupted manually inserted staged jobs default en; no real staged job should exist before release. |
| Consumers listed use enabled lookups; no protected engine/page/API file edited by this job | Documented | Inventory/current hashes; concurrent work was detected and retained, not overwritten. |
| Timing ratio/rates and lexical thresholds are calibrated | Not claimed; estimated/unmeasured | Ratio1.0; rate None; curated screen, no probabilities. |
| New speech/translation/Arabic accent quality | Unmeasured | No live trial; all provisional Arabic/source wording needs native check. |
| Prepared wiring will work after activation | Likely, requires integration checks | Exact patches parse offline; full engine/UI runtime and eight-badge visual verification not performed as a release. |
