"""Lightweight audio enhancement using scipy DSP filters.

Applies:
1. High-pass filter at 80Hz (remove rumble/microphone noise)
2. High-shelf boost +5dB above 2.5kHz (add crispness/presence)
3. Gentle limiter to prevent clipping

No ML models, no GPU, instant processing.
"""
import threading
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

_jobs = {}


def _high_shelf_filter(audio: np.ndarray, sr: int, shelf_freq: float = 2500.0,
                       gain_db: float = 5.0) -> np.ndarray:
    """Apply a high-shelf boost using a biquad filter."""
    # Convert to normalized frequency
    w0 = 2 * np.pi * shelf_freq / sr
    A = 10 ** (gain_db / 40)  # amplitude factor
    Q = 0.707  # Butterworth-like Q

    # High-shelf filter coefficients (from Audio EQ Cookbook)
    alpha = np.sin(w0) / 2 * np.sqrt((A + 1 / A) * (1 / Q - 1) + 2)
    cos_w0 = np.cos(w0)

    b0 = A * ((A + 1) + (A - 1) * cos_w0 + 2 * np.sqrt(A) * alpha)
    b1 = -2 * A * ((A - 1) + (A + 1) * cos_w0)
    b2 = A * ((A + 1) + (A - 1) * cos_w0 - 2 * np.sqrt(A) * alpha)
    a0 = (A + 1) - (A - 1) * cos_w0 + 2 * np.sqrt(A) * alpha
    a1 = 2 * ((A - 1) - (A + 1) * cos_w0)
    a2 = (A + 1) - (A - 1) * cos_w0 - 2 * np.sqrt(A) * alpha

    b = np.array([b0, b1, b2]) / a0
    a = np.array([1.0, a1 / a0, a2 / a0])

    return signal.lfilter(b, a, audio, axis=0)


def _high_pass_filter(audio: np.ndarray, sr: int, cutoff: float = 80.0) -> np.ndarray:
    """Apply a 4th-order Butterworth high-pass filter."""
    nyq = sr / 2
    normal_cutoff = cutoff / nyq
    b, a = signal.butter(4, normal_cutoff, btype='high', analog=False)
    return signal.lfilter(b, a, audio, axis=0)


def _limiter(audio: np.ndarray, threshold: float = 0.95) -> np.ndarray:
    """Simple soft-knee limiter to prevent clipping."""
    return np.tanh(audio / threshold) * threshold


def enhance_background(job_id: str, input_path: str, output_path: str):
    """Enhance background audio with DSP filters. Updates _jobs[job_id] with progress."""
    _jobs[job_id] = {"status": "processing", "percent": 0, "status_text": "Loading audio..."}
    try:
        audio, sr = sf.read(input_path)
        if audio.ndim == 1:
            audio = audio.reshape(-1, 1)

        _jobs[job_id] = {"status": "processing", "percent": 20, "status_text": "Removing low-frequency rumble..."}
        # High-pass to remove rumble
        audio = _high_pass_filter(audio, sr, cutoff=80.0)

        _jobs[job_id] = {"status": "processing", "percent": 50, "status_text": "Adding crispness (high-shelf boost)..."}
        # High-shelf boost for clarity
        audio = _high_shelf_filter(audio, sr, shelf_freq=2500.0, gain_db=5.0)

        _jobs[job_id] = {"status": "processing", "percent": 80, "status_text": "Applying limiter and saving..."}
        # Limiter to prevent clipping
        audio = _limiter(audio, threshold=0.95)

        # Save
        sf.write(output_path, audio, sr)

        _jobs[job_id] = {"status": "done", "percent": 100, "status_text": "Background enhanced!"}
    except Exception as e:
        _jobs[job_id] = {"status": "error", "percent": 0, "error": str(e)}
        # Fallback: copy original
        import shutil
        try:
            shutil.copy2(input_path, output_path)
        except Exception:
            pass


def get_progress(job_id: str) -> dict:
    return _jobs.get(job_id, {"status": "not_found"})


def start_enhance(job_id: str, input_path: str, output_path: str):
    t = threading.Thread(target=enhance_background, args=(job_id, input_path, output_path), daemon=True)
    t.start()