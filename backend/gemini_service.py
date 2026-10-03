import base64
import json
import re
import time
import urllib.request
import urllib.error
from pathlib import Path

from config import (
    GEMINI_MODELS,
    CANONICAL_EMOTIONS,
    EMOTION_SYNONYMS,
    OUTPUT_DIR,
)
from app_state import jobs_progress, usage_bucket, record_gemini
from ffmpeg_utils import cut_audio_segment
from user_errors import friendly_error
from resource_meter import metered as _metered


def normalize_emotion(value):
    """Map any Gemini emotion wording onto our canonical tag list."""
    if not value:
        return "neutral"
    text = str(value).lower().strip().strip('.!?,')
    if text in CANONICAL_EMOTIONS:
        return text
    if text in EMOTION_SYNONYMS:
        return EMOTION_SYNONYMS[text]
    for emotion in CANONICAL_EMOTIONS:
        if emotion in text:
            return emotion
    for key, mapped in EMOTION_SYNONYMS.items():
        if key in text:
            return mapped
    return "neutral"


def normalize_emotions(value, min_tags: int = 2, max_tags: int = 3) -> str:
    """Map Gemini's (possibly multi-tag) style wording onto our canonical list,
    keeping MULTIPLE tags instead of collapsing everything to a single word.
    Returns a comma-separated string of canonical tags, e.g. "happy, softly".
    """
    if not value:
        return "neutral"
    raw_parts = re.split(r"[,+/;]| and ", str(value).lower())
    tags = []
    for part in raw_parts:
        part = part.strip().strip(".!?")
        if not part:
            continue
        tag = normalize_emotion(part)
        if tag not in tags:
            tags.append(tag)
        if len(tags) >= max_tags:
            break
    if not tags:
        return "neutral"
    # Drop a redundant standalone "neutral" once we already have a real tag
    if len(tags) > 1 and "neutral" in tags:
        tags = [t for t in tags if t != "neutral"] or ["neutral"]
    # If Gemini only gave us one usable tag, don't invent a second one —
    # min_tags is enforced via the prompt, not by padding here.
    return ", ".join(tags[:max_tags])


def call_gemini(api_key: str, payload: dict, timeout: int = 120):
    """Call Gemini, falling back across models. Returns (data, None) or (None, error)."""
    last_error = None
    payload_bytes = json.dumps(payload).encode("utf-8")
    for model_name in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        for attempt in range(3):
            request = urllib.request.Request(url, data=payload_bytes, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.load(response), None
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode(errors="ignore")
                except Exception:
                    body = str(e)
                last_error = f"[{model_name}] HTTP {e.code}: {body}"
                if e.code in [429, 503] and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                break
            except Exception as e:
                last_error = f"[{model_name}] {str(e)}"
                if attempt < 2:
                    time.sleep(3)
                    continue
                break
    return None, last_error


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _public_errors(errors):
    """One plain sentence instead of one raw provider message per failed line."""
    n = len(errors or [])
    if not n:
        return []
    return [f"We couldn't detect the emotion for {n} line{'s' if n != 1 else ''}, so {'they were' if n != 1 else 'it was'} set to Neutral. You can change {'them' if n != 1 else 'it'} manually."]


def translate_segments(job_id: str, segments: list, api_key: str) -> dict:
    """Translate all segments to Arabic (MSA + Tashkeel) and detect emotions."""
    if not api_key:
        return {"error": "Translation is temporarily unavailable. Please try again later."}
    if not segments:
        return {"error": "There are no lines to translate yet."}

    segments_for_prompt = []
    for seg in segments:
        segments_for_prompt.append({
            "segment_id": seg.segment_id,
            "start": seg.start,
            "end": seg.end,
            "duration": round(seg.end - seg.start, 2),
            "english_text": seg.text,
            "speaker": seg.speaker,
        })

    prompt = f"""You are a professional Arabic translator and voice dubbing specialist.
Translate the following English audio segments into Modern Standard Arabic (MSA).
CRITICAL RULES FOR TIMING:
Arabic takes about 20% longer to speak than English.
You MUST keep the translation very short for short segments.
Rule of thumb: Maximum 2 to 2.5 words per second of duration.
Example: If duration is 1.5 seconds, use maximum 3 words. If 2 seconds, max 4-5 words.
Do not add filler words. Be extremely concise to fit the time limit.
OTHER RULES:
Translate into clear, natural MSA Arabic suitable for voice dubbing.
Add full Tashkeel (Arabic diacritics) to every word.
Detect the emotion AND speaking style of each line. You MUST return exactly TWO comma-separated tags per line (never just one) — a primary emotion tag plus a secondary delivery tag (pacing, volume, or manner) that together best describe how the line should be performed. Choose both tags ONLY from this exact list:
{', '.join(CANONICAL_EMOTIONS)}
Example: a sad line spoken quietly would be "sad, softly". An urgent, angry line would be "angry, rushed".
Preserve the core meaning, but prioritize fitting the time limit.
Return ONLY valid JSON. No explanations.
Return JSON array:
[
{{"segment_id": "...", "arabic_text": "Arabic text with Tashkeel", "emotion": "neutral, conversational"}}
]
Segments:
{json.dumps(segments_for_prompt, ensure_ascii=False)}"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
        },
    }

    data, err = call_gemini(api_key, payload, timeout=120)
    record_gemini(job_id, data)
    if data is None:
        return {"error": friendly_error(err, "translate")}

    try:
        result_text = _strip_code_fences(data["candidates"][0]["content"]["parts"][0]["text"])
        translated_segments = json.loads(result_text)
    except Exception as ex:
        print(f"[translate] {job_id}: could not read the translation answer: {type(ex).__name__}: {ex}")
        return {"error": "We couldn't translate this text. Please try again, or translate fewer lines at a time."}

    for item in translated_segments:
        if isinstance(item, dict):
            item["emotion"] = normalize_emotions(item.get("emotion", ""))

    return {"status": "success", "translated_segments": translated_segments}


def add_tashkeel_lines(job_id: str, items: list, api_key: str):
    """Adds Arabic tashkeel (diacritics) to the lines that need it.
    items: [{"segment_id": ..., "arabic_text": ...}]. Returns
    {segment_id: text_with_tashkeel}, or None when the AI service did not
    answer. The caller checks the result word by word (see longdub_service)."""
    if not api_key or not items:
        return None
    prompt = (
        "You are an Arabic diacritization (tashkeel) engine.\n"
        "Add full, correct Arabic tashkeel (harakat) to every Arabic word in each text below that has none.\n"
        "STRICT RULES:\n"
        "- Do NOT translate.\n"
        "- Do NOT change, add, remove, or reorder any words or letters.\n"
        "- Words that already carry tashkeel must stay exactly as they are.\n"
        "- Keep punctuation and spacing exactly as is.\n"
        '- Return ONLY a valid JSON array: [{"segment_id": "...", "arabic_text": "..."}]\n'
        "Texts:\n" + json.dumps(items, ensure_ascii=False, indent=1)
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192, "responseMimeType": "application/json"},
    }
    data, err = call_gemini(api_key, payload, timeout=120)
    record_gemini(job_id, data)
    if data is None:
        print(f"[gemini] tashkeel failed on all models: {err}")
        return None
    try:
        arr = json.loads(_strip_code_fences(data["candidates"][0]["content"]["parts"][0]["text"]))
    except Exception as ex:
        print(f"[gemini] tashkeel answer was not valid JSON: {ex}")
        return None
    out = {}
    for it in arr if isinstance(arr, list) else []:
        if isinstance(it, dict) and it.get("segment_id") is not None and isinstance(it.get("arabic_text"), str):
            out[str(it["segment_id"])] = it["arabic_text"]
    return out


def shorten_arabic_line(job_id: str, english: str, arabic: str, max_letters: int, api_key: str):
    """Rewrites ONE Arabic dubbing line shorter (same meaning, tone and register)
    so the spoken line fits its time. `max_letters` counts Arabic letters only
    (not tashkeel marks, not spaces). Returns the new text, or None when the AI
    service did not answer or gave nothing usable. The caller checks that it
    really is shorter."""
    if not api_key or not (arabic or "").strip():
        return None
    prompt = (
        "You are an Arabic dubbing script editor.\n"
        "The Arabic line below is spoken in a dubbed video, but spoken aloud it is TOO LONG for the time it has.\n"
        "Rewrite it SHORTER so that it can be spoken in less time.\n"
        "STRICT RULES:\n"
        f"- At most {int(max_letters)} Arabic letters in total (do not count tashkeel marks or spaces).\n"
        "- Keep the same meaning, the same tone and the same dialect/register as the original. Natural spoken Arabic.\n"
        "- Do NOT add any new information. Drop the least important words or use a shorter way to say the same thing.\n"
        "- Keep full Arabic tashkeel (harakat) on every word, like the original.\n"
        "- Keep names, numbers and the sentence type (question / statement) as they are.\n"
        '- Return ONLY valid JSON: {"arabic_text": "..."}\n\n'
        f"English original (for meaning only): {json.dumps(english or '', ensure_ascii=False)}\n"
        f"Arabic line to shorten: {json.dumps(arabic, ensure_ascii=False)}\n"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 8192, "responseMimeType": "application/json"},
    }
    data, err = call_gemini(api_key, payload, timeout=90)
    record_gemini(job_id, data)
    if data is None:
        print(f"[gemini] shorten failed on all models: {err}")
        return None
    try:
        obj = json.loads(_strip_code_fences(data["candidates"][0]["content"]["parts"][0]["text"]))
    except Exception as ex:
        print(f"[gemini] shorten answer was not valid JSON: {ex}")
        return None
    if isinstance(obj, list) and obj:
        obj = obj[0]
    text = obj.get("arabic_text") if isinstance(obj, dict) else None
    return text.strip() if isinstance(text, str) and text.strip() else None


def pick_native_candidate(job_id: str, previews: list, api_key: str):
    """previews = the WAV bytes of several localized candidates of ONE voice,
    each speaking Arabic. Asks Gemini to listen to all of them and say which
    one sounds most like a native Modern Standard Arabic speaker (no foreign
    accent, natural rhythm and pronunciation). Returns (index, scores) with a
    0-based index, or None when there is no usable answer."""
    if not api_key or len(previews or []) < 2:
        return None
    parts = []
    for i, wav in enumerate(previews):
        parts.append({"text": f"Clip {i + 1}:"})
        parts.append({"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(wav).decode("ascii")}})
    n = len(previews)
    parts.append({"text": (
        f"You heard {n} short clips of the SAME voice speaking Arabic. Judge only HOW NATIVE each one sounds when speaking "
        "Modern Standard Arabic: correct pronunciation of Arabic sounds, natural rhythm and stress, and NO foreign (for example English) accent. "
        "Ignore recording quality and loudness. "
        f'Return ONLY valid JSON: {{"scores": [a score from 0 to 10 for each clip, in order, {n} numbers], "best": the number of the clip that sounds most native (1 to {n})}}')})
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 8192, "responseMimeType": "application/json"},
    }
    data, err = call_gemini(api_key, payload, timeout=120)
    record_gemini(job_id, data)
    if data is None:
        print(f"[gemini] candidate listening failed on all models: {err}")
        return None
    try:
        obj = json.loads(_strip_code_fences(data["candidates"][0]["content"]["parts"][0]["text"]))
        if isinstance(obj, list) and obj:
            obj = obj[0]
        best = int(obj["best"]) - 1
        scores = [float(x) for x in (obj.get("scores") or [])][:n]
    except Exception as ex:
        print(f"[gemini] candidate listening answer not usable: {ex}")
        return None
    if scores and len(scores) == n:
        best = max(range(n), key=lambda i: scores[i]) if not (0 <= best < n) else best
    if not (0 <= best < n):
        return None
    return best, scores


@_metered("shortdub_emotions", lambda job_id, *a, **k: job_id)
def detect_emotions_worker(job_id: str, input_path: str, api_key: str, segments: list):
    """Background worker: listen to each segment and classify its emotion."""
    progress_key = f"emotions_{job_id}"
    try:
        jobs_progress[progress_key] = {
            "status": "processing", "percent": 0, "current": 0,
            "total": len(segments), "emotions": {}, "errors": [],
        }
        emotions_result = {}
        errors = []
        bucket = usage_bucket(job_id)

        for i, seg in enumerate(segments):
            try:
                jobs_progress[progress_key]["current"] = i + 1
                jobs_progress[progress_key]["percent"] = int(((i + 1) / len(segments)) * 100)
                if i > 0:
                    time.sleep(0.5)

                segment_file = OUTPUT_DIR / f"emotion_{job_id}_{seg.segment_id}.mp3"
                duration = seg.end - seg.start
                if duration < 0.3:
                    emotions_result[seg.segment_id] = "neutral"
                    jobs_progress[progress_key]["emotions"] = emotions_result.copy()
                    continue

                cut_audio_segment(input_path, seg.start, duration, segment_file,
                                  sample_rate=16000, channels=1)

                with open(segment_file, "rb") as f:
                    audio_b64 = base64.b64encode(f.read()).decode()
                bucket["audio_sec"] += duration

                prompt = (
                    "Listen to this audio clip carefully. "
                    "What emotion or speaking style is the speaker expressing in their voice tone? "
                    "You MUST return exactly TWO comma-separated style tags (never just one) — "
                    "a primary emotion plus a secondary delivery trait (pacing, volume, or manner) "
                    "from this list (example: 'confident, calm' or 'sad, softly'): "
                    + ", ".join(CANONICAL_EMOTIONS) +
                    ". Do not add any other text."
                )
                payload = {
                    "contents": [{
                        "parts": [
                            {"inline_data": {"mime_type": "audio/mpeg", "data": audio_b64}},
                            {"text": prompt},
                        ]
                    }]
                }

                data, err = call_gemini(api_key, payload, timeout=60)
                record_gemini(job_id, data)

                if data is None:
                    errors.append(f"{seg.segment_id}: {err}")
                    print(f"[emotions] {job_id}: {seg.segment_id}: {str(err)[:300]}")
                    emotions_result[seg.segment_id] = "neutral"
                    jobs_progress[progress_key]["emotions"] = emotions_result.copy()
                    jobs_progress[progress_key]["errors"] = _public_errors(errors)
                    continue

                result_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                emotions_result[seg.segment_id] = normalize_emotions(result_text)
                jobs_progress[progress_key]["emotions"] = emotions_result.copy()

                try:
                    segment_file.unlink()
                except Exception:
                    pass

            except Exception as e:
                errors.append(f"{seg.segment_id}: {str(e)}")
                print(f"[emotions] {job_id}: {seg.segment_id}: {str(e)[:300]}")
                emotions_result[seg.segment_id] = "neutral"
                jobs_progress[progress_key]["emotions"] = emotions_result.copy()
                jobs_progress[progress_key]["errors"] = _public_errors(errors)

        jobs_progress[progress_key]["status"] = "done"
        jobs_progress[progress_key]["percent"] = 100
        jobs_progress[progress_key]["emotions"] = emotions_result
        jobs_progress[progress_key]["errors"] = _public_errors(errors)

    except Exception as e:
        jobs_progress[progress_key] = {
            "status": "error", "percent": 0, "error": friendly_error(e, "emotions"), "emotions": {}, "errors": [],
        }