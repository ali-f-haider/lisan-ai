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


# How the voters' answers are combined (LONGDUB_VOTE_RULE): 'plurality' (default) = the weighted majority, the listening check
# counting twice; 'listen_first' = the listening check decides unless two of the other voters agree on one other person.
# Measured on the labelled C4K clip (28 Oct 2026) with the voters' names matched the way the app matches them: both 88.6%
# (listening alone 87.7%). On the Dutch clip the listening check was right alone against two others on 14 lines, which favours
# the weighted majority, so it stays the default until a second labelled clip says otherwise.
VOTE_RULE = os.environ.get("LONGDUB_VOTE_RULE", "plurality").strip().lower()
if VOTE_RULE not in ("listen_first", "plurality"):
    VOTE_RULE = "plurality"

def _level(name, default):
    v = os.environ.get(name, default).strip().lower()
    return v if v in ("low", "medium", "high") else default


# How hard each check thinks. Both think hard (Ali, 28 Oct 2026: "keep deep thinking on listening even if it costs more").
# Measured: C4K reading 83-85% -> 88%, listening 88% -> 89%; Air Force One replay (1.84.1) listening 93.2% -> 94.9% of the
# lines, for about a third more cost. (At "medium" with a JSON answer Argon does not think at all.)
THINKING = _level("LONGDUB_SPEAKER_THINKING", "high")            # the reading check
LISTEN_THINKING = _level("LONGDUB_LISTEN_THINKING", "high")      # the listening check

STATE_FILE = "speaker_vote.json"
STATE_VERSION = 1

# The reasons the editor shows next to "Check speaker" for a line the vote is unsure about (the rest are not shown).
SHOWN_REASONS = ("voters_split", "candidate_rejected", "picture_disagrees", "two_faces")
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
    # "high" thinking: Gemini 4 Argon does not think at all at "medium" when it must answer in JSON (measured 28 Oct 2026: 0 thinking
    # tokens). With "high" it thinks (6-12k tokens) and gets more lines right on the labelled C4K clip: reading 83-85% -> 88%,
    # listening 88% -> 89%.
    return {"contents": [{"parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": _limit(len(rows), retry),
                                 "thinkingConfig": {"thinkingLevel": LISTEN_THINKING if kind == "audio" else THINKING}}}


def _answer_text(data):
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    except (KeyError, IndexError, TypeError):
        return ""


def speaker_models():
    """The models the speaker checks try, in order: the strongest first (config.SPEAKER_MODELS), then the usual ones."""
    import config
    return list(dict.fromkeys(list(getattr(config, "SPEAKER_MODELS", []) or []) + list(config.GEMINI_MODELS)))


def _default_ask(api_key, payload, timeout):
    import gemini_service
    return gemini_service.call_gemini(api_key, payload, timeout=timeout, models=speaker_models())


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
        if isinstance(data, dict) and data.get("modelVersion"):
            usage["model"] = str(data["modelVersion"])[:60]      # which model answered (for the log; not summed)
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
        return {"dialogue": saved.get("dialogue"), "audio": saved.get("audio"), "usage": saved.get("usage") or {}, "reused": True,
                "models": saved.get("models") or {}}
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
            models[kind] = u.get("model")
            for k in usage:
                usage[k] += u[k]

    models = {}
    kinds = ["dialogue"] + (["audio"] if audio_b64 else [])
    threads = [threading.Thread(target=run, args=(k,), daemon=True) for k in kinds]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    usage["usd"] = round(usage["usd"], 5)
    out = {"dialogue": results.get("dialogue"), "audio": results.get("audio"), "usage": usage, "reused": False,
           "models": {k: v for k, v in models.items() if v}}
    try:
        _save_state(state_path, {"v": STATE_VERSION, "fp": fp, "dialogue": out["dialogue"], "audio": out["audio"],
                                 "usage": usage, "models": out["models"], "at": time.time()})
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

def apply(rows, votes, allow_new=True, picture=None, max_new=None, min_new_lines=None, new_support=None, fresh=False, keep=False, cap=None):
    """Run the vote on `rows` (dicts with segment_id, speaker) and write the result into them. allow_new=False: the number of speakers is
    known (the user said it), so the vote only decides who of the existing speakers says a line and never adds a new one.
    fresh: the user's number replaces the detector's grouping: the speakers are the people the two checks tell apart (one letter, one speaker);
    the detector's names only decide nothing. Needs allow_new and max_new (= the number of people minus one).
    new_support: how many checks must agree on a line for it to count towards a new speaker (the vote's default: 2).
    min_new_lines: lines a new speaker needs (default of the vote: 5); only used together with max_new.
    max_new: at most this many new speakers (used when the user's number is higher than the detector found: the vote may fill the gap, no further).
    keep: every speaker the rows start with keeps existing (used when the detector found exactly the user's number): a speaker the vote would
    empty gets back the lines the detector gave it, each marked 'check speaker'. Lines are still corrected between speakers that survive.
    cap: the user's number of speakers. With keep, or when the vote may fill a gap (max_new), a detector speaker no check backs may give its
    place to a person the checks agree on, never going above cap (see _swap).
    Returns a summary: status ('ok' or why the vote did not act), counts and the names of new speakers.
    Rows are changed only when status == 'ok'."""
    summary = {"status": "not_run", "changed": 0, "badged": 0, "new_speakers": [], "merged": 0, "voters": []}
    try:
        lines = [{"segment_id": r["segment_id"], "speaker": "Speaker 1" if fresh else r["speaker"],
                  **({"manual_speaker": r["manual_speaker"]} if picture is not None and isinstance(r.get("manual_speaker"), bool) else {})} for r in rows]
        policy = {"audio_weight": _audio_weight(), "explain": True, "rule": VOTE_RULE}
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
        voters = {"dialogue": votes.get("dialogue"), "audio": votes.get("audio"), "voice": None, **extra}
        res = speaker_vote.decide(lines, voters, policy)
        swap = None
        unbacked = _unbacked(res, {r["speaker"] for r in rows}) if (res.get("summary") or {}).get("status") == "ok" else set()
        if not fresh and ((keep and not allow_new) or (allow_new and max_new is not None)):
            swap = _swap(rows, lines, voters, policy, res, cap if cap else (len({r["speaker"] for r in rows}) if keep else None), keep)
        if swap:
            res = swap["res"]
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
    summary["swapped"] = [dict(old=o, old_lines=sum(1 for v in start.values() if v == o)) for o in sorted(swap["replaced"])] if swap else []
    if keep and not fresh:
        gone = before - after
        if swap:
            gone = set(swap["restore"])       # the speakers that gave their place to a new person stay gone; only the rest come back
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
                   merged=max(0, len(before - after) - len(summary["swapped"])),
                   dropped_small=len(s.get("dropped_small") or []),
                   candidates=[{k: x.get(k) for k in ("lines", "backed_lines", "reason", "first")} for x in (s.get("dropped_small") or [])],
                   unbacked=sorted(unbacked))
    return summary


# The user said how many people speak. The detector found that number (or fewer, and the vote fills the gap), but one of its
# speakers is backed by no check at all (the vote takes every line away from it, or it keeps a few lines only the detector stands
# behind) while the checks agree on a person the detector does not have. In the test video: a mother and her daughter on one
# speaker, or the judge's lines on the main character, while a separate speaker held half of one 'Mm-hmm' or one 'no'.
# Such a person may take that place: never more speakers than the user's number.
SWAP_MAX_LINES = 3        # a speaker with more lines than this is never given away (even when only the detector backs it)
SWAP_MIN_LINES = 2        # the new person needs this many lines, each one backed by at least SWAP_SUPPORT checks (2: the channel owner's two closing lines)
SWAP_SUPPORT = 2


def _unbacked(res, before):
    """The detector's speakers that no check backs after a vote: the vote emptied them, or every line they keep (at most
    SWAP_MAX_LINES) is backed by the detector alone."""
    per = {}
    for d in res["lines"]:
        per.setdefault(d["speaker"], []).append(d)
    out = set(before) - set(per)
    for name, items in per.items():
        if name in before and len(items) <= SWAP_MAX_LINES and all(set(d.get("voters_for") or []) <= {"app"} for d in items):
            out.add(name)
    return out


def _swap(rows, lines, voters, policy, res, cap, keep):
    """When the checks agree on a person the detector lacks and a detector speaker is backed by no check, the person takes that place.
    keep: the number stays exactly the detector's (= the user's); otherwise (the vote filled a gap) it never goes above cap and never
    below what the first vote reached, and the swap must add someone. Returns None (nothing to swap) or
    {res, replaced: the detector's speakers given away, restore: emptied speakers that come back (keep only)}."""
    s1 = res.get("summary") or {}
    if s1.get("status") != "ok" or not cap:
        return None
    before = {r["speaker"] for r in rows}
    first = {d["speaker"] for d in res["lines"]}
    first_new = len(s1.get("new_speakers") or [])
    places = len(_unbacked(res, before))
    start = {}
    for r in rows:
        start[r["speaker"]] = start.get(r["speaker"], 0) + 1
    for n in range(first_new + places, first_new, -1):
        p = dict(policy, allow_new=True, max_new_speakers=n, min_new_lines=SWAP_MIN_LINES, new_support=SWAP_SUPPORT, prefer_backed=True)
        r2 = speaker_vote.decide(lines, voters, p)
        s2 = r2.get("summary") or {}
        added = len(s2.get("new_speakers") or [])
        if s2.get("status") != "ok" or added <= first_new:
            return None       # the checks agree on nobody more: nothing to swap
        final = {d["speaker"] for d in r2["lines"]}
        gone = before - final
        if keep:
            if len(final) > cap or len(gone) < added:
                continue      # more people than the user's number: try with one place fewer
            # More speakers emptied than people added: the biggest of them (most lines from the detector) come back.
            back = sorted(gone, key=lambda x: (-start.get(x, 0), x))[:len(gone) - added]
            return {"res": r2, "replaced": gone - set(back), "restore": back}
        if len(final) > cap or len(final) < len(first):
            continue
        # the speakers that made room: the smallest of those emptied, one for each person the swap added
        return {"res": r2, "replaced": set(sorted(gone, key=lambda x: (start.get(x, 0), x))[:added - first_new]), "restore": []}
    return None


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
