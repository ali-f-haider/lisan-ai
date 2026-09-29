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
from pathlib import Path
from types import SimpleNamespace

from config import DATA_DIR, GEMINI_API_KEY, HF_TOKEN
import ffmpeg_utils

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

_LOCK = threading.RLock()
_JOBS = {}          # job_id -> job dict (memory copy of job.json)
_JOB_LOCKS = {}     # job_id -> Lock (serialises chunk writes / saves per job)
_RUNNING = set()    # job_ids that currently have a worker thread
_worker_slots = threading.Semaphore(MAX_CONCURRENT_WORKERS)


class Hooks:
    """Set once by main.py -- see configure(). Defaults do nothing so the
    module can also be imported by tests."""
    get_credits = staticmethod(lambda uid: None)
    charge = staticmethod(lambda uid, amount, action, job_id, seconds=None: True)
    refund = staticmethod(lambda uid, amount, job_id: True)
    send_email = staticmethod(lambda uid, subject, text: False)
    pricing = staticmethod(lambda: {})


def configure(**kwargs):
    for k, v in kwargs.items():
        setattr(Hooks, k, staticmethod(v))


# ------------------------------------------------------------------ helpers

def _now():
    return time.time()


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
                 "updated", "received_count", "total_chunks", "warnings", "price", "result")


def public_view(job):
    v = {k: job.get(k) for k in PUBLIC_FIELDS if k in job}
    v["received_count"] = len(job.get("received", []))
    v["total_chunks"] = job.get("total_chunks", 0)
    return v


# ---------------------------------------------------------------- estimate

def compute_estimate(duration_sec, cfg):
    """Cost of the whole job, shown to the user before anything expensive
    runs. cfg = Hooks.pricing():
      fee               -- small fixed charge for producing this estimate
      analysis_per_min  -- transcribe + speaker detection + translate, per minute
      chars_per_credit  -- text-to-speech rate
      clone_credits     -- per cloned speaker voice
      merge_credits     -- final assembly
    The fee is NOT extra: it counts toward the total."""
    minutes = max(0.0, float(duration_sec)) / 60.0
    fee = int(cfg.get("fee", 3))
    analysis = int(math.ceil(minutes * float(cfg.get("analysis_per_min", 2))))
    cpc = max(1, int(cfg.get("chars_per_credit", 60)))
    voice = int(math.ceil(minutes * EST_CHARS_PER_MIN / cpc))
    clones = int(cfg.get("clone_credits", 5)) * EST_SPEAKERS
    merge = max(1, int(cfg.get("merge_credits", 1)))
    total = fee + analysis + voice + clones + merge
    return {
        "minutes": round(minutes, 2), "fee": fee, "analysis": analysis, "voice": voice,
        "clones": clones, "merge": merge, "total": total,
        "assumed_speakers": EST_SPEAKERS,
    }


# ------------------------------------------------------------------ upload

def active_count(uid):
    n = 0
    for j in list_jobs_for_uid(uid):
        if j.get("status") not in ("done", "failed", "cancelled", "expired"):
            n += 1
    return n


def init_upload(uid, filename, size):
    """Creates the job record + empty part file. Returns (job, None) or
    (None, (error_message, http_status))."""
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        return None, ("Unsupported file type. Use MP4, MOV, MKV, WEBM, AVI, MP3, WAV, M4A, AAC, FLAC or OGG.", 400)
    try:
        size = int(size)
    except Exception:
        return None, ("Missing file size.", 400)
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
        "n_segments": 0, "speakers": [], "warnings": [],
    }
    with _LOCK:
        _JOBS[job_id] = job
    _save(job)
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
        return False, ("The uploaded file is damaged or incomplete. Please upload it again.", 400)
    src = d / f"src{job['ext']}"
    os.replace(part, src)
    cfg = Hooks.pricing()
    try:
        info = _ffprobe_json(src, "format=duration:stream=codec_type")
        streams = [s.get("codec_type") for s in info.get("streams", [])]
        duration = float(info.get("format", {}).get("duration"))
    except Exception:
        _discard(job)
        return False, ("This file couldn't be read as audio/video. Please try another file.", 400)
    if "audio" not in streams:
        _discard(job)
        return False, ("This file has no audio track to dub.", 400)
    max_min = float(cfg.get("max_min", DEFAULT_MAX_MIN))
    if duration < MIN_SEC:
        _discard(job)
        return False, (f"This clip is only {duration:.0f} seconds. For clips under {MIN_SEC:.0f} seconds use the normal dubbing page.", 400)
    if duration > max_min * 60 + 1:
        _discard(job)
        return False, (f"This video is {duration / 60:.1f} minutes long. The limit is {max_min:g} minutes.", 413)
    fee = int(cfg.get("fee", 3))
    bal = Hooks.get_credits(uid)
    if bal is not None and bal < fee:
        # Keep the upload so the user can top up and retry finishing.
        os.replace(src, part)
        return False, (f"Getting an estimate costs {fee} credits and you have {bal}. Use Buy to top up, then try again.", 402)
    with _lock_for(job["id"]):
        job["duration"] = round(duration, 3)
        job["has_video"] = "video" in streams and job["ext"] in VIDEO_EXTS
        job["estimate"] = compute_estimate(duration, cfg)
        if fee > 0 and not job["paid"]["fee"]:
            Hooks.charge(uid, fee, "long_dub_estimate", job["id"])
            job["paid"]["fee"] = fee
        job["status"] = "estimated"
        job["stage"] = "estimate"
        job["percent"] = 100
        job["message"] = "Estimate ready."
    _save(job)
    return True, None


def _discard(job):
    """Remove a job that never got past validation (nothing was charged)."""
    with _LOCK:
        _JOBS.pop(job["id"], None)
    shutil.rmtree(job_dir(job["id"]), ignore_errors=True)


def accept(job, uid):
    """User agreed to the estimate: check balance for the analysis part, charge
    it, and start the background worker. Returns (True, None) or
    (False, (message, http_status))."""
    if job.get("status") != "estimated":
        return False, ("This job is not waiting for approval.", 409)
    est = job["estimate"]
    cfg = Hooks.pricing()
    need_now = est["analysis"]
    bal = Hooks.get_credits(uid)
    # He must be able to cover the whole estimate (minus what he already paid)
    # to proceed -- the exact voice cost is confirmed later after editing.
    remaining_total = est["total"] - job["paid"]["fee"]
    if bal is not None and bal < remaining_total:
        return False, (f"Not enough credits. The full estimate is {est['total']} credits (you already paid {job['paid']['fee']}); "
                       f"you have {bal}. Use Buy to top up.", 402)
    with _lock_for(job["id"]):
        if need_now > 0 and not job["paid"]["analysis"]:
            Hooks.charge(uid, need_now, "long_dub_analysis", job["id"])
            job["paid"]["analysis"] = need_now
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
        try:
            job = load_job(job_id)
            if job["status"] in ("accepted", "analyzing"):
                _run_analysis(job)
            elif job["status"] in ("confirmed", "dubbing"):
                runner = globals().get("_run_dubbing")
                if runner:
                    runner(job)
        finally:
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


def rows_from_raw(raw_segments, turns, speaker_label_map):
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
    return ws.merge_mid_sentence_rows(result)


def _run_analysis(job):
    """extract audio -> cut pieces -> separate vocals/background per piece ->
    speaker detection (whole file, one pass) -> transcribe per piece ->
    translate in small batches -> ready to edit. Every step checkpoints, so a
    restart continues instead of starting over."""
    wd = _wd(job)
    src = wd / f"src{job['ext']}"
    try:
        job["status"] = "analyzing"
        an = job.setdefault("analysis", {})
        an.setdefault("sep_done", [])
        an.setdefault("asr_done", [])
        _save(job)

        # 1. audio track -------------------------------------------------
        audio = wd / "audio.wav"
        if not audio.exists():
            _mark(job, "extract", 2, "Extracting the audio...")
            ffmpeg_utils.run_ffmpeg(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "2", "-ar", str(SAMPLE_RATE),
                                     "-acodec", "pcm_s16le", str(audio)])
        # 2. pieces --------------------------------------------------------
        if not an.get("pieces"):
            _mark(job, "plan", 4, "Finding natural pauses to split at...")
            sil = detect_silences(audio)
            dur = ffmpeg_utils.get_media_duration(audio)
            an["pieces"] = [list(p) for p in plan_pieces(dur, sil)]
            an["audio_duration"] = dur
            _save(job)
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
                    job.setdefault("warnings", []).append(
                        f"Background music could not be separated for part {i + 1}; that part will have no background sound.")
            else:
                shutil.copy(piece, vocals_dst)
            try:
                piece.unlink()
            except Exception:
                pass
            an["sep_done"].append(i)
            _save(job)
            time.sleep(0.05)

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
        rows = rows_from_raw(raw_all, turns, label_map)
        if not rows:
            _fail(job, "No speech was found in this video", "analysis")
            return
        if len(rows) > MAX_SEGMENTS:
            _fail(job, f"This video has too many separate lines ({len(rows)}); the limit is {MAX_SEGMENTS}", "analysis")
            return
        _write_segments(job, rows)

        # 7. translate in small batches ------------------------------------
        _translate_all(job, rows)
        _write_segments(job, rows)

        # 8. done: free the big intermediates, keep what dubbing needs ------
        for sub in ("pieces", "vocals", "bg", "asr", "sep"):
            shutil.rmtree(wd / sub, ignore_errors=True)
        for f in ("vocals.wav", "vocals_normalized.wav"):
            try:
                (wd / f).unlink()
            except Exception:
                pass
        job["speakers"] = sorted({r["speaker"] for r in rows})
        job["n_segments"] = len(rows)
        job["status"] = "editing"
        _mark(job, "review", 100, "Ready for you to review.")
        _save(job)
        try:
            Hooks.send_email(job["uid"], "Your Lisan AI transcript is ready to review",
                             f"Hi,\n\nThe transcript and Arabic translation of \"{job['filename']}\" are ready. "
                             "Please review and edit them, then confirm to start the dubbing:\n"
                             "https://lisanai.org/dub-long\n\n-- Lisan AI")
        except Exception:
            pass
    except Exception as ex:
        import traceback
        print(f"[longdub] analysis failed for {job['id']}: {ex}\n{traceback.format_exc()}")
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


def update_segments(job, edits):
    """edits = [{segment_id, text?, arabic_text?}]. Only text fields are
    editable; times, speakers and ids can never be changed from the page."""
    if job.get("status") != "editing":
        return False, "This job is not open for editing."
    rows = read_segments(job)
    by_id = {r["segment_id"]: r for r in rows}
    changed = 0
    for e in edits or []:
        r = by_id.get(str(e.get("segment_id", "")))
        if not r:
            continue
        if isinstance(e.get("text"), str):
            r["text"] = e["text"][:MAX_TEXT_LEN]
            changed += 1
        if isinstance(e.get("arabic_text"), str):
            r["arabic_text"] = e["arabic_text"][:MAX_TEXT_LEN]
            changed += 1
    if changed:
        _write_segments(job, rows)
    return True, changed


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
    r["arabic_text"], r["emotion"] = got[segment_id]
    _write_segments(job, rows)
    return True, "", r["arabic_text"]


# ------------------------------------------------------ housekeeping

# Unfinished jobs nobody came back to are removed so they never pile up on
# the disk. (Finished results are handled by the app's normal retention.)
STALE_HOURS = {"uploading": 24, "estimated": 24, "failed": 24, "cancelled": 24, "editing": 24 * 7}


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
            except Exception as ex:
                print(f"[longdub] sweep error: {ex}")
    threading.Thread(target=_loop, daemon=True).start()
