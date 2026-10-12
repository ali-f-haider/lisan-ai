# Explicit original-speech removal — 12 October 2026

This batch adds **Remove speech / إزالة الكلام** to the short editor, the long editor and the completed-project correction editor. It lets a person mark an original word that survived transcription/separation without changing the Arabic line's placement. Changing Arabic times by itself does not request background removal. The saved marks take effect on the next generation, merge or correction export.

The changes are already applied in `backend`. No commit, push, deployment, version change, SQL migration or new dependency was made. The diff compares against the exact files at the start of this batch, rather than claiming a Git commit as its base.

## How Ali can test it

1. Open an existing short or long project. Press **Remove speech** on a line near an unwanted original word.
2. In the original player, set Start and End around that word. Use **Play start to end** to check the selection. Press **Remove**.
3. Confirm that the Arabic start/end and both text boxes stayed unchanged. A notice explains that removal is saved for the next export.
4. For short dub, merge again. For an unfinished long project, generate normally. For a completed long project, open its correction editor and leave all voice lines unselected: the button becomes **Export background**.
5. A background-only export is the full-length cleaned music/effects layer. It does not re-speak or re-clone anyone. Its download says **Cleaned background**.
6. Reopen Remove speech. Press the × beside a saved time range to undo that range. Overlapping marks remain individually undoable. Export again to hear the change; files already exported are unchanged.
7. Check that Cancel and Escape do not save a new range, and that invalid times cannot silently substitute the line's original selection.

For an older project whose missing background was recovered with removal marks already applied, changing/undoing those marks may require attaching its original file again. The quote refuses before charging if that original is unavailable, and the editor shows the recovery controls. This prevents a misleading undo that would keep using a background with the old marks baked into it.

## Pricing and saved data

- Saving, selecting and undoing marks cost zero credits. Local assembly for a project with an explicit cleanup record, including an emptied record after undo, adds no assembly fee. Ordinary jobs without such a record retain their existing assembly fee.
- Background-only correction exports charge no voices, cloning or local assembly. Optional paid music repair still uses the existing quoted maximum and successful-repair billing. If it has a positive price, the first click shows it on the button; a second click starts the matching quote. A free export starts directly.
- Selecting voice lines still incurs their existing speech/cloning costs. A background-only export does not extend cloned-voice retention.
- Marks are stored in the job's `speech_removals.json`: version 1, `ranges: [[start, end], ...]`. There are at most 256 finite, positive, millisecond-sized ranges within the source duration. Exact duplicate ranges are deduplicated; overlaps remain separate for undo.
- A SHA-256 revision protects each save from a stale tab. A damaged record blocks export instead of silently dropping the person's cleanup selections. Old projects default to no explicit marks.
- Short project files and browser session saves include marks only once their state is known. Opening/resaving an old file without this field does not manufacture an empty snapshot that clears existing server marks.
- Completed corrections cache a cleaned background only against matching marks, a matching original asset, and a completed correction job owned by the same user and belonging to that parent. An already recovered background with identical marks is reused too. Missing/deleted caches trigger a fresh quote.
- Storage reservations include the extra reusable background asset; the existing owned-output naming and expiry rules cover it.

## Changes by file and function

| File | Functions or area changed | Purpose |
| --- | --- | --- |
| `app.js` | `createRow`, `openShortSpeechRemoval` (new), `saveProject`, `loadProjectFile`, `saveWs`, `applyRestore`, `mergeVideo`, `validMergeQuote`, language dictionary | Small row button; original source playback; separate removal records; project/session persistence; synchronize marks before merge and submit their revision; accept a zero-credit cleanup quote. |
| `index.html` | Shared CSS/script includes | Load the shared player and removal module for short dubbing. |
| `dub_long.html` | `openPlayer`, row-building controls, shared includes | Reuse the existing original player in a separate removal mode; preserve the timing editor's original behavior. Defaults use saved source bounds where available. |
| `dub_long_edit.html` | Shared CSS/script includes | Load the removal controls on completed projects. |
| `dub_long_edit.js` | `count`, `openPlayer`, correction-button handler, `renderHistory`, row controls | Removal mode; separate state/notification; background-only export when no voice lines are selected; show any paid repair price first; refresh recovery availability; clear background-only download label. |
| `longdub_player.js` | `open`, time-input handlers, `applyAndClose`, `renderSaved` (new), discard/Escape handlers | Removal-specific title, one short instruction, Remove/Cancel, saved-range undo. Escape cancels only this new mode. Invalid typed marks stay visible and block saving; final values are read at click time. Ordinary timing Escape retains its earlier save behavior. |
| `speech_removal.js` (new) | `copy`, `request`, `load`, `open`, `button`, `remember`, `projectData`, `saved` | One shared save/undo/persistence implementation. Uses same-origin credentials, revision checks and project-switch guards; Arabic save failures remain Arabic. |
| `speech_removal.css` (new) | Scoped `.lisan-remove` rules | Match existing theme variables and button classes; compact saved-range list; mobile layout. Ordinary controls are outside the scope. |
| `main.py` | `SpeechRemovalEdits`, short/long GET and PUT routes, script/style routes, `_short_removal_location` | Owned, validated metadata edits; short operation guard and long job lock; refuse running jobs/exports; no generation or debit during saving. |
| `main.py` | `MergeRequest`, `_short_merge_price`, `merge_video`, `_merge_video_run`, `_ld_public_rows` | Quote and verify removal revision before billing; allow free cleanup assembly; keep legacy quote fallback; expose original source bounds to the long player. Existing reaction-map consumers receive the same union of source-speech and explicit removal spans. |
| `speech_removal.py` (new) | `validate`, `revision`, `read`, `write`, `SpeechSpans`, `forced`, `clipped`, `signature`, `asset_signature`, `splice_background` | Strict region records and atomic saves; carry mandatory/reference masks through existing list consumers; cache fingerprints; bounded, full-length replacement of selected background sections. |
| `longdub_service.py` | `set_line_time`, `redo_project`, `music_quote` | Preserve original bounds before the first normal alignment edit; copy removal metadata on redo; accept an explicit removal override when quoting recovered assets. |
| `dub_background.py` | `speech_spans`, `count_repairs`, `checkpointed_prepare`, `removal_quote` (new) | Use original source bounds rather than Arabic placement; merge explicit selections; quote the same free ambience preflight as export; fingerprint cleanup checkpoints so an old completed/paid repair cannot hide new selections. |
| `bg_duck.py` | `mute_speech`, `keep_music` | Explicit selections bypass automatic “not needed” decisions and minimum-length thresholds. Their background gain is zero before restoration, even where music preservation would otherwise keep the window. Supports brief selections/short sources. |
| `pause_bed.py` | `speech_frames`, `bed_floor` | Original-pause restoration cannot bring marked speech back. When cleaning an existing background without a vocal stem, known speech is excluded from references; trusted same-file background restoration is narrowly permitted. Raw original recordings still require vocal evidence. |
| `section_background.py` | `_spans` | Carry forced/reference spans correctly when clipping sections and shifting their times. |
| `longdub_edits.py` | `view`, `output_budget`, `quote`, `start`, `run`, `removal_background` (new) | Owned background-only exports; zero-debit path; cleanup/cache/storage accounting; no needless clone retention; safe recovered-background undo; source cleanup independent of Arabic alignment. Legacy queued plans keep their earlier quiet-filter behavior. |
| `tests/test_billing_review.py` | Assembly-quote fixture only | Supply the new real temporary retained background and work-folder context. Every existing billing safety assertion stays unchanged. |
| `tests/test_dub_revision.py` | Corrected-file timing test | The old assertion required about 20 dB of lowering merely because an Arabic line gained time. The new assertion requires that background to stay level; explicit-removal tests cover actual source cleanup separately. Legacy quiet-filter tests remain. |
| `tests/test_speech_removal.py` (new) | 29 tests | Region validity, ownership, save/undo, source/output separation, short clips, stale quotes, cache ownership, recovered undo, zero debits, no unwanted voices/clones, memory-sized masks and real audio output. Also runs the shared JavaScript tests when Node is available. |
| `tests/test_speech_removal_ui.js` (new) | 18 tests | Actual shared-module/dialog/export handlers with mocked browser objects: save, undo, stale revisions, project switches, project snapshots, both languages, Escape/Cancel, invalid/unblurred marks and price-before-paid-export behavior. |

## Audio behavior and its limits

This feature tells the existing cleanup engine exactly where the person heard unwanted original speech. It does not guess that intent from a timing change, invent a new separator or add a new audio provider. The mandatory speech mask overrides keep-music decisions and original-pause restoration. Existing free ambience restoration runs first; existing paid music repair is quoted if needed. A section that cannot be restored safely can remain quiet under the existing fallback policy.

For an existing completed background, the engine prepares cleanup and then splices it into only the selected sections, using the normal 0.10-second leading/0.30-second trailing speech margins and 0.08-second outer fades. Samples elsewhere are untouched in the intermediate PCM output; the final audio export is encoded normally. The duration stays unchanged. Replacement uses one-second buffers; an hour-long frame mask is 360,000 bytes. This is a bounded-memory check, not a measured one-hour full-workflow peak.

The range must cover the whole unwanted word. The app cannot guarantee removal of a word outside the person's selection. Existing already-exported files are not rewritten.

## Verification

- Required full command run: `python -m unittest discover -s tests -v`. Last full run: **1,366 tests**, **1 failure**, **1 error**, **1 skip**, about **662 seconds**. The failure/error are the same two Windows checks seen before this batch: concurrent `segments.json` replacement returns `WinError 5`, and the repository hygiene test's Git process cannot access the parent checkout. These were not hidden or altered.
- After the last backend changes, refreshed affected checks: **116 tests**, **OK**, with the existing opt-in browser test skipped. This covered removal, billing review, correction exports and the shared timing dialog.
- Final new-feature run: **29 Python tests passed**. Final JavaScript run: **18 tests passed**. Python syntax and **9 JavaScript scripts**, including inline page scripts, passed. Byte audit confirms existing newline styles and unchanged `config.py`.
- Real synthetic audio tests prove zero selected carrier after mandatory mute, no restoration of that carrier through the complete free cleanup pipeline, unchanged duration, and exact PCM samples outside the splice/fades.
- No live project, provider call, paid credit consumption or customer data upload was used. Visual browser QA could not run: the Windows sandbox closed the separate browser and blocked the local preview connection. The dialog handlers were tested directly; rendered appearance and a live Railway export still need a normal-browser check.

## Publishing from Command Prompt

The code is already in the project folder. Inspect the diff before committing if you have other pending work: Git commits the current complete versions of the listed files, including any earlier uncommitted edits inside them. The commands below exclude `config.py` and all unrelated paths. They were provided for Ali, not executed by the assistant.

The companion `Speech-removal-feature.diff` contains the complete batch diff. `Speech-removal-feature.zip` contains the changed files, including this report, under `backend/`.

```bat
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"
git --no-pager diff --check -- SPEECH_REMOVAL_FEATURE.md app.js bg_duck.py dub_background.py dub_long.html dub_long_edit.html dub_long_edit.js index.html longdub_edits.py longdub_player.js longdub_service.py main.py pause_bed.py section_background.py speech_removal.css speech_removal.js speech_removal.py tests/test_billing_review.py tests/test_dub_revision.py tests/test_speech_removal.py tests/test_speech_removal_ui.js
git --no-pager add -- SPEECH_REMOVAL_FEATURE.md app.js bg_duck.py dub_background.py dub_long.html dub_long_edit.html dub_long_edit.js index.html longdub_edits.py longdub_player.js longdub_service.py main.py pause_bed.py section_background.py speech_removal.css speech_removal.js speech_removal.py tests/test_billing_review.py tests/test_dub_revision.py tests/test_speech_removal.py tests/test_speech_removal_ui.js
git --no-pager commit --only -m "Add explicit source-speech removal to dubbing editors" -- SPEECH_REMOVAL_FEATURE.md app.js bg_duck.py dub_background.py dub_long.html dub_long_edit.html dub_long_edit.js index.html longdub_edits.py longdub_player.js longdub_service.py main.py pause_bed.py section_background.py speech_removal.css speech_removal.js speech_removal.py tests/test_billing_review.py tests/test_dub_revision.py tests/test_speech_removal.py tests/test_speech_removal_ui.js
git --no-pager push origin main
```
