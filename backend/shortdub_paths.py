"""Job-scoped line audio names; never read the old shared seg_0 files."""
import hashlib
import re
import threading
from pathlib import Path

_JOB = re.compile(r"[A-Za-z0-9_-]{20,100}")
_operations_lock = threading.Lock()
_active_operations = set()


def begin_operation(job_id):
    with _operations_lock:
        if job_id in _active_operations:
            return False
        _active_operations.add(job_id)
        return True


def finish_operation(job_id):
    with _operations_lock:
        _active_operations.discard(job_id)


def operation_active(job_id):
    with _operations_lock:
        return job_id in _active_operations


def line_audio_path(output_dir, job_id, segment_id, kind, suffix):
    if not _JOB.fullmatch(str(job_id or "")):
        raise ValueError("bad job id")
    if not segment_id or kind not in ("raw", "stretched") or suffix not in (".mp3", ".wav", ".pcm", ""):
        raise ValueError("bad line audio path")
    # Hash the segment ID so filenames remain safe on Windows and Linux,
    # including older projects containing spaces or Unicode segment IDs.
    sid = hashlib.sha256(str(segment_id).encode("utf-8")).hexdigest()
    return Path(output_dir) / f"{job_id}_line_{sid}_{kind}{suffix}"


def clear_line_audio(output_dir, job_id):
    # Validate before enumerating; match the entire name, not a job-ID prefix.
    line_audio_path(output_dir, job_id, "validation", "raw", ".wav")
    pattern = re.compile(re.escape(str(job_id)) + r"_line_[0-9a-f]{64}_(?:raw|stretched)\.(?:mp3|wav|pcm)")
    for path in Path(output_dir).iterdir():
        if path.is_file() and pattern.fullmatch(path.name):
            path.unlink()
