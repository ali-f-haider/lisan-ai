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
    measure_loudness_db,
)
from media_paths import resolve_job_audio

eleven_client = None
USER_GAINS = {}
OVERLAP_FLAGS = {}  # segment_id -> True: this line may be talked over (intruders not faded)  # job_id -> {segment_id: extra dB from Step 5.5 sliders}

def friendly_error(e):
    return str(e)

def _bake_gain(path: Path, gain_db: float) -> Path:
    """Apply a volume gain to a line file (returns final path, always .wav)."""
    if abs(gain_db) < 0.15:
        return path
    out = path.with_suffix(".wav")
    tmp = path.with_name(path.name + ".gaintmp.wav")
    run_ffmpeg([
        "ffmpeg", "-y", "-i", str(path),
        "-af", f"volume={gain_db:.2f}dB",
        "-ar", "44100", "-ac", "1", "-acodec", "pcm_s16le",
        str(tmp),
    ])
    try:
        if out != path and path.exists():
            path.unlink()
        tmp.replace(out)
        return out
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        return path

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
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode(errors="ignore")
        except Exception:
            error_body = str(e)
        return {"error": f"ElevenLabs API error {e.code}: {error_body}"}
    except Exception as e:
        return {"error": str(e)}

def record_gemini(job_id, data):
    if not isinstance(data, dict):
        return
    u = data.get("usageMetadata") or {}
    b = usage_bucket(job_id)
    b["gemini_in"] += int(u.get("promptTokenCount", 0) or 0)
    b["gemini_out"] += int(u.get("candidatesTokenCount", 0) or 0)
    b["gemini_thoughts"] += int(u.get("thoughtsTokenCount", 0) or 0)

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
                    print(f"Warning: failed to cut clone sample for {speaker}: {cut_err}")
                    if cut_file.exists():
                        cut_file.unlink()
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
                                    if cf.exists():
                                        cf.unlink()
                                cut_files = [fallback_file]
                                total_valid_duration = fallback_actual
                            else:
                                if fallback_file.exists():
                                    fallback_file.unlink()
                except Exception as fallback_err:
                    print(f"Warning: fallback clone cut failed for {speaker}: {fallback_err}")
            if total_valid_duration < 1.0 or not cut_files:
                cloned_voices[speaker] = f"ERROR: Not enough valid audio for {speaker} ({total_valid_duration:.2f}s)."
                for cf in cut_files:
                    if cf.exists():
                        cf.unlink()
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
                if concat_file.exists():
                    concat_file.unlink()
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
                    try:
                        cf.unlink()
                    except Exception:
                        pass
            if concat_file is not None and concat_file.exists():
                try:
                    concat_file.unlink()
                except Exception:
                    pass
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

def _mix_filter_part(input_index, allowed, delay_ms, gdb, trim):
    vol = f"volume={10 ** (gdb / 20.0):.4f}," if abs(gdb) > 0.05 else ""
    if trim:
        fade_start = max(0, allowed - 0.06)
        return (f"[{input_index}]{vol}aformat=channel_layouts=stereo,atrim=0:{allowed:.3f},"
                f"afade=t=out:st={fade_start:.3f}:d=0.06,asetpts=PTS-STARTPTS,"
                f"adelay={delay_ms}|{delay_ms},apad[a{input_index - 1}]")
    return (f"[{input_index}]{vol}aformat=channel_layouts=stereo,atrim=0:{allowed:.3f},"
            f"asetpts=PTS-STARTPTS,adelay={delay_ms}|{delay_ms},apad[a{input_index - 1}]")

def generate_worker(req):
    global eleven_client
    global OVERLAP_FLAGS
    OVERLAP_FLAGS = dict(getattr(req, 'overlap_allowed', None) or {})
    try:
        jobs_progress["generate"] = {"status": "processing", "percent": 0, "result": None, "error": None}
        total_segments = len(req.segments)
        if total_segments == 0:
            raise Exception("No segments found.")
        bucket = usage_bucket(req.job_id)
        sorted_segments = sorted(req.segments, key=lambda s: s.start)
        generated_files = []
        lines_meta = []
        src_for_loudness = resolve_job_audio(req.job_id)
        # Wipe line files from any previous job so stale audio can never leak in
        for stale in OUTPUT_DIR.glob("*_stretched.*"):
            try:
                stale.unlink()
            except Exception:
                pass
        for stale in OUTPUT_DIR.glob("*_raw.*"):
            try:
                stale.unlink()
            except Exception:
                pass
        for i, seg in enumerate(sorted_segments):
            if not seg.arabic_text.strip():
                continue
            target_duration = max(seg.end - seg.start, 0.5)
            tts_text = seg.arabic_text
            if req.tts_provider == "gemini":
                if not req.gemini_api_key:
                    raise Exception("Missing Gemini API key.")
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={req.gemini_api_key}"
                payload = {"contents": [{"parts": [{"text": tts_text}]}],
                           "generationConfig": {"responseModalities": ["AUDIO"],
                                                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": req.gemini_voice or "Kore"}}}}}
                request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=120) as response:
                    data = json.load(response)
                record_gemini(req.job_id, data)
                audio_bytes = base64.b64decode(data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
                raw_filename = f"{seg.segment_id}_raw.wav"
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
                response = eleven_client.text_to_speech.convert(text=tts_text, voice_id=voice_id, model_id="eleven_v3")
                audio_bytes = response if isinstance(response, bytes) else b"".join(chunk for chunk in response if chunk)
                raw_filename = f"{seg.segment_id}_raw.mp3"
            raw_path = OUTPUT_DIR / raw_filename
            raw_path.write_bytes(audio_bytes)
            actual_duration = get_media_duration(raw_path)
            if actual_duration <= 0:
                actual_duration = target_duration
            required_tempo = actual_duration / target_duration
            if req.tempo_mode == "excellent":
                min_tempo, max_tempo = 0.95, 1.10
            elif req.tempo_mode == "good":
                min_tempo, max_tempo = 0.85, 1.25
            else:
                min_tempo, max_tempo = 0.75, 1.35
            needs_warning = False
            if required_tempo < min_tempo:
                tempo = 1.0
            elif required_tempo > max_tempo:
                tempo = max_tempo
                needs_warning = True
            else:
                tempo = required_tempo
            stretched_filename = f"{seg.segment_id}_stretched.wav" if req.tts_provider == "gemini" else f"{seg.segment_id}_stretched.mp3"
            stretched_path = OUTPUT_DIR / stretched_filename
            if abs(tempo - 1.0) > 0.02:
                run_ffmpeg(["ffmpeg", "-y", "-i", str(raw_path), "-filter:a", f"atempo={tempo:.6f}", str(stretched_path)])
            else:
                import shutil
                shutil.copy(raw_path, stretched_path)
            stretched_duration = get_media_duration(stretched_path)
            if stretched_duration <= 0:
                stretched_duration = target_duration
            # ---- Volume match: separated original vocals vs generated line ----
            orig_db = None
            dub_db = None
            auto_gain = 0.0
            try:
                if src_for_loudness is not None:
                    orig_db = measure_loudness_db(str(src_for_loudness), seg.start, target_duration)
                dub_db = measure_loudness_db(str(stretched_path))
                if orig_db is not None and dub_db is not None and orig_db > -60 and dub_db > -60:
                    auto_gain = max(-10.0, min(10.0, orig_db - dub_db))
            except Exception:
                auto_gain = 0.0
            lines_meta.append({"segment_id": seg.segment_id, "speaker": seg.speaker,
                               "start": seg.start, "end": seg.end,
                               "orig_db": round(orig_db, 1) if orig_db is not None else None,
                               "dub_db": round(dub_db, 1) if dub_db is not None else None,
                               "auto_gain_db": round(auto_gain, 1), "duration": round(stretched_duration, 3)})
            generated_files.append({"file": stretched_filename, "sid": seg.segment_id, "start": seg.start,
                                    "end": seg.end, "speaker": seg.speaker, "duration": stretched_duration,
                                    "tempo_warning": needs_warning})
            jobs_progress["generate"]["percent"] = int(((i + 1) / total_segments) * 90)
        if not generated_files:
            raise Exception("No Arabic text found.")
        USER_GAINS[req.job_id] = {lm["segment_id"]: float(lm["auto_gain_db"]) for lm in lines_meta}
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
            # PATCHED: global overlap check
            allowed_end = final_duration
            for _j in range(len(generated_files)):
                if _j == i: continue
                _other_start = generated_files[_j]["start"]
                if _other_start > item["start"] and _other_start - 0.005 < allowed_end:
                    allowed_end = _other_start - 0.005
            allowed_duration = allowed_end - item["start"]
            if allowed_duration <= 0.02:
                continue
            if item["duration"] > allowed_duration + 0.02:
                cut_count += 1
            item["allowed_duration"] = min(item["duration"], allowed_duration)
            adjusted_files.append(item)
        if not adjusted_files:
            raise Exception("No generated segments fit.")
        inputs = ["-f", "lavfi", "-t", str(final_duration), "-i", "anullsrc=r=44100:cl=stereo"]
        filter_parts = []
        active_gen = dict(USER_GAINS.get(req.job_id or "", {}))
        for idx, item in enumerate(adjusted_files):
            input_index = idx + 1
            inputs.extend(["-i", str(OUTPUT_DIR / item["file"])])
            delay_ms = int(item["start"] * 1000)
            allowed = max(item["allowed_duration"], 0.05)
            gdb = float(active_gen.get(item.get("sid", ""), 0.0) or 0.0)
            trim = item["duration"] > allowed + 0.02
            filter_parts.append(_mix_filter_part(input_index, allowed, delay_ms, gdb, trim))
        mix_inputs = "".join([f"[a{i}]" for i in range(len(adjusted_files))])
        filter_parts.append(f"[0]{mix_inputs}amix=inputs={len(adjusted_files) + 1}:duration=first:normalize=0[out]")
        filter_complex = ";".join(filter_parts)
        output_file = OUTPUT_DIR / "final_dubbed.mp3"
        run_ffmpeg(["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[out]", "-t", str(final_duration), str(output_file)])
        warning_count = sum(1 for f in adjusted_files if f.get("tempo_warning"))
        result = {"status": "success", "output_folder": str(OUTPUT_DIR), "final_file": str(output_file),
                  "segments_generated": len(adjusted_files), "tempo_warnings": warning_count,
                  "duration_cuts": cut_count, "final_duration": round(final_duration, 2),
                  "eleven_credits_used": bucket["eleven_chars"], "lines": lines_meta}
        jobs_progress["generate"].update({"status": "done", "percent": 100, "result": result, "error": None})
    except Exception as e:
        jobs_progress["generate"] = {"status": "error", "percent": 0, "error": str(e), "result": None}

def rebuild_final_mix(segments, total_duration, duration_mode="exact", job_id=None, flags=None):
    global OVERLAP_FLAGS
    if flags: OVERLAP_FLAGS = dict(flags)
    """Rebuild final_dubbed.mp3 from existing line files (.wav OR .mp3), applying Step 5.5 gains."""
    active = dict(USER_GAINS.get(job_id or "", {}))
    segs = sorted([s for s in segments if (s.arabic_text or "").strip()], key=lambda s: s.start)
    items = []
    for s in segs:
        sp = OUTPUT_DIR / f"{s.segment_id}_stretched.wav"
        if not sp.exists():
            sp = OUTPUT_DIR / f"{s.segment_id}_stretched.mp3"
        if not sp.exists():
            continue
        items.append({"file": sp.name, "sid": s.segment_id, "start": s.start, "end": s.end,
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
        # PATCHED: global overlap check
        allowed_end = final_duration
        for _j in range(len(items)):
            if _j == i: continue
            _other_start = items[_j]["start"]
            if _other_start > item["start"] and _other_start - 0.005 < allowed_end:
                allowed_end = _other_start - 0.005
        allowed = allowed_end - item["start"]
        if allowed <= 0.02:
            continue
        if item["duration"] > allowed + 0.02:
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
        gdb = float(active.get(item["sid"], 0.0) or 0.0)
        trim = item["duration"] > allowed + 0.02
        filter_parts.append(_mix_filter_part(input_index, allowed, delay_ms, gdb, trim))
    mix_inputs = "".join([f"[a{i}]" for i in range(len(adjusted))])
    filter_parts.append(f"[0]{mix_inputs}amix=inputs={len(adjusted) + 1}:duration=first:normalize=0[out]")
    filter_complex = ";".join(filter_parts)
    output_file = OUTPUT_DIR / "final_dubbed.mp3"
    run_ffmpeg(["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[out]", "-t", str(final_duration), str(output_file)])
    return {"segments_generated": len(adjusted), "duration_cuts": cuts,
            "final_duration": round(final_duration, 2)}

def regenerate_line(req):
    """Re-speak ONE segment with TTS, stretch it into its window, volume-match it, then rebuild the mix."""
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
        if req.tempo_mode == "excellent":
            min_tempo, max_tempo = 0.95, 1.10
        elif req.tempo_mode == "good":
            min_tempo, max_tempo = 0.85, 1.25
        else:
            min_tempo, max_tempo = 0.75, 1.35
        warning = False
        if required < min_tempo:
            tempo = 1.0
        elif required > max_tempo:
            tempo = max_tempo
            warning = True
        else:
            tempo = required
        stretched = OUTPUT_DIR / f"{seg.segment_id}_stretched.wav"
        cmd = ["ffmpeg", "-y", "-i", str(raw_path)]
        if abs(tempo - 1.0) > 0.02:
            cmd += ["-filter:a", f"atempo={tempo:.6f}"]
        cmd += ["-acodec", "pcm_s16le", str(stretched)]
        run_ffmpeg(cmd)
        # Volume-match the re-spoken line to the original vocal slice (mix-time gain)
        try:
            src = resolve_job_audio(req.job_id)
            orig_db = measure_loudness_db(str(src), seg.start, target_duration) if src else None
            dub_db = measure_loudness_db(str(stretched))
            if orig_db is not None and dub_db is not None and orig_db > -60 and dub_db > -60:
                USER_GAINS.setdefault(req.job_id, {})[seg.segment_id] = round(max(-10.0, min(10.0, orig_db - dub_db)), 1)
        except Exception:
            pass
        mix = rebuild_final_mix(req.segments, req.total_duration, req.duration_mode, job_id=req.job_id, flags=getattr(req, "overlap_allowed", None))
        return {"status": "success",
                "stretched_duration": round(get_media_duration(stretched), 2),
                "target": round(target_duration, 2),
                "tempo_warning": warning,
                "mix": mix}
    except Exception as e:
        return {"error": friendly_error(e)}

def remix_with_offsets(req):
    """Rebuild final_dubbed.mp3 applying per-segment time offsets AND Step 5.5 volume gains."""
    global OVERLAP_FLAGS
    OVERLAP_FLAGS = dict(getattr(req, 'overlap_allowed', None) or {})
    try:
        gains = dict(getattr(req, "gains", None) or {})
        if gains:
            USER_GAINS[req.job_id] = {k: float(v) for k, v in gains.items()}
        active = dict(USER_GAINS.get(req.job_id or "", {}))
        segs = sorted([s for s in req.segments if (s.arabic_text or "").strip()], key=lambda s: s.start)
        items = []
        for s in segs:
            sp = OUTPUT_DIR / f"{s.segment_id}_stretched.wav"
            if not sp.exists():
                sp = OUTPUT_DIR / f"{s.segment_id}_stretched.mp3"
            if not sp.exists():
                continue
            off = float((req.offsets or {}).get(s.segment_id, 0.0) or 0.0)
            items.append({"file": sp.name, "sid": s.segment_id, "start": max(0.0, s.start + off),
                          "end": s.end + off, "duration": get_media_duration(sp)})
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
            # PATCHED: global overlap check
            allowed_end = final_duration
            for _j in range(len(items)):
                if _j == i: continue
                _other_start = items[_j]["start"]
                if _other_start > item["start"] and _other_start - 0.005 < allowed_end:
                    allowed_end = _other_start - 0.005
            allowed = allowed_end - item["start"]
            if allowed <= 0.02:
                continue
            if item["duration"] > allowed + 0.02:
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
            gdb = float(active.get(item["sid"], 0.0) or 0.0)
            trim = item["duration"] > allowed + 0.02
            filter_parts.append(_mix_filter_part(input_index, allowed, delay_ms, gdb, trim))
        mix_inputs = "".join([f"[a{i}]" for i in range(len(adjusted))])
        filter_parts.append(f"[0]{mix_inputs}amix=inputs={len(adjusted) + 1}:duration=first:normalize=0[out]")
        filter_complex = ";".join(filter_parts)
        output_file = OUTPUT_DIR / "final_dubbed.mp3"
        run_ffmpeg(["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[out]", "-t", str(final_duration), str(output_file)])
        return {"status": "success", "segments_generated": len(adjusted), "duration_cuts": cuts,
                "final_duration": round(final_duration, 2)}
    except Exception as e:
        return {"error": friendly_error(e)}

def cleanup_cloned_voices(api_key: str, keep_ids: list = None) -> dict:
    keep = set(keep_ids or [])
    try:
        req = urllib.request.Request("https://api.elevenlabs.io/v1/voices?page_size=100", headers={"xi-api-key": api_key})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        deleted, errors = 0, []
        for v in data.get("voices", []):
            name = v.get("name") or ""
            vid = v.get("voice_id")
            if not name.startswith(("Cloned_", "Custom_")) or vid in keep:
                continue
            try:
                dreq = urllib.request.Request(f"https://api.elevenlabs.io/v1/voices/{vid}", method="DELETE", headers={"xi-api-key": api_key})
                with urllib.request.urlopen(dreq, timeout=30) as dr:
                    dr.read()
                deleted += 1
            except Exception as e:
                errors.append(f"{name}: {e}")
        return {"deleted": deleted, "errors": errors}
    except Exception as e:
        return {"deleted": 0, "errors": [str(e)]}

def add_custom_voice(job_id: str, speaker: str, src_path, api_key: str):
    safe = "".join(c for c in speaker if c.isalnum()).strip() or "spk"
    wav = OUTPUT_DIR / f"custom_{job_id}_{safe}.wav"
    run_ffmpeg(["ffmpeg", "-y", "-i", str(src_path), "-vn", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le", str(wav)])
    dur = get_media_duration(wav)
    if dur > 20.5:
        try:
            wav.unlink()
        except Exception:
            pass
        return f"ERROR: Clip is {dur:.1f}s — the limit is 20 seconds."
    if dur < 1.0:
        pad = OUTPUT_DIR / f"custom_{job_id}_{safe}_pad.wav"
        run_ffmpeg(["ffmpeg", "-y", "-i", str(wav), "-af", f"apad=pad_dur={max(0.2, 1.15 - dur)}", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le", str(pad)])
        try:
            wav.unlink()
        except Exception:
            pass
        wav = pad
    with open(wav, "rb") as f:
        file_data = f.read()
    http = urllib3.PoolManager()
    resp = http.request("POST", "https://api.elevenlabs.io/v1/voices/add",
                        headers={"xi-api-key": api_key},
                        fields={"name": f"Custom_{speaker}", "files": (wav.name, file_data, "audio/wav"), "labels": "{}"})
    try:
        wav.unlink()
    except Exception:
        pass
    if resp.status == 200:
        return json.loads(resp.data.decode()).get("voice_id", "ERROR: no voice_id returned")
    return f"ERROR: Voice engine rejected the clip (status {resp.status})."