"""Dub Long Video -- engine for videos of several minutes (the normal Step 1
flow is limited to 15/30 seconds because everything in it works on one clip
held in memory at once).

How this keeps the server calm
------------------------------
* The upload arrives in small resumable chunks (never one giant request).
* Every heavy step works on a ~40 second PIECE, one piece at a time, using the
  same Demucs / Whisper / pyannote tools the short flow already uses -- so the
  peak memory is the same as for a normal 30-second clip.
* Every heavy unit runs inside the SAME one-at-a-time slot the short flow uses
  (whisper_service._transcribe_queue). Between pieces a long job checks whether
  anybody else is waiting and, if so, gives the slot up and re-queues behind
  them, so a 10-minute job never blocks someone's 20-second clip for more than
  one piece.
* All state lives in one small job.json per job on the persistent volume
  (DATA_DIR/longjobs/<job_id>/), written atomically after every checkpoint, so
  a redeploy/restart resumes exactly where the job stopped instead of losing it.

Nothing in here touches the short flow's files: working files live in their own
folder, which the app's periodic clean-up (it only looks at uploads/ and
outputs/ top-level files) never sees.

Money is handled through Hooks (set once by main.py) so this module never has
to import main.py.
"""
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

from config import DATA_DIR, OUTPUT_DIR, GEMINI_API_KEY, HF_TOKEN, INWORLD_API_KEY
import ffmpeg_utils
import voice_clean
import bg_duck

LONG_DIR = DATA_DIR / "longjobs"
LONG_DIR.mkdir(parents=True, exist_ok=True)

VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm", ".avi")
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")
ALLOWED_EXTS = VIDEO_EXTS + AUDIO_EXTS

CHUNK_BYTES = 8 * 1024 * 1024            # upload chunk size the page uses
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB hard ceiling on the source file
MIN_SEC = 20.0                           # shorter than this: use the normal app
DEFAULT_MAX_MIN = 10                     # overridden by admin (longDubMaxMin)
MAX_ACTIVE_PER_USER = 2                  # unfinished long jobs one user may hold at once
MAX_CONCURRENT_WORKERS = 2               # long jobs running at once, whole server
MAX_SEGMENTS = 1500                      # sanity ceiling on transcript lines

PIECE_TARGET = 40.0                      # aim for ~40 s pieces...
PIECE_MIN = 20.0                         # ...never shorter than this (except the last)
PIECE_MAX = 55.0                         # ...never longer than this
TRANSLATE_BATCH = 15                     # lines per translation request

# Estimate assumptions (before the real text exists): a minute of speech is
# about 1000 characters of dubbed Arabic including the emotion tags, and two
# speakers. The exact price is computed from the real edited text later.
EST_CHARS_PER_MIN = 1000
EST_SPEAKERS = 2

SAMPLE_RATE = 44100

MAX_SPEAKERS = 8
# Bump when the wording of what the user is told with the offer changes, so a
# complaint can be matched to the exact terms that were shown.
TERMS_VERSION = "2026-10-03"

# Time estimates: how many seconds of work per second of video, until real
# measurements from finished jobs replace these (see _record_speed).
DEFAULT_SPEED = {"analysis": 4.0, "dubbing": 0.8, "lipsync": 60.0}   # seconds of waiting per second of video (lip-sync: per clip second; the engine needs ~15 min per 15 s clip)
DUB_BASE_SEC = 120          # fixed part of a dubbing run (start-up, clones, final assembly)

# Lip-sync (optional). The lip-sync engine (Wan 3.0, see lipsync_service.py)
# takes clips of 4-15 seconds only, so a long video is lip-synced clip by clip:
# only the stretches where somebody speaks, each clip at most LIPSYNC_MAX_CLIP
# seconds, a few at a time, then everything is joined back into one video.
LIPSYNC_MIN_CLIP = 4.2      # engine minimum is 4 s; a little margin for frame rounding
LIPSYNC_MAX_CLIP = 14.5     # engine maximum is 15 s
LIPSYNC_HEAD = 0.2          # seconds of picture before the first word of a clip
LIPSYNC_TAIL = 0.6          # ...and after the last one (dubbed lines may run a bit long)
LIPSYNC_PARALLEL = 3        # clips of one job at the engine at the same time
LIPSYNC_MAX_SIZE = (1280, 720)   # lip-synced videos are delivered in up to 720p, the size the price is calibrated for
DEFAULT_LIPSYNC_MAX_MIN = 3      # overridden by admin (longDubLipsyncMaxMin)
LIPSYNC_RETRY_WAITS = (0, 30, 90)
_LIPSYNC_SLOTS = threading.Semaphore(4)   # clips at the engine at once, whole server
_FF_LOCAL = threading.Semaphore(1)        # local video encodes, one at a time, whole server

_LOCK = threading.RLock()
_JOBS = {}          # job_id -> job dict (memory copy of job.json)
_JOB_LOCKS = {}     # job_id -> Lock (serialises chunk writes / saves per job)
_RUNNING = set()    # job_ids that currently have a worker thread
_worker_slots = threading.Semaphore(MAX_CONCURRENT_WORKERS)
_slot_state = threading.local()      # .held -- does this worker thread hold one of the slots?


class Hooks:
    """Set once by main.py -- see configure(). Defaults do nothing so the
    module can also be imported by tests."""
    get_credits = staticmethod(lambda uid: None)
    charge = staticmethod(lambda uid, amount, action, job_id, seconds=None: True)
    refund = staticmethod(lambda uid, amount, job_id: True)
    send_email = staticmethod(lambda uid, subject, text: False)
    email_error = staticmethod(lambda: "")       # why the last send_email failed (for the event log)
    pricing = staticmethod(lambda: {})
    # log_event(uid, job_id, step, status, detail, credits) writes one row to
    # the long_dub_events table (best effort, never raises).
    log_event = staticmethod(lambda uid, job_id, step, status, detail="", credits=None: None)
    # allowed(uid) -> (True, "") or (False, "message"): Studio-plan check.
    allowed = staticmethod(lambda uid: (True, ""))


def configure(**kwargs):
    for k, v in kwargs.items():
        setattr(Hooks, k, staticmethod(v))


# ------------------------------------------------------------------ helpers

def _now():
    return time.time()


def _ev(job, step, status="ok", detail="", credits=None):
    """One line in the job's permanent record (long_dub_events table) -- what
    happened, whether it worked, and why not. Also printed to the server log.
    Never raises: logging must never break the job itself."""
    try:
        print(f"[longdub] {job['id']} {step} {status} {detail}"[:400])
        Hooks.log_event(job.get("uid"), job["id"], step, status, str(detail)[:1500], credits)
    except Exception:
        pass


def job_dir(job_id):
    return LONG_DIR / job_id


def _valid_id(job_id):
    return bool(job_id) and bool(re.fullmatch(r"[0-9a-f\-]{36}", str(job_id)))


def _lock_for(job_id):
    with _LOCK:
        lk = _JOB_LOCKS.get(job_id)
        if lk is None:
            lk = _JOB_LOCKS[job_id] = threading.RLock()
        return lk


def _save(job):
    """Atomic write of job.json (temp file + rename) so a crash mid-write can
    never leave a half-written state file."""
    job["updated"] = _now()
    d = job_dir(job["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "job.json.tmp"
    with _lock_for(job["id"]):
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / "job.json")


def load_job(job_id):
    """Memory copy first, then disk. Returns None when unknown/invalid."""
    if not _valid_id(job_id):
        return None
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            return job
        p = job_dir(job_id) / "job.json"
        if not p.exists():
            return None
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        _JOBS[job_id] = job
        return job


def list_jobs_for_uid(uid):
    out = []
    if not LONG_DIR.exists():
        return out
    for d in LONG_DIR.iterdir():
        if not d.is_dir():
            continue
        job = load_job(d.name)
        if job and job.get("uid") == uid:
            out.append(job)
    out.sort(key=lambda j: j.get("created", 0), reverse=True)
    return out


def _set(job, **kw):
    """Update in-memory progress fields (cheap; not persisted)."""
    job.update(kw)


def _ffprobe_json(path, entries):
    cmd = ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(path)]
    out = subprocess.check_output(cmd, timeout=120)
    return json.loads(out.decode() or "{}")


PUBLIC_FIELDS = ("id", "filename", "size", "status", "stage", "percent", "message", "duration",
                 "has_video", "estimate", "paid", "error", "n_segments", "speakers", "created",
                 "updated", "received_count", "total_chunks", "warnings", "price", "result",
                 "stated_speakers", "detected_speakers", "speaker_list", "lipsync")


def result_file(job):
    """Path of the finished file if it still exists on disk, else None."""
    r = job.get("result") or {}
    name = r.get("file")
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    p = OUTPUT_DIR / name
    return p if p.exists() else None


def public_view(job):
    v = {k: job.get(k) for k in PUBLIC_FIELDS if k in job}
    v["received_count"] = len(job.get("received", []))
    v["total_chunks"] = job.get("total_chunks", 0)
    if job.get("status") == "done":
        v["file_available"] = result_file(job) is not None
    return v


# ---------------------------------------------------------------- estimate

STATS_PATH = LONG_DIR / "stats.json"


def _load_stats():
    try:
        return json.loads(STATS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _speed_factor(kind):
    """Seconds of work per second of video for this kind of stage: the median
    of the last real runs on this server once there are at least 2, else the
    built-in default."""
    runs = _load_stats().get(kind) or []
    ratios = sorted(e / m for m, e in runs if m > 0 and e > 0)
    if len(ratios) >= 2:
        return ratios[len(ratios) // 2]
    return DEFAULT_SPEED[kind]


def _record_speed(kind, media_sec, elapsed_sec):
    """Remember how long a finished stage really took so future estimates get
    more accurate (last 30 runs kept)."""
    try:
        with _LOCK:
            st = _load_stats()
            runs = st.get(kind) or []
            runs.append([round(float(media_sec), 1), round(float(elapsed_sec), 1)])
            st[kind] = runs[-30:]
            tmp = STATS_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(st), encoding="utf-8")
            os.replace(tmp, STATS_PATH)
    except Exception as ex:
        print(f"[longdub] could not record speed: {ex}")


def estimate_times(duration_sec, lipsync=False):
    """Rough waiting time, as minute ranges: analysis (transcribe/translate),
    dubbing, and both together. The user's own reviewing time is not included.
    With lip-sync the lip-sync waiting time is reported separately
    (lipsync_min/max) and is part of the totals."""
    a = _speed_factor("analysis") * duration_sec
    d = _speed_factor("dubbing") * duration_sec + DUB_BASE_SEC

    def rng(x):
        lo = max(1, int(x * 0.7 / 60))
        hi = max(lo + 1, int(math.ceil(x * 1.6 / 60)))
        return lo, hi
    a_lo, a_hi = rng(a)
    d_lo, d_hi = rng(d)
    out = {"analysis_min": a_lo, "analysis_max": a_hi, "dub_min": d_lo, "dub_max": d_hi,
           "total_min": a_lo + d_lo, "total_max": a_hi + d_hi}
    if lipsync:
        out.update(lipsync_range(duration_sec))
        out["total_min"] += out["lipsync_min"]
        out["total_max"] += out["lipsync_max"]
    return out


def lipsync_range(clip_seconds):
    """Waiting time (minutes) for lip-syncing clip_seconds of video: the
    engine works on clips of at most 15 seconds, LIPSYNC_PARALLEL at a time.
    The real speed is learned from finished jobs (stats 'lipsync')."""
    x = _speed_factor("lipsync") * max(0.0, float(clip_seconds)) / LIPSYNC_PARALLEL
    lo = max(1, int(x * 0.5 / 60))
    hi = max(lo + 1, int(math.ceil(x * 1.5 / 60)))
    return {"lipsync_min": lo, "lipsync_max": hi}


def compute_estimate(duration_sec, cfg, speakers=2, lipsync=False):
    """Cost of the whole job, shown to the user before anything expensive
    runs. cfg = Hooks.pricing():
      fee               -- small fixed charge for producing this estimate
      analysis_per_min  -- transcribe + speaker detection + translate, per minute
      chars_per_credit  -- text-to-speech rate
      clone_credits     -- per cloned speaker voice
      merge_credits     -- final assembly
      lipsync_per_sec   -- lip-sync, per second of the clips that are lip-synced
    The fee is NOT extra: it counts toward the total. With lip-sync the
    estimate carries its highest possible price (every second of the video);
    the exact price is fixed later, after editing, and only covers the
    seconds where somebody actually speaks."""
    minutes = max(0.0, float(duration_sec)) / 60.0
    speakers = max(1, min(MAX_SPEAKERS, int(speakers or 2)))
    fee = int(cfg.get("fee", 3))
    analysis = int(math.ceil(minutes * float(cfg.get("analysis_per_min", 2))))
    cpc = max(1, int(cfg.get("chars_per_credit", 60)))
    voice = int(math.ceil(minutes * EST_CHARS_PER_MIN / cpc))
    clone_each = int(cfg.get("clone_credits", 5))
    clones = clone_each * speakers
    merge = max(1, int(cfg.get("merge_credits", 1)))
    per_sec = float(cfg.get("lipsync_per_sec", 40))
    lip = int(math.ceil(max(0.0, float(duration_sec)) * per_sec)) if lipsync else 0
    total = fee + analysis + voice + clones + merge + lip
    return {
        "minutes": round(minutes, 2), "fee": fee, "analysis": analysis, "voice": voice,
        "clones": clones, "clone_each": clone_each, "merge": merge, "total": total,
        "speakers": speakers, "assumed_speakers": speakers,
        "lipsync_wanted": bool(lipsync), "lipsync": lip, "lipsync_per_sec": per_sec if lipsync else 0,
        "times": estimate_times(float(duration_sec), bool(lipsync)), "terms_version": TERMS_VERSION,
    }


# ------------------------------------------------------------------ upload

def active_count(uid):
    n = 0
    for j in list_jobs_for_uid(uid):
        if j.get("status") not in ("done", "failed", "cancelled", "expired"):
            n += 1
    return n


def init_upload(uid, filename, size, speakers=2, lipsync=False):
    """Creates the job record + empty part file. Returns (job, None) or
    (None, (error_message, http_status))."""
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        return None, ("Unsupported file type. Use MP4, MOV, MKV, WEBM, AVI, MP3, WAV, M4A, AAC, FLAC or OGG.", 400)
    try:
        size = int(size)
    except Exception:
        return None, ("Missing file size.", 400)
    try:
        speakers = int(speakers)
    except Exception:
        speakers = 2
    if speakers < 1 or speakers > MAX_SPEAKERS:
        return None, (f"The number of speakers must be between 1 and {MAX_SPEAKERS}.", 400)
    lipsync = bool(lipsync)
    if lipsync and ext not in VIDEO_EXTS:
        return None, ("Lip-sync needs a video file. Choose \"without lip-sync\" for audio files.", 400)
    if lipsync and not lipsync_available():
        return None, ("Lip-sync is not available right now. Please choose \"without lip-sync\" or try again later.", 503)
    if size <= 0:
        return None, ("The file is empty.", 400)
    if size > MAX_UPLOAD_BYTES:
        return None, (f"This file is {size / 1048576:.0f} MB. The limit is {MAX_UPLOAD_BYTES // 1048576} MB.", 413)
    if active_count(uid) >= MAX_ACTIVE_PER_USER:
        return None, (f"You already have {MAX_ACTIVE_PER_USER} unfinished long dubs. Finish or delete one first.", 429)
    try:
        free = shutil.disk_usage(str(DATA_DIR)).free
    except Exception:
        free = None
    # Need room for the upload itself plus working files (audio copies, stems).
    if free is not None and free < size * 2 + 1024 * 1024 * 1024:
        return None, ("The server is short on storage right now. Please try again later.", 503)
    job_id = str(uuid.uuid4())
    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    total_chunks = int(math.ceil(size / float(CHUNK_BYTES)))
    job = {
        "id": job_id, "uid": uid, "filename": (filename or "video")[:200], "ext": ext, "size": size,
        "created": _now(), "updated": _now(), "status": "uploading", "stage": "upload",
        "percent": 0, "message": "Uploading...", "received": [], "total_chunks": total_chunks,
        "chunk_bytes": CHUNK_BYTES, "duration": 0.0, "has_video": ext in VIDEO_EXTS,
        "estimate": None, "paid": {"fee": 0, "analysis": 0, "dub": 0}, "error": "",
        "n_segments": 0, "speakers": [], "warnings": [], "stated_speakers": speakers,
        "speaker_list": [], "detected_speakers": 0, "lipsync": {"wanted": lipsync},
    }
    with _LOCK:
        _JOBS[job_id] = job
    _save(job)
    _ev(job, "upload_started", "ok", f"file={job['filename']} size={size} stated_speakers={speakers} lipsync={'yes' if lipsync else 'no'}")
    return job, None


def write_chunk(job, index, data):
    """Writes one chunk at its own offset. Returns (True, None) or (False, msg)."""
    if job.get("status") != "uploading":
        return False, "This upload is no longer accepting data."
    try:
        index = int(index)
    except Exception:
        return False, "Bad chunk index."
    total = job["total_chunks"]
    if index < 0 or index >= total:
        return False, "Chunk index out of range."
    expected = CHUNK_BYTES if index < total - 1 else job["size"] - CHUNK_BYTES * (total - 1)
    if len(data) != expected:
        return False, f"Chunk {index} has {len(data)} bytes, expected {expected}."
    part = job_dir(job["id"]) / "src.part"
    with _lock_for(job["id"]):
        mode = "r+b" if part.exists() else "w+b"
        with open(part, mode) as f:
            f.seek(index * CHUNK_BYTES)
            f.write(data)
        if index not in job["received"]:
            job["received"].append(index)
        job["percent"] = int(100 * len(job["received"]) / max(1, total))
    # Persist occasionally, not for every 8 MB chunk.
    if len(job["received"]) % 8 == 0:
        _save(job)
    return True, None


def finish_upload(job, uid):
    """All chunks in -> verify, probe, compute the estimate, charge the small
    estimate fee (counts toward the total). Returns (True, None) or
    (False, (message, http_status))."""
    if job.get("status") == "estimated":
        return True, None
    if job.get("status") != "uploading":
        return False, ("This upload is already finished.", 409)
    if len(set(job["received"])) != job["total_chunks"]:
        return False, (f"Upload incomplete ({len(job['received'])} of {job['total_chunks']} parts received).", 409)
    d = job_dir(job["id"])
    part = d / "src.part"
    if not part.exists() or part.stat().st_size != job["size"]:
        _ev(job, "upload_finished", "failed", "uploaded file damaged or incomplete")
        return False, ("The uploaded file is damaged or incomplete. Please upload it again.", 400)
    src = d / f"src{job['ext']}"
    os.replace(part, src)
    cfg = Hooks.pricing()
    try:
        info = _ffprobe_json(src, "format=duration:stream=codec_type")
        streams = [s.get("codec_type") for s in info.get("streams", [])]
        duration = float(info.get("format", {}).get("duration"))
    except Exception:
        _ev(job, "upload_finished", "failed", "file could not be read as audio/video")
        _discard(job)
        return False, ("This file couldn't be read as audio/video. Please try another file.", 400)
    if "audio" not in streams:
        _ev(job, "upload_finished", "failed", "no audio track")
        _discard(job)
        return False, ("This file has no audio track to dub.", 400)
    max_min = float(cfg.get("max_min", DEFAULT_MAX_MIN))
    if duration < MIN_SEC:
        _ev(job, "upload_finished", "failed", f"too short: {duration:.1f}s")
        _discard(job)
        return False, (f"This clip is only {duration:.0f} seconds. For clips under {MIN_SEC:.0f} seconds use the normal dubbing page.", 400)
    if duration > max_min * 60 + 1:
        _ev(job, "upload_finished", "failed", f"too long: {duration / 60:.1f} min (limit {max_min:g})")
        _discard(job)
        return False, (f"This video is {duration / 60:.1f} minutes long. The limit is {max_min:g} minutes.", 413)
    lip_wanted = bool((job.get("lipsync") or {}).get("wanted"))
    geo = None
    if lip_wanted:
        lmax = float(cfg.get("lipsync_max_min", DEFAULT_LIPSYNC_MAX_MIN))
        if duration > lmax * 60 + 1:
            _ev(job, "upload_finished", "failed", f"too long for lip-sync: {duration / 60:.1f} min (limit {lmax:g})")
            _discard(job)
            return False, (f"With lip-sync the limit is {lmax:g} minutes and this video is {duration / 60:.1f} minutes long. "
                           f"Choose \"without lip-sync\" or use a shorter video. Nothing was charged.", 413)
        if not lipsync_available():
            _ev(job, "upload_finished", "failed", "lip-sync engine is not available")
            _discard(job)
            return False, ("Lip-sync is not available right now. Please start again without lip-sync or try later. Nothing was charged.", 503)
        geo = _video_geometry(src)
        if geo is None or "video" not in streams:
            _ev(job, "upload_finished", "failed", "no readable video picture for lip-sync")
            _discard(job)
            return False, ("This file has no readable video picture, so it cannot be lip-synced. Nothing was charged.", 400)
    fee = int(cfg.get("fee", 3))
    bal = Hooks.get_credits(uid)
    if bal is not None and bal < fee:
        # Keep the upload so the user can top up and retry finishing.
        os.replace(src, part)
        _ev(job, "upload_finished", "failed", f"not enough credits for the estimate fee (need {fee}, have {bal})")
        return False, (f"Getting an estimate costs {fee} credits and you have {bal}. Use Buy to top up, then try again.", 402)
    with _lock_for(job["id"]):
        job["duration"] = round(duration, 3)
        job["has_video"] = "video" in streams and job["ext"] in VIDEO_EXTS
        job["estimate"] = compute_estimate(duration, cfg, job.get("stated_speakers", 2), lip_wanted)
        if lip_wanted and geo:
            job["lipsync"].update({"fps": str(geo[0]), "w": geo[1], "h": geo[2]})
        if fee > 0 and not job["paid"]["fee"]:
            Hooks.charge(uid, fee, "long_dub_estimate", job["id"])
            job["paid"]["fee"] = fee
            _ev(job, "estimate_fee_charged", "ok", f"{fee} credits", fee)
        job["status"] = "estimated"
        job["stage"] = "estimate"
        job["percent"] = 100
        job["message"] = "Estimate ready."
    _save(job)
    e = job["estimate"]
    _ev(job, "estimate_created", "ok",
        f"duration={duration:.1f}s total={e['total']} speakers={e['speakers']} time={e['times']['total_min']}-{e['times']['total_max']}min "
        f"lipsync={'yes (up to ' + str(e['lipsync']) + ' credits at ' + format(e['lipsync_per_sec'], 'g') + '/s)' if lip_wanted else 'no'} terms={TERMS_VERSION}")
    return True, None


def _discard(job):
    """Remove a job that never got past validation (nothing was charged)."""
    with _LOCK:
        _JOBS.pop(job["id"], None)
    shutil.rmtree(job_dir(job["id"]), ignore_errors=True)


def accept(job, uid, agreed=False):
    """User agreed to the estimate and the terms: check balance for the whole
    estimate, charge the analysis part, and start the background worker.
    Returns (True, None) or (False, (message, http_status))."""
    if job.get("status") != "estimated":
        return False, ("This job is not waiting for approval.", 409)
    if not agreed:
        return False, ("Please tick the box to confirm you have read the terms of this offer.", 400)
    est = job["estimate"]
    need_now = est["analysis"]
    bal = Hooks.get_credits(uid)
    # He must be able to cover the whole estimate (minus what he already paid)
    # to proceed -- the exact voice cost is confirmed later after editing.
    remaining_total = est["total"] - job["paid"]["fee"]
    if bal is not None and bal < remaining_total:
        _ev(job, "accepted", "failed", f"not enough credits (need {remaining_total}, have {bal})")
        return False, (f"Not enough credits. The full estimate is {est['total']} credits (you already paid {job['paid']['fee']}); "
                       f"you have {bal}. Use Buy to top up.", 402)
    with _lock_for(job["id"]):
        job["terms_accepted"] = {"version": TERMS_VERSION, "at": _now()}
        _ev(job, "terms_accepted", "ok", f"version={TERMS_VERSION} estimate_total={est['total']}")
        if need_now > 0 and not job["paid"]["analysis"]:
            Hooks.charge(uid, need_now, "long_dub_analysis", job["id"])
            job["paid"]["analysis"] = need_now
            _ev(job, "analysis_fee_charged", "ok", f"{need_now} credits", need_now)
        job["status"] = "accepted"
        job["stage"] = "queued"
        job["percent"] = 0
        job["message"] = "Waiting for a free processing slot..."
    _save(job)
    start_worker(job["id"])
    return True, None


def delete_job(job, uid):
    """User throws a job away (only when no worker is busy on it)."""
    if job["id"] in _RUNNING:
        return False, ("This job is being processed right now and can't be deleted yet.", 409)
    _ev(job, "deleted_by_user", "ok", f"status={job.get('status')} paid={job.get('paid')}")
    _delete_pending_voices(job)
    with _LOCK:
        _JOBS.pop(job["id"], None)
    shutil.rmtree(job_dir(job["id"]), ignore_errors=True)
    return True, None


# ------------------------------------------------------- piece planning

def parse_silences(stderr_text):
    gaps, pending = [], None
    for line in (stderr_text or "").splitlines():
        if "silence_start:" in line:
            try:
                pending = float(line.split("silence_start:")[1].strip())
            except Exception:
                pending = None
        elif "silence_end:" in line and pending is not None:
            try:
                end = float(line.split("silence_end:")[1].split("|")[0].strip())
                gaps.append((pending, end))
            except Exception:
                pass
            pending = None
    return gaps


def detect_silences(audio_path, noise_db="-35dB", min_sec=0.3):
    cmd = ["ffmpeg", "-nostats", "-i", str(audio_path),
           "-af", f"silencedetect=noise={noise_db}:d={min_sec}", "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except Exception:
        return []
    return parse_silences(p.stderr)


def _to_sample(t):
    return round(float(t) * SAMPLE_RATE) / SAMPLE_RATE


def plan_pieces(duration, silences, target=PIECE_TARGET, pmin=PIECE_MIN, pmax=PIECE_MAX):
    """Split [0, duration] into contiguous pieces, cutting in the middle of a
    silence whenever one exists in the allowed window, so no word is cut in
    half. Boundaries are snapped to whole samples so pieces re-join exactly.
    Returns [(start, end), ...] covering the file with no gaps or overlaps."""
    mids = [((a + b) / 2.0, b - a) for (a, b) in silences if b > a]
    bounds = [0.0]
    cur = 0.0
    while duration - cur > pmax:
        lo, hi = cur + pmin, cur + pmax
        cands = [(m, l) for (m, l) in mids if lo <= m <= hi]
        if cands:
            # Prefer a cut close to the target length, rewarding longer silences.
            best = min(cands, key=lambda c: abs((c[0] - cur) - target) - min(c[1], 2.0) * 3.0)
            cut = best[0]
        else:
            cut = cur + target
        cut = _to_sample(cut)
        bounds.append(cut)
        cur = cut
    bounds.append(math.ceil(duration * SAMPLE_RATE) / SAMPLE_RATE if duration > cur else cur)
    pieces = []
    for i in range(len(bounds) - 1):
        if bounds[i + 1] - bounds[i] > 0.05:
            pieces.append((bounds[i], bounds[i + 1]))
    return pieces


# --------------------------------------------------------- worker control

def start_worker(job_id):
    with _LOCK:
        if job_id in _RUNNING:
            return False
        _RUNNING.add(job_id)
    threading.Thread(target=_worker_main, args=(job_id,), daemon=True).start()
    return True


def _worker_main(job_id):
    try:
        job = load_job(job_id)
        if not job:
            return
        _worker_slots.acquire()
        _slot_state.held = True
        try:
            job = load_job(job_id)
            if job["status"] in ("accepted", "analyzing"):
                _run_analysis(job)
            elif job["status"] in ("confirmed", "dubbing"):
                runner = globals().get("_run_dubbing")
                if runner:
                    runner(job)
        finally:
            if getattr(_slot_state, "held", False):
                _slot_state.held = False
                _worker_slots.release()
    finally:
        with _LOCK:
            _RUNNING.discard(job_id)


def resume_all():
    """Called once at server start: relaunch every job that was mid-run when
    the process last stopped. Jobs resume from their last checkpoint."""
    n = 0
    if not LONG_DIR.exists():
        return 0
    for d in LONG_DIR.iterdir():
        if not d.is_dir():
            continue
        job = load_job(d.name)
        if job and job.get("status") in ("accepted", "analyzing", "confirmed", "dubbing"):
            if start_worker(job["id"]):
                _ev(job, "resumed_after_restart", "info", f"status={job.get('status')}")
                n += 1
    return n


def _fail(job, message, refund_kind=None):
    """Mark the job failed, refund the charge for the stage that failed, tell
    the user by email."""
    uid = job["uid"]
    refunded = 0
    with _lock_for(job["id"]):
        if refund_kind and job["paid"].get(refund_kind):
            refunded = int(job["paid"][refund_kind])
            try:
                Hooks.refund(uid, refunded, job["id"])
                job["paid"][refund_kind] = 0
            except Exception as ex:
                print(f"[longdub] refund failed for {job['id']}: {ex}")
                refunded = 0
        job["status"] = "failed"
        job["error"] = message
        job["message"] = message
    _save(job)
    _ev(job, "job_failed", "failed", f"{message} | refunded={refunded} kind={refund_kind}", refunded or None)
    _delete_pending_voices(job)
    try:
        extra = f" We refunded {refunded} credits." if refunded else ""
        Hooks.send_email(uid, "Your Lisan AI long video could not be finished",
                         f"Hi,\n\nSorry, your long video \"{job['filename']}\" could not be finished: {message}.{extra}\n\n"
                         "You can start again from https://lisanai.org/dub-long\n\n-- Lisan AI")
    except Exception:
        pass


# ------------------------------------------------------------ slot control

def _queue():
    import whisper_service
    return whisper_service._transcribe_queue


class _Slot:
    """Holds the shared one-at-a-time processing slot and lets a long job
    politely step aside between pieces when somebody else is waiting."""

    def __init__(self, job):
        self.job = job
        self.held = False

    def _on_update(self, ahead):
        if ahead <= 0:
            msg = "Waiting for a free processing slot..."
        elif ahead == 1:
            msg = "Waiting in queue - 1 job ahead of this one..."
        else:
            msg = f"Waiting in queue - {ahead} jobs ahead of this one..."
        self.job["message"] = msg

    def take(self):
        if not self.held:
            _queue().acquire("long_" + self.job["id"], on_update=self._on_update)
            self.held = True

    def drop(self):
        if self.held:
            self.held = False
            _queue().release()

    def yield_if_needed(self, release_model=False):
        """Between pieces: if another job is waiting, let it go first."""
        q = _queue()
        if self.held and q.has_waiters():
            if release_model:
                import whisper_service
                whisper_service._release_model()
            self.drop()
            self.take()


# --------------------------------------------------------------- analysis

def _wd(job):
    return job_dir(job["id"])


def _mark(job, stage, percent, message):
    job["stage"] = stage
    job["percent"] = int(percent)
    job["message"] = message
    try:
        import app_state
        app_state.last_job_activity = time.time()
    except Exception:
        pass


def _concat_wavs(files, out_path):
    """Sample-exact join of same-format PCM wavs."""
    ffmpeg_utils.concat_audio_files(list(files), out_path)


def _silence_wav(seconds, out_path):
    ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-f", "lavfi", "-t", f"{seconds:.6f}",
                             "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo",
                             "-acodec", "pcm_s16le", str(out_path)])


def _shift_segments(raw, offset):
    out = []
    for s in raw:
        words = [SimpleNamespace(word=w["word"], start=w["start"] + offset, end=w["end"] + offset)
                 for w in (s.get("words") or [])]
        out.append(SimpleNamespace(start=s["start"] + offset, end=s["end"] + offset,
                                   text=s["text"], words=words))
    return out


_SENT_END = re.compile(r'[.!?\u061F\u2026]["\')\]\u201D]*$')
_NOT_SENT_END = {"mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "jr.", "sr.", "vs.", "etc.", "e.g.", "i.e.", "no.", "inc.",
                 "ltd.", "co.", "mt.", "gen.", "col.", "lt.", "capt.", "sgt.", "a.m.", "p.m."}


def split_rows_by_sentence(rows):
    """One row per sentence. The speech recogniser hands over stretches that can
    hold two or three sentences by the same speaker; each sentence is dubbed
    (and edited) better as its own line. Uses the word times, so every new line
    starts where its first word starts and ends where its last word ends."""
    out = []
    for r in rows:
        words = r.get("words") or []
        if len(words) < 2:
            out.append(r)
            continue
        pieces, cur = [], []
        for k, w in enumerate(words):
            cur.append(w)
            tok = str(w.get("word") or "").strip()
            if k < len(words) - 1 and _SENT_END.search(tok) and tok.lower() not in _NOT_SENT_END:
                pieces.append(cur)
                cur = []
        if cur:
            pieces.append(cur)
        if len(pieces) <= 1:
            out.append(r)
            continue
        for pw in pieces:
            text = " ".join(str(w.get("word") or "").strip() for w in pw).strip()
            if not text:
                continue
            nr = dict(r)
            nr.update({"words": pw, "text": text, "start": round(float(pw[0]["start"]), 2), "end": round(float(pw[-1]["end"]), 2)})
            out.append(nr)
    return out


PAUSE_MIN_SEC = 0.6          # a real pause between two phrases (same threshold as the short flow)
PAUSE_HALF_MIN_SEC = 0.4     # a cut never leaves a piece shorter than this


def _pause_boundary(words, g0, g1):
    """Index of the last word that belongs BEFORE the silence (g0, g1), or -1.
    A word whose middle lies inside the silence is never trusted as being on
    either side of it (the recogniser's word times drift around a pause)."""
    idx = -1
    for m in range(len(words) - 1):
        mid = (float(words[m]["start"]) + float(words[m]["end"])) / 2.0
        if mid < g0:
            idx = m
        elif mid <= g1:
            continue
        else:
            break
    return idx


def _piece(row, words):
    text = " ".join(str(w.get("word") or "").strip() for w in words).strip()
    nr = dict(row)
    nr.update({"words": words, "text": text, "start": round(float(words[0]["start"]), 2), "end": round(float(words[-1]["end"]), 2)})
    return nr


def split_rows_at_pauses(rows, silences, min_gap=PAUSE_MIN_SEC):
    """Cuts a line wherever there is a real pause inside it. Two people trading
    lines (or one person taking a beat between two phrases) come out of the
    speech recogniser as one long stretch; each phrase is dubbed, timed and
    assigned better as its own line. The pause is taken from the real silence in
    the audio (silences = [(start, end)]); only when a line holds no measured
    silence does a wide gap between two words count. Returns (rows, n_cuts)."""
    cuts = [0]

    def cut(row, was_split):
        words = row.get("words") or []
        if len(words) < 2:
            return [row]
        inside = [(a, b) for (a, b) in silences
                  if a > float(row["start"]) + 0.15 and b < float(row["end"]) - 0.05 and (b - a) >= min_gap]
        best = None
        for a, b in sorted(inside, key=lambda g: g[1] - g[0], reverse=True):
            i = _pause_boundary(words, a, b)
            if 0 <= i < len(words) - 1:
                best = i
                break
        if best is None and not was_split:
            widest = 0.0
            for k in range(len(words) - 1):
                gap = float(words[k + 1]["start"]) - float(words[k]["end"])
                if gap >= min_gap and gap > widest:
                    widest, best = gap, k
        if best is None:
            return [row]
        first, second = words[:best + 1], words[best + 1:]
        if (float(first[-1]["end"]) - float(first[0]["start"]) < PAUSE_HALF_MIN_SEC
                or float(second[-1]["end"]) - float(second[0]["start"]) < PAUSE_HALF_MIN_SEC):
            return [row]
        cuts[0] += 1
        return cut(_piece(row, first), True) + cut(_piece(row, second), True)

    out = []
    for r in rows:
        out.extend(cut(r, False))
    return out, cuts[0]


def rows_from_raw(raw_segments, turns, speaker_label_map, silences=None):
    """Same row building as whisper_service.transcribe_worker (speaker-turn
    grouping, 15 s cap per line, merge of mid-sentence splits), copied so the
    short flow stays untouched."""
    import whisper_service as ws
    result = []
    seg_index = 0
    for segment in raw_segments:
        segment_words = getattr(segment, "words", None) or []
        groups = ws.group_words_by_speaker(segment_words, turns) if turns else []
        if groups:
            for raw_speaker, words in groups:
                for chunk in ws.chunk_words_by_duration(words, 15.0):
                    speaker = speaker_label_map.get(raw_speaker, "Speaker 1") if raw_speaker else "Speaker 1"
                    text = " ".join((w.word or "").strip() for w in chunk).strip()
                    if not text:
                        continue
                    result.append({
                        "segment_id": f"seg_{seg_index}",
                        "start": round(float(chunk[0].start), 2), "end": round(float(chunk[-1].end), 2),
                        "text": text, "speaker": speaker, "gender": "male", "emotion": "neutral",
                        "arabic_text": "",
                        "words": [{"word": w.word, "start": round(float(w.start), 3), "end": round(float(w.end), 3)} for w in chunk],
                    })
                    seg_index += 1
        else:
            for part in ws.split_segment(segment, max_duration=15.0):
                speaker = "Speaker 1"
                part_words = []
                if turns:
                    raw_speaker = ws.assign_speaker_by_words(segment_words, part["start"], part["end"], turns)
                    if not raw_speaker:
                        raw_speaker = ws.assign_speaker_for_segment(part["start"], part["end"], turns)
                    if raw_speaker:
                        speaker = speaker_label_map.get(raw_speaker, "Speaker 1")
                for w in segment_words:
                    wst, wen = getattr(w, "start", None), getattr(w, "end", None)
                    if wst is None or wen is None:
                        continue
                    mid = (float(wst) + float(wen)) / 2.0
                    if part["start"] - 0.01 <= mid <= part["end"] + 0.01:
                        part_words.append({"word": w.word, "start": round(float(wst), 3), "end": round(float(wen), 3)})
                result.append({
                    "segment_id": f"seg_{seg_index}", "start": part["start"], "end": part["end"],
                    "text": part["text"], "speaker": speaker, "gender": "male", "emotion": "neutral",
                    "arabic_text": "", "words": part_words,
                })
                seg_index += 1
    result = ws.merge_mid_sentence_rows(split_rows_by_sentence(result))
    if silences:
        try:
            result, _n_cuts = split_rows_at_pauses(result, silences)
            print(f"[longdub] pause cuts: {_n_cuts} line(s) cut at a real pause")
        except Exception as ex:
            print(f"[longdub] pause cutting skipped: {ex}")
    for i, r in enumerate(result):
        r["segment_id"] = f"seg_{i}"
    return result


def _run_analysis(job):
    """extract audio -> cut pieces -> separate vocals/background per piece ->
    speaker detection (whole file, one pass) -> transcribe per piece ->
    translate in small batches -> ready to edit. Every step checkpoints, so a
    restart continues instead of starting over."""
    wd = _wd(job)
    src = wd / f"src{job['ext']}"
    try:
        resumed = job.get("status") == "analyzing"
        job["status"] = "analyzing"
        an = job.setdefault("analysis", {})
        an.setdefault("sep_done", [])
        an.setdefault("asr_done", [])
        if resumed:
            an["resumed"] = True
        an.setdefault("started", _now())
        _save(job)
        _ev(job, "analysis_started", "info", "resumed from checkpoint" if resumed else "first run")

        # 1. audio track -------------------------------------------------
        audio = wd / "audio.wav"
        if not audio.exists():
            _mark(job, "extract", 2, "Extracting the audio...")
            ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "2", "-ar", str(SAMPLE_RATE),
                                     "-acodec", "pcm_s16le", str(audio)])
            _ev(job, "audio_extracted", "ok", f"{audio.stat().st_size // 1048576} MB")
        # 2. pieces --------------------------------------------------------
        if not an.get("pieces"):
            _mark(job, "plan", 4, "Finding natural pauses to split at...")
            sil = detect_silences(audio)
            dur = ffmpeg_utils.get_media_duration(audio)
            an["pieces"] = [list(p) for p in plan_pieces(dur, sil)]
            an["audio_duration"] = dur
            _save(job)
            _ev(job, "pieces_planned", "ok", f"{len(an['pieces'])} pieces, audio {dur:.1f}s")
        pieces = an["pieces"]
        n = len(pieces)
        (wd / "pieces").mkdir(exist_ok=True)
        (wd / "vocals").mkdir(exist_ok=True)
        (wd / "bg").mkdir(exist_ok=True)
        slot = _Slot(job)
        has_video = bool(job.get("has_video"))

        # 3. separate vocals from background, one piece at a time ---------
        for i, (b0, b1) in enumerate(pieces):
            if i in an["sep_done"]:
                continue
            _mark(job, "separate", 5 + int(40 * i / n),
                  f"Separating voices from background music (part {i + 1} of {n})...")
            piece = wd / "pieces" / f"p{i:03d}.wav"
            ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-ss", f"{b0:.6f}", "-t", f"{b1 - b0:.6f}", "-i", str(audio),
                                     "-acodec", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", "2", str(piece)])
            vocals_dst = wd / "vocals" / f"p{i:03d}.wav"
            bg_dst = wd / "bg" / f"p{i:03d}.wav"
            if has_video:
                ok = False
                slot.take()
                try:
                    for attempt in range(2):
                        try:
                            sep_dir = wd / "sep" / f"p{i:03d}"
                            v, b = ffmpeg_utils.separate_vocals(str(piece), str(sep_dir))
                            if Path(v).exists() and Path(b).exists():
                                # Re-encode to one fixed PCM format so every
                                # piece (including a silence stand-in) joins
                                # byte-exactly later.
                                for srcf, dstf in ((v, vocals_dst), (b, bg_dst)):
                                    ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-i", str(srcf), "-ac", "2",
                                                             "-ar", str(SAMPLE_RATE), "-acodec", "pcm_s16le", str(dstf)])
                                ok = True
                                break
                        except Exception as ex:
                            print(f"[longdub] separation of part {i} failed (attempt {attempt + 1}): {ex}")
                        finally:
                            shutil.rmtree(wd / "sep" / f"p{i:03d}", ignore_errors=True)
                finally:
                    slot.drop()
                if not ok:
                    shutil.copy(piece, vocals_dst)
                    _silence_wav(b1 - b0, bg_dst)
                    if i not in an.setdefault("bg_failed", []):
                        an["bg_failed"].append(i)
                    job.setdefault("warnings", []).append(
                        f"Background music could not be separated for part {i + 1}; that part will have no background sound.")
                    _ev(job, "separation", "failed", f"piece {i + 1}/{n}: separation failed twice; used the original sound and silent background")
            else:
                shutil.copy(piece, vocals_dst)
            try:
                piece.unlink()
            except Exception:
                pass
            an["sep_done"].append(i)
            _save(job)
            time.sleep(0.05)

        if not an.get("sep_logged"):
            _ev(job, "separation", "ok" if has_video else "skipped",
                f"{n} pieces" if has_video else "audio-only file: no background separation")
            an["sep_logged"] = True
        vocals_all = wd / "vocals.wav"
        if not vocals_all.exists():
            _concat_wavs([wd / "vocals" / f"p{i:03d}.wav" for i in range(n)], vocals_all)
        if has_video and not (wd / "background.wav").exists():
            _concat_wavs([wd / "bg" / f"p{i:03d}.wav" for i in range(n)], wd / "background.wav")

        # 4. speaker detection over the whole vocals track ---------------
        turns_path = wd / "turns.json"
        if not an.get("diarized"):
            turns = []
            if HF_TOKEN:
                import whisper_service
                _mark(job, "speakers", 46, "Detecting who is speaking...")
                slot.take()
                try:
                    stated = int(job.get("stated_speakers") or 0)
                    try:
                        turns = whisper_service.get_speaker_turns(str(vocals_all), HF_TOKEN, 0, min_speakers=stated)
                    except Exception as ex_min:
                        print(f"[longdub] speaker detection with at least {stated} speakers failed ({ex_min}); retrying without that hint")
                        turns = whisper_service.get_speaker_turns(str(vocals_all), HF_TOKEN, 0)
                except Exception as ex:
                    print(f"[longdub] speaker detection failed: {ex}")
                    job.setdefault("warnings", []).append("Speaker detection failed; every line was assigned to Speaker 1.")
                    turns = []
                finally:
                    try:
                        whisper_service._release_diarization_pipeline(HF_TOKEN)
                    except Exception:
                        pass
                    slot.drop()
            turns_path.write_text(json.dumps(turns), encoding="utf-8")
            an["diarized"] = True
            _save(job)
            if not HF_TOKEN:
                _ev(job, "speaker_detection", "skipped", "no HF_TOKEN configured")
            elif turns:
                secs = {}
                for t in turns:
                    secs[t["speaker"]] = secs.get(t["speaker"], 0.0) + max(0.0, t["end"] - t["start"])
                _ev(job, "speaker_detection", "ok",
                    f"{len(secs)} speakers, {len(turns)} turns (at least {job.get('stated_speakers')} asked for); seconds each: "
                    + ", ".join(f"{k} {v:.0f}" for k, v in sorted(secs.items(), key=lambda kv: -kv[1])))
            else:
                _ev(job, "speaker_detection", "failed", "no speaker turns returned")
        turns = json.loads(turns_path.read_text(encoding="utf-8")) if turns_path.exists() else []

        # 5. transcribe piece by piece -------------------------------------
        (wd / "asr").mkdir(exist_ok=True)
        import whisper_service
        slot.take()
        try:
            for i, (b0, b1) in enumerate(pieces):
                if i in an["asr_done"]:
                    continue
                _mark(job, "transcribe", 52 + int(33 * i / n), f"Transcribing (part {i + 1} of {n})...")
                slot.yield_if_needed(release_model=True)
                segs_gen, _info = whisper_service._get_model().transcribe(
                    str(wd / "vocals" / f"p{i:03d}.wav"), beam_size=5, language="en",
                    word_timestamps=True, vad_filter=True, condition_on_previous_text=False)
                raw = []
                for s in segs_gen:
                    raw.append({"start": float(s.start), "end": float(s.end), "text": s.text,
                                "words": [{"word": w.word, "start": float(w.start), "end": float(w.end)}
                                          for w in (getattr(s, "words", None) or [])
                                          if w.start is not None and w.end is not None]})
                (wd / "asr" / f"p{i:03d}.json").write_text(json.dumps(raw), encoding="utf-8")
                an["asr_done"].append(i)
                _save(job)
        finally:
            try:
                whisper_service._release_model()
            except Exception:
                pass
            slot.drop()
        _ev(job, "transcription", "ok", f"{n} pieces")

        # 6. build lines ----------------------------------------------------
        _mark(job, "build", 86, "Building the transcript...")
        raw_all = []
        for i, (b0, b1) in enumerate(pieces):
            raw = json.loads((wd / "asr" / f"p{i:03d}.json").read_text(encoding="utf-8"))
            raw_all.extend(_shift_segments(raw, b0))
        label_map = {}
        for t in sorted(turns, key=lambda x: x["start"]):
            if t["speaker"] not in label_map:
                label_map[t["speaker"]] = f"Speaker {len(label_map) + 1}"
        silences = []
        pauses_path = wd / "pauses.json"
        if pauses_path.exists():
            try:
                silences = [tuple(x) for x in json.loads(pauses_path.read_text(encoding="utf-8"))]
            except Exception:
                silences = []
        elif vocals_all.exists():
            try:
                silences = detect_silences(vocals_all, noise_db="-30dB", min_sec=PAUSE_MIN_SEC)
                pauses_path.write_text(json.dumps(silences), encoding="utf-8")
            except Exception as ex:
                print(f"[longdub] pause detection failed: {ex}")
        rows = rows_from_raw(raw_all, turns, label_map, silences)
        try:     # what the detector said and what became of it (for the server log; short clips only)
            if len(turns) <= 80:
                print(f"[longdub] {job['id']} turn_map: " + " ".join(
                    f"{t['start']:.1f}-{t['end']:.1f}={label_map.get(t['speaker'], t['speaker'])[-1:]}" for t in sorted(turns, key=lambda x: x['start'])))
            if len(rows) <= 60:
                print(f"[longdub] {job['id']} line_map: " + " ".join(
                    f"{r['start']:.1f}-{r['end']:.1f}={str(r['speaker'])[-1:]}" for r in rows))
        except Exception:
            pass
        if not rows:
            _fail(job, "No speech was found in this video", "analysis")
            return
        if len(rows) > MAX_SEGMENTS:
            _fail(job, f"This video has too many separate lines ({len(rows)}); the limit is {MAX_SEGMENTS}", "analysis")
            return
        _init_speakers(job, rows)
        _write_segments(job, rows)
        _ev(job, "transcript_built", "ok", f"{len(rows)} lines, {job['detected_speakers']} speakers detected, "
                                           f"{job.get('stated_speakers')} stated by the user; "
                                           f"{len(silences)} pauses of {PAUSE_MIN_SEC:g}s+ measured")

        # 7. translate in small batches ------------------------------------
        _translate_all(job, rows)
        _write_segments(job, rows)
        missing_ar = sum(1 for r in rows if not (r.get("arabic_text") or "").strip())
        _ev(job, "translation", "ok" if not missing_ar else "partial",
            f"{len(rows) - missing_ar} of {len(rows)} lines translated")

        # 8. done: free the big intermediates, keep what dubbing needs ------
        # (a mono copy of the separated voices stays: it is the reference for
        # cloning and for volume matching later)
        try:
            ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-i", str(vocals_all), "-ac", "1", "-ar", str(SAMPLE_RATE),
                                     "-acodec", "pcm_s16le", str(wd / "vocals_mono.wav")])
        except Exception as ex:
            print(f"[longdub] could not keep a mono voices copy: {ex}")
        for sub in ("pieces", "vocals", "bg", "asr", "sep"):
            shutil.rmtree(wd / sub, ignore_errors=True)
        for f in ("vocals.wav", "vocals_normalized.wav"):
            try:
                (wd / f).unlink()
            except Exception:
                pass
        job["speakers"] = [sp["name"] for sp in job["speaker_list"]]
        job["n_segments"] = len(rows)
        job["status"] = "editing"
        _mark(job, "review", 100, "Ready for you to review.")
        _save(job)
        elapsed = _now() - an.get("started", _now())
        _ev(job, "analysis_done", "ok", f"{elapsed:.0f}s for {an.get('audio_duration', 0):.0f}s of audio"
            + (" (resumed run)" if an.get("resumed") else ""))
        if not an.get("resumed"):
            _record_speed("analysis", an.get("audio_duration", 0), elapsed)
        # (No e-mail here on purpose: the only e-mail a user gets is the one saying the dubbed file is ready.)
    except Exception as ex:
        import traceback
        print(f"[longdub] analysis failed for {job['id']}: {ex}\n{traceback.format_exc()}")
        _ev(job, "analysis_failed", "failed", f"{type(ex).__name__}: {ex}"[:600])
        _fail(job, "Something went wrong while analysing this video", "analysis")


# ------------------------------------------------------------- segments

def _seg_path(job):
    return _wd(job) / "segments.json"


def _write_segments(job, rows):
    p = _seg_path(job)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def read_segments(job):
    p = _seg_path(job)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _translate_batch(job_id, batch):
    """Returns {segment_id: (arabic, emotion)} for the batch, {} on failure."""
    import gemini_service
    from models import Segment
    segs = [Segment(segment_id=r["segment_id"], start=r["start"], end=r["end"], speaker=r["speaker"],
                    text=r["text"]) for r in batch]
    for attempt in range(2):
        try:
            res = gemini_service.translate_segments(job_id, segs, GEMINI_API_KEY)
            if isinstance(res, dict) and res.get("status") == "success":
                out = {}
                for it in res.get("translated_segments", []):
                    if isinstance(it, dict) and it.get("segment_id"):
                        out[str(it["segment_id"])] = (str(it.get("arabic_text") or "").strip(),
                                                      str(it.get("emotion") or "neutral"))
                return out
        except Exception as ex:
            print(f"[longdub] translate batch failed (attempt {attempt + 1}): {ex}")
        time.sleep(3)
    return {}


def _translate_all(job, rows):
    total = len(rows)
    missing = 0
    for i in range(0, total, TRANSLATE_BATCH):
        batch = rows[i:i + TRANSLATE_BATCH]
        _mark(job, "translate", 88 + int(10 * i / max(1, total)), f"Translating to Arabic ({min(i + TRANSLATE_BATCH, total)} of {total} lines)...")
        got = _translate_batch(job["id"], batch)
        for r in batch:
            if r["segment_id"] in got and got[r["segment_id"]][0]:
                r["arabic_text"], r["emotion"] = got[r["segment_id"]]
            else:
                missing += 1
    if missing:
        job.setdefault("warnings", []).append(
            f"{missing} line(s) could not be translated automatically. Please type the Arabic for them, or use 'Translate again'.")


MAX_TEXT_LEN = 2000


# ------------------------------------------------------------- tashkeel
#
# Before the price is fixed (and again when the user confirms) every Arabic word
# that has no tashkeel (vowel marks) gets it. Words that already carry marks are
# never touched, and the AI's answer is accepted word by word only when the
# letters are exactly the ones the user had.

TASHKEEL_BATCH = 25
_MARKS = "\u064B\u064C\u064D\u064E\u064F\u0650\u0651\u0652\u0653\u0654\u0655\u0670"
_AR_LETTER = re.compile("[\u0621-\u064A\u0671-\u06D3]")
_MARK_RE = re.compile("[" + _MARKS + "]")


def _strip_marks(t):
    return _MARK_RE.sub("", t)


def _same_letters(a, b):
    """Same letters, ignoring marks and the hamza on an alef (a diacritizer
    restoring \u0623/\u0625 for a bare alef is correct spelling, not a change of word)."""
    def norm(t):
        return _strip_marks(t).replace("\u0623", "\u0627").replace("\u0625", "\u0627").replace("\u0622", "\u0627").replace("\u0671", "\u0627")
    return norm(a) == norm(b)


def _word_needs_tashkeel(w):
    """An Arabic word (2+ letters) that has no vowel mark at all."""
    return len(_AR_LETTER.findall(w)) >= 2 and not _MARK_RE.search(w)


def needs_tashkeel(text):
    return any(_word_needs_tashkeel(w) for w in (text or "").split())


def merge_tashkeel(original, returned):
    """Original text with tashkeel taken from `returned` for the words that
    had none. Returns the new text (== original when nothing could be used)."""
    parts = re.split(r"(\s+)", original)
    words = [x for x in returned.split()] if isinstance(returned, str) else []
    toks = [x for x in parts[0::2] if x != ""]
    if len(words) != len(toks):
        return original
    it = iter(words)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1 or part == "":
            out.append(part)
            continue
        cand = next(it)
        if _word_needs_tashkeel(part) and _same_letters(cand, part) and _MARK_RE.search(cand):
            out.append(cand)
        else:
            out.append(part)
    return "".join(out)


def ensure_tashkeel(job):
    """Adds tashkeel to every line that lacks it. Returns (lines_changed, None)
    or (0, error message) when the AI service did not answer (nothing changed)."""
    import gemini_service
    rows = read_segments(job)
    todo = [r for r in rows if (r.get("arabic_text") or "").strip() and needs_tashkeel(r["arabic_text"])]
    if not todo:
        return 0, None
    changed = 0
    unresolved = 0
    for i in range(0, len(todo), TASHKEEL_BATCH):
        batch = todo[i:i + TASHKEEL_BATCH]
        got = None
        for attempt in range(2):
            try:
                got = gemini_service.add_tashkeel_lines(
                    job["id"], [{"segment_id": r["segment_id"], "arabic_text": r["arabic_text"]} for r in batch], GEMINI_API_KEY)
            except Exception as ex:
                print(f"[longdub] tashkeel batch failed (attempt {attempt + 1}): {ex}")
                got = None
            if got is not None:
                break
            time.sleep(2)
        if got is None:
            _ev(job, "tashkeel", "failed", f"the AI service did not answer for {len(batch)} lines")
            return 0, "The tashkeel service did not answer. Nothing was charged. Please try again in a moment."
        for r in batch:
            new = merge_tashkeel(r["arabic_text"], got.get(r["segment_id"], ""))
            if new != r["arabic_text"]:
                r["arabic_text"] = new
                changed += 1
            if needs_tashkeel(r["arabic_text"]):
                unresolved += 1
    if changed:
        with _lock_for(job["id"]):
            fresh = {x["segment_id"]: x for x in read_segments(job)}
            for r in todo:
                if r["segment_id"] in fresh and fresh[r["segment_id"]].get("arabic_text") != r["arabic_text"]:
                    fresh[r["segment_id"]]["arabic_text"] = r["arabic_text"]
            _write_segments(job, [fresh[x["segment_id"]] for x in rows if x["segment_id"] in fresh])
    _ev(job, "tashkeel", "ok" if not unresolved else "partial",
        f"{changed} of {len(todo)} lines got tashkeel" + (f", {unresolved} still have words without" if unresolved else ""))
    return changed, None


MIN_LINE_SEC = 0.3        # shortest line the editor allows
MAX_LINE_SEC = 60.0       # longest line the editor allows
NEW_LINE_SEC = 2.0        # default length of a line the user inserts
OVERLAP_OK = 0.05         # two lines may not overlap by more than this


def _fmt_t(sec):
    sec = max(0.0, float(sec))
    m = int(sec // 60)
    return f"{m}:{sec - 60 * m:04.1f}"


def _total_secs(job):
    return float((job.get("analysis") or {}).get("audio_duration") or job.get("duration") or 0)


def _by_start(rows):
    """Rows in time order (rows that start together keep their order)."""
    return [r for _, r in sorted(enumerate(rows), key=lambda p: (float(p[1].get("start") or 0), p[0]))]


def _time_error(rows, seg_id, start, end, total):
    """Why (start, end) can't be this line's time, or None when it can."""
    if start < 0:
        return "A line can't start before the beginning of the video."
    if total and end > total + 0.01:
        return f"A line can't end after the end of the video ({_fmt_t(total)})."
    if end - start < MIN_LINE_SEC:
        return f"A line must be at least {MIN_LINE_SEC:g} seconds long."
    if end - start > MAX_LINE_SEC:
        return f"A line can be at most {MAX_LINE_SEC:g} seconds long. Split it into two lines."
    for o in rows:
        if o.get("segment_id") == seg_id:
            continue
        ov = min(end, float(o["end"])) - max(start, float(o["start"]))
        if ov > OVERLAP_OK:
            return (f"This time overlaps another line ({_fmt_t(o['start'])} – {_fmt_t(o['end'])}). "
                    "Change that line's time first, or choose a free gap.")
    return None


def clean_emotion(value):
    """Only known delivery words are kept (at most 3, comma separated); anything
    else, or an empty value, becomes 'neutral'."""
    import inworld_service
    from config import CANONICAL_EMOTIONS
    allowed = set(CANONICAL_EMOTIONS) | set(inworld_service.INWORLD_EXTRA_TAGS)
    kept = []
    for p in str(value or "").lower().split(","):
        p = " ".join(p.split())
        if p in allowed and p not in kept:
            kept.append(p)
    kept = [p for p in kept if p != "neutral"] or ["neutral"]
    return ", ".join(kept[:3])


def update_segments(job, edits):
    """edits = [{segment_id, text?, arabic_text?, speaker_id?, emotion?}]. The
    text, the speaker and the emotion of a line are saved here; times, inserting and deleting
    lines have their own calls (set_line_time, insert_line, delete_line)."""
    if job.get("status") != "editing":
        return False, "This job is not open for editing."
    with _lock_for(job["id"]):
        rows = read_segments(job)
        by_id = {r["segment_id"]: r for r in rows}
        sp_names = {sp["id"]: sp["name"] for sp in job.get("speaker_list", [])}
        changed = 0
        for e in edits or []:
            r = by_id.get(str(e.get("segment_id", "")))
            if not r:
                continue
            if e.get("speaker_id") in sp_names and e["speaker_id"] != r.get("speaker_id"):
                r["speaker_id"] = e["speaker_id"]
                r["speaker"] = sp_names[e["speaker_id"]]
                changed += 1
            if isinstance(e.get("text"), str):
                r["text"] = e["text"][:MAX_TEXT_LEN]
                changed += 1
            if isinstance(e.get("arabic_text"), str):
                r["arabic_text"] = e["arabic_text"][:MAX_TEXT_LEN]
                changed += 1
            if isinstance(e.get("emotion"), str):
                emo = clean_emotion(e["emotion"])
                if emo != r.get("emotion"):
                    r["emotion"] = emo
                    r["emotion_set"] = True
                    changed += 1
        if changed:
            _write_segments(job, rows)
    return True, changed


def set_line_time(job, segment_id, start, end):
    """Change when one line starts and ends. Returns (ok, message, rows)."""
    if job.get("status") != "editing":
        return False, "This job is not open for editing.", None
    try:
        start, end = round(float(start), 2), round(float(end), 2)
    except (TypeError, ValueError):
        return False, "Enter the times as numbers (for example 1:23.5 or 83.5).", None
    if not (math.isfinite(start) and math.isfinite(end)):
        return False, "Enter the times as numbers (for example 1:23.5 or 83.5).", None
    total = _total_secs(job)
    if total and end > total and end - total <= 0.01:
        end = round(total, 2)
    with _lock_for(job["id"]):
        rows = read_segments(job)
        r = next((x for x in rows if x["segment_id"] == segment_id), None)
        if not r:
            return False, "Line not found.", None
        err = _time_error(rows, segment_id, start, end, total)
        if err:
            return False, err, None
        r["start"], r["end"] = start, end
        rows = _by_start(rows)
        _write_segments(job, rows)
    _ev(job, "line_time_changed", "ok", f"{segment_id} -> {start}-{end}")
    return True, "", rows


def _new_segment_id(job, rows):
    top = -1
    for r in rows:
        m = re.fullmatch(r"seg_(\d+)", str(r.get("segment_id", "")))
        if m:
            top = max(top, int(m.group(1)))
    n = max(int(job.get("seg_counter") or 0), top + 1)
    job["seg_counter"] = n + 1
    return f"seg_{n}"


def insert_line(job, after_id):
    """Add an empty line right after the given line, in the free time between
    it and the next line. Returns (ok, message, rows, new_segment_id)."""
    if job.get("status") != "editing":
        return False, "This job is not open for editing.", None, None
    total = _total_secs(job)
    with _lock_for(job["id"]):
        rows = read_segments(job)
        if len(rows) >= MAX_SEGMENTS:
            return False, f"A project can have at most {MAX_SEGMENTS} lines.", None, None
        idx = next((i for i, x in enumerate(rows) if x["segment_id"] == after_id), None)
        if idx is None:
            return False, "Line not found.", None, None
        a = rows[idx]
        a_end = float(a["end"])
        later = [float(o["start"]) for o in rows if o is not a and float(o["start"]) >= a_end - OVERLAP_OK
                 and float(o["start"]) > float(a["start"])]
        limit = min(later) if later else total
        start = round(a_end, 2)
        end = round(min(start + NEW_LINE_SEC, limit), 2)
        if end - start < MIN_LINE_SEC:
            return False, ("There is no free time after this line for a new one. Shorten this line's time (or the next "
                           "line's) first, then insert."), None, None
        err = _time_error(rows, "", start, end, total)
        if err:
            return False, err, None, None
        sid = _new_segment_id(job, rows)
        rows.insert(idx + 1, {
            "segment_id": sid, "start": start, "end": end, "text": "", "arabic_text": "",
            "speaker": a.get("speaker"), "speaker_id": a.get("speaker_id"), "gender": a.get("gender") or "male",
            "emotion": "neutral", "words": [], "added": True})
        rows = _by_start(rows)
        _write_segments(job, rows)
    _save(job)
    _ev(job, "line_inserted", "ok", f"{sid} after {after_id} at {start}-{end}")
    return True, "", rows, sid


def _split_plan(row, position):
    """Where to cut a line's English text. Returns (plan, None) or (None, message).
    The cut is moved to the nearest gap between two words; the two new times come
    from the recognised word times when they still match the text, otherwise from
    how far into the text the cut is."""
    text = str(row.get("text") or "")
    toks = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    if len(toks) < 2:
        return None, "This line has only one word, so it can't be split."
    try:
        pos = int(position)
    except (TypeError, ValueError):
        pos = -1
    if pos <= 0 or pos >= len(text.rstrip()):
        return None, "Click in the English text, between the words where the second part starts, then press Split."
    k = sum(1 for (s, e) in toks if e <= pos)
    for (s, e) in toks:
        if s < pos < e and (pos - s) > (e - pos):
            k += 1
    if k < 1 or k > len(toks) - 1:
        return None, "Click between two words in the middle of the text, then press Split."
    left, right = text[:toks[k - 1][1]].strip(), text[toks[k][0]:].strip()
    start, end = float(row["start"]), float(row["end"])
    words = row.get("words") or []
    if len(words) == len(toks):
        w_left, w_right = words[:k], words[k:]
        t1 = float(w_left[-1]["end"])
        t2 = float(w_right[0]["start"])
    else:
        w_left, w_right = [], []
        frac = len(left) / float(max(1, len(left) + len(right)))
        t1 = t2 = start + (end - start) * frac
    t1, t2 = round(max(t1, start + MIN_LINE_SEC), 2), round(min(t2, end - MIN_LINE_SEC), 2)
    if t2 < t1:
        t1 = t2 = round((t1 + t2) / 2.0, 2)
    if t1 - start < MIN_LINE_SEC - 1e-6 or end - t2 < MIN_LINE_SEC - 1e-6:
        return None, f"One of the two lines would be shorter than {MIN_LINE_SEC:g} seconds. Put the cut nearer the middle of the line."
    return {"left": left, "right": right, "t1": t1, "t2": t2, "w_left": w_left, "w_right": w_right}, None


def split_line(job, segment_id, position):
    """Cut one line in two at a place in its English text (the user clicks
    between two words). Both parts keep the speaker; both are translated again
    together so each Arabic line matches its own English. Returns
    (ok, message, rows, new_segment_id)."""
    if job.get("status") != "editing":
        return False, "This job is not open for editing.", None, None
    rows = read_segments(job)
    if len(rows) >= MAX_SEGMENTS:
        return False, f"A project can have at most {MAX_SEGMENTS} lines.", None, None
    r0 = next((x for x in rows if x["segment_id"] == segment_id), None)
    if not r0:
        return False, "Line not found.", None, None
    plan, err = _split_plan(r0, position)
    if err:
        return False, err, None, None
    first = dict(r0)
    second = dict(r0)
    first.update({"text": plan["left"], "words": plan["w_left"], "end": plan["t1"], "arabic_text": ""})
    second.update({"text": plan["right"], "words": plan["w_right"], "start": plan["t2"], "arabic_text": "", "added": True})
    with _lock_for(job["id"]):
        rows = read_segments(job)
        cur = next((x for x in rows if x["segment_id"] == segment_id), None)
        if not cur or cur.get("text") != r0.get("text"):
            return False, "The line changed while it was being split. Please try again.", None, None
        new_id = _new_segment_id(job, rows)
    second["segment_id"] = new_id
    got = _translate_batch(job["id"], [first, second])
    if not all(got.get(x["segment_id"], ("",))[0] for x in (first, second)):
        return False, "The translation service didn't answer, so nothing was changed. Please try again.", None, None
    for x in (first, second):
        x["arabic_text"], emo = got[x["segment_id"]]
        if not x.get("emotion_set"):
            x["emotion"] = emo
    with _lock_for(job["id"]):
        rows = read_segments(job)
        idx = next((i for i, x in enumerate(rows) if x["segment_id"] == segment_id), None)
        if idx is None or rows[idx].get("text") != r0.get("text"):
            return False, "The line changed while it was being split. Please try again.", None, None
        rows[idx:idx + 1] = [first, second]
        rows = _by_start(rows)
        _write_segments(job, rows)
    _save(job)
    _ev(job, "line_split", "ok", f"{segment_id} -> {segment_id} + {new_id} at {plan['t1']}/{plan['t2']}")
    return True, "", rows, new_id


def delete_line(job, segment_id):
    """Remove one line. Returns (ok, message, rows)."""
    if job.get("status") != "editing":
        return False, "This job is not open for editing.", None
    with _lock_for(job["id"]):
        rows = read_segments(job)
        keep = [r for r in rows if r["segment_id"] != segment_id]
        if len(keep) == len(rows):
            return False, "Line not found.", None
        _write_segments(job, keep)
    _ev(job, "line_deleted", "ok", segment_id)
    return True, "", keep


def retranslate_line(job, segment_id):
    if job.get("status") != "editing":
        return False, "This job is not open for editing.", None
    rows = read_segments(job)
    r = next((x for x in rows if x["segment_id"] == segment_id), None)
    if not r:
        return False, "Line not found.", None
    if not (r.get("text") or "").strip():
        return False, "There is no English text to translate.", None
    got = _translate_batch(job["id"], [r])
    if segment_id not in got or not got[segment_id][0]:
        return False, "The translation service didn't answer. Please try again.", None
    new_ar, new_emo = got[segment_id]
    r["arabic_text"] = new_ar
    if not r.get("emotion_set"):      # a delivery the user picked is never overwritten
        r["emotion"] = new_emo
    _write_segments(job, rows)
    return True, "", r["arabic_text"]


def line_emotion(job, segment_id):
    r = next((x for x in read_segments(job) if x["segment_id"] == segment_id), None)
    return (r or {}).get("emotion") or "neutral"


# ------------------------------------------------------------ speakers

def _speaker_num(name):
    m = re.search(r"(\d+)\s*$", name or "")
    return int(m.group(1)) if m else 10 ** 6


def _init_speakers(job, rows):
    """Builds the speaker list from what was detected, and adds empty speakers
    up to the number the user said at upload (so the count he gave is honoured;
    lines can then be moved to them)."""
    names = sorted({r["speaker"] for r in rows}, key=lambda n: (_speaker_num(n), n))
    lst = [{"id": f"sp{i + 1}", "name": n} for i, n in enumerate(names)]
    by_name = {sp["name"]: sp["id"] for sp in lst}
    for r in rows:
        r["speaker_id"] = by_name[r["speaker"]]
    detected = len(lst)
    stated = max(1, min(MAX_SPEAKERS, int(job.get("stated_speakers") or detected)))
    k = len(lst)
    while len(lst) < stated:
        k += 1
        nm = f"Speaker {k}"
        while any(sp["name"].lower() == nm.lower() for sp in lst):
            k += 1
            nm = f"Speaker {k}"
        lst.append({"id": f"sp{len(lst) + 1}", "name": nm})
    job["speaker_list"] = lst
    job["detected_speakers"] = detected


def set_speakers(job, speakers):
    """Replace the speaker list (rename / add / remove). A speaker can only be
    removed when no line is assigned to it. Returns (ok, message)."""
    if job.get("status") != "editing":
        return False, "This job is not open for editing."
    if not isinstance(speakers, list) or not (1 <= len(speakers) <= MAX_SPEAKERS):
        return False, f"You can have between 1 and {MAX_SPEAKERS} speakers."
    existing = {sp["id"] for sp in job.get("speaker_list", [])}
    nums = [int(re.sub(r"\D", "", i) or 0) for i in existing] or [0]
    next_num = max(nums) + 1
    new, names, used_ids = [], set(), set()
    for item in speakers:
        name = re.sub(r"\s+", " ", str((item or {}).get("name", ""))).strip()[:40]
        if not name:
            return False, "Every speaker needs a name."
        if name.lower() in names:
            return False, f'Two speakers are called "{name}". Give each one a different name.'
        names.add(name.lower())
        sid = str((item or {}).get("id") or "")
        if sid not in existing or sid in used_ids:
            sid = f"sp{next_num}"
            next_num += 1
        used_ids.add(sid)
        new.append({"id": sid, "name": name})
    kept = {sp["id"]: sp["name"] for sp in new}
    rows = read_segments(job)
    for r in rows:
        if r.get("speaker_id") not in kept:
            return False, "A speaker that still has lines can't be removed. Move its lines to another speaker first."
    for r in rows:
        r["speaker"] = kept[r["speaker_id"]]
    _write_segments(job, rows)
    job["speaker_list"] = new
    job["speakers"] = [sp["name"] for sp in new]
    _save(job)
    _ev(job, "speakers_updated", "ok", "speakers: " + ", ".join(sp["name"] for sp in new))
    return True, ""


# ------------------------------------------------------ price + confirm

# ------------------------------------------------------------- lip-sync
#
# What "with lip-sync" does, step by step:
#   1. At confirm time the clips are planned from the edited text (so the price
#      is exact): only stretches where somebody speaks, each 4.2-14.5 seconds,
#      cut in the pauses between lines, snapped to exact video frames.
#   2. After the dub is mixed, every clip goes (picture + the dubbed speech of
#      that stretch) to the lip-sync engine, a few at a time.
#   3. A clip the engine cannot do is refunded and keeps the original picture.
#   4. All pieces (lip-synced clips + untouched stretches) are joined back into
#      one silent video of the exact original length; the audio of the final
#      file is always OUR mix, never the engine's.

_STD_FPS = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
            Fraction(50), Fraction(60000, 1001), Fraction(60)]


def lipsync_available():
    """True when everything the lip-sync engine needs is configured: switched
    on, not in test mode, Alibaba key + workspace, and the R2 storage the
    clips are staged in."""
    try:
        import config
        import r2_backup
        return bool(config.LIPSYNC_ENABLED and not config.LIPSYNC_TEST_MODE
                    and config.DASHSCOPE_API_KEY and config.DASHSCOPE_WORKSPACE_ID
                    and r2_backup._enabled())
    except Exception:
        return False


def _pick_fps(text):
    """Frame rate to deliver at: the source's own, snapped to the usual
    broadcast rates when it is within 2% of one (29.97, 25, ...); rates that are
    not sensible (variable or broken metadata) become 30."""
    try:
        n, d = str(text).split("/")
        v = float(n) / float(d)
    except Exception:
        return Fraction(30)
    if not (8.0 <= v <= 120.0):
        return Fraction(30)
    best = min(_STD_FPS, key=lambda f: abs(float(f) - v))
    if abs(float(best) - v) / float(best) < 0.02:
        return best
    return Fraction(v).limit_denominator(1001)


def _capped_size(w, h):
    """Picture size lip-synced videos are delivered in: the source size, but
    at most 1280x720 (720x1280 upright), even numbers."""
    w, h = max(2, int(w)), max(2, int(h))
    s = min(1.0, LIPSYNC_MAX_SIZE[0] / float(max(w, h)), LIPSYNC_MAX_SIZE[1] / float(min(w, h)))
    return max(2, int(w * s) // 2 * 2), max(2, int(h * s) // 2 * 2)


def _video_geometry(path):
    """(frame rate as Fraction, width, height as the picture is SHOWN) of the
    first video stream, or None."""
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,avg_frame_rate,r_frame_rate:stream_tags=rotate:stream_side_data=rotation",
             "-of", "json", str(path)], timeout=60)
        st = (json.loads(out.decode() or "{}").get("streams") or [None])[0]
        if not st:
            return None
        w, h = int(st["width"]), int(st["height"])
        rot = 0
        try:
            rot = int(float((st.get("tags") or {}).get("rotate", 0)))
        except Exception:
            pass
        for sd in st.get("side_data_list") or []:
            if "rotation" in sd:
                try:
                    rot = int(float(sd["rotation"]))
                except Exception:
                    pass
        if abs(rot) % 180 == 90:
            w, h = h, w
        fps = _pick_fps(st.get("avg_frame_rate") if st.get("avg_frame_rate") not in (None, "0/0") else st.get("r_frame_rate"))
        cw, ch = _capped_size(w, h)
        return fps, cw, ch
    except Exception:
        return None


def plan_lipsync_clips(lines, total, fps):
    """lines: [(start, end)] of the dubbed lines; total: length in seconds;
    fps: Fraction. Returns (clips, short) where clips is a list of
    {"f0", "f1", "dur"} (frame numbers; frame f is at f/fps seconds) that are
    sorted, never overlap, and last LIPSYNC_MIN_CLIP..LIPSYNC_MAX_CLIP seconds,
    and short is the number of speaking moments too short (and too boxed in by
    neighbouring clips) to become a clip -- those keep the original picture.
    Deterministic: the same input always gives the same plan (the price shown
    to the user is computed from it)."""
    fr = float(fps)
    n_total = int(math.floor(float(total) * fr))
    body = LIPSYNC_MAX_CLIP - LIPSYNC_HEAD - LIPSYNC_TAIL
    items = []
    for s, e in sorted((max(0.0, float(a)), min(float(total), float(b))) for a, b in lines):
        if e - s <= 0.01:
            continue
        pieces = int(math.ceil((e - s) / body))
        for k in range(pieces):
            items.append((s + (e - s) * k / pieces, s + (e - s) * (k + 1) / pieces))
    groups, cur = [], None
    for s, e in items:
        if cur is not None and (e + LIPSYNC_TAIL) - (cur[0] - LIPSYNC_HEAD) <= LIPSYNC_MAX_CLIP:
            cur[1] = max(cur[1], e)
        else:
            if cur is not None:
                groups.append(cur)
            cur = [s, e]
    if cur is not None:
        groups.append(cur)
    # frame boundaries; between two neighbouring groups the cut is midway
    # between the last word of one and the first word of the next
    bounds = []
    for k, (s, e) in enumerate(groups):
        t0 = max(0.0, s - LIPSYNC_HEAD)
        t1 = min(float(total), e + LIPSYNC_TAIL)
        if bounds and t0 < bounds[-1][1]:
            b = (groups[k - 1][1] + s) / 2.0
            bounds[-1][1] = min(bounds[-1][1], b)
            t0 = b
        bounds.append([t0, t1])
    clips = []
    for t0, t1 in bounds:
        f0, f1 = int(round(t0 * fr)), int(round(t1 * fr))
        f0, f1 = max(0, f0), min(n_total, f1)
        if f1 > f0:
            clips.append({"f0": f0, "f1": f1})
    # no overlap after rounding
    for k in range(1, len(clips)):
        if clips[k]["f0"] < clips[k - 1]["f1"]:
            clips[k]["f0"] = clips[k - 1]["f1"]
    clips = [c for c in clips if c["f1"] > c["f0"]]
    # too short: grow into free picture on either side, else give up on it
    min_f = int(math.ceil(LIPSYNC_MIN_CLIP * fr))
    max_f = int(math.floor(LIPSYNC_MAX_CLIP * fr))
    short = 0
    out = []
    for k, c in enumerate(clips):
        lo = out[-1]["f1"] if out else 0
        hi = clips[k + 1]["f0"] if k + 1 < len(clips) else n_total
        need = min_f - (c["f1"] - c["f0"])
        if need > 0:
            right = min(need, hi - c["f1"])
            c["f1"] += right
            need -= right
            left = min(need, c["f0"] - lo)
            c["f0"] -= left
            need -= left
        if need > 0:
            short += 1
            continue
        if c["f1"] - c["f0"] > max_f:      # rounding only; never more than a frame or two
            c["f1"] = c["f0"] + max_f
        out.append(c)
    for c in out:
        c["dur"] = round((c["f1"] - c["f0"]) / fr, 3)
    return out, short


def lipsync_price(job, rows, cfg):
    """Exact lip-sync price for the text as it stands now. rows: the dubbed
    lines (with Arabic text). Returns a dict, or None when lip-sync was not
    chosen for this job."""
    ls = job.get("lipsync") or {}
    if not ls.get("wanted"):
        return None
    per_sec = float(cfg.get("lipsync_per_sec", 40))
    total = float((job.get("analysis") or {}).get("audio_duration") or job.get("duration") or 0)
    fps = Fraction(ls.get("fps") or "30")
    clips, short = plan_lipsync_clips([(r["start"], r["end"]) for r in rows], total, fps)
    for c in clips:
        c["credits"] = max(1, int(round(c["dur"] * per_sec)))
    secs = sum(c["dur"] for c in clips)
    lo_hi = lipsync_range(secs) if clips else {"lipsync_min": 0, "lipsync_max": 0}
    return {"clips": clips, "short": short, "seconds": round(secs, 1), "per_sec": per_sec,
            "credits": sum(c["credits"] for c in clips), "n": len(clips),
            "time_min": lo_hi["lipsync_min"], "time_max": lo_hi["lipsync_max"]}


def _enc_common():
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an",
            "-threads", "2", "-movflags", "+faststart"]


def _encode_from_source(src, f0, f1, fps, w, h, out):
    """Exactly f1-f0 frames of the source, in the delivery format."""
    tmp = out.with_name(out.stem + ".tmp.mp4")
    vf = f"fps={fps},scale={w}:{h}:flags=lanczos,setsar=1,format=yuv420p,tpad=stop_mode=clone:stop_duration=3"
    with _FF_LOCAL:
        ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-ss", f"{float(f0 / fps):.6f}", "-i", str(src), "-vf", vf,
                                 "-frames:v", str(f1 - f0)] + _enc_common() + [str(tmp)])
    os.replace(tmp, out)


def _encode_engine_output(raw, f0, f1, fps, w, h, out):
    """The engine's clip, brought to the delivery format and to exactly
    f1-f0 frames (its own frame rate/size/length may differ a little)."""
    tmp = out.with_name(out.stem + ".tmp.mp4")
    vf = (f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,tpad=stop_mode=clone:stop_duration=3")
    with _FF_LOCAL:
        ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-i", str(raw), "-vf", vf, "-frames:v", str(f1 - f0)] + _enc_common() + [str(tmp)])
    os.replace(tmp, out)


def _engine_clip(job, src, dub_full, f0, f1, fps, w, h, work, tag):
    """Lip-sync one clip. Returns (task_id, None) on success, (task_id, error)
    on failure. Runs in a worker thread and never touches the job dict."""
    import lipsync_service
    from config import DASHSCOPE_API_KEY, DASHSCOPE_WORKSPACE_ID, DASHSCOPE_REGION
    t0 = float(f0 / fps)
    dur = float((f1 - f0) / fps)
    inp = work / f"{tag}_in.mp4"
    aud = work / f"{tag}_dub.mp3"
    raw = work / f"{tag}_raw.mp4"
    prog = {}
    try:
        with _FF_LOCAL:
            ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-ss", f"{t0:.6f}", "-i", str(src), "-t", f"{dur:.6f}",
                                     "-map", "0:v:0", "-map", "0:a:0?",
                                     "-vf", f"scale={w}:{h}:flags=lanczos,setsar=1,format=yuv420p",
                                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "96k",
                                     "-threads", "2", "-movflags", "+faststart", str(inp)])
            ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-ss", f"{t0:.6f}", "-t", f"{dur:.6f}", "-i", str(dub_full),
                                     "-ac", "2", "-ar", "44100", "-codec:a", "libmp3lame", "-q:a", "2", str(aud)])
        last = None
        for wait in LIPSYNC_RETRY_WAITS:
            if wait:
                time.sleep(wait)
            try:
                with _LIPSYNC_SLOTS:
                    lipsync_service._alibaba_wan3_lipsync(inp, aud, DASHSCOPE_API_KEY, DASHSCOPE_WORKSPACE_ID,
                                                          DASHSCOPE_REGION, raw, prog, job["id"], None)
                last = None
                break
            except Exception as ex:
                last = ex
                msg = str(ex)
                # only failures that happened BEFORE a task existed are worth repeating
                if not (re.match(r"Lip-sync create HTTP (429|500|502|503|504)", msg) or msg.startswith("Could not stage")):
                    break
        if last is not None:
            return prog.get("generation_id"), last
        if not raw.exists() or raw.stat().st_size < 1000:
            return prog.get("generation_id"), Exception("the engine returned no picture")
        return prog.get("generation_id"), None
    except Exception as ex:
        return prog.get("generation_id"), ex


def _run_lipsync(job, dub_full, kept, total, d):
    """Lip-sync stage. Returns the path of the joined picture-only video, or
    None when nothing could be lip-synced (the original picture is then used
    as it is). Resumable: finished clips are kept on disk and in job['dub']['lip']."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    wd = _wd(job)
    uid = job["uid"]
    dub = job["dub"]
    plan = (job.get("dub_plan") or {}).get("lipsync") or {}
    clips = plan.get("clips") or []
    if not clips:
        _ev(job, "lipsync", "info", "no speaking stretch long enough for a lip-sync clip")
        return None
    ls = job["lipsync"]
    fps = Fraction(ls["fps"])
    w, h = int(ls["w"]), int(ls["h"])
    n_total = int(math.floor(float(total) * float(fps)))
    src = wd / f"src{job['ext']}"
    ldir = d / "lip"
    ldir.mkdir(parents=True, exist_ok=True)
    state = dub.setdefault("lip", {})

    # order of pieces on the timeline: untouched stretch, clip, untouched stretch...
    pieces, pos = [], 0
    for k, c in enumerate(clips):
        if c["f0"] > pos:
            pieces.append(("gap", pos, c["f0"], None))
        pieces.append(("clip", c["f0"], c["f1"], k))
        pos = c["f1"]
    if pos < n_total:
        pieces.append(("gap", pos, n_total, None))

    def pfile(j):
        return ldir / f"p{j:03d}.mp4"

    def clip_done(k):
        j = [i for i, p in enumerate(pieces) if p[3] == k][0]
        return state.get(str(k), {}).get("state") in ("done", "failed", "skipped") and pfile(j).exists()

    todo = [k for k in range(len(clips)) if not clip_done(k)]
    n_clips = len(clips)
    _ev(job, "lipsync", "info", f"{n_clips} clips ({sum(c['dur'] for c in clips):.1f}s), {len(todo)} still to do, {w}x{h} at {fps} fps")
    started = time.time()
    finished = {"n": n_clips - len(todo)}

    def worker(k):
        c = clips[k]
        f0, f1 = c["f0"], c["f1"]
        t0, t1 = float(f0 / fps), float(f1 / fps)
        spoken = any(it["start"] < t1 and it["start"] + it["allowed"] > t0 for it in kept)
        if not spoken:
            return {"k": k, "status": "skipped", "why": "no dubbed speech was left in this stretch", "task": None, "secs": 0}
        j = [i for i, p in enumerate(pieces) if p[3] == k][0]
        t_start = time.time()
        task, err = _engine_clip(job, src, dub_full, f0, f1, fps, w, h, ldir, f"k{k:03d}")
        if err is None:
            try:
                _encode_engine_output(ldir / f"k{k:03d}_raw.mp4", f0, f1, fps, w, h, pfile(j))
            except Exception as ex:
                err = ex
        for suffix in ("_in.mp4", "_dub.mp3", "_raw.mp4"):
            try:
                (ldir / f"k{k:03d}{suffix}").unlink()
            except Exception:
                pass
        if err is not None:
            return {"k": k, "status": "failed", "why": f"{type(err).__name__}: {err}"[:300], "task": task, "secs": time.time() - t_start}
        return {"k": k, "status": "done", "why": "", "task": task, "secs": time.time() - t_start}

    def refund_clip(k, why):
        st = state.setdefault(str(k), {})
        if st.get("refunded"):
            return
        credits = int(clips[k].get("credits", 0))
        if credits and job["paid"].get("dub", 0) >= credits:
            try:
                Hooks.refund(uid, credits, job["id"])
                job["paid"]["dub"] -= credits
                st["refunded"] = credits
                _ev(job, "lipsync_refund", "ok", f"clip {k + 1}: {why}: {credits} credits back", credits)
            except Exception as ex:
                _ev(job, "lipsync_refund", "failed", f"clip {k + 1}: {ex}")

    def fallback_piece(k):
        j = [i for i, p in enumerate(pieces) if p[3] == k][0]
        _encode_from_source(src, clips[k]["f0"], clips[k]["f1"], fps, w, h, pfile(j))

    # The waiting for the engine is done WITHOUT holding a worker slot, so a
    # long lip-sync never blocks other people's jobs. What is left after it
    # (joining the pieces, the final file) is light, so the slot is not taken
    # back; the worker thread simply won't release it a second time.
    if getattr(_slot_state, "held", False):
        _slot_state.held = False
        _worker_slots.release()
    pool = ThreadPoolExecutor(max_workers=LIPSYNC_PARALLEL)
    futs = []
    try:
        futs = [pool.submit(worker, k) for k in todo]
        # untouched stretches are encoded while the engine is busy
        for j, (kind, f0, f1, k) in enumerate(pieces):
            if kind == "gap" and not pfile(j).exists():
                _encode_from_source(src, f0, f1, fps, w, h, pfile(j))
        for fut in as_completed(futs):
            r = fut.result()
            k = r["k"]
            c = clips[k]
            label = f"clip {k + 1}/{n_clips} ({c['dur']:.1f}s at {c['f0'] / float(fps):.1f}s)"
            st = state.setdefault(str(k), {})
            if r["status"] == "done":
                st.update({"state": "done", "task": r["task"], "secs": round(r["secs"], 1)})
                _ev(job, "lipsync_clip", "ok", f"{label} task={r['task']} {r['secs']:.0f}s")
                if not job["dub"].get("resumed"):
                    _record_speed("lipsync", c["dur"], r["secs"])
            else:
                fallback_piece(k)
                st.update({"state": r["status"], "task": r["task"], "why": r["why"]})
                _ev(job, "lipsync_clip", "failed" if r["status"] == "failed" else "skipped", f"{label} task={r['task']} {r['why']}")
                refund_clip(k, "clip not lip-synced")
            finished["n"] += 1
            _mark(job, "lipsync", 86 + int(9 * finished["n"] / max(1, n_clips)), f"Lip-syncing (clip {finished['n']} of {n_clips})...")
            _save(job)
        pool.shutdown(wait=True)
    except BaseException:
        for f in futs:
            f.cancel()
        pool.shutdown(wait=False)
        raise

    n_done = sum(1 for k in range(n_clips) if state.get(str(k), {}).get("state") == "done")
    n_failed = sum(1 for k in range(n_clips) if state.get(str(k), {}).get("state") == "failed")
    n_skip = sum(1 for k in range(n_clips) if state.get(str(k), {}).get("state") == "skipped")
    if n_done == 0:
        _ev(job, "lipsync", "failed", f"no clip could be lip-synced ({n_failed} failed, {n_skip} without speech); original picture kept")
        job.setdefault("warnings", []).append(
            "The lip-sync could not be done for this video, so the original picture was kept. The price of the lip-sync was refunded.")
        return None
    # join everything into one picture-only video of the exact length
    lst = ldir / "list.txt"
    lst.write_text("".join(f"file '{pfile(j).name}'\n" for j in range(len(pieces))), encoding="utf-8")
    out = d / "video_lip.mp4"
    with _FF_LOCAL:
        ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                                 "-movflags", "+faststart", str(out)])
    got = ffmpeg_utils.get_media_duration(out)
    want = n_total / float(fps)
    if abs(got - want) > 0.2:
        raise Exception(f"joined picture is {got:.2f}s long, expected {want:.2f}s")
    if n_failed or n_skip:
        job.setdefault("warnings", []).append(
            f"{n_failed + n_skip} of {n_clips} lip-sync clip(s) could not be lip-synced and keep the original mouth movement; "
            "their price was refunded.")
    _ev(job, "lipsync", "ok" if not n_failed else "partial",
        f"{n_done} of {n_clips} clips lip-synced, {n_failed} failed, {n_skip} without speech, {time.time() - started:.0f}s")
    return out


def _refund_all_lipsync(job, why):
    """The lip-sync stage broke as a whole: give back every lip-sync credit
    that has not been given back yet, and go on with the original picture."""
    uid = job["uid"]
    plan = (job.get("dub_plan") or {}).get("lipsync") or {}
    state = job.setdefault("dub", {}).setdefault("lip", {})
    todo = []
    for k, c in enumerate(plan.get("clips") or []):
        st = state.setdefault(str(k), {})
        cr = int(c.get("credits", 0))
        if not st.get("refunded") and cr:
            todo.append((k, cr))
    back = sum(cr for _, cr in todo)
    if back and job["paid"].get("dub", 0) >= back:
        try:
            Hooks.refund(uid, back, job["id"])
            for k, cr in todo:
                state[str(k)]["refunded"] = cr
            job["paid"]["dub"] -= back
            _ev(job, "lipsync_refund", "ok", f"lip-sync stage failed ({why}): {back} credits back", back)
        except Exception as ex:
            _ev(job, "lipsync_refund", "failed", str(ex))
    job.setdefault("warnings", []).append("The lip-sync could not be completed, so the original picture was kept. Its price was refunded.")
    _save(job)


def dub_price(job, cfg=None):
    """Exact price of the dubbing stage, from the text as it stands now."""
    cfg = cfg or Hooks.pricing()
    import inworld_service
    rows = read_segments(job)
    names = {sp["id"]: sp["name"] for sp in job.get("speaker_list", [])}
    chars = 0
    lines = 0
    without = 0
    used = {}
    for r in rows:
        ar = (r.get("arabic_text") or "").strip()
        if not ar:
            if (r.get("text") or "").strip():
                without += 1
            continue
        chars += len(inworld_service.instruction_tag(r.get("emotion"))) + len(ar)
        lines += 1
        used[r.get("speaker_id")] = used.get(r.get("speaker_id"), 0) + 1
    cpc = max(1, int(cfg.get("chars_per_credit", 60)))
    clone_each = int(cfg.get("clone_credits", 5))
    voice = int(math.ceil(chars / float(cpc))) if chars else 0
    clones = clone_each * len(used)
    merge = max(1, int(cfg.get("merge_credits", 1)))
    due = voice + clones + merge
    lip = lipsync_price(job, [r for r in rows if (r.get("arabic_text") or "").strip()], cfg)
    if lip:
        due += lip["credits"]
    already = int(job["paid"].get("fee", 0)) + int(job["paid"].get("analysis", 0))
    dur = float(job.get("duration") or 0)
    d = _speed_factor("dubbing") * dur + DUB_BASE_SEC
    lo = max(1, int(d * 0.7 / 60))
    hi = max(lo + 1, int(math.ceil(d * 1.6 / 60)))
    if lip:
        lo += lip["time_min"]
        hi += lip["time_max"]
    return {
        "lines": lines, "chars": chars, "voice": voice, "clones": clones, "clone_each": clone_each,
        "merge": merge, "due": due, "already_paid": already, "total": already + due,
        "lines_without_arabic": without, "chars_per_credit": cpc,
        "speakers_used": [{"id": k, "name": names.get(k, "?"), "lines": v} for k, v in used.items()],
        "time_min": lo, "time_max": hi,
        "lipsync": ({"clips": lip["n"], "seconds": lip["seconds"], "credits": lip["credits"], "per_sec": lip["per_sec"],
                     "short": lip["short"], "time_min": lip["time_min"], "time_max": lip["time_max"]} if lip else None),
    }


def confirm(job, uid, expected_due):
    """User reviewed everything and accepts the exact price: charge it and
    start the dubbing. Returns (True, None) or (False, (message, status))."""
    if job.get("status") != "editing":
        return False, ("This job is not waiting for confirmation.", 409)
    added, terr = ensure_tashkeel(job)      # words typed without tashkeel get it (this changes the price)
    if terr:
        return False, (terr, 503)
    price = dub_price(job)
    if price["lines"] <= 0:
        return False, ("There is no Arabic text to dub yet.", 400)
    if not INWORLD_API_KEY:
        _ev(job, "dub_confirmed", "failed", "voice service (Inworld) is not configured")
        return False, ("The voice service is not available right now. Nothing was charged. Please try again later.", 503)
    try:
        if int(expected_due) != price["due"]:
            return False, ("The price changed because the text was edited. Please check the new price and confirm again.", 409)
    except (TypeError, ValueError):
        return False, ("Missing price confirmation.", 400)
    lip_plan = None
    if (job.get("lipsync") or {}).get("wanted"):
        if not lipsync_available():
            _ev(job, "dub_confirmed", "failed", "lip-sync engine is not available")
            return False, ("Lip-sync is not available right now. Nothing was charged. Please try again later.", 503)
        cfg = Hooks.pricing()
        lp = lipsync_price(job, [r for r in read_segments(job) if (r.get("arabic_text") or "").strip()], cfg)
        if lp["credits"] != (price.get("lipsync") or {}).get("credits"):
            return False, ("The price changed. Please check the new price and confirm again.", 409)
        lip_plan = {"per_sec": lp["per_sec"], "credits": lp["credits"], "seconds": lp["seconds"], "short": lp["short"],
                    "clips": [{"f0": c["f0"], "f1": c["f1"], "dur": c["dur"], "credits": c["credits"]} for c in lp["clips"]]}
    bal = Hooks.get_credits(uid)
    if bal is not None and bal < price["due"]:
        _ev(job, "dub_confirmed", "failed", f"not enough credits (need {price['due']}, have {bal})")
        return False, (f"Not enough credits. Dubbing costs {price['due']} credits and you have {bal}. Use Buy to top up.", 402)
    with _lock_for(job["id"]):
        Hooks.charge(uid, price["due"], "long_dub_dub", job["id"])
        job["paid"]["dub"] = price["due"]
        job["dub_plan"] = {"chars": price["chars"], "cpc": price["chars_per_credit"], "clone_each": price["clone_each"],
                           "merge": price["merge"], "lines": price["lines"],
                           "speakers": [s["id"] for s in price["speakers_used"]], "confirmed_at": _now()}
        if lip_plan is not None:
            job["dub_plan"]["lipsync"] = lip_plan
        job["status"] = "confirmed"
        job["stage"] = "queued"
        job["percent"] = 0
        job["message"] = "Waiting for a free processing slot..."
    _save(job)
    _ev(job, "dub_confirmed", "ok",
        f"due={price['due']} (voice {price['voice']}, clones {price['clones']}, merge {price['merge']}"
        + (f", lip-sync {lip_plan['credits']} for {len(lip_plan['clips'])} clips / {lip_plan['seconds']}s at {lip_plan['per_sec']:g}/s" if lip_plan else "")
        + f") chars={price['chars']} lines={price['lines']} speakers={len(price['speakers_used'])}", price["due"])
    start_worker(job["id"])
    return True, None


# --------------------------------------------------------------- dubbing

DUB_CHUNK_SPAN = 45.0          # seconds of dubbed speech mixed per ffmpeg call
# The app's "good" stretching values (same as tempo_mode "good" in the normal
# flow: eleven_service.py / tts_service.py). Every long dub uses them by default.
# A line that is still too long after stretching is allowed to continue into the
# silence after it (never over the next line) -- see _plan_timeline.
TEMPO_MIN = 0.85
try:      # the fastest a line may be played before its wording is shortened (env LONGDUB_TEMPO_MAX, 1.1 - 1.5)
    TEMPO_MAX = min(1.5, max(1.1, float(os.environ.get("LONGDUB_TEMPO_MAX") or 1.25)))
except ValueError:
    TEMPO_MAX = 1.25
MAX_FAILED_LINE_SHARE = 0.10   # more failed lines than this and the whole job fails (refunded)


def _delete_pending_voices(job):
    """Delete every temporary cloned voice this job still has on Inworld.
    Voices are never kept: they are removed as soon as the job ends, whether
    it finished, failed or was deleted. Anything that can't be deleted right
    now stays listed and is retried by the hourly housekeeping."""
    ids = list(job.get("voices_pending_delete") or [])
    if not ids:
        return
    import inworld_service
    remaining = []
    for vid in ids:
        res = {"ok": False, "error": "no key"}
        if INWORLD_API_KEY:
            res = inworld_service.delete_voice(vid, INWORLD_API_KEY)
        if res.get("ok") or "404" in str(res.get("error", "")):
            _ev(job, "voice_deleted", "ok", vid)
        else:
            remaining.append(vid)
            _ev(job, "voice_deleted", "failed", f"{vid}: {res.get('error')}")
    job["voices_pending_delete"] = remaining
    if job_dir(job["id"]).exists():
        _save(job)


def sweep_voices():
    """Retry deleting temporary voices left behind (e.g. Inworld was down when
    the job ended)."""
    if not LONG_DIR.exists():
        return
    for d in LONG_DIR.iterdir():
        if not d.is_dir():
            continue
        job = load_job(d.name)
        if job and job.get("voices_pending_delete") and job["id"] not in _RUNNING \
                and job.get("status") not in ("confirmed", "dubbing"):
            _delete_pending_voices(job)


def _clone_sample(job, spid, rows, out_dir):
    """One reference clip (up to ~20 s, well under Inworld's 30 s limit) for a
    speaker, cut from that speaker's longest lines in the separated voices.
    Returns (path or None, seconds)."""
    wd = _wd(job)
    ref = wd / "vocals_mono.wav"
    if not ref.exists():
        ref = wd / "audio.wav"
    total = float(job.get("analysis", {}).get("audio_duration") or 0) or ffmpeg_utils.get_media_duration(ref)
    cands = [r for r in rows if r.get("speaker_id") == spid and (r.get("text") or "").strip()
             and r["end"] - r["start"] > 0.3]
    cands.sort(key=lambda r: r["end"] - r["start"], reverse=True)
    parts, acc = [], 0.0
    for r in cands:
        if acc >= 20.0:
            break
        a = max(0.0, float(r["start"]) - 0.35)
        b = min(total, float(r["end"]) + 0.35)
        d = min(b - a, 28.0 - acc)
        if d < 0.3:
            continue
        out = out_dir / f"ref_{spid}_{len(parts)}.wav"
        ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-ss", f"{a:.3f}", "-t", f"{d:.3f}", "-i", str(ref), "-vn", "-ac", "1",
                                 "-ar", str(SAMPLE_RATE), "-acodec", "pcm_s16le", str(out)])
        if out.exists() and out.stat().st_size > 2000:
            parts.append(out)
            acc += d
    if acc < 0.5 or not parts:
        return None, acc
    final = out_dir / f"ref_{spid}.wav"
    if len(parts) == 1:
        shutil.copy(parts[0], final)
    else:
        _concat_wavs(parts, final)
    if acc < 1.0:
        padded = out_dir / f"ref_{spid}_pad.wav"
        ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-i", str(final), "-af", f"apad=pad_dur={1.15 - acc:.3f}", "-ac", "1",
                                 "-ar", str(SAMPLE_RATE), "-acodec", "pcm_s16le", str(padded)])
        final = padded
        acc = 1.15
    for p in parts:
        try:
            p.unlink()
        except Exception:
            pass
    return final, acc


def _clone_with_retry(name, wav):
    import inworld_service
    # The sample is English speech, so the voice is labelled English (see
    # inworld_service.CLONE_SAMPLE_LANGUAGE, changeable with the Railway variable
    # INWORLD_CLONE_LANGUAGE). If the service refuses the label, the default
    # one is tried.
    last = ""
    lang = inworld_service.CLONE_SAMPLE_LANGUAGE
    for attempt in range(3):
        try:
            return inworld_service.clone_voice_from_file(name, wav, INWORLD_API_KEY, language_code=lang), ""
        except Exception as ex:
            last = inworld_service._http_error_detail(ex)
            if lang != inworld_service.DEFAULT_LANGUAGE and "language" in str(last).lower():
                lang = inworld_service.DEFAULT_LANGUAGE
            time.sleep(5 * (attempt + 1))
    return None, last


def _tts_with_retry(voice_id, text):
    import inworld_service
    import urllib.error
    last = ""
    for wait in (0, 4, 12, 30):
        if wait:
            time.sleep(wait)
        try:
            return inworld_service.synthesize(voice_id, text, INWORLD_API_KEY, language="ar"), ""
        except urllib.error.HTTPError as ex:
            last = inworld_service._http_error_detail(ex)
            if ex.code in (400, 401, 403, 404, 422):
                break
        except Exception as ex:
            last = str(ex)
    return None, last


SILENCE_TRIM_DB = -50          # below this a voice is treated as silence at the start/end of a line
GAIN_MAX_DB = 8.0              # most a line is made louder / quieter to match the original speaker
PEAK_CEIL_DB = -1.0            # a boosted line never peaks above this


def _trim_silence(raw_path, out_wav):
    """Copy of a generated line without the silence the voice adds before and
    after the words (a little is kept so nothing is clipped). Returns its
    length, or None when nothing sensible came out."""
    af = (f"silenceremove=start_periods=1:start_threshold={SILENCE_TRIM_DB}dB:start_silence=0.03,areverse,"
          f"silenceremove=start_periods=1:start_threshold={SILENCE_TRIM_DB}dB:start_silence=0.08,areverse")
    try:
        ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-i", str(raw_path), "-af", af, "-ac", "2", "-ar", str(SAMPLE_RATE),
                                 "-acodec", "pcm_s16le", str(out_wav)])
        d = ffmpeg_utils.get_media_duration(out_wav)
        return d if d >= 0.15 else None
    except Exception:
        return None


def _speech_levels(path, start=None, dur=None):
    """(mean_db, max_db) of the SPEECH in a file or a slice of it -- pauses are
    left out, so a line with pauses is not judged quieter than it is. Uses the
    info log level because volumedetect prints its result at that level (the
    shared helper in ffmpeg_utils asks for errors only and so never sees it).
    Returns (None, None) when there is no speech to measure."""
    cmd = ["ffmpeg", "-nostats", "-hide_banner"]
    if start is not None:
        cmd += ["-ss", f"{float(start):.3f}"]
    if dur is not None:
        cmd += ["-t", f"{float(dur):.3f}"]      # before -i: the slice is cut from the INPUT (the filter drops silence, so an output limit would read on)
    cmd += ["-i", str(path)]
    cmd += ["-af", "silenceremove=start_periods=1:start_threshold=-45dB:stop_periods=-1:stop_duration=0.25:stop_threshold=-45dB,"
                   "volumedetect", "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        m1 = re.search(r"mean_volume:\s*(-?[0-9.]+)\s*dB", p.stderr or "")
        m2 = re.search(r"max_volume:\s*(-?[0-9.]+)\s*dB", p.stderr or "")
        if m1 and m2:
            return float(m1.group(1)), float(m2.group(1))
    except Exception:
        pass
    return None, None


def _volume_stats(path):
    """(mean_db, max_db) of a whole file, or (None, None)."""
    try:
        p = subprocess.run(["ffmpeg", "-nostats", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=300)
        m1 = re.search(r"mean_volume:\s*(-?[0-9.]+)\s*dB", p.stderr or "")
        m2 = re.search(r"max_volume:\s*(-?[0-9.]+)\s*dB", p.stderr or "")
        if m1 and m2:
            return float(m1.group(1)), float(m2.group(1))
    except Exception:
        pass
    return None, None


FAINT_BG_MEAN_DB = -50.0     # a separated background this quiet on average is reported to the user
# what the background goes through on its way into the final video (volume=0.8 is -1.9 dB)
BG_MIX_FILTER = ("highpass=f=80:poles=2,highpass=f=80:poles=2,highshelf=f=2500:g=5:t=q:w=0.707,"
                 "alimiter=limit=0.95,volume=0.8")


def _bg_final_event(job, wd, bg_mix, dub_full):
    """Log how loud the background really is in the final video, next to the original's and the voices'."""
    try:
        pauses = []
        pp = wd / "pauses.json"
        if pp.exists():
            pauses = [tuple(x) for x in json.loads(pp.read_text(encoding="utf-8"))]
        rep = bg_duck.final_level_report(bg_mix, BG_MIX_FILTER, dub_full, pauses)
        if not rep:
            return
        msg = f"background in the final video {rep['final_bg_all']} dB overall"
        if "final_bg_pauses" in rep:
            msg += f", {rep['final_bg_pauses']} dB in the pauses of the voices"
        if "final_bg_while_dub_speaks" in rep:
            msg += f", {rep['final_bg_while_dub_speaks']} dB while the dubbed voice speaks"
        if "dub_voice" in rep:
            msg += f"; dubbed voice {rep['dub_voice']} dB"
        _ev(job, "background_final", "info", msg)
    except Exception as ex:
        print(f"[longdub] final level report skipped: {ex}")


def _bg_levels_event(job, wd, bg):
    """Log how loud the original and the separated background are (overall and in the pauses of
    the voices), so 'no music' can be told apart from 'music lost in the separation'."""
    try:
        pauses = []
        pp = wd / "pauses.json"
        if pp.exists():
            pauses = [tuple(x) for x in json.loads(pp.read_text(encoding="utf-8"))]
        orig = wd / "audio.wav"
        if not orig.exists():
            return
        voc = wd / "vocals.wav"
        rep = bg_duck.level_report(orig, bg, pauses, voc if voc.exists() else None)
        if not rep:
            return
        msg = f"original {rep['orig_all']} dB, separated background {rep['bg_all']} dB overall"
        if "orig_pauses" in rep:
            msg += (f"; in the {rep['pause_sec']:g}s where nobody speaks: original {rep['orig_pauses']} dB, "
                    f"background {rep['bg_pauses']} dB")
            if "voc_pauses" in rep:
                msg += f"; separated voices track in those pauses {rep['voc_pauses']} dB"
        _ev(job, "background_levels", "info", msg)
    except Exception as ex:
        print(f"[longdub] level report skipped: {ex}")


def _pick_tempo(actual, slot, room):
    """How fast to play a generated line of `actual` seconds.
    slot = the length the original line had; room = the time the line may
    really use (its slot plus the silence after it, never over the next line).
    1. It fits its own slot: slow it down a little to fill it if that stays in
       our "good" range, else keep it natural.
    2. It is longer than its slot but fits the silence after it: keep it
       natural and let it run on into that silence (no speeding up at all).
    3. Still too long: speed it up, but never beyond the "good" limit; if it is
       still too long the mixer has to cut its end (reported as a warning).
    Returns (tempo, at_limit)."""
    own = max(min(slot, room), 0.3)
    room = max(room, own)
    if actual <= own:
        req = actual / own
        return (1.0 if req < TEMPO_MIN else req), False
    if actual <= room:
        return 1.0, False
    req = actual / room
    if req > TEMPO_MAX:
        return TEMPO_MAX, True
    return req, False


def _fit_line(raw_path, out_wav, slot, room, loud_ref, start):
    """Fit one generated line into its time slot (see _pick_tempo) and match its
    loudness to the original speaker. Returns metadata for the mixer."""
    trimmed = out_wav.with_name(out_wav.stem + "_trim.wav")
    src = raw_path
    actual = _trim_silence(raw_path, trimmed)
    if actual is not None:
        src = trimmed
    else:
        actual = ffmpeg_utils.get_media_duration(raw_path)
    if actual <= 0:
        actual = max(min(slot, room), 0.3)
    tempo, warn = _pick_tempo(actual, slot, room)
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if abs(tempo - 1.0) > 0.02:
        cmd += ["-filter:a", f"atempo={tempo:.6f}"]
    cmd += ["-ac", "2", "-ar", str(SAMPLE_RATE), "-acodec", "pcm_s16le", str(out_wav)]
    ffmpeg_utils.run_ffmpeg(cmd)
    try:
        trimmed.unlink()
    except Exception:
        pass
    dur = ffmpeg_utils.get_media_duration(out_wav)
    gain = 0.0
    try:
        o_mean, _o_max = _speech_levels(loud_ref, start, max(min(slot, room), 0.3))
        d_mean, d_max = _speech_levels(out_wav)
        if o_mean is not None and d_mean is not None and o_mean > -60 and d_mean > -60:
            gain = max(-GAIN_MAX_DB, min(GAIN_MAX_DB, o_mean - d_mean))
            gain = min(gain, PEAK_CEIL_DB - d_max)      # a boost must not clip
    except Exception:
        gain = 0.0
    return {"dur": round(dur, 3), "tempo": round(tempo, 3), "warn": warn, "gain_db": round(gain, 1),
            "raw": round(actual, 3), "slot": round(slot, 2), "room": round(room, 2)}


def _mix_part(input_index, allowed, delay_ms, gdb, trim):
    vol = f"volume={10 ** (gdb / 20.0):.4f}," if abs(gdb) > 0.05 else ""
    if trim:
        fade_start = max(0, allowed - 0.06)
        return (f"[{input_index}]{vol}aformat=channel_layouts=stereo,atrim=0:{allowed:.3f},"
                f"afade=t=out:st={fade_start:.3f}:d=0.06,asetpts=PTS-STARTPTS,"
                f"adelay={delay_ms}|{delay_ms},apad[a{input_index - 1}]")
    return (f"[{input_index}]{vol}aformat=channel_layouts=stereo,atrim=0:{allowed:.3f},"
            f"asetpts=PTS-STARTPTS,adelay={delay_ms}|{delay_ms},apad[a{input_index - 1}]")


def _mix_chunk(items, s0, s1, fit_dir, out_wav):
    """Mix the lines of one stretch of the timeline into an exact-length wav
    (s0..s1 are sample positions)."""
    n = s1 - s0
    t0 = s0 / float(SAMPLE_RATE)
    inputs = ["-f", "lavfi", "-t", f"{n / float(SAMPLE_RATE) + 1.0:.3f}", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo"]
    parts = []
    for idx, it in enumerate(items):
        inputs += ["-i", str(fit_dir / f"{it['seg']}.wav")]
        delay_ms = max(0, int(round((it["start"] - t0) * 1000)))
        parts.append(_mix_part(idx + 1, max(it["allowed"], 0.05), delay_ms, it["gain_db"], it["trim"]))
    ins = "".join(f"[a{i}]" for i in range(len(items)))
    parts.append(f"[0]{ins}amix=inputs={len(items) + 1}:duration=first:normalize=0,"
                 f"atrim=end_sample={n},asetpts=PTS-STARTPTS[out]")
    ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y"] + inputs + ["-filter_complex", ";".join(parts), "-map", "[out]",
                                                         "-ac", "2", "-ar", str(SAMPLE_RATE), "-acodec", "pcm_s16le",
                                                         str(out_wav)])


def _plan_timeline(lines, total):
    """Decide, for every generated line, how much of it may play (it may run
    into the silence after it but never over the next line) and split the
    timeline into stretches that start at a line, so no line crosses a
    stretch boundary. Returns (kept_items, chunks[(items, s0, s1)], dropped)."""
    lines = sorted(lines, key=lambda x: x["start"])
    dropped = []
    for i, it in enumerate(lines):
        nxt = lines[i + 1]["start"] - 0.05 if i + 1 < len(lines) else total
        room = nxt - it["start"]
        it["allowed"] = min(it["dur"], room)
        it["trim"] = it["dur"] > it["allowed"] + 0.02
        if it["allowed"] <= 0.02:
            dropped.append(it)
    kept = [it for it in lines if it["allowed"] > 0.02]
    groups, cur = [], []
    for it in kept:
        if cur and it["start"] - cur[0]["start"] >= DUB_CHUNK_SPAN:
            groups.append(cur)
            cur = []
        cur.append(it)
    if cur:
        groups.append(cur)
    total_samples = int(math.ceil(total * SAMPLE_RATE))
    chunks = []
    for k, g in enumerate(groups):
        s0 = 0 if k == 0 else int(math.floor(g[0]["start"] * SAMPLE_RATE))
        s1 = total_samples if k == len(groups) - 1 else int(math.floor(groups[k + 1][0]["start"] * SAMPLE_RATE))
        chunks.append((g, s0, s1))
    return kept, chunks, dropped


REPHRASE_ENABLED = True
REPHRASE_TRIES = 2             # at most this many shorter versions are tried for one line
try:      # a line with fewer Arabic letters than this is never rewritten shorter (env LONGDUB_REPHRASE_MIN_LETTERS, 8 - 40)
    REPHRASE_MIN_LETTERS = min(40, max(8, int(float(os.environ.get("LONGDUB_REPHRASE_MIN_LETTERS") or 14))))
except Exception:
    REPHRASE_MIN_LETTERS = 14
# Very short lines keep their emotion and their full meaning: their audio is played at the tempo limit at most
# (a slightly cut end is better than a different sentence). Rewriting them halved their words (11 -> 5 letters).


def _ar_letters(text):
    """Arabic letters of a text (no spaces, no tashkeel marks) -- a good measure of how long it takes to say."""
    return len(re.sub(r"[\s\u064B-\u065F\u0670\u0640]", "", text or ""))


def _rephrase_to_fit(job, r, tag, meta, slot, room, voice_id, loud_ref, d):
    """A line that is still too long after using the silence after it and the
    "good" stretch would have its end cut. Instead, ask for a SHORTER version of
    the same sentence (same meaning), speak that, and use it if it is shorter.
    Up to REPHRASE_TRIES tries. Returns (meta, new_text) -- new_text is None when
    nothing better was found (the original line and meta stay)."""
    import gemini_service
    sid = r["segment_id"]
    cur_text = r["arabic_text"].strip()
    best_meta, best_text, best_out = meta, None, None
    for attempt in range(1, REPHRASE_TRIES + 1):
        letters = _ar_letters(cur_text)
        actual = float(best_meta.get("raw") or 0)
        if letters < REPHRASE_MIN_LETTERS or actual <= 0:
            break
        # speech time we are aiming for, so that at the "good" limit it fits the room with a little to spare
        target_raw = room * TEMPO_MAX * (0.92 if attempt == 1 else 0.82)
        ratio = target_raw / actual
        if ratio >= 0.97:
            break
        max_letters = max(3, int(letters * ratio))
        new = None
        for ask in range(2):        # the AI service sometimes answers with nothing: ask once more
            try:
                new = gemini_service.shorten_arabic_line(job["id"], r.get("text") or "", cur_text, max_letters, GEMINI_API_KEY)
            except Exception as ex:
                print(f"[longdub] rephrase call failed for {sid}: {ex}")
            if new:
                break
            time.sleep(2)
        if not new:
            _ev(job, "line_rephrase", "failed", f"{sid}: the AI service gave no usable answer (try {attempt})")
            break
        if _ar_letters(new) >= letters * 0.95:
            _ev(job, "line_rephrase", "failed", f"{sid}: the new text was not shorter (try {attempt})")
            break
        if needs_tashkeel(new):
            try:
                got = gemini_service.add_tashkeel_lines(job["id"], [{"segment_id": sid, "arabic_text": new}], GEMINI_API_KEY)
                if got:
                    new = merge_tashkeel(new, got.get(sid, ""))
            except Exception as ex:
                print(f"[longdub] tashkeel for a rephrased line failed: {ex}")
        audio, err = _tts_with_retry(voice_id, f"{tag}{new}")
        if audio is None:
            _ev(job, "line_rephrase", "failed", f"{sid}: the voice service did not speak the shorter version: {err}")
            break
        raw2 = d / "lines" / f"{sid}_rp{attempt}.mp3"
        raw2.write_bytes(audio)
        out2 = d / "fit" / f"{sid}_rp{attempt}.wav"
        m2 = _fit_line(raw2, out2, slot, room, loud_ref, float(r["start"]))
        try:
            raw2.unlink()
        except Exception:
            pass
        if m2["dur"] < best_meta["dur"] - 0.05:
            best_meta, best_text, best_out = m2, new, out2
            cur_text = new
            if m2["dur"] <= room + 0.02:
                break
        else:
            break
    if best_text is not None:
        os.replace(best_out, d / "fit" / f"{sid}.wav")
    for f in (d / "fit").glob(f"{sid}_rp*.wav"):
        try:
            f.unlink()
        except Exception:
            pass
    return best_meta, best_text


def _run_dubbing(job):
    """confirmed -> per-speaker temporary voice clones -> Arabic speech line by
    line -> fit to timing -> mix in stretches -> join with the background ->
    ONE final file. Checkpointed: a restart continues where it stopped (clones
    already made are reused, never re-made, never double-charged)."""
    wd = _wd(job)
    uid = job["uid"]
    try:
        resumed = job.get("status") == "dubbing"
        job["status"] = "dubbing"
        dub = job.setdefault("dub", {})
        dub.setdefault("voices", {})
        dub.setdefault("fallback", {})
        dub.setdefault("lines", {})
        dub.setdefault("failed", [])
        dub.setdefault("started", _now())
        if resumed:
            dub["resumed"] = True
        _save(job)
        _ev(job, "dubbing_started", "info", "resumed from checkpoint" if resumed else "first run")
        plan = job["dub_plan"]
        if not INWORLD_API_KEY:
            _fail(job, "The voice service is not available right now", "dub")
            return
        d = wd / "dub"
        for sub in ("", "lines", "fit", "mix"):
            (d / sub if sub else d).mkdir(parents=True, exist_ok=True)
        rows_all = read_segments(job)
        names = {sp["id"]: sp["name"] for sp in job.get("speaker_list", [])}
        rows = sorted([r for r in rows_all if (r.get("arabic_text") or "").strip()], key=lambda r: r["start"])
        total = float(job["analysis"]["audio_duration"])
        import inworld_service

        # 1. temporary voice per speaker ---------------------------------
        sp_ids = [s for s in plan["speakers"]]
        for i, spid in enumerate(sp_ids):
            if spid in dub["voices"] or spid in dub["fallback"]:
                continue
            _mark(job, "clone", 3 + int(7 * i / max(1, len(sp_ids))), f"Copying the voice of {names.get(spid, spid)}...")
            wav, secs = _clone_sample(job, spid, rows_all, d)
            vid, err = (None, f"not enough clear speech to copy this voice ({secs:.1f}s)")
            if wav is not None:
                vid, err = _clone_with_retry(f"lisan-tmp-{job['id'][:8]}-{spid}", wav)
                try:
                    wav.unlink()
                except Exception:
                    pass
            if vid:
                dub["voices"][spid] = vid
                job.setdefault("voices_pending_delete", []).append(vid)
                _save(job)
                _ev(job, "voice_cloned", "ok", f"{names.get(spid)}: reference {secs:.1f}s, voice labelled '{inworld_service.CLONE_SAMPLE_LANGUAGE}'")
            else:
                dub["fallback"][spid] = ""     # resolved below once the others are done
                _save(job)
                _ev(job, "voice_cloned", "failed", f"{names.get(spid)}: {err}")
        # Optional (INWORLD_LOCALIZE=1): give each copied voice a native Arabic
        # accent. All speakers are done at the same time; a voice that cannot be
        # localized simply keeps sounding as it is. Checkpointed, so a restart
        # never asks (and pays) twice.
        if inworld_service.LOCALIZE_ENABLED and dub["voices"]:
            done_loc = dub.setdefault("localized", {})
            todo = [s for s in sp_ids if s in dub["voices"] and s not in done_loc]
            if todo:
                _mark(job, "clone", 10, "Giving the voices a native Arabic accent...")
                t0 = time.time()
                results = inworld_service.localize_many({s: dub["voices"][s] for s in todo}, INWORLD_API_KEY,
                                                        inworld_service.gemini_chooser(job["id"]))
                for spid in todo:
                    res = results.get(spid) or {"ok": False, "error": "no answer"}
                    done_loc[spid] = "ok" if res.get("ok") else "failed"
                    _ev(job, "voice_localized", "ok" if res.get("ok") else "failed",
                        (f"{names.get(spid)}: candidate {res.get('picked', 0) + 1} of {res.get('candidates')} approved "
                         f"(listening scores {res.get('scores') or 'none'}) {res.get('note') or ''}, {time.time() - t0:.0f}s, answer {res.get('answer')}") if res.get("ok")
                        else f"{names.get(spid)}: {res.get('error')} (the plain copied voice is used)")
                _save(job)
        good = [s for s in sp_ids if s in dub["voices"]]
        if not good:
            _fail(job, "The voices could not be copied from this video", "dub")
            return
        # A speaker whose voice couldn't be copied borrows the voice of the
        # speaker with the most lines, and its clone charge is refunded.
        busiest = max(good, key=lambda s: sum(1 for r in rows if r.get("speaker_id") == s))
        for spid in [s for s in sp_ids if s in dub["fallback"] and not dub["fallback"][s]]:
            dub["fallback"][spid] = dub["voices"][busiest]
            refund = int(plan["clone_each"])
            if refund and job["paid"].get("dub", 0) >= refund:
                try:
                    Hooks.refund(uid, refund, job["id"])
                    job["paid"]["dub"] -= refund
                    _ev(job, "clone_refund", "ok", f"{names.get(spid)}: {refund} credits back", refund)
                except Exception as ex:
                    _ev(job, "clone_refund", "failed", str(ex))
            job.setdefault("warnings", []).append(
                f"The voice of {names.get(spid)} could not be copied, so {names.get(busiest)}'s voice was used for those lines.")
            _save(job)

        def voice_for(spid):
            return dub["voices"].get(spid) or dub["fallback"].get(spid) or dub["voices"][busiest]

        # 2. speak every line, fit it to its slot ----------------------------
        loud_ref = wd / "vocals_mono.wav"
        if not loud_ref.exists():
            loud_ref = wd / "audio.wav"
        n = len(rows)
        for i, r in enumerate(rows):
            sid = r["segment_id"]
            if sid in dub["lines"] or sid in dub["failed"]:
                continue
            _mark(job, "speak", 10 + int(62 * i / max(1, n)), f"Generating the Arabic voice (line {i + 1} of {n})...")
            tag = inworld_service.instruction_tag(r.get("emotion"))
            text = f"{tag}{r['arabic_text'].strip()}"
            audio, err = _tts_with_retry(voice_for(r["speaker_id"]), text)
            if audio is None:
                dub["failed"].append(sid)
                _ev(job, "line_generation", "failed", f"{sid} ({len(text)} chars): {err}")
                _save(job)
                continue
            raw = d / "lines" / f"{sid}.mp3"
            raw.write_bytes(audio)
            slot = max(float(r["end"]) - float(r["start"]), 0.5)
            nxt = float(rows[i + 1]["start"]) - 0.05 if i + 1 < n else total
            room = max(nxt - float(r["start"]), 0.05)
            meta = _fit_line(raw, d / "fit" / f"{sid}.wav", slot, room, loud_ref, float(r["start"]))
            new_text = None
            if REPHRASE_ENABLED and meta["dur"] > room + 0.02 and _ar_letters(r["arabic_text"]) >= REPHRASE_MIN_LETTERS:
                _mark(job, "speak", 10 + int(62 * i / max(1, n)), f"Shortening a line to fit (line {i + 1} of {n})...")
                meta, new_text = _rephrase_to_fit(job, r, tag, meta, slot, room, voice_for(r["speaker_id"]), loud_ref, d)
            meta.update({"start": float(r["start"]), "seg": sid, "chars": len(text) if new_text is None else len(f"{tag}{new_text}")})
            if new_text is not None:
                fits = meta["dur"] <= room + 0.02
                dub.setdefault("rephrased", {})[sid] = {"t": round(float(r["start"]), 1), "before": r["arabic_text"].strip(),
                                                        "after": new_text, "fits": fits}
                _ev(job, "line_rephrased", "ok" if fits else "partial",
                    f"{sid} at {r['start']:.1f}s: {_ar_letters(r['arabic_text'])} -> {_ar_letters(new_text)} letters, "
                    f"length {meta['dur']:.2f}s for {room:.2f}s of room")
            dub["lines"][sid] = meta
            try:
                raw.unlink()
            except Exception:
                pass
            if len(dub["lines"]) % 10 == 0:
                _save(job)
            time.sleep(0.15)
        _save(job)
        failed = list(dub["failed"])
        if n and len(failed) / float(n) > MAX_FAILED_LINE_SHARE:
            _ev(job, "speech_generation", "failed", f"{len(failed)} of {n} lines failed")
            _fail(job, "Too many lines could not be generated by the voice service", "dub")
            return
        if not dub["lines"]:
            _fail(job, "No line could be generated", "dub")
            return
        by_seg = {r["segment_id"]: r for r in rows}
        _ev(job, "speech_generation", "ok" if not failed else "partial",
            f"{len(dub['lines'])} of {n} lines generated, {len(failed)} failed")
        try:     # how tight every line was (server log): letters, natural speech time, slot, room, speed-up
            fm = []
            for r in rows:
                m_ = dub["lines"].get(r["segment_id"])
                if not m_:
                    continue
                txt_ = (dub.get("rephrased") or {}).get(r["segment_id"], {}).get("after") or r["arabic_text"]
                fm.append(f"{r['segment_id']}@{float(r['start']):.1f}:{_ar_letters(txt_)}L raw{m_.get('raw', 0):.1f}s "
                          f"slot{m_.get('slot', 0):.1f}s room{m_.get('room', 0):.1f}s x{m_.get('tempo', 1):.2f} "
                          f"[{str(r.get('emotion') or 'neutral').replace(' ', '')}]")
            print(f"[longdub] {job['id']} fit_map (tempo limit x{TEMPO_MAX:.2f}): " + " | ".join(fm[:60]))
        except Exception:
            pass
        reph = dub.get("rephrased") or {}
        if reph and not dub.get("rephrase_refund"):
            # The price was worked out on the longer text: give back the whole credits the shorter text saved.
            saved = sum(max(0, len(v["before"]) - len(v["after"])) for v in reph.values())
            rback = saved // max(1, int(plan["cpc"]))
            if rback and job["paid"].get("dub", 0) >= rback:
                try:
                    Hooks.refund(uid, rback, job["id"])
                    job["paid"]["dub"] -= rback
                    dub["rephrase_refund"] = rback
                    _ev(job, "rephrase_refund", "ok", f"{len(reph)} lines rephrased shorter, {saved} characters saved: {rback} credits back", rback)
                except Exception as ex:
                    _ev(job, "rephrase_refund", "failed", str(ex))
            _save(job)
        if failed:
            # Pro-rata refund for the characters of lines that were not generated.
            fchars = sum(len(inworld_service.instruction_tag(by_seg[s].get("emotion"))) + len(by_seg[s]["arabic_text"].strip())
                         for s in failed)
            back = int(math.ceil(fchars / float(max(1, plan["cpc"]))))
            if back and job["paid"].get("dub", 0) >= back and not dub.get("line_refund"):
                try:
                    Hooks.refund(uid, back, job["id"])
                    job["paid"]["dub"] -= back
                    dub["line_refund"] = back
                    _ev(job, "line_refund", "ok", f"{len(failed)} lines left silent: {back} credits back", back)
                except Exception as ex:
                    _ev(job, "line_refund", "failed", str(ex))
            job.setdefault("warnings", []).append(
                f"{len(failed)} line(s) could not be generated and were left silent; the price of those lines was refunded.")
            _save(job)

        # 3. timeline: fit into gaps, mix in stretches ------------------------
        _mark(job, "mix", 74, "Placing the lines on the timeline...")
        items = [dict(m) for m in dub["lines"].values()]
        for it in items:
            it["end"] = float(by_seg[it["seg"]]["end"])
        kept, chunks, dropped = _plan_timeline(items, total)
        if dropped:
            _ev(job, "timeline", "partial", f"{len(dropped)} lines had no room and were left out")
            job.setdefault("warnings", []).append(f"{len(dropped)} line(s) had no room before the next line and were left out.")
        if not chunks:
            _fail(job, "No generated line fits the timeline", "dub")
            return
        mix_dir = d / "mix"
        mixed = []
        clean_slot = _Slot(job)
        clean_stats = {"cleaned": 0, "kept": 0, "reasons": [], "loss": [], "floor": []}
        for k, (g, s0, s1) in enumerate(chunks):
            out = mix_dir / f"c{k:03d}.wav"
            _mark(job, "mix", 75 + int(10 * k / len(chunks)), f"Mixing (part {k + 1} of {len(chunks)})...")
            if not out.exists():
                raw_mix = mix_dir / f"c{k:03d}.raw.wav"
                _mix_chunk(g, s0, s1, d / "fit", raw_mix)
                # Isolate the voice from any noise before it is joined to the background.
                _mark(job, "mix", 75 + int(10 * k / len(chunks)), f"Cleaning the voice (part {k + 1} of {len(chunks)})...")
                clean_slot.take()
                try:
                    res = voice_clean.clean_voice(raw_mix, out, mix_dir, SAMPLE_RATE)
                finally:
                    clean_slot.drop()
                try:
                    raw_mix.unlink()
                except Exception:
                    pass
                if res.get("cleaned"):
                    clean_stats["cleaned"] += 1
                    if res.get("loss_db") is not None:
                        clean_stats["loss"].append(res["loss_db"])
                    if res.get("floor_db") is not None:
                        clean_stats["floor"].append(res["floor_db"])
                else:
                    clean_stats["kept"] += 1
                    if res.get("reason") and res["reason"] not in clean_stats["reasons"]:
                        clean_stats["reasons"].append(res["reason"])
            mixed.append(out)
            time.sleep(0.05)
        if voice_clean.ENABLED and (clean_stats["cleaned"] or clean_stats["kept"]):
            _ev(job, "voice_clean", "ok" if not clean_stats["kept"] else "partial",
                f"{clean_stats['cleaned']} of {len(chunks)} parts cleaned"
                + (f"; noise floor before cleaning {min(clean_stats['floor']):.0f} to {max(clean_stats['floor']):.0f} dB" if clean_stats["floor"] else "")
                + (f"; {clean_stats['kept']} kept as they were: {'; '.join(clean_stats['reasons'])[:400]}" if clean_stats["kept"] else ""))
        dub_full = d / "dub_full.wav"
        _concat_wavs(mixed, dub_full)
        got = ffmpeg_utils.get_media_duration(dub_full)
        if abs(got - total) > 0.25:
            raise Exception(f"dubbed track length {got:.2f}s does not match the video ({total:.2f}s)")
        _ev(job, "mix", "ok", f"{len(kept)} lines in {len(chunks)} parts; {sum(1 for x in kept if x['trim'])} trimmed, "
                              f"{sum(1 for x in kept if x['warn'])} at the speed limit")
        # A plain report of the lines the user may want to look at next time.
        cut = sorted(round(x["start"], 1) for x in kept if x["trim"])
        fast = sorted(round(x["start"], 1) for x in kept if not x["trim"] and float(x.get("tempo") or 1.0) >= 1.2)
        dub["timing"] = {"n_cut": len(cut), "cut": cut[:30], "n_fast": len(fast), "fast": fast[:30]}
        if cut or fast:
            _ev(job, "timing_report", "partial",
                f"cut short at {cut[:12]}; sped up to 1.2x or more at {fast[:12]}")

        # 3b. optional lip-sync ---------------------------------------------------
        lip_video = None
        lip_summary = None
        lip_secs = 0.0
        if (job.get("lipsync") or {}).get("wanted") and job.get("has_video") and (job.get("dub_plan") or {}).get("lipsync"):
            _mark(job, "lipsync", 86, "Lip-syncing the picture...")
            _save(job)
            lip_t0 = time.time()
            try:
                lip_video = _run_lipsync(job, dub_full, kept, total, d)
            except Exception as ex:
                import traceback
                print(f"[longdub] lip-sync failed for {job['id']}: {ex}\n{traceback.format_exc()}")
                _ev(job, "lipsync", "failed", f"{type(ex).__name__}: {ex}"[:600])
                lip_video = None
                _refund_all_lipsync(job, str(ex)[:120])
            lip_secs = time.time() - lip_t0
            st_lip = job["dub"].get("lip", {})
            lip_summary = {"clips": len((job["dub_plan"]["lipsync"]).get("clips") or []),
                           "synced": sum(1 for v in st_lip.values() if v.get("state") == "done") if lip_video else 0,
                           "refunded": sum(int(v.get("refunded") or 0) for v in st_lip.values())}

        # 4. one final file ---------------------------------------------------
        _mark(job, "finish", 88 if lip_video is None else 96, "Building your final file...")
        video_out = bool(job.get("has_video"))
        final_name = f"{job['id']}_final_dubbed_video.mp4" if video_out else f"{job['id']}_final_dubbed.mp3"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        tmp_final = OUTPUT_DIR / f"{job['id']}_finalizing{Path(final_name).suffix}"
        src = wd / f"src{job['ext']}"
        vsrc = lip_video if lip_video is not None else src      # picture: lip-synced or original
        bg = wd / "background.wav"
        bg_info = None
        if video_out:
            an_ = job.get("analysis") or {}
            failed_parts = len(an_.get("bg_failed") or [])
            parts_total = len(an_.get("pieces") or [])
            if not bg.exists():
                bg_info = {"state": "missing", "failed_parts": failed_parts, "parts": parts_total}
            else:
                b_mean, b_max = _volume_stats(bg)
                if b_max is None or b_max < -60:
                    bg_info = {"state": "silent", "failed_parts": failed_parts, "parts": parts_total}
                elif failed_parts:
                    bg_info = {"state": "partial", "failed_parts": failed_parts, "parts": parts_total}
                elif b_mean is not None and b_mean < FAINT_BG_MEAN_DB:
                    bg_info = {"state": "faint", "failed_parts": 0, "parts": parts_total}
                else:
                    bg_info = {"state": "mixed", "failed_parts": 0, "parts": parts_total}
                if b_max is not None:
                    bg_info["mean_db"], bg_info["max_db"] = round(b_mean, 1), round(b_max, 1)
            if bg.exists():
                _bg_levels_event(job, wd, bg)
            _ev(job, "background_mix", "ok" if bg_info["state"] == "mixed" else "failed",
                f"{bg_info['state']}; background track mean {bg_info.get('mean_db')} dB, peak {bg_info.get('max_db')} dB; "
                f"{bg_info['failed_parts']} of {bg_info['parts']} parts had no separated background")
        bg_use = bg
        bed_done = False
        if video_out and bg.exists() and bg_info and bg_info["state"] in ("mixed", "faint"):
            try:
                pauses_ = []
                pp_ = wd / "pauses.json"
                if pp_.exists():
                    pauses_ = [tuple(x) for x in json.loads(pp_.read_text(encoding="utf-8"))]
                # First choice: a steady background sound that the separator filed under "voices" (a crowd, a
                # machine hum, traffic, rain ...) is rebuilt from the pauses between the speakers and laid under
                # the whole video. Second choice (below): the separated background is simply raised in level.
                plan_, why_ = bg_duck.plan_ambience_bed(wd / "audio.wav", bg, wd / "vocals_mono.wav", pauses_)
                if plan_:
                    sep_used = bg
                    if bg_duck.ENABLED:
                        _bdk = bg_duck.duck_background(bg, wd / "vocals_mono.wav", wd / "background_ducked.wav")
                        if _bdk["ducked"]:
                            sep_used = wd / "background_ducked.wav"
                        _ev(job, "background_duck", "ok" if _bdk["ducked"] else "info", _bdk["reason"])
                    import zlib
                    binfo_ = bg_duck.add_ambience_bed(plan_, sep_used, wd / "background_restored.wav",
                                                      seed=zlib.crc32(job["id"].encode("utf-8")))
                    if binfo_["ok"]:
                        bg_use = wd / "background_restored.wav"
                        # the final mix costs a few dB (volume, bass filter): give them back so that the
                        # pauses end up as loud as in the original
                        g2_, _n2 = bg_duck.makeup_gain(wd / "audio.wav", bg_use, pauses_, max_db=6.0,
                                                       mix_filter=BG_MIX_FILTER, min_db=1.0)
                        if g2_ > 0 and bg_duck.lift_background(bg_use, wd / "background_restored_lifted.wav", g2_):
                            bg_use = wd / "background_restored_lifted.wav"
                        l_mean, l_max = _volume_stats(bg_use)
                        if bg_info["state"] == "faint" and l_mean is not None and l_mean >= FAINT_BG_MEAN_DB:
                            bg_info["state"] = "mixed"
                        bed_done = True
                        _ev(job, "background_bed", "ok", f"{binfo_['reason']}; level correction {g2_:g} dB; "
                                                          f"the track averages {l_mean} dB, peak {l_max} dB")
                    else:
                        _ev(job, "background_bed", "info", "could not be built (" + binfo_["reason"] + "); the level is raised instead")
                else:
                    _ev(job, "background_bed", "info", "not used: " + str(why_))
                if not bed_done:
                    # The separator often keeps far less of the room sound than the original had. Raise the
                    # background until, in the pauses of the voices, it is as loud as the original there.
                    gain_, note_ = bg_duck.makeup_gain(wd / "audio.wav", bg, pauses_, mix_filter=BG_MIX_FILTER)
                    if gain_ > 0 and bg_duck.lift_background(bg, wd / "background_lifted.wav", gain_):
                        bg_use = wd / "background_lifted.wav"
                        l_mean, l_max = _volume_stats(bg_use)
                        if bg_info["state"] == "faint" and l_mean is not None and l_mean >= FAINT_BG_MEAN_DB:
                            bg_info["state"] = "mixed"       # no longer faint once raised
                        _ev(job, "background_lift", "ok", note_ + f"; the raised track averages {l_mean} dB, peak {l_max} dB")
                    else:
                        _ev(job, "background_lift", "info", note_ if gain_ <= 0 else "could not raise the background, it is used as it is")
            except Exception as ex:
                print(f"[longdub] background restore skipped: {ex}")
        bg_mix = bg_use
        if video_out and bg.exists() and bg_duck.ENABLED and not bed_done:
            # the separated background keeps a faint metallic copy of the original voices:
            # lower its voice range only while the original speakers talk
            _bdk = bg_duck.duck_background(bg_use, wd / "vocals_mono.wav", wd / "background_ducked.wav")
            if _bdk["ducked"]:
                bg_mix = wd / "background_ducked.wav"
            _ev(job, "background_duck", "ok" if _bdk["ducked"] else "info", _bdk["reason"])
        if video_out and bg.exists():
            _bg_final_event(job, wd, bg_mix, dub_full)
            fc = ("[1:a]volume=1.0[d];"
                  f"[2:a]{BG_MIX_FILTER}[b];"
                  "[d][b]amix=inputs=2:duration=first:normalize=0[out]")
            ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-i", str(vsrc), "-i", str(dub_full), "-i", str(bg_mix),
                                     "-filter_complex", fc, "-map", "0:v:0", "-map", "[out]",
                                     "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(tmp_final)])
        elif video_out:
            ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-i", str(vsrc), "-i", str(dub_full), "-map", "0:v:0", "-map", "1:a:0",
                                     "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(tmp_final)])
        else:
            ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-i", str(dub_full), "-codec:a", "libmp3lame", "-q:a", "2", str(tmp_final)])
        if not tmp_final.exists() or tmp_final.stat().st_size < 1000:
            raise Exception("the final file was not produced")
        os.replace(tmp_final, OUTPUT_DIR / final_name)
        size = (OUTPUT_DIR / final_name).stat().st_size
        _ev(job, "output_saved", "ok", f"{final_name} {size // 1024} KB")

        # 5. voices gone, working files gone, tell the user ---------------------
        _delete_pending_voices(job)
        elapsed = _now() - dub.get("started", _now())
        job["result"] = {"kind": "video" if video_out else "audio", "file": final_name, "size": size,
                         "duration": round(total, 1), "lines": len(dub["lines"]), "failed_lines": len(failed),
                         "finished": _now()}
        if lip_summary is not None:
            job["result"]["lipsync"] = lip_summary
        if bg_info and bg_info["state"] != "mixed":
            job["result"]["background"] = {"state": bg_info["state"], "failed_parts": bg_info["failed_parts"], "parts": bg_info["parts"]}
        if dub.get("timing") and (dub["timing"]["n_cut"] or dub["timing"]["n_fast"]):
            job["result"]["timing"] = dub["timing"]
        if dub.get("rephrased"):
            job["result"]["rephrased"] = sorted(dub["rephrased"].values(), key=lambda v: v["t"])[:40]
            job["result"]["n_rephrased"] = len(dub["rephrased"])
            if dub.get("rephrase_refund"):
                job["result"]["rephrase_refund"] = dub["rephrase_refund"]
        job["status"] = "done"
        _mark(job, "done", 100, "Your dubbed file is ready.")
        _save(job)
        for sub in ("dub",):
            shutil.rmtree(wd / sub, ignore_errors=True)
        for f in ("audio.wav", "background.wav", "background_lifted.wav", "background_restored.wav", "background_restored_lifted.wav", "background_ducked.wav", "vocals_mono.wav", "pauses.json", f"src{job['ext']}", "turns.json"):
            try:
                (wd / f).unlink()
            except Exception:
                pass
        _ev(job, "dubbing_done", "ok", f"{elapsed:.0f}s" + (" (resumed run)" if dub.get("resumed") else ""))
        if not dub.get("resumed"):
            _record_speed("dubbing", total, max(elapsed - lip_secs - DUB_BASE_SEC, 0.1 * total))
        note = ""
        if failed or dropped:
            note = ("\n\nNote: some lines could not be dubbed and were left silent; the price of the lines that could not be "
                    "generated was refunded.")
        if dub.get("rephrased"):
            note += (f"\n\n{len(dub['rephrased'])} Arabic line(s) were too long for their time even after stretching, so they were "
                     "rephrased shorter with the same meaning. You can see what changed on the download page."
                     + (f" The credits saved on the shorter text ({dub['rephrase_refund']}) were refunded." if dub.get("rephrase_refund") else ""))
        if bg_info and bg_info["state"] != "mixed":
            note += ("\n\nNote: the original background sound (music, ambience) could not be separated "
                     + ("for the whole video" if bg_info["state"] in ("missing", "silent") else
                        f"for {bg_info['failed_parts']} of {bg_info['parts']} parts of the video")
                     + ", so it is missing from the dubbed file there.")
        if lip_summary is not None:
            note += (f"\n\nLip-sync: {lip_summary['synced']} of {lip_summary['clips']} clips were lip-synced."
                     + (f" The price of the clips that could not be lip-synced ({lip_summary['refunded']} credits) was refunded."
                        if lip_summary["refunded"] else ""))
        ok = Hooks.send_email(uid, "Your dubbed video is ready",
                              f"Hi,\n\nYour dubbed {'video' if video_out else 'audio'} \"{job['filename']}\" is ready.\n"
                              "Download it from https://lisanai.org/dub-long (or your Account page).\n"
                              "As agreed, the copied voices were deleted and you get this one file. "
                              f"It stays available for your plan's storage period.{note}\n\n-- Lisan AI")
        _ev(job, "email_sent", "ok" if ok else "failed", "finished email" + ("" if ok else f": {Hooks.email_error() or 'unknown reason'}"))
    except Exception as ex:
        import traceback
        print(f"[longdub] dubbing failed for {job['id']}: {ex}\n{traceback.format_exc()}")
        _ev(job, "dubbing_failed", "failed", f"{type(ex).__name__}: {ex}"[:600])
        _fail(job, "Something went wrong while dubbing this video", "dub")


# ------------------------------------------------------ housekeeping

# Unfinished jobs nobody came back to are removed so they never pile up on
# the disk. (Finished results are handled by the app's normal retention.)
STALE_HOURS = {"uploading": 24, "estimated": 24, "failed": 24, "cancelled": 24, "editing": 24 * 7,
               "done": 24 * 35}


def sweep_stale():
    now = _now()
    removed = 0
    if not LONG_DIR.exists():
        return 0
    for d in list(LONG_DIR.iterdir()):
        if not d.is_dir():
            continue
        job = load_job(d.name)
        if not job:
            # A folder with no readable state: only remove when old.
            try:
                if now - d.stat().st_mtime > 24 * 3600:
                    shutil.rmtree(d, ignore_errors=True)
                    removed += 1
            except Exception:
                pass
            continue
        hours = STALE_HOURS.get(job.get("status"))
        if hours and job["id"] not in _RUNNING and now - job.get("updated", now) > hours * 3600:
            _ev(job, "removed_by_housekeeping", "ok", f"status={job.get('status')} idle for over {hours} hours")
            _delete_pending_voices(job)
            with _LOCK:
                _JOBS.pop(job["id"], None)
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


def start_housekeeping():
    def _loop():
        while True:
            time.sleep(3600)
            try:
                sweep_stale()
                sweep_voices()
            except Exception as ex:
                print(f"[longdub] sweep error: {ex}")
    threading.Thread(target=_loop, daemon=True).start()
