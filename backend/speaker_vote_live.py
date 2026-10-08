"""Live side of the speaker vote: asks the two listening requests, applies speaker_vote.decide to the rows.

speaker_vote.py is pure logic (no network); this module is the only place that talks to the listening service for it.
Voters used in production: the dialogue request, the audio request and the app's own detector. The voiceprint voter exists
in speaker_vote.py but is OFF here until a matched end-to-end memory test shows no higher peak memory.

Nothing in this file names a vendor or model to a customer: every message that can reach a customer is written in plain words.
"""
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
STATE_FILE = "speaker_vote.json"
STATE_VERSION = 1

# The reasons the editor shows next to "Check speaker" for a line the vote is unsure about (the rest are not shown).
SHOWN_REASONS = ("voters_split", "candidate_rejected")
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


def prompt_for(kind, rows):
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


def _payload(kind, rows, audio_b64, retry=False):
    parts = []
    if kind == "audio":
        parts.append({"inline_data": {"mime_type": "audio/mpeg", "data": audio_b64}})
    parts.append({"text": prompt_for(kind, rows)})
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


def _one_voter(kind, job_id, rows, audio_b64, api_key, ask, usage):
    """One listening request, with one more try (and more output room) when the answer is missing or unusable.
    Returns {segment_id: label} or None. Never raises."""
    ids = [r["segment_id"] for r in rows]
    parse = speaker_vote.parse_audio_answer if kind == "audio" else speaker_vote.parse_dialogue_answer
    for attempt in range(2):
        try:
            data, _err = ask(api_key, _payload(kind, rows, audio_b64, retry=attempt > 0), 600)
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


def collect(job_id, rows, vocals_path, work_dir, api_key, ask=None):
    """Returns {"dialogue": map|None, "audio": map|None, "usage": {...}, "reused": bool}.
    Answers are saved in work_dir so a restart reuses them (no second request, no second cost)."""
    ask = ask or _default_ask
    fp = fingerprint(rows)
    state_path = Path(work_dir) / STATE_FILE
    saved = _load_state(state_path, fp)
    if saved:
        return {"dialogue": saved.get("dialogue"), "audio": saved.get("audio"), "usage": saved.get("usage") or {}, "reused": True}
    usage = {"usd": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_thinking": 0}
    if not api_key:
        return {"dialogue": None, "audio": None, "usage": usage, "reused": False}
    audio_b64 = None
    mp3 = Path(work_dir) / "speaker_vote_voices.mp3"
    if vocals_path and Path(vocals_path).exists():
        small = shrink_audio(vocals_path, mp3)
        if small:
            audio_b64 = base64.b64encode(small.read_bytes()).decode("ascii")
        try:
            mp3.unlink()
        except Exception:
            pass
    results, locks = {}, threading.Lock()

    def run(kind):
        u = {"usd": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_thinking": 0}
        res = _one_voter(kind, job_id, rows, audio_b64, api_key, ask, u)
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


# ------------------------------------------------------------------ apply

def apply(rows, votes):
    """Run the vote on `rows` (dicts with segment_id, speaker) and write the result into them.
    Returns a summary: status ('ok' or why the vote did not act), counts and the names of new speakers.
    Rows are changed only when status == 'ok'."""
    summary = {"status": "not_run", "changed": 0, "badged": 0, "new_speakers": [], "merged": 0, "voters": []}
    try:
        lines = [{"segment_id": r["segment_id"], "speaker": r["speaker"]} for r in rows]
        res = speaker_vote.decide(lines, {"dialogue": votes.get("dialogue"), "audio": votes.get("audio"), "voice": None})
    except Exception as ex:
        summary["status"] = "error_" + type(ex).__name__
        return summary
    s = res.get("summary") or {}
    summary["status"] = s.get("status", "error")
    summary["voters"] = list(s.get("voters_used") or [])
    if summary["status"] != "ok":
        return summary
    by_id = {r["segment_id"]: r for r in res["lines"]}
    before = {r["speaker"] for r in rows}
    for row in rows:
        d = by_id.get(row["segment_id"])
        if not d:
            continue
        row["speaker"] = d["speaker"]
        row["speaker_vote"] = {"source": d["source"], "support": d["support"], "present": d["voters_present"],
                               "badge": bool(d["badge"]), "reasons": list(d["reasons"])}
    after = {r["speaker"] for r in rows}
    summary.update(changed=int(s.get("lines_changed", 0)), badged=int(s.get("badged_lines", 0)),
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
