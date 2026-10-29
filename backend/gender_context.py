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


PITCH_SURE_MALE = 140.0       # Hz: a single clean voice this low is a man's ...
PITCH_SURE_FEMALE = 200.0     # ... and this high a woman's. Between them (and below/above 150/185) the pitch only guesses.
PITCH_SURE_SECONDS = 3.0      # clean speech the measurement must rest on
PITCH_SPLIT = 165.0           # each measured line must fall on the same side of this: one voice, not two people put together


def _line_pitches(svc, vm, src, pieces, cut, work, n=6):
    """The pitch of each of the speaker's n longest lines (lines too short or without a clear voice are left out)."""
    out = []
    for a, b in sorted(pieces, key=lambda p: p[0] - p[1])[:n]:
        try:
            x, got = svc._speaker_audio(src, [(a, b)], cut, work, 10.0)
            f0 = vm.acoustic_profile(x, svc.SR).get("f0_median") if got >= 0.6 else None
            if f0:
                out.append(float(f0))
        except Exception:
            pass
    return out


PITCH_MOST = 0.75             # share of the measured lines that must sit on the speaker's side of PITCH_SPLIT
PITCH_OTHER_MALE = 190.0      # a "male" speaker with a line this high (or a "female" one with a line this low) may be two people
PITCH_OTHER_FEMALE = 135.0


def _median(values):
    v = sorted(values)
    n = len(v)
    return (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2) if n else None


def pitch_is_sure(gender, f0, seconds, line_pitches, ai=None):
    """True when the pitch alone settles the gender: the usual pitch of the speaker's lines (`f0`, the median of the lines measured one
    by one) is clearly male (140 Hz or lower) or clearly female (200 Hz or higher), measured on enough speech, at least three in four
    lines sit on that side, no line clearly belongs to the other gender (then two people may be mixed), and the listening check, when it
    answered at all, heard neither a child nor the other gender. A single noisy or shared line no longer spoils a clear voice."""
    try:
        if gender not in ("male", "female") or not f0 or seconds < PITCH_SURE_SECONDS or not line_pitches:
            return False
        male = gender == "male"
        if (male and f0 > PITCH_SURE_MALE) or (not male and f0 < PITCH_SURE_FEMALE):
            return False
        same = sum(1 for p in line_pitches if (p < PITCH_SPLIT) == male)
        if same < PITCH_MOST * len(line_pitches):
            return False
        if any((p >= PITCH_OTHER_MALE) if male else (p <= PITCH_OTHER_FEMALE) for p in line_pitches):
            return False
        if ai:
            if ai.get("age") == "child":
                return False
            heard = clean(ai.get("gender"))
            if heard and heard != gender:
                return False
        return True
    except Exception:
        return False


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
                if not g and ai and clean(ai.get("gender")):
                    # The listener named a gender but said it was not sure (short, noisy or mixed clip), and the pitch cannot tell
                    # (the overlap of 150-185 Hz): better a marked guess the person can see than no gender at all.
                    g, source, uncertain = clean(ai.get("gender")), "heard", True
                if source == "heard" and pitch_g and pitch_g != g:
                    uncertain = True                       # the listener and the pitch disagree
                if uncertain and source in ("pitch", "heard", ""):
                    # The usual pitch of the speaker's lines measured one by one (as the pitch check does), not of all lines joined: one long
                    # line with music or another voice in it can pull the joined measurement into the middle band (Speaker 4: 121 Hz per line).
                    lines_f0 = _line_pitches(svc, vm, src, pieces, cut, work, n=8)
                    usual = _median(lines_f0)
                    lg = "male" if usual and usual <= PITCH_SURE_MALE else ("female" if usual and usual >= PITCH_SURE_FEMALE else "")
                    if lg and (not g or g == lg) and pitch_is_sure(lg, usual, got, lines_f0, ai):
                        if not g:
                            g, source = lg, "pitch"
                        uncertain = False                  # a clearly male or female voice settles it (an unsure listener that agrees stays the source)
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


def build_context(rows_all, ids, genders, roles=None, asked=None):
    """{segment_id: {"speaker_gender", "before": [...], "after": [...]}} for the lines `ids`, from ALL lines in time order.
    Each neighbour is {"speaker", "gender", "english"}. rows_all: [{segment_id, start, speaker, text}].
    A line also gets "addressee_is" (a certain gender: the line says sir / ma'am, or it uses a title like "Your Honor" and the
    speaker with that role, e.g. the judge, is known) and "probably_said_to" (the speaker of the line just before, else after).
    asked: {segment_id: what the question about the conversation found (addressee_ask.resolve)}: for a line with no sir / ma'am /
    title it gives the certain addressee ("male", "female" or "group" with "group_of") or says it is said to nobody, and then
    the neighbour hint is left out (it was the cause of the wrong "you" forms in phone calls and three-person scenes)."""
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
        found = (asked or {}).get(sid) if not sure else None
        if found:
            out[sid].update({k: v for k, v in found.items() if k in ("addressee_is", "group_of", "said_to") and v})
            continue
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


# ------------------------------------------------------------------ hearing each speaker in the scene (since 1.84.4)
# A voice cut out of its scene can fool both the pitch and a listener: on Air Force One (28 Oct 2026) the hijacker shouting and the
# pilot on the radio measured 160-170 Hz, so they were guessed "female (pitch, unsure)", while the face check saw a man. The strongest
# model hears the whole voice track with each speaker's lines marked by time, and says man / woman / child per speaker. Measured the
# same day on the two labelled clips: 20 of 20 speakers right (Air Force One 8 of 8, C4K 12 of 12), about $0.04 a clip.

CONTEXT_MAX_LINES = 12          # lines listed per speaker (the longest ones)
CONTEXT_THINKING = "medium"     # telling a man from a woman needs no long thinking


def context_prompt(speakers):
    """speakers: {name: [{"id", "start", "end", "text"}]}"""
    out = ["You hear the isolated voices of a whole film scene. Below, for some speakers, are lines they say, with start and end "
           "seconds. Listen to each speaker's lines at those times and decide, from the VOICE, whether this speaker is a man, a woman "
           "or a child. Shouting, a radio or a telephone can make a man's voice sound higher: judge the whole person, not one line. "
           "Say sure=false when the lines sound like different people or you cannot tell. Return JSON only: "
           '{"speakers": [{"name": "Speaker 1", "gender": "male" or "female", "child": false, "sure": true}]}', ""]
    for name, lines in speakers.items():
        out.append(f"{name}:")
        for l in lines:
            out.append("  " + json.dumps({"start": round(float(l["start"]), 2), "end": round(float(l["end"]), 2),
                                          "text": str(l.get("text") or "")[:120]}, ensure_ascii=False))
    return "\n".join(out)


def parse_context_answer(text, names):
    """{name: {"gender", "child", "sure"}} for the asked names only; {} when the answer is unusable."""
    try:
        obj = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", str(text or ""), re.S)
        if not m:
            return {}
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return {}
    items = obj.get("speakers") if isinstance(obj, dict) else obj
    out = {}
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        n, g = str(it.get("name") or "").strip(), clean(it.get("gender"))
        if n in names and g:
            out[n] = {"gender": g, "child": bool(it.get("child")), "sure": it.get("sure") is True}
    return out


def hear_in_context(job_id, audio_b64, rows, names, key, ask=None):
    """Asks once for every name in `names`. Returns ({name: {"gender", "child", "sure"}}, usage dict). Never raises."""
    usage = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_thinking": 0, "usd": 0.0}
    if not names or not audio_b64 or (not key and ask is None):
        return {}, usage
    speakers = {}
    for n in names:
        mine = [r for r in rows if r.get("speaker") == n and str(r.get("text") or "").strip()]
        mine.sort(key=lambda r: -(float(r["end"]) - float(r["start"])))
        if mine:
            speakers[n] = sorted(mine[:CONTEXT_MAX_LINES], key=lambda r: float(r["start"]))
    if not speakers:
        return {}, usage
    payload = {"contents": [{"parts": [{"inline_data": {"mime_type": "audio/mpeg", "data": audio_b64}},
                                       {"text": context_prompt(speakers)}]}],
               "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 8192,
                                    "thinkingConfig": {"thinkingLevel": CONTEXT_THINKING}}}
    try:
        if ask is None:
            import gemini_service
            import speaker_vote_live
            ask = lambda p: gemini_service.call_gemini(key, p, timeout=300, models=speaker_vote_live.speaker_models())
        data, _err = ask(payload)
    except Exception as ex:
        print(f"[gender] hearing in the scene failed: {ex}")
        return {}, usage
    if not data:
        return {}, usage
    try:
        import resource_meter
        u = data.get("usageMetadata") or {}
        usage.update(calls=1, tokens_in=int(u.get("promptTokenCount") or 0), tokens_out=int(u.get("candidatesTokenCount") or 0),
                     tokens_thinking=int(u.get("thoughtsTokenCount") or 0), usd=float(resource_meter.gemini_usd(data) or 0.0))
    except Exception:
        pass
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    except Exception:
        text = ""
    return parse_context_answer(text, set(speakers)), usage


def apply_context(found, heard):
    """Puts what was heard in the scene into the gender table. Only an unsure voice guess (pitch, a listener that was not sure, or
    nothing) is replaced; the person's choice and what the words show are never touched. A sure answer that agrees or replaces makes
    the speaker sure; a sure answer that disagrees with a sure voice result only marks the speaker unsure. Returns the changed names."""
    changed = []
    for name, h in (heard or {}).items():
        info = found.get(name)
        if info is None or info.get("source") in ("chosen", "text"):
            continue
        g, cur = h["gender"], clean(info.get("gender"))
        unsure_now = bool(info.get("uncertain")) or info.get("source") in ("pitch", "")
        if cur == g:
            if h["sure"] and info.get("uncertain"):
                found[name] = dict(info, uncertain=False)
                changed.append(name)
            continue
        if unsure_now and h["sure"]:
            found[name] = dict(info, gender=g, source="heard", uncertain=False)
            changed.append(name)
        elif unsure_now and not cur:
            found[name] = dict(info, gender=g, source="heard", uncertain=True)
            changed.append(name)
        elif h["sure"]:
            found[name] = dict(info, uncertain=True)          # two sure opinions disagree: the person looks
            changed.append(name)
    return changed
