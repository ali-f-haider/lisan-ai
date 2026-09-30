"""Inworld AI voice cloning + text-to-speech -- the alternative voice engine
to ElevenLabs (eleven_service.py), added 2026-09-27 after Ali evaluated
several Arabic voice-cloning providers (SILMA, Resemble, Camb.ai, Munsit,
Fish Audio, Gemini...) and picked Inworld for its per-character pricing, a
confirmed Modern Standard Arabic (`ar`) Tier-1 quality rating in their own
docs, and no mandatory consent-phrase recording (unlike Gemini, which
blocks cloning a voice out of existing footage since the source speaker
can't recite a phrase on demand).

Nothing in eleven_service.py was removed or altered beyond adding the
per-speaker engine dispatch inside generate_worker/regenerate_line --
ElevenLabs stays fully wired. Which engine actually runs for NEW clones is
the admin panel's Settings tab "Voice Engine" switch (see main.py's
_active_voice_engine() / pricing_config.voice_engine) -- a voice already
cloned keeps using whichever engine created it forever (see main.py's
_voice_engines_for_ids), so flipping the switch later never breaks an
existing customer's saved voice.

API reference used (confirmed directly against Inworld's docs, Sept 2026):
  - Clone a Voice:  POST   https://api.inworld.ai/voices/v1/voices:clone
  - Delete a Voice: DELETE https://api.inworld.ai/voices/v1/voices/{voiceId}
  - List Voices:    GET    https://api.inworld.ai/voices/v1/voices
  - Synthesize:     POST   https://api.inworld.ai/tts/v1/voice
Auth header on every call: "Authorization: Basic <INWORLD_API_KEY>" -- the
key value copied from the Inworld portal is used AS-IS after "Basic ", no
extra base64 step needed (per Inworld's own docs).

Emotion/style tags: confirmed via Inworld's own "Prompting for TTS-2" docs
(Sept 2026) that steering works by putting a natural-language instruction
in square brackets at the START of the `text` string itself (NOT a
separate request field) -- e.g. "[sound sad, speak softly] <the line>" --
and ONLY on the inworld-tts-2 model (DEFAULT_MODEL_ID above);
inworld-tts-2-flash ignores these brackets and reads them aloud literally.
Also, per that same doc, multiple descriptors should be COMBINED into one
bracketed phrase rather than stacked as separate brackets the way
ElevenLabs' "[happy][softly]" style works (see eleven_service.
_emotion_tags) -- see instruction_tag() below, which maps this app's
canonical emotion vocabulary (config.CANONICAL_EMOTIONS) onto Inworld's
phrasing style and combines multiple tags into one bracket accordingly.

Important honesty note (2026-09-27, Ali asked "did you add all the possible
tags of Inworld"): there is no fixed, closed enum to fully enumerate here --
inworld-tts-2 interprets ANY reasonable free-form natural-language
instruction in brackets, not a fixed tag vocabulary the way this dict might
suggest. _INSTRUCTION_PHRASES below covers two things: (1) a phrasing for
every word in this app's own 52-word canonical vocabulary (config.
CANONICAL_EMOTIONS), and (2) INWORLD_EXTRA_TAGS -- the specific additional
non-verbal/prosody examples named in Inworld's docs and confirmed via their
support bot (laugh, sigh, clear throat, yawn, very fast, very quiet, high
pitch) that aren't part of that canonical vocabulary at all. These extras
are exposed as one-click options in the Step 2 "Style / Emotion" dropdown
ONLY when Inworld is the active engine (see app.js's window._realPricing.
voiceEngine) -- but the free-text tags field still accepts anything a user
types, same as always (sanitizeStyle in app.js now whitelists this same
extra list too, not just the canonical 48). Anything beyond these named
examples can still be typed there manually; Inworld's model will interpret
reasonable free-form phrasing even if it isn't one of the words below.

Known, deliberate limitation of this version (flagged for Ali, not
silently skipped):
  - generate_sample() (used only by the admin "Compare Voice Providers"
    tool, /api/admin/compare_voice_providers) was intentionally NOT ported
    here -- that tool is unrelated to the live dubbing pipeline this swap
    is actually about, out of scope for this change.
"""
import base64
import json
import os
import random
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

from config import OUTPUT_DIR
from user_errors import friendly_error as _friendly_error, UserError
from ffmpeg_utils import get_media_duration, run_ffmpeg
from media_paths import resolve_job_audio

API_BASE = "https://api.inworld.ai"
DEFAULT_MODEL_ID = "inworld-tts-2"
# Modern Standard Arabic -- Inworld's "Tier 1" base code, deliberately NOT
# one of the regional dialect codes (arz/afb/acw/ayl/ars/acx/aeb), since
# Ali's product targets MSA only.
DEFAULT_LANGUAGE = "ar"
# The language label put on a voice copied from a video (the videos are
# English). Inworld's cloning API describes this field as "the voice's
# language". It can be changed without a code change: set the Railway variable
# INWORLD_CLONE_LANGUAGE to en, ar or auto, then compare a short dub by ear.
CLONE_SAMPLE_LANGUAGE = (os.environ.get("INWORLD_CLONE_LANGUAGE") or "en").strip() or "en"

# Voice Localization (Inworld): makes a voice cloned from English speech speak
# another language like a native speaker instead of with the English accent.
# OFF unless the Railway variable INWORLD_LOCALIZE is set to 1. Used by Dub
# Long Video only, for now. The target language and the number of candidates
# to ask for can be changed with INWORLD_LOCALIZE_LANGUAGE (default "ar") and
# INWORLD_LOCALIZE_CANDIDATES (default 3). When there are several candidates,
# a "chooser" listens to their previews and picks the most native-sounding one
# (see gemini_service.pick_native_candidate); without one, the first is used.
LOCALIZE_ENABLED = (os.environ.get("INWORLD_LOCALIZE") or "").strip().lower() in ("1", "true", "yes", "on")
LOCALIZE_LANGUAGE = (os.environ.get("INWORLD_LOCALIZE_LANGUAGE") or "ar").strip() or "ar"
try:
    LOCALIZE_CANDIDATES = max(1, min(5, int(os.environ.get("INWORLD_LOCALIZE_CANDIDATES") or 3)))
except ValueError:
    LOCALIZE_CANDIDATES = 3


# Maps this app's canonical emotion/style vocabulary (config.
# CANONICAL_EMOTIONS) onto Inworld's natural-language instruction-tag
# phrasing (their own examples: "[say excitedly]", "[sound sad]",
# "[whisper in a hushed style]", "[very fast]", "[very quiet]"). "neutral"
# maps to "" on purpose -- Inworld's docs show no bare "[neutral]"-style
# example, and default (untagged) delivery is already neutral, so a
# neutral-only tag is simply omitted rather than guessing at a phrasing
# for it.
_INSTRUCTION_PHRASES = {
    "neutral": "",
    "happy": "sound happy",
    "sad": "sound sad",
    "angry": "sound angry",
    "fearful": "sound afraid",
    "surprised": "sound surprised",
    "disgusted": "sound disgusted",
    "shouting": "shout",
    "whispering": "whisper",
    "screaming": "scream",
    "yelling": "yell",
    "crying": "speak while crying",
    "laughing": "laugh while speaking",
    "sarcastic": "say this sarcastically",
    "seductive": "say this in a seductive tone",
    "narrative": "say this in a storytelling narrator's tone",
    "announcer": "say this in an announcer's tone",
    "conversational": "say this in a casual, conversational tone",
    "depressed": "sound depressed",
    "anxious": "sound anxious",
    "confident": "sound confident",
    "indifferent": "sound indifferent",
    "excited": "sound excited",
    "serious": "sound serious",
    "playful": "sound playful",
    "terrified": "sound terrified",
    "relieved": "sound relieved",
    "thoughtful": "sound thoughtful",
    "mocking": "say this mockingly",
    "pleading": "say this pleadingly",
    "commanding": "say this in a commanding tone",
    "slowly": "speak slowly",
    "rushed": "speak quickly, rushed",
    "drawn out": "speak slowly, drawing out the words",
    "hesitant": "speak hesitantly",
    "stammering": "stammer while speaking",
    "softly": "speak softly and quietly",
    "booming": "speak loudly in a booming voice",
    "sorrowful": "sound sorrowful",
    "frustrated": "sound frustrated",
    "annoyed": "sound annoyed",
    "appalled": "sound appalled",
    "awe": "sound in awe",
    "regretful": "sound regretful",
    "resigned": "sound resigned",
    "curious": "sound curious",
    "deadpan": "say this in a flat, deadpan tone",
    "tired": "sound tired",
    # ---- non-verbal human sounds (2026-09-28, Ali's request) ----
    # Added to config.CANONICAL_EMOTIONS alongside the ElevenLabs-side
    # bracket-tag fix -- every canonical word needs an entry here too, or
    # Inworld requests using these would silently get no instruction at
    # all (falling through _INSTRUCTION_PHRASES.get(p, "") to "").
    "sneezing": "sneeze",
    "coughing": "cough",
    "sighing": "sigh",
    "gasping": "gasp",

    # ---- Inworld-only extras (NOT part of config.CANONICAL_EMOTIONS) ----
    # Concrete non-verbal/prosody examples named in Inworld's own docs and
    # confirmed via their support bot (2026-09-27) -- offered as extra Step 2
    # dropdown options only when Inworld is the active engine (see
    # INWORLD_EXTRA_TAGS below and app.js's INWORLD_EXTRA_TAGS/sanitizeStyle).
    # Distinct from existing canonical mappings above: "laugh" here is a
    # standalone non-verbal sound insertion (vs "laughing" -> "laugh while
    # speaking", which describes HOW a line is delivered), and likewise
    # "very fast" / "very quiet" / "high pitch" are more extreme, explicit
    # prosody knobs than the existing "rushed" / "softly" mappings.
    "laugh": "laugh",
    "sigh": "sigh",
    "clear throat": "clear your throat",
    "yawn": "yawn",
    "very fast": "speak very fast",
    "very quiet": "speak very quietly",
    "high pitch": "say this in a high pitch",
}

# The raw keys of the Inworld-only extras above, as their own list -- kept in
# sync by hand with app.js's INWORLD_EXTRA_TAGS constant (same convention
# config.CANONICAL_EMOTIONS already uses with app.js's EMOTIONS constant).
# Not imported by the frontend (plain JS, no shared build step) -- this is
# just the source of truth for what that JS list should contain.
INWORLD_EXTRA_TAGS = ["laugh", "sigh", "clear throat", "yawn", "very fast", "very quiet", "high pitch"]


def instruction_tag(emotion) -> str:
    """Turns a (possibly multi-tag) 'sad, softly' emotion string into ONE
    combined Inworld instruction tag plus a trailing space, e.g.
    '[sound sad, speak softly and quietly] ' -- ready to prepend directly
    onto seg.arabic_text. Returns '' (no tag, no trailing space) if every
    part maps to neutral or an unrecognized word, matching Inworld's own
    guidance to combine descriptors into a single bracketed phrase rather
    than stacking separate tags the way ElevenLabs does."""
    parts = [p.strip().lower() for p in str(emotion or "").split(",") if p.strip()]
    phrases = []
    for p in parts:
        phrase = _INSTRUCTION_PHRASES.get(p, "")
        if phrase and phrase not in phrases:
            phrases.append(phrase)
    if not phrases:
        return ""
    return f"[{', '.join(phrases)}] "


def _configured(api_key: str) -> bool:
    return bool((api_key or "").strip())


def _auth_headers(api_key: str) -> dict:
    return {"Authorization": f"Basic {api_key}", "Content-Type": "application/json"}


def _request(method: str, path: str, api_key: str, body: dict = None, timeout: int = 30):
    """Low-level JSON call against the Inworld API. Raises on any failure
    (network error, non-2xx status, bad JSON) -- every caller below catches
    and translates into this app's usual {"error": ...} / "ERROR: ..."
    conventions, matching eleven_service.py's error-handling style."""
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_auth_headers(api_key))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def _http_error_detail(e) -> str:
    if isinstance(e, urllib.error.HTTPError):
        try:
            return f"Inworld error {e.code}: {e.read().decode(errors='ignore')}"
        except Exception:
            return f"Inworld error {e.code}"
    return str(e)


def _cust(e, where="") -> str:
    """The customer-safe sentence for an error (the raw text goes to the log)."""
    if isinstance(e, UserError):
        return str(e)
    return _friendly_error(_http_error_detail(e), where)


def clone_voice_from_file(display_name: str, wav_path: Path, api_key: str, language_code: str = DEFAULT_LANGUAGE) -> str:
    """Uploads one reference clip (base64-encoded, per Inworld's clone API)
    and returns the new voiceId. Raises on failure."""
    with open(wav_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode("ascii")
    body = {
        "displayName": display_name[:200],
        "languageCode": language_code,
        "voiceSamples": [{"audioData": audio_b64}],
        "audioProcessingConfig": {"removeBackgroundNoise": True},
    }
    # Inworld rate-limits the clone endpoint per plan and answers over-limit
    # calls with HTTP 429 (their rate-limit docs recommend exponential
    # backoff with jitter). A multi-speaker job clones one speaker after
    # another, so the 2nd/3rd clone in a minute can hit that limit -- retry
    # a few times instead of failing the speaker outright. Only 429 is
    # retried: a 429 means the request was rejected before doing anything,
    # so a retry can never create a duplicate voice. Any other error still
    # raises immediately, exactly as before.
    data = None
    for attempt, base_wait in enumerate((20, 30, 40, None)):
        try:
            data = _request("POST", "/voices/v1/voices:clone", api_key, body, timeout=60)
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or base_wait is None:
                raise
            wait = base_wait + random.uniform(0, 5)
            print(f"[inworld-clone] 429 rate limited cloning {display_name!r}; retry {attempt + 1} in {wait:.0f}s")
            time.sleep(wait)
    voice_id = (data.get("voice") or {}).get("voiceId")
    if not voice_id:
        print(f"[inworld-clone] no voiceId in the answer: {json.dumps(_shape(data))[:300]}")
        raise UserError("We couldn't create a voice from this audio. Please use a clear recording of one person speaking, or choose a studio voice in Step 4.")
    return voice_id


def _shape(obj, depth=0):
    """A short description of a JSON answer (keys and sizes, never the audio
    data itself) for the log when the answer is not what we expected."""
    if isinstance(obj, dict):
        return {k: (_shape(v, depth + 1) if depth < 3 else "...") for k, v in list(obj.items())[:20]}
    if isinstance(obj, list):
        return [f"{len(obj)} items"] + ([_shape(obj[0], depth + 1)] if obj and depth < 3 else [])
    if isinstance(obj, str):
        return f"text({len(obj)})"
    return obj


def _pick(d, *names):
    """The first non-empty value under any of these keys."""
    if isinstance(d, dict):
        for n in names:
            v = d.get(n)
            if v:
                return v
    return None


def _preview_bytes(cand):
    """The audio preview of one candidate (a base64 WAV in a text field), or None."""
    if not isinstance(cand, dict):
        return None
    best = ""
    for k, v in cand.items():
        if isinstance(v, str) and len(v) > 2000 and len(v) > len(best):
            best = v
        elif isinstance(v, dict):                 # e.g. {"preview": {"audioContent": "..."}}
            for v2 in v.values():
                if isinstance(v2, str) and len(v2) > 2000 and len(v2) > len(best):
                    best = v2
    if not best:
        return None
    try:
        return base64.b64decode(best)
    except Exception:
        return None


def localize_voice(voice_id: str, api_key: str, target_language: str = None, candidates: int = None, chooser=None) -> dict:
    """Voice Localization in two calls: ask for localized candidates, then
    approve one. Inworld does no scoring itself (a person is meant to listen),
    so when there are several candidates `chooser(list_of_wav_bytes)` is asked
    which one sounds most native and must return (index, scores) or None; with
    no chooser, or no answer, the FIRST candidate is approved. Afterwards the
    SAME voiceId is spoken with language=<target> and sounds like a native
    speaker. Deleting the voice deletes its localization.
    Returns {"ok": True, "candidates": n, "candidate": id, "picked": i, "scores": [...]}
    or {"ok": False, "error": "..."} -- never raises."""
    if not _configured(api_key) or not voice_id:
        return {"ok": False, "error": "not configured or no voice_id"}
    lang = target_language or LOCALIZE_LANGUAGE
    n = candidates or LOCALIZE_CANDIDATES
    try:
        data = None
        for attempt, wait in enumerate((20, 40, None)):
            try:
                data = _request("POST", f"/voices/v1/voices/{voice_id}:localize", api_key,
                                {"targetLanguage": lang, "candidateCount": n}, timeout=420)
                break
            except urllib.error.HTTPError as e:
                if e.code != 429 or wait is None:
                    raise
                time.sleep(wait + random.uniform(0, 5))
        draft = _pick(data, "draftVoiceId", "draft_voice_id")
        if not draft:
            dv = _pick(data, "draftVoice", "draft")
            draft = dv if isinstance(dv, str) else _pick(dv, "voiceId", "id")
        cands = _pick(data, "candidates", "localizedCandidates", "localizations") or []
        ids = []
        for c in (cands if isinstance(cands, list) else []):
            ids.append(c if isinstance(c, str) else _pick(c, "candidateId", "id", "name"))
        if not draft or not ids or not ids[0]:
            return {"ok": False, "error": f"unexpected answer to localize: {json.dumps(_shape(data))[:600]}"}
        picked, scores, why = 0, [], ""
        if chooser and len(ids) > 1:
            previews = [_preview_bytes(c) for c in cands]
            if all(previews):
                try:
                    got = chooser(previews)
                    if got and 0 <= int(got[0]) < len(ids):
                        picked, scores = int(got[0]), list(got[1] or [])
                    else:
                        why = "the listener gave no answer, first candidate used"
                except Exception as ex:
                    why = f"the listener failed ({ex}), first candidate used"
            else:
                why = "no audio previews found in the answer, first candidate used"
        cand = ids[picked]
        ans = _request("POST", f"/voices/v1/voices/{voice_id}:approveLocalization", api_key,
                       {"draftVoiceId": draft, "candidateId": cand}, timeout=120)
        return {"ok": True, "candidates": len(ids), "candidate": str(cand), "picked": picked, "scores": scores,
                "note": why, "answer": json.dumps(_shape(ans))[:300]}
    except Exception as e:
        return {"ok": False, "error": _http_error_detail(e)[:600]}


def localize_many(voices: dict, api_key: str, chooser=None) -> dict:
    """{name: voice_id} -> {name: localize_voice result}. All voices are done at
    the same time (each takes about two minutes on Inworld's side)."""
    from concurrent.futures import ThreadPoolExecutor
    out = {}
    if not voices:
        return out
    with ThreadPoolExecutor(max_workers=len(voices)) as pool:
        futs = {name: pool.submit(localize_voice, vid, api_key, None, None, chooser) for name, vid in voices.items()}
        for name, fut in futs.items():
            try:
                out[name] = fut.result()
            except Exception as ex:
                out[name] = {"ok": False, "error": str(ex)}
    return out


def gemini_chooser(job_id):
    """A chooser (see localize_voice) that lets Gemini listen to the previews."""
    def choose(previews):
        import gemini_service
        from config import GEMINI_API_KEY
        return gemini_service.pick_native_candidate(job_id, previews, GEMINI_API_KEY)
    return choose


def synthesize(voice_id: str, text: str, api_key: str, language: str = DEFAULT_LANGUAGE, model_id: str = DEFAULT_MODEL_ID) -> bytes:
    """Given a cloned voiceId and plain text, returns generated audio bytes
    (MP3). Raises on failure -- same convention as eleven_client.
    text_to_speech.convert() in eleven_service.py, so the callers there
    (generate_worker / regenerate_line) don't need any special-case error
    handling for this branch; their existing try/except already covers it."""
    if not _configured(api_key):
        raise UserError("Voice generation is temporarily unavailable. Please try again later.")
    body = {
        "text": text,
        "voiceId": voice_id,
        "modelId": model_id,
        "language": language,
        "audioConfig": {"audioEncoding": "MP3"},
    }
    data = _request("POST", "/tts/v1/voice", api_key, body, timeout=60)
    audio_b64 = data.get("audioContent")
    if not audio_b64:
        print(f"[inworld-tts] no audio in the answer: {json.dumps(_shape(data))[:300]}")
        raise UserError("We couldn't generate this line. Please try again.")
    return base64.b64decode(audio_b64)


def clone_voices(job_id: str, segments: list, api_key: str, speakers_to_clone: list = None) -> dict:
    """Same behavior/shape as eleven_service.clone_voices: extracts the
    cleanest reference clip per speaker from the source video (up to ~20s,
    comfortably under Inworld's 30s Instant Voice Cloning sample cap), then
    clones each speaker on Inworld instead of ElevenLabs. Returns
    {"status": "success", "cloned_voices": {speaker: voiceId or "ERROR: ..."}, "warnings": [...]} --
    the exact same shape eleven_service.clone_voices returns, so main.py's
    /api/clone handler needs no special-casing beyond picking which of the
    two functions to call.

    The audio-extraction logic below is intentionally duplicated from
    eleven_service.clone_voices rather than shared/imported -- this keeps
    the two engines fully independent, so nothing here can ever change
    ElevenLabs' behavior, and vice versa."""
    if not _configured(api_key):
        return {"error": "Voice cloning is temporarily unavailable. Please try again later, or choose a studio voice in Step 4."}
    audio_path = resolve_job_audio(job_id)
    if audio_path is None:
        return {"error": "We couldn't find the audio of this video. Please upload it again."}
    source_duration = get_media_duration(audio_path)
    if source_duration <= 0:
        print(f"[inworld-clone] could not read the source audio duration: {audio_path}")
        return {"error": "We couldn't read the audio of this video. Please upload it again."}
    cloned_voices = {}
    speakers = list(set(s.speaker for s in segments if (s.text or "").strip()))
    if speakers_to_clone:
        speakers = [s for s in speakers if s in speakers_to_clone]
    for speaker in speakers:
        cut_files = []
        concat_file = None
        try:
            safe_speaker = "".join(c for c in speaker if c.isalnum()).strip() or "speaker"
            speaker_segs = [
                s for s in segments
                if s.speaker == speaker and (s.text or "").strip() and (s.end - s.start) > 0.05
            ]
            if not speaker_segs:
                cloned_voices[speaker] = f"ERROR: {speaker} doesn't have enough clear speech to copy a voice. Choose a studio voice for this speaker in Step 4."
                continue
            speaker_segs.sort(key=lambda s: s.end - s.start, reverse=True)
            total_valid_duration = 0.0
            for seg in speaker_segs:
                if total_valid_duration >= 20.0:
                    break
                margin = 0.35
                start_time = max(0.0, float(seg.start) - margin)
                end_time = min(source_duration, float(seg.end) + margin)
                dur = end_time - start_time
                if dur < 0.2:
                    continue
                cut_file = OUTPUT_DIR / f"iwclone_{job_id}_{safe_speaker}_{seg.segment_id}.wav"
                try:
                    run_ffmpeg([
                        "ffmpeg", "-y",
                        "-ss", str(start_time),
                        "-i", str(audio_path),
                        "-t", str(dur),
                        "-vn", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le",
                        str(cut_file)
                    ])
                    if not cut_file.exists() or cut_file.stat().st_size < 2000:
                        if cut_file.exists():
                            cut_file.unlink()
                        continue
                    actual_dur = get_media_duration(cut_file)
                    if actual_dur >= 0.5:
                        cut_files.append(cut_file)
                        total_valid_duration += actual_dur
                    else:
                        if cut_file.exists():
                            cut_file.unlink()
                except Exception as cut_err:
                    print(f"Warning: failed to cut Inworld clone sample for {speaker}: {cut_err}")
                    if cut_file.exists():
                        cut_file.unlink()
                    continue
            if total_valid_duration < 1.0:
                try:
                    min_start = max(0.0, min(float(s.start) for s in speaker_segs) - 0.5)
                    max_end = min(source_duration, max(float(s.end) for s in speaker_segs) + 0.5)
                    fallback_dur = max_end - min_start
                    if fallback_dur >= 1.0:
                        fallback_file = OUTPUT_DIR / f"iwclone_{job_id}_{safe_speaker}_fallback.wav"
                        run_ffmpeg([
                            "ffmpeg", "-y",
                            "-ss", str(min_start), "-i", str(audio_path), "-t", str(fallback_dur),
                            "-vn", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le",
                            str(fallback_file)
                        ])
                        if fallback_file.exists() and fallback_file.stat().st_size > 2000:
                            fallback_actual = get_media_duration(fallback_file)
                            if fallback_actual >= 1.0:
                                for cf in cut_files:
                                    if cf.exists():
                                        cf.unlink()
                                cut_files = [fallback_file]
                                total_valid_duration = fallback_actual
                            else:
                                if fallback_file.exists():
                                    fallback_file.unlink()
                except Exception as fallback_err:
                    print(f"Warning: fallback Inworld clone cut failed for {speaker}: {fallback_err}")
            if total_valid_duration < 1.0 or not cut_files:
                cloned_voices[speaker] = f"ERROR: {speaker} doesn't have enough clear speech to copy a voice. Choose a studio voice for this speaker in Step 4."
                for cf in cut_files:
                    if cf.exists():
                        cf.unlink()
                continue
            concat_file = OUTPUT_DIR / f"iwclone_{job_id}_{safe_speaker}_final.wav"
            if len(cut_files) == 1:
                run_ffmpeg(["ffmpeg", "-y", "-i", str(cut_files[0]), "-vn", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le", str(concat_file)])
            else:
                inputs, labels = [], []
                for i, cf in enumerate(cut_files):
                    inputs.extend(["-i", str(cf)])
                    labels.append(f"[{i}:a]")
                filter_complex = "".join(labels) + f"concat=n={len(cut_files)}:v=0:a=1[out]"
                run_ffmpeg(["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex, "-map", "[out]", "-vn", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le", str(concat_file)])
            if not concat_file.exists() or concat_file.stat().st_size < 2000:
                cloned_voices[speaker] = f"ERROR: {speaker} doesn't have enough clear speech to copy a voice. Choose a studio voice for this speaker in Step 4."
                continue
            final_duration = get_media_duration(concat_file)
            if final_duration < 1.0:
                padded_file = OUTPUT_DIR / f"iwclone_{job_id}_{safe_speaker}_final_padded.wav"
                pad_needed = max(0.2, 1.15 - final_duration)
                run_ffmpeg(["ffmpeg", "-y", "-i", str(concat_file), "-af", f"apad=pad_dur={pad_needed}", "-vn", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le", str(padded_file)])
                if concat_file.exists():
                    concat_file.unlink()
                concat_file = padded_file
                final_duration = get_media_duration(concat_file)
            if final_duration < 1.0:
                cloned_voices[speaker] = f"ERROR: {speaker} doesn't have enough clear speech to copy a voice. Choose a studio voice for this speaker in Step 4."
                continue
            try:
                voice_id = clone_voice_from_file(f"Cloned_{speaker}", concat_file, api_key, language_code=CLONE_SAMPLE_LANGUAGE)
                cloned_voices[speaker] = voice_id
                # Keep the reference sample, same as eleven_service.
                # clone_voices -- lets the user download it via
                # /api/download_voice_sample; Inworld (like ElevenLabs)
                # doesn't offer exporting the cloned voice model itself.
                try:
                    sample_path = OUTPUT_DIR / f"voice_sample_{job_id}_{safe_speaker}.wav"
                    if concat_file != sample_path:
                        concat_file.replace(sample_path)
                    concat_file = None
                except Exception as _sample_err:
                    # Was a silent `pass` -- caught 2026-09-28 after Ali cloned
                    # "abu safwan"/"safwan" successfully but got a 404 "voice
                    # sample no longer available" on Download. With this
                    # swallowed silently, there was no way to tell whether the
                    # rename genuinely failed here or something unrelated (e.g.
                    # Railway's ephemeral filesystem losing the file between
                    # requests) deleted it afterward. Logging it doesn't fix the
                    # underlying cause by itself, but the next occurrence will
                    # show up in Railway's logs instead of vanishing silently.
                    print(f"[inworld-clone] WARNING: could not save downloadable reference sample for {speaker} (job {job_id}): {_sample_err}")
            except Exception as e:
                cloned_voices[speaker] = "ERROR: " + _cust(e, "clone")
        except Exception as e:
            cloned_voices[speaker] = "ERROR: " + _cust(e, "clone")
        finally:
            for cf in cut_files:
                if cf.exists():
                    try:
                        cf.unlink()
                    except Exception:
                        pass
            if concat_file is not None and concat_file.exists():
                try:
                    concat_file.unlink()
                except Exception:
                    pass
    # Optional (INWORLD_LOCALIZE=1): give every new voice a native Arabic accent.
    # A voice that cannot be localized keeps working as the plain copy.
    loc_warnings = []
    if LOCALIZE_ENABLED:
        fresh = {sp: v for sp, v in cloned_voices.items() if not str(v).startswith("ERROR")}
        if fresh:
            results = localize_many(fresh, api_key, gemini_chooser(job_id))
            for sp, res in results.items():
                if res.get("ok"):
                    print(f"[inworld-localize] {job_id} {sp}: candidate {res.get('picked', 0) + 1} of {res.get('candidates')} approved, scores {res.get('scores')} {res.get('note') or ''}")
                else:
                    print(f"[inworld-localize] {job_id} {sp} failed: {res.get('error')}")
                    loc_warnings.append(f"{sp}: the natural Arabic accent could not be added, so the standard cloned voice is used.")
    warnings = list(loc_warnings)
    for speaker in cloned_voices:
        if not str(cloned_voices[speaker]).startswith("ERROR"):
            speaker_segs = [s for s in segments if s.speaker == speaker]
            total_available = sum(max(0, s.end - s.start) for s in speaker_segs)
            if total_available < 3.0:
                warnings.append(f"{speaker}: Only {total_available:.1f}s available. Clone quality will likely be poor.")
            elif total_available < 10.0:
                warnings.append(f"{speaker}: Only {total_available:.1f}s available. Clone quality may be reduced.")
    return {"status": "success", "cloned_voices": cloned_voices, "warnings": warnings}


def add_custom_voice(job_id: str, speaker: str, src_path, api_key: str):
    """Same shape as eleven_service.add_custom_voice: returns a voice_id
    string on success, or an "ERROR: ..." string on failure -- never raises,
    matching the existing convention main.py's /api/upload_custom_voice
    already handles."""
    if not _configured(api_key):
        return "ERROR: Voice cloning is temporarily unavailable. Please try again later."
    safe = "".join(c for c in speaker if c.isalnum()).strip() or "spk"
    wav = OUTPUT_DIR / f"iwcustom_{job_id}_{safe}.wav"
    run_ffmpeg(["ffmpeg", "-y", "-i", str(src_path), "-vn", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le", str(wav)])
    dur = get_media_duration(wav)
    if dur > 20.5:
        try:
            wav.unlink()
        except Exception:
            pass
        return f"ERROR: This clip is {dur:.1f} seconds long. The maximum is 20 seconds. Please use a shorter recording."
    if dur < 1.0:
        pad = OUTPUT_DIR / f"iwcustom_{job_id}_{safe}_pad.wav"
        run_ffmpeg(["ffmpeg", "-y", "-i", str(wav), "-af", f"apad=pad_dur={max(0.2, 1.15 - dur)}", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le", str(pad)])
        try:
            wav.unlink()
        except Exception:
            pass
        wav = pad
    try:
        voice_id = clone_voice_from_file(f"Custom_{speaker}", wav, api_key)
        return voice_id
    except Exception as e:
        return "ERROR: " + _cust(e, "custom voice")
    finally:
        try:
            wav.unlink()
        except Exception:
            pass


def delete_voice(voice_id: str, api_key: str) -> dict:
    """Deletes exactly ONE voice by id -- same shape as
    eleven_service.delete_voice."""
    if not _configured(api_key) or not voice_id:
        return {"ok": False, "error": "This voice could not be removed."}
    try:
        _request("DELETE", f"/voices/v1/voices/{voice_id}", api_key, timeout=30)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": _cust(e, "delete voice")}


def get_voice_slot_usage(api_key: str) -> dict:
    """How many CUSTOM voices sit in the Inworld workspace right now -- the
    number that counts against the plan's "Custom Voices (storage slots)"
    limit (100 on On-Demand, per Inworld's pricing page). Inworld has no
    quota/usage endpoint at all (their docs list none), so this is counted
    from List Voices (GET /voices/v1/voices, paged via pageToken).

    What counts: voices whose `source` is IVC (instant clone), TVD (text-
    designed) or PVC (professional clone). System catalog voices (SYSTEM)
    are excluded. A voice with no usable `source` (missing or OTHER) only
    counts if it carries this app's own Cloned_/Custom_ display-name
    prefix. Returns {"custom_voices": n, "by_source": {...}} or
    {"error": "..."} -- never raises."""
    if not _configured(api_key):
        return {"error": "INWORLD_API_KEY not set"}
    try:
        total = 0
        by_source = {}
        token = None
        for _page in range(25):  # hard stop: 25 pages x 200 voices
            path = "/voices/v1/voices?pageSize=200"
            if token:
                path += "&pageToken=" + urllib.parse.quote(str(token), safe="")
            data = _request("GET", path, api_key, timeout=30)
            for v in (data.get("voices") or []):
                src = str(v.get("source") or "").upper()
                name = v.get("displayName") or ""
                if src in ("IVC", "TVD", "PVC"):
                    pass
                elif src == "SYSTEM":
                    continue
                elif name.startswith(("Cloned_", "Custom_")):
                    src = src or "UNKNOWN"
                else:
                    continue
                by_source[src] = by_source.get(src, 0) + 1
                total += 1
            token = data.get("nextPageToken")
            if not token:
                break
        return {"custom_voices": total, "by_source": by_source}
    except Exception as e:
        return {"error": _http_error_detail(e)}


def cleanup_cloned_voices(api_key: str, keep_ids: list = None) -> dict:
    """Sweeps every Inworld voice this app created (displayName starting
    with Cloned_/Custom_) that ISN'T in keep_ids -- same "sweep account-
    wide, protect saved voices" behavior as eleven_service.
    cleanup_cloned_voices. No-ops cleanly (returns a harmless empty result
    instead of an error) if Inworld isn't configured yet -- e.g. Ali hasn't
    set INWORLD_API_KEY, or has never switched the admin panel to Inworld
    -- so /api/cleanup_voices calling this unconditionally alongside the
    ElevenLabs sweep is always safe."""
    if not _configured(api_key):
        return {"deleted": 0, "errors": []}
    keep = set(keep_ids or [])
    try:
        data = _request("GET", "/voices/v1/voices?pageSize=200", api_key, timeout=30)
        deleted, errors = 0, []
        for v in data.get("voices", []):
            name = v.get("displayName") or ""
            vid = v.get("voiceId")
            if not name.startswith(("Cloned_", "Custom_")) or vid in keep:
                continue
            try:
                _request("DELETE", f"/voices/v1/voices/{vid}", api_key, timeout=30)
                deleted += 1
            except Exception as e:
                errors.append(f"{name}: {_http_error_detail(e)}")
        return {"deleted": deleted, "errors": errors}
    except Exception as e:
        return {"deleted": 0, "errors": [_http_error_detail(e)]}
