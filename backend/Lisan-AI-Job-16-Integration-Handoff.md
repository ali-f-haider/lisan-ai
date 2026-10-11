# Lisan AI — Job 16 integration handoff

The six original-language choices are now wired into the local short-dub, long-dub and API workflows: English, Spanish, French, Portuguese, Italian and German, always dubbed into Arabic. The project keeps its original language through saving, reopening, redo and corrections. A confidently detected mismatch pauses analysis and offers an explicit language switch using the prepared audio. English compatibility tests pass for the covered prompts and text helpers. Live quality in the five added languages, native review and browser visual checks remain unverified; this delivery is not a claim that every language produces equally good dubbing.

Prepared 2026-10-11. Ali authorized the engine/page integration after the foundation delivery. No config.py, SQL, Git commits or pushes were changed or performed. Claude owns the version bump. Files are already applied to Ali's local backend folder. The accompanying ZIP contains the full required Job 16 foundation and integration files; its manifest lists hashes. Never overwrite unrelated local work blindly with the ZIP.

## Behavior and evidence

| Area | Result | Evidence / limitation |
| --- | --- | --- |
| Original language | Optional en/es/fr/pt/it/de; omitted source defaults to en; Arabic remains the target | Unit/API tests; all beta flags false and regional variants empty |
| Old projects | Missing or unknown saved values resolve to en without rewriting legacy records | Lifecycle tests; unknown new requests are rejected |
| Case handling | Registry, saved records and website validators normalize supported codes; public API schema requires exact lowercase codes | Tests; no silent default for a new unsupported request |
| Short dub | Selection reaches transcription, translation, subtitle alignment, gender context, emotion listening and scene context | Actual worker tests with mocked external tools; no paid calls |
| Short persistence | Source sidecar in uploads, saved project JSON and reattachment; sidecar included in existing register backup sweep | Local persistence tests. A paused short job still cannot resume after a process restart: its progress/owner runtime lifecycle was not redesigned |
| Long dub | Source stored in job JSON, defaults in public views, inherited by redo and correction jobs; source retained for recloning | Lifecycle tests; existing job backup/storage machinery is retained |
| Mismatch | Existing audio detector, sequential model queue and release retained; pause before decoding/translation on sufficiently strong different-language evidence | Finite evidence guard tested. Existing thresholds: >=4 seconds per judged stretch, >=0.75 probability and >=8 seconds flagged speech (read from files). 60% dominance is estimated, not calibrated accuracy |
| Switch | Owner/state checks; recognition reruns on the same job using prepared extraction/separation/diarization. No new estimate/analysis charge | Worker/lifecycle/API tests; expired short upload produces a clear re-upload message |
| Subtitles | Chosen source passed into cleaning/matching; output retains original spelling, accents and apostrophes; download labels/filenames use source | Tests. Script and vocabulary evidence cannot reliably distinguish closely related Latin languages in every short or shared phrase; matching to recognized dialogue is also used |
| Timing | Non-English sentence splitting uses language punctuation and word timestamps; short visual source slots use actual timing, not the English character estimate | Tests. Registry timing ratios are conservative estimates; no native timing benchmark completed |
| Voices | Original-language label follows cloned samples and retries; synthesized dialogue stays Arabic | Source/lifecycle tests. No live clone-quality validation claimed |
| Pricing | Existing credit rates, paid-operation accounting, quote hashing, clone counts and Arabic target are retained | Existing billing/API tests pass; no language surcharge or separate charge for the switch |
| Privacy/logs | Long job records source code in language_set; public API exposes supported detected code/switch availability only | Allow-list tests. No media, dialogue or names from the court clip were added to repository fixtures |

Inconclusive speech evidence remains a review warning instead of claiming the selected language was confirmed. A supported mismatch preselects the detected language and requires the user to click the switch. Unsupported detected languages do not become English silently; the person must choose a supported original language. Very short clips and mixed speech may lack sufficient evidence to pause.

## Changed code paths

The complete file list and changed Python entry points follow at the end. The main wiring is:

- languages.py, lang_text.py, language_prompts.py: six-language registry, separate English ending profiles, offset-preserving matching and prompt builders. German multiword abbreviations are protected during sentence splitting. No shared replacement for the two English ending behaviors.
- source_jobs.py: UUID-only, atomic short-project source and prepared-retry files. Invalid/outside-upload retry paths are refused. Caller routes enforce ownership.
- source_rows.py: word-time splitting without changing original words; the non-English short worker assigns unique final segment IDs after splitting.
- whisper_service.py: selected recognition language, source-aware row helpers, mismatch checkpoint and prepared retry. Model queue order is retained and the model is released at a pause.
- longdub_service.py: create/public/redo source persistence; source forwarding through row planning, subtitle import, gender context, translation, listening, scene inference, speaker prompts, split/retranslation/shortening and cloning; explicit switch invalidates recognition only. Speaker voting algorithms, thresholds and voiceprint extraction were not changed.
- line_tidy.py and line_planner.py: source-aware punctuation and prompt branches. English follows the old implementation. Foreign speech-rate estimates do not use the English syllable heuristic.
- gemini_service.py, gender_context.py, emotion_listen.py, scene_context.py and speaker_vote_live.py: source-aware instructions and cache keys where needed, preserving the captured English prompt strings. Scene lookup can derive a saved short-job source for unchanged downstream callers.
- subs_align.py: source-aware checking, cleaning, token surfaces, aligning and missed-line additions; matching tokens never replace the original written surfaces. English uses the previous path.
- inworld_service.py and longdub_edits.py: saved/parent source reaches cloning and correction recloning. Non-English clone retries retain that label rather than falling back to the old unrelated sample language. Existing English retry behavior remains.
- main.py: validated source fields/forms, short source sidecars and progress, both owned switch routes, source script/registry serving, long original subtitle downloads and public API adapter. No billing algorithm changes.
- api_schema.py, api_dub.py, api_jobs.py, docs/api/openapi.yaml and api_docs.html: optional create source, polling language_mismatch, owned idempotent switch endpoint, safe mismatch object and matching documentation. API request bodies are hashed before any default source is injected.
- source_language.js and app.js: localized selector independently of site language; source forwarded with upload/subtitles/translation/reattachment; saved JSON source/default; dynamic source headings; mismatch choice and resumed polling; non-English source timeline slots and localized original-line tooltips.
- index.html and dub_long.html: source script, long create selector, localized mismatch action, original subtitle/help wording and dynamic original-language download labels. The completed-project editor already uses neutral original-line wording and receives source from its parent job; dub_long_edit.js needed no content change and is not in this batch.
- landing.html, pricing.html, help.html, terms.html, privacy.html and assistant_service.py: replace English-only source claims with the six supported original languages. Arabic drafts still need Ali's wording review.

The remaining files in the original inventory were reviewed as retained internal field names, interface-language switches, English compatibility branches, historical developer documents or neutral controls. account.html, admin.html, dialogs.js, models.py, add_faq.py and rebuild_help.py have no changes in this batch. An internal field called text/en/English is not automatically renamed if that would break stored data or original-track export compatibility.

## English compatibility

The foundation comparisons cover 423 invented strings and preserve both existing ending profiles. Twelve additional translation prompt hashes (six invented examples, each with/without gender context) were captured from the pre-integration files and match the integrated English branch byte for byte. Other prompt builders have source-based equality tests. These are bounded proofs for tested helpers/prompts, not a claim that nondeterministic external recognition or translation returns the same bytes on every call.

| Ending | Line tidy | Subtitle compatibility |
| --- | --- | --- |
| Wait... | sentence end | not sentence end |
| Wait… | sentence end | not sentence end |
| Wait. | sentence end | sentence end |
| Wait— | not sentence end | not sentence end |
| Wait - | not sentence end | not sentence end |
| Wait." | sentence end | sentence end |
| Wait. followed by a space | sentence end | not sentence end |
| Hi Mr. | not sentence end | sentence end |

English callers retain their previous defaults. Matching source detection does not pause. Mismatch handling is the intentionally authorized addition for a wrong source choice. New source metadata/controls and source-neutral customer copy are also deliberate additions; old English prompt and text-processing output are what the byte compatibility assertions cover.

## Tests

The new foundation has 35 tests. Integration adds 33 Python tests: 18 source/worker tests, 9 lifecycle tests and 6 authenticated API tests. Browser-script coverage adds 6 source UI tests plus a save/load test in the existing short-editor suite. Existing AST test fixtures were updated to provide the new dependencies, preserving their safety assertions. The API schema example set now includes the switch request. All invented fixtures are local; unit tests make no external calls.

- **New Job 16 Python tests:** 68 passed in 6.542 seconds (35 foundation + 33 integration), offline.
- **API regression suite:** 205 passed in 6.334 seconds.
- **Assistant billing checks after the final copy edit:** 9 passed in 0.600 seconds.
- **Full Python discovery:** 1,224 tests in 445.768 seconds; 1,218 passed, 1 failure, 4 errors, 1 skipped. Before integration: 1,191 tests, the same 1 failure, 4 errors and 1 skipped.
- **Full Node discovery:** 140 tests in 573.4515 milliseconds; 139 passed, 1 failed. All seven added language/save-load cases passed. Before integration: 133 tests, 132 passed, the same single failure.
- **Syntax checks:** 12 JavaScript/inline scripts passed; changed Python files compile; OpenAPI JSON parses. The 423-string English comparisons and 12 pre-integration translation-prompt hashes pass.

The remaining full-suite issues reproduce in the measured baseline:

| Test | Result / cause |
| --- | --- |
| test_lipsync_provider_flag | Import error: missing botocore dependency |
| test_r2_retention | Import error: missing botocore dependency |
| test_register_backup | Import error: missing botocore dependency |
| test_repository_hygiene.BackupRulesTests.test_no_currently_tracked_backend_file_matches_an_ignore_rule | Git exits 128 in the restricted environment |
| test_segment_writes.SegmentWrites.test_many_saves_at_once_never_fail_and_leave_no_temporary_files | Existing Windows concurrent-file-write failure (WinError 5) |
| tests/test_loading_ui.js: Auto Translate shows the loading panel while it waits and hides it afterwards | Existing Node fixture failure: null instead of flex |

The browser-play test remains skipped without its opt-in environment setting. Real paid language trials and browser visual checks were not completed. Browser preview attempts timed out. The final customer wording/selector changes were checked with the full Node run, syntax checks and relevant Python checks; no external recognition, translation or voice services were called.


The corresponding logs are delivered with the package outside the repository. The full-suite baseline was measured before integration in this same local project. The Git hygiene failure is a sandbox/environment failure. Missing backup dependencies affect test imports. The Windows concurrent segment-write failure is a real pre-existing local failure; this job does not repair or dismiss it. The separate Node loading-panel failure also reproduces before integration. Do not treat the whole suite as green.

Syntax checks parse changed Python files and check changed browser scripts, including inline scripts. Unchanged original lines retain their exact original line-ending bytes. config.py's SHA-256 is 6011f4ae5ad86e3c86498fb1dc30c15b72fb7a445f816de4fcc95e41a7fa4cf9, unchanged from before this integration.

## Remaining validation

Native readers must approve the Arabic copy and expected translation forms; the curated stopword lists are not independently verified frequency rankings. Speech rates and cross-language timing ratios are estimates. Regional varieties share a language code in this release; no dialect-specific transcription/translation quality is promised. No real paid language trials or live deployment test were run for this batch. Use tools/language_trial.py --dry for local data checks; live trials require configured credentials and explicit cost approval, and prices must be supplied rather than guessed.

Browser visual QA could not run: the Codex browser timed out opening the local read-only preview. DOM/worker/API tests and script syntax checks ran instead. Before release, review both site languages on short and long pages using an invented clip for each source: selected language reaches recognition; wrong selection pauses and switching works; subtitle correction and source download preserve spelling; edited project save/load/reattach/redo/correction keeps source; voice output remains Arabic; interface language changes keep the source; quoted cost is unchanged. Check old English jobs and unsupported inputs as well. A staging restart is useful for long-job persistence and must not be confused with short-job restart resumability.

No schema migration is needed: source lives in existing long job JSON and short upload sidecars/project JSON. Defaults handle old records. This is proven for the tested lifecycle functions, while hosting-volume and backup restoration behavior still requires deployment verification.

## Handoff history and files

Lisan-AI-Job-16-Handoff.md and docs/languages/inventory.md retain the foundation snapshot for review. docs/languages/wiring.md now points here as the integrated status. docs/languages/api-contract.patch is superseded: do not apply it to the current integrated schema. The foundation ZIP in the earlier delivery is superseded by this integration package.

| Python file | Changed entry points |
| --- | --- |
| api_dub.py | `register` |
| api_jobs.py | `operation_view`, `job_view`, `mismatch_view` |
| assistant_service.py | `build_system_prompt` |
| emotion_listen.py | `_payload`, `listen` |
| gemini_service.py | `translate_segments`, `shorten_arabic_line` |
| gender_context.py | `read_transcript_people` |
| inworld_service.py | `clone_voices` |
| lang_check.py | `is_flagged`, `summarize`, `text_check`, `mismatch` |
| lang_text.py | `ends_sentence` |
| languages.py | `supported` |
| line_planner.py | `build_prompt`, `plan_lines` |
| line_tidy.py | `_ends_sentence`, `merge_split_phrases`, `_tail_start`, `move_unfinished_beginnings`, `flag_doubtful_cuts`, `tidy_lines` |
| longdub_edits.py | `start`, `run` |
| longdub_service.py | `public_view`, `init_upload`, `redo_project`, `split_rows_by_sentence`, `_vote_speakers`, `rows_from_raw`, `_run_analysis`, `attach_subtitle`, `_apply_subtitle`, `glossary_apply`, `retranslate_for_gender`, `_gender_args`, `_detect_speaker_genders`, `_translate_batch`, `_settle_addressees`, `_row_paces`, `_ground_rows`, `ground_emotions`, `_listen_and_merge`, `_translate_all`, `split_line`, `retranslate_line`, `_clone_with_retry`, `_rephrase_to_fit`, `_run_dubbing`, `switch_source_language` |
| main.py | `TranslateRequest._source_ok`, `EmotionRequest._source_ok`, `_cleanup_worker`, `source_language_script`, `source_languages`, `transcribe`, `short_switch_language`, `attach_media`, `progress`, `translate`, `_short_gender_context`, `SubsAlignRequest._source_ok`, `subs_align_route`, `LongDubInit._source_ok`, `longdub_init`, `long_switch_language`, `longdub_subtitles`, `_ApiLongDub.create`, `_ApiLongDub.switch_source` |
| scene_context.py | `_prompt`, `scenes_for_job` |
| speaker_vote_live.py | `prompt_for`, `_payload`, `_one_voter`, `collect` |
| subs_align.py | `_ends_sentence`, `reason_message`, `check_file`, `_clean_line`, `_cue`, `parse_subtitles`, `build_stream`, `_row_tokens`, `correct_rows`, `_add_missed`, `_language_stream`, `_surface_join` |
| tests/lang_source_capture.py | `source_namespace` |
| tests/test_api_language_proposal.py | `ApiLanguageProposalTests.test_proposal_matches_source_and_schema`, `ApiLanguageProposalTests.test_real_validator_optional_enums_defaults_and_unknowns` |
| tests/test_gender_aware.py | `ShortDubTests.load` |
| tests/test_speaker_quality.py | `speaker_helpers` |
| whisper_service.py | `split_segment`, `smooth_speaker_islands`, `merge_mid_sentence_rows`, `transcribe_worker` |

All delivered files (83):

- Lisan-AI-Job-16-Handoff.md
- Lisan-AI-Job-16-Integration-Handoff.md
- api_docs.html
- api_dub.py
- api_jobs.py
- api_schema.py
- app.js
- assistant_service.py
- docs/api/openapi.yaml
- docs/languages/adding-a-language.md
- docs/languages/api-contract.patch
- docs/languages/copy.md
- docs/languages/inventory.json
- docs/languages/inventory.md
- docs/languages/sources.md
- docs/languages/wiring.md
- dub_long.html
- emotion_listen.py
- gemini_service.py
- gender_context.py
- help.html
- index.html
- inworld_service.py
- landing.html
- lang_check.py
- lang_text.py
- language_prompts.py
- languages.py
- line_planner.py
- line_tidy.py
- longdub_edits.py
- longdub_service.py
- main.py
- pricing.html
- privacy.html
- scene_context.py
- source_jobs.py
- source_language.js
- source_rows.py
- speaker_vote_live.py
- subs_align.py
- terms.html
- tests/fixtures/lang/de/dialogue.json
- tests/fixtures/lang/de/sample.expected.json
- tests/fixtures/lang/de/sample.srt
- tests/fixtures/lang/en/comparison.json
- tests/fixtures/lang/en/endings.json
- tests/fixtures/lang/en/sample.expected.json
- tests/fixtures/lang/en/sample.srt
- tests/fixtures/lang/english_prompt_hashes.json
- tests/fixtures/lang/es/dialogue.json
- tests/fixtures/lang/es/sample.expected.json
- tests/fixtures/lang/es/sample.srt
- tests/fixtures/lang/fr/dialogue.json
- tests/fixtures/lang/fr/sample.expected.json
- tests/fixtures/lang/fr/sample.srt
- tests/fixtures/lang/it/dialogue.json
- tests/fixtures/lang/it/sample.expected.json
- tests/fixtures/lang/it/sample.srt
- tests/fixtures/lang/pt/dialogue.json
- tests/fixtures/lang/pt/sample.expected.json
- tests/fixtures/lang/pt/sample.srt
- tests/fixtures/lang_gold/de.json
- tests/fixtures/lang_gold/es.json
- tests/fixtures/lang_gold/fr.json
- tests/fixtures/lang_gold/it.json
- tests/fixtures/lang_gold/pt.json
- tests/lang_source_capture.py
- tests/test_api_language_proposal.py
- tests/test_api_schema.py
- tests/test_api_source_language.py
- tests/test_gender_aware.py
- tests/test_lang_english_unchanged.py
- tests/test_lang_prompts.py
- tests/test_lang_registry.py
- tests/test_lang_text.py
- tests/test_shortdub_ui.js
- tests/test_source_language_integration.py
- tests/test_source_language_lifecycle.py
- tests/test_source_language_ui.js
- tests/test_speaker_quality.py
- tools/language_trial.py
- whisper_service.py

## Commands for Ali

Run these in Windows Command Prompt from backend. They stage only this batch and commit only the listed paths, leaving unrelated staged work outside the commit. Review your working folder before committing. No config.py or push command is included.

```bat
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"
git --no-pager add -- "Lisan-AI-Job-16-Handoff.md" "Lisan-AI-Job-16-Integration-Handoff.md" "api_docs.html" "api_dub.py" "api_jobs.py" "api_schema.py" "app.js" "assistant_service.py" "docs/api/openapi.yaml" "docs/languages/adding-a-language.md" "docs/languages/api-contract.patch" "docs/languages/copy.md"
git --no-pager add -- "docs/languages/inventory.json" "docs/languages/inventory.md" "docs/languages/sources.md" "docs/languages/wiring.md" "dub_long.html" "emotion_listen.py" "gemini_service.py" "gender_context.py" "help.html" "index.html" "inworld_service.py" "landing.html"
git --no-pager add -- "lang_check.py" "lang_text.py" "language_prompts.py" "languages.py" "line_planner.py" "line_tidy.py" "longdub_edits.py" "longdub_service.py" "main.py" "pricing.html" "privacy.html" "scene_context.py"
git --no-pager add -- "source_jobs.py" "source_language.js" "source_rows.py" "speaker_vote_live.py" "subs_align.py" "terms.html" "tests/fixtures/lang/de/dialogue.json" "tests/fixtures/lang/de/sample.expected.json" "tests/fixtures/lang/de/sample.srt" "tests/fixtures/lang/en/comparison.json" "tests/fixtures/lang/en/endings.json" "tests/fixtures/lang/en/sample.expected.json"
git --no-pager add -- "tests/fixtures/lang/en/sample.srt" "tests/fixtures/lang/english_prompt_hashes.json" "tests/fixtures/lang/es/dialogue.json" "tests/fixtures/lang/es/sample.expected.json" "tests/fixtures/lang/es/sample.srt" "tests/fixtures/lang/fr/dialogue.json" "tests/fixtures/lang/fr/sample.expected.json" "tests/fixtures/lang/fr/sample.srt" "tests/fixtures/lang/it/dialogue.json" "tests/fixtures/lang/it/sample.expected.json" "tests/fixtures/lang/it/sample.srt" "tests/fixtures/lang/pt/dialogue.json"
git --no-pager add -- "tests/fixtures/lang/pt/sample.expected.json" "tests/fixtures/lang/pt/sample.srt" "tests/fixtures/lang_gold/de.json" "tests/fixtures/lang_gold/es.json" "tests/fixtures/lang_gold/fr.json" "tests/fixtures/lang_gold/it.json" "tests/fixtures/lang_gold/pt.json" "tests/lang_source_capture.py" "tests/test_api_language_proposal.py" "tests/test_api_schema.py" "tests/test_api_source_language.py" "tests/test_gender_aware.py"
git --no-pager add -- "tests/test_lang_english_unchanged.py" "tests/test_lang_prompts.py" "tests/test_lang_registry.py" "tests/test_lang_text.py" "tests/test_shortdub_ui.js" "tests/test_source_language_integration.py" "tests/test_source_language_lifecycle.py" "tests/test_source_language_ui.js" "tests/test_speaker_quality.py" "tools/language_trial.py" "whisper_service.py"
git --no-pager diff --cached --check -- "Lisan-AI-Job-16-Handoff.md" "Lisan-AI-Job-16-Integration-Handoff.md" "api_docs.html" "api_dub.py" "api_jobs.py" "api_schema.py" "app.js" "assistant_service.py" "docs/api/openapi.yaml" "docs/languages/adding-a-language.md" "docs/languages/api-contract.patch" "docs/languages/copy.md" "docs/languages/inventory.json" "docs/languages/inventory.md" "docs/languages/sources.md" "docs/languages/wiring.md" "dub_long.html" "emotion_listen.py" "gemini_service.py" "gender_context.py" "help.html" "index.html" "inworld_service.py" "landing.html" "lang_check.py" "lang_text.py" "language_prompts.py" "languages.py" "line_planner.py" "line_tidy.py" "longdub_edits.py" "longdub_service.py" "main.py" "pricing.html" "privacy.html" "scene_context.py" "source_jobs.py" "source_language.js" "source_rows.py" "speaker_vote_live.py" "subs_align.py" "terms.html" "tests/fixtures/lang/de/dialogue.json" "tests/fixtures/lang/de/sample.expected.json" "tests/fixtures/lang/de/sample.srt" "tests/fixtures/lang/en/comparison.json" "tests/fixtures/lang/en/endings.json" "tests/fixtures/lang/en/sample.expected.json" "tests/fixtures/lang/en/sample.srt" "tests/fixtures/lang/english_prompt_hashes.json" "tests/fixtures/lang/es/dialogue.json" "tests/fixtures/lang/es/sample.expected.json" "tests/fixtures/lang/es/sample.srt" "tests/fixtures/lang/fr/dialogue.json" "tests/fixtures/lang/fr/sample.expected.json" "tests/fixtures/lang/fr/sample.srt" "tests/fixtures/lang/it/dialogue.json" "tests/fixtures/lang/it/sample.expected.json" "tests/fixtures/lang/it/sample.srt" "tests/fixtures/lang/pt/dialogue.json" "tests/fixtures/lang/pt/sample.expected.json" "tests/fixtures/lang/pt/sample.srt" "tests/fixtures/lang_gold/de.json" "tests/fixtures/lang_gold/es.json" "tests/fixtures/lang_gold/fr.json" "tests/fixtures/lang_gold/it.json" "tests/fixtures/lang_gold/pt.json" "tests/lang_source_capture.py" "tests/test_api_language_proposal.py" "tests/test_api_schema.py" "tests/test_api_source_language.py" "tests/test_gender_aware.py" "tests/test_lang_english_unchanged.py" "tests/test_lang_prompts.py" "tests/test_lang_registry.py" "tests/test_lang_text.py" "tests/test_shortdub_ui.js" "tests/test_source_language_integration.py" "tests/test_source_language_lifecycle.py" "tests/test_source_language_ui.js" "tests/test_speaker_quality.py" "tools/language_trial.py" "whisper_service.py"
git --no-pager commit --only -m "Integrate six original languages across dubbing and API" -- "Lisan-AI-Job-16-Handoff.md" "Lisan-AI-Job-16-Integration-Handoff.md" "api_docs.html" "api_dub.py" "api_jobs.py" "api_schema.py" "app.js" "assistant_service.py" "docs/api/openapi.yaml" "docs/languages/adding-a-language.md" "docs/languages/api-contract.patch" "docs/languages/copy.md" "docs/languages/inventory.json" "docs/languages/inventory.md" "docs/languages/sources.md" "docs/languages/wiring.md" "dub_long.html" "emotion_listen.py" "gemini_service.py" "gender_context.py" "help.html" "index.html" "inworld_service.py" "landing.html" "lang_check.py" "lang_text.py" "language_prompts.py" "languages.py" "line_planner.py" "line_tidy.py" "longdub_edits.py" "longdub_service.py" "main.py" "pricing.html" "privacy.html" "scene_context.py" "source_jobs.py" "source_language.js" "source_rows.py" "speaker_vote_live.py" "subs_align.py" "terms.html" "tests/fixtures/lang/de/dialogue.json" "tests/fixtures/lang/de/sample.expected.json" "tests/fixtures/lang/de/sample.srt" "tests/fixtures/lang/en/comparison.json" "tests/fixtures/lang/en/endings.json" "tests/fixtures/lang/en/sample.expected.json" "tests/fixtures/lang/en/sample.srt" "tests/fixtures/lang/english_prompt_hashes.json" "tests/fixtures/lang/es/dialogue.json" "tests/fixtures/lang/es/sample.expected.json" "tests/fixtures/lang/es/sample.srt" "tests/fixtures/lang/fr/dialogue.json" "tests/fixtures/lang/fr/sample.expected.json" "tests/fixtures/lang/fr/sample.srt" "tests/fixtures/lang/it/dialogue.json" "tests/fixtures/lang/it/sample.expected.json" "tests/fixtures/lang/it/sample.srt" "tests/fixtures/lang/pt/dialogue.json" "tests/fixtures/lang/pt/sample.expected.json" "tests/fixtures/lang/pt/sample.srt" "tests/fixtures/lang_gold/de.json" "tests/fixtures/lang_gold/es.json" "tests/fixtures/lang_gold/fr.json" "tests/fixtures/lang_gold/it.json" "tests/fixtures/lang_gold/pt.json" "tests/lang_source_capture.py" "tests/test_api_language_proposal.py" "tests/test_api_schema.py" "tests/test_api_source_language.py" "tests/test_gender_aware.py" "tests/test_lang_english_unchanged.py" "tests/test_lang_prompts.py" "tests/test_lang_registry.py" "tests/test_lang_text.py" "tests/test_shortdub_ui.js" "tests/test_source_language_integration.py" "tests/test_source_language_lifecycle.py" "tests/test_source_language_ui.js" "tests/test_speaker_quality.py" "tools/language_trial.py" "whisper_service.py"
```
