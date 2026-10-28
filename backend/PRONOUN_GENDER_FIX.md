# Lisan AI: Milch und Zucker findings and pronoun speech fix

Prepared for Ali on 12 October 2026. Only the approved pronoun speech-copy fix was applied. Volume matching and translation/transcription code were not changed. No commit, push, deployment, paid generation or live project edit was performed. `config.py` is unchanged.

## Applied fix: correct feminine text lost its vowel before speech

Ali supplied the editor text at 3:33: **وَأَنْتِ؟**. The subtitle export strips vowel marks and shows **وأنت؟**. The stored/editor version is feminine and correct for addressing a woman.

Under the default Auto mode or Stop, the existing `_pausal_word` replaced its final kasra with sukoon, sending **وَأَنْتْ؟**. It did exactly the same to masculine **وَأَنْتَ؟**. Thus the speech-preparation step removed the lexical distinction even when the translation was correct. The exact historical request and this line's selected waqf mode are not in the supplied event export, so the event log alone cannot establish every detail of the old provider request; the conversion itself is reproducible in the production code.

With Ali's explicit permission to edit the protected file, `_pausal_word` now preserves the final fatha or kasra in **أنتَ / أنتِ**, including **و / ف** prefixes. These vowels identify the addressee, rather than grammatical case. This rule is independent of source language, speaker, video and project. It does not add gender when it is missing, change ordinary noun stopping, or alter editor/subtitle text. Join behavior remains compatible. This is a tested exception for these pronouns, not a claim that every possible Arabic inflection or provider speech error has been solved.

Changed files:

- `arabic_waqf.py`: four added executable/comment lines in `_pausal_word`, plus one documentation example. Existing CRLF preserved.
- `tests/test_waqf_pronouns.py`: seven regression tests, including 108 combinations of genders, conjunctions, punctuation and modes; distinct speech copies; internal sentence stops; existing ordinary endings; no invented marks; idempotence; and the actual long-dub `_voice_text` wrapper.
- `PRONOUN_GENDER_FIX.md`: this handoff.

All seven new tests and all 34 existing Arabic stop-form tests pass individually. No previous assertion was weakened.

Final full `python -m unittest discover -s tests` run:

```text
Ran 1329 tests in 259.996s

FAILED (failures=1, errors=1, skipped=1)
```

The remaining blocked checks are:

- `test_no_currently_tracked_backend_file_matches_an_ignore_rule (test_repository_hygiene.BackupRulesTests.test_no_currently_tracked_backend_file_matches_an_ignore_rule)`: Git cannot change into the parent checkout through this sandbox.
- `test_many_saves_at_once_never_fail_and_leave_no_temporary_files (test_segment_writes.SegmentWrites.test_many_saves_at_once_never_fail_and_leave_no_temporary_files)`: Windows access-denied during concurrent segment-file replacement.

These are unchanged failures from the earlier batches. The full suite is **not green in this sandbox**. The seven new pronoun regressions and all 34 existing stop-form tests pass.

After publishing, regenerate only the 3:33 line in the completed-project correction editor, using the stored **وَأَنْتِ؟**. Existing rendered files are not rewritten by deploying this change. A new generation uses the usual site pricing; this investigation made no paid request. As an immediate workaround before deployment, setting this sentence-ending line to **Join** keeps the original gender vowel in the current code.

## Volume: same policy for all source languages

`voice_level.py` is shared by short and long dubbing. There is no German-only attenuation in that leveler. Each line's generated active-speech level is compared with its original reference. A speaker with at least four measured lines receives an anchor: the median original level. Each target follows 60% of its deviation from that anchor, limited to ±6 dB around it. Quiet originals therefore remain quiet; this is not an automatic overall loudness normalization feature.

Long-dub gains are limited to ±8 dB with a per-line peak guard; short-dub gains have a ±10 dB limit. These can prevent a quiet generated line from reaching its intended target. Provider voices and delivery tags can yield different raw levels even though the correction policy is shared.

In this job the log shows:

| Speaker | Original anchor | Median applied change | Lines hitting boost cap |
|---|---:|---:|---:|
| Speaker 1 | −22.4 dB | +4.1 dB | 7 |
| Speaker 2 | −22.7 dB | +2.4 dB | 0 (one line hit the reduction cap) |

This confirms that the engine attempted to raise the voices; it does not prove the exact post-mix discrepancy of every capped line because the event export contains a summary rather than each line's original/generated/peak/gain metadata.

The background is measured locally, including approximately half-second level windows. It follows the original background reference and the dubbed/original voice-level ratio within speech spans, with bounded gain changes. The current default adds a 3 dB reduction under speech; locally rebuilt sound has its separate lower-level policy. Clean-pause measurements guide replacement ambience within each sound section. These are changing local targets, not one fixed background volume throughout the video.

This job's final layer report gives the dubbed voice at **−20.5 dB** and background while the voice speaks at **−34.4 dB**, about 13.9 dB lower on average. These are the logger's RMS-based layer measurements, not LUFS and not proof that every moment has that balance.

Both supplied local videos were inspected without altering them. FFmpeg's measured input loudness:

| Measurement | Original | Arabic output | Difference |
|---|---:|---:|---:|
| Whole mix, integrated LUFS | −23.06 | −23.29 | −0.23 |
| Mix inside the exported subtitle windows, integrated LUFS | −20.95 | −21.17 | −0.22 |

These averages do not invalidate Ali's observation: they hide individual quiet lines. Active mono-frame measurements in matching subtitle windows showed, for example, a roughly **9.1 dB lower mix at 2:36.26** and **6.4 dB lower at 5:30.34**, while some other windows were louder. Those measurements include background and differing speech timing; they are not isolated-voice comparisons. The two audio streams are both stereo 44.1 kHz AAC, so this comparison did not find a stereo-versus-surround layout change.

Recommended next volume work: inspect the stored per-line voice levels, peak and applied gain for quiet passages; flag target misses; correct quiet lines only where headroom permits, with controlled dynamics when peaks prevent matching. Consider an explicit clearer-playback option for quiet recordings while preserving relative background balance. A blanket German boost or indiscriminately lifting all background/voice audio is not justified by these measurements. No such volume change was applied in this batch.

## Translation: corrected source and the connected Arabic sentence

Ali corrected his original report: the spoken German was affirmative **Ich habe damit gerechnet…** (“I expected that…”), without **nicht**. The earlier missing-negation finding is withdrawn. The export contains:

- 1:10.330–1:11.710: **Nee, ich hab**, Arabic **لا، كنت**.
- 1:21.440–1:23.640: **damit gerechnet, dass du dich irgendwann melden würdest.**, Arabic **أتوقع أن تتصلي يوما.**

**أتوقع** on its own expresses present expectation. However, the preceding Arabic row supplies **كنت**: together the rows read **لا، كنت أتوقع أن تتصلي يومًا**, which expresses past expectation. **Nee / لا** is the opening conversational reply; it does not negate **كنت أتوقع**. The German and Arabic both split the sentence across a gap of almost ten seconds. Reading only the second Arabic row can make its tense appear different, but the full stored sentence does not establish a past-to-present translation error.

This corrected example does not establish a lost negation, a transcription failure, or a translation-provider failure. The event export also cannot prove the exact historical speech request or whether every written word was spoken. No transcription, translation, timing or volume code was changed on the basis of this sentence. The independently reproduced pronoun gender bug at 3:33 and the volume measurements above remain valid.

## Delivery

The small ZIP contains the full three changed files, the unified diff, test logs and measurement data. Private videos, credentials and project records are not included. File comparison uses saved local before-edit bytes; no Git mutation was performed.

```bat
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"
git --no-pager add -- arabic_waqf.py tests/test_waqf_pronouns.py PRONOUN_GENDER_FIX.md
git --no-pager commit --only -m "Preserve Arabic addressee gender in pronoun speech copies" -- arabic_waqf.py tests/test_waqf_pronouns.py PRONOUN_GENDER_FIX.md
git --no-pager push origin main
```

These commands stage only this small fix; they do not publish other pending batches. They exclude `config.py` and unrelated staged work. Rerun `python -m unittest discover -s tests` in normal Command Prompt before publishing if the sandbox-only failures remain.
