import subprocess
import sys
from pathlib import Path


def run_ffmpeg(cmd):
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.decode(errors="ignore") if e.stderr else str(e)
        raise Exception(error_message)


def get_media_duration(file_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path)
    ]
    result = subprocess.check_output(cmd).decode().strip()
    return float(result)


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


def mix_two_audio(main_audio: Path, bg_audio: Path, out_wav: Path,
                  main_vol: float = 1.0, bg_vol: float = 0.8):
    cmd = [
        "ffmpeg", "-y",
        "-i", str(main_audio), "-i", str(bg_audio),
        "-filter_complex",
        f"[0:a]volume={main_vol}[d];[1:a]volume={bg_vol}[b];[d][b]amix=inputs=2:duration=first:normalize=0[out]",
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


def detect_silence_gaps(file_path, min_silence_sec: float = 0.4, noise_db: str = "-30dB"):
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
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(file_path)]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-af", "volumedetect", "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
        import re
        m = re.search(r"mean_volume:\s*(-?[0-9.]+)\s*dB", p.stderr or "")
        if m:
            return float(m.group(1))
    except Exception:
        pass
    return None