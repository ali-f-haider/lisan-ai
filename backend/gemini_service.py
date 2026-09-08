import base64
import json
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


def translate_segments(job_id: str, segments: list, api_key: str) -> dict:
    """Translate all segments to Arabic (MSA + Tashkeel) and detect emotions."""
    if not api_key:
        return {"error": "Missing Gemini API key."}
    if not segments:
        return {"error": "No segments to translate."}

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
Detect the emotion or speaking style of each line and use ONLY one tag from this exact list:
{', '.join(CANONICAL_EMOTIONS)}
Preserve the core meaning, but prioritize fitting the time limit.
Return ONLY valid JSON. No explanations.
Return JSON array:
[
{{"segment_id": "...", "arabic_text": "Arabic text with Tashkeel", "emotion": "neutral"}}
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
        return {"error": f"Gemini API failed on all models. Last error: {err}"}

    result_text = _strip_code_fences(data["candidates"][0]["content"]["parts"][0]["text"])
    translated_segments = json.loads(result_text)

    for item in translated_segments:
        if isinstance(item, dict):
            item["emotion"] = normalize_emotion(item.get("emotion", ""))

    return {"status": "success", "translated_segments": translated_segments}


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
                    "You may return up to three comma-separated style tags from this list (example: 'confident, calm'): "
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
                    emotions_result[seg.segment_id] = "neutral"
                    jobs_progress[progress_key]["emotions"] = emotions_result.copy()
                    jobs_progress[progress_key]["errors"] = errors
                    continue

                result_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                emotions_result[seg.segment_id] = normalize_emotion(result_text)
                jobs_progress[progress_key]["emotions"] = emotions_result.copy()

                try:
                    segment_file.unlink()
                except Exception:
                    pass

            except Exception as e:
                errors.append(f"{seg.segment_id}: {str(e)}")
                emotions_result[seg.segment_id] = "neutral"
                jobs_progress[progress_key]["emotions"] = emotions_result.copy()
                jobs_progress[progress_key]["errors"] = errors

        jobs_progress[progress_key]["status"] = "done"
        jobs_progress[progress_key]["percent"] = 100
        jobs_progress[progress_key]["emotions"] = emotions_result
        jobs_progress[progress_key]["errors"] = errors

    except Exception as e:
        jobs_progress[progress_key] = {
            "status": "error", "percent": 0, "error": str(e), "emotions": {}, "errors": [],
        }