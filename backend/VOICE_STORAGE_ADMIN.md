# Lisan AI: two-day voice retention and admin storage controls

Prepared for Ali, 11 October 2026. Changes are saved locally. Nothing was committed, pushed or deployed. `config.py` is unchanged.

## Why seven days, and what changes now

Seven days was an application policy intended to give people time to correct completed projects without paying to clone every speaker again. It was not a provider requirement. Ali has selected **two days** as the new default. Admin can set a whole number from **1 to 30 days**, independently of prices and the configured voice-slot limit. Settings are stored atomically in `DATA_DIR/voice_management.json` on the server data volume, not in `config.py`; there is no SQL migration or new secret.

The period starts from the finished dub if no corrections have used its voices, otherwise from the last correction use. Merely opening a project does not restart it. The new period applies to existing projects too: after deployment, unused long-dub clones older than two days become eligible at the next hourly housekeeping run. Running jobs and corrections stay protected. Saved library voices do not acquire a two-day expiry. Missing or unreadable dates are kept for explicit review; a valid zero Unix timestamp can expire. Corrupt settings do not silently fall back to a shorter period.

## What the live read showed

The read-only provider check returned **83 custom voices**:

| Name/type | Count |
|---|---:|
| Long-dub temporary clones (`lisan-tmp-`) | 74 |
| Short-dub clones (`Cloned_`) | 3 |
| Custom uploads (`Custom_`) | 2 |
| Other workspace voices | 4 |

This check made no clones and deleted no voices. It proves that long-dub tests account for most of the provider storage. It does not prove which 74 are expired: that requires the deployed server's project records, which the new admin inventory reads after publication. The 100-slot number remains the administrator's configured limit, rather than a provider-verified plan quota. The alert now directs Ali to inspect and clean unused voices before considering an upgrade.

## Admin controls

Open **Admin → Voice → Voice storage manager**.

1. **Keep temporary voices for (days)** and **Save retention** set the period. Default: 2.
2. **Refresh voice inventory** reads the complete provider list, local long projects and saved library entries. No provider call or cleanup occurs automatically on loading this panel; the settings file alone is read then.
3. Counts and rows distinguish saved library voices, running jobs, retained correction voices, expired/released voices, leftover long clones, untracked short clones and other workspace voices. Project names and expiry times are shown when available. Incomplete reference checks leave a read-only inventory and disable deletion.
4. **Preview expired and leftover cleanup** selects only expired project voices and orphan long clones. Saved voices, retained voices, untracked short voices and other workspace voices are excluded from this automatic selection.
5. **Preview selected deletion** allows explicit release of known app voices, including retained project voices and saved library voices. **Select all available** selects eligible rows; unrecognized other-workspace voices remain read-only. The confirmation lists selected names, identifies saved-library removals and explains later re-cloning.
6. Cleanup runs in the background, with a progress bar, status and a durable receipt in `DATA_DIR/voice_cleanup/`. Up to 100 voices may be selected per batch. A preview expires after five minutes and is rechecked before deletion. Completed receipts return the same result on repeat requests. Interrupted/partial cleanup can be retried from the panel.

Free inventory inspection, local settings and deletion add **zero Lisan credits**. No prices or subscription allowances were changed. New mutating API requests pause during manual cleanup, while reads and cleanup-status polling remain available. Existing running work blocks cleanup. This coordination targets the current single-process deployment; do not treat it as a distributed lock for multiple replicas.

## References and partial failures

A cleanup intent is saved before anything changes. Saved library rows are removed before provider deletion; matching speaker-to-voice mappings are cleared in every affected local long project. Pending provider IDs and the cleanup receipt remain available for retry. Provider errors/timeouts are never reported as successful deletions; an explicit 404 is treated as already deleted. Export files, background tracks, subtitles, source voice samples and user accounts remain intact. If a later correction needs a released voice, the existing correction path quotes re-cloning from saved reference samples.

Short-project JSON files saved on Ali's computer cannot be rewritten by server cleanup. They may retain an old ID: choose another voice or clone again when opening such a file. Untracked short clones are therefore a separate explicit choice rather than part of expired/orphan cleanup. Other provider voices are counted and shown but are not assumed to belong to Lisan's project lifecycle.

## Files and functions

| File | Changes |
|---|---|
| `voice_admin.py` (new) | Atomic retention settings; shared `expires_at`; complete paginated custom-voice reads; conservative classification; preview fingerprints; durable, resumable cleanup with reference clearing and confirmed provider deletion. |
| `main.py` | Admin settings/inventory/preview/cleanup/status endpoints, all guarded by `_admin_check`; project/saved-library adapters; background cleanup worker; mutation gate; long-dub config publishes the retention period; serves the admin control script. |
| `admin_voice_controls.js` (new) | Escaped inventory table, setting form behavior, preview/confirmation, progress polling and retry controls. Provider diagnostics/keys are not inserted in the page. |
| `admin.html` | Voice storage manager card and script; loads settings after admin sign-in. Inventory remains an explicit action. |
| `longdub_service.py` | `sweep_voices` uses configured expiry; `public_view` publishes the period; completion email reports it. No speech generation, fitting, audio mixing or pricing logic was changed by this admin batch. |
| `dub_long.html` | Retention wording uses the current project/config period in English and Arabic, including confirmations. |
| `privacy.html`, `help.html`, `landing.html`, `pricing.html` | Explain the period displayed in the project instead of promising a fixed seven days. Other storage durations remain as before. |
| `service_usage_monitor.py` | Alert prioritizes inventory/cleanup and clarifies that the configured slot limit is not provider-verified. |
| `tests/test_voice_admin.py` (new) | 22 backend safety and behavior regressions. |
| `tests/test_admin_voice_controls.js` (new) | Five UI/wording tests. |
| `tests/test_site_qa.py` | Updates the earlier fixed-seven-day wording assertions to the configurable policy, retaining the other storage-limit assertions. |
| `VOICE_STORAGE_ADMIN.md` (new) | This handoff. |

Existing file line endings are preserved. New files use LF. No protected audio DSP files were changed in this admin batch; the previously authorized service/page edits are limited to voice lifecycle and wording.

## Verification

- The final targeted backend run is recorded separately; all new safety assertions pass, including actual housekeeping at 7 versus 2 days, full pagination, stock-voice exclusion, stale previews, running jobs, provider failures, repeat cleanup, saved-reference clearing, admin authentication, the mutation gate and worker-launch failure recovery.
- Nine selected JavaScript tests pass: five new controls/retention checks, one existing admin save check and three background-report checks.
- The actual admin page was exercised in headless Chrome with mocked services: default setting, protected rows, selection, cancel without cleanup, confirm, progress and saving a changed period. No page errors. The screenshot is in the workspace outputs. This was not a live deletion test.
- A read-only live provider call confirmed the 83-voice breakdown above. No live deletion or account reset was performed.

Final `python -m unittest discover -s tests` result:

```text
Ran 1322 tests in 254.175s

FAILED (failures=1, errors=1, skipped=1)
```

The remaining blocked checks are:

- `test_no_currently_tracked_backend_file_matches_an_ignore_rule (test_repository_hygiene.BackupRulesTests.test_no_currently_tracked_backend_file_matches_an_ignore_rule)`: Git cannot change into the parent checkout through this sandbox.
- `test_many_saves_at_once_never_fail_and_leave_no_temporary_files (test_segment_writes.SegmentWrites.test_many_saves_at_once_never_fail_and_leave_no_temporary_files)`: Windows access-denied during concurrent segment-file replacement.

These are the same unchanged tests that failed in the preceding ambience batch. The full suite is **not green in this sandbox**. Rerun it in normal Command Prompt before publishing. The earlier unused-clone expiry regression has been fixed in code, with the existing proof and a new zero-timestamp regression intact.

## Delivery and publishing

The working folder already contains the changes. The admin-only diff compares with saved before-edit snapshots. Because the immediately preceding ambience batch was unpublished when work started, the combined ZIP and publishing commands include it too; this prevents deploying updated `main.py` or the long-dub page without their new ambience modules, script and assets. If the previous batch has already been committed, unchanged files will simply add nothing to the new commit. Full-file snapshots are a delivery backup, not instructions to overwrite concurrent work blindly. A Git HEAD comparison was unavailable in this sandbox; the snapshot comparison is documented in the package.

Run these yourself in Command Prompt. `commit --only` limits the commit to this combined batch and excludes unrelated staged files, including `config.py`.

```bat
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"

git --no-pager add -- ambience_assets BACKGROUND_LIBRARY.md VOICE_STORAGE_ADMIN.md admin.html admin_voice_controls.js ambience_library.py ambience_match.py app.js background_review.js dub_background.py dub_long.html dub_long_edit.html dub_long_edit.js help.html index.html landing.html longdub_edits.py longdub_service.py main.py pause_bed.py pricing.html privacy.html section_background.py service_usage_monitor.py styles.css tests/test_admin_voice_controls.js tests/test_ambience_library.py tests/test_background_review.js tests/test_scene_ambience.py tests/test_site_qa.py tests/test_source_language_ui.js tests/test_step1_layout.py tests/test_voice_admin.py voice_admin.py

git --no-pager commit --only -m "Add ambience library and admin voice retention controls" -- ambience_assets BACKGROUND_LIBRARY.md VOICE_STORAGE_ADMIN.md admin.html admin_voice_controls.js ambience_library.py ambience_match.py app.js background_review.js dub_background.py dub_long.html dub_long_edit.html dub_long_edit.js help.html index.html landing.html longdub_edits.py longdub_service.py main.py pause_bed.py pricing.html privacy.html section_background.py service_usage_monitor.py styles.css tests/test_admin_voice_controls.js tests/test_ambience_library.py tests/test_background_review.js tests/test_scene_ambience.py tests/test_site_qa.py tests/test_source_language_ui.js tests/test_step1_layout.py tests/test_voice_admin.py voice_admin.py

git --no-pager push origin main
```

## Test after deploying

Open Admin → Voice and confirm the retention field shows 2. Refresh the inventory; compare its total with the provider workspace (the older Health monitor may take its normal 20-minute interval to refresh). Inspect projects/expiry before selecting cleanup. Cancel one preview and confirm no slots disappear. Then, when ready, confirm expired/orphan cleanup or explicitly selected test voices. Confirm the receipt reports only confirmed deletions and the live inventory drops. Saved library entries should disappear only if explicitly selected. Exports and reference files should remain accessible. A later correction on a released project should quote re-cloning rather than reuse an invalid ID. No live cleanup has been performed on Ali's behalf.
