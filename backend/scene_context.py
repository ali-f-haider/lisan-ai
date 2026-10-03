"""Scene context for the room sound: what kind of place is each stretch of the video recorded in?

Why: room_acoustics.py measures the reverb of the ORIGINAL blindly from its separated voices. That is the truth about how
the recording sounds, but a blind measurement can be fooled (laughter, music, overlapping voices). A second, completely
independent opinion exists: the story itself. A scene in a kitchen cannot have a two second cathedral tail.

How: one Gemini call per dub reads the transcript (and a few frames of the video when there is one) and splits the
timeline into stretches, each labelled with one kind of place. Every kind of place has an upper limit for how long and how
loud a believable room tail can be (SETTINGS below). room_acoustics.make_plan then LIMITS the measured reverb of the lines
inside that stretch to that ceiling. The context is only ever a ceiling:
  * it never adds reverb to something that was measured dry (a courtroom can be recorded with close microphones),
  * it never raises a measurement,
  * a stretch Gemini is not confident about, or does not know, is not limited at all.
So the measurement stays the source of truth, and the context only stops it from going far beyond what is plausible.

Cost: one Gemini call per dub, recorded the same way the other Gemini calls are. The result is cached next to the job, so
changing a Step 5.5 setting or redoing a long dub with the same text never pays twice.

Everything here is best effort and never raises into the caller; on any problem there is simply no limit.
Switch off globally with SCENE_CONTEXT=0.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ENABLED = os.environ.get("SCENE_CONTEXT", "1").strip() not in ("0", "false", "False", "")

CACHE_VERSION = 1
MAX_TEXT_CHARS = 90000            # transcript sent to Gemini; longer ones are thinned out evenly (about 25k tokens)
MAX_FRAMES = 10
FRAME_WIDTH = 384
PRICE_IN_PER_M = 0.30             # $ per million tokens, gemini-2.5-flash (same rates main.py uses for its job costs)
PRICE_OUT_PER_M = 2.50

# kind of place -> (what it covers, longest believable decay time in s, loudest believable tail in dB).
# These are starting estimates in the same units room_acoustics measures in; they are ceilings, not targets.
SETTINGS = {
    "outdoors":      ("outdoors, in a street, a field, a yard, on a beach",            0.30, -26.0),
    "vehicle":       ("inside a car, bus, plane cabin or boat cabin",                   0.25, -26.0),
    "studio":        ("studio, voice-over/narration over footage, close-microphone interview, call or video chat", 0.30, -26.0),
    "small_room":    ("bedroom, kitchen, office, elevator, small apartment room, cave-like closet", 0.60, -21.0),
    "tiled_room":    ("bathroom, shower, tiled kitchen, stairwell, corridor with hard walls",       1.10, -17.0),
    "medium_room":   ("living room, cafe, classroom, restaurant, shop, small courtroom, meeting room", 1.00, -17.0),
    "large_room":    ("gym, lobby, warehouse, conference hall, courtroom, school hall, large restaurant, small church", 1.90, -14.0),
    "huge_space":    ("cathedral, mosque, stadium, arena, airport or station hall, large auditorium, big cave",  3.50, -11.0),
}
UNKNOWN = "unknown"


def _fmt_t(t):
    t = max(0, int(round(float(t))))
    return f"{t // 3600}:{(t % 3600) // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


# ---------------------------------------------------------------- reading the answer

def _clean_scenes(raw, duration):
    """Gemini's list -> consecutive stretches that cover [0, duration], every one with a known kind of place or UNKNOWN."""
    out = []
    if not isinstance(raw, list):
        return out
    for it in raw:
        if not isinstance(it, dict):
            continue
        try:
            t0, t1 = float(it.get("t0")), float(it.get("t1"))
        except (TypeError, ValueError):
            continue
        if not (t1 > t0) or t0 < -1 or t0 > duration + 5:
            continue
        key = str(it.get("setting") or "").strip().lower()
        if key not in SETTINGS:
            key = UNKNOWN
        conf = "high" if str(it.get("confidence") or "").strip().lower() == "high" else "low"
        place = re.sub(r"\s+", " ", str(it.get("place") or ""))[:40].strip()
        out.append({"t0": max(0.0, t0), "t1": min(float(duration), t1) if duration else t1, "key": key, "conf": conf, "place": place})
    out.sort(key=lambda s: s["t0"])
    fixed = []
    for s in out:                                   # no overlaps: a stretch starts where the previous one ended
        if fixed and s["t0"] < fixed[-1]["t1"]:
            s["t0"] = fixed[-1]["t1"]
        if s["t1"] > s["t0"]:
            fixed.append(s)
    return fixed


def scene_at(scenes, t):
    """The stretch holding time t (or the nearest one: a line just outside every stretch takes its neighbour), or None."""
    if not scenes:
        return None
    best, best_d = None, None
    for s in scenes:
        if s["t0"] <= t <= s["t1"]:
            return s
        d = min(abs(t - s["t0"]), abs(t - s["t1"]))
        if best_d is None or d < best_d:
            best, best_d = s, d
    return best if best_d is not None and best_d <= 5.0 else None


def limit_for(scene):
    """-> (rt_max, wet_max) for a confident, known stretch, else None (no limit)."""
    if not scene or scene.get("conf") != "high" or scene.get("key") not in SETTINGS:
        return None
    _label, rt_max, wet_max = SETTINGS[scene["key"]]
    return rt_max, wet_max


def apply_limit(rt, wet_db, scene):
    """-> (rt, wet_db, changed). Only ever lowers."""
    lim = limit_for(scene)
    if lim is None:
        return rt, wet_db, False
    rt2, wet2 = min(float(rt), lim[0]), min(float(wet_db), lim[1])
    return rt2, wet2, (rt2 < float(rt) - 1e-6 or wet2 < float(wet_db) - 1e-6)


# ---------------------------------------------------------------- the Gemini call

def _transcript(rows):
    lines = []
    for r in sorted(rows or [], key=lambda x: float(x.get("start") or 0)):
        text = re.sub(r"\s+", " ", str(r.get("text") or "")).strip()
        if text:
            lines.append((float(r.get("start") or 0), text))
    if not lines:
        return ""
    total = sum(len(t) + 9 for _, t in lines)
    if total > MAX_TEXT_CHARS:                       # thin out evenly, keep the timeline whole
        step = total / MAX_TEXT_CHARS
        keep, acc = [], 0.0
        for i, ln in enumerate(lines):
            acc += 1.0
            if acc >= step:
                keep.append(ln)
                acc -= step
        lines = keep or lines[:1]
    return "\n".join(f"[{_fmt_t(t)}] {text}" for t, text in lines)


def _frames(video_path, duration, n):
    """[(seconds, jpeg bytes)] -- a few small frames spread over the video. Any failure just gives fewer frames."""
    out = []
    if not video_path or not Path(str(video_path)).exists() or not duration or duration < 1:
        return out
    n = max(1, min(MAX_FRAMES, n))
    for i in range(n):
        t = duration * (i + 0.5) / n
        try:
            r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video_path), "-frames:v", "1",
                                "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "6", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                               capture_output=True, timeout=30)
            if r.returncode == 0 and len(r.stdout) > 500:
                out.append((t, r.stdout))
        except Exception:
            continue
    return out


def _prompt(duration, has_frames):
    kinds = "\n".join(f'  "{k}": {v[0]}' for k, v in SETTINGS.items())
    return f"""You help a dubbing studio decide how much room echo (reverb) the original recording of a video has.
Below is the transcript of the video with the time of every line{', and a few frames of the video with their times' if has_frames else ''}.
The video is {duration:.0f} seconds long. Split the WHOLE timeline into consecutive stretches, each recorded in ONE kind of place,
and name the kind of place for each stretch. Use only these kinds:
{kinds}
  "unknown": you cannot tell

Rules:
- Judge where the MICROPHONE was, not the fiction: a narrator speaking over footage, a phone/video call or a studio
  interview is "studio" whatever the footage shows.
- A new stretch only where the place really changes. Do not make a new stretch for every line.
- Say "confidence": "high" only when the transcript or a frame clearly shows the place. Otherwise "low".
- "place" is two to four words, for example "apartment kitchen" or "courtroom".
- Never invent anything the transcript and frames do not support.
Return ONLY valid JSON, a list: [{{"t0": seconds, "t1": seconds, "setting": "<kind>", "confidence": "high"|"low", "place": "..."}}]"""


def _cost(usage):
    t_in = int(usage.get("promptTokenCount", 0) or 0)
    t_out = int(usage.get("candidatesTokenCount", 0) or 0)
    t_th = int(usage.get("thoughtsTokenCount", 0) or 0)
    usd = t_in / 1e6 * PRICE_IN_PER_M + (t_out + t_th) / 1e6 * PRICE_OUT_PER_M
    return {"tokens_in": t_in, "tokens_out": t_out, "tokens_thoughts": t_th, "cost_usd": round(usd, 5)}


def _record(job_id, cost):
    """Add to the job's usage bucket in its own counters: the dub's cost shows up in the admin numbers without changing what
    the customer is charged."""
    try:
        from app_state import usage_bucket
        b = usage_bucket(job_id)
        b["scene_in"] = int(b.get("scene_in", 0)) + cost["tokens_in"]
        b["scene_out"] = int(b.get("scene_out", 0)) + cost["tokens_out"] + cost["tokens_thoughts"]
        b["scene_usd"] = round(float(b.get("scene_usd", 0.0)) + cost["cost_usd"], 5)
    except Exception:
        pass


def scenes_for_job(job_id, cache_dir, rows, api_key, video_path=None, duration=None):
    """-> {"scenes": [...], "cost_usd": float, "reused": bool, "summary": str} or None when there is nothing to go on.
    rows = [{"start": s, "text": english text}] (the transcript)."""
    try:
        if not ENABLED or not api_key:
            return None
        text = _transcript(rows)
        if len(text.split()) < 8:
            return None
        if not duration:
            duration = max(float(r.get("end") or r.get("start") or 0) for r in rows)
        duration = float(duration)
        if duration < 5:
            return None
        key = hashlib.sha1((text + f"|{duration:.0f}|v{CACHE_VERSION}").encode("utf-8")).hexdigest()[:16]
        cache = Path(cache_dir) / f"{job_id}_scenes.json"
        try:
            if cache.exists():
                c = json.loads(cache.read_text(encoding="utf-8"))
                if c.get("key") == key and isinstance(c.get("scenes"), list):
                    return {"scenes": c["scenes"], "cost_usd": 0.0, "reused": True, "summary": _summary(c["scenes"], 0.0, True)}
        except Exception:
            pass
        import gemini_service
        n_frames = 0
        frames = []
        if video_path:
            n_frames = max(2, min(MAX_FRAMES, int(duration // 150) + 2))
            frames = _frames(video_path, duration, n_frames)
        parts = [{"text": _prompt(duration, bool(frames))}, {"text": "TRANSCRIPT:\n" + text}]
        for t, jpg in frames:
            parts.append({"text": f"Frame at {_fmt_t(t)} (about {t:.0f} s):"})
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpg).decode("ascii")}})
        payload = {"contents": [{"parts": parts}],
                   "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048, "responseMimeType": "application/json",
                                        "thinkingConfig": {"thinkingBudget": 0}}}
        data, err = gemini_service.call_gemini(api_key, payload, timeout=90)
        if data is None:
            print(f"[scene] {job_id}: no answer ({str(err)[:200]})")
            return None
        cost = _cost(data.get("usageMetadata") or {})
        _record(job_id, cost)
        try:
            raw = json.loads(gemini_service._strip_code_fences(data["candidates"][0]["content"]["parts"][0]["text"]))
        except Exception as ex:
            print(f"[scene] {job_id}: could not read the answer ({type(ex).__name__})")
            return {"scenes": [], "cost_usd": cost["cost_usd"], "reused": False, "summary": f"unreadable answer, no limit (${cost['cost_usd']:.4f})"}
        scenes = _clean_scenes(raw, duration)
        try:
            cache.write_text(json.dumps({"key": key, "scenes": scenes, "cost": cost}), encoding="utf-8")
        except Exception:
            pass
        s = _summary(scenes, cost["cost_usd"], False)
        if frames:
            s += f"; {len(frames)} frames"
        return {"scenes": scenes, "cost_usd": cost["cost_usd"], "tokens_in": cost["tokens_in"],
                "tokens_out": cost["tokens_out"] + cost["tokens_thoughts"], "reused": False, "summary": s}
    except Exception as ex:
        print(f"[scene] {job_id}: skipped ({type(ex).__name__}: {ex})")
        return None


def _summary(scenes, usd, reused):
    if not scenes:
        return "no stretch identified, no limit" + ("" if reused else f" (${usd:.4f})")
    bits = []
    for s in scenes[:12]:
        nm = s["place"] or s["key"]
        bits.append(f"{_fmt_t(s['t0'])}-{_fmt_t(s['t1'])} {nm} [{s['key']}{'' if s['conf'] == 'high' else ', unsure: no limit'}]")
    more = f" +{len(scenes) - 12} more" if len(scenes) > 12 else ""
    return "; ".join(bits) + more + (" (reused, no cost)" if reused else f" (cost ${usd:.4f})")
