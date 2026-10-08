"""The server side of "Auto-Assign": measure each speaker, listen once, and pick the closest library voices.

Everything outside the computation is injected, so it can be tested without audio
tools or paid services:
    cut(src, start, dur, out_path)   cut one piece of the job audio (16 kHz mono)
    describe(job_id, mp3_path)       one listening answer for one speaker (may raise)
    fetch_wav(url)                   a library voice's preview as a 16 kHz wav path
"""
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import numpy as np

import voice_match as vm

SR = vm.SR
PITCH_SECONDS = 30.0        # speech used to measure the pitch
LISTEN_SECONDS = 15.0       # speech sent to the listener
MIN_PIECE = 0.4
MAX_LISTENED_SPEAKERS = 6   # the rest are matched on pitch and the person's choices only
_VOICES_TTL = 600.0
_voices_cache = {"at": 0.0, "voices": None, "key": None}
_voices_lock = threading.Lock()


def library_voices(fetch_voices):
    """The account's voices (cached ten minutes, per voice engine: a `cache_key` function on the loader names it). [] when the list cannot be loaded."""
    try:
        key = fetch_voices.cache_key()
    except Exception:
        key = None
    with _voices_lock:
        if _voices_cache["voices"] is not None and _voices_cache.get("key") == key and time.monotonic() - _voices_cache["at"] < _VOICES_TTL:
            return _voices_cache["voices"]
    try:
        got = fetch_voices()
        voices = got.get("voices") if isinstance(got, dict) else None
    except Exception:
        voices = None
    if not voices:
        return []
    with _voices_lock:
        _voices_cache.update(at=time.monotonic(), voices=voices, key=key)
    return voices


def reset_cache():
    with _voices_lock:
        _voices_cache.update(at=0.0, voices=None, key=None)


def _speaker_audio(src, pieces, cut, work, seconds):
    """Up to `seconds` of the speaker's longest lines joined into one array (and how many seconds were found)."""
    parts, got = [], 0.0
    for a, b in sorted(pieces, key=lambda p: p[0] - p[1]):      # longest first
        if got >= seconds:
            break
        d = min(b - a, seconds - got)
        if d < MIN_PIECE:
            continue
        out = work / f"{uuid.uuid4().hex}.wav"
        try:
            cut(src, a, d, out)
            x, sr = vm.read_wav_mono(out)
            parts.append(x)
            got += len(x) / float(sr)
        except Exception:
            pass
        finally:
            try:
                out.unlink()
            except Exception:
                pass
    return (np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)), got


def _write_wav(path, x):
    import wave
    data = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(data.tobytes())


def match(job_id, src, speakers, segments, voices, *, cut, describe=None, fetch_wav=None, work=None, taken=()):
    """Pick voices for every speaker.

    speakers: [{name, gender (the person's choice or ""), age (the person's choice or "")}]
    segments: [{speaker, start, end}] of the job
    voices:   the library list [{voice_id, gender, age, descriptive, use_case, preview_url}]
    Returns {"speakers": {name: {...}}, "listener": "used"|"unavailable"|"off", "measured_voices": n}.
    """
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    measured = {}
    if fetch_wav and voices:
        try:
            measured = vm.measure_missing(voices, fetch_wav)
        except Exception:
            measured = vm.load_measurements()
    else:
        measured = vm.load_measurements()
    profiles = [vm.voice_profile(v, measured.get(v.get("voice_id"))) for v in voices]

    listener_state = "off" if describe is None else "used"
    rows, asked = [], 0
    for sp in speakers:
        name = sp["name"]
        pieces = [(float(s["start"]), float(s["end"])) for s in segments if s.get("speaker") == name]
        seconds = sum(b - a for a, b in pieces)
        x, got = _speaker_audio(src, pieces, cut, work, PITCH_SECONDS)
        prof = vm.acoustic_profile(x, SR) if got >= 1.0 else {}
        ai = None
        if describe is not None and got >= 2.0 and asked < MAX_LISTENED_SPEAKERS:
            asked += 1
            mp3 = work / f"{uuid.uuid4().hex}.mp3"
            wav = work / f"{uuid.uuid4().hex}.wav"
            try:
                _write_wav(wav, x[: int(LISTEN_SECONDS * SR)])
                cut(wav, 0, LISTEN_SECONDS, mp3)
                ai = describe(job_id, mp3)
            except Exception:
                ai = None
                listener_state = "unavailable"
            finally:
                for p in (wav, mp3):
                    try:
                        p.unlink()
                    except Exception:
                        pass
        traits = vm.speaker_traits(prof, ai, chosen_gender=sp.get("gender"), chosen_age=sp.get("age"))
        rows.append({"key": name, "seconds": seconds, "gender": traits["gender"], "age": traits["age"],
                     "f0": traits["f0"], "tones": traits["tones"], "_traits": traits, "_ai": ai, "_prof": prof})
    # a speaker whose gender nobody could tell is matched on pitch and age across both genders
    result = vm.assign(rows, profiles, taken=taken)
    out = {}
    for r in rows:
        res = result[r["key"]]
        t = r["_traits"]
        out[r["key"]] = {
            "gender": t["gender"], "gender_source": t["gender_source"],
            "age": t["age"], "age_source": t["age_source"],
            "tone": t["tones"], "pitch_hz": t["f0"],
            "best": res["best"], "options": res["options"], "fit": res["fit"],
        }
    return {"speakers": out, "listener": listener_state, "measured_voices": len([1 for v in voices if v.get("voice_id") in measured])}


def make_wav_fetcher(work, convert, max_bytes=2_000_000):
    """fetch_wav(url) for library previews: https only, size-limited, converted with `convert(src, dst)`."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)

    def fetch(url):
        if urllib.parse.urlparse(url).scheme != "https":
            raise ValueError("preview must be https")
        raw = work / f"{uuid.uuid4().hex}.mp3"
        wav = work / f"{uuid.uuid4().hex}.wav"
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                data = r.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("preview too large")
            raw.write_bytes(data)
            convert(raw, wav)
            return wav
        finally:
            try:
                raw.unlink()
            except Exception:
                pass
    return fetch
