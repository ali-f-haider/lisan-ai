# Lisan AI: preserve person and gender in final ت speech endings

Prepared for Ali on 12 October 2026.

Ali reported that the correct written line **مَتَى شِئْتِ.** was spoken as **مَتَى شِئْتْ.** The local speech-preparation code reproduces that exact conversion before the request reaches a voice service. It also converted masculine **شِئْتَ** and first-person **شِئْتُ** to the same **شِئْتْ**. The earlier exception preserved **أنتَ / أنتِ** only; it did not cover verb endings.

With Ali's approval, `_pausal_word` in `arabic_waqf.py` now keeps an existing final fatha, kasra or damma on **ت**. This is a conservative rule across words, videos, source languages and users, rather than a list of individual verbs. It covers **قلتَ / قلتِ / قلتُ**, **فعلتَ / فعلتِ / فعلتُ**, partially marked words and shadda. It neither guesses person/gender nor adds a missing vowel. The copy sent for speech retains **مَتَى شِئْتِ.**; the stored text is unchanged.

The approved tradeoff is explicit: nouns ending in **ت** also keep an existing short vowel, such as **بِنْتِ** and **الْبَيْتِ**. This avoids relying on uncertain word classification. Tanween and other stopping rules retain their previous behavior; **بَيْتٌ** still becomes **بَيْتْ**. This fix covers the final **ت** family and is not a claim to solve every possible Arabic ending or speech-provider pronunciation issue.

## Changed files

- `arabic_waqf.py`: replace the narrow أنت exception with the general final-ت rule in `_pausal_word`; update its documentation example. Existing LF line endings retained.
- `tests/test_waqf_verb_endings.py`: ten new offline regressions covering the reported sentence, distinct person forms, 405 combinations of verbs/vowels/prefixes/modes, punctuation/gaps, partial marks/shadda, the approved noun tradeoff, existing tanween/other stops, idempotence, and actual long-dub/short-regeneration request text with mocked speech services.
- `tests/test_arabic_waqf.py`: two expected outputs updated for the approved policy (**الْبَيْتِ** retains kasra and **فَهِمْتُ** retains damma). Other assertions remain unchanged.
- `tests/test_waqf_pronouns.py`: the ordinary-noun stopping assertion now uses **كِتَابِ → كِتَابْ**; the new tests separately pin the approved **بِنْتِ** behavior.
- `WAQF_VERB_ENDINGS_FIX.md`: this report.

No other production function was changed. `config.py` was not edited. No commit, push, deployment, paid generation or live project edit was performed.

## Verification

Before applying the change, the new reported-sentence and distinct-person tests both failed against the original code. The candidate then passed all **51** targeted checks: ten new regressions, seven prior pronoun checks and 34 existing stop-form checks. Speech services were mocked; no real speech request was made.

The full discovery run required local-loopback permission for the Windows test client and a writable temporary folder. It was rerun with verbose diagnostics; these adjustments changed the test environment only, not application code or test assertions.

Completed full unittest discovery with the diagnostic wrapper:

```text
Ran 1326 tests in 574.079s

FAILED (failures=1, errors=2, skipped=1)
```

The diagnostic wrapper initially omitted the project folder from Python’s import path, so the existing `test_ambience_library` module failed to load. This was a runner error, not a missing application file. The runner was corrected, and that module was rerun with the normal `python -m unittest discover -s tests -p test_ambience_library.py -v` command:

```text
Ran 14 tests in 19.668s

OK
```

The full-suite log retains its initial import error; the separate recovery log records its resolution.

The full suite is not green in this sandbox. Its remaining failures are environment checks previously recorded:

- `test_no_currently_tracked_backend_file_matches_an_ignore_rule (test_repository_hygiene.BackupRulesTests.test_no_currently_tracked_backend_file_matches_an_ignore_rule)`: Git cannot change into the parent checkout from this sandbox.
- `test_many_saves_at_once_never_fail_and_leave_no_temporary_files (test_segment_writes.SegmentWrites.test_many_saves_at_once_never_fail_and_leave_no_temporary_files)`: Windows access denied during concurrent temporary-file replacement.

All 51 applied stop-form checks pass. Rerun the full suite in your normal terminal before publishing.


## Publishing and checking the affected line

The files are already updated in the local project folder. After deployment, regenerate the affected line with its stored **مَتَى شِئْتِ.**, using Auto or Stop. Existing downloaded audio is not changed by a deployment. Before deploying, Join is the available workaround for preserving the last word's written vowel on this line.

Run these commands yourself in Windows Command Prompt:

```bat
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"
git --no-pager add -- arabic_waqf.py tests/test_arabic_waqf.py tests/test_waqf_pronouns.py tests/test_waqf_verb_endings.py WAQF_VERB_ENDINGS_FIX.md
git --no-pager commit --only -m "Preserve person and gender in Arabic verb speech endings" -- arabic_waqf.py tests/test_arabic_waqf.py tests/test_waqf_pronouns.py tests/test_waqf_verb_endings.py WAQF_VERB_ENDINGS_FIX.md
git --no-pager push origin main
```

These commands include only this batch and exclude `config.py` and unrelated staged files.
