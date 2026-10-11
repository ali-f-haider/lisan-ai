> **Superseded for integration:** The engine, pages and API have now been wired locally. Use [Lisan-AI-Job-16-Integration-Handoff.md](Lisan-AI-Job-16-Integration-Handoff.md) for the current changed files, test results and commands. The foundation results and proposed patches below are historical; do not apply the old API patch over the integrated files.

# Lisan AI — Job 16 handoff

The language foundation is ready for Claude to integrate: a registry, text tools, prompt builders, invented test data, an API proposal and a map of the source-language changes. It does **not** enable new languages on the live site. Local tests prove the covered English behaviour is preserved; native-language quality and live translation quality are not yet measured. The main risks during integration are losing the chosen language on a saved/restarted project, applying the wrong English ending profile, and treating Latin-looking text as proof of its language.

Prepared 2026-10-11. All counts in this handoff are **measured here** unless explicitly labelled **read from the brief/files**, **cited**, or **estimated**. Repository filenames/function identifiers are technical references, not customer copy. Every Arabic copy draft and translation expectation is marked for native review.

## Scope and Ali's answered decisions

English plus Spanish, French, Portuguese, Italian and German; target remains Modern Standard Arabic. Launch flags have no Beta labels, price policy stays the same as English, source choice is optional/default English, no separate auto-detect mode, one entry per language with empty future variants, and subtitles must match the video's source language. These are data settings; Claude must wire their persistence and consumers. No money logic was changed.

The two approved profiles remain separate: `tidy` for line cleanup/planning; `subtitle` for subtitle compatibility. There is no merged preferred rule. Their intentional differences are listed below.

## Files delivered

There are **42 new files** in this package; existing production files were not edited by this job.

- `languages.py`: frozen records, safe internal defaults, names, punctuation/elisions/abbreviations, source notes, policy flags and conservative timing settings.
- `lang_text.py`: independent English profiles; lossless matching tokens/offsets; sentence and subtitle helpers; accent-aware script shares and advisory language evidence.
- `language_prompts.py`: pure translation, emotion, scene, speaker, planner, gender/context and people prompt builders. Current English prompt text is copied and checked against the current source, not rewritten by a broad string replacement.
- `tools/language_trial.py`: offline dry checks and an explicitly invoked live translation trial, with supplied token prices and returned-usage cost reporting.
- `tests/lang_source_capture.py` and the five test files: pure source capture, text/data tests, current-English comparisons and an in-memory API patch check.
- `tests/fixtures/lang/`: invented dialogue/subtitle controls and pinned English endings/comparisons. `tests/fixtures/lang_gold/`: provisional Arabic regex screens.
- `docs/languages/`: location inventory, customer copy, cited sources/limitations, future-language guide, protected-file wiring plan and **unapplied** API contract patch.

No config/version changes, protected-file edits, SQL, commits, pushes or paid requests were made. Git diff/history inspection could not run in this sandbox; this is not a claim that the whole checkout is clean or that other developers have no changes. The package includes only the explicit new files listed in the commands below.

## Inventory

The active-source scan found **482 matching locations in 56 files**. This includes conservative false positives; a match is not permission to change it. [inventory.md](docs/languages/inventory.md) gives file:line, existing text, change/copy section, English compatibility and regression check. [inventory.json](docs/languages/inventory.json) is its machine-readable counterpart. Both describe scan scope and excluded dependency, backup, private-media and historical folders.

| Category | Locations |
|---|---:|
| API | 12 |
| UI text | 219 |
| engine | 156 |
| help | 54 |
| legal | 12 |
| prompt | 18 |
| test | 11 |

Two forced-English transcription calls were found, for short and long dubbing. Other paths that need source propagation include saved jobs/corrections, subtitles, translation/shortening, speaker context, clone sample labels and original-language exports. UI locale, Arabic target, internal `english_text`/glossary `en` keys, identifiers and existing English demo labels must remain independent of the new source choice.

## Wiring plan for Claude

Read [wiring.md](docs/languages/wiring.md) before integrating. It includes old/new signature and argument fragments, exact pure builder calls, persistence/default rules, the prompt location table and the safety matrix. The inventory maps every protected-file location to that plan or a customer-copy ID. These are integration instructions and fragments, **not a complete applied engine patch**.

1. Store canonical `source_language` per project. Long job JSON accepts extra fields; short projects need the described owner-checked atomic sidecar plus project JSON save/load. Missing legacy source defaults to `en`. Explicit invalid new API input is rejected rather than silently decoded as English.
2. Forward source through **both** transcription calls and every analysis/translation/planner/subtitle caller. Keep existing English timed-row splitters verbatim; the new lossless string splitter is not a replacement for timestamped rows.
3. Retain the `tidy`/`subtitle` distinction at every call site. Match by folded tokens for new sources, but write the original spans back. Never reconstruct source text from folded matching forms.
4. Add the audio mismatch gate and explicit switch action. Script/function-word evidence is advisory; it cannot certify Spanish versus Portuguese. Re-run source-dependent work after a user-approved switch, preserving ownership, paid-operation identities and current safeguards.
5. Use the pure prompt builders, retain audio/frame attachments and accounting, and forward parent source to correction/recloning. Arabic target speech stays Arabic. Verify supported clone sample labels in staging.
6. Apply [api-contract.patch](docs/languages/api-contract.patch) **with** API forwarding and engine wiring, never alone. Omitted source remains English; old idempotency bodies must not be changed before hashing. Live `api_schema.py` and OpenAPI were left untouched deliberately.
7. Apply the English/Arabic copy drafts at the inventory locations. Keep interface locale separate from source choice and preserve old editor labels on the English path.
8. Run the integration matrix in wiring.md: all supported sources through short/long flows, old/default English, save/load/restart/redo/copy/reattach/correction, wrong/inconclusive subtitles and audio mismatch, unchanged billing and source-only logs.

For a new-language batch, do not enable just the page selector or schema: that can create a job whose source is selected correctly but processed as English. None of those live integrations was done here because Claude owns the protected files.

## Tests and results

The **35 added unit tests pass**: registry 6, text/fixtures 10, prompts/trial 12, English compatibility 5, API proposal 2. Test count is not a translation-quality score. The new unit tests and dry trials make no network calls. Speaker prompt comparisons read the source AST; they do not import the speaker-vote/Job 13 modules.

Full Python discovery: **1191 tests in 503.656 seconds** (measured here); **FAILED (failures=1, errors=4, skipped=1)**.

Compared with the baseline read from the brief: collection changed by +84; 35 are this package's added tests, leaving a collection difference of +49 beyond those additions. Failures changed from 4 to 1; errors from 7 to 4. Without a completed before-change run, the remaining difference cannot be attributed to this job.

The full-suite problems, exactly as reported:

- `test_lipsync_provider_flag (unittest.loader._FailedTest.test_lipsync_provider_flag)`
- `test_r2_retention (unittest.loader._FailedTest.test_r2_retention)`
- `test_register_backup (unittest.loader._FailedTest.test_register_backup)`
- `test_no_currently_tracked_backend_file_matches_an_ignore_rule (test_repository_hygiene.BackupRulesTests.test_no_currently_tracked_backend_file_matches_an_ignore_rule)`
- `test_many_saves_at_once_never_fail_and_leave_no_temporary_files (test_segment_writes.SegmentWrites.test_many_saves_at_once_never_fail_and_leave_no_temporary_files)`

Cause review (read from the measured test logs): three module-load errors are `ModuleNotFoundError: No module named botocore` through r2_backup.py; the repository hygiene error is Git exit 128 in this sandbox. The failure is the existing concurrent `_write_segments` test: Windows raises `PermissionError [WinError 5]` replacing its temporary file with segments.json. A separate rerun of tests/test_segment_writes.py ran 3 tests and reproduced that one failure. This is outside the language modules and remains **an unresolved Windows concurrency finding** for Claude; it must not be described as a passing full suite or dismissed as a proven harmless permission issue. Neither the existing writer nor its test was edited.

The one skip is the existing browser playback test in test_shared_time_dialog.py, which requires LISAN_BROWSER_TESTS=1. That browser interaction was not exercised by this run.


The baseline **read from the brief** is 1107 tests, with 4 failures and 7 errors. That is a supplied baseline, not an independently reproduced before-change run. Compare the final local collection/result above; failures outside the added tests are reported, not silently accepted or fixed in protected code. The full run used a writable workspace temp directory, UTF-8 output, loopback permission for existing local API tests and one maths-library worker to avoid the local default's excessive convolution overhead. Application settings/code were unchanged.

Node: **133 tests, 132 passed, 1 failed**, no skips/cancellations. The failure is `tests/test_loading_ui.js:44`, “Auto Translate shows the loading panel while it waits and hides it afterwards”: actual `null`, expected `flex`. No page or script was changed by Job 16. This remains an existing-path UI failure for Claude to investigate; a before-change Node run was not separately recorded.

Dry trial: **125 cases prepared**, **0 live attempts**, no tokens or paid usage. Translation percentages are **not measured**. Fake-transport tests verify output plus thought tokens are priced with the explicitly supplied output rate and missing usage is reported. Returned usage excludes hidden retries, so a live trial report is not a complete billing receipt.

English proof: **423 invented strings** checked against current helpers with missing/en/upper-case/unknown internal code; **23 pinned ending cases**; current-source prompt equality for all delivered prompt families; no within-profile differences found on those inputs. This proves the tested pure helpers/prompts, not byte-identical end-to-end output after Claude's future wiring. Where there was no old token-object/string-split API, tests prove losslessness rather than claiming a nonexistent parity comparison. Existing English timed-row branches must remain unchanged.

Token proof: **203 samples across 6 languages**, including mixed punctuation, accents, elisions and combining marks, rejoin character-for-character with contiguous offsets. Invented source fixtures contain **150 dialogue lines** plus **6 subtitle controls**. Gold screens contain **25 cases per new language** and require native approval.

## English ending disagreements — preserve both

The values below were captured from today's helpers, not chosen for grammatical preference. `␠` means a literal space. `stop`/`continue` is the helper's boolean ending decision, not a promise about performance.

| Ending | tidy: line cleanup/planner | subtitle: matching/import |
|---|---|---|
| `Wait...` | stop | continue |
| `Wait…` | stop | continue |
| `Wait.` | stop | stop |
| `Wait—` | continue | continue |
| `Wait␠-` | continue | continue |
| `Wait..."` | stop | continue |
| `Wait…"` | stop | continue |
| `Wait."` | stop | stop |
| `Wait—"` | continue | continue |
| `Wait␠-"` | continue | continue |
| `Wait...␠` | stop | continue |
| `Wait…␠` | stop | continue |
| `Wait.␠` | stop | continue |
| `Wait—␠` | continue | continue |
| `Wait␠-␠` | continue | continue |
| `Mr.` | continue | continue |
| `Hi␠Mr.` | continue | stop |
| `Mr."` | stop | stop |
| `U.S.` | stop | stop |
| `3.5` | continue | continue |
| `What?!` | stop | stop |
| `I-If` | continue | continue |
| `Wait.)` | stop | stop |

These are cross-profile disagreements, not new-versus-old failures. Unifying them would change English and needs a separate approved job. No new product decision is required for this delivery; the previously answered choices were preserved.

## What is still missing / evidence limits

- **Native review:** source dialogue, subtitle conventions, address/false-friend notes, Arabic copy and Arabic regex expectations. Regexes can reject valid synonyms or accept a misleading substring; they are a preliminary screen, not a meaning or audio-quality verdict.
- **Requested frequency ranking remains unverified:** the registry has 40 curated function words per language, not a proven corpus top-40 list. This gap is explicitly documented in sources.md. Do not market it as frequency research or use it as a probability.
- **Rates are estimated:** source six-syllable/second values are descriptive placeholders only, not used for money/timing measurement. Ratios begin at estimated 1.0; the existing 2–2.5 target Arabic words/second budget is read from files. No source/Arabic audio rate was measured. Regional accent/vocabulary coverage has not been measured.
- **Live trials were not run:** launch quality cannot be inferred from the dry run. Supply the key securely and actual current input/output token prices to the trial tool only after approving the spend. Do not paste keys into chat. Evaluate meaning, audible delivery and timing alongside screens.
- **End-to-end integration is Claude's work:** lifecycle persistence, audio mismatch confidence calibration, subtitle alignment, UI/API forwarding, clone-language support and complete English workflow regression still need the listed checks. The estimated detector-dominance proposal is not a measured accuracy threshold.
- **Migration evidence is scoped:** long job JSON and generic event detail storage support new fields without SQL by source inspection. Short sidecar cleanup/backup and policy-setting persistence are documented designs, not deployed proofs. No database schema was altered.

## Local verification commands (Command Prompt, backend)

```bat
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"
python -m unittest discover -s tests -p "test_lang*.py"
python -m unittest discover -s tests -p test_api_language_proposal.py
python tools/language_trial.py --dry
python -m unittest discover -s tests
```

For the Node suite, use `node --test tests/test_*.js` if your installed Node supports wildcard input; otherwise pass the individual `test_*.js` filenames, as was done for the measured run. The Python command may use `venv\Scripts\python.exe` if `python` is not the project's interpreter. Any trial cost figure is a returned-usage estimate, not customer credits.

## Literal Git commands — Ali only, no push

Review the handoff and files first. These commands include **only** this package, exclude config.py and use `commit --only` so unrelated staged changes are not included. If Claude has already integrated a file, review that overlap before staging it; do not overwrite it from the ZIP.

```bat
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"
git --no-pager add -- languages.py lang_text.py language_prompts.py tools/language_trial.py tests/lang_source_capture.py tests/test_lang_registry.py tests/test_lang_text.py tests/test_lang_prompts.py tests/test_lang_english_unchanged.py tests/test_api_language_proposal.py docs/languages/inventory.md docs/languages/inventory.json docs/languages/sources.md docs/languages/copy.md docs/languages/adding-a-language.md docs/languages/wiring.md docs/languages/api-contract.patch tests/fixtures/lang/en/sample.srt tests/fixtures/lang/en/sample.expected.json tests/fixtures/lang/es/sample.srt tests/fixtures/lang/es/sample.expected.json tests/fixtures/lang/es/dialogue.json tests/fixtures/lang_gold/es.json tests/fixtures/lang/fr/sample.srt tests/fixtures/lang/fr/sample.expected.json tests/fixtures/lang/fr/dialogue.json tests/fixtures/lang_gold/fr.json tests/fixtures/lang/pt/sample.srt tests/fixtures/lang/pt/sample.expected.json tests/fixtures/lang/pt/dialogue.json tests/fixtures/lang_gold/pt.json tests/fixtures/lang/it/sample.srt tests/fixtures/lang/it/sample.expected.json tests/fixtures/lang/it/dialogue.json tests/fixtures/lang_gold/it.json tests/fixtures/lang/de/sample.srt tests/fixtures/lang/de/sample.expected.json tests/fixtures/lang/de/dialogue.json tests/fixtures/lang_gold/de.json tests/fixtures/lang/en/endings.json tests/fixtures/lang/en/comparison.json Lisan-AI-Job-16-Handoff.md
git --no-pager commit --only -m "Add tested source-language foundation and integration handoff" -- languages.py lang_text.py language_prompts.py tools/language_trial.py tests/lang_source_capture.py tests/test_lang_registry.py tests/test_lang_text.py tests/test_lang_prompts.py tests/test_lang_english_unchanged.py tests/test_api_language_proposal.py docs/languages/inventory.md docs/languages/inventory.json docs/languages/sources.md docs/languages/copy.md docs/languages/adding-a-language.md docs/languages/wiring.md docs/languages/api-contract.patch tests/fixtures/lang/en/sample.srt tests/fixtures/lang/en/sample.expected.json tests/fixtures/lang/es/sample.srt tests/fixtures/lang/es/sample.expected.json tests/fixtures/lang/es/dialogue.json tests/fixtures/lang_gold/es.json tests/fixtures/lang/fr/sample.srt tests/fixtures/lang/fr/sample.expected.json tests/fixtures/lang/fr/dialogue.json tests/fixtures/lang_gold/fr.json tests/fixtures/lang/pt/sample.srt tests/fixtures/lang/pt/sample.expected.json tests/fixtures/lang/pt/dialogue.json tests/fixtures/lang_gold/pt.json tests/fixtures/lang/it/sample.srt tests/fixtures/lang/it/sample.expected.json tests/fixtures/lang/it/dialogue.json tests/fixtures/lang_gold/it.json tests/fixtures/lang/de/sample.srt tests/fixtures/lang/de/sample.expected.json tests/fixtures/lang/de/dialogue.json tests/fixtures/lang_gold/de.json tests/fixtures/lang/en/endings.json tests/fixtures/lang/en/comparison.json Lisan-AI-Job-16-Handoff.md
```

No push command is included. Claude owns version bumps and live integration.
