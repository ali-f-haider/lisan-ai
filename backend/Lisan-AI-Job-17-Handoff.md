# Lisan AI — Job 17 handoff

Dutch and Polish foundations are ready in the project folder: metadata, pure text tools, prompt notes, invented fixtures and tests. They remain disabled for real jobs. The current six languages retain their captured text/prompt/data outputs. Claude still needs to review and apply the supplied wiring proposals, Ali needs to approve the Arabic drafts and native-language cases, and real audio quality remains unmeasured. English's existing false matches are a finding, as Ali requested; stricter nl/pl checks cannot make an unchanged English or German check reject every Dutch/Polish paragraph. No paid requests, SQL, commits or pushes were made. config.py was not edited by this job.

Read from the files at completion: version **1.82.50**, changed concurrently outside this job (brief was 1.82.49). No version action requested from Ali; Claude controls versions. Ali approved no Beta labels when these languages eventually launch. Exact copied-voice accent caveat is retained; no native-quality/accent promise.

## Delivered changes

- languages.py: frozen nl/pl rows, 40 curated distinct words each, quotation/abbreviation/elision/sound candidates, Arabic/native/English names, unmeasured source rate, estimated ratio1.0, beta false and empty variants. Added get_record/registered/is_registered for metadata; production get/names/supported/is_supported remain enabled-only. Single future ENABLED_LANGUAGES switch, NEW_LANGUAGES unchanged.
- lang_text.py: metadata lookup for development helpers; scoped ł/Ł fold for Polish; conservative Polish ordinals/year abbreviations; Dutch clitic matching and intact IJ/compounds; contextual etc./enz./itd./itp. dots; new-only competitive lexical screen with inconclusive outcomes on weak evidence. Tokens and split spans preserve every original character. No case stemming, syntax parser or automatic number-value rewrite.
- language_prompts.py: Dutch/Polish meaning/address/gender/time/name notes used by all relevant builders. The 72 captured legacy prompt/context/glossary outputs are exact-equality tested. Stable english_text/en/english keys are not renamed. Language notes instruct a translator, not a deterministic morphological analyzer.
- tools/language_trial.py: explicit nl/pl development selection; old five default choices retained. Dry mode builds/validates 50 prompts without keys, engine calls or network. Live prices remain explicit arguments, not assumed rates.
- New fixtures: 60 independently authored dialogue expectations; 20 subtitle cues with independent clean/change expectations; 160 invented confusion paragraphs (20 per language, three lines each); 50 provisional Arabic gold screens with positive/counterexamples and context. Polish includes eight past predicate-gender cases plus raw second-person vowel tests; Dutch includes ten time/idiom/false-friend cases. No film/customer dialogue used.
- Updated developer docs: cited evidence, exhaustive current inventory, bilingual copy drafts, guide and unapplied wiring. Three new test files only; **no existing test edited** in this delivery. Proposed future test edits exist only inside the activation patch.

The package contains 30 backend files. Full originals/current outputs, minimal unified delivery diff, ZIP and SHA256 manifest are supplied in the local outputs folder. The proposed wiring patches are documentation artifacts, not applied changes to their targets.

## Tests and measured limits

| Run | Actual result |
| --- | --- |
| Before: existing language tests | 33 tests, OK (0.795s). |
| Before: full Python checkout | 1,445 tests, 1 failure, 1 error, 1 skipped (650.711s). Windows concurrent-file replacement failure and sandbox Git-read error. |
| Delivered language tests | 53 tests, OK (1.712s); 20 added methods with thousands of subcases. |
| Dry trial | nl25 + pl25 prepared; zero attempted, zero cost; translation pass percentage not measured. |
| Final full Python checkout | **1,481 tests, 1 failure, 2 errors, 1 skipped** (530.226s). All language tests pass. Details below. |
| Before full Node run | 165 tests, 163 pass, 2 fail. Missing LISAN_BACKEND caused one file-level failure; loading-panel assertion was the other. |
| Final full Node run | **167 tests, 166 pass, 1 fail, 0 skipped**. Set LISAN_BACKEND to backend so the existing background-review tests run. Existing test_loading_ui.js:44 expects flex but receives null. |
| Isolated unapplied activation proposal | 53 language tests, OK (2.042s). Python proposals parse and OpenAPI proposal parses as JSON/YAML. This is not full engine/browser/audio release validation. |

Final full Python failures/errors, preserved rather than hidden:

1. test_segment_writes.SegmentWrites.test_many_saves_at_once_never_fail_and_leave_no_temporary_files: Windows WinError5 during concurrent atomic replacement, also failed before.
2. test_repository_hygiene.BackupRulesTests.test_no_currently_tracked_backend_file_matches_an_ignore_rule: Git ls-files exits128 under sandbox checkout access, also errored before.
3. test_billing_review.BillingReviewTests.test_worker_restart_after_charge_before_job_save_does_not_charge_again: current protected longdub_service.confirm references clean_delivery_mode absent in that test's extracted namespace (NameError, line4050). It appeared during concurrent outside-scope edits; it is not a Dutch/Polish path and is not fixed here. Hand this to the developer doing delivery-mode integration.

Brief's earlier baseline was 1,436 tests / 2 failures / 1 error / 5 skipped. This checkout differs; do not present that old count as the measured baseline. Bundled Node was available and added to PATH. Final count is 36 higher than this run's actual baseline; 20 methods were added by Job17. The remaining 16-test difference coincides with concurrent checkout changes and is not fully attributed here. Do not claim a completely green suite.

Saved logs: job17-baseline-language-tests.log, job17-baseline-full-tests.log, job17-language-tests.log, job17-final-python-tests.log, job17-final-node-tests.log, job17-proposed-language-tests.log, job17-dry-trial.json. Full-suite invocation was `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`; a writable test-temp directory, bundled Node PATH and LISAN_BACKEND were set. No real API job submitted. A Windows launcher later failed with MXC volume D: error87; file tools completed packaging from the already saved results.

## Complete confusion matrix — measured here, not speech accuracy

| Actual source (20 each) | en | es | fr | pt | it | de | nl | pl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| en | 20 | 0 | 0 | 0 | 5 | 0 | 0 | 0 |
| es | 20 | 20 | 11 | 4 | 8 | 0 | 0 | 0 |
| fr | 20 | 10 | 20 | 6 | 17 | 0 | 0 | 0 |
| pt | 20 | 14 | 6 | 20 | 9 | 0 | 0 | 0 |
| it | 20 | 9 | 8 | 3 | 20 | 0 | 0 | 0 |
| de | 20 | 0 | 0 | 0 | 0 | 20 | 0 | 0 |
| nl | 20 | 0 | 0 | 0 | 0 | 3 | 20 | 0 |
| pl | 20 | 1 | 1 | 4 | 6 | 0 | 0 | 20 |

English false matches: **140**, all 20 paragraphs for every non-English source. These are explicitly recorded findings and **not test failures**. Every false match, including other old checks, appears in the complete table `docs/languages/job17-confusion.md` and machine-readable `job17-confusion.json`. Dutch/Polish checks themselves accept their own 20 paragraphs each and zero paragraphs from the other seven languages on this set.

The unchanged German check also accepts these Dutch paragraphs: nl_09, nl_11, nl_14. Dutch→English20 and Dutch→German3 therefore remain; Polish→English20 remains. Dutch→Polish0, Polish→Dutch0 and Polish→German0. Meeting the requested absolute cross-language rejection for English/German would require a separately approved change to legacy behavior. This delivery does not silently change those checks or alter fixtures to conceal matches. The six legacy-source screens are advisory; no probability or exclusive audio detector is claimed.

Short/names/numbers-only text is inconclusive for nl/pl; balanced mixed examples do not pass either. Strongly one-sided mixtures may pass a lexical screen, and uncommon vocabulary/very short valid Dutch or Polish may remain inconclusive. Test source wording and naturalness still need native check. No voter or real-world quality ranking is inferred.

## Inventory and wiring for Claude

| Category | Locations measured here |
| --- | --- |
| API | 9 |
| UI text | 23 |
| engine | 74 |
| help | 5 |
| legal | 2 |
| prompt | 16 |
| test | 49 |

Total: 178 locations; exact file:line/current/action/safety/test rows in inventory.md and full JSON. All production registry consumers were read. Current generic non-English engine branches need no source-specific code patch; they must keep the enabled get() lookup. New metadata cannot leak via public supported/is_supported/get/names. Saved old projects still default to English; differently-cased valid codes normalise; unknown/garbage/staged inputs cannot authorise new jobs today.

Apply order in an isolated integration checkout, then release together:

1. Review `01-api-and-interface.patch`: 9 target files (shared source entries, API enums/docs including mismatch output, assistant/help/copy/landing/pricing/badges). Arabic drafts and four-badge-per-side layout need approval/visual checks. Don't publish lists before jobs are enabled.
2. Review `02-new-source-safety.patch`: new-only inconclusive text warning, subtitle lexical mismatch check, Unicode Latin counting for nl/pl in long-upload subtitle precheck. English and five other paths unchanged. Short/ambiguous wrong-language subtitles remain a documented residual limitation; no invented certainty or arbitrary name rejection.
3. Run the eight-source lifecycle/correction/mismatch/subtitle/API/UI matrix and unchanged-output tests. Check consented audio/native output separately. Apply `03-activate-last.patch` only after those checks and Ali's approval: one ENABLED_LANGUAGES line plus production-count and stage-state test updates. NEW_LANGUAGES/default trial set unchanged. Version belongs to Claude.

Exact patch targets/current hashes/literal replacements are in `job17-wiring/manifest.json`; three unified patch files are **UNAPPLIED**. No actual main/API/page/voice/audio/recognition/gender/line/subtitle engine file was edited by Job17. The following existing files changed concurrently and were retained as-is:

assistant_service.py, config.py, dub_long.html, help.html, inworld_service.py, longdub_edits.py, longdub_service.py, main.py.

This means the whole checkout is not identical to the initial snapshot outside our scope. Four allowed implementation files were separately verified equal to our installed candidates. Do not copy old protected files over concurrent work. Recheck patch before-hashes after merging. No config, credit/price/clone/storage/SQL changes belong to this batch.

## Native checks still needed

- Dutch: invented source naturalness (including Belgian usage), all 25 Arabic proposals/regexes, exact m.i. abbreviation and subtitle frequency/coverage, clitic candidates, sound-description filters, IJ/name transliterations, mr. title rendering, contextual brutaal/gezellig/mooi meanings, formal/generic/plural address, compound/separable/subordinate-clause boundaries. Timing and copied-voice accent are unmeasured.
- Polish: invented source naturalness, all 25 Arabic proposals/regexes (including exact second-person tashkeel), candidate abbreviations not individually verified in council text, case/title dot ambiguity, name identity/transliterations, quoted past/gender, nonhuman/nonvirile plural, polite third-person address, sound-description coverage. No first-person Arabic verb gender distinction is invented. Timing and copied-voice accent are unmeasured.
- All Arabic customer drafts/labels in copy.md and proposal patches **need native check** by Ali. The source metadata marks candidates; cited recogniser benchmark numbers are not Lisan dubbing performance. Current runtime model override and all real new-language audio still need deployment verification.

## Safety status

Proven on captured inputs: six records identical, 2,754 complete text output hashes identical, 72 complete prompt/context outputs identical, original subtitle fixture clean/change outputs identical; deterministic spans lossless; staged public and short stored-code gates reject nl/pl. Documented: all production lookup consumers and unapplied targets, current hashes and concurrent changes. Estimated: timing ratio1.0, trial slots, curated lexical thresholds. Unmeasured/likely until release tests: recognition, translation and copied-voice quality, new UI layout, real complete-job integration. No live quality percentages, rates or native-accent claims.

## Exact Windows Command Prompt commands — no push

Files are already changed in the backend folder. These scoped commands include only Job17's 30 files and documentation proposals, preserving separately staged work. Check the listed handoff/results before committing. No config.py included, and no push command:

```cmd
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"

git --no-pager add -- languages.py lang_text.py language_prompts.py tools/language_trial.py tests/test_lang_staged.py tests/test_lang_confusion.py tests/test_lang_job17_legacy.py tests/fixtures/lang/nl/dialogue.json tests/fixtures/lang/nl/sample.srt tests/fixtures/lang/nl/sample.expected.json tests/fixtures/lang/pl/dialogue.json tests/fixtures/lang/pl/sample.srt tests/fixtures/lang/pl/sample.expected.json tests/fixtures/lang/job17_legacy_outputs.json tests/fixtures/lang/confusion.json tests/fixtures/lang_gold/nl.json tests/fixtures/lang_gold/pl.json docs/languages/sources.md docs/languages/inventory.md docs/languages/copy.md docs/languages/adding-a-language.md docs/languages/wiring.md docs/languages/job17-confusion.json docs/languages/job17-confusion.md docs/languages/job17-inventory.json docs/languages/job17-wiring/01-api-and-interface.patch docs/languages/job17-wiring/02-new-source-safety.patch docs/languages/job17-wiring/03-activate-last.patch docs/languages/job17-wiring/manifest.json Lisan-AI-Job-17-Handoff.md

git --no-pager commit --only -m "Stage Dutch and Polish language foundations (Job 17)" -- languages.py lang_text.py language_prompts.py tools/language_trial.py tests/test_lang_staged.py tests/test_lang_confusion.py tests/test_lang_job17_legacy.py tests/fixtures/lang/nl/dialogue.json tests/fixtures/lang/nl/sample.srt tests/fixtures/lang/nl/sample.expected.json tests/fixtures/lang/pl/dialogue.json tests/fixtures/lang/pl/sample.srt tests/fixtures/lang/pl/sample.expected.json tests/fixtures/lang/job17_legacy_outputs.json tests/fixtures/lang/confusion.json tests/fixtures/lang_gold/nl.json tests/fixtures/lang_gold/pl.json docs/languages/sources.md docs/languages/inventory.md docs/languages/copy.md docs/languages/adding-a-language.md docs/languages/wiring.md docs/languages/job17-confusion.json docs/languages/job17-confusion.md docs/languages/job17-inventory.json docs/languages/job17-wiring/01-api-and-interface.patch docs/languages/job17-wiring/02-new-source-safety.patch docs/languages/job17-wiring/03-activate-last.patch docs/languages/job17-wiring/manifest.json Lisan-AI-Job-17-Handoff.md

```

Optional offline rerun: `.venv\Scripts\python.exe tools\language_trial.py --languages nl pl --dry --report job17-dry-trial.json` (report is local test output, not a production asset).

Future explicitly approved live text trial only, supply real current numeric prices in place of placeholders; no live trial performed here:

```cmd
.venv\Scripts\python.exe tools\language_trial.py --languages nl pl --input-usd-per-million INPUT_PRICE --output-usd-per-million OUTPUT_PRICE --report job17-live-trial.json
```

Use existing secure local environment credentials; do not paste keys into chat. All returned Arabic requires listening/native review before any quality claim or enablement. No new unanswered product-choice question remains.
