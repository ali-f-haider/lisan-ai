# Adding another source language

The local engine, pages and API now integrate the six registered source languages. Deployment and native language-quality approval remain with Ali and Claude. Arabic stays the target. Keep version changes with Claude and money code unchanged.

Add a frozen language record with recogniser code, names, script, punctuation, quotes, elisions, subtitle descriptions and evidence metadata. `beta` and `variants` are settings; new entries are not automatically advertised by a production page. `LanguagePolicy` controls enabled languages, pricing policy, source selection, region entries, subtitle policy and timing ratios. Persist those settings through the existing configuration mechanism only after checking its schema; this package performs no database changes.

Choose the correct text strategy, not only a language name. Token matching must preserve original spans and offsets, accents are folded only for matching, speech descriptions must not eat real dialogue, and quotes alone must not become speakers. Document ambiguous abbreviations and ordinals. Keep the **two English profiles separate** and preserve every old English engine branch until an end-to-end comparison proves equivalence.

Add invented source dialogue and subtitle fixtures with independent expected splits and speaker boundaries. Native reviewers must approve source idioms, abbreviation usage, address, gender, number and Arabic expectations. Translation regexes are a preliminary screen: synonyms can fail a correct result and a substring can pass a wrong sentence. Review complete meaning, audible delivery, overlap and duration separately. A small invented text set is not a release benchmark or a ranking of languages.

Run the new pure tests, English comparisons, full Python suite and Node suite. Use `tools/language_trial.py --dry` without a key first. For a live trial, supply `GEMINI_API_KEY` securely and explicit current input/output prices; approve a spending limit first. The tool reports returned input/output/thought tokens and their estimated cost. Missing usage and hidden retries make that report a lower bound, not a billing receipt. Do not guess prices or claim dry results measure quality.

## Languages excluded from this release

All requirements below are **documented engineering needs / likely risks**, not implementations or measured quality claims.

| Future source | Additional work before an entry is safe |
|---|---|
| Chinese | Word segmentation without spaces; full-width punctuation; simplified/traditional variants; subtitle alignment by words or characters; names, tones and native delivery review. |
| Japanese | Script mixture and segmentation; full-width punctuation and quotation brackets; honorifics and omitted subjects; target gender/addressee review; ruby/reading markup. |
| Thai | Reliable word/sentence segmentation without ordinary word spaces; combining marks; subtitle offset preservation; native prosody and name/number checks. |
| Hindi | Devanagari combining marks, conjuncts, danda and token boundaries; gender/number/address; code-switching, subtitle transliteration and names. |
| Urdu | Arabic-derived script, right-to-left source editor, joining/combining characters and regional letters; honorifics/gender and punctuation; source-versus-Arabic detection. |
| Persian | Arabic-derived script, Persian letter forms and zero-width non-joiner; source-versus-Arabic evidence; right-to-left editing, names and politeness. |
| Russian | Cyrillic-aware script evidence and matching; inflected names/gender; abbreviation/initial handling; source reading and target timing measurements. |
| Turkish | Locale-aware I/İ/ı/i casing, suffixes and agglutinated words; sentence/abbreviation rules; honorifics; gender often absent in source and must remain uncertain. |
| Arabic as source | Separate source/target roles even when both are Arabic; dialect identification versus MSA output; avoid accidental identity translation; diacritics, right-to-left source, Arabic subtitles and voice consent review. |

## Validation and launch settings

Ali has approved launch without Beta labels, one Spanish entry and one Portuguese entry, and the same per-minute rate as English. Regional entries may later improve vocabulary and pronunciation control but add UI/settings/testing work; a recogniser language code alone cannot select an accent or guarantee regional quality. Keep variants empty for now. Optional choice is **not** permission to silently rewrite a detected mismatch: show the one-click switch, preserve the original upload, and re-run only after the person chooses. A subtitle in another language is rejected, not translated on import.

The registry's 40 function words are curated rather than a verified top-frequency list, and source speech rates/timing multipliers remain estimates. See sources.md for those unfinished validation items. Do not use them as probabilities, per-language pricing or promised production quality.
