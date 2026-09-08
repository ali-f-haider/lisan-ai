import json
import base64
import re
import urllib.request
import urllib.error
from pathlib import Path
import urllib3
from elevenlabs.client import ElevenLabs

from config import OUTPUT_DIR
from app_state import jobs_progress, usage_bucket
from ffmpeg_utils import (
    get_media_duration,
    run_ffmpeg,
    cut_audio_segment,
    concat_audio_files,
)
from media_paths import resolve_job_audio

eleven_client = None

GEMINI_TTS_MODELS = [
    "gemini-2.5-flash-tts",
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-tts",
    "gemini-2.5-pro-preview-tts",
]

# --- FIX 1: FRIENDLY ERROR TRANSLATOR ---
def friendly_error(e):
    """Translates ugly technical errors into clean, user-friendly messages."""
    msg = str(e).lower()
    if "getaddrinfo failed" in msg or "urlopen error" in msg or "connectionerror" in msg or "max retries exceeded" in msg or "winerror 1006" in msg or "name or service not known" in msg:
        return "🌐 No internet connection detected. Please check your Wi-Fi and try again."
    if "timeout" in msg or "timed out" in msg:
        return "⏳ The service is taking too long to respond. Please try again in a moment."
    if "429" in msg or "quota" in msg or "resource_exhausted" in msg:
        return "⚠️ You have hit the API rate limit. Please wait a minute and try again."
    if "401" in msg or "403" in msg or "permission_denied" in msg or "invalid api key" in msg:
        return "🔑 Your API key is invalid or does not have permission. Please check your key."
    return f"⚠️ An unexpected error occurred. Please try again."


def fetch_voices(api_key: str) -> dict:
    try:
        request = urllib.request.Request("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": api_key})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        voices = []
        for voice in data.get("voices", []):
            labels = voice.get("labels") or {}
            voices.append({"voice_id": voice.get("voice_id"), "name": voice.get("name", "Unnamed"),
                           "gender": labels.get("gender", ""), "category": voice.get("category", "")})
        return {"voices": voices}
    except Exception as e:
        return {"error": friendly_error(e)}


def clone_voices(job_id: str, segments: list, api_key: str, speakers_to_clone: list = None) -> dict:
    audio_path = resolve_job_audio(job_id)
    if audio_path is None:
        return {"error": "Audio file not found."}

    source_duration = get_media_duration(audio_path)
    if source_duration <= 0:
        return {"error": f"Could not read source audio duration: {audio_path}"}

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
                cloned_voices[speaker] = f"ERROR: No usable timed segments found for {speaker}."
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

                cut_file = OUTPUT_DIR / f"clone_{job_id}_{safe_speaker}_{seg.segment_id}.wav"

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
                        if cut_file.exists(): cut_file.unlink()
                        continue

                    actual_dur = get_media_duration(cut_file)
                    if actual_dur >= 0.5:
                        cut_files.append(cut_file)
                        total_valid_duration += actual_dur
                    else:
                        if cut_file.exists(): cut_file.unlink()

                except Exception as cut_err:
                    print(f"Warning: failed to cut clone sample for {speaker}: {cut_err}")
                    if cut_file.exists(): cut_file.unlink()
                    continue

            if total_valid_duration < 1.0:
                try:
                    min_start = max(0.0, min(float(s.start) for s in speaker_segs) - 0.5)
                    max_end = min(source_duration, max(float(s.end) for s in speaker_segs) + 0.5)
                    fallback_dur = max_end - min_start

                    if fallback_dur >= 1.0:
                        fallback_file = OUTPUT_DIR / f"clone_{job_id}_{safe_speaker}_fallback.wav"
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
                                    if cf.exists(): cf.unlink()
                                cut_files = [fallback_file]
                                total_valid_duration = fallback_actual
                            else:
                                if fallback_file.exists(): fallback_file.unlink()
                except Exception as fallback_err:
                    print(f"Warning: fallback clone cut failed for {speaker}: {fallback_err}")

            if total_valid_duration < 1.0 or not cut_files:
                cloned_voices[speaker] = f"ERROR: Not enough valid audio for {speaker} ({total_valid_duration:.2f}s)."
                for cf in cut_files:
                    if cf.exists(): cf.unlink()
                continue

            concat_file = OUTPUT_DIR / f"clone_{job_id}_{safe_speaker}_final.wav"
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
                cloned_voices[speaker] = f"ERROR: Final clone sample for {speaker} is empty."
                continue

            final_duration = get_media_duration(concat_file)
            if final_duration < 1.0:
                padded_file = OUTPUT_DIR / f"clone_{job_id}_{safe_speaker}_final_padded.wav"
                pad_needed = max(0.2, 1.15 - final_duration)
                run_ffmpeg(["ffmpeg", "-y", "-i", str(concat_file), "-af", f"apad=pad_dur={pad_needed}", "-vn", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le", str(padded_file)])
                if concat_file.exists(): concat_file.unlink()
                concat_file = padded_file
                final_duration = get_media_duration(concat_file)

            if final_duration < 1.0:
                cloned_voices[speaker] = f"ERROR: Final clone sample for {speaker} is still too short ({final_duration:.2f}s)."
                continue

            with open(concat_file, "rb") as f:
                file_data = f.read()

            http = urllib3.PoolManager()
            response = http.request(
                "POST", "https://api.elevenlabs.io/v1/voices/add",
                headers={"xi-api-key": api_key},
                fields={"name": f"Cloned_{speaker}", "files": (f"{safe_speaker}.wav", file_data, "audio/wav"), "labels": "{}"}
            )

            if response.status == 200:
                data = json.loads(response.data.decode())
                cloned_voices[speaker] = data["voice_id"]
            else:
                raise Exception(f"API error {response.status}: {response.data.decode(errors='ignore')}")

        except Exception as e:
            cloned_voices[speaker] = f"ERROR: {friendly_error(e)}"

        finally:
            for cf in cut_files:
                if cf.exists():
                    try: cf.unlink()
                    except: pass
            if concat_file is not None and concat_file.exists():
                try: concat_file.unlink()
                except: pass

    warnings = []
    for speaker in cloned_voices:
        if not str(cloned_voices[speaker]).startswith("ERROR"):
            speaker_segs = [s for s in segments if s.speaker == speaker]
            total_available = sum(max(0, s.end - s.start) for s in speaker_segs)
            if total_available < 3.0:
                warnings.append(f"{speaker}: Only {total_available:.1f}s available. Clone quality will likely be poor.")
            elif total_available < 10.0:
                warnings.append(f"{speaker}: Only {total_available:.1f}s available. Clone quality may be reduced.")

    return {"status": "success", "cloned_voices": cloned_voices, "warnings": warnings}


def record_gemini(job_id, data):
    if not isinstance(data, dict):
        return
    u = data.get("usageMetadata") or {}
    b = usage_bucket(job_id)
    b["gemini_in"] += int(u.get("promptTokenCount", 0) or 0)
    b["gemini_out"] += int(u.get("candidatesTokenCount", 0) or 0)
    b["gemini_thoughts"] += int(u.get("thoughtsTokenCount", 0) or 0)


def _head_of(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read(4)


def _is_raw_pcm(head: bytes) -> bool:
    return not (head == b"RIFF" or head[:3] == b"ID3" or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0))


def _audio_duration(path: Path, rate: int) -> float:
    """Duration without ffprobe for headerless raw PCM (computed from byte size)."""
    head = _head_of(path)
    if _is_raw_pcm(head):
        size = path.stat().st_size
        return size / float(rate * 2)   # 16-bit mono
    return get_media_duration(path)


def _stretch_to_wav(src: Path, out: Path, tempo: float, rate: int):
    """Stretch any source (raw PCM / WAV / MP3) into a valid WAV. Never ffprobes raw PCM."""
    head = _head_of(src)
    if _is_raw_pcm(head):
        src_args = ["-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", str(src)]
    else:
        src_args = ["-i", str(src)]
    cmd = ["ffmpeg", "-y"] + src_args
    if abs(tempo - 1.0) > 0.02:
        cmd += ["-filter:a", f"atempo={tempo:.6f}"]
    cmd += ["-acodec", "pcm_s16le", str(out)]
    run_ffmpeg(cmd)


def _save_gemini_audio(data_bytes: bytes, base_path: Path, rate: int) -> Path:
    if not data_bytes:
        raise Exception("Gemini TTS returned empty audio.")
    if data_bytes[:4] == b"RIFF":
        out = base_path.with_suffix(".wav")
        out.write_bytes(data_bytes)
        return out
    if data_bytes[:3] == b"ID3" or (data_bytes[0] == 0xFF and (data_bytes[1] & 0xE0) == 0xE0):
        out = base_path.with_suffix(".mp3")
        out.write_bytes(data_bytes)
        return out
    out = base_path.with_suffix(".pcm")
    out.write_bytes(data_bytes)
    return out


def _gemini_tts_call(api_key: str, text: str, voice: str):
    last_err = None
    for model_name in GEMINI_TTS_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}
            }
        }
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.load(response)
        except urllib.error.HTTPError as e:
            body = ""
            try: body = e.read().decode(errors="ignore")
            except Exception: body = str(e)
            last_err = f"[{model_name}] HTTP {e.code}: {body[:200]}"
            continue
        except Exception as e:
            last_err = f"[{model_name}] {e}"
            continue

        cands = data.get("candidates") or []
        if not cands:
            block = (data.get("promptFeedback") or {}).get("blockReason")
            last_err = f"[{model_name}] No candidates (blockReason={block or 'unknown'})"
            continue
        cand = cands[0]
        content = cand.get("content")
        if not content:
            last_err = f"[{model_name}] Candidate has no content (finishReason={cand.get('finishReason')})"
            continue
        parts = content.get("parts") or []
        inline = None
        for p in parts:
            inline = p.get("inlineData") or p.get("inline_data")
            if inline:
                break
        if not inline or not inline.get("data"):
            last_err = f"[{model_name}] No audio data in response"
            continue
        return data, model_name
    raise Exception(f"Gemini TTS failed on all models. Last: {last_err}")


def generate_worker(req):
    global eleven_client
    try:
        jobs_progress["generate"] = {"status": "processing", "percent": 0, "result": None, "error": None}
        total_segments = len(req.segments)
        if total_segments == 0:
            raise Exception("No segments found.")
        bucket = usage_bucket(req.job_id)
        sorted_segments = sorted(req.segments, key=lambda s: s.start)
        generated_files = []

        for i, seg in enumerate(sorted_segments):
            if not seg.arabic_text.strip():
                continue
            target_duration = max(seg.end - seg.start, 0.5)
            rate = 24000

            if req.tts_provider == "gemini":
                if not req.gemini_api_key:
                    raise Exception("Missing Gemini API key.")
                
                try:
                    data, used_model = _gemini_tts_call(req.gemini_api_key, seg.arabic_text, req.gemini_voice or "Kore")
                    record_gemini(req.job_id, data)
                    cands = data.get("candidates") or []
                    inline = None
                    for p in (cands[0].get("content") or {}).get("parts") or []:
                        inline = p.get("inlineData") or p.get("inline_data")
                        if inline:
                            break
                    mime = inline.get("mimeType") or inline.get("mime_type") or ""
                    m = re.search(r"rate=(\d+)", mime)
                    rate = int(m.group(1)) if m else 24000
                    pcm = base64.b64decode(inline.get("data", ""))
                    raw_path = _save_gemini_audio(pcm, OUTPUT_DIR / f"{seg.segment_id}_raw", rate)
                    
                    # --- FIX 2: CRASH-PROOF DURATION CALCULATION ---
                    actual_duration = _audio_duration(raw_path, rate)
                    
                except Exception as e:
                    raise Exception(friendly_error(e))

            else:
                api_key = req.elevenlabs_api_key.strip()
                if not api_key:
                    raise Exception("Missing ElevenLabs API key.")
                if eleven_client is None:
                    eleven_client = ElevenLabs(api_key=api_key)
                voice_id = req.speaker_voices.get(seg.speaker, "").strip() or req.default_voice_id.strip()
                if not voice_id:
                    raise Exception(f"No voice assigned for speaker: {seg.speaker}")
                tts_text = f"[{seg.emotion}] {seg.arabic_text}"
                bucket["eleven_chars"] += len(tts_text)
                
                try:
                    response = eleven_client.text_to_speech.convert(text=tts_text, voice_id=voice_id, model_id="eleven_v3")
                    audio_bytes = response if isinstance(response, bytes) else b"".join(chunk for chunk in response if chunk)
                    raw_path = OUTPUT_DIR / f"{seg.segment_id}_raw.mp3"
                    raw_path.write_bytes(audio_bytes)
                    actual_duration = get_media_duration(raw_path)
                except Exception as e:
                    raise Exception(friendly_error(e))

            if actual_duration <= 0:
                actual_duration = target_duration
            required_tempo = actual_duration / target_duration
            if req.tempo_mode == "excellent": min_tempo, max_tempo = 0.95, 1.10
            elif req.tempo_mode == "good": min_tempo, max_tempo = 0.85, 1.25
            else: min_tempo, max_tempo = 0.75, 1.35
            needs_warning = False
            if required_tempo < min_tempo: tempo = 1.0
            elif required_tempo > max_tempo: tempo = max_tempo; needs_warning = True
            else: tempo = required_tempo

            stretched_path = OUTPUT_DIR / f"{seg.segment_id}_stretched.wav"
            _stretch_to_wav(raw_path, stretched_path, tempo, rate)
            stretched_duration = get_media_duration(stretched_path)
            if stretched_duration <= 0:
                stretched_duration = target_duration
            generated_files.append({"file": stretched_path.name, "start": seg.start, "end": seg.end,
                                    "speaker": seg.speaker, "duration": stretched_duration, "tempo_warning": needs_warning})
            jobs_progress["generate"]["percent"] = int(((i + 1) / total_segments) * 90)

        if not generated_files:
            raise Exception("No Arabic text found.")
        jobs_progress["generate"]["percent"] = 92
        generated_files.sort(key=lambda item: item["start"])
        max_segment_end = max(item["end"] for item in generated_files)
        max_played_end = max(item["start"] + item["duration"] for item in generated_files)
        if req.duration_mode == "extend":
            final_duration = max(req.total_duration, max_played_end, max_segment_end)
        else:
            final_duration = req.total_duration if req.total_duration > 0 else max(max_segment_end, max_played_end)

        adjusted_files = []
        cut_count = 0
        for i, item in enumerate(generated_files):
            allowed_end = generated_files[i + 1]["start"] - 0.02 if i + 1 < len(generated_files) else final_duration
            allowed_duration = allowed_end - item["start"]
            if allowed_duration <= 0.05: continue
            if item["duration"] > allowed_duration + 0.05: cut_count += 1
            item["allowed_duration"] = min(item["duration"], allowed_duration)
            adjusted_files.append(item)
        if not adjusted_files:
            raise Exception("No generated segments fit.")

        inputs = ["-f", "lavfi", "-t", str(final_duration), "-i", "anullsrc=r=44100:cl=stereo"]
        filter_parts = []
        for idx, item in enumerate(adjusted_files):
            input_index = idx + 1
            inputs.extend(["-i", str(OUTPUT_DIR / item["file"])])
            delay_ms = int(item["start"] * 1000)
            allowed = max(item["allowed_duration"], 0.05)
            if item["duration"] > allowed + 0.05:
                fade_start = max(0, allowed - 0.2)
                filter_parts.append(f"[{input_index}]aformat=channel_layouts=stereo,atrim=0:{allowed:.3f},afade=t=out:st={fade_start:.3f}:d=0.2,asetpts=PTS-STARTPTS,adelay={delay_ms}|{delay_ms},apad[a{idx}]")
            else:
                filter_parts.append(f"[{input_index}]aformat=channel_layouts=stereo,atrim=0:{allowed:.3f},asetpts=PTS-STARTPTS,adelay={delay_ms}|{delay_ms},apad[a{idx}]")
        mix_inputs = "".join([f"[a{i}]" for i in range(len(adjusted_files))])
        filter_parts.append(f"[0]{mix_inputs}amix=inputs={len(adjusted_files) + 1}:duration=first:normalize=0[out]")
        filter_complex = ";".join(filter_parts)
        output_file = OUTPUT_DIR / "final_dubbed.mp3"
        run_ffmpeg(["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[out]", "-t", str(final_duration), str(output_file)])

        if req.tts_provider != "gemini" and req.cloned_voice_ids and eleven_client:
            for vid in req.cloned_voice_ids:
                try: eleven_client.voices.delete(voice_id=vid)
                except Exception: pass

        warning_count = sum(1 for f in adjusted_files if f.get("tempo_warning"))
        result = {"status": "success", "output_folder": str(OUTPUT_DIR), "final_file": str(output_file),
                  "segments_generated": len(adjusted_files), "tempo_warnings": warning_count,
                  "duration_cuts": cut_count, "final_duration": round(final_duration, 2),
                  "eleven_credits_used": bucket["eleven_chars"]}
        jobs_progress["generate"].update({"status": "done", "percent": 100, "result": result, "error": None})
    except Exception as e:
        jobs_progress["generate"] = {"status": "error", "percent": 0, "error": friendly_error(e), "result": None}