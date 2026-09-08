"""AudioSR background enhancement service."""
import os
import threading
from pathlib import Path

_jobs = {}


def enhance_background(job_id: str, input_path: str, output_path: str):
    """Run AudioSR on the background track. Updates _jobs[job_id] with progress."""
    _jobs[job_id] = {"status": "processing", "percent": 0, "status_text": "Loading AudioSR model..."}
    try:
        import audiosr
        _jobs[job_id] = {"status": "processing", "percent": 10, "status_text": "Enhancing background audio to 48kHz..."}
        result = audiosr.super_resolution(
            input_path,
            seed=42,
            device="cpu"
        )
        _jobs[job_id] = {"status": "processing", "percent": 80, "status_text": "Saving enhanced audio..."}
        import soundfile as sf
        import numpy as np
        if hasattr(result, 'cpu'):
            audio_np = result.cpu().numpy().squeeze()
        else:
            audio_np = np.array(result).squeeze()
        sf.write(output_path, audio_np, 48000)
        _jobs[job_id] = {"status": "done", "percent": 100, "status_text": "Background enhanced!"}
    except ImportError:
        _jobs[job_id] = {"status": "done", "percent": 100, "status_text": "AudioSR not available — using original background."}
        # Fallback: just copy the original
        import shutil
        shutil.copy2(input_path, output_path)
    except Exception as e:
        _jobs[job_id] = {"status": "error", "percent": 0, "error": str(e)}
        import shutil
        shutil.copy2(input_path, output_path)


def get_progress(job_id: str) -> dict:
    return _jobs.get(job_id, {"status": "not_found"})


def start_enhance(job_id: str, input_path: str, output_path: str):
    t = threading.Thread(target=enhance_background, args=(job_id, input_path, output_path), daemon=True)
    t.start()