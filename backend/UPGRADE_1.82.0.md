# Lisan AI 1.82.0-rc1 — Claude handoff

Status: guarded release candidate prepared for publication at the owner’s explicit request; actual commit/push/deployment status is recorded separately in the release receipt. Base commit: `c87426b` on `main`, the published 1.81.1 upgrade. Repository: `C:\Users\Ali Haider\Desktop\ai-dubbing-app`; application directory: `backend`.

The owner requested selected-line long-dub corrections, consistent short/long background processing, local volume matching, overlap detection, ten-line review pages, storage preflight, login-header repairs, provider usage reporting, compact help text, and better emotion review. The owner requires tested changes, a complete handoff, and terminal publishing commands rather than copying replacement source files. Railway deploys when main is pushed. The owner subsequently authorized pushing this guarded candidate with the known music-repair failure. Keep the guard active; this authorization does not establish music-inpainting quality or a stable release.

## What was actually validated

- 34 existing Python regression tests passed using the project's installed FastAPI/Pydantic versions. The isolated test harness does not import/start `main.py` or run its housekeeping against live data.
- 43 new Python checks cover real FFmpeg strict muting and a real 96-second correction export. The export test checks full duration, silence outside selected voice lines, continuous background, use of the same mocked provider voice ID, and a selected line crossing a 45-second processing boundary. Additional checks cover complete spend-history pagination, avoiding repeated provider calls after an interrupted repair, safe attenuation of a loud clean repair, refusal to over-amplify a quiet repair, and rejection of severe clipping before lowering volume.
- 12 JavaScript checks passed, covering existing generation/regeneration billing and new short-merge confirmation cancellation and invalid prices.
- Local browser fixture, using the actual correction HTML/JS: 23 lines appear in groups of ten/ten/three; a reviewed group shows a green check; text saves before page navigation; selections survive page changes; canceling the price dialog sends no dubbing request and restores controls; help opens on click; Arabic layout switches correctly.
- Provider and customer-credit operations in the 89 automated checks are simulated. Subsequent live checks are recorded separately below; customer billing remained simulated and isolated.

Final verification output and exact source hashes are separate files beside this handoff. If further code changes are made, repeat relevant checks and regenerate those files.

## Subsequent live service checks and the remaining audio blocker

The owner confirmed the local development credentials and added `FAL_API_KEY` and `FAL_ADMIN_KEY` to `.env.local` and Railway. `.env.local` also contains working Inworld and Gemini credentials. No credential is missing for these tests. The earlier absence report came from checking `.env` rather than the local development configuration; do not request or copy secrets into the handoff or Git.

- Inworld read-only authentication passed. Two temporary test clones were created from the approved demo reference during diagnosis; both were deleted successfully. Two Arabic TTS requests used the same clone in two separate selected-line correction worker runs. No unselected line was synthesized. Each resulting voice track was 24 seconds, with exactly zero measured RMS in the unselected interval; background continued there. The customer debit hooks were simulated, not the production database.
- Gemini's actual five-second voice-listening request returned a valid uncertain/neutral result and explained that loud background sound interrupted the short speech. That verifies the live response contract, not emotion accuracy.
- Fal's actual admin wallet and model-usage requests succeeded. At the last check the shared wallet was $14.59 USD and this month's model usage was $0.1665 for five audios, of which three were this candidate's checks. The provider lists $0.0333 per audio; the three calls account for $0.0999. These figures are a timestamped observation, not a permanent balance.
- Live repair exposed a baseline guard that refused more than 12 dB of attenuation. A controlled clean sine-wave reproduction confirmed the problem: a loud but undistorted repair of the same frequency can be reduced to the measured surrounding level without harming its shape. The candidate now permits that attenuation, retains the +6 dB amplification limit, and tests both cases.
- The live model returned severely clipped material. Its repaired interval contained 24.59% exact full-scale samples, and Gemini's qualitative listening check described distorted static with no music or intelligible speech. Functional export and placement checks passed before this quality diagnosis; they do not establish a usable music repair.
- The final candidate rejects a generated interval with at least 10% exact full-scale samples before level matching. Replaying the captured provider result with zero network calls confirms that it is refused and no repaired output is written. This guard catches severe saturation; it is not a complete music-quality or ghost-speech detector. A music-preserving export that requires this repair now stops instead of publishing that static.

**Music repair remains unresolved for a stable release.** The owner later explicitly authorized publishing this guarded release candidate with failed repairs blocked. Do not substitute another model or relax the clipping guard just to make it pass. The provider reproduction and captured input/output are included beside this handoff. The safe resolution is to establish why the documented model returns unusable audio, then demonstrate usable repairs on real short and long examples before publishing. No provider support message was sent.

The provider JSON reports describe the sequence of checks. The earlier `music_repair.status=PASS` means the request/assembly contract passed before the subsequent quality test; `final_quality_verification` records the final rejection. Correction mix samples from that earlier run are diagnostic artifacts and include the unusable generated section. They are not quality-approved examples or a production delivery.

## Complete source-file inventory

### Modified files

`backend/main.py`

- Adds correction page/script routes; owned-project draft, quote, generation, recovery, finish, and reviewed-group endpoints. Reuses `_ld_job` ownership checks.
- Adds short-merge quote and user-confirmed maximum music budget. Paid requests require a verified account/balance and a current accepted price. Short merge uses the shared background pipeline.
- Stages merged output under a pending filename, then publishes it after the merge payment is confirmed. A failed merge debit raises into the repair-refund path and preserves any previous successful export.
- Adds audio-style review endpoints that cut one original audio interval, use the shared Gemini classifier, and remove the temporary sample. Original audio is required; individual intervals are limited to 30 seconds.
- Reuses the existing session-to-user lookup for the main app header. An account/balance service outage does not invent a Guest identity or a -1 balance. The browser displays an unavailable balance as an ellipsis.
- Storage checks fail closed on account/storage lookup failure. Budgets include final media, background track, optional voices, and voice references. Reservations include other outstanding projects. Same-account long-job reservation operations are serialized within the server process; correction preflight and submission use that lock too.
- Spend-history lookup now reads subsequent 1,000-row pages instead of silently counting only the first thousand records. It refuses an incomplete history beyond its bounded 200,000-row scan.
- Checks user quota and server scratch capacity before paid processing. Rechecks using the measured media duration after upload and before final dubbing. Register correction M4A files in output ownership/retention accounting.
- The optional strict mode of `_sb_rpc` surfaces refund service failure rather than writing a refund receipt for a failed HTTP call. Existing callers retain their default behavior.
- The long-price preview still shows voice pricing if music preservation is unavailable; it returns a music error that blocks preservation until resolved or explicitly deselected.

`backend/longdub_service.py`

- Raises normal long-dub duration capacity to 60 minutes and transcript ceiling to 6,000 lines. The existing 2 GiB source-file ceiling remains. Server scratch space and user quota can still reject a file.
- Adds prepayment capacity hooks and confirmed-debit checks. Adds a repeated-accept status check under the job lock.
- Uses the shared strict residual-speech removal, music repair, and local background-level matching. Removes the old global background lift/filter chain from the final background mix; the final background filter is unity gain.
- Saves short speaker reference WAVs, provider voice IDs, per-line original loudness, and a small original-voice RMS envelope for corrections. Saves the full music/effects track even if separate voice-track download was not requested.
- Retains provider clones for seven days after their last correction use, or releases them when the user finishes corrections. Housekeeping retries failed provider deletions. Reference samples remain for later re-cloning.
- Dispatches correction jobs through the existing worker and resumes confirmed/dubbing correction jobs through the existing startup resume path.
- Separates a correction recovery job from an ordinary full-redo job, so attaching an original for corrections cannot take over a full-redo project already being edited.
- Removes generated background scratch WAVs after completion and when parking media. Keeps the tiny timing maps and editing references.
- Updates clone-retention email wording and the terms version to `2026-10-05-corrections`.

`backend/dub_long.html`

- Shows ten transcript lines at a time. All group buttons remain visible; reviewed groups get green checks. Checks belong to a hash of that group's text, timing, speaker, and style, so edits invalidate them.
- Highlights overlapping line intervals. Adds the manual timing reminder after insertion/splitting.
- Prevents navigation/review marking/confirmation from treating unsaved edits as saved.
- Moves the screenshot's introductory explanation, steps, speaker explanation, and subtitle explanation behind small question-mark help controls. Help supports hover, keyboard focus, click, and Escape.
- Adds audio-based style review with a confirmation before applying a replacement.
- Shows the music budget before confirmation and includes it in the balance check and displayed maximum. Music preservation failures disable that confirmation.
- Adds a link to the correction page and updates English/Arabic explanations to match saved references and temporary clone retention.

`backend/app.js`

- Adds a manual timing reminder on short-line insertion.
- Adds individual audio-style review. Applying a suggestion requires confirmation. Manually selected styles are marked and skipped by the active batch listening-result handler.
- Adds short-merge maximum-price confirmation, including music inpainting. Validates quote numbers and snapshots the original job/options through confirmation; cancellation submits no paid merge.
- Preserves the account header on failed user-info requests and avoids displaying a negative/unavailable balance as -1.

`backend/gemini_service.py`

- Adds the shared `inspect_audio_style` classifier. Both individual review and the existing batch listening worker use it.
- Asks for supported style tags, an uncertainty flag, and a short explanation. Ambiguous results fall back to neutral. No invented accuracy percentage is returned. Batch output retains its existing emotion-map format and adds review metadata.

`backend/bg_duck.py`

- Adds an explicit `strict` option to `mute_speech`. In the new shared pipeline, known original speech intervals have zero gain; the earlier spectral-floor behavior remains available to callers that do not request strict mode.

`backend/music_fill.py`

- Extends the whole-track inspection ceiling to one hour. Keeps bounded PCM processing and avoids converting a whole hour into a float array for the music-description step.
- Stops successful delivery if a repair payment callback fails. Closes the PCM map before removing temporary files, including Windows failure paths.
- Permits safe attenuation to the measured context while retaining the amplification cap. Refuses severe generated clipping before attenuation can conceal its loudness; the shared path blocks delivery when a required repair fails.
- Gives each provider sample a unique temporary filename, avoiding collisions when different jobs repair the same timestamp.
- Uses the existing Fal Stable Audio 3 small music inpainting endpoint and its existing no-vocals/no-speech prompts. Gap/context/quality limits are retained; unsupported holes are rejected by the new shared caller instead of quietly treated as repaired.

`backend/admin.html`

- Adds Fal wallet balance and this month's music-inpainting provider spend, with unavailable states and a top-up link.
- Displays the new 60-minute normal long-dub maximum. Untouched legacy mixed line endings were preserved to avoid unrelated formatting changes.

`backend/config.py`: version is `1.82.0-rc1`, deliberately identifying an unpublished release candidate.

`backend/tests/test_shortdub_ui.js`: adds cancellation and invalid-price checks for short merge.

### New files

`backend/dub_review.py`: pure overlap detection, ten-line group fingerprints, suggested/confirmed style state, and conservative output-size estimates.

`backend/dub_audio.py`: bounded decoding, half-second RMS measurements, original voice profile, per-interval background gain planning, smoothing, and an attenuation-only peak guard.

`backend/dub_background.py`: shared original speech masks, strict muting, preflight music-repair counting, rejection of missing clean context/unsupported holes, required-repair failure handling, and local level matching. When the user explicitly deselects music preservation, repair is skipped. Long jobs checkpoint before entering paid background processing and after a complete matched background. A completed checkpoint is reused; an interrupted paid repair is stopped for refund/review rather than submitted again. Recovered parent background is published through a temporary file and atomic replacement.

`backend/longdub_edits.py`: immutable original transcript; separate correction draft; selected-line validation and quote fingerprint; existing voice reuse or paid re-cloning; full-duration selected-voice export and full-background correction export; recovery and finish behavior. Creates the durable project folder/state before payment, queues only a confirmed payment, and attempts a refund if queuing fails.

`backend/dub_long_edit.html` and `backend/dub_long_edit.js`: English/Arabic completed-project correction page. Users select bad lines, edit text/speaker/style/times, save, mark groups reviewed, confirm a quote, and download each correction pass. Saves are serialized. Controls are disabled while a quote/generation action is pending. Old projects can attach their original file again. Recovery remains accessible when references are missing even if an effects track exists.

`backend/emotion_review.py`: compatibility wrapper for the shared Gemini classifier; there is no second independent classifier implementation.

`backend/fal_usage.py`: read-only Fal billing/usage calls, bounded timeout, 60-second cache, real USD wallet reporting, current-month model usage, and explicit unavailable states. Uses `FAL_ADMIN_KEY` if configured; otherwise tries the existing FAL key. Fal billing requires an admin-scoped key. Never expose keys in the browser.

`backend/tests/test_dub_revision.py`: isolated state/billing/provider-contract checks and real FFmpeg audio checks described above.

`backend/UPGRADE_1.82.0.md`: repository copy of this handoff.

## Short versus long processing

| Stage | Short dub | Long dub / corrections |
|---|---|---|
| Upload | Existing short-clip upload and short-duration limits | Resumable 8 MiB uploads; measured duration up to one hour; existing 2 GiB ceiling |
| Analysis | Existing separation, transcription, speaker processing and translation for one short clip | Separation/transcription in roughly 40-second pieces, speaker mapping and translation batches, checkpointed background jobs |
| Voice generation | Existing engine/voice choices, short generation and single-line regeneration | Inworld per-speaker clones; timeline fitting, optional shorter rephrasing; corrections generate only selected rows |
| Background | Previously lacked the long flow's inpainting path; now uses the same shared strict muting/repair/local-level path during merge | Replaces global background lift with the shared path; keeps the resulting full effects track for later corrections |
| Timing | Existing short fitting/timeline controls | Existing long fitting plus ten-line review and overlap warning; correction placement uses edited start/end times |
| Output | Short dubbed MP3 and merged video | Original-duration main output; each correction pass is audio-only at the full original duration, with background+selected voices and a voice-only alternative |
| Billing | Existing confirmed short generation/regeneration price; merge now confirms its repair maximum | Original analysis/dub billing retained; corrections pay selected text/style characters, only required new clones, assembly, and any first background recovery |

The short and long workflows retain different analysis, voice, timing, and job-lifecycle behavior. Sharing the background path does not make every processing step identical.

## Answers to the audio questions

The previous 1.81.1 patch fixed isolation and billing; it did not add short-dub music inpainting. The old background path used automatic spectral attenuation, which could leave residual original words. Long processing also had whole-track make-up gain and a boosted background filter, which could make the retained layer too loud. There was no single fixed measurable ghost-voice volume across files.

The candidate sets zero amplitude inside known original speech masks. Zero amplitude means digital silence, not “0 dB.” The real muting test verifies silence there. This does not prove that every original word was detected, that separated layers are perfectly clean, or that a generative model cannot produce vocal artifacts.

Music inpainting is requested only for usable silent holes with sufficient clean background context; usable retained background outside the mask is kept. If hard muting leaves sound that cannot be restored reliably, music-preserving export is blocked rather than silently delivered with those larger gaps. A truly silent original background does not trigger invented music. Background that consists solely of residual speech can be conservatively rejected too: the code does not pretend it can always distinguish speech-only residue from lost music.

Local level matching uses the separated original background and vocal measurements for each interval. These are estimates from separation, not direct access to an unavailable perfect music master. Correction exports reuse the saved first background so it remains consistent across passes. The music+voices correction must replace corresponding timeline audio; stacking it over the first background doubles the music. Use the voice-only correction when keeping the first background.

Gemini listening can add evidence from tone, pace and volume that the transcript alone lacks. Its uncertainty flag is a suggestion, not a calibrated accuracy score. Neutral is the fallback and the user's confirmed style is preserved. Official audio documentation: https://ai.google.dev/gemini-api/docs/audio .

## Remaining validation and practical limits

1. Live credentials and Fal wallet reporting are verified. Live clone creation/deletion, same-ID reuse, and the Gemini listening contract passed. The captured Fal repair failed audio quality and is now refused: music-preserving exports requiring that repair may stop in this candidate. Seven-day expiry/re-cloning and production account billing still need validation. Do not request keys in chat or include either environment file in a commit.
2. Music repair keeps the existing minimum hole/context constraints, 20-second maximum individual hole, maximum 200 repairs, and total/time limits. Continuous dialogue may leave too little clean context or produce an unsupported larger hole. The candidate blocks preservation in that case. It does NOT establish dependable inpainting for every one-hour video or recover arbitrary original sound effects perfectly.
3. One-hour duration checks and conservative storage budgets are tested, but a complete one-hour provider run/Railway resource measurement is not. The existing upload ceiling remains 2 GiB. Studio subscription does not bypass storage quota or server scratch checks.
4. Exactly-once billing through process death remains a required integration audit. The music checkpoint guard now prevents blindly repeating uncertain paid repairs; an interrupted job is stopped for refund/review rather than automatically resuming those repairs. Correction `payment_pending` makes a job traceable before debit and blocks another correction submission, but process death between debit and the confirmed checkpoint still requires reconciliation against `credit_spends`. Do not claim this is a database transaction or guarantee automatic refund through a network outage.
5. Storage reservations are protected within one server process, not by a database transaction spanning multiple replicas. Spend history is now paginated; local output/reference accounting and retention still need verification against actual production account records.
6. Re-cloning an old deleted provider voice from a saved/original sample can approximate the original speaker; it cannot recover the deleted provider ID or guarantee an identical regenerated timbre. Existing live IDs are reused while retained. Already-finished pre-upgrade projects need the original file if references/background were deleted.
7. User voice references stay on the server for later corrections and count toward storage. Account/project deletion now removes inactive owned samples/media/text and keeps only anonymous provider-ID retry records when the provider cannot delete immediately; the mocked failure/restart lifecycle is tested, while live outage behavior still needs validation. Lip-sync-specific processing was not tested in this task.
8. Actual heard ghost-voice levels in the owner's two videos have not been measured. Synthetic silence tests cannot substitute for listening to and comparing those real outputs.

These are concrete remaining validations/limitations, not a claim that music repair is ready or that every one-hour video is verified. On 2026-10-05, after being told failed music repairs would be blocked, the owner explicitly answered “Push it now.” Publishing this guarded candidate is therefore authorized; the known limitations remain.

## Publishing and preserving existing work

Do not use `git add .`, `git add -A`, or a broad reset. The user's tree already contains nine deleted demo videos, backup directories, alternate app/login files, promotional media, and other untracked work. None was created for this upgrade and none belongs in its commit.

The provided patch/manifest includes only the source files above. Files were edited directly in the repository; no replacement-code copying is required. No Git commit, push, Railway deployment, provider top-up/purchase, or production customer billing operation was performed. The owner-authorized live AI checks above did consume provider usage; these must not be described as entirely offline.

Keep `1.82.0-rc1` for this owner-authorized candidate push. A future stable `1.82.0` requires resolving the remaining validations, repeating relevant checks, and regenerating this record. Stage/commit only the 20 manifest files, using `git --no-pager`; do not reuse the earlier PowerShell publishing script. Record the actual pushed commit and remote verification in the separate release receipt.

Provider API references: https://fal.ai/models/fal-ai/stable-audio-3/small/music/base/audio-inpainting/api ; https://fal.ai/docs/platform-apis/v1/account/billing ; https://fal.ai/docs/platform-apis/v1/models/usage .

Diagnostic evidence: `Lisan-AI-1.82.0-available-provider-checks.json`, `Lisan-AI-1.82.0-live-audio-checks.json`, `Lisan-AI-1.82.0-music-quality-check.json`, `Lisan-AI-1.82.0-clipping-guard-check.json`, and `Lisan-AI-music-inpainting-reproduction.md`.

## Publication audit and owner decision

The owner explicitly selected **“Push it now”** after the candidate notice: failed music repairs are blocked, so some music-preserving exports may stop. This is publication authorization for `1.82.0-rc1`, not an instruction to remove the music quality guard or call the candidate a stable release.

The final read-only audit and reproduced cases led to these additional narrow fixes before publication:

- `backend/main.py`: records a confirmed short-merge debit before replacing the final output. If publishing the file fails, the exception path refunds the confirmed merge debit together with any confirmed music-repair charges; the previous successful export is preserved. The account-delete route now serializes the account operation and checks/removes owned inactive long-project data before Stripe/profile/auth deletion. Active jobs or failed local cleanup stop account deletion first.
- `backend/longdub_service.py`: voice expiry and stale-project removal now protect parents with active correction children. Restored original files are also protected from parking while corrections use them. Parent locks serialize these checks with correction submission. Project deletion verifies ownership and inactivity. Shared account/project cleanup removes owned reference samples, media, transcript data, and outputs. Failed provider deletions retain a minimal anonymous `provider_cleanup` record; housekeeping retries its IDs and removes even an empty record after crash/restart. A failed filesystem cleanup retains an owner-linked minimal checkpoint so retry cannot silently skip undeleted files. Directory boundaries are verified before removal.
- `backend/longdub_edits.py`: a confirmed correction refreshes the parent’s voice-retention timestamp before queueing. Explicit finish uses the same parent lock and rejects active corrections, preventing deletion during submission.
- `backend/tests/test_dub_revision.py`: eleven further regression checks cover these final cases, including actual output-replacement failure after debit, active/expired voice protection, restoring-file parking protection, owner-only account cleanup, running-job refusal, provider deletion retry, disk cleanup failure, and empty cleanup records after restart.

Final validation: **34 existing Python + 43 new Python + 12 JavaScript = 89 passing tests**, no skips. Candidate syntax and source-only patch checks also pass. The live provider checks recorded earlier still apply to their tested paths; these final failure/cleanup cases use isolated mocked provider/database services. No real user account was deleted or modified during the audit.

The separate release receipt records the actual commit/push result and Railway verification. Before that receipt exists, do not infer successful deployment from this document.
