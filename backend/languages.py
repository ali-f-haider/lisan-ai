"""Source-language data only. Adding a record does not enable it in the app.

Numeric defaults are estimates, not dubbing measurements. Linguistic provenance
and limitations live in docs/languages/sources.md. English data is read from
the existing helpers; do not replace their independent compatibility profiles.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SpeechRate:
    value: float | None
    unit: str
    status: str
    source: str


@dataclass(frozen=True)
class Language:
    code: str
    whisper_code: str
    english_name: str
    native_name: str
    arabic_name: str
    script: str
    sentence_end: str
    inverted_marks: str
    quote_pairs: tuple
    apostrophes: str
    elisions: tuple
    abbreviations: tuple
    dialogue_dashes: str
    speaker_change_markers: tuple
    sound_description: tuple
    music_marks: str
    speaker_label: str
    typographic_spacing: tuple
    stopwords: tuple
    stopwords_status: str
    speech_rate: SpeechRate
    timing_ratio_to_english: float
    timing_status: str
    sources: tuple
    native_check: tuple
    beta: bool = False
    variants: tuple = ()


_DASHES = "-‐‑‒–—―"
_QUOTES = (("\"", "\""), ("“", "”"), ("‘", "’"), ("«", "»"))
_LABEL = r"^[^\W\d_][^:\n]{0,39}:\s+"
_LEGACY_ABBR = ("mr.", "mrs.", "ms.", "dr.", "st.", "jr.", "sr.", "vs.", "no.",
                "mt.", "prof.", "gen.", "col.", "sgt.", "lt.", "capt.")
_DATA = (
    ("en", "English", "English", "الإنجليزية", "", (), _LEGACY_ABBR,
     ("music", "sighs", "laughs", "noise", "applause"),
     "the a an and or but if then of to in on at by for with from is are was were be been being i you he she it we they me him her us them my your his our their".split(),
     (), "read from the files: existing title list; function words curated, not frequency-ranked"),
    ("es", "Spanish", "Español", "الإسبانية", "¿¡", (),
     ("sr.", "sra.", "srta.", "dr.", "dra.", "d.", "dña.", "ud.", "uds.", "prof.", "etc."),
     ("música", "suspira", "ríe", "ruido", "aplausos"),
     "el la los las un una unos unas de del a al en con por para y o pero si no que como cuando donde quien yo tú usted él ella nosotros ustedes ellos ellas mi tu su es son se lo le".split(),
     (), "cited: RAE abbreviation list; function words curated, not frequency-ranked"),
    ("fr", "French", "Français", "الفرنسية", "", ("l", "j", "d", "qu", "n", "s", "m", "t", "c"),
     ("mme", "mlle", "m.", "mm.", "dr", "dre", "pr", "pre", "st", "ste", "dr.", "prof.", "etc."),
     ("musique", "soupire", "rit", "bruit", "applaudissements"),
     "le la les un une des de du à au aux en dans sur avec pour par et ou mais si ne pas que qui quoi je tu il elle nous vous ils elles me te se mon ton son ce est sont".split(),
     ("Accept ordinary, no-break and narrow no-break spaces before ? ! : ; and inside « ».",
      "Preserve the original spacing. French typographic practice varies by region; do not impose a rewrite."),
     "cited: French language office; Mme and Mlle have no abbreviation dot"),
    ("pt", "Portuguese", "Português", "البرتغالية", "", (),
     ("sr.", "sra.", "sr.ª", "dr.", "dra.", "dr.ª", "prof.", "profa.", "etc."),
     ("música", "suspira", "ri", "ruído", "aplausos"),
     "o a os as um uma uns umas de do da dos das em no na nos nas com por para e ou mas se não que quem como quando onde eu tu você ele ela nós vocês eles elas meu seu".split(),
     (), "cited: Portuguese language consultation; function words curated, not frequency-ranked"),
    ("it", "Italian", "Italiano", "الإيطالية", "", ("l", "un", "dell", "all", "nell", "sull", "dall", "coll", "quest", "quell"),
     ("sig.", "sig.ra", "sig.na", "dott.", "dott.ssa", "dr.", "prof.", "prof.ssa", "avv.", "ecc."),
     ("musica", "sospira", "ride", "rumore", "applausi"),
     "il lo la i gli le un uno una di del della dei delle a al alla in nel nella con per da e o ma se non che chi come quando dove io tu lui lei noi voi loro mi ti si".split(),
     (), "cited: Italian academy and dictionary; function words curated, not frequency-ranked"),
    ("de", "German", "Deutsch", "الألمانية", "", (),
     ("dr.", "prof.", "hr.", "fr.", "z. b.", "d. h.", "usw.", "bzw.", "etc."),
     ("musik", "seufzen", "lacht", "geräusch", "applaus"),
     "der die das ein eine einer eines einem einen und oder aber wenn dann von zu in auf an bei für mit aus ist sind war waren sein ich du er sie es wir ihr mich dich ihm uns euch mein dein nicht dass".split(),
     ("A digit followed by a dot may be an ordinal. Do not split inside decimal numbers or spaced abbreviations.",),
     "cited: German spelling dictionary; function words curated, not frequency-ranked"),
    ("nl", "Dutch", "Nederlands", "الهولندية", "", ("m", "z", "d", "zo"),
     ("dhr.", "mw.", "mevr.", "mr.", "drs.", "dr.", "ir.", "prof.", "bijv.", "enz.", "o.a.", "m.b.t.", "d.w.z.", "t.o.v.", "a.u.b.", "nr.", "blz.", "m.i.", "bv.", "etc."),
     ("muziek", "zucht", "lacht", "geluid", "applaus"),
     "de het een en of maar want dat die dit deze is zijn was waren ben bent ik jij je u hij zij we wij jullie ze mij jou ons in op aan met voor van naar bij niet als".split(),
     ("IJ stays i+j, never Y; compounds are not decomposed. Leading and internal apostrophes preserve original offsets.",
      "Decimal comma; thousands dot/space. Ordinals 1e/1ste/2de. Terminal etc./enz. are context-sensitive punctuation hints, not syntax proof."),
     "cited: Dutch Language Union spelling/advice and timed-text guide; function words curated, not frequency-ranked"),
    ("pl", "Polish", "Polski", "البولندية", "", (),
     ("p.", "dr", "dr.", "prof.", "inż.", "mgr", "mgr.", "ks.", "ul.", "godz.", "np.", "itd.", "itp.", "tzn.", "tj.", "m.in.", "ok.", "r.", "w.", "ww.", "por.", "zob.", "tys.", "zł"),
     ("muzyka", "wzdycha", "śmiech", "hałas", "oklaski"),
     "i a ale lub czy że gdy jeśli bo nie tak to jest są był była byli były ja ty on ona ono my wy oni one mnie mi ci mu się ten ta te z do na w o dla po przy bez za".split(),
     ("Dot after an integer may be an ordinal; year+r. and terminal abbreviations are ambiguous. Decimal comma; grouped numbers retain their source separators.",
      "Names inflect: matching does not stem or equate Marek/Marka/Markiem. Fold ł only for Polish matching; original spelling never changes."),
     "cited: Polish Language Council 2026 rules, Polish Academy dictionary paradigms and timed-text guide; function words curated, not frequency-ranked"),
)


def _record(row):
    code, en, native, ar, inverted, elisions, abbr, sound, words, spacing, provenance = row
    # Estimated 6 syllables/second is descriptive only. No paper's reading-task
    # rate is treated as a verified rate for arbitrary uploaded dialogue.
    return Language(code, code, en, native, ar, "latin", ".!?…", inverted,
                    _QUOTES + ((("„", "“"), ("»", "«")) if code == "de" else (("„", "”"),) if code in ("nl", "pl") else ()),
                    "'’‘", elisions, abbr, _DASHES,
                    ("leading dialogue dash", "different leading speaker labels",
                     "guillemets quote speech but alone do not prove a speaker change"),
                    tuple(r"(?i)\b" + word + r"\b" for word in sound), "♪♫♩",
                    _LABEL, spacing, tuple(words[:40]), provenance,
                    SpeechRate(None, "syllables/second", "unmeasured", "no source-language dubbing rate measured") if code in ("nl", "pl") else
                    SpeechRate(6.0, "syllables/second", "estimated",
                               "placeholder only; no local or per-language dubbing rate measured"),
                    1.0, "estimated" if code in ("nl", "pl") else "estimated: retain the current Arabic target budget until calibrated",
                    ("docs/languages/sources.md#" + code,),
                    ("Subtitle sound descriptions and speaker-label conventions need a native check.",
                     "Abbreviations at the end of a real sentence remain ambiguous.",
                     "The 40 function words are curated evidence, not a measured top-40 ranking.")
                    + (("New-source conventions, abbreviations and Arabic expectations need a native check before enablement.",) if code in ("nl", "pl") else ())
                    + (("Hr. and Fr. are cited spellings; their frequency in subtitles needs a native check.",) if code == "de" else ())
                    + (("St/Ste, Dr/Dre and Pr/Pre have no abbreviation dot in the cited Canadian practice. Dotted Dr./Prof. are compatibility candidates needing a native check for regional subtitles.",) if code == "fr" else ()))


_REGISTRY = tuple(_record(row) for row in _DATA)
_BY_CODE = {record.code: record for record in _REGISTRY}
NEW_LANGUAGES = ("es", "fr", "pt", "it", "de")
STAGED_LANGUAGES = ("nl", "pl")
# Claude changes only this switch after engine/UI wiring and release approval.
ENABLED_LANGUAGES = ("en",) + NEW_LANGUAGES + STAGED_LANGUAGES


def get_record(code=None):
    """Development metadata, including staged records; does not authorize jobs."""
    if isinstance(code, Language):
        code = code.code
    return _BY_CODE.get(code.strip().lower(), _REGISTRY[0]) if isinstance(code, str) else _REGISTRY[0]


def get(code=None):
    """Enabled saved-job lookup; staged, unknown and malformed codes stay English."""
    record = get_record(code)
    return record if record.code in ENABLED_LANGUAGES else _REGISTRY[0]


def registered():
    """All data records, including development-only staged languages."""
    return _REGISTRY


def is_registered(code):
    return isinstance(code, str) and code.strip().lower() in _BY_CODE


def supported():
    """Only explicitly enabled source languages for production selectors."""
    return tuple(record for record in _REGISTRY if record.code in ENABLED_LANGUAGES)


def is_supported(code):
    return isinstance(code, str) and code.strip().lower() in ENABLED_LANGUAGES


def names(code=None, ui_language="en"):
    record = get(code)
    return record.arabic_name if ui_language == "ar" else record.native_name if ui_language == "native" else record.english_name


@dataclass(frozen=True)
class LanguagePolicy:
    """Ali's settings; the lead owns persistence and UI wiring."""
    enabled_languages: tuple = ENABLED_LANGUAGES
    beta_languages: tuple = ()
    price_policy: str = "same_as_english"
    auto_detect: bool = False
    regional_entries: bool = False
    subtitle_policy: str = "source_only"
    beta_exit_gate: str = "native_review_and_measured_audio_quality"
    timing_overrides: tuple = ()

    def timing_ratio(self, code):
        for key, value in self.timing_overrides:
            if key == get_record(code).code and type(value) in (int, float) and math.isfinite(value) and 0.5 <= value <= 2.0:
                return float(value)
        return get_record(code).timing_ratio_to_english


def arabic_word_budget(code=None, policy=None):
    """Estimated target words/second, not source words/second or a pricing rule."""
    ratio = (policy or LanguagePolicy()).timing_ratio(code)
    return (2.0 / ratio, 2.5 / ratio)
