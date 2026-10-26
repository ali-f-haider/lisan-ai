"""Who speaks and who is spoken to, for a translation into Arabic (which marks gender and number; English does not).

* detect_genders(): the gender of each speaker. The person's own choice always wins; then what a listening check hears in the
  speaker's own voice; then the pitch. Each answer says where it came from and whether it is uncertain.
* build_context(): for the lines being translated, the speaker's gender, the neighbouring lines (who they say it to is
  usually the speaker of the line before or after) and the other speakers with their genders. The translator reads this.
* Nothing here changes a line. It never raises; a missing piece just leaves the translator with less to go on.
"""
import languages, language_prompts
import json
import re
import subprocess
import uuid
from pathlib import Path

GENDERS = ("male", "female")
CONTEXT_LINES = 3           # neighbouring lines shown before and after each line
MAX_CONTEXT_CHARS = 220     # of each neighbouring line


# Words that say WHO is spoken to, whatever the voice sounds like. Only words that are (almost) always said TO a person.
_FEMALE_CUE = re.compile(r"\b(?:ma'?am|madam|madame|your ladyship)\b", re.I)
_MALE_CUE = re.compile(r"\b(?:sir|your lordship)\b", re.I)
# Titles whose Arabic form follows the person addressed; who that is has to be found in the dialogue.
_HONORIFIC = re.compile(r"\byour\s+(?:honou?r|majesty|excellency|highness|grace|eminence|holiness)\b", re.I)
_ROLE_OF = {"honou?r": r"judge|justice|magistrate", "majesty": r"king|queen|monarch|emperor|empress|sultan",
            "highness": r"prince|princess", "excellency": r"ambassador|governor|president|minister"}


def clean(g):
    g = str(g or "").strip().lower()
    return g if g in GENDERS else ""


def _ffmpeg_cut(src, start, dur, out):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, float(start)):.3f}", "-t", f"{float(dur):.3f}",
                    "-i", str(src), "-vn", "-ar", "16000", "-ac", "1", str(out)], check=True, timeout=60)


def detect_genders(job_id, src, segments, chosen=None, *, work, key=None, cut=None, describe=None, max_listened=24):      # (was 6: with many speakers the rest were judged by pitch alone, which is the weakest evidence)
    """{speaker name: {"gender": "male"|"female"|"", "source": "chosen"|"heard"|"pitch"|"", "uncertain": bool}}.

    segments: [{speaker, start, end}] (times in the file `src`); chosen: {name: gender} only what the person picked.
    describe(job_id, mp3_path) is the listening check (default: gemini_service.describe_speaker_voice with `key`)."""
    chosen = {str(k): clean(v) for k, v in (chosen or {}).items() if clean(v)}
    names = []
    for s in segments or []:
        n = str(s.get("speaker") or "")
        if n and n not in names:
            names.append(n)
    out = {n: {"gender": chosen[n], "source": "chosen", "uncertain": False} for n in names if n in chosen}
    todo = [n for n in names if n not in chosen]
    if not todo:
        return out
    for n in todo:
        out[n] = {"gender": "", "source": "", "uncertain": True}
    if src is None or not Path(str(src)).exists():
        return out
    try:
        import voice_match as vm
        import voice_match_service as svc
        if describe is None and key:
            import gemini_service
            describe = lambda jid, mp3: gemini_service.describe_speaker_voice(jid, mp3, key)
        cut = cut or _ffmpeg_cut
        work = Path(work)
        work.mkdir(parents=True, exist_ok=True)
        todo.sort(key=lambda n: -sum(float(s["end"]) - float(s["start"]) for s in segments if s.get("speaker") == n))
        asked = 0
        for n in todo:
            try:
                pieces = [(float(s["start"]), float(s["end"])) for s in segments if s.get("speaker") == n]
                x, got = svc._speaker_audio(src, pieces, cut, work, svc.PITCH_SECONDS)
                prof = vm.acoustic_profile(x, svc.SR) if got >= 1.0 else {}
                ai = None
                if describe is not None and got >= 2.0 and asked < max_listened:
                    asked += 1
                    wav, mp3 = work / f"{uuid.uuid4().hex}.wav", work / f"{uuid.uuid4().hex}.mp3"
                    try:
                        svc._write_wav(wav, x[: int(svc.LISTEN_SECONDS * svc.SR)])
                        cut(wav, 0, svc.LISTEN_SECONDS, mp3)
                        ai = describe(job_id, mp3)
                    except Exception:
                        ai = None
                    finally:
                        for p in (wav, mp3):
                            try:
                                p.unlink()
                            except Exception:
                                pass
                t = vm.speaker_traits(prof, ai)
                g, source = clean(t.get("gender")), t.get("gender_source") or ""
                pitch_g = clean(vm.guess_gender(prof.get("f0_median"))) if prof else ""
                uncertain = (source != "heard") or bool(ai and ai.get("uncertain"))
                if source == "heard" and pitch_g and pitch_g != g:
                    uncertain = True                       # the listener and the pitch disagree
                out[n] = {"gender": g, "source": source if g else "", "uncertain": uncertain if g else True}
            except Exception as ex:
                print(f"[gender] could not judge {n}: {ex}")
    except Exception as ex:
        print(f"[gender] detection skipped: {ex}")
    return out


def address_cue(text):
    """"male" / "female" when the line says "sir" / "ma'am" (so the person spoken to is known from the words), else ""."""
    t = str(text or "")
    f, m = bool(_FEMALE_CUE.search(t)), bool(_MALE_CUE.search(t))
    return "female" if f and not m else ("male" if m and not f else "")


def _role_speaker(text, me, genders, roles):
    """The one speaker (not `me`) whose role matches the title in the line ("Your Honor" -> the judge), as (name, gender), or None."""
    m = _HONORIFIC.search(str(text or ""))
    if not m or not roles:
        return None
    word = m.group(0).split()[-1].lower()
    for key, pattern in _ROLE_OF.items():
        if re.fullmatch(key, word):
            hits = [n for n, r in roles.items() if n != me and re.search(r"\b(?:%s)\b" % pattern, str(r or ""), re.I)]
            if len(hits) == 1:
                return hits[0], clean(((genders or {}).get(hits[0]) or {}).get("gender"))
    return None


def speaker_table(genders, roles=None):
    """What the translator is told about each speaker: {name: "male"|"female"|"unknown"} plus how sure we are (and the role, when known)."""
    table = {}
    for name, info in (genders or {}).items():
        g = clean((info or {}).get("gender"))
        src = (info or {}).get("source")
        if not g:
            table[name] = "unknown"
        elif src == "chosen":
            table[name] = f"{g} (certain: chosen by the user)"
        elif src == "text":
            table[name] = f"{g} (from the dialogue: a name, a title or he/she words{'; the voice sounded different' if (info or {}).get('uncertain') else ''})"
        else:
            table[name] = f"{g} ({'probably' if (info or {}).get('uncertain') else 'heard'}, from the voice)"
        role = str((roles or {}).get(name) or "").strip()
        if role:
            table[name] += f"; role: {role}"
    return table


def build_context(rows_all, ids, genders, roles=None):
    """{segment_id: {"speaker_gender", "before": [...], "after": [...]}} for the lines `ids`, from ALL lines in time order.
    Each neighbour is {"speaker", "gender", "english"}. rows_all: [{segment_id, start, speaker, text}].
    A line also gets "addressee_is" (a certain gender: the line says sir / ma'am, or it uses a title like "Your Honor" and the
    speaker with that role, e.g. the judge, is known) and "probably_said_to" (the speaker of the line just before, else after)."""
    order = sorted((r for r in rows_all or [] if str(r.get("text") or "").strip() or r.get("segment_id") in ids),
                   key=lambda r: float(r.get("start") or 0))
    pos = {r.get("segment_id"): i for i, r in enumerate(order)}

    def view(r):
        g = clean(((genders or {}).get(r.get("speaker")) or {}).get("gender"))
        return {"speaker": r.get("speaker"), "gender": g or "unknown", "english": str(r.get("text") or "").strip()[:MAX_CONTEXT_CHARS]}

    out = {}
    for sid in ids:
        i = pos.get(sid)
        if i is None:
            continue
        me = order[i]
        out[sid] = {"speaker_gender": clean(((genders or {}).get(me.get("speaker")) or {}).get("gender")) or "unknown",
                    "before": [view(r) for r in order[max(0, i - CONTEXT_LINES):i]],
                    "after": [view(r) for r in order[i + 1:i + 1 + CONTEXT_LINES]]}
        sure = address_cue(me.get("text"))
        by_role = _role_speaker(me.get("text"), me.get("speaker"), genders, roles)
        if by_role and by_role[1] and not sure:
            sure = by_role[1]
        if sure:
            out[sid]["addressee_is"] = sure
        for other in ([order[i - 1]] if i else []) + ([order[i + 1]] if i + 1 < len(order) else []):
            if other.get("speaker") != me.get("speaker"):
                out[sid]["probably_said_to"] = {"speaker": other.get("speaker"), "gender": view(other)["gender"]}
                break
    return out


PEOPLE_PROMPT = """Read this dialogue transcript. Every line is: speaker | text.
For EVERY speaker in the list, say what the DIALOGUE ITSELF shows about them (the sound of a voice is not evidence here):
- "gender": "male" or "female" only when the words prove it: someone calls them by a name or title that has a gender (Mr., Mrs., Ms., Miss, Madam, Sir, a clearly feminine or masculine first name, "Judge Roberta"), or he / she / his / her is said about them, or they say it of themselves. Otherwise "".
- "evidence": the exact words from the transcript that prove the gender (copy them letter for letter), or "".
- "role": their job or position when the dialogue shows it (judge, lawyer, prosecutor, doctor, officer, king, teacher ...): one or two words in lower case, or "".
Never guess; when the words do not prove it, leave it empty.
Return ONLY JSON: [{"speaker":"...","gender":"","evidence":"","role":""}]
"""
MAX_PEOPLE_CHARS = 400000


def _plain(t):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", str(t or "").lower())).strip()


def ask_people(job_id, prompt, key):
    """One Gemini call; returns the parsed JSON or None. Never raises."""
    try:
        import gemini_service
        payload = {"contents": [{"parts": [{"text": prompt}]}],
                   "generationConfig": {"maxOutputTokens": 8192, "responseMimeType": "application/json"}}
        data, err = gemini_service.call_gemini(key, payload, timeout=90)
        try:
            gemini_service.record_gemini(job_id, data)
        except Exception:
            pass
        if data is None:
            print(f"[gender] dialogue read: no answer: {err}")
            return None
        return json.loads(gemini_service._strip_code_fences(data["candidates"][0]["content"]["parts"][0]["text"]))
    except Exception as ex:
        print(f"[gender] dialogue read: unusable answer: {ex}")
        return None


def read_transcript_people(job_id, rows, key, ask=None, source_language="en"):
    """{speaker: {"gender": "male"|"female"|"", "role": str}} from what the WORDS of the dialogue show (names, titles, he/she, jobs).
    A gender counts only when the quoted evidence really occurs in the transcript. Never raises; {} when nothing is known."""
    try:
        lines = [(str(r.get("speaker") or ""), str(r.get("text") or "").strip()[:240])
                 for r in sorted(rows or [], key=lambda r: float(r.get("start") or 0)) if str(r.get("text") or "").strip()]
        names = []
        for n, _ in lines:
            if n and n not in names:
                names.append(n)
        if not names or (ask is None and not key):
            return {}
        body, used = [], 0
        for n, t in lines:
            used += len(n) + len(t) + 4
            if used > MAX_PEOPLE_CHARS:
                break
            body.append(f"{n} | {t}")
        prompt = (PEOPLE_PROMPT if languages.get(source_language).code == "en" else language_prompts.people_rules(source_language)) + "Speakers: " + ", ".join(names) + "\nTranscript:\n" + "\n".join(body)
        answer = ask(prompt) if ask else ask_people(job_id, prompt, key)
        if isinstance(answer, dict):
            answer = answer.get("speakers") or answer.get("people")
        if not isinstance(answer, list):
            return {}
        haystack = " " + _plain(" ".join(t for _, t in lines)) + " "
        out = {}
        for it in answer:
            if not isinstance(it, dict) or str(it.get("speaker") or "") not in names:
                continue
            name = str(it["speaker"])
            ev = _plain(it.get("evidence"))
            g = clean(it.get("gender")) if ev and f" {ev} " in haystack else ""
            role = re.sub(r"[^a-z \-]", "", str(it.get("role") or "").lower()).strip()[:30]
            if g or role:
                out[name] = {"gender": g, "role": role}
        return out
    except Exception as ex:
        print(f"[gender] dialogue read skipped: {ex}")
        return {}


def combine_people(genders, people):
    """Merge what the dialogue shows into the gender table. The person's own choice is never touched. A gender the words prove wins
    over a voice guess (a deep or separated voice can fool the listener); if the voice said otherwise the speaker stays marked
    unsure, so the person is shown it. Returns (new table, {speaker: role})."""
    out, roles = {}, {}
    for name, info in (genders or {}).items():
        info = dict(info or {})
        p = (people or {}).get(name) or {}
        if p.get("role"):
            roles[name] = p["role"]
        tg = clean(p.get("gender"))
        if tg and info.get("source") != "chosen":
            voice = clean(info.get("gender"))
            if voice == tg:
                info["uncertain"] = False
            else:
                info = {"gender": tg, "source": "text", "uncertain": bool(voice)}
        out[name] = info
    return out, roles


def read_flags(item):
    """(addressee, gender_check) from one translated item; unknown or odd values become ("", False)."""
    addressee = str((item or {}).get("addressee") or "").strip().lower()
    if addressee not in ("male", "female", "group", "none", "unknown"):
        addressee = ""
    return addressee, (item or {}).get("gender_check") is True
