"""Disk-space guard for the Railway volume (DATA_DIR).

The volume is small (5 GB on the Hobby plan) and every user's long dub writes
1-3 GB of temporary files while it runs. Per-user storage quotas limit what
ONE user keeps, but nothing limited the volume as a whole -- a few people
working at once could fill it, and a full disk breaks everything (uploads cut
off halfway, dubs crash after the user was charged, project files that can't
be saved).

What this module does
  * check(incoming_bytes, working_gb)  -- asked before a new upload is
    accepted: is there room for the file, its working files, and a safety
    reserve? If not it first tries a safe emergency clean-up of leftovers,
    and only then says no (the caller shows a friendly "try again shortly"
    message; nothing is charged and nothing is saved).
  * emergency_cleanup()                -- deletes only leftovers: old temporary
    upload/working files of jobs nobody is running, stale separation
    folders, and long-dub projects idle for hours (which are "saved" -- only
    the video is removed, the text stays and the user re-attaches the file).
    Finished dubs the user paid for are never touched.
  * a background monitor               -- every few minutes: records usage for
    the admin panel, cleans up when the volume is getting full, and e-mails
    Ali (once per level, re-reminding every 12 h) when it stays high. The
    e-mail has its own admin on/off switch (diskAlertsEnabled), exactly like
    the ElevenLabs and Railway alerts.

Everything here is best-effort: a failure is logged and swallowed, never
raised into a request.
"""
import json
import os
import re
import shutil
import threading
import time
import urllib.request

from config import DATA_DIR, UPLOAD_DIR, OUTPUT_DIR, RESEND_API_KEY, CONTACT_TO_EMAIL

GB = 1024 ** 3


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


# Always keep at least this much free AFTER accepting a new file.
RESERVE_GB = _env_float("DISK_GUARD_RESERVE_GB", 0.8)
# Leftovers older than this are fair game when the disk is tight (the normal
# sweep in main.py uses 6 hours).
STALE_HOURS = _env_float("DISK_GUARD_STALE_HOURS", 3)
# Long-dub projects idle this long are parked (video removed, text kept) when
# the disk is tight (normal rule: 24 hours).
PARK_IDLE_HOURS = _env_float("DISK_GUARD_PARK_HOURS", 6)
WARN_PERCENT = _env_float("DISK_GUARD_WARN_PERCENT", 80)
CRIT_PERCENT = _env_float("DISK_GUARD_CRIT_PERCENT", 90)
CHECK_INTERVAL_MIN = _env_float("DISK_GUARD_INTERVAL_MIN", 10)
RENOTIFY_HOURS = 12

# working files a new job needs on top of its own upload (rough, generous)
WORK_SHORT_GB = 0.5
WORK_LONG_GB = 1.5

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_lock = threading.Lock()
_latest = {}
_last_cleanup = {"ts": 0.0, "freed": 0}
_alert_level = 0            # 0 none, 1 warn, 2 critical
_last_alert_sent = 0.0
_last_refusal = {"ts": 0.0, "count": 0}
_cleanup_lock = threading.Lock()


# ---------------------------------------------------------------- numbers
def usage():
    """(total, used, free) bytes of the volume DATA_DIR lives on."""
    total, used, free = shutil.disk_usage(str(DATA_DIR))
    return total, used, free


def snapshot():
    try:
        total, used, free = usage()
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
    pct = used * 100.0 / total if total else 0.0
    return {
        "ok": True, "total_gb": round(total / GB, 2), "used_gb": round(used / GB, 2),
        "free_gb": round(free / GB, 2), "percent_used": round(pct, 1),
        "reserve_gb": RESERVE_GB, "warn_percent": WARN_PERCENT, "crit_percent": CRIT_PERCENT,
    }


def get_cached():
    with _lock:
        out = dict(_latest)
        out["last_cleanup_freed_mb"] = round(_last_cleanup["freed"] / 1048576, 1)
        out["last_cleanup_ts"] = _last_cleanup["ts"]
        out["refusals"] = _last_refusal["count"]
        out["last_refusal_ts"] = _last_refusal["ts"]
        return out


# ---------------------------------------------------------------- clean-up
def _tree_newest_mtime(p):
    newest = 0.0
    try:
        newest = p.stat().st_mtime
        if p.is_dir():
            for root, dirs, files in os.walk(p):
                for n in files:
                    try:
                        newest = max(newest, os.stat(os.path.join(root, n)).st_mtime)
                    except OSError:
                        pass
    except OSError:
        pass
    return newest


def _tree_size(p):
    try:
        if p.is_file():
            return p.stat().st_size
        total = 0
        for root, dirs, files in os.walk(p):
            for n in files:
                try:
                    total += os.stat(os.path.join(root, n)).st_size
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def remove_stale_dirs(dirs, cutoff, is_final_output=None):
    """Remove sub-FOLDERS (e.g. "<job id>_separated", the Demucs output) whose
    newest file is older than cutoff (epoch seconds). The regular sweep in
    main.py only ever removed plain files, so these folders -- tens to
    hundreds of MB each -- piled up forever. Returns bytes freed."""
    freed = 0
    for d in dirs:
        try:
            entries = list(d.glob("*"))
        except Exception:
            continue
        for p in entries:
            try:
                if not p.is_dir():
                    continue
                if _tree_newest_mtime(p) < cutoff:
                    size = _tree_size(p)
                    shutil.rmtree(p, ignore_errors=True)
                    freed += size
            except Exception:
                pass
    return freed


def _busy_ids():
    """Job ids that are being worked on right now (never deleted from)."""
    busy = set()
    try:
        import app_state
        for key, val in list(app_state.jobs_progress.items()):
            if isinstance(val, dict) and val.get("status") == "processing":
                m = _UUID_RE.search(str(key))
                if m:
                    busy.add(m.group(0))
    except Exception:
        pass
    return busy


def emergency_cleanup(is_final_output):
    """Delete leftovers only. Returns bytes freed. Never removes a finished
    output (is_final_output(path) is main.py's own definition of one) and
    never touches a job that is running right now."""
    if not _cleanup_lock.acquire(blocking=False):
        return 0        # another clean-up is already running
    freed = 0
    try:
        cutoff = time.time() - STALE_HOURS * 3600
        busy = _busy_ids()
        for d in (UPLOAD_DIR, OUTPUT_DIR):
            try:
                entries = list(d.glob("*"))
            except Exception:
                continue
            for p in entries:
                try:
                    m = _UUID_RE.match(p.name)
                    if m and m.group(0) in busy:
                        continue
                    if p.is_file() and is_final_output(p):
                        continue
                    if _tree_newest_mtime(p) >= cutoff:
                        continue
                    size = _tree_size(p)
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink()
                    freed += size
                except Exception:
                    pass
        # long-dub projects that have been sitting idle: park them (the video
        # goes, the text stays and the user re-attaches the file)
        try:
            import longdub_service
            before = _tree_size(DATA_DIR / "longjobs") if (DATA_DIR / "longjobs").exists() else 0
            longdub_service.sweep_stale(park_hours=PARK_IDLE_HOURS)
            after = _tree_size(DATA_DIR / "longjobs") if (DATA_DIR / "longjobs").exists() else 0
            freed += max(0, before - after)
        except Exception as ex:
            print(f"[disk-guard] long-dub park sweep skipped: {ex}")
    finally:
        _cleanup_lock.release()
    with _lock:
        _last_cleanup["ts"] = time.time()
        _last_cleanup["freed"] = freed
    if freed:
        print(f"[disk-guard] emergency clean-up freed {freed / 1048576:.0f} MB")
    return freed


# ---------------------------------------------------------------- the guard
def check(incoming_bytes=0, working_gb=WORK_SHORT_GB, is_final_output=None):
    """May a new job of this size start? Returns (ok, info). When the room is
    short it tries emergency_cleanup once and re-checks before saying no."""
    need = RESERVE_GB * GB + max(0, int(incoming_bytes or 0)) + working_gb * GB
    try:
        free = usage()[2]
    except Exception as ex:
        print(f"[disk-guard] could not read disk usage, allowing: {ex}")
        return True, {}
    if free >= need:
        return True, {"free_gb": round(free / GB, 2)}
    if is_final_output is not None:
        emergency_cleanup(is_final_output)
        try:
            free = usage()[2]
        except Exception:
            return True, {}
        if free >= need:
            return True, {"free_gb": round(free / GB, 2)}
    with _lock:
        _last_refusal["ts"] = time.time()
        _last_refusal["count"] += 1
    print(f"[disk-guard] refused new work: free {free / GB:.2f} GB, needed {need / GB:.2f} GB")
    return False, {"free_gb": round(free / GB, 2), "needed_gb": round(need / GB, 2)}


REFUSAL_MESSAGE = ("We're a little over capacity right now, so we can't take a new upload for a few minutes. "
                   "You have not been charged. Please try again shortly.")


# ---------------------------------------------------------------- monitor + e-mail
def _alerts_enabled():
    try:
        import main
        return bool(main._disk_alerts_enabled())
    except Exception:
        return True


def _send_alert_email(snap, level, cleaned_mb):
    if not RESEND_API_KEY or not CONTACT_TO_EMAIL:
        return False
    if not _alerts_enabled():
        return False
    word = "critically full" if level >= 2 else "getting full"
    subject = (f"Lisan AI: server disk is {word} -- {snap['percent_used']:.0f}% used "
               f"({snap['free_gb']:.1f} GB free)")
    body = (
        "Hi,\n\n"
        f"The Railway volume is {snap['percent_used']:.0f}% full: {snap['used_gb']:.2f} GB used of "
        f"{snap['total_gb']:.2f} GB, {snap['free_gb']:.2f} GB free.\n\n"
        + ("New uploads are being turned away with a polite \"try again shortly\" message until space is freed.\n\n"
           if snap['free_gb'] < RESERVE_GB + WORK_SHORT_GB else
           "New uploads still work, but the guard will start turning them away when free space drops "
           f"below about {RESERVE_GB + WORK_SHORT_GB:.1f} GB.\n\n")
        + (f"The automatic clean-up just freed about {cleaned_mb:.0f} MB of leftover temporary files.\n\n" if cleaned_mb >= 1 else
           "The automatic clean-up found nothing more it may safely delete.\n\n")
        + "What you can do:\n"
        "  1. Open the admin dashboard, Health tab, and look at Server Storage.\n"
        "  2. Finished dubs are kept 30 days (subscribers) -- they are the usual reason the disk fills.\n"
        "  3. If this keeps happening, grow the volume: Railway Pro allows a much bigger one.\n\n"
        "You can switch these e-mails off in the admin panel (Settings: Send disk-space alerts).\n\n"
        "-- Lisan AI monitoring"
    )
    payload = json.dumps({"from": "Lisan AI <noreply@lisanai.org>", "to": [CONTACT_TO_EMAIL],
                          "subject": subject, "text": body}).encode("utf-8")
    try:
        req = urllib.request.Request(
            "https://api.resend.com/emails", data=payload,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json",
                     "User-Agent": "LisanAI-Backend/1.0 (+https://lisanai.org)"},
            method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status in (200, 201)
    except Exception as ex:
        print(f"[disk-guard] alert email failed: {ex}")
        return False


def poll_once(is_final_output, send_email=_send_alert_email):
    """One monitor cycle (separate function so it can be tested)."""
    global _alert_level, _last_alert_sent
    snap = snapshot()
    with _lock:
        _latest.clear()
        _latest.update(snap)
        _latest["ts"] = time.time()
    if not snap.get("ok"):
        return snap
    cleaned = 0
    if snap["percent_used"] >= WARN_PERCENT:
        cleaned = emergency_cleanup(is_final_output)
        if cleaned:
            snap = snapshot()
            with _lock:
                _latest.clear()
                _latest.update(snap)
                _latest["ts"] = time.time()
    pct = snap["percent_used"]
    level = 2 if pct >= CRIT_PERCENT else (1 if pct >= WARN_PERCENT else 0)
    now = time.time()
    if level == 0:
        if pct < WARN_PERCENT - 5:
            _alert_level = 0           # fully calmed down: the next rise alerts again
        return snap
    should = level > _alert_level or (now - _last_alert_sent) > RENOTIFY_HOURS * 3600
    if should and send_email(snap, level, cleaned / 1048576):
        # only counted as "told" once the e-mail really went out, so a
        # temporary Resend failure is retried on the next check
        _last_alert_sent = now
        _alert_level = max(_alert_level, level)
    return snap


def _worker(is_final_output):
    time.sleep(60)
    while True:
        try:
            poll_once(is_final_output)
        except Exception as ex:
            print(f"[disk-guard] monitor error: {ex}")
        time.sleep(CHECK_INTERVAL_MIN * 60)


def start(is_final_output):
    threading.Thread(target=_worker, args=(is_final_output,), daemon=True).start()
