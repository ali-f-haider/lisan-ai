# Linguistic evidence and limitations

Research date: 2026-10-11. Spellings are lower-cased in registry matching; original text is preserved. An abbreviation can end a real sentence: the deterministic rule is a conservative hint, not grammatical certainty. All subtitle conventions, invented source dialogue and Arabic expectations still **need a native check** before release. These sources establish spelling conventions; they do not measure dubbing quality.

## en

**Read from the files:** abbreviations and ending expressions come from `line_tidy.py` and `subs_align.py`. Their disagreements remain separate `tidy` and `subtitle` profiles. Internal English vowel/word heuristics are preserved only on English paths. Registry function words are curated, not a measured frequency ranking.

## es

[RAE abbreviation list](https://www.rae.es/dpd/ayuda/abreviaturas) is the source for Sr., Sra., Srta., Dr., Dra., D., Dña., Ud., Uds., Prof. and etc. [RAE orthography](https://www.rae.es/diccionario-estudiante/docs/ortografia.pdf) supports paired question/exclamation marks. Lower-case matching does not change the original case, marks or accents. Source abbreviations suppress a proposed stop when they appear as the last token; whether a particular use really continues needs context/native review.

## fr

[OQLF civil-title abbreviations](https://vitrinelinguistique.oqlf.gouv.qc.ca/21435/les-abreviations-et-les-symboles/les-abreviations/cas-particuliers-dabreviations/abreviation-de-madame-de-mademoiselle-et-de-monsieur) supports M., MM., Mme and Mlle. Mme/Mlle do not get an abbreviation dot. [Canadian abbreviation list](https://nos-langues.canada.ca/fr/cles-de-la-redaction/abreviations-sigles-et-symboles-liste-alphabetique) and [university writing guide](https://www.uottawa.ca/notre-universite/administration-services/services-linguistiques/guide-de-redaction) cover Dr/Dre and Pr/Pre. [Canadian saint guidance](https://nos-langues.canada.ca/fr/cles-de-la-redaction/saint-majuscule-et-abreviation) gives St/Ste without an abbreviation dot. Dotted Dr./Prof. are compatibility candidates for regional subtitles and **need a native check**, not spellings attributed to these sources.

[OQLF spacing](https://vitrinelinguistique.oqlf.gouv.qc.ca/22039/la-typographie/espacement/espacement-avant-et-apres-les-signes-de-ponctuation-et-les-symboles) and [no-break spaces](https://vitrinelinguistique.oqlf.gouv.qc.ca/24566/la-typographie/espacement/contextes-exigeant-une-espace-insecable) show regional differences. The implementation accepts ordinary, no-break and narrow no-break space before marks and closing guillemets; it does not impose one typographic standard. The elision prefixes are proposed matching rules; uncommon names/apostrophes need native review. Literal apostrophes are retained in original spans.

## pt

[Ciberdúvidas academic forms of address](https://ciberduvidas.iscte-iul.pt/artigos/rubricas/idioma/formas-de-tratamento-em-contexto-academico/3952) supports Sr., Sra., Sr.ª, Dr., Dra., Dr.ª, Prof. and Profa.; [forms of address](https://ciberduvidas.iscte-iul.pt/consultorio/perguntas/pronomes-de-tratamento/3015) gives related forms. etc. is a common candidate; its subtitle use **needs a native check**. One entry covers Brazil/Portugal. This does not force a regional recogniser vocabulary or promise identical accent coverage; vocabulary, treatment of você/tu and name pronunciation still require checks in both varieties.

## it

[Treccani letter-writing guide](https://www.treccani.it/enciclopedia/lettere-commerciali-e-ufficiali-prontuario_%28Enciclopedia-dell%27Italiano%29/) supports Sig., Sig.ra, Dott., Dott.ssa, Prof., Prof.ssa and avv. [Crusca Dr./Dott.](https://accademiadellacrusca.it/it/consulenza/dott-e-dr-che-differenza-c%C3%A8-tra-le-due-abbreviazioni-e-quale-si-deve-usare-per-riferirsi-a-chi-svolge-la-professione-di-medico/18) supports those alternatives. Sig.na and ecc. are candidates **needing a native check**. [Crusca indefinite article](https://accademiadellacrusca.it/it/consulenza/larticolo-indeterminativo/82) distinguishes feminine un' from masculine un. Matching splits listed elision prefixes but preserves every apostrophe and character; additional prefixes need native verification. Capitalised Lei must be resolved from context, not treated automatically as a female referent.

## de

[Duden abbreviation rules](https://www.duden.de/sprachwissen/rechtschreibregeln/abkuerzungen) supports dotted abbreviation practice. [Hr.](https://www.duden.de/rechtschreibung/Hr_) and [Fr.](https://www.duden.de/rechtschreibung/Fr_) support the spellings; Fr. is ambiguous between Frau, Friday and currency. The proposed matching table also contains Dr., Prof., z. B., d. h., usw., bzw. and etc.; their exact subtitle distributions **need a native check**. [Duden numbers](https://www.duden.de/sprachwissen/rechtschreibregeln/zahlen-und-ziffern) explains numeral ordinals followed by a dot. `1.` is deliberately conservative/ambiguous. `3.5.` remains a decimal plus sentence stop. [Duden hyphen rules](https://www.duden.de/sprachwissen/rechtschreibregeln/bindestrich) informs compound handling. German noun capitals are not proof of sentence starts or personal gender.

## Matching evidence, rates and timing

**Estimated:** script-evidence guards of 50% and 80%, a minimum of four matching tokens and two distinct function words are conservative screening defaults, not calibrated probabilities. They cannot reliably distinguish similar Latin-script languages, names, code-switching or very short cues. Audio detection must decide the switch suggestion; never turn `looks_like(...)=compatible` into “verified language”. No-letter English input keeps the old unflagged result; new-language no-letter input is inconclusive.

**Read from files:** English uses 2–2.5 target Arabic words/second. **Estimated:** every new-language timing ratio starts at 1.0 so no unvalidated multiplier makes translations longer; the old “20% longer” English claim is not propagated to other languages. **Estimated:** the registry's descriptive six syllables/second placeholder is not used by translation, billing or pace measurement. No source speech rate has been measured locally. A reading-rate research PDF could not be fetched reliably, so no remembered paper numbers are presented as evidence.

To calibrate, obtain parallel, meaning-equivalent readings in each source language and MSA, including regional speakers. Measure articulated duration with the same silence rule. Compare each source→Arabic duration ratio to the English→Arabic ratio; set a robust conservative multiplier using the held-out clips. Measure actual generated duration and human judgement before changing the setting. A language's source syllables/second alone cannot justify Arabic words/second. LanguagePolicy accepts finite ratios in the estimated guard range 0.5–2.0; English prompt timing ignores overrides for exact compatibility.

**Unfinished linguistic validation:** each list has 40 curated function words, not the requested verified top-40 corpus ranking. Do not label them “most frequent” in product/research claims. [Snowball language algorithms](https://snowballstem.org/algorithms/) and [stopword ambiguity](https://snowballstem.org/texts/introduction.html) are relevant background, not a spoken-frequency ranking. A licensed, representative subtitle/dialogue corpus and native review are needed to rank them or tune language evidence. Script detection and English parity do not depend on a claimed ranking.

**Needs a native check:** all sound-description candidates, caption speaker-label/dash conventions, address/false-friend prompt notes and Arabic regex screens. Quotes/guillemets alone never prove a new speaker. Unknown parenthetical source content is retained for new languages; the English profile intentionally keeps its existing blanket removal behaviour.
