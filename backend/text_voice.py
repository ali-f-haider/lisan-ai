"""Text to voice (Ali's design, Nov 2026): a person who has only a text gets it spoken in Arabic.

The text may be in any language Lisan AI accepts (languages.ENABLED_LANGUAGES) or in Arabic. It is cut into lines (a script
written as "Name: text" gets one speaker per name), then:
  1. prepare: a non-Arabic text is translated completely, with no time limit (gemini_service.translate_script_lines), with the
     speakers' genders the person chose, the question of who each "you" is said to (English scripts with several speakers,
     addressee_ask) and the tashkeel proofread; an Arabic text keeps its words and only gets the missing tashkeel (the same
     word-by-word merge and proofread the long dub uses). Paid per 1,000 characters of the text.
  2. the person corrects the Arabic and the emotion of any line, and picks a voice for each speaker: a stock voice, one of
     their saved voices, or their own voice (cloned in main.py, then given the Arabic accent here: localize_later).
  3. speak: only the lines that are new or changed since the last time are spoken (paid by characters, the same rate as the
     dub), then every line is put in order with short pauses, levelled and saved as one MP3.
Every charge has its own ID saved in the project before the money moves; whatever could not be delivered is given back
(Hooks.refund, retried in the background when a reply is lost). Projects are kept KEEP_DAYS days after their last change.
Nothing here imports main.py: main.py sets the Hooks (configure) and the routes.
"""
import hashlib
import json
import math
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

import languages
from config import DATA_DIR, GEMINI_API_KEY, INWORLD_API_KEY
from shortdub_billing import debit_confirmed as _debit_ok

ENABLED = os.environ.get("TEXT_VOICE", "1").strip() != "0"          # "0" hides the page and refuses new projects


def _env_num(name, default):
    try:
        v = os.environ.get(name, "")
        return float(v) if v.strip() else float(default)
    except ValueError:
        return float(default)


MAX_CHARS = int(_env_num("TEXT_VOICE_MAX_CHARS", 20000))      # one project's text (about 20 minutes of speech)
MAX_SPEAKERS = 20
MAX_LINES = 600
LINE_CHARS = 280                # a line (one translation item and one request to the voice) holds at most this much text
MAX_ARABIC_CHARS = 1500         # a corrected Arabic line
BATCH_CHARS = 3000              # text translated in one request
BATCH_LINES = 30
# Prices (credits). The translation: Gemini Flash at the January 2027 price ($1.50 / $7.50 per 1M tokens, 1 credit = 1 cent):
# about 4 cents per 1,000 characters with the proofread and the "you" question, so 5. An Arabic text only gets tashkeel: 2.
TRANSLATE_PER_1000 = _env_num("TEXT_VOICE_TRANSLATE_PER_1000", 5)
ARABIC_PER_1000 = _env_num("TEXT_VOICE_ARABIC_PER_1000", 2)
PAUSES = {"same": 0.35, "turn": 0.6, "para": 0.9}       # seconds of silence before a line: same speaker, new speaker, new paragraph
SPEEDS = (0.85, 0.9, 1.0, 1.1, 1.2)      # the speed of the whole text or of one line (played faster or slower, the voice stays the same)
PRON_MAX = 60                   # words in the pronunciation list of a project
PRON_TERM_MAX = 80              # characters on each side of one entry
READ_FILE_MAX = 2_000_000       # an uploaded .txt, .docx or .srt file
KEEP_DAYS = 30                 # the text of a project, after its last change
WORKERS = threading.Semaphore(2)                       # projects being worked on at once (whole server)
NARRATOR = "Narrator"


class Hooks:
    """Set by main.py (configure). The defaults do nothing, so tests can import the module."""
    charge = staticmethod(lambda uid, amount, action, job_id, operation_id=None: None)
    refund = staticmethod(lambda uid, amount, job_id, debit_id=None, key=None: None)
    chars_per_credit = staticmethod(lambda: 60)
    voice_status = staticmethod(lambda voice_id: "ready")     # "ready" | "preparing" | "failed" (own voices getting the accent)
    log = staticmethod(lambda uid, job_id, step, status, detail="": None)
    # hours a finished MP3 is kept: the same rule as every other finished file (30 days with a subscription, else 48 hours)
    keep_hours = staticmethod(lambda uid: KEEP_DAYS * 24)


def configure(**kwargs):
    for k, v in kwargs.items():
        setattr(Hooks, k, staticmethod(v))


ROOT = Path(DATA_DIR) / "text_voice"
_LOCK = threading.RLock()
_LOCKS = {}
_RUNNING = set()


def _lock_for(job_id):
    with _LOCK:
        return _LOCKS.setdefault(job_id, threading.RLock())


def _now():
    return time.time()


def _valid_id(job_id):
    return bool(re.fullmatch(r"[0-9a-f]{32}", str(job_id or "")))


def _dir(job_id):
    return ROOT / job_id


def _ev(job, step, status="ok", detail=""):
    try:
        print(f"[text-voice] {job['id']} {step} {status} {detail}"[:400])
        Hooks.log(job.get("uid"), job["id"], step, status, str(detail)[:1500])
    except Exception:
        pass


def _save(job):
    job["updated"] = _now()
    d = _dir(job["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / ("job.json." + uuid.uuid4().hex + ".tmp")
    with _lock_for(job["id"]):
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / "job.json")


def load(job_id):
    if not _valid_id(job_id):
        return None
    try:
        return json.loads((_dir(job_id) / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def owned(job_id, uid):
    """The project when it belongs to this account, else None (the routes answer "not found")."""
    job = load(job_id)
    return job if job and uid and str(job.get("uid")) == str(uid) else None


# ------------------------------------------------------------------ the text

_LABEL = re.compile(r"^\s*([^\W\d_][^:\n]{0,39}?)\s*[:：]\s+(\S.*)$")
_SENTENCE = re.compile(r"(?<=[.!?؟…。])[\"'”»)]*\s+")


def source_languages():
    """[{code, name, native, arabic}] the page offers: every enabled language, then Arabic."""
    out = [{"code": r.code, "name": r.english_name, "native": r.native_name, "arabic": r.arabic_name} for r in languages.supported()]
    out.append({"code": "ar", "name": "Arabic", "native": "العربية",
                "arabic": "العربية"})
    return out


def clean_language(code):
    code = str(code or "").strip().lower()
    if code == "ar":
        return "ar"
    if languages.is_supported(code):
        return languages.get(code).code
    raise ValueError("Choose the language of your text.")


def _pieces(text):
    """A long text cut into pieces of at most LINE_CHARS: at sentence ends, else at commas, else at spaces."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= LINE_CHARS:
        return [text] if text else []
    out, cur = [], ""
    for sentence in _SENTENCE.split(text):
        parts = [sentence]
        if len(sentence) > LINE_CHARS:
            parts, cur2 = [], ""
            words = []
            for w in sentence.split():
                words += [w[k:k + LINE_CHARS] for k in range(0, len(w), LINE_CHARS)]      # a "word" longer than a line is cut
            for w in words:
                if cur2 and len(cur2) + 1 + len(w) > LINE_CHARS:
                    parts.append(cur2)
                    cur2 = w
                else:
                    cur2 = (cur2 + " " + w).strip()
            if cur2:
                parts.append(cur2)
        for p in parts:
            if cur and len(cur) + 1 + len(p) > LINE_CHARS:
                out.append(cur)
                cur = p
            else:
                cur = (cur + " " + p).strip()
    if cur:
        out.append(cur)
    return [p for p in out if p]


def _speakable(text):
    """True when a text has a letter or a digit. A line of only marks or symbols ("...", "-", "*") has nothing to say: the voice
    answers it with no audio, so it is never sent, charged or counted, and the audio just keeps the pause there."""
    return bool(re.search(r"[^\W_]", str(text or "")))


def parse_script(text):
    """(speakers, lines). A script where at least two lines, and at least half of them, start with "Name:" gets one speaker per
    name (an unlabelled line continues the speaker before it); any other text has one speaker, the narrator."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = text.split("\n")
    filled = [ln for ln in raw if ln.strip()]
    hits = [m for m in (_LABEL.match(ln) for ln in filled) if m and len(m.group(1).split()) <= 4]
    names = []
    for m in hits:
        n = m.group(1).strip()
        if n not in names:
            names.append(n)
    labelled = len(hits) >= 2 and len(hits) * 2 >= len(filled) and len(names) <= MAX_SPEAKERS
    speakers, ids = [], {}

    def speaker(name):
        if name not in ids:
            ids[name] = "s%d" % (len(speakers) + 1)
            speakers.append({"id": ids[name], "name": name, "gender": "", "voice_id": "", "voice_name": ""})
        return ids[name]

    lines, current, blank = [], None, False
    for ln in raw:
        if not ln.strip():
            blank = True
            continue
        m = _LABEL.match(ln) if labelled else None
        named = bool(m and len(m.group(1).split()) <= 4)
        pieces = [p for p in _pieces(m.group(2) if named else ln) if _speakable(p)]
        if not pieces:              # a line of only marks or symbols ("***", "-") is a break between paragraphs, not a line
            blank = True
            continue
        sid = speaker(m.group(1).strip()) if named else (current or speaker(NARRATOR))
        for i, piece in enumerate(pieces):
            pause = "same"
            if lines:
                if i == 0 and blank:
                    pause = "para"
                elif sid != lines[-1]["speaker_id"]:
                    pause = "turn"
            lines.append({"id": "l%d" % (len(lines) + 1), "speaker_id": sid, "text": piece, "pause": pause,
                          "arabic": "", "emotion": "", "gender_check": False, "audio": ""})
        current, blank = sid, False
    return speakers, lines


# ------------------------------------------------------------------ projects

def new_job(uid, text, source_language, title=""):
    """(project, None) or (None, (message, status code)). Free: nothing is charged before the person confirms a price."""
    if not ENABLED:
        return None, ("This feature is not available right now.", 503)
    try:
        lang = clean_language(source_language)
    except ValueError as ex:
        return None, (str(ex), 400)
    text = str(text or "").strip()
    if not text:
        return None, ("Please paste or type your text first.", 400)
    if len(text) > MAX_CHARS:
        return None, (f"Your text has {len(text):,} characters. The limit is {MAX_CHARS:,}. Please split it into parts.", 413)
    speakers, lines = parse_script(text)
    if not lines:
        return None, ("Please paste or type your text first.", 400)
    if len(lines) > MAX_LINES:
        return None, (f"Your text has too many lines ({len(lines)}). The limit is {MAX_LINES}. Please split it into parts.", 413)
    cleanup_old()
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:120] or re.sub(r"\s+", " ", text)[:60]
    job = {"id": uuid.uuid4().hex, "uid": uid, "created": _now(), "updated": _now(), "title": title,
           "source_language": lang, "speakers": speakers, "lines": lines, "prepared": False,
           "busy": "", "percent": 0, "message": "", "error": "", "ops": {}, "audio": {}}
    _save(job)
    _ev(job, "created", "ok", f"{lang}, {len(text)} characters, {len(lines)} lines, {len(speakers)} speaker(s)")
    return job, None


def _text_chars(job):
    return sum(len(ln["text"]) for ln in job["lines"])


def prepare_price(job):
    per = ARABIC_PER_1000 if job["source_language"] == "ar" else TRANSLATE_PER_1000
    return max(1, int(math.ceil(_text_chars(job) / 1000.0 * per)))


def _tag(emotion):
    import inworld_service
    return inworld_service.instruction_tag(emotion)


def _gap(job, i):
    """Silence after line i (for the spoken stop form at its end)."""
    nxt = job["lines"][i + 1] if i + 1 < len(job["lines"]) else None
    return PAUSES[nxt["pause"]] if nxt else 2.0


# ------------------------------------------------------------------ pronunciation list and speed (Ali, Nov 2026)
# The pronunciation list: "written = how to say it", for a name or word the voice says wrongly. Only the copy sent to the
# voice changes (the Arabic text shown and the subtitles stay as written), so a change re-makes only the lines with that word.
_DIAC = "[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]"
_PRON_SPLIT = re.compile(r"\s*(?:=>|->|\u2192|\u2190|=|\t)\s*")
_PRON_RE = {}


def _bare(text):
    """The text without diacritics and the stretching mark (ـ)."""
    return re.sub(_DIAC, "", str(text or ""))


def parse_pronunciation(text):
    """'written = how to say it' per line -> ([{word, say}], number of lines that could not be read). A blank line or a line
    starting with # is ignored; a word written twice keeps its first entry."""
    out, bad, seen = [], 0, set()
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = _PRON_SPLIT.split(line, maxsplit=1)
        if len(parts) != 2:
            bad += 1
            continue
        word, say = parts[0].strip(), parts[1].strip()
        if (not _speakable(word) or not _speakable(say) or len(word) > PRON_TERM_MAX or len(say) > PRON_TERM_MAX
                or len(_bare(word).split()) > 6):
            bad += 1
            continue
        key = _bare(word).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"word": word, "say": say})
        if len(out) >= PRON_MAX:
            break
    return out, bad


def _pron_regex(word):
    """The word in a line, with or without diacritics, also after و ف ب ل ك and ال (which are kept)."""
    key = _bare(word).lower()
    if key not in _PRON_RE:
        letters = "".join(("\\s+" if ch.isspace() else "[\u0627\u0623\u0625\u0622]" if ch in "\u0627\u0623\u0625\u0622" else re.escape(ch))
                          + _DIAC + "*" for ch in re.sub(r"\s+", " ", key))      # an alef with or without hamza is the same letter here
        prefix = "((?:[\u0648\u0641\u0628\u0644\u0643]" + _DIAC + "*){0,2}(?:\u0627" + _DIAC + "*\u0644" + _DIAC + "*)?)" if re.match(r"[\u0600-\u06FF]", key) else "()"
        _PRON_RE[key] = re.compile(r"(?<!\w)" + prefix + letters + r"(?!\w)", re.IGNORECASE)
    return _PRON_RE[key]


def _spoken_arabic(job, ln):
    """The Arabic of a line as it is sent to the voice: with the pronunciation list applied (longest words first)."""
    text = (ln.get("arabic") or "").strip()
    for e in sorted(job.get("pronunciation") or [], key=lambda e: -len(_bare(e["word"]))):
        text = _pron_regex(e["word"]).sub(lambda m, say=e["say"]: m.group(1) + say, text)
    return text


def _voice_copy(job, i):
    """What the voice really reads for line i (the pronunciation list and the stop form: مُمْتَازٌ، -> مُمْتَازْ،), shown under the line so the
    person sees what they will hear (Ali, 6 Nov 2026). "" when it is the same as the line, or the line has nothing to say."""
    ln = job["lines"][i]
    if not _speakable(ln.get("arabic")):
        return ""
    try:
        import arabic_waqf
        said = arabic_waqf.pausal(_spoken_arabic(job, ln), _waqf_of(ln), _gap(job, i))
    except Exception:
        return ""
    return said if said != (ln.get("arabic") or "").strip() else ""


def _waqf_of(ln):
    """"exact" when the person ticked "Read it exactly as I wrote it" for this line (no stop form at all), else "auto"."""
    return "exact" if ln.get("as_written") else "auto"


def _stop_form_changes(job, i):
    """True when the stop form changes something in line i (only then the page offers "Read it exactly as I wrote it")."""
    ln = job["lines"][i]
    if not _speakable(ln.get("arabic")):
        return False
    try:
        import arabic_waqf
        said = _spoken_arabic(job, ln)
        return arabic_waqf.pausal(said, "auto", _gap(job, i)) != said
    except Exception:
        return False


def _spoken_len(job, i):
    ln = job["lines"][i]
    return len(_tag(ln.get("emotion"))) + len(_spoken_arabic(job, ln))


def _speed_of(job, ln):
    v = ln.get("speed") or job.get("speed") or 1.0
    return float(v) if float(v) in SPEEDS else 1.0


def _clean_speed(v):
    try:
        v = round(float(v), 2)
    except (TypeError, ValueError):
        return None
    return v if v in SPEEDS else None


def set_options(job, payload):
    """The speed of the whole text and the pronunciation list. Free. Returns (True, {"bad": n}) or (False, (message, status))."""
    if job.get("busy"):
        return False, ("Please wait until the current step has finished.", 409)
    payload = payload if isinstance(payload, dict) else {}
    bad = 0
    if "speed" in payload:
        v = _clean_speed(payload.get("speed"))
        if v is None:
            return False, ("Please choose one of the speeds offered.", 400)
        job["speed"] = v
    if "pronunciation" in payload:
        text = str(payload.get("pronunciation") or "")
        if len(text) > 12000:
            return False, ("The pronunciation list is too long.", 400)
        entries, bad = parse_pronunciation(text)
        job["pronunciation"] = entries
        _ev(job, "pronunciation_saved", "ok", f"{len(entries)} words, {bad} lines not understood")
    _save(job)
    return True, {"bad": bad}


def _voice_of(job, line):
    sp = next((s for s in job["speakers"] if s["id"] == line["speaker_id"]), None)
    return (sp or {}).get("voice_id") or ""


def _line_hash(job, i):
    ln = job["lines"][i]
    raw = "|".join([_voice_of(job, ln), ln.get("emotion") or "", _spoken_arabic(job, ln), "%.2f" % _gap(job, i)])
    if ln.get("take"):                          # "Make this line again": a new take of the same text (only then, so older hashes stay)
        raw += "|take:%d" % int(ln["take"])
    if ln.get("as_written"):                    # "Read it exactly as I wrote it": no stop form, so the audio is different
        raw += "|exact"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _line_file(job, ln):
    return _dir(job["id"]) / "lines" / f"{ln['id']}_{ln.get('audio')}.mp3"


def _to_speak(job):
    """Indexes of the lines with Arabic whose spoken copy is missing or older than the text, voice or emotion."""
    out = []
    for i, ln in enumerate(job["lines"]):
        if not _speakable(ln.get("arabic")):
            continue
        if ln.get("audio") != _line_hash(job, i) or not _line_file(job, ln).is_file():
            out.append(i)
    return out


def speak_price(job, todo=None):
    todo = _to_speak(job) if todo is None else todo
    chars = sum(_spoken_len(job, i) for i in todo)
    cpc = max(1, int(Hooks.chars_per_credit() or 60))
    return (int(math.ceil(chars / float(cpc))) if chars else 0), chars


def _final_hash(job):
    parts = [ln.get("audio") or "" for ln in job["lines"] if _speakable(ln.get("arabic"))]
    raw = "|".join(parts) + "|" + "|".join(ln["pause"] for ln in job["lines"])
    speeds = [_speed_of(job, ln) for ln in job["lines"] if _speakable(ln.get("arabic"))]
    if any(v != 1.0 for v in speeds):           # only then, so the audio made before speeds existed stays current
        raw += "|speed:" + ",".join("%.2f" % v for v in speeds)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def public(job):
    """What the page sees (never the account id or the payment ids)."""
    todo = _to_speak(job) if job.get("prepared") else []
    price, _ = speak_price(job, todo) if job.get("prepared") else (0, 0)
    audio = job.get("audio") or {}
    current = bool(audio.get("hash")) and audio.get("hash") == _final_hash(job) and not todo and (_dir(job["id"]) / "final.mp3").is_file()
    spoken = set()
    if job.get("prepared"):
        for i, ln in enumerate(job["lines"]):
            if ln.get("audio") and ln.get("audio") == _line_hash(job, i) and _line_file(job, ln).is_file():
                spoken.add(ln["id"])
    return {
        "id": job["id"], "title": job.get("title") or "", "source_language": job["source_language"],
        "created": job.get("created"), "updated": job.get("updated"),
        "speakers": [{k: s.get(k, "") for k in ("id", "name", "gender", "voice_id", "voice_name")} for s in job["speakers"]],
        "lines": [{"id": ln["id"], "speaker_id": ln["speaker_id"], "text": ln["text"], "pause": ln["pause"],
                   "arabic": ln.get("arabic") or "", "emotion": ln.get("emotion") or "", "gender_check": bool(ln.get("gender_check")),
                   "spoken": ln["id"] in spoken, "speed": ln.get("speed") or None,
                   "silent": not _speakable(ln.get("arabic")), "voice_text": _voice_copy(job, i),
                   "as_written": bool(ln.get("as_written")), "stop_form": _stop_form_changes(job, i)} for i, ln in enumerate(job["lines"])],
        "chars": _text_chars(job), "prepared": bool(job.get("prepared")),
        "prepare_price": prepare_price(job), "speak_price": price, "lines_to_speak": len(todo),
        "busy": job.get("busy") or "", "percent": int(job.get("percent") or 0), "message": job.get("message") or "",
        "error": job.get("error") or "", "audio_ready": current,
        # the last MP3 stays to listen to and download after a correction, until the new one is made (Ali, 3 Nov 2026)
        "audio_file": bool(audio_path(job)), "audio_outdated": bool(audio_path(job)) and not current,
        "audio_seconds": audio.get("seconds") if audio_path(job) else None,
        "notes": job.get("notes") or [],
        "speed": float(job.get("speed") or 1.0), "speeds": list(SPEEDS),
        "pronunciation": "\n".join(e["word"] + " = " + e["say"] for e in job.get("pronunciation") or []),
        "pronunciation_count": len(job.get("pronunciation") or []),
        "subtitles_ready": bool(_timing(job)),
    }


def note_download(job):
    """The MP3 was downloaded (counted for the admin's Activity view; not a change by the person)."""
    try:
        with _lock_for(job["id"]):
            fresh = load(job["id"]) or job
            fresh["downloads"] = int(fresh.get("downloads") or 0) + 1
            fresh["last_download"] = _now()
            _save_keep_updated(fresh)
    except Exception as ex:
        print(f"[text-voice] download not counted: {ex}")


def admin_summary(uid=None):
    """{uid: what that account did with Text to voice}, read from the projects on disk, for the admin's Activity view
    (with uid: only that account, for the feedback question)."""
    out = {}
    for d in (ROOT.iterdir() if ROOT.is_dir() else []):
        job = load(d.name) if d.is_dir() else None
        if not job or not job.get("uid") or (uid is not None and str(job["uid"]) != str(uid)):
            continue
        u = out.setdefault(str(job["uid"]), {"projects": 0, "chars": 0, "arabic_made": 0, "audio_made": 0, "audio_runs": 0,
                                             "downloads": 0, "languages": [], "speakers_max": 0, "failures": 0, "last_failure": None,
                                             "last_active": 0})
        u["projects"] += 1
        u["chars"] += _text_chars(job)
        u["arabic_made"] += 1 if job.get("prepared") else 0
        runs = int(job.get("audio_count") or (1 if (job.get("audio") or {}).get("hash") else 0))
        u["audio_made"] += 1 if runs else 0
        u["audio_runs"] += runs
        u["downloads"] += int(job.get("downloads") or 0)
        if job["source_language"] not in u["languages"]:
            u["languages"].append(job["source_language"])
        u["speakers_max"] = max(u["speakers_max"], len(job.get("speakers") or []))
        lf = job.get("last_failure")
        if lf:
            u["failures"] += 1
            if not u["last_failure"] or float(lf.get("at") or 0) > float(u["last_failure"].get("at") or 0):
                u["last_failure"] = lf
        u["last_active"] = max(u["last_active"], float(job.get("updated") or 0), float(job.get("last_download") or 0))
    return out


def list_jobs(uid, limit=30):
    out = []
    if not uid or not ROOT.is_dir():
        return out
    for d in ROOT.iterdir():
        job = load(d.name) if d.is_dir() else None
        if job and str(job.get("uid")) == str(uid):
            out.append({"id": job["id"], "title": job.get("title") or "", "updated": job.get("updated"),
                        "source_language": job["source_language"], "lines": len(job["lines"]),
                        "audio_ready": bool((job.get("audio") or {}).get("hash"))})
    out.sort(key=lambda j: j.get("updated") or 0, reverse=True)
    return out[:limit]


def delete_job(job):
    if job.get("busy"):
        return False, ("Please wait until the current step has finished.", 409)
    shutil.rmtree(_dir(job["id"]), ignore_errors=True)
    return True, None


def _drop_audio(job):
    """The spoken lines and the MP3 of a project are removed (its text stays)."""
    shutil.rmtree(_dir(job["id"]) / "lines", ignore_errors=True)
    (_dir(job["id"]) / "final.mp3").unlink(missing_ok=True)
    for ln in job["lines"]:
        ln["audio"] = ""
    job["audio"] = {}


def cleanup_old(now=None):
    """Projects not changed for KEEP_DAYS days are removed (text and audio). The audio of the others is removed once it is
    older than the account's retention for finished files (Hooks.keep_hours). Never raises."""
    try:
        now = now or _now()
        limit = now - KEEP_DAYS * 86400
        keep = {}
        for d in (ROOT.iterdir() if ROOT.is_dir() else []):
            job = load(d.name) if d.is_dir() else None
            if not job or job.get("busy") or d.name in _RUNNING:
                continue
            if float(job.get("updated") or 0) < limit:
                shutil.rmtree(d, ignore_errors=True)
                continue
            files = [p for p in [d / "final.mp3"] + list((d / "lines").glob("*.mp3")) if p.is_file()]
            if not files:
                continue
            uid = job.get("uid")
            if uid not in keep:
                try:
                    keep[uid] = float(Hooks.keep_hours(uid))
                except Exception:
                    keep[uid] = None            # could not ask: nothing is removed this time
            if keep[uid] is not None and max(p.stat().st_mtime for p in files) < now - keep[uid] * 3600:
                with _lock_for(job["id"]):
                    _drop_audio(job)
                    job["notes"] = [n for n in job.get("notes") or [] if n != "audio_expired"] + ["audio_expired"]
                    _save_keep_updated(job)          # not a change by the person: the text is kept as long as before
    except Exception as ex:
        print(f"[text-voice] cleanup skipped: {ex}")


def _save_keep_updated(job):
    """_save without touching "updated"."""
    d = _dir(job["id"])
    tmp = d / ("job.json." + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / "job.json")


def _jobs_of(uid):
    for d in (ROOT.iterdir() if ROOT.is_dir() else []):
        job = load(d.name) if d.is_dir() else None
        if job and str(job.get("uid")) == str(uid):
            yield job


def audio_bytes(uid):
    """Storage used by this account's spoken lines and MP3s (they count toward the account's storage like other files)."""
    total = 0
    try:
        for job in _jobs_of(uid):
            d = _dir(job["id"])
            for p in [d / "final.mp3"] + list((d / "lines").glob("*.mp3")):
                if p.is_file():
                    total += p.stat().st_size
    except Exception as ex:
        print(f"[text-voice] storage count skipped: {ex}")
    return total


def finished_for(uid, keep_hours=None):
    """The finished MP3s of this account, for the Account page: [{job_id, title, created_at, bytes, expires_at}]."""
    out = []
    for job in _jobs_of(uid):
        p = audio_path(job)
        if not p:
            continue
        mtime = p.stat().st_mtime
        d = _dir(job["id"])
        size = sum(x.stat().st_size for x in [p] + list((d / "lines").glob("*.mp3")) if x.is_file())
        hours = float(keep_hours if keep_hours is not None else KEEP_DAYS * 24)
        out.append({"job_id": job["id"], "kind": "text", "title": job.get("title") or "", "bytes": size,
                    "outdated": audio_path(job, current=True) is None,
                    "seconds": (job.get("audio") or {}).get("seconds"),
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(mtime)),
                    "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(min(mtime + hours * 3600,
                                                                                         float(job.get("updated") or mtime) + KEEP_DAYS * 86400)))})
    return out


def busy_for(uid):
    """True while a project of this account is being worked on (an account is never deleted under a running step)."""
    return any(job.get("busy") or job["id"] in _RUNNING for job in _jobs_of(uid))


def delete_all_for(uid):
    """Every project of this account ("Delete all my files", deleting the account). (removed, busy and kept)."""
    removed = skipped = 0
    for job in list(_jobs_of(uid)):
        if job.get("busy") or job["id"] in _RUNNING:
            skipped += 1
            continue
        shutil.rmtree(_dir(job["id"]), ignore_errors=True)
        removed += 1
    return removed, skipped


def set_speakers(job, updates):
    """Gender and voice of each speaker. A changed gender is used from the next translation on."""
    if job.get("busy"):
        return False, ("Please wait until the current step has finished.", 409)
    by_id = {s["id"]: s for s in job["speakers"]}
    for u in updates if isinstance(updates, list) else []:
        sp = by_id.get(str((u or {}).get("id") or ""))
        if not sp:
            continue
        if "gender" in u:
            g = str(u.get("gender") or "").strip().lower()
            sp["gender"] = g if g in ("male", "female") else ""
        if "voice_id" in u:
            v = str(u.get("voice_id") or "").strip()
            if v and not re.fullmatch(r"[A-Za-z0-9_\-.:]{1,200}", v):
                return False, ("This voice cannot be used.", 400)
            sp["voice_id"] = v
            sp["voice_name"] = str(u.get("voice_name") or "").strip()[:80]
    _save(job)
    return True, None


def tashkeel_line(job, line_id):
    """The Tashkeel button of one line: the words of its Arabic that have no marks get them (words that have marks are kept,
    letters never change; the same word-by-word merge the long dub uses). Free. (ok, (message, code) or None, project)."""
    import gemini_service
    import longdub_service as ld
    if job.get("busy"):
        return False, ("Please wait until the current step has finished.", 409), job
    ln = next((x for x in job["lines"] if x["id"] == line_id), None)
    if not ln:
        return False, ("Line not found.", 404), job
    text = (ln.get("arabic") or "").strip()
    if not text:
        return False, ("There is no Arabic text on this line yet.", 400), job
    if not any(ld._word_needs_tashkeel(w) for w in text.split()):
        return True, None, job            # every word already has its marks
    got = None
    for attempt in range(2):
        try:
            got = gemini_service.add_tashkeel_lines(job["id"], [{"segment_id": line_id, "arabic_text": text}], GEMINI_API_KEY)
        except Exception as ex:
            print(f"[text-voice] tashkeel button failed (attempt {attempt + 1}): {ex}")
            got = None
        if got is not None:
            break
        time.sleep(1.5)
    if got is None:
        return False, ("We couldn't add the diacritics just now. Please try again in a moment.", 503), job
    new = ld.merge_tashkeel(text, got.get(line_id, ""))
    if new == text:
        return False, ("The diacritics could not be added to this line. Please try again.", 503), job
    with _lock_for(job["id"]):
        fresh = load(job["id"]) or job
        row = next((x for x in fresh["lines"] if x["id"] == line_id), None)
        if row is None or (row.get("arabic") or "").strip() != text:
            return False, ("The line was changed while the diacritics were being added. Please try again.", 409), fresh
        row["arabic"] = new
        _save(fresh)
    return True, None, fresh


def set_lines(job, edits):
    """The person's corrections: Arabic text and emotion per line. Free."""
    if job.get("busy"):
        return False, ("Please wait until the current step has finished.", 409)
    from config import CANONICAL_EMOTIONS
    allowed = set(CANONICAL_EMOTIONS) | {"laugh", "sigh", "clear throat", "yawn", "very quiet", "high pitch"}
    by_id = {ln["id"]: ln for ln in job["lines"]}
    for e in edits if isinstance(edits, list) else []:
        ln = by_id.get(str((e or {}).get("id") or ""))
        if not ln:
            continue
        if "arabic" in e:
            a = re.sub(r"[ \t]+", " ", str(e.get("arabic") or "").replace("\n", " ")).strip()
            if len(a) > MAX_ARABIC_CHARS:
                return False, (f"A line can have at most {MAX_ARABIC_CHARS} characters.", 400)
            if a != (ln.get("arabic") or ""):
                ln["arabic"] = a
                ln["gender_check"] = False
        if "emotion" in e:
            emo = str(e.get("emotion") or "").strip().lower()
            ln["emotion"] = emo if emo in allowed else ""
        if "as_written" in e:                    # "Read it exactly as I wrote it" (Ali, 7 Nov 2026)
            ln["as_written"] = e.get("as_written") is True
        if e.get("again") is True and ln.get("audio"):     # a new take of this line only; it is made and paid at the next "Make the audio"
            ln["take"] = int(ln.get("take") or 0) + 1
        if "speed" in e:                        # "" or None: the speed of the whole text
            ln["speed"] = _clean_speed(e.get("speed")) if e.get("speed") not in (None, "", 0) else None
    _save(job)
    return True, None


# ------------------------------------------------------------------ money

def _charge(job, kind, amount):
    """Charge once per step. The ID is saved first (when it cannot be saved nothing is charged). An unconfirmed charge keeps
    its ID, so pressing the button again with the same price asks the ledger the same question (it never charges twice)."""
    ops = job.setdefault("ops", {})
    entry = ops.get(kind)
    if not (isinstance(entry, dict) and entry.get("id") and entry.get("amount") == amount and not entry.get("closed")):
        if isinstance(entry, dict) and entry.get("id"):
            job.setdefault("ops_done", []).append(entry)
        ops[kind] = {"id": str(uuid.uuid4()), "amount": int(amount), "refunded": 0, "closed": False, "at": _now()}
        _save(job)
    return Hooks.charge(job["uid"], int(amount), "text_voice_" + kind, job["id"], operation_id=ops[kind]["id"])


def _give_back(job, kind, amount, key):
    """Return part of the payment of this step. True when the ledger confirmed it (or nothing was due)."""
    entry = (job.get("ops") or {}).get(kind) or {}
    amount = min(int(amount), int(entry.get("amount", 0)) - int(entry.get("refunded", 0)))
    if amount <= 0 or not entry.get("id"):
        return True
    try:
        ok = Hooks.refund(job["uid"], amount, job["id"], debit_id=entry["id"], key=key)
    except Exception as ex:
        print(f"[text-voice] refund not confirmed for {job['id']}: {ex}")
        ok = None
    if ok:
        entry["refunded"] = int(entry.get("refunded", 0)) + amount
        _ev(job, "refund", "ok", f"{amount} credits back ({key})")
    else:
        job.setdefault("notes", []).append("refund_pending")
        _ev(job, "refund", "pending", f"{amount} credits ({key}): retried automatically")
    return bool(ok)


def _close(job, kind):
    entry = (job.get("ops") or {}).get(kind)
    if isinstance(entry, dict):
        entry["closed"] = True


# ------------------------------------------------------------------ step 1: the Arabic

def start_prepare(job):
    """Charge the translation (or tashkeel) and run it in the background. (True, None) or (False, (message, code))."""
    with _lock_for(job["id"]):
        job = load(job["id"]) or job
        if job.get("busy") or job["id"] in _RUNNING:
            return False, ("This step is already running.", 409)
        if job["source_language"] != "ar":
            missing = [s["name"] for s in job["speakers"] if s.get("gender") not in ("male", "female")]
            if missing:
                return False, ("Please choose male or female for: " + ", ".join(missing) + ". Arabic needs it for the right forms.", 400)
        price = prepare_price(job)
        res = _charge(job, "prepare", price)
        if res is False:
            _close(job, "prepare")          # the ledger refused it: nothing was charged, the next try gets a new ID
            _save(job)
            return False, (f"Not enough credits. This step costs {price} credits. Use Buy to top up.", 402)
        if not _debit_ok(res):
            return False, ("Your payment could not be confirmed. Please try again.", 503)
        job["busy"], job["percent"], job["error"] = "prepare", 0, ""
        job["message"] = "Translating..." if job["source_language"] != "ar" else "Adding the diacritics..."
        job["ops"]["prepare"]["confirmed"] = True
        _save(job)
        _ev(job, "prepare_charged", "ok", f"{price} credits for {_text_chars(job)} characters")
        _RUNNING.add(job["id"])
    threading.Thread(target=_run_prepare, args=(job["id"],), daemon=True, name="text-voice-prepare").start()
    return True, None


def _gender_for(job, lines, rows_all):
    """What the translator is told about genders and who each line is said to (the same builders the long dub uses)."""
    import gender_context
    import language_prompts
    names = {s["id"]: s["name"] for s in job["speakers"]}
    genders = {s["name"]: {"gender": s.get("gender") or "", "source": "chosen", "uncertain": False} for s in job["speakers"]}
    ids = [ln["id"] for ln in lines]
    if job["source_language"] != "en":
        return language_prompts.gender_context(rows_all, ids, genders, job["source_language"])
    asked = None
    saved = job.get("addressees") or {}
    if saved:
        import addressee_ask
        asked = {}
        for r in rows_all:
            if r["segment_id"] in ids:
                got = addressee_ask.resolve(saved.get(r["segment_id"]), r["text"], names, genders)
                if got and got.get("said_to") != r["speaker"]:
                    asked[r["segment_id"]] = got
    return {"speakers": gender_context.speaker_table(genders), "lines": gender_context.build_context(rows_all, ids, genders, asked=asked)}


ASK_DEADLINE = 45        # seconds the translation waits for "who is each you said to"; then it goes on with the usual hint


def _ask_addressees(job, rows_all):
    """English script with two or more speakers: who each "you" or command is said to, read from the whole script (addressee_ask).
    6 Nov 2026: one answer took about 2.5 minutes while the page said "Translating…". The translation now waits at most
    ASK_DEADLINE seconds; without an answer by then the lines keep the usual hint (and a "check" mark where unsure)."""
    try:
        import addressee_ask
        import gender_context
        if job["source_language"] != "en" or len(job["speakers"]) < 2 or not addressee_ask.ENABLED or not GEMINI_API_KEY:
            return
        todo = [r["segment_id"] for r in rows_all if addressee_ask.needs_question(r["text"]) and not gender_context.address_cue(r["text"])]
        if not todo:
            return
        import gemini_service
        from app_state import record_gemini
        box = {}

        def call(key, payload, timeout):
            data, err = gemini_service.call_gemini(key, payload, timeout=min(timeout, ASK_DEADLINE))
            box["model"] = str((data or {}).get("modelVersion") or "")[:40]
            if err:
                box["error"] = str(err)[:200]
            return data, err

        def run():
            box["got"] = addressee_ask.ask(rows_all, todo, GEMINI_API_KEY, call, record=lambda data: record_gemini(job["id"], data))

        started = time.time()
        worker = threading.Thread(target=run, daemon=True, name="text-voice-addressees")
        worker.start()
        worker.join(ASK_DEADLINE)
        secs = time.time() - started
        if "got" not in box:
            _ev(job, "addressees_asked", "skipped", f"no answer within {ASK_DEADLINE} s; the usual hint is used")
            return
        job["addressees"] = box["got"]
        _ev(job, "addressees_asked", "ok", f"{addressee_ask.summary(box['got'])} ({secs:.0f} s"
            + (f", {box['model']}" if box.get("model") else "") + (f", error: {box['error']}" if box.get("error") else "") + ")")
    except Exception as ex:
        print(f"[text-voice] addressee question skipped: {ex}")


def _batches(lines):
    cur, size = [], 0
    for ln in lines:
        if cur and (size + len(ln["text"]) > BATCH_CHARS or len(cur) >= BATCH_LINES):
            yield cur
            cur, size = [], 0
        cur.append(ln)
        size += len(ln["text"])
    if cur:
        yield cur


def _translate(job, progress):
    """{line id: (arabic, emotion, gender_check)} for every line, or None when a part could not be translated."""
    import gemini_service
    import gender_context
    import longdub_service as ld
    names = {s["id"]: s["name"] for s in job["speakers"]}
    rows_all = [{"segment_id": ln["id"], "start": float(i), "speaker": names[ln["speaker_id"]], "speaker_id": ln["speaker_id"],
                 "text": ln["text"]} for i, ln in enumerate(job["lines"])]
    _ask_addressees(job, rows_all)
    out, done, total = {}, 0, len(job["lines"])
    for batch in _batches(job["lines"]):
        gender = _gender_for(job, batch, rows_all)
        first = job["lines"].index(batch[0])
        previous = [{"speaker": names[ln["speaker_id"]], "text": ln["text"]} for ln in job["lines"][max(0, first - 4):first]]
        items = [{"segment_id": ln["id"], "speaker": names[ln["speaker_id"]], "text": ln["text"]} for ln in batch]
        got, started = {}, time.time()
        for attempt in range(3):
            res = gemini_service.translate_script_lines(job["id"], [it for it in items if it["segment_id"] not in got], GEMINI_API_KEY,
                                                        source_language=job["source_language"], gender=gender, previous=previous)
            for it in (res or {}).get("translated_segments") or []:
                sid, ar = str(it.get("segment_id")), str(it.get("arabic_text") or "").strip()
                if sid in {x["segment_id"] for x in items} and ar:
                    got[sid] = (ar, it.get("emotion") or "neutral", gender_context.read_flags(it))
            if len(got) == len(items):
                break
            time.sleep(2 * (attempt + 1))
        if len(got) < len(items):
            return None
        plain = {sid: (v[0], v[1]) for sid, v in got.items()}
        translated_at = time.time()
        ld._check_tashkeel(job["id"], plain, gender)          # the vowel proofread (only safe corrections are taken)
        print(f"[text-voice] {job['id']} {len(batch)} lines: translated in {translated_at - started:.0f} s, "
              f"vowels checked in {time.time() - translated_at:.0f} s")
        for sid, (ar, emo) in plain.items():
            out[sid] = (ar, emo, bool(got[sid][2][1]))
        done += len(batch)
        progress(int(90 * done / total))
    fixes = ld._TASHKEEL_LOG.pop(job["id"], [])
    if fixes:
        _ev(job, "tashkeel_checked", "ok", f"{len(fixes)} word(s) corrected")
    return out


def _vowel(job, progress):
    """Arabic text: its own words, with the missing tashkeel added word by word, then the same proofread. {id: (arabic, "", False)}."""
    import gemini_service
    import longdub_service as ld
    out, done, total = {}, 0, len(job["lines"])
    for batch in _batches(job["lines"]):
        todo = [ln for ln in batch if ld.needs_tashkeel(ln["text"]) or any(ld._word_needs_tashkeel(w) for w in ln["text"].split())]
        got = None
        if todo:
            for attempt in range(3):
                got = gemini_service.add_tashkeel_lines(job["id"], [{"segment_id": ln["id"], "arabic_text": ln["text"]} for ln in todo],
                                                         GEMINI_API_KEY)
                if got is not None:
                    break
                time.sleep(2 * (attempt + 1))
            if got is None:
                return None
        plain = {}
        for ln in batch:
            plain[ln["id"]] = (ld.merge_tashkeel(ln["text"], (got or {}).get(ln["id"], "")) if ln in todo else ln["text"], "")
        ld._check_tashkeel(job["id"], plain, None)
        for sid, (ar, _e) in plain.items():
            out[sid] = (ar, "", False)
        done += len(batch)
        progress(int(90 * done / total))
    ld._TASHKEEL_LOG.pop(job["id"], None)
    return out


def _run_prepare(job_id):
    job = load(job_id)
    try:
        with WORKERS:
            def progress(p):
                job["percent"] = p
                _save(job)
            got = (_vowel if job["source_language"] == "ar" else _translate)(job, progress)
        with _lock_for(job_id):
            if got is None:
                _give_back(job, "prepare", job["ops"]["prepare"]["amount"], "prepare_failed")
                job["error"] = "We couldn't finish the Arabic text. Nothing was charged for this step. Please try again."
                _ev(job, "prepared", "failed", "the AI did not answer for every line")
            else:
                for ln in job["lines"]:
                    ar, emo, check = got[ln["id"]]
                    ln["arabic"], ln["emotion"], ln["gender_check"] = ar, emo, check
                job["prepared"] = True
                job["error"] = ""
                _ev(job, "prepared", "ok", f"{len(got)} lines, {sum(1 for v in got.values() if v[2])} to check for gender")
            _close(job, "prepare")
            job["busy"], job["percent"], job["message"] = "", 100, ""
            _save(job)
    except Exception as ex:
        print(f"[text-voice] {job_id} prepare failed: {type(ex).__name__}: {ex}")
        try:
            _give_back(job, "prepare", job["ops"]["prepare"]["amount"], "prepare_error")
            job["error"] = "Something went wrong. Nothing was charged for this step. Please try again."
            _close(job, "prepare")
            job["busy"], job["message"] = "", ""
            _save(job)
        except Exception:
            pass
    finally:
        _RUNNING.discard(job_id)


# ------------------------------------------------------------------ step 2: the voice

def start_speak(job):
    with _lock_for(job["id"]):
        job = load(job["id"]) or job
        if job.get("busy") or job["id"] in _RUNNING:
            return False, ("This step is already running.", 409)
        if not job.get("prepared"):
            return False, ("Please make the Arabic text first.", 400)
        used = {ln["speaker_id"] for ln in job["lines"] if _speakable(ln.get("arabic"))}
        if not used:
            return False, ("There is no Arabic text to speak.", 400)
        for sp in job["speakers"]:
            if sp["id"] not in used:
                continue
            if not sp.get("voice_id"):
                return False, (f"Please choose a voice for {sp['name']}.", 400)
            st = Hooks.voice_status(sp["voice_id"])
            if st == "preparing":
                return False, ("Your own voice is still getting its Arabic accent (about two minutes). Please try again shortly.", 409)
        todo = _to_speak(job)
        price, chars = speak_price(job, todo)
        if price:
            res = _charge(job, "speak", price)
            if res is False:
                _close(job, "speak")
                _save(job)
                return False, (f"Not enough credits. This costs {price} credits. Use Buy to top up.", 402)
            if not _debit_ok(res):
                return False, ("Your payment could not be confirmed. Please try again.", 503)
            job["ops"]["speak"]["confirmed"] = True
            job["ops"]["speak"]["chars"] = chars
            _ev(job, "speak_charged", "ok", f"{price} credits for {len(todo)} lines, {chars} characters")
        job["busy"], job["percent"], job["error"], job["message"] = "speak", 0, "", "Speaking..."
        _save(job)
        _RUNNING.add(job["id"])
    threading.Thread(target=_run_speak, args=(job["id"], todo, price), daemon=True, name="text-voice-speak").start()
    return True, None


def _speak_line(job, i):
    import longdub_service as ld
    ln = job["lines"][i]
    h = _line_hash(job, i)
    audio, err = ld._tts_with_retry(_voice_of(job, ln), _tag(ln.get("emotion")) + _spoken_arabic(job, ln), _waqf_of(ln), _gap(job, i))
    if not audio:
        print(f"[text-voice] {job['id']} line {ln['id']} not spoken: {str(err)[:300]}")
        _FAILS.setdefault(job["id"], []).append(str(err or "no answer")[:300])
        return i, None
    folder = _dir(job["id"]) / "lines"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{ln['id']}_{h}.mp3").write_bytes(audio)
    return i, h


_FAILS = {}         # project id -> why lines were not spoken in the running step (kept on the project for the admin's Activity view)


def _run_speak(job_id, todo, price):
    from concurrent.futures import ThreadPoolExecutor
    job = load(job_id)
    failed = []
    _FAILS.pop(job_id, None)
    try:
        with WORKERS:
            done = 0
            with ThreadPoolExecutor(max_workers=3) as pool:
                for i, h in pool.map(lambda k: _speak_line(job, k), todo):
                    done += 1
                    if h:
                        old = _line_file(job, job["lines"][i]) if job["lines"][i].get("audio") else None
                        job["lines"][i]["audio"] = h
                        if old and old.is_file() and old.name != f"{job['lines'][i]['id']}_{h}.mp3":
                            old.unlink(missing_ok=True)
                    else:
                        failed.append(i)
                    job["percent"] = int(85 * done / max(1, len(todo)))
                    _save(job)
            if not failed:
                job["message"] = "Putting it together..."
                _save(job)
                seconds = _assemble(job)
                job["audio"] = {"hash": _final_hash(job), "seconds": round(seconds, 1), "at": _now(), "timing": job.pop("audio_timing", [])}
                job["notes"] = [n for n in job.get("notes") or [] if n != "audio_expired"]
                job["audio_count"] = int(job.get("audio_count") or 0) + 1
        with _lock_for(job_id):
            if failed and price:
                # the lines that were not spoken are given back (their share of the characters)
                cpc = max(1, int(Hooks.chars_per_credit() or 60))
                lost = sum(_spoken_len(job, i) for i in failed)
                _give_back(job, "speak", min(price, int(math.ceil(lost / float(cpc)))), "speak_failed")
            if failed:
                job["error"] = (f"{len(failed)} line(s) could not be spoken. They were not charged. Press the button again to try them once more.")
                why = _FAILS.pop(job_id, [])
                job["last_failure"] = {"at": _now(), "lines": len(failed), "of": len(todo), "detail": (why[0] if why else "")[:300]}
                _ev(job, "spoken", "partial", f"{len(todo) - len(failed)} of {len(todo)} lines")
            else:
                job["error"] = ""
                _ev(job, "spoken", "ok", f"{len(todo)} lines spoken, {job['audio'].get('seconds')} s")
            _close(job, "speak")
            job["busy"], job["percent"], job["message"] = "", 100, ""
            _save(job)
    except Exception as ex:
        print(f"[text-voice] {job_id} speak failed: {type(ex).__name__}: {ex}")
        try:
            job["last_failure"] = {"at": _now(), "lines": len(failed), "of": len(todo), "detail": f"{type(ex).__name__}: {ex}"[:300]}
            _FAILS.pop(job_id, None)
            if price:
                spoken = [i for i in todo if i not in failed and job["lines"][i].get("audio") == _line_hash(job, i)]
                cpc = max(1, int(Hooks.chars_per_credit() or 60))
                kept = sum(_spoken_len(job, i) for i in spoken)
                _give_back(job, "speak", price - int(math.ceil(kept / float(cpc))), "speak_error")
            job["error"] = "Something went wrong while generating the audio. What could not be generated was not charged. Please try again."
            _close(job, "speak")
            job["busy"], job["message"] = "", ""
            _save(job)
        except Exception:
            pass
    finally:
        _RUNNING.discard(job_id)


def _active_rms(x):
    import numpy as np
    mono = x.mean(axis=1) if x.ndim == 2 else x
    if not len(mono):
        return 0.0
    frame = 2205
    n = len(mono) // frame
    if n < 1:
        return float(np.sqrt(np.mean(np.square(mono))))
    e = np.sqrt(np.mean(np.square(mono[:n * frame].reshape(n, frame)), axis=1))
    loud = e[e > max(1e-4, float(np.max(e)) * 0.1)]
    return float(np.sqrt(np.mean(np.square(loud)))) if len(loud) else 0.0


def _assemble(job):
    """Every spoken line in order, without the voice's own silence, at one level, with the pauses; saved as final.mp3. Returns seconds."""
    import numpy as np
    import soundfile as sf
    import ffmpeg_utils
    import longdub_service as ld
    work = _dir(job["id"]) / "work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        clips = []
        for i, ln in enumerate(job["lines"]):
            if not _speakable(ln.get("arabic")):
                continue
            wav = work / f"{ln['id']}.wav"
            if ld._trim_silence(_line_file(job, ln), wav) is None:
                continue
            speed = _speed_of(job, ln)
            if speed != 1.0:                    # faster or slower, the voice keeps its pitch
                fast = work / f"{ln['id']}_speed.wav"
                ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-i", str(wav), "-filter:a", f"atempo={speed:.3f}", str(fast)])
                wav = fast
            x, sr = sf.read(str(wav), dtype="float32", always_2d=True)
            clips.append((ln, x, sr))
        if not clips:
            raise RuntimeError("no audio")
        sr = clips[0][2]
        levels = [r for r in (_active_rms(x) for _ln, x, _sr in clips) if r > 0]
        target = float(np.median(levels)) if levels else 0.0
        parts, timing, at = [], [], 0
        for k, (ln, x, _sr) in enumerate(clips):
            if k:
                gap = np.zeros((int(PAUSES[ln["pause"]] * sr), x.shape[1]), dtype="float32")
                parts.append(gap)
                at += len(gap)
            r = _active_rms(x)
            if target and r:
                x = x * float(np.clip(target / r, 10 ** (-6 / 20), 10 ** (6 / 20)))
            parts.append(x)
            timing.append({"id": ln["id"], "start": round(at / float(sr), 3), "end": round((at + len(x)) / float(sr), 3)})
            at += len(x)
        job["audio_timing"] = timing          # where each line is in the MP3 (for the subtitles and the lines ZIP)
        mix = np.concatenate(parts)
        peak = float(np.max(np.abs(mix))) if len(mix) else 0.0
        if peak > 0.98:
            mix *= 0.98 / peak
        joined = work / "joined.wav"
        sf.write(str(joined), mix, sr, subtype="PCM_16")
        final = _dir(job["id"]) / "final.mp3"
        tmp = _dir(job["id"]) / "final.tmp.mp3"
        ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-i", str(joined), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100",
                                 "-codec:a", "libmp3lame", "-b:a", "192k", str(tmp)])
        os.replace(tmp, final)
        return len(mix) / float(sr)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def audio_path(job, current=False):
    """The last finished MP3. It stays after a correction (to listen to and download) until the new one is made; with
    current=True only while it matches the text and voices as they are now."""
    p = _dir(job["id"]) / "final.mp3"
    if not p.is_file() or not (job.get("audio") or {}).get("hash"):
        return None
    if current and ((job.get("audio") or {}).get("hash") != _final_hash(job) or _to_speak(job)):
        return None
    return p


def line_audio_path(job, line_id):
    for i, ln in enumerate(job["lines"]):
        if ln["id"] == line_id and ln.get("audio") and ln["audio"] == _line_hash(job, i):
            p = _line_file(job, ln)
            return p if p.is_file() else None
    return None


# ------------------------------------------------------------------ subtitles and the lines ZIP (Ali, Nov 2026)

SUB_ROW = 42                    # characters on one subtitle row; two rows at most on screen


def _timing(job):
    """Where each line is in the current MP3 ([{id, start, end}]), or None (no MP3 that matches the text, or one made
    before the timing was kept: making the audio again is free then)."""
    if not audio_path(job, current=True):
        return None
    return (job.get("audio") or {}).get("timing") or None


def _srt_time(t):
    ms = int(round(max(0.0, t) * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def _sub_pieces(text):
    """A line cut into subtitles of at most two rows, at word ends."""
    words, out, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > 2 * SUB_ROW:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    rows = []
    for piece in out:                       # two rows, split near the middle
        if len(piece) > SUB_ROW:
            ws, top = piece.split(), ""
            while ws and len(top) + len(ws[0]) < len(piece) / 2:
                top = (top + " " + ws.pop(0)).strip()
            piece = top + "\n" + " ".join(ws) if top and ws else piece
        rows.append(piece)
    return rows


def subtitles(job, marks=False):
    """The subtitles of the current MP3 as SRT text (the Arabic as written, without diacritics unless marks=True), or None."""
    timing = _timing(job)
    if not timing:
        return None
    by_id = {ln["id"]: ln for ln in job["lines"]}
    out, n = [], 0
    for t in timing:
        ln = by_id.get(t["id"])
        if not ln:
            continue
        text = (ln.get("arabic") or "").strip()
        text = re.sub(r"\s+", " ", text if marks else _bare(text)).strip()
        pieces = _sub_pieces(text) or [""]
        total = sum(len(p) for p in pieces) or 1
        start, length = float(t["start"]), float(t["end"]) - float(t["start"])
        for piece in pieces:                # each piece gets the share of the line's time of its length
            end = start + length * len(piece) / total
            n += 1
            out.append(f"{n}\n{_srt_time(start)} --> {_srt_time(end)}\n{piece}\n")
            start = end
    return "\n".join(out)


def _safe_name(text, fallback):
    name = re.sub(r"[^\w\- ]+", "", str(text or ""), flags=re.UNICODE).strip()[:40]
    return name or fallback


def lines_zip(job, dest):
    """A ZIP of the current MP3 cut at the start of every line (each part: the line and the pause after it), written to
    dest. Returns dest, or None when there is no current MP3 with timing. Cut without re-encoding, so it is quick."""
    import zipfile
    import ffmpeg_utils
    timing = _timing(job)
    final = audio_path(job, current=True)
    if not timing or not final:
        return None
    names = {s["id"]: ("Narrator" if s["name"] == NARRATOR else s["name"]) for s in job["speakers"]}
    by_id = {ln["id"]: ln for ln in job["lines"]}
    work = _dir(job["id"]) / "zip_work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        cuts = ",".join("%.3f" % float(t["start"]) for t in timing[1:])
        cmd = ["ffmpeg", "-y", "-i", str(final), "-map", "0:a", "-c", "copy", "-f", "segment", "-reset_timestamps", "1"]
        if cuts:
            cmd += ["-segment_times", cuts]
        ffmpeg_utils.run_ffmpeg(cmd + [str(work / "part%04d.mp3")])
        parts = sorted(work.glob("part*.mp3"))
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as z:
            for k, (t, part) in enumerate(zip(timing, parts)):
                ln = by_id.get(t["id"]) or {}
                z.write(part, "%03d_%s.mp3" % (k + 1, _safe_name(names.get(ln.get("speaker_id")), "line")))
        return dest
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ a text from a file (Ali, Nov 2026)

def _decode(data):
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    for enc in ("utf-8", "cp1256"):         # cp1256: Arabic texts saved by older Windows programs
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def _docx_text(data):
    import io
    import zipfile
    from xml.etree import ElementTree as ET
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        info = z.getinfo("word/document.xml")
        if info.file_size > 30_000_000:
            raise ValueError("too big")
        root = ET.fromstring(z.read(info))
    paras = []
    for para in root.iter(w + "p"):
        bits = []
        for node in para.iter():
            if node.tag == w + "t" and node.text:
                bits.append(node.text)
            elif node.tag == w + "tab":
                bits.append(" ")
            elif node.tag in (w + "br", w + "cr"):
                bits.append("\n")
        paras.append("".join(bits))
    return "\n".join(paras)


_SUB_TIME = re.compile(r"^\s*(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}\s*-->")


def _subtitle_text(text):
    """The words of an .srt or .vtt file: without numbers, times, styling and the WEBVTT header; one line per subtitle."""
    out, cur = [], []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        s = line.strip()
        if not s:
            if cur:
                out.append(" ".join(cur))
                cur = []
            continue
        if s.isdigit() or _SUB_TIME.match(s) or s.upper().startswith(("WEBVTT", "NOTE", "STYLE")):
            continue
        cur.append(re.sub(r"<[^>]+>|\{\\[^}]*\}", "", s).strip())
    if cur:
        out.append(" ".join(cur))
    return "\n".join(x for x in out if x)


def read_text_file(name, data):
    """(text, None) or (None, message): the text of an uploaded .txt, .docx, .srt or .vtt file. Free; nothing is kept."""
    ext = os.path.splitext(str(name or "").lower())[1]
    if not data:
        return None, "The file is empty."
    if len(data) > READ_FILE_MAX:
        return None, "The file is too big. Please use a file under 2 MB."
    try:
        if ext == ".docx":
            text = _docx_text(data)
        elif ext in (".srt", ".vtt"):
            text = _subtitle_text(_decode(data))
        elif ext in (".txt", ".text", ".md", ""):
            text = _decode(data)
        else:
            return None, "Please choose a .txt, .docx or .srt file."
    except Exception:
        return None, "This file could not be read. Please save it as .txt or .docx and try again."
    text = re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")).strip().lstrip("\ufeff").strip()
    if not text:
        return None, "No text was found in this file."
    return text, None


# ------------------------------------------------------------------ after a restart

def recover():
    """A project that was busy when the server stopped: what was paid for and not delivered is given back. Run once at start."""
    if not ROOT.is_dir():
        return 0
    n = 0
    for d in ROOT.iterdir():
        job = load(d.name) if d.is_dir() else None
        if not job or not job.get("busy") or job["id"] in _RUNNING:
            continue
        try:
            kind = job["busy"]
            entry = (job.get("ops") or {}).get(kind) or {}
            if kind == "prepare":
                _give_back(job, "prepare", entry.get("amount", 0), "prepare_interrupted")
            elif kind == "speak" and entry.get("amount"):
                cpc = max(1, int(Hooks.chars_per_credit() or 60))
                spoken = sum(_spoken_len(job, i) for i, ln in enumerate(job["lines"])
                             if ln.get("audio") == _line_hash(job, i) and _line_file(job, ln).is_file()
                             and _line_file(job, ln).stat().st_mtime + 1 >= float(entry.get("at") or 0))
                _give_back(job, "speak", int(entry["amount"]) - min(int(entry["amount"]), int(math.ceil(spoken / float(cpc)))), "speak_interrupted")
            _close(job, kind)
            job["busy"], job["message"] = "", ""
            job["error"] = "The server restarted during this step. What was not finished was not charged. Please press the button again."
            _save(job)
            n += 1
        except Exception as ex:
            print(f"[text-voice] recovery of {job.get('id')} skipped: {ex}")
    return n


# ------------------------------------------------------------------ own voices: the Arabic accent

VOICES = ROOT / "voices"


def _voice_file(voice_id):
    safe = re.sub(r"[^A-Za-z0-9_\-.]", "_", str(voice_id))[:200]
    return VOICES / (safe + ".json")


def voice_status(voice_id):
    """"ready" for every voice except an own voice still getting its Arabic accent ("preparing")."""
    try:
        data = json.loads(_voice_file(voice_id).read_text(encoding="utf-8"))
        if data.get("status") == "preparing" and _now() - float(data.get("at") or 0) > 900:
            return "ready"            # stuck for 15 minutes (a restart): the voice works without the accent
        return data.get("status") or "ready"
    except (OSError, ValueError):
        return "ready"


def _set_voice(voice_id, status, detail=""):
    VOICES.mkdir(parents=True, exist_ok=True)
    tmp = _voice_file(voice_id).with_suffix(".tmp")
    tmp.write_text(json.dumps({"status": status, "detail": str(detail)[:300], "at": _now()}), encoding="utf-8")
    os.replace(tmp, _voice_file(voice_id))


def localize_later(voice_id, job_id=""):
    """A voice cloned from a sample in another language gets the Arabic accent in the background (Inworld localization,
    about two minutes); until then voice_status says "preparing". When it fails the voice still works, with the sample's accent."""
    import inworld_service
    _set_voice(voice_id, "preparing")

    def run():
        try:
            res = inworld_service.localize_voice(voice_id, INWORLD_API_KEY, chooser=inworld_service.gemini_chooser(job_id or "text_voice"))
            _set_voice(voice_id, "ready" if res.get("ok") else "no_accent", "" if res.get("ok") else res.get("error"))
            print(f"[text-voice] voice {voice_id} accent: {'ok' if res.get('ok') else res.get('error')}"[:300])
        except Exception as ex:
            _set_voice(voice_id, "no_accent", ex)
    threading.Thread(target=run, daemon=True, name="text-voice-accent").start()
