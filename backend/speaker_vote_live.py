"""Live side of the speaker vote: asks the two listening requests, applies speaker_vote.decide to the rows.

speaker_vote.py is pure logic (no network); this module is the only place that talks to the listening service for it.
Voters used in production: the dialogue request, the audio request and the app's own detector. The voiceprint voter exists
in speaker_vote.py but is OFF here until a matched end-to-end memory test shows no higher peak memory.

Nothing in this file names a vendor or model to a customer: every message that can reach a customer is written in plain words.
"""
import languages, language_prompts
import base64
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import speaker_vote

# One number ties the engine to the longest video the app dubs (main._ld_pricing uses it as max_min). At 24 kbps an hour of
# voices is about 10.8 MB (measured), 14.4 MB once encoded for sending; the request limit is 20 MB, so the audio fits up to
# about 80 minutes. The engine was measured on an 8-minute clip only; raise this only after a long-video test.
ENGINE_MAX_MIN = 60
AUDIO_BITRATE = "24k"
MAX_AUDIO_BYTES = 13_500_000            # the encoded audio is a third larger when sent; the request limit is 20 MB
def _audio_weight():
    """How many times the voter that listens counts in the vote (LONGDUB_AUDIO_WEIGHT, 1 = every voter once). Default 2: in the Dutch
    test clip it was alone against the other two on 14 lines and right on all 14."""
    try:
        n = int(os.environ.get("LONGDUB_AUDIO_WEIGHT", "2"))
    except ValueError:
        return 2
    return n if 1 <= n <= 3 else 2


STATE_FILE = "speaker_vote.json"
STATE_VERSION = 1

# The reasons the editor shows next to "Check speaker" for a line the vote is unsure about (the rest are not shown).
SHOWN_REASONS = ("voters_split", "candidate_rejected", "picture_disagrees")
NOT_VOTED_REASONS = ("insufficient_line_voters", "manual_choice", "insufficient_support")

ADDED_SPEAKER_MESSAGE = "Another speaker was found and added. Please check their lines."


def fits(duration_sec):
    """The engine runs on videos up to ENGINE_MAX_MIN minutes (the same limit as the dubbing itself)."""
    try:
        return 0 < float(duration_sec) <= ENGINE_MAX_MIN * 60 + 1
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------------ the two requests (wording measured on the test clip)

def _row_json(row):
    return json.dumps({"id": row["segment_id"], "start": round(float(row["start"]), 2), "end": round(float(row["end"]), 2),
                       "text": str(row.get("text") or "").strip()}, ensure_ascii=False)


def transcript(rows):
    return "\n".join(_row_json(r) for r in rows)


def exact_people(prompt, people):
    """The user's number of speakers is a fact: tell the check exactly how many people speak (instead of "several"), so it does not
    invent more or fold two together. people=None (no number given) leaves the wording as it was."""
    try:
        n = int(people)
    except (TypeError, ValueError):
        return prompt
    if n < 1:
        return prompt
    who = "person" if n == 1 else "people"
    out = prompt.replace("Several different people speak. ", f"Exactly {n} different {who} speak{'s' if n == 1 else ''}. ")
    out = out.replace("Do not invent extra people unless clearly needed.",
                      f"Do not invent extra people: exactly {n} {who} speak{'s' if n == 1 else ''}, so use only {n} letter{'' if n == 1 else 's'}.")
    return out.replace('{"people": 3,', f'{{"people": {n},')


def prompt_for(kind, rows, source_language="en", people=None):
    if languages.get(source_language).code != "en":
        return exact_people(language_prompts.speaker_prompt(kind, rows, source_language), people)
    return exact_people(_prompt_en(kind, rows), people)


def _prompt_en(kind, rows):
    audio = kind == "audio"
    head = ("Below is the transcript of a scene from a film, one line per row, with start and end seconds. Several different people speak. ")
    if audio:
        head += ("You also hear the isolated voices of the whole clip; use the timestamps to find each line in the audio, and use how the "
                 "voice sounds (not only the words), especially for very short lines. ")
    return (head + "Decide for EVERY line which person says it, using the flow of the conversation (who is addressed, titles used, who answers an "
            "order, read-backs, self-references)" + (" and the sound of the voices" if audio else "") + ". Name the people with letters A, B, C...; "
            "the same letter always means the same person. A one-word reply can belong to a different person than the line before it. "
            "Do not invent extra people unless clearly needed. Return JSON only: "
            "{\"people\": 3, \"lines\": [{\"id\": \"seg_0\", \"speaker\": \"A\"}, ...]} covering every line.\n\nLINES:\n" + transcript(rows))


def _limit(n_lines, retry=False):
    """Output room. Thinking counts toward the output limit and a long video has hundreds of lines to answer (about 17 tokens per
    line, measured on 92 lines; thinking measured at 4,000-7,000 tokens on 92 lines), so the room grows with the number of lines."""
    if retry:
        return 65536
    return min(65536, 24576 + 40 * max(1, int(n_lines)))


def _payload(kind, rows, audio_b64, retry=False, source_language="en", people=None, extra=""):
    parts = []
    if kind == "audio":
        parts.append({"inline_data": {"mime_type": "audio/mpeg", "data": audio_b64}})
    parts.append({"text": prompt_for(kind, rows, source_language=source_language, people=people) + (extra or "")})
    # "medium" thinking is what the test clip was measured with (an 8,192-token budget maps to it in gemini_service)
    return {"contents": [{"parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": _limit(len(rows), retry),
                                 "thinkingConfig": {"thinkingLevel": "medium"}}}


def _answer_text(data):
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    except (KeyError, IndexError, TypeError):
        return ""


def _default_ask(api_key, payload, timeout):
    import gemini_service
    return gemini_service.call_gemini(api_key, payload, timeout=timeout)


def _one_voter(kind, job_id, rows, audio_b64, api_key, ask, usage, source_language="en", people=None, extra=""):
    """One listening request, with one more try (and more output room) when the answer is missing or unusable.
    Returns {segment_id: label} or None. Never raises."""
    ids = [r["segment_id"] for r in rows]
    parse = speaker_vote.parse_audio_answer if kind == "audio" else speaker_vote.parse_dialogue_answer
    for attempt in range(2):
        try:
            data, _err = ask(api_key, _payload(kind, rows, audio_b64, retry=attempt > 0, source_language=source_language, people=people, extra=extra), 600)
        except Exception as ex:      # a failing request is an unavailable voter, not a failing job
            print(f"[speaker_vote] {kind} request failed: {type(ex).__name__}")
            data = None
        if data is None:
            continue
        try:
            import app_state
            app_state.record_gemini(job_id, data)
        except Exception:
            pass
        try:
            import resource_meter
            usage["usd"] += float(resource_meter.gemini_usd(data) or 0.0)
        except Exception:
            pass
        u = (data.get("usageMetadata") or {}) if isinstance(data, dict) else {}
        usage["calls"] += 1
        usage["tokens_in"] += int(u.get("promptTokenCount", 0) or 0)
        usage["tokens_out"] += int(u.get("candidatesTokenCount", 0) or 0)
        usage["tokens_thinking"] += int(u.get("thoughtsTokenCount", 0) or 0)
        parsed = parse(_answer_text(data), ids)
        if parsed:
            return parsed
    return None


# ------------------------------------------------------------------ audio for the audio request

def shrink_audio(vocals_path, out_path):
    """Mono 16 kHz mp3 at AUDIO_BITRATE. Returns the path, or None when it cannot be made or is too big to send."""
    import ffmpeg_utils
    try:
        ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-loglevel", "error", "-i", str(vocals_path), "-ac", "1", "-ar", "16000",
                                 "-b:a", AUDIO_BITRATE, str(out_path)])
        size = Path(out_path).stat().st_size
    except Exception as ex:
        print(f"[speaker_vote] could not prepare the audio: {type(ex).__name__}")
        return None
    if size <= 0 or size > MAX_AUDIO_BYTES:
        return None
    return Path(out_path)


# ------------------------------------------------------------------ saved answers (a restart must not pay twice)

def fingerprint(rows):
    h = hashlib.sha1()
    for r in rows:
        h.update(f"{r['segment_id']}|{float(r['start']):.2f}|{float(r['end']):.2f}|{r.get('text') or ''}\n".encode("utf-8"))
    return h.hexdigest()


def _load_state(path, fp):
    try:
        s = json.loads(Path(path).read_text(encoding="utf-8"))
        if s.get("v") == STATE_VERSION and s.get("fp") == fp:
            return s
    except Exception:
        pass
    return None


def _save_state(path, state):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _audio_b64(vocals_path, work_dir):
    """The voices as a small mp3, base64, or None when there is no recording or it is too big to send."""
    mp3 = Path(work_dir) / "speaker_vote_voices.mp3"
    out = None
    if vocals_path and Path(vocals_path).exists():
        small = shrink_audio(vocals_path, mp3)
        if small:
            out = base64.b64encode(small.read_bytes()).decode("ascii")
        try:
            mp3.unlink()
        except Exception:
            pass
    return out


def collect(job_id, rows, vocals_path, work_dir, api_key, ask=None, source_language="en", people=None):
    """Returns {"dialogue": map|None, "audio": map|None, "usage": {...}, "reused": bool}.
    Answers are saved in work_dir so a restart reuses them (no second request, no second cost)."""
    ask = ask or _default_ask
    fp = fingerprint(rows)
    if people:      # answers given for another number of speakers are not reused
        fp = hashlib.sha256((fp + f"|people={int(people)}").encode("ascii")).hexdigest()
    if languages.get(source_language).code != "en":
        fp = hashlib.sha256((fp + languages.get(source_language).code).encode("ascii")).hexdigest()
    state_path = Path(work_dir) / STATE_FILE
    saved = _load_state(state_path, fp)
    if saved:
        return {"dialogue": saved.get("dialogue"), "audio": saved.get("audio"), "usage": saved.get("usage") or {}, "reused": True}
    usage = {"usd": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_thinking": 0}
    if not api_key:
        return {"dialogue": None, "audio": None, "usage": usage, "reused": False}
    audio_b64 = _audio_b64(vocals_path, work_dir)
    results, locks = {}, threading.Lock()

    def run(kind):
        u = {"usd": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_thinking": 0}
        res = _one_voter(kind, job_id, rows, audio_b64, api_key, ask, u, source_language=source_language, people=people)
        with locks:
            results[kind] = res
            for k in usage:
                usage[k] += u[k]

    kinds = ["dialogue"] + (["audio"] if audio_b64 else [])
    threads = [threading.Thread(target=run, args=(k,), daemon=True) for k in kinds]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    usage["usd"] = round(usage["usd"], 5)
    out = {"dialogue": results.get("dialogue"), "audio": results.get("audio"), "usage": usage, "reused": False}
    try:
        _save_state(state_path, {"v": STATE_VERSION, "fp": fp, "dialogue": out["dialogue"], "audio": out["audio"],
                                 "usage": usage, "at": time.time()})
    except Exception as ex:
        print(f"[speaker_vote] could not save the answers: {type(ex).__name__}")
    return out


# ------------------------------------------------------------------ the second look (fewer people named than the user said)

REASK_STATE_FILE = "speaker_vote_reask.json"
REASK_VERSION = 1
REASK = os.environ.get("LONGDUB_SPEAKER_REASK", "1").strip().lower() not in ("0", "off", "no", "false")      # 0: never look a second time


def people_named(answer):
    """How many different people an answer ({segment_id: label}) names."""
    return len(set(answer.values())) if isinstance(answer, dict) else 0


def reask_text(kind, named, people):
    """The paragraph added after the lines when the first answer named fewer people than the user said speak. Plain words, no vendor."""
    n = int(people)
    out = (f"\n\nA FIRST ANSWER to these lines used only {int(named)} different letters, but exactly {n} different people speak. "
           f"Some lines were given to a person who does not say them. Read every line again and use all {n} letters: give a line to a letter "
           "that is still unused whenever a different name, title, or a reply to the previous line shows that another person speaks. "
           "Several short replies in a row often belong to different people. Never give two different people the same letter. "
           "Return the same JSON, covering every line.")
    if kind == "audio":
        out += " Listen again to the voices: a clearly different voice is a different person."
    return out


def reask(job_id, rows, votes, vocals_path, work_dir, api_key, people, ask=None, source_language="en"):
    """The user said N people speak and an answer named fewer: that check is asked once more, told how few it named and that exactly N
    speak. A new answer replaces the old one only when it names MORE people (it never makes things worse). Saved in work_dir, so a restart
    or a resume after the user's answer never pays twice. Returns {"dialogue", "audio" (the answers to use), "changed": [kinds replaced],
    "asked": [kinds asked], "usage", "reused"}. Never raises."""
    out = {"dialogue": (votes or {}).get("dialogue"), "audio": (votes or {}).get("audio"), "changed": [], "asked": [],
           "usage": {"usd": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_thinking": 0}, "reused": False}
    try:
        n = int(people)
        if n < 2 or not REASK or not api_key:
            return out
        need = [k for k in ("dialogue", "audio") if isinstance(out[k], dict) and out[k] and people_named(out[k]) < n]
        if not need:
            return out
        ask = ask or _default_ask
        fp = fingerprint(rows)
        key = f"{fp}|reask|v{REASK_VERSION}|people={n}|" + ",".join(f"{k}:{people_named(out[k])}" for k in need)
        if languages.get(source_language).code != "en":
            key += "|" + languages.get(source_language).code
        fp = hashlib.sha256(key.encode("utf-8")).hexdigest()
        state_path = Path(work_dir) / REASK_STATE_FILE
        saved = _load_state(state_path, fp)
        if saved:
            new, usage, reused = saved.get("new") or {}, saved.get("usage") or out["usage"], True
        else:
            audio_b64 = _audio_b64(vocals_path, work_dir) if "audio" in need else None
            if "audio" in need and not audio_b64:
                need = [k for k in need if k != "audio"]
            new, usage, reused = {}, {"usd": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_thinking": 0}, False
            lock = threading.Lock()

            def run(kind):
                u = {"usd": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_thinking": 0}
                res = _one_voter(kind, job_id, rows, audio_b64, api_key, ask, u, source_language=source_language, people=n,
                                 extra=reask_text(kind, people_named(out[kind]), n))
                with lock:
                    new[kind] = res
                    for k in usage:
                        usage[k] += u[k]

            threads = [threading.Thread(target=run, args=(k,), daemon=True) for k in need]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            usage["usd"] = round(usage["usd"], 5)
            try:
                _save_state(state_path, {"v": STATE_VERSION, "fp": fp, "new": new, "usage": usage, "at": time.time()})
            except Exception as ex:
                print(f"[speaker_vote] could not save the second look: {type(ex).__name__}")
        out["asked"] = [k for k in need if k in new]      # (a check with no audio to send was not asked)
        for kind in need:
            ans = new.get(kind)
            if isinstance(ans, dict) and people_named(ans) > people_named(out[kind]):
                out[kind] = ans
                out["changed"].append(kind)
        out["usage"], out["reused"] = usage, reused
    except Exception as ex:
        print(f"[speaker_vote] second look skipped: {type(ex).__name__}")
    return out


# ------------------------------------------------------------------ apply

def apply(rows, votes, allow_new=True, picture=None, max_new=None, min_new_lines=None, new_support=None, fresh=False, keep=False):
    """Run the vote on `rows` (dicts with segment_id, speaker) and write the result into them. allow_new=False: the number of speakers is
    known (the user said it), so the vote only decides who of the existing speakers says a line and never adds a new one.
    fresh: the user's number replaces the detector's grouping: the speakers are the people the two checks tell apart (one letter, one speaker);
    the detector's names only decide nothing. Needs allow_new and max_new (= the number of people minus one).
    new_support: how many checks must agree on a line for it to count towards a new speaker (the vote's default: 2).
    min_new_lines: lines a new speaker needs (default of the vote: 5); only used together with max_new.
    max_new: at most this many new speakers (used when the user's number is higher than the detector found: the vote may fill the gap, no further).
    keep: every speaker the rows start with keeps existing (used when the detector found exactly the user's number): a speaker the vote would
    empty gets back the lines the detector gave it, each marked 'check speaker'. Lines are still corrected between speakers that survive.
    Returns a summary: status ('ok' or why the vote did not act), counts and the names of new speakers.
    Rows are changed only when status == 'ok'."""
    summary = {"status": "not_run", "changed": 0, "badged": 0, "new_speakers": [], "merged": 0, "voters": []}
    try:
        lines = [{"segment_id": r["segment_id"], "speaker": "Speaker 1" if fresh else r["speaker"],
                  **({"manual_speaker": r["manual_speaker"]} if picture is not None and isinstance(r.get("manual_speaker"), bool) else {})} for r in rows]
        policy = {"audio_weight": _audio_weight()}
        extra = {}
        if picture is not None:
            import image_unit
            extra["picture"] = image_unit.votes_for_vote(picture, rows, votes.get("dialogue") or {})
            policy["picture_weight"] = image_unit.PICTURE_WEIGHT
        if fresh:
            policy["app_votes"] = False
        if not allow_new:
            policy["allow_new"] = False
        elif max_new is not None:
            policy["max_new_speakers"] = max(0, int(max_new))
            if min_new_lines is not None:
                policy["min_new_lines"] = max(1, int(min_new_lines))      # the user said these people exist: a small part is enough
            if new_support is not None:
                policy["new_support"] = max(1, int(new_support))      # ... and when they insisted, one check's word is enough
        res = speaker_vote.decide(lines, {"dialogue": votes.get("dialogue"), "audio": votes.get("audio"), "voice": None, **extra}, policy)
    except Exception as ex:
        summary["status"] = "error_" + type(ex).__name__
        return summary
    s = res.get("summary") or {}
    summary["status"] = s.get("status", "error")
    summary["voters"] = list(s.get("voters_used") or [])
    summary["fresh"] = bool(fresh)
    if summary["status"] != "ok":
        return summary
    by_id = {r["segment_id"]: r for r in res["lines"]}
    before = {r["speaker"] for r in rows}
    start = {r["segment_id"]: r["speaker"] for r in rows}
    summary["by_line"] = {sid: {"by": list(d.get("voters_for") or []), "support": d.get("support"), "present": d.get("voters_present")}
                          for sid, d in by_id.items()}      # who backed each line's winner (for the log only)
    for row in rows:
        d = by_id.get(row["segment_id"])
        if not d:
            continue
        row["speaker"] = d["speaker"]
        row["speaker_vote"] = {"source": d["source"], "support": d["support"], "present": d["voters_present"],
                               "badge": bool(d["badge"]), "reasons": list(d["reasons"])}
    after = {r["speaker"] for r in rows}
    restored = 0
    if keep and not fresh:
        gone = before - after
        for row in rows:
            if start.get(row["segment_id"]) in gone:
                row["speaker"] = start[row["segment_id"]]
                v = row.get("speaker_vote") if isinstance(row.get("speaker_vote"), dict) else {}
                row["speaker_vote"] = dict(v, source="kept", badge=True, reasons=["voters_split"])
                restored += 1
        after = {r["speaker"] for r in rows}
    summary["restored"] = restored
    summary.update(changed=max(0, int(s.get("lines_changed", 0)) - restored),
                   badged=sum(1 for r in rows if isinstance(r.get("speaker_vote"), dict) and r["speaker_vote"].get("badge")) if restored else int(s.get("badged_lines", 0)),
                   new_speakers=[x["label"] for x in (s.get("new_speakers") or [])],
                   merged=len(before - after),
                   dropped_small=len(s.get("dropped_small") or []))
    return summary


def compact_names(rows):
    """Default names 'Speaker N' are renumbered 1..k in order, so a merged or added speaker leaves no gap ('Speaker 1, 2, 3, 6').
    Only names of that default form are touched. Returns {old_name: new_name} (names that did not change are left out)."""
    import re
    nums = {}
    for r in rows:
        m = re.fullmatch(r"Speaker ([0-9]+)", str(r.get("speaker") or ""))
        if m:
            nums[r["speaker"]] = int(m.group(1))
    order = sorted(nums, key=lambda n: nums[n])
    taken = {r["speaker"] for r in rows} - set(order)
    rename, k = {}, 0
    for old in order:
        k += 1
        new = f"Speaker {k}"
        while new in taken:
            k += 1
            new = f"Speaker {k}"
        if new != old:
            rename[old] = new
    if rename:
        for r in rows:
            if r.get("speaker") in rename:
                r["speaker"] = rename[r["speaker"]]
    return rename


def review_from_vote(row):
    """The 'check speaker' evidence for a line the vote ruled on, or None when the vote did not rule on it
    (the detector-based evidence stays for those lines)."""
    v = row.get("speaker_vote")
    if not isinstance(v, dict):
        return None
    reasons = v.get("reasons") or []
    if any(x in NOT_VOTED_REASONS for x in reasons):
        return None
    shown = [x for x in reasons if x in SHOWN_REASONS]
    if v.get("badge") and not shown:
        shown = ["voters_split"]
    return {"speaker_confidence": "low" if v.get("badge") else "high", "speaker_reasons": shown}
