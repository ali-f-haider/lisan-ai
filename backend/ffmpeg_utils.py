import subprocess
import sys
from pathlib import Path


def run_ffmpeg(cmd):
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.decode(errors="ignore") if e.stderr else str(e)
        raise Exception(error_message)


def get_video_resolution(file_path: Path):
    """Returns (width, height) of the first video stream, or None if it
    can't be read (missing file, audio-only file, corrupt stream, etc --
    callers should treat None as "unknown" and fall back to a safe
    default rather than raising)."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        str(file_path)
    ]
    try:
        result = subprocess.check_output(cmd, timeout=30).decode().strip()
        w, h = result.split("x")
        return int(w), int(h)
    except Exception:
        return None


def get_media_duration(file_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path)
    ]
    result = subprocess.check_output(cmd).decode().strip()
    return float(result)


def trim_media(src_path, out_path, start: float, duration: float, is_video: bool):
    """Cuts one section (start .. start+duration seconds) out of a longer
    upload and writes it to out_path. Added so a user with a 5-10 minute
    video can pick the 15/30-second part to dub instead of being turned
    away at the upload limit -- everything downstream (Whisper, Demucs,
    cloning, TTS) then only ever sees the short slice, exactly like a
    normal short upload, so memory use is unchanged.

    -ss BEFORE -i seeks to the nearest keyframe cheaply and, because the
    section is re-encoded (not stream-copied), ffmpeg then decodes forward
    to the exact requested frame -- a stream copy would snap to a keyframe
    and could be off by several seconds. Video is bounded to a 1920x1920
    box (1080p landscape AND portrait pass through untouched; 4K is scaled
    down) with dimensions forced even, which libx264/yuv420p requires.
    Audio-only sources come out as PCM wav (in AUDIO_EXTS, universally
    readable downstream).
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{float(start):.3f}", "-i", str(src_path),
        "-t", f"{float(duration):.3f}",
    ]
    if is_video:
        cmd += [
            "-map", "0:v:0?", "-map", "0:a:0?",
            "-vf", "scale='min(iw,1920)':'min(ih,1920)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
        ]
    else:
        cmd += ["-vn", "-c:a", "pcm_s16le"]
    cmd.append(str(out_path))
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.decode(errors="ignore") if e.stderr else str(e)
        raise Exception(error_message)


def extract_audio_from_video(video_path: str, output_audio_path: str):
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2",
        str(output_audio_path)
    ]
    run_ffmpeg(cmd)


def normalize_audio_for_diarization(input_path: str) -> str:
    input_file = Path(input_path)
    normalized_path = input_file.with_name(input_file.stem + "_normalized.wav")
    cmd = [
        "ffmpeg", "-y", "-i", str(input_file),
        "-ac", "1", "-ar", "16000",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        str(normalized_path)
    ]
    run_ffmpeg(cmd)
    return str(normalized_path)


def cut_audio_segment(input_path: str, start: float, duration: float,
                      out_path: Path, sample_rate: int = 16000, channels: int = 1):
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start), "-t", str(duration),
        "-i", str(input_path),
        "-ar", str(sample_rate), "-ac", str(channels),
        str(out_path)
    ]
    run_ffmpeg(cmd)


def concat_audio_files(file_list, out_path: Path):
    list_file = out_path.with_suffix(".txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for cf in file_list:
            safe_path = str(cf).replace("\\", "/").replace("'", "'\''")
            f.write(f"file '{safe_path}'\n")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)]
    try:
        run_ffmpeg(cmd)
    finally:
        try:
            list_file.unlink()
        except Exception:
            pass


def separate_vocals(input_audio_path: str, output_dir: str):
    cmd = [
        sys.executable, "-m", "demucs.separate",
        "--two-stems", "vocals",
        "-o", str(output_dir),
        str(input_audio_path)
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        error_message = e.stderr if e.stderr else str(e)
        raise Exception(f"Demucs vocal separation failed: {error_message}")
    input_name = Path(input_audio_path).stem
    vocals_path = Path(output_dir) / "htdemucs" / input_name / "vocals.wav"
    background_path = Path(output_dir) / "htdemucs" / input_name / "no_vocals.wav"
    if not vocals_path.exists():
        htdemucs_dir = Path(output_dir) / "htdemucs"
        if htdemucs_dir.exists():
            for subfolder in htdemucs_dir.iterdir():
                if subfolder.is_dir():
                    vp = subfolder / "vocals.wav"
                    bp = subfolder / "no_vocals.wav"
                    if vp.exists():
                        vocals_path = vp
                        background_path = bp
                        break
    return str(vocals_path), str(background_path)


def compress_video_for_upload(video_path: Path, out_path: Path):
    """Re-encodes the video before it goes to a lip-sync provider.
    Previously: 720p cap + CRF 30 on "veryfast" -- fairly aggressive,
    quality-losing settings. A lip-sync provider regenerates the
    mouth/lower-face region from whatever it's given, so feeding it a
    heavily downscaled, heavily compressed source gives it less real
    detail to work from than the original video actually has.
    Now: 1080p cap + CRF 20 (notably less lossy; x264's visually-lossless
    range starts around 18) on the "medium" preset -- better quality per
    byte than "veryfast" without jumping all the way to "slow", since this
    runs on the same Railway service the memory/CPU monitoring was just
    added for and shouldn't add a large CPU spike per job.
    Note: Sync Labs enforces a hard 20MB cap on the upload (see the
    "Video > 20MB" check in lipsync_service.py) -- these higher-quality
    settings produce meaningfully larger files than before, so a video
    that used to fit under that cap may not anymore. Not a concern for
    VEED/ElevenLabs, which have no such limit in this codebase.
    """
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        str(out_path)
    ]
    run_ffmpeg(cmd)


def mute_video_copy(video_path: Path, out_path: Path):
    """Re-muxes the video with its audio track dropped entirely -- video
    stream copied as-is (`-c:v copy`, no re-encode, so this is fast and
    lossless), just no `-c:a`/audio output at all.

    Added 2026-09-28 for the Wan 3.0 lip-sync call specifically: that
    provider is handed the source video as a VISUAL reference AND a
    separately-produced Arabic dub as the target audio, with the prompt
    explicitly telling it to use only the Arabic track and not touch the
    audio. But the reference video file itself (from
    compress_video_for_upload above) still carries the ORIGINAL English
    audio track -- so the model receives two audio signals for one request,
    and Ali got a real result back with half a line in English and half in
    Arabic, consistent with some of that original track leaking through
    despite the prompt's instructions. This has nothing to do with the
    prompt's own wording (its "do not change the audio" instruction is
    about not letting the model resynthesize/alter the Arabic
    reference_audio that becomes the output -- a separate input, unaffected
    by this) -- it's a data-preparation step done before the request is
    even built, removing a second audio signal that was never meant to be
    used as the output audio in the first place. Only used for the copy
    staged to Wan 3.0; the general-purpose compressed copy other lip-sync
    providers use is untouched."""
    cmd = ["ffmpeg", "-y", "-i", str(video_path), "-c:v", "copy", "-an", str(out_path)]
    run_ffmpeg(cmd)


def mix_two_audio(main_audio: Path, bg_audio: Path, out_wav: Path,
                  main_vol: float = 1.0, bg_vol: float = 1.0, extra_audio: Path = None, extra_vol: float = 1.0):
    """Dubbed voice + background. With extra_audio (the laughter / applause layer) a third track is added.
    The background is NOT turned down here (bg_vol=1.0): its level is set beforehand from the ORIGINAL audio
    (bg_duck.prepare_background). A safety limiter at the end only catches peaks so the sum can never clip."""
    if extra_audio is not None and Path(extra_audio).exists():
        cmd = [
            "ffmpeg", "-y",
            "-i", str(main_audio), "-i", str(bg_audio), "-i", str(extra_audio),
            "-filter_complex",
            f"[0:a]volume={main_vol}[d];[1:a]volume={bg_vol}[b];[2:a]volume={extra_vol}[r];"
            "[d][b][r]amix=inputs=3:duration=first:normalize=0[m];[m]alimiter=limit=0.97:level=disabled[out]",
            "-map", "[out]",
            str(out_wav)
        ]
        run_ffmpeg(cmd)
        return
    cmd = [
        "ffmpeg", "-y",
        "-i", str(main_audio), "-i", str(bg_audio),
        "-filter_complex",
        f"[0:a]volume={main_vol}[d];[1:a]volume={bg_vol}[b];[d][b]amix=inputs=2:duration=first:normalize=0[m];[m]alimiter=limit=0.97:level=disabled[out]",
        "-map", "[out]",
        str(out_wav)
    ]
    run_ffmpeg(cmd)


def mux_audio_into_video(video_path: Path, audio_path: Path, out_path: Path):
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path), "-i", str(audio_path),
        "-c:v", "copy",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        str(out_path)
    ]
    run_ffmpeg(cmd)


def detect_silence_gaps(file_path, min_silence_sec: float = 0.6, noise_db: str = "-30dB"):
    """Real, audio-measured silence windows in a file (absolute seconds),
    via ffmpeg's silencedetect filter. Independent of Whisper entirely --
    Whisper's own per-word timestamps come from an attention-based DTW
    alignment that isn't silence-aware, so a genuine ~1-2s pause between
    phrases can come back compressed to a near-zero gap between the
    surrounding words' timestamps. This gives the auto-split-at-pauses
    feature (app.js: detectInternalPause) a second, ground-truth signal to
    fall back on when the word timestamps alone don't show the pause.
    Returns [] on any ffmpeg failure rather than raising -- this is a
    best-effort enhancement, never something that should break a
    transcription job."""
    # silencedetect logs its start/end events at "info" level -- -v error
    # (used elsewhere in this file) would silence exactly the output this
    # function needs, so this stays at the default verbosity and only
    # trims the per-frame progress spam instead.
    cmd = ["ffmpeg", "-nostats", "-i", str(file_path),
           "-af", f"silencedetect=noise={noise_db}:d={min_silence_sec}",
           "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception:
        return []
    gaps = []
    pending_start = None
    for line in (p.stderr or "").splitlines():
        if "silence_start:" in line:
            try:
                pending_start = float(line.split("silence_start:")[1].strip())
            except Exception:
                pending_start = None
        elif "silence_end:" in line and pending_start is not None:
            try:
                end_str = line.split("silence_end:")[1].split("|")[0].strip()
                gaps.append({"start": round(pending_start, 2), "end": round(float(end_str), 2)})
            except Exception:
                pass
            pending_start = None
    return gaps


def measure_loudness_db(file_path, start: float = None, duration: float = None):
    """Mean loudness (dB) of a file or a time slice, via ffmpeg volumedetect."""
    # volumedetect reports its result at the "info" log level: with "-v error" (as this used to be) nothing is printed
    # and the function always returned None, so no line ever got a measured original level.
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-v", "info"]
    if start is not None:
        cmd += ["-ss", str(max(0.0, float(start)))]
    cmd += ["-i", str(file_path)]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-vn", "-af", "volumedetect", "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
        import re
        m = re.search(r"mean_volume:\s*(-?[0-9.]+)\s*dB", p.stderr or "")
        if m:
            return float(m.group(1))
    except Exception:
        pass
    return None


def measure_speech_loudness_db(file_path, start, duration, spans):
    """Mean loudness (dB) of a time slice counting ONLY the moments where words are spoken (spans = [(start, end)]
    seconds, the speech map made at transcription). Laughter, applause and music inside the slice are left out.
    None when the slice holds no spoken moment or on any problem (the caller then uses measure_loudness_db)."""
    try:
        import numpy as np
        t0, t1 = float(start), float(start) + float(duration)
        inside = [(max(a, t0), min(z, t1)) for a, z in spans if z > t0 and a < t1]
        inside = [(a, z) for a, z in inside if z - a > 0.05]
        if not inside:
            return None
        rate = 16000
        p = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(max(0.0, t0)), "-t", str(t1 - t0), "-i", str(file_path),
                            "-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"], capture_output=True, timeout=120)
        x = np.frombuffer(p.stdout, dtype="<f4").astype(np.float64)
        if x.size < rate // 10:
            return None
        keep = np.zeros(x.size, dtype=bool)
        for a, z in inside:
            keep[int((a - t0) * rate):int((z - t0) * rate) + 1] = True
        if keep.sum() < rate // 20:
            return None
        ms = float(np.mean(x[keep] ** 2))
        return 10.0 * float(np.log10(ms + 1e-12))
    except Exception:
        return None
