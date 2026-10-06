# Lisan AI 1.82.1-rc1 — complete change handoff

Prepared 6 October 2026. Base: published commit `c921a29` (1.82.0-rc1), branch `main`. These changes are local and uncommitted. The owner will publish from Windows Command Prompt; do not push automatically. Railway deploys automatically after the owner's push. This remains a release candidate because the Fal music-quality discrepancy is unresolved.

## Requested behavior and implemented changes

### Long Dub and correction-page appearance

- Moved the existing Long Dub styles to `longdub_editor.css`, used by both pages alongside the main `styles.css`. The correction page now receives the same account UI-style setting from the server and uses the same light/dark and language preferences.
- Replaced the bilingual “Edit completed projects” link with a styled button displaying the selected language. Correction-page header navigation uses buttons; credits appear in the top bar. No header anchors remain.
- Help icons are centered 18 × 18 pixel circles. Paragraphs remain available through the existing hover/focus/click help controls.
- Each ten-line page button now has an adjacent review checkbox. Both editors expose the page controls at the top and bottom. Checking/unchecking persists; subsequent edits invalidate the corresponding review hash. The server keeps the existing default-reviewed behavior for older clients.
- Added `longdub_editor.js`: a ResizeObserver measures the wrapping account bar so sticky page controls stay below it. This fixes a browser-discovered collision where the header intercepted clicks on review controls.

### Completed-project correction editor

- Added original-editor actions: Tashkeel, retranslation, split at the original-text cursor, insert after, delete, speaker assignment, multiple style tags, style check by listening, and manual timing with an original-file player.
- Inserting/splitting displays a reminder to set both times manually. Newly created lines receive unique IDs and are selected for correction; splitting selects both resulting parts. Only the separate correction draft changes, never the original transcript/export.
- Structural actions use the existing Long Dub split/translation/diacritics helpers, preserve manually confirmed styles, validate ownership/line membership, reject active jobs, and save atomically after successful translation. Empty drafts stay empty instead of silently restoring original lines.
- The manual player supports seek, speed, ±1/0.1/0.01-second movement, current-time markers, range playback, Play/Pause, Save & Close and Discard. It can use retained original media/preview or a locally chosen original file. Choosing a local playback file does not upload it. The existing separate recovery upload remains available when required assets are missing.
- Style listening uses the correction row's current original-audio timings and the existing classifier. It does not silently replace the chosen style: the suggestion remains reviewable.
- Added real job progress with percentage/status and accessible progressbar markup. Completion shows actual recorded charges. Failed jobs show their terminal message. Balance-refresh failures cannot stop progress polling; transient polling failures retry without another paid submission.
- Fixed correction autosave retaining stale row objects: saving no longer breaks the next edit made in already-rendered controls. Timing reordering retains the focused row's page. Loading another project hides stale editor content while the request runs.
- Existing full-duration music/effects-plus-selected-voices and selected-voices-only downloads remain, now styled consistently. Original background and unselected voice gaps retain the prior 1.82.0 behavior.

### Price display and direct single-line generation

- Short “Generate Arabic audio” displays the numeric server quote, including unsettled analysis where applicable. Price requests are debounced, ignore stale responses, and refresh after text/voice changes or programmatic voice assignment. Invalid prices display a dash.
- Short full generation and line regeneration start directly after validating a current server quote. There is no price confirmation modal for those actions. Exact submitted text/voice choices are snapshotted, duplicate generation clicks are blocked, and the backend still validates accepted credits and balance.
- One selected correction line starts without a price dialog regardless of the amount. Multiple selected correction lines retain their maximum-cost confirmation. Merge and voice-release/delete confirmations remain.
- Short completed-generation progress now includes the actual confirmed debit; the client reports that amount. Null worker result metadata cannot turn an already-confirmed charge into an apparent generation error.

### One-second clean music references

- Lowered measured minimum clean music/context from three/two seconds to one second. This is an eligibility change, not a promise of generated quality. No removed speech is used as reference.
- Mask seam padding now preserves the minimum clean reference. For a one-second reference followed by silence until six seconds, the mask is exactly 1.0–6.0 instead of consuming the final 0.1 second of the reference.
- Existing clipping, silence, length, gain, seam, budget and payment checks remain. No clean-loop fallback was authorized or added. No production-wide prompt/model/output-format change was made based on an unsuccessful diagnostic.

## New or changed HTTP contracts

- Static no-cache assets: `/longdub_editor.css`, `/longdub_editor.js`.
- Existing owned review endpoint accepts `reviewed: false` to uncheck a page; omitted value retains true.
- `POST /api/longdub/{id}/corrections/line/{operation}`: insert, split, delete, retranslate, tashkeel. Returns a correction view plus new_id where relevant. Uses existing rate limiting and owned-project checks.
- `POST /api/longdub/{id}/corrections/player`: prepares/returns an owned original preview without reopening/mutating the completed parent's lifecycle state.
- `POST /api/longdub/{id}/corrections/emotion`: listens to original vocals for the edited row's times, bounded to 30 seconds, using the existing style classifier.
- Correction view adds `can_play`; draft edits accept explicit `emotion_set` and `manual_time` flags. No dependency or database-schema migration was added.

## Validation and its limits

- 35 existing/extended Python short-dub tests; 54 Python revision tests; 17 short UI Node tests; 4 correction UI Node tests: all 110 passed. The accompanying final validation log records execution results.
- Python source parsing, changed JavaScript and inline page-script syntax checks are included. Tests use isolated state, mocked customer billing/providers and real FFmpeg where appropriate; they do not import/start the production main application.
- A localhost browser fixture served the actual changed page assets with 23 transcript lines and mocked account/provider endpoints. Verified top/bottom review check/uncheck, page navigation and saved text, edits after autosave, Tashkeel/retranslation/insert controls, new-line reminder/selection, manual timing save, direct single-line submission, actual mock charge, 100% progress, multi-line cancellation, Arabic RTL, visible header credits, no header links, 18px centered help and localized completed-project navigation.
- Browser fixture verification is not live paid end-to-end validation of every provider action. Tashkeel/split translation failures, foreign ownership, active jobs and original-draft isolation are covered by isolated Python tests.
- Read-only review additionally found/fixed missing audio Play/Pause, balance refresh preventing polling, and price updates occurring before voice selection had changed. Regression tests cover these cases.

## Fal discrepancy — unresolved

The owner reports an acceptable result in the Fal playground from the supplied six-second diagnostic input and a 1–6-second mask. Their prompt described a one-second C4 tone followed by five seconds of silence. The supplied reference was constructed diagnostic audio rather than music from the owner's video.

Three successful provider calls for this revision were inspected: original generic prompt/WAV with 0.9–6 mask (21.22% full-scale samples), more specific sustained-tone continuation/WAV with 1–6 mask (21.37%), and the owner's exact description with documented playground defaults/MP3 (11.27% after PCM decoding; seed 1799341628). All exceeded the existing 10% clipping guard. One additional attempt stopped at connection setup before the successful retry. Customer billing/database operations were not started by these diagnostics. No valid preview is claimed.

The returned WAV itself is PCM16 and already clipped; this is not evidence of a floating-point-to-PCM conversion overflow in the site. The MP3 reproduction used the documented defaults rather than a captured request from the owner's actual successful run. Random seed and any unreported playground settings may differ. The owner's acceptable result must not be dismissed based on these separate runs. Compare their actual output/request settings before claiming a cause or weakening the guard. A prompt suggestion alone does not establish a quality fix.

The accompanying reproduction notes, input/reference WAVs and JSON reports document the comparison. No provider support message was sent, no provider was substituted and no random parameter sweep was performed. Existing music-preserving exports can still stop if required repairs fail.

## Exact source inventory

Modified: `app.js`, `config.py`, `dub_long.html`, `dub_long_edit.html`, `dub_long_edit.js`, `longdub_edits.py`, `main.py`, `music_fill.py`, `tests/test_dub_revision.py`, `tests/test_shortdub_ui.js`, `tests/test_shortdub_upgrade.py`.

Added: `longdub_editor.css`, `longdub_editor.js`, `tests/test_correction_ui.js`, `UPGRADE_1.82.1.md`.

`config.APP_VERSION` is `1.82.1-rc1`. Secrets, demo-media deletions, unrelated backup directories and other pre-existing untracked files are excluded. No commit, push or deployment was performed for this revision. Do not use `git add .` or `git add -A` for publication. The accompanying SHA-256 inventory identifies the reviewed file bytes.

## Suggested post-deploy smoke checks

1. Refresh both editors; check the selected language, theme, top-bar balance and help layout.
2. Review a ten-line group from its bottom checkbox; navigate away/back; edit a line and confirm its check clears.
3. On a small completed project, insert/split, set timing manually, edit/style/Tashkeel/retranslate, then reload and confirm saved draft changes.
4. Generate one selected correction line; confirm no payment modal, actual charge notification and progress. Cancel a multi-line quote and confirm no request/charge.
5. In short dub, assign/change voices and edit Arabic text; verify the numeric price refreshes. Generate and regenerate with the expected actual charge notification.
6. Keep music-quality validation separate: compare a real successful Fal output/request and listen to real short/long repairs before declaring inpainting resolved.
