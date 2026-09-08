import json
import base64
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

def fetch_voices(api_key: str) -> dict:
    try:
        request = urllib.request.Request("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": api_key})
        with urllib.request.urlopen(request, timeout=30) as response: data = json.load(response)
        voices = []
        for voice in data.get("voices", []):
            labels = voice.get("labels") or {}
            voices.append({"voice_id": voice.get("voice_id"), "name": voice.get("name", "Unnamed"), "gender": labels.get("gender", ""), "category": voice.get("category", "")})
        return {"voices": voices}
    except urllib.error.HTTPError as e:
        try: error_body = e.read().decode(errors="ignore")
        except Exception: error_body = str(e)
        return {"error": f"ElevenLabs API error {e.code}: {error_body}"}
    except Exception as e: return {"error": str(e)}

    
    audio_path = resolve_job_audio(job_id)
    if audio_path is None:
        return {"error": "Audio file not found."}
    
    cloned_voices = {}
    speakers = list(set(s.speaker for s in segments if s.text.strip()))
    if speakers_to_clone:
        speakers = [s for s in speakers if s in speakers_to_clone]
        
    for speaker in speakers:
        try:
            safe_speaker = "".join(c for c in speaker if c.isalnum()).strip() or "speaker"
            
            # Get all segments for this speaker, sorted by start time
            speaker_segs = [s for s in segments if s.speaker == speaker]
            speaker_segs.sort(key=lambda s: s.start)
            
            # Merge short segments (< 1.0s) with neighbors to avoid losing audio context
            merged_segs = []
            for seg in speaker_segs:
                dur = seg.end - seg.start
                if dur < 1.0 and merged_segs:
                    prev = merged_segs[-1]
                    prev.end = max(prev.end, seg.end)
                    prev.text += " " + seg.text
                elif dur < 1.0 and not merged_segs:
                    merged_segs.append(seg)
                else:
                    if merged_segs and (merged_segs[-1].end - merged_segs[-1].start) < 1.0:
                        prev = merged_segs[-1]
                        prev.end = max(prev.end, seg.end)
                        prev.text += " " + seg.text
                    else:
                        merged_segs.append(seg)
            
            # Sort by duration descending to get the longest, clearest clips first
            merged_segs.sort(key=lambda s: s.end - s.start, reverse=True)
            
            cut_files = []
            total_time = 0.0
            
            for seg in merged_segs:
                if total_time >= 20.0: 
                    break
                
                dur = seg.end - seg.start
                if dur < 0.1: 
                    continue
                
                # Clamp timestamps to ensure they are within the audio file bounds
                start_time = max(0.0, seg.start)
                
                cut_file = OUTPUT_DIR / f"clone_{job_id}_{safe_speaker}_{seg.segment_id}.mp3"
                
                try:
                    cut_audio_segment(str(audio_path), start_time, dur, cut_file, sample_rate=44100, channels=1)
                    
                    # VERIFY THE CUT: Ensure the file was actually created and has a valid duration
                    if cut_file.exists() and cut_file.stat().st_size > 1000:
                        # Double-check duration using ffprobe
                        actual_dur = get_media_duration(cut_file)
                        if actual_dur >= 0.1:
                            cut_files.append(cut_file)
                            total_time += actual_dur
                        else:
                            if cut_file.exists(): cut_file.unlink()
                    else:
                        if cut_file.exists(): cut_file.unlink()
                except Exception as cut_err:
                    print(f"Warning: Failed to cut audio for {speaker} segment {seg.segment_id}: {cut_err}")
                    if cut_file.exists(): cut_file.unlink()
                    continue
            
            # SAFETY CHECK & PADDING: ElevenLabs requires at least 1 second of valid audio
            if total_time < 1.0 or not cut_files:
                cloned_voices[speaker] = f"ERROR: Not enough valid audio for {speaker} ({total_time:.2f}s). ElevenLabs requires at least 1.0s."
                for cf in cut_files:
                    try: cf.unlink()
                    except: pass
                continue
            
            # SAFE MERGE: Use ffmpeg filter_complex to concatenate instead of the fragile concat demuxer
            concat_file = OUTPUT_DIR / f"clone_{job_id}_{safe_speaker}.mp3"
            
            try:
                if len(cut_files) == 1:
                    # If only one file, just copy/re-encode it directly
                    run_ffmpeg(["ffmpeg", "-y", "-i", str(cut_files[0]), "-acodec", "libmp3lame", "-ar", "44100", "-ac", "1", str(concat_file)])
                else:
                    # Build a safe filter_complex chain to concatenate all clips
                    inputs = []
                    filter_parts = []
                    for i, cf in enumerate(cut_files):
                        inputs.extend(["-i", str(cf)])
                        filter_parts.append(f"[{i}:a]")
                    
                    filter_chain = "".join(filter_parts) + f"concat=n={len(cut_files)}:v=0:a=1[out]"
                    cmd = ["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_chain, "-map", "[out]", "-acodec", "libmp3lame", "-ar", "44100", "-ac", "1", str(concat_file)]
                    run_ffmpeg(cmd)
            except Exception as concat_err:
                cloned_voices[speaker] = f"ERROR: Failed to merge audio clips for {speaker}: {str(concat_err)}"
                for cf in cut_files:
                    try: cf.unlink()
                    except: pass
                continue
            
            # Verify the concatenated file
            if not concat_file.exists() or concat_file.stat().st_size < 1000:
                cloned_voices[speaker] = f"ERROR: Merged audio file for {speaker} is invalid or empty."
                for cf in cut_files:
                    try: cf.unlink()
                    except: pass
                if concat_file.exists(): concat_file.unlink()
                continue
            
            # Check final duration and pad with silence if it's slightly under 1.0s
            final_dur = get_media_duration(concat_file)
            if final_dur < 1.0:
                pad_needed = 1.05 - final_dur
                padded_file = OUTPUT_DIR / f"clone_{job_id}_{safe_speaker}_padded.mp3"
                run_ffmpeg([
                    "ffmpeg", "-y", "-i", str(concat_file), 
                    "-af", f"apad=pad_dur={pad_needed}", 
                    "-acodec", "libmp3lame", "-ar", "44100", "-ac", "1", str(padded_file)
                ])
                concat_file.unlink()
                concat_file = padded_file
            
            with open(concat_file, "rb") as f:
                file_data = f.read()
                
            http = urllib3.PoolManager()
            response = http.request(
                'POST', 
                'https://api.elevenlabs.io/v1/voices/add',
                headers={'xi-api-key': api_key},
                fields={
                    'name': f"Cloned_{speaker}", 
                    'files': (f"{safe_speaker}.mp3", file_data, 'audio/mpeg'), 
                    'labels': '{}'
                }
            )
            
            if response.status == 200:
                data = json.loads(response.data.decode())
                cloned_voices[speaker] = data['voice_id']
            else:
                raise Exception(f"API error {response.status}: {response.data.decode()}")
                
            # Cleanup
            for cf in cut_files:
                try: cf.unlink()
                except Exception: pass
            try: concat_file.unlink()
            except Exception: pass
            
        except Exception as e:
            cloned_voices[speaker] = f"ERROR: {str(e)}"
            
    warnings = []
    for speaker in cloned_voices:
        if not cloned_voices[speaker].startswith("ERROR"):
            speaker_segs = [s for s in segments if s.speaker == speaker]
            total_available = sum(s.end - s.start for s in speaker_segs)
            if total_available < 3.0:
                warnings.append(f"{speaker}: Only {total_available:.1f}s available. Clone quality may be reduced.")
            elif total_available < 10.0:
                warnings.append(f"{speaker}: Only {total_available:.1f}s available. Clone quality may be reduced.")
                
    return {"status": "success", "cloned_voices": cloned_voices, "warnings": warnings}


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
            
            # Get segments for this speaker
            speaker_segs = [
                s for s in segments
                if s.speaker == speaker and (s.text or "").strip() and (s.end - s.start) > 0.05
            ]

            if not speaker_segs:
                cloned_voices[speaker] = f"ERROR: No usable timed segments found for {speaker}."
                continue

            # Longest segments first
            speaker_segs.sort(key=lambda s: s.end - s.start, reverse=True)
            total_valid_duration = 0.0

            for seg in speaker_segs:
                if total_valid_duration >= 20.0:
                    break
                
                # Add margins so ffmpeg doesn't cut too tightly
                margin = 0.35
                start_time = max(0.0, float(seg.start) - margin)
                end_time = min(source_duration, float(seg.end) + margin)
                dur = end_time - start_time

                if dur < 0.2:
                    continue

                cut_file = OUTPUT_DIR / f"clone_{job_id}_{safe_speaker}_{seg.segment_id}.wav"

                try:
                    # Cut as WAV PCM (much safer than MP3 for short clips)
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

            # Fallback wide cut if normal cutting failed
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

            # Merge into one final WAV
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
            cloned_voices[speaker] = f"ERROR: {str(e)}"

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

def generate_worker(req):
    global eleven_client
    try:
        jobs_progress["generate"] = {"status": "processing", "percent": 0, "result": None, "error": None}
        total_segments = len(req.segments)
        if total_segments == 0: raise Exception("No segments found.")
        bucket = usage_bucket(req.job_id)
        sorted_segments = sorted(req.segments, key=lambda s: s.start)
        generated_files = []

        for i, seg in enumerate(sorted_segments):
            if not seg.arabic_text.strip(): continue
            target_duration = max(seg.end - seg.start, 0.5)
            tts_text = seg.arabic_text # Gemini doesn't use emotion tags in text like ElevenLabs
            
            if req.tts_provider == "gemini":
                if not req.gemini_api_key: raise Exception("Missing Gemini API key.")
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={req.gemini_api_key}"
                payload = {
                    "contents": [{"parts": [{"text": tts_text}]}],
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": req.gemini_voice or "Kore"}}}
                    }
                }
                request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=120) as response:
                    data = json.load(response)
                record_gemini(req.job_id, data)
                audio_b64 = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
                audio_bytes = base64.b64decode(audio_b64)
                raw_filename = f"{seg.segment_id}_raw.wav"
            else:
                # ElevenLabs
                api_key = req.elevenlabs_api_key.strip()
                if not api_key: raise Exception("Missing ElevenLabs API key.")
                if eleven_client is None: eleven_client = ElevenLabs(api_key=api_key)
                voice_id = req.speaker_voices.get(seg.speaker, "").strip() or req.default_voice_id.strip()
                if not voice_id: raise Exception(f"No voice assigned for speaker: {seg.speaker}")
                tts_text = f"[{seg.emotion}] {seg.arabic_text}"
                bucket["eleven_chars"] += len(tts_text)
                response = eleven_client.text_to_speech.convert(text=tts_text, voice_id=voice_id, model_id="eleven_v3")
                audio_bytes = response if isinstance(response, bytes) else b"".join(chunk for chunk in response if chunk)
                raw_filename = f"{seg.segment_id}_raw.mp3"

            raw_path = OUTPUT_DIR / raw_filename
            raw_path.write_bytes(audio_bytes)
            actual_duration = get_media_duration(raw_path)
            if actual_duration <= 0: actual_duration = target_duration
            required_tempo = actual_duration / target_duration
            if req.tempo_mode == "excellent": min_tempo, max_tempo = 0.95, 1.10
            elif req.tempo_mode == "good": min_tempo, max_tempo = 0.85, 1.25
            else: min_tempo, max_tempo = 0.75, 1.35
            needs_warning = False
            if required_tempo < min_tempo: tempo = 1.0
            elif required_tempo > max_tempo: tempo = max_tempo; needs_warning = True
            else: tempo = required_tempo
            stretched_filename = f"{seg.segment_id}_stretched.wav" if req.tts_provider == "gemini" else f"{seg.segment_id}_stretched.mp3"
            stretched_path = OUTPUT_DIR / stretched_filename
            if abs(tempo - 1.0) > 0.02:
                run_ffmpeg(["ffmpeg", "-y", "-i", str(raw_path), "-filter:a", f"atempo={tempo:.6f}", str(stretched_path)])
            else:
                import shutil; shutil.copy(raw_path, stretched_path)
            stretched_duration = get_media_duration(stretched_path)
            if stretched_duration <= 0: stretched_duration = target_duration
            generated_files.append({"file": stretched_filename, "start": seg.start, "end": seg.end, "speaker": seg.speaker, "duration": stretched_duration, "tempo_warning": needs_warning})
            jobs_progress["generate"]["percent"] = int(((i + 1) / total_segments) * 90)

        if not generated_files: raise Exception("No Arabic text found.")
        jobs_progress["generate"]["percent"] = 92
        generated_files.sort(key=lambda item: item["start"])
        max_segment_end = max(item["end"] for item in generated_files)
        max_played_end = max(item["start"] + item["duration"] for item in generated_files)
        if req.duration_mode == "extend": final_duration = max(req.total_duration, max_played_end, max_segment_end)
        else: final_duration = req.total_duration if req.total_duration > 0 else max(max_segment_end, max_played_end)
        
        adjusted_files = []
        cut_count = 0
        for i, item in enumerate(generated_files):
            allowed_end = generated_files[i + 1]["start"] - 0.02 if i + 1 < len(generated_files) else final_duration
            allowed_duration = allowed_end - item["start"]
            if allowed_duration <= 0.05: continue
            if item["duration"] > allowed_duration + 0.05: cut_count += 1
            item["allowed_duration"] = min(item["duration"], allowed_duration)
            adjusted_files.append(item)
        if not adjusted_files: raise Exception("No generated segments fit.")

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


        warning_count = sum(1 for f in adjusted_files if f.get("tempo_warning"))
        result = {"status": "success", "output_folder": str(OUTPUT_DIR), "final_file": str(output_file), "segments_generated": len(adjusted_files), "tempo_warnings": warning_count, "duration_cuts": cut_count, "final_duration": round(final_duration, 2), "eleven_credits_used": bucket["eleven_chars"]}
        jobs_progress["generate"].update({"status": "done", "percent": 100, "result": result, "error": None})
    except Exception as e:
        jobs_progress["generate"] = {"status": "error", "percent": 0, "error": str(e), "result": None}

def record_gemini(job_id, data):
    from app_state import usage_bucket
    if not isinstance(data, dict): return
    u = data.get("usageMetadata") or {}
    b = usage_bucket(job_id)
    b["gemini_in"] += int(u.get("promptTokenCount", 0) or 0)
    b["gemini_out"] += int(u.get("candidatesTokenCount", 0) or 0)
    b["gemini_thoughts"] += int(u.get("thoughtsTokenCount", 0) or 0)
    
def rebuild_final_mix(segments, total_duration, duration_mode="exact"):
    """Rebuild final_dubbed.mp3 from existing segment files (no TTS calls)."""
    segs = sorted([s for s in segments if (s.arabic_text or "").strip()], key=lambda s: s.start)
    items = []
    for s in segs:
        sp = OUTPUT_DIR / f"{s.segment_id}_stretched.wav"
        if not sp.exists():
            continue
        items.append({"file": sp.name, "start": s.start, "end": s.end,
                      "duration": get_media_duration(sp)})
    if not items:
        raise Exception("No generated line audio found. Run Generate once first.")

    max_segment_end = max(i["end"] for i in items)
    max_played_end = max(i["start"] + i["duration"] for i in items)
    if duration_mode == "extend":
        final_duration = max(total_duration, max_played_end, max_segment_end)
    else:
        final_duration = total_duration if total_duration > 0 else max(max_segment_end, max_played_end)

    adjusted = []
    cuts = 0
    for i, item in enumerate(items):
        allowed_end = items[i + 1]["start"] - 0.02 if i + 1 < len(items) else final_duration
        allowed = allowed_end - item["start"]
        if allowed <= 0.05:
            continue
        if item["duration"] > allowed + 0.05:
            cuts += 1
        item["allowed_duration"] = min(item["duration"], allowed)
        adjusted.append(item)
    if not adjusted:
        raise Exception("No lines fit the timeline.")

    inputs = ["-f", "lavfi", "-t", str(final_duration), "-i", "anullsrc=r=44100:cl=stereo"]
    filter_parts = []
    for idx, item in enumerate(adjusted):
        input_index = idx + 1
        inputs.extend(["-i", str(OUTPUT_DIR / item["file"])])
        delay_ms = int(item["start"] * 1000)
        allowed = max(item["allowed_duration"], 0.05)
        if item["duration"] > allowed + 0.05:
            fade_start = max(0, allowed - 0.2)
            filter_parts.append(f"[{input_index}]aformat=channel_layouts=stereo,atrim=0:{allowed:.3f},afade=t=out:st={fade_start:.3f}:d=0.2,asetpts=PTS-STARTPTS,adelay={delay_ms}|{delay_ms},apad[a{idx}]")
        else:
            filter_parts.append(f"[{input_index}]aformat=channel_layouts=stereo,atrim=0:{allowed:.3f},asetpts=PTS-STARTPTS,adelay={delay_ms}|{delay_ms},apad[a{idx}]")
    mix_inputs = "".join([f"[a{i}]" for i in range(len(adjusted))])
    filter_parts.append(f"[0]{mix_inputs}amix=inputs={len(adjusted) + 1}:duration=first:normalize=0[out]")
    filter_complex = ";".join(filter_parts)
    output_file = OUTPUT_DIR / "final_dubbed.mp3"
    run_ffmpeg(["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[out]", "-t", str(final_duration), str(output_file)])
    return {"segments_generated": len(adjusted), "duration_cuts": cuts,
            "final_duration": round(final_duration, 2)}


def regenerate_line(req):
    """Re-speak ONE segment with TTS, stretch it into its window, then rebuild the final mix."""
    global eleven_client
    try:
        seg = req.segment
        if not (seg.arabic_text or "").strip():
            return {"error": "This line has no Arabic text yet."}
        api_key = req.elevenlabs_api_key.strip()
        if not api_key:
            return {"error": "Missing ElevenLabs API key."}
        voice_id = (req.voice_id or "").strip()
        if not voice_id:
            return {"error": f"No voice selected for {seg.speaker}. Pick one in Step 4 first."}

        if eleven_client is None:
            eleven_client = ElevenLabs(api_key=api_key)

        bucket = usage_bucket(req.job_id)
        target_duration = max(seg.end - seg.start, 0.5)
        tts_text = f"[{seg.emotion}] {seg.arabic_text}"
        bucket["eleven_chars"] += len(tts_text)

        response = eleven_client.text_to_speech.convert(text=tts_text, voice_id=voice_id, model_id="eleven_v3")
        audio_bytes = response if isinstance(response, bytes) else b"".join(c for c in response if c)
        raw_path = OUTPUT_DIR / f"{seg.segment_id}_raw.mp3"
        raw_path.write_bytes(audio_bytes)

        actual = get_media_duration(raw_path)
        if actual <= 0:
            actual = target_duration
        required = actual / target_duration
        if req.tempo_mode == "excellent": min_tempo, max_tempo = 0.95, 1.10
        elif req.tempo_mode == "good": min_tempo, max_tempo = 0.85, 1.25
        else: min_tempo, max_tempo = 0.75, 1.35
        warning = False
        if required < min_tempo: tempo = 1.0
        elif required > max_tempo: tempo = max_tempo; warning = True
        else: tempo = required

        stretched = OUTPUT_DIR / f"{seg.segment_id}_stretched.wav"
        cmd = ["ffmpeg", "-y", "-i", str(raw_path)]
        if abs(tempo - 1.0) > 0.02:
            cmd += ["-filter:a", f"atempo={tempo:.6f}"]
        cmd += ["-acodec", "pcm_s16le", str(stretched)]
        run_ffmpeg(cmd)

        mix = rebuild_final_mix(req.segments, req.total_duration, req.duration_mode)
        return {"status": "success",
                "stretched_duration": round(get_media_duration(stretched), 2),
                "target": round(target_duration, 2),
                "tempo_warning": warning,
                "mix": mix}
    except Exception as e:
        return {"error": friendly_error(e)}
        
friendly_error = lambda e: str(e)

def rebuild_final_mix(segments, total_duration, duration_mode="exact"):
    """Rebuild final_dubbed.mp3 from existing line files (.wav OR .mp3)."""
    segs = sorted([s for s in segments if (s.arabic_text or "").strip()], key=lambda s: s.start)
    items = []
    for s in segs:
        sp = OUTPUT_DIR / f"{s.segment_id}_stretched.wav"
        if not sp.exists():
            sp = OUTPUT_DIR / f"{s.segment_id}_stretched.mp3"
        if not sp.exists():
            continue
        items.append({"file": sp.name, "start": s.start, "end": s.end,
                      "duration": get_media_duration(sp)})
    if not items:
        raise Exception("No generated line audio found. Run Generate once first.")

    max_segment_end = max(i["end"] for i in items)
    max_played_end = max(i["start"] + i["duration"] for i in items)
    if duration_mode == "extend":
        final_duration = max(total_duration, max_played_end, max_segment_end)
    else:
        final_duration = total_duration if total_duration > 0 else max(max_segment_end, max_played_end)

    adjusted = []
    cuts = 0
    for i, item in enumerate(items):
        allowed_end = items[i + 1]["start"] - 0.02 if i + 1 < len(items) else final_duration
        allowed = allowed_end - item["start"]
        if allowed <= 0.05:
            continue
        if item["duration"] > allowed + 0.05:
            cuts += 1
        item["allowed_duration"] = min(item["duration"], allowed)
        adjusted.append(item)
    if not adjusted:
        raise Exception("No lines fit the timeline.")

    inputs = ["-f", "lavfi", "-t", str(final_duration), "-i", "anullsrc=r=44100:cl=stereo"]
    filter_parts = []
    for idx, item in enumerate(adjusted):
        input_index = idx + 1
        inputs.extend(["-i", str(OUTPUT_DIR / item["file"])])
        delay_ms = int(item["start"] * 1000)
        allowed = max(item["allowed_duration"], 0.05)
        if item["duration"] > allowed + 0.05:
            fade_start = max(0, allowed - 0.2)
            filter_parts.append(f"[{input_index}]aformat=channel_layouts=stereo,atrim=0:{allowed:.3f},afade=t=out:st={fade_start:.3f}:d=0.2,asetpts=PTS-STARTPTS,adelay={delay_ms}|{delay_ms},apad[a{idx}]")
        else:
            filter_parts.append(f"[{input_index}]aformat=channel_layouts=stereo,atrim=0:{allowed:.3f},asetpts=PTS-STARTPTS,adelay={delay_ms}|{delay_ms},apad[a{idx}]")
    mix_inputs = "".join([f"[a{i}]" for i in range(len(adjusted))])
    filter_parts.append(f"[0]{mix_inputs}amix=inputs={len(adjusted) + 1}:duration=first:normalize=0[out]")
    filter_complex = ";".join(filter_parts)
    output_file = OUTPUT_DIR / "final_dubbed.mp3"
    run_ffmpeg(["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[out]", "-t", str(final_duration), str(output_file)])
    return {"segments_generated": len(adjusted), "duration_cuts": cuts,
            "final_duration": round(final_duration, 2)}
            
def remix_with_offsets(req):
    """Rebuild final_dubbed.mp3 applying per-segment time offsets (seconds)."""
    try:
        segs = sorted([s for s in req.segments if (s.arabic_text or "").strip()], key=lambda s: s.start)
        items = []
        for s in segs:
            sp = OUTPUT_DIR / f"{s.segment_id}_stretched.wav"
            if not sp.exists():
                sp = OUTPUT_DIR / f"{s.segment_id}_stretched.mp3"
            if not sp.exists():
                continue
            off = float((req.offsets or {}).get(s.segment_id, 0.0) or 0.0)
            items.append({"file": sp.name, "start": max(0.0, s.start + off), "end": s.end + off,
                          "duration": get_media_duration(sp)})
        if not items:
            return {"error": "No generated line audio found. Run Generate once first."}
        max_segment_end = max(i["end"] for i in items)
        max_played_end = max(i["start"] + i["duration"] for i in items)
        if req.duration_mode == "extend":
            final_duration = max(req.total_duration, max_played_end, max_segment_end)
        else:
            final_duration = req.total_duration if req.total_duration > 0 else max(max_segment_end, max_played_end)
        adjusted = []
        cuts = 0
        for i, item in enumerate(items):
            allowed_end = items[i + 1]["start"] - 0.02 if i + 1 < len(items) else final_duration
            allowed = allowed_end - item["start"]
            if allowed <= 0.05:
                continue
            if item["duration"] > allowed + 0.05:
                cuts += 1
            item["allowed_duration"] = min(item["duration"], allowed)
            adjusted.append(item)
        if not adjusted:
            return {"error": "No lines fit the timeline."}
        inputs = ["-f", "lavfi", "-t", str(final_duration), "-i", "anullsrc=r=44100:cl=stereo"]
        filter_parts = []
        for idx, item in enumerate(adjusted):
            input_index = idx + 1
            inputs.extend(["-i", str(OUTPUT_DIR / item["file"])])
            delay_ms = int(item["start"] * 1000)
            allowed = max(item["allowed_duration"], 0.05)
            if item["duration"] > allowed + 0.05:
                fade_start = max(0, allowed - 0.2)
                filter_parts.append(f"[{input_index}]aformat=channel_layouts=stereo,atrim=0:{allowed:.3f},afade=t=out:st={fade_start:.3f}:d=0.2,asetpts=PTS-STARTPTS,adelay={delay_ms}|{delay_ms},apad[a{idx}]")
            else:
                filter_parts.append(f"[{input_index}]aformat=channel_layouts=stereo,atrim=0:{allowed:.3f},asetpts=PTS-STARTPTS,adelay={delay_ms}|{delay_ms},apad[a{idx}]")
        mix_inputs = "".join([f"[a{i}]" for i in range(len(adjusted))])
        filter_parts.append(f"[0]{mix_inputs}amix=inputs={len(adjusted) + 1}:duration=first:normalize=0[out]")
        filter_complex = ";".join(filter_parts)
        output_file = OUTPUT_DIR / "final_dubbed.mp3"
        run_ffmpeg(["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[out]", "-t", str(final_duration), str(output_file)])
        return {"status": "success", "segments_generated": len(adjusted), "duration_cuts": cuts,
                "final_duration": round(final_duration, 2)}
    except Exception as e:
        return {"error": friendly_error(e)}