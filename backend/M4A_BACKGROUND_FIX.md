# Saved-background M4A compatibility fix — 12 October 2026

Ali reported that Remove speech failed on the completed Milch & Zucker project with `Error opening ..._final_effects.m4a: Format not recognised`. The feature passed a saved AAC/M4A background into `section_background.level_reference()`, which opened it directly through SoundFile. On this host that reader cannot open AAC/M4A, even when the file is valid. A valid generated M4A reproduced the same error in the complete free cleanup pipeline. This was a gap in the speech-removal implementation, rather than evidence of a failed speech-generation request.

## Changes

- **`section_background.py`: new `_pcm_reference(path, work)` context manager.** A reference that SoundFile can read at the required sample rate/channel count is reused. Unsupported formats or references requiring sample-rate/channel conversion are decoded by the existing FFmpeg runtime into a private temporary stereo, 44.1 kHz, PCM WAV. Conversion uses disk and the existing reader processes ten-second blocks; it does not load a whole video into RAM. The temporary directory is removed on success or failure. The saved source is never overwritten. An unreadable reference gives a plain message instead of exposing a server path.
- **`section_background.py`: `level_reference()`.** Both inputs now enter the compatibility context before SoundFile opens them. ExitStack closes readers before temporary files are removed, including on Windows. Existing duration checks and every loudness/mask calculation remain unchanged.
- **`tests/test_background_reference_formats.py`: five new regression tests.** Real AAC/M4A fixtures test exact equivalence with manually decoded PCM, the unchanged WAV path without conversion, cleanup on duration mismatch, a plain error for genuinely unreadable audio, and a complete free Remove speech preparation from a saved M4A. The full-pipeline test checks that ambience restoration actually ran, duration stays unchanged, and the selected synthetic speech carrier does not return.

The fix is general across background files; it contains no project ID, clip name, language-specific branch or special timestamp. There are no new provider requests or credit charges. No audio generation, billing, UI, protected pipeline files, dependencies, SQL or version settings were changed. `config.py` is byte-for-byte unchanged. No commit, push or deployment was performed.

## Verification

Before the fix, the five new tests produced four errors, including the exact M4A format error through both the reference helper and the complete cleanup path. After the fix all five pass. Supported WAV references produce the same result without conversion. M4A reference output is sample-identical to the manually decoded reference output. The tests confirm the original M4A bytes remain unchanged and no conversion scratch directory remains.

Required full check: `python -m unittest discover -s tests -v` ran **1,373 tests in 280.785 seconds**: 1,370 passed, one skipped, one failure and one error. The five new tests all pass. The remaining two are the same previously observed unrelated local limitations:

- `test_segment_writes.SegmentWrites.test_many_saves_at_once_never_fail_and_leave_no_temporary_files`: Windows `WinError 5` during concurrent replacement of `segments.json`.
- `test_repository_hygiene.BackupRulesTests.test_no_currently_tracked_backend_file_matches_an_ignore_rule`: the sandbox cannot inspect the parent Git checkout (`git ls-files` exits 128).

Python syntax, preserved LF line endings and the unchanged `config.py` byte comparison also passed. The full suite is therefore **not completely green** on this local host; no unrelated code or tests were changed to hide those results.

No live provider calls, paid tests or uploads were used. These tests reproduce the format problem locally; the repaired code must still be pushed by Ali before it affects Railway.

## Retry after deployment

Reopen the completed project after Railway finishes deployment, keep the saved removal regions and retry the background export. Leave voice lines unselected if the purpose is only removing original speech. That avoids regenerating the voices; any existing paid music-repair component still follows the displayed quote. The failed job remains in history and a retry creates a new attempt under the existing failure/retry rules.

## Delivery

The code is already applied in Ali's backend folder. This is a follow-up fix to the Remove speech batch; use this new diff or these new files after that batch. The accompanying diff includes the complete changes. The publishing commands below include only the fix, its tests and this report, and exclude `config.py`.

Run these in Windows Command Prompt. `commit --only` limits this commit to these three paths even if other work is already staged:

```bat
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"
git --no-pager diff --check -- section_background.py tests/test_background_reference_formats.py M4A_BACKGROUND_FIX.md
git --no-pager add -- section_background.py tests/test_background_reference_formats.py M4A_BACKGROUND_FIX.md
git --no-pager commit --only -m "Fix saved M4A backgrounds in speech removal" -- section_background.py tests/test_background_reference_formats.py M4A_BACKGROUND_FIX.md
git --no-pager push origin main
```
