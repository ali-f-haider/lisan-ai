# Lisan AI — Job 12 brief: measure how fast a speaker REALLY speaks (pauses are not slowness)

Version at hand-off: 1.82.30 (Claude bumps the version). Work from the current `backend` folder.
You do not commit or push. You deliver changed/new files, a handoff `.md`, and literal git commands (Windows Command Prompt, run from `backend`, `git --no-pager`, `git add -- <files>` then `git commit --only -m "..." -- <files>`). No SQL.

## The finding behind this job (Ali's observation, checked by Claude on the real clip)

Ali: a speaker who *thinks while speaking* leaves pauses between words, but the words themselves are not slow. A dub that slows the whole sentence to imitate that sounds synthetic. The pace of a speaker must be judged from **how they articulate (pauses removed)** and **in the context of that speaker's whole speech**, not from the time a line occupies.

What Claude measured (one line, original 5:16–5:26 of a film clip, English, radio-style speech; **measured here**, one small general speech model on noisy audio, so treat as ±, direction is what matters):

- The transcription's word times (faster-whisper, `word_timestamps=True`) put the line's first word at 316.6 s; the voice-only audio and a forced alignment of the same words to it put the first word at about **318.0 s**. Whisper also gives single short words absurd durations ("is" 0.96 s, "copy" 1.38 s, "have" 0.84 s) because it spreads **pauses over the neighbouring words**: there are almost no gaps between its word times. So `delivery.line_rate()` (which only removes gaps between words ≥ 0.25 s) sees almost no pauses and reports **2.96 syllables/s → "slow"**.
- On the isolated voice the same sentence has real silences (0.2–0.9 s several times, a 1.5 s one before the last word) and the speech between them is at about **4.5–5 syllables/s with the pauses removed**: normal.
- Downstream result in Ali's dub: that line (31 Arabic letters, ordinary length ≈ 3 s at a natural pace — **estimated**) was generated at **6.67 s** (log: "seg_70 … 38 -> 31 letters, length 6.67s for 6.66s of room"), i.e. the voice was told to speak slowly and stretched to fill the slot. That is exactly the unnatural result Ali describes.

Already done by Claude (1.82.30, so you build on it): automatic `slowly` / `drawn out` tags are switched **off** (`delivery.AUTO_SLOW_ALLOWED = False`; `ground()` and `emotion_listen.merge()` never add them). They stay off until this job gives a trustworthy measurement. A user can still pick any delivery by hand.

## What to deliver

### Part A — Measure pace from the audio, not from word timestamps
New module (suggested `speech_pace.py`), **numpy/scipy only: no new model, no new heavy dependency, no raise in peak memory** (memory was just brought from 1.9 GB to 0.7 GB idle; every MB matters). Input: the isolated mono vocals (`vocals_mono.wav`, 16-bit PCM; 16–44.1 kHz — check `longdub_service.SAMPLE_RATE`) plus the rows (`start, end, text, words`). Output per row: `{active_seconds, pause_seconds, pauses: [(t, dur)], syllables_text, articulation_rate, rate_with_pauses, quality}` with a `quality` flag (`good|noisy|unknown`) when the audio cannot support a number.
1. **Speech activity / pauses from the signal:** voice-band energy (about 300–3400 Hz) relative to the local speech level, plus a voicing cue; pauses ≥ ~0.15 s inside a line are not speech. Research and justify thresholds; make them work for radio-filtered voices and background hiss (the isolated-vocals track contains static, as in the clip above).
2. **Syllable count:** from the text (as `delivery.syllables` does) **and** from the audio (intensity-peak syllable-nuclei counting after de Jong & Wempe 2009 — read the paper/Praat script description, cite it, state its known error). Use the audio count only to cross-check the text count; when they disagree strongly, set `quality="unknown"`.
3. **Rate:** `articulation_rate = syllables / active_speech_seconds`. Keep `rate_with_pauses` for information.
4. **Do not trust Whisper word times for pace.** They may still be used for *which words are in the line*. Say in the handoff exactly where `delivery.py` (`line_rate`, `_line_stats`, `moment_rate`, `pace`) would take its numbers from the new module instead. `pace(rows, i)` has callers in `longdub_service._row_paces` and `gemini_service`: keep signatures or describe the exact change for Claude.
5. **Test offline** with invented signals: tone/noise bursts amplitude-modulated at a controlled syllable rate with controlled silences (known ground truth), added noise at several levels, a radio-like band-limit, a line whose words are evenly spread (no pauses) vs the same words with the same total duration but with pauses — the pause one must NOT come out slower. Report error vs ground truth (measured here).

### Part B — Judge a line against the speaker's own whole speech
1. For each speaker, build a **baseline** = median articulation rate over all of that speaker's usable lines in the clip (state the minimum number of lines; below it → no baseline → pace "unknown"). Use the same speaker only.
2. A line is `slow` only if its articulation rate is clearly below **both** the speaker's baseline (state a ratio, e.g. below 0.75 × baseline — justify it) **and** an absolute floor (justify it), **and** the neighbouring lines of that speaker agree. A line with many pauses but normal articulation is `normal` and may be flagged `pausey` (new, informational: "thinking pauses") — see Part C. `fast` likewise relative + absolute.
3. Report, on invented fixtures, how many lines change class compared with the current `delivery.pace`, and why.
4. Keep `tempo_cap(pace)` meaning, and tell Claude what changes in the cap behaviour (a mis-measured "slow" gives a stricter cap and more shortened wording).
5. Say clearly when `AUTO_SLOW_ALLOWED` could safely be turned back on and under what conditions, or recommend leaving it off for good; give your reasoning.

### Part C — Research (decision for Ali, do not implement)
How should a dub treat a line that has thinking pauses in the original?
1. Today `longdub_service._pick_tempo` slows a generated line down to fill its slot when it is shorter (`TEMPO_MIN = 0.85`, i.e. up to 15 % slower), and a `slowly` tag asks the voice for slow speech: both stretch **words**. Describe (from the code) when this happens and how much it changes a typical line.
2. Alternatives: (a) never slow down (`TEMPO_MIN = 1.0`) and let the line end early; (b) a smaller floor (e.g. 0.95); (c) **pause-preserving placement**: split the Arabic at the points that correspond to the original's pauses (using the pause map from Part A and the word alignment) and place the chunks so the pauses stay pauses while the words stay at natural speed. Assess feasibility, risks (Arabic grammar/breath groups, tashkeel, voice-cloning continuity, lip-sync), effect on cost (more TTS requests?) and on the existing mixer; recommend one and say what it would take. This touches protected audio files, so write the exact proposed change as text only.

### Part D — Wiring notes for Claude
Exact places in `longdub_service` (analysis: after the isolated mono vocals exist and before `_row_paces` is used; edits; completed projects without audio → pace "unknown"), the data to persist per row (so a redo project keeps the numbers), the log line (one `pace` event: counts of slow/normal/fast/pausey/unknown, no vendor names), and how `emotion_listen.merge` / `delivery.ground` consume the new pace.

## Rules (same as always)
- General solutions only: no special case for one clip, film, speaker or value. No film dialogue in the repo (invent neutral text).
- You may edit `delivery.py`, `emotion_listen.py`'s use of delivery only if needed (describe it), and add new modules/tests/tools (for example `tools/pace_replay.py`). Do NOT edit `config.py`, the audio/voice modules (`dub_audio.py, dub_background.py, bg_duck.py, voice_clean.py, voice_level.py, music_fill.py, ffmpeg_utils.py, longdub_service.py, lipsync_service.py, tts_service.py, eleven_service.py, inworld_service.py, gemini_service.py, voice_match*.py, voice_numbers.py`), `main.py`'s `_ld_*` functions or the `voice_match_routes.register(...)` block; describe exact changes instead.
- Money code: nothing here touches credits, prices, billing or refunds. A change that makes dubs longer/shorter does not change prices, but say so if you see any link.
- Never name AI companies or models to customers.
- Memory and time: report the expected peak memory and seconds per minute of audio for the new measurement (measured here or estimated, labelled). Anything that loads a model is a question for Ali, not a default.
- Every number in the handoff is labelled **measured here / read from the files / cited / estimated**; do not claim accuracy on Ali's real clip that you did not measure.
- Run the full Python suite and report exact counts (expected environmental failures only: the git-history hygiene test and missing numpy/fastapi/Node on Ali's machine).

## Deliverables
1. Changed and new files.
2. `Lisan-AI-Job-12-Handoff.md`: plain-language first paragraph for Ali ("is a pausey speaker still called slow? how is pace measured now? what remains uncertain?"), method and citations, fixture results (before/after), Part B thresholds with reasoning, Part C research and recommendation, wiring for Claude, tests added with counts.
3. Questions for Ali, one decision each (turning slow tags back on; the slot-fill floor `TEMPO_MIN`; pause-preserving placement).
4. Literal git commands. No push.
