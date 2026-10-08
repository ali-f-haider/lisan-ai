"""Choose library voices that fit a speaker.

Gender is a hard rule. Inside the right gender a voice is scored on how close it
is to the speaker in age band, pitch and tone, and the best unused voice wins.
Everything here is pure computation (numpy only): the speaker's pitch is
measured from the audio, and the voice list comes from the caller, so nothing
is paid for and the same input always gives the same answer.

Age is never guessed from the sound here: it comes from the person's own choice
or from the listening step (see gemini_service.describe_speaker_voice). With no
age known, age simply does not count, it is never invented.
"""
import json
import math
import os
import threading
import time
from pathlib import Path

import numpy as np

SR = 16000
AGE_BANDS = ("child", "young", "adult", "senior")
# Words the voice provider and the listener use, mapped to one scale.
_AGE_WORDS = {
    "child": "child", "kid": "child", "boy": "child", "girl": "child",
    "young": "young", "youth": "young", "teen": "young", "young_adult": "young",
    "adult": "adult", "middle_aged": "adult", "mature": "adult",
    "old": "senior", "older": "senior", "elderly": "senior", "senior": "senior", "aged": "senior",
}
# Typical speaking pitch (Hz) when a voice has no measurement yet.
_TYPICAL_F0 = {"male": 120.0, "female": 210.0}
TONE_WORDS = ("warm", "deep", "bright", "soft", "calm", "energetic", "authoritative", "raspy", "smooth", "breathy", "gentle", "serious")

# Score weights (a perfect match is 0 penalty; higher penalty = worse fit).
AGE_STEP_PENALTY = 3.0          # per band of difference (child..senior is 3 steps)
AGE_UNKNOWN_VOICE_PENALTY = 1.0  # the voice does not say how old it sounds
PITCH_PENALTY_PER_SEMITONE = 0.45
PITCH_PENALTY_CAP = 5.0
TONE_BONUS = 0.6                # per shared tone word (max 2)
WEAK_PENALTY = 4.0              # above this the best voice is only a rough fit


def age_band(value):
    """One of AGE_BANDS, or None when the text says nothing usable."""
    if not value:
        return None
    return _AGE_WORDS.get(str(value).strip().lower().replace("-", "_").replace(" ", "_"))


def tone_words(value):
    """The known tone words found in a text or list."""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value)
    text = str(value or "").lower()
    return [w for w in TONE_WORDS if w in text]


# ----------------------------------------------------------------- measuring

def read_wav_mono(path):
    """(float32 samples in -1..1, sample rate) from a 16-bit PCM wav."""
    import wave
    with wave.open(str(path), "rb") as w:
        n, ch, width, sr = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(n)
    if width != 2:
        raise ValueError("expected 16-bit audio")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def acoustic_profile(x, sr=SR):
    """Pitch and brightness of one voice. Returns {} when there is too little voiced speech.

    f0_median / f0_low / f0_high in Hz, voiced_seconds, brightness_hz (spectral
    centroid of the voiced frames), clarity (0..1, how periodic the voice is)."""
    x = np.asarray(x, dtype=np.float32)
    if x.size < sr // 2:
        return {}
    frame, hop = int(0.040 * sr), int(0.020 * sr)
    n = 1 + (x.size - frame) // hop
    if n < 10:
        return {}
    idx = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx]
    fr = fr - fr.mean(axis=1, keepdims=True)
    fr = fr * np.hanning(frame)[None, :]
    rms = np.sqrt((fr ** 2).mean(axis=1) + 1e-12)
    loud = rms > max(0.15 * np.percentile(rms, 95), 1e-4)
    spec = np.fft.rfft(fr, 2 * frame, axis=1)
    ac = np.fft.irfft(spec * np.conj(spec), axis=1)[:, :frame]
    ac = ac / (ac[:, :1] + 1e-12)
    lo, hi = int(sr / 400.0), int(sr / 60.0)
    seg = ac[:, lo:hi]
    peak = seg.max(axis=1)
    voiced = loud & (peak > 0.45)
    if voiced.sum() < 8:
        return {}
    # Take the shortest period that is nearly as strong as the strongest one (avoids octave errors).
    f0 = np.zeros(n)
    for i in np.nonzero(voiced)[0]:
        row = seg[i]
        cand = np.nonzero((row >= 0.9 * row.max()) & (row > 0.4))[0]
        # local maxima only
        cand = [c for c in cand if (c == 0 or row[c] >= row[c - 1]) and (c == len(row) - 1 or row[c] >= row[c + 1])]
        if cand:
            lag = lo + cand[0]
            f0[i] = sr / lag
    f = f0[f0 > 0]
    if f.size < 8:
        return {}
    med = float(np.median(f))
    f = f[(f > med / 1.6) & (f < med * 1.6)]          # drop the occasional octave jump
    if f.size < 8:
        return {}
    mag = np.abs(np.fft.rfft(fr[voiced], axis=1))
    freqs = np.fft.rfftfreq(frame, 1.0 / sr)
    centroid = float(((mag * freqs[None, :]).sum(axis=1) / (mag.sum(axis=1) + 1e-9)).mean())
    return {"f0_median": round(float(np.median(f)), 1), "f0_low": round(float(np.percentile(f, 10)), 1),
            "f0_high": round(float(np.percentile(f, 90)), 1), "voiced_seconds": round(float(voiced.sum() * hop / sr), 2),
            "brightness_hz": round(centroid, 0), "clarity": round(float(peak[voiced].mean()), 3)}


def guess_gender(f0_median):
    """'male' / 'female' from the average pitch, or None in the overlap where it is not safe to say."""
    if not f0_median:
        return None
    if f0_median < 150:
        return "male"
    if f0_median > 185:
        return "female"
    return None


# ------------------------------------------------------------------- scoring

def _semitones(a, b):
    return abs(12.0 * math.log2(max(a, 1.0) / max(b, 1.0)))


def voice_profile(voice, measured=None):
    """What we know about one library voice (labels from the provider + measured pitch)."""
    measured = measured or {}
    gender = str(voice.get("gender") or "").strip().lower()
    return {
        "voice_id": voice.get("voice_id"),
        "gender": gender if gender in ("male", "female") else None,
        "age": age_band(voice.get("age")),
        "f0": measured.get("f0_median"),
        "tones": tone_words([voice.get("descriptive"), voice.get("use_case"), voice.get("description")]),
    }


def score_voice(speaker, voice):
    """(penalty, reasons) of one voice for one speaker; None when the gender rules it out.

    speaker: {gender, age, f0, tones}. Lower penalty is better."""
    sg, vg = speaker.get("gender"), voice.get("gender")
    if sg in ("male", "female") and vg in ("male", "female") and sg != vg:
        return None
    penalty, reasons = 0.0, []
    sa, va = speaker.get("age"), voice.get("age")
    if sa in AGE_BANDS:
        if va in AGE_BANDS:
            steps = abs(AGE_BANDS.index(sa) - AGE_BANDS.index(va))
            penalty += AGE_STEP_PENALTY * steps
            reasons.append("age" if steps == 0 else "age_off")
        else:
            penalty += AGE_UNKNOWN_VOICE_PENALTY
    s_f0 = speaker.get("f0")
    if s_f0:
        v_f0 = voice.get("f0") or _TYPICAL_F0.get(vg or sg)
        if v_f0:
            st = _semitones(s_f0, v_f0)
            penalty += min(PITCH_PENALTY_CAP, PITCH_PENALTY_PER_SEMITONE * st)
            if st <= 2.5 and voice.get("f0"):
                reasons.append("pitch")
    shared = set(speaker.get("tones") or []) & set(voice.get("tones") or [])
    if shared:
        penalty -= TONE_BONUS * min(2, len(shared))
        reasons.append("tone")
    return penalty, reasons


def rank_voices(speaker, voices, taken=()):
    """All usable voices for the speaker, best first: [{voice_id, penalty, reasons}]. Ties go to the lower voice_id."""
    out = []
    for v in voices:
        if not v.get("voice_id") or v["voice_id"] in taken:
            continue
        s = score_voice(speaker, v)
        if s is not None:
            out.append({"voice_id": v["voice_id"], "penalty": round(s[0], 3), "reasons": s[1]})
    out.sort(key=lambda r: (r["penalty"], str(r["voice_id"])))
    return out


def assign(speakers, voices, taken=(), top=3):
    """Give each speaker the best unused voice.

    speakers: [{key, seconds, gender, age, f0, tones}] (key = the speaker's name).
    Speakers who talk most choose first, so the main voices get the best fits.
    Returns {key: {"best": voice_id or None, "options": [...top ranked...], "fit": "good"|"rough"|"none"}}."""
    used = set(taken)
    result = {}
    for sp in sorted(speakers, key=lambda s: (-float(s.get("seconds") or 0), str(s.get("key")))):
        ranked = rank_voices(sp, voices, used)
        if not ranked:
            # every fitting voice is already in use: allow reuse rather than leave the speaker without one
            ranked = rank_voices(sp, voices, ())
        if not ranked:
            result[sp["key"]] = {"best": None, "options": [], "fit": "none"}
            continue
        best = ranked[0]
        used.add(best["voice_id"])
        result[sp["key"]] = {"best": best["voice_id"], "options": ranked[:top],
                             "fit": "good" if best["penalty"] <= WEAK_PENALTY else "rough"}
    return result


def speaker_traits(profile=None, ai=None, chosen_gender=None, chosen_age=None):
    """Combine everything known about a speaker into the dict score_voice expects.

    The person's own choice always wins; then the listener; then the pitch (gender only)."""
    profile, ai = profile or {}, ai or {}
    gender = chosen_gender if chosen_gender in ("male", "female") else None
    source = "chosen" if gender else None
    if not gender and ai.get("gender") in ("male", "female") and not ai.get("uncertain"):
        gender, source = ai["gender"], "heard"
    if not gender:
        gender = guess_gender(profile.get("f0_median"))
        source = "pitch" if gender else None
    age, age_source = age_band(chosen_age), "chosen"
    if not age and not ai.get("uncertain"):
        age, age_source = age_band(ai.get("age")), "heard"
    return {"gender": gender, "gender_source": source, "age": age, "age_source": age_source if age else None,
            "f0": profile.get("f0_median"), "tones": tone_words(ai.get("tone")) if ai else []}


# ------------------------------------------------------- measured-voice cache

_CACHE_LOCK = threading.Lock()


def _cache_path():
    base = os.environ.get("DATA_DIR")
    if not base:
        try:
            from config import DATA_DIR
            base = DATA_DIR
        except Exception:
            base = "."
    return Path(base) / "voice_catalog.json"


def load_measurements():
    with _CACHE_LOCK:
        try:
            data = json.loads(_cache_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def save_measurement(voice_id, preview_url, profile):
    with _CACHE_LOCK:
        try:
            data = json.loads(_cache_path().read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data[voice_id] = dict(profile, preview_url=preview_url, at=int(time.time()))
        path = _cache_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)


def measure_missing(voices, fetch_wav, budget_seconds=8.0, workers=4):
    """Measure the pitch of voices whose preview has not been measured yet.

    fetch_wav(url) -> path of a 16 kHz mono wav (or raises). Stops when the time
    budget is used; the rest is done on the next call, so the first request stays quick."""
    from concurrent.futures import ThreadPoolExecutor, wait
    known = load_measurements()
    todo = [v for v in voices if v.get("voice_id") and v.get("preview_url") and not str(v["preview_url"]).startswith("/")
            and known.get(v["voice_id"], {}).get("preview_url") != v["preview_url"]]
    if not todo:
        return known

    def one(v):
        try:
            path = fetch_wav(v["preview_url"])
            x, sr = read_wav_mono(path)
            prof = acoustic_profile(x, sr)
            save_measurement(v["voice_id"], v["preview_url"], prof or {"f0_median": None})
            try:
                Path(path).unlink()
            except Exception:
                pass
        except Exception:
            pass

    pool = ThreadPoolExecutor(max_workers=workers)
    futures = [pool.submit(one, v) for v in todo]
    wait(futures, timeout=budget_seconds)
    pool.shutdown(wait=False, cancel_futures=True)
    return load_measurements()
