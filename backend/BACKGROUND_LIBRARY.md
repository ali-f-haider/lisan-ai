# Lisan AI: recorded ambience and sound sections

Prepared for Ali on 11 October 2026. The changes are saved in the local project. No commit or push was made; `config.py` is unchanged. This batch supersedes the earlier unpublished short-grain ambience implementation and includes the previously delivered, unpublished Step 1 layout changes.

## What changed

The engine keeps surviving background sound, extends adequate clean ambience from the same sound section, and can select an approved recorded bed when only a short measurement reference remains. It compares the existing scene context with broad frequency balance and stereo correlation. A single fraction-of-a-second reference is no longer repeated as a bed. Library tracks play continuously before looping, with a retained, energy-compensated crossfade. Mixing adds only the measured missing background under the speech mask; clean pauses and surviving louder effects are retained. This is replacement ambience, not recovery of the exact original recording.

Acoustic changes in clean pauses now split reference pools even inside a single visual scene. Each music repair receives its own section audio, reference, description and local times. Quotes run free ambience restoration before counting paid music holes. Local matching, library playback and looping add zero credits. Existing merge prices and successful music-repair billing remain in place. Section callbacks convert back to original timeline times before billing. Job-wide request and wall-time limits remain enforced by the wrapper and existing approval callback.

Short and long results show a collapsible, English/Arabic background report distinguishing extended original ambience, replacement ambience, restored music and sections needing review. The panel never displays provider names or raw diagnostic messages. The short merge notice no longer says that every background region is silent when an ambience bed was actually restored. Correction exports keep the original completed background and its report; when assets need recovery, they use the shared engine with the original recording. Long-job checkpoints reuse the completed bed and metadata.

The Step 1 redesign included here puts the source-language field above the upload actions, uses native matching upload/load buttons, and keeps language selection independent of the file picker. Its detailed prior handoff is `Step1-redesign-handoff.md` in the workspace outputs.

## Library

Eight recordings are installed in `backend/ambience_assets`, using approximately 47.2 MiB of audio. Every installed recording is under four minutes; the import used a 600-second limit, so a longer source would be trimmed to ten minutes. The two indoor recordings retain the lossless storage of their public MP3 preview audio; converting a preview to FLAC does not turn it into an original lossless recording. Other assets use compact Ogg Vorbis. Original and stored formats are recorded honestly in the catalogue.

| ID | Environment / characteristics | Stored length | Automatic selection |
|---|---|---:|---|
| indoor_hallway | Empty indoor hallway, medium/large rooms; frequency balance, level and width measured | 50.90 s | Approved by Ali's listening check |
| large_studio | Empty indoor studio/large room; quiet ventilation/hum character | 157.00 s | Approved by Ali's listening check |
| quiet_home | Domestic interior candidate; publisher description and automated audit disagree | 106.07 s | Disabled pending review |
| wind_trees | Outdoor trees/wind candidate; automated description uncertain | 218.44 s | Disabled pending review |
| forest | Outdoor birds/insects; identifiable animal events | 54.35 s | Disabled pending review |
| rain_concrete | Continuous rain on concrete | 80.81 s | Enabled after full-recording automated audit; requires a matching outdoor cue and acoustic reference |
| sea | Continuous small waves | 137.64 s | Enabled after full-recording automated audit; requires a matching outdoor cue and acoustic reference |
| traffic | Distant urban background candidate; possible voices/events reported by audit | 80.83 s | Disabled pending review |

An automated audit is not a guarantee that a recording contains no speech or music. Its disagreement on the indoor candidates is retained in the manifest, alongside Ali's explicit listening approval. Automatic selection excludes unapproved entries. The source descriptions, semantic tags, occupancy/perspective, measured RMS/peak/level range/frequency bands/stereo correlation, duration, format, loop fade, source URLs, authors, licence and SHA-256 checksums are all stored in `catalogue.json` for later curation.

The six BigSoundBank recordings are individually identified as CC0. Its [published licence](https://bigsoundbank.com/licenses.html) permits commercial use, adaptation and redistribution. The two individually CC0 Freesound sources are [Empty Office Hallway](https://freesound.org/people/Sheyvan/sounds/570015/) and [Large Empty Studio](https://freesound.org/people/Sheyvan/sounds/540222/). Public preview downloads were used for these two; no account restriction was bypassed. Public downloads worked, so Ali's BigSoundBank account was not needed. Source attribution records are retained although CC0 does not require attribution.

## Exact files and functions

| File | Change |
|---|---|
| `ambience_library.py` (new) | `characteristics`, `inspect`, `catalogue`, `asset_path`, `select`, `LoopReader`: validate assets and checksums, match conservative candidates, read at most ten seconds at once and compensate overlap energy. |
| `ambience_match.py` (previously unpublished, revised) | `cached_scenes` shares short quote/generation cache lookup; `regions` splits acoustic changes within visual scenes; `Pieces.take` compensates overlap; `restore` prohibits tiny-grain repetition, selects approved beds with original measured levels and records outcomes. |
| `section_background.py` (new) | `local_bed`, `bounds`, `_clip`, `_spans`, `level_reference`, `count`, `fill`: shared free preflight and section-scoped music handling. PCM sections seek directly with bounded copies instead of repeatedly decoding a long recording from the beginning. A temporary loudness guide prevents the separator's silent reference from erasing the restored bed during level matching. |
| `pause_bed.py` | Includes the earlier unpublished `bed_floor(..., scenes=None)` hook into `ambience_match.restore`, required by this batch. No other audio redesign was made here. |
| `dub_background.py` | `count_repairs` includes original/vocal/context preflight and section quotes; `prepare` performs local ambience before music repair and carries its report; avoids mixing the same bed twice or applying the speech level reduction twice. Fixes a nearby dictionary being overwritten by a string on incomplete music repair, which would otherwise break the new result path. Existing `checkpointed_prepare` caches the new metadata without changing its payment/restart guard. |
| `main.py` | `_short_merge_price` passes original/vocal files to preflight; `_merge_video_run` returns the background report; `background_review_js` serves the shared panel script. |
| `longdub_service.py` | `music_quote` uses the same free preflight; `_run_dubbing` retains background outcomes in the completed public result. Temporary reconstructed beds and their loudness guides are removed by normal job cleanup. No voice generation or timing logic changed. |
| `longdub_edits.py` | `run` supplies the original recording during background recovery and carries the reused/recovered background report into correction results. |
| `background_review.js` (new) | Shared neutral, bilingual, safe renderer of section outcomes; ignores invalid times and never inserts raw provider/error strings. |
| `app.js` | Adds the background-details panel to successful merges; updates background notices and quote wording. Also includes the previous Step 1 `sourceControls()` mounting change. |
| `dub_long.html` | Loads the panel script and renders completed background details. |
| `dub_long_edit.js`, `dub_long_edit.html` | Load/render completed correction background details. |
| `index.html`, `styles.css` | Include the unpublished Step 1 layout work; index also loads the shared background script. |
| `tests/test_ambience_library.py` (new) | Fourteen library, streaming, matching, scoping and money-safety regressions. |
| `tests/test_scene_ambience.py` | Eighteen tests; replaces the previous expectation that a tiny grain should fill a minute with a regression forbidding that repetition. Positive scene-isolation fixtures now supply adequate clean donor material. |
| `tests/test_background_review.js` (new) | Three English/Arabic/invalid-data panel checks. |
| `tests/test_source_language_ui.js`, `tests/test_step1_layout.py` | Include the previously delivered Step 1 regression checks. |
| `ambience_assets/` | Audio, catalogue and source/licence notes. |
| `BACKGROUND_LIBRARY.md` | This handoff. |

Existing file endings are preserved. New files use LF. No reformatting of the existing audio pipeline was performed.

## Verification

- Fourteen new library/section integration tests passed, including a complete 3,600-second loop stream, block-size-independent output, correlated seam level, checksums/path containment, conservative rejection, one visual scene with a sound change, exact PCM section samples, independent music contexts, global billing times, the same short-job context in quote/generation, and no paid provider call or charge for a free library repair.
- Eighteen scene-ambience tests cover original pauses, surviving effects, source masks, changing levels, scene boundaries and short-reference rejection.
- All 42 selected JavaScript regressions passed, including the three new background-panel checks and the existing short-dub/source-language cases.
- Actual pages/scripts loaded in a headless browser with mocked account/API responses. Background details were checked on short, long and correction pages in English and Arabic, with no page-script errors. This is local UI testing, not a fresh live paid dub.
- A private courtroom regression preview uses the supplied audio, its subtitle speech mask and its known room context. The revised matcher chooses `indoor_hallway`, measures the 0.43-second original reference, and restores 60.37 seconds of missing bed between 1.50 and 63.47 seconds. It uses about 1.22 long library cycles instead of approximately 3.1 tiny-grain repetitions per second. This preview deliberately mutes speech from the supplied recording; it does not certify a complete separation/dubbing/mux run.
- The revised first-minute audio and bilingual panel screenshots are in the workspace outputs, outside the application repository.

The final full `python -m unittest discover -s tests` run reported:

```text
Ran 1300 tests in 252.014s

FAILED (failures=1, errors=1, skipped=1)
```

The remaining failing checks are:

- `test_no_currently_tracked_backend_file_matches_an_ignore_rule (test_repository_hygiene.BackupRulesTests.test_no_currently_tracked_backend_file_matches_an_ignore_rule)`: Git cannot change into the parent checkout through this sandbox.
- `test_many_saves_at_once_never_fail_and_leave_no_temporary_files (test_segment_writes.SegmentWrites.test_many_saves_at_once_never_fail_and_leave_no_temporary_files)`: Windows access-denied during concurrent file replacement.

These files were not changed in this batch. The same conditions appeared before the final audio fixes. The full suite is therefore **not green** here; rerun it in normal Command Prompt before publishing. All fourteen new ambience tests and the five existing bed-floor tests pass individually.

## Practical limits

This is a conservative first implementation. Acoustic boundaries are estimates from clean pauses and cached visual context, not a validated universal music/ambience/effects classifier. It can miss a change hidden throughout continuous speech. A scene label alone is insufficient: without clean level/spectral evidence, a suitable approved asset, or reliable speech masks, it leaves the unresolved part for review. Intentional silence does not receive an invented bed. It does not manufacture missing footsteps, impacts or conversations, recover every musical hole, or guarantee inaudible repetition for every soundtrack.

The one-hour streaming test demonstrates bounded library reads and exact duration. Full Railway workflow peak RAM for a one-hour video has not been measured for this batch. The existing separator, fitting and mixing pipelines still have their own memory requirements. The background panel is a preview/review report; per-section manual preset switching and a universal trained sound classifier are not delivered here. Four stored candidates remain disabled until reviewed.

Retained long-dub background/checkpoints are reused for corrections. An already completed old project will keep its old background; publishing this code does not retroactively regenerate it. Validate a new dub or a genuine background-recovery path to hear the change.

## How Ali can test it

After deploying, start a new project with a room scene that has dialogue and at least a short clean pause. Keep the background in the export, then listen to dialogue and pauses together: the bed should stay quiet and continuous without the earlier rapid propeller repetition. Open the background-details panel to see whether the original was extended, a recorded replacement was used, or a section needs review. Test a second clip where music starts later; the early room bed must not borrow that later music. Also test intentional silence, surviving effects and an unknown setting: the engine must not invent an atmosphere without evidence. For correction exports, confirm that the completed background remains consistent. Existing fixed merge fees still apply; free local ambience restoration adds no new credits.

## Delivery and commands

All source changes and assets are already in Ali's project folder. The ZIP contains full changed files. The unified diff covers text files; audio binaries are in the ZIP and local working folder. It combines this batch with the earlier unpublished Step 1 and ambience-hook work, so do not apply both the old ambience patch and this patch separately. The comparison snapshots are documented in the package; a Git diff could not be produced through this Windows sandbox. Review selected files in normal Command Prompt before committing if other local work exists in them.

Run these yourself in Command Prompt. `commit --only` limits this commit to the listed batch, excluding unrelated staged files. It does not include `config.py`.

```bat
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"

git --no-pager add -- ambience_assets ambience_library.py ambience_match.py section_background.py dub_background.py pause_bed.py main.py longdub_service.py longdub_edits.py background_review.js app.js index.html styles.css dub_long.html dub_long_edit.html dub_long_edit.js tests/test_ambience_library.py tests/test_scene_ambience.py tests/test_background_review.js tests/test_source_language_ui.js tests/test_step1_layout.py BACKGROUND_LIBRARY.md

git --no-pager commit --only -m "Add recorded ambience matching and section-scoped background repair" -- ambience_assets ambience_library.py ambience_match.py section_background.py dub_background.py pause_bed.py main.py longdub_service.py longdub_edits.py background_review.js app.js index.html styles.css dub_long.html dub_long_edit.html dub_long_edit.js tests/test_ambience_library.py tests/test_scene_ambience.py tests/test_background_review.js tests/test_source_language_ui.js tests/test_step1_layout.py BACKGROUND_LIBRARY.md

git --no-pager push origin main
```

For a local check first, run `python -m unittest discover -s tests` from `backend` with the project's Python environment active. There is no SQL migration or new secret to install. Nothing was deployed by Codex.
