"""Letting the server sleep when nobody uses it (Railway "Serverless"), without ever sleeping in the middle of a job.

Railway puts a service to sleep after it has sent no outbound packet for about 5-10 minutes, and wakes it on the next
request from the internet (that first request can wait or get a 502 while the server starts). Two things follow:

1. Our own regular background work (refund retries, monitors, backups, reading the settings) must not go online while
   the site is quiet, or the server never sleeps. Those loops sleep with `keep_awake.sleep(seconds)`: after the pause
   they wait, without any network use, until the site is used again or a job runs.
2. A job must keep the server awake even when the person who started it closed the page and the job itself is busy
   on the processor for minutes (voice separation, speaker detection): while a job runs, a tiny packet (one DNS
   question, under 100 bytes) is sent every HEARTBEAT_SEC.

"Quiet" = no request from outside for QUIET_MIN minutes (env SERVER_QUIET_MIN, default 10) and no job running.
Nothing here changes what a job does. Pure standard library."""
import os
import random
import socket
import struct
import threading
import time

try:
    QUIET_MIN = min(240.0, max(2.0, float(os.environ.get("SERVER_QUIET_MIN") or 10)))
except ValueError:
    QUIET_MIN = 10.0
HEARTBEAT_SEC = 120            # well under Railway's 5 minutes
CHECK_SEC = 30                 # how often a waiting loop looks again (local only, no network)
HEARTBEAT_TO = ("1.1.1.1", 53)

_lock = threading.Lock()
_last_request = time.time()    # starting up counts as being used
_holds = 0                     # jobs running right now (see hold())
_extra_busy = []               # more "is something running?" checks (the work queue), registered by main.py
_stats = {"heartbeats": 0, "quiet_since": None}


def touch():
    """A request came in from outside."""
    global _last_request
    _last_request = time.time()


class hold:
    """`with keep_awake.hold():` around a job: the server stays awake until it ends."""

    def __enter__(self):
        global _holds
        with _lock:
            _holds += 1
        _ensure_heartbeat()
        return self

    def __exit__(self, *exc):
        global _holds
        with _lock:
            _holds = max(0, _holds - 1)
        touch()                      # a job that just ended is activity: its own follow-up work may still go online
        return False


def add_busy_check(fn):
    """fn() -> True while something runs that this module does not see (e.g. jobs waiting in the queue)."""
    _extra_busy.append(fn)


def busy():
    if _holds > 0:
        return True
    for fn in list(_extra_busy):
        try:
            if fn():
                return True
        except Exception:
            pass
    return False


def quiet(now=None):
    """True when nobody has used the site for QUIET_MIN minutes and no job runs: background work stays offline."""
    now = time.time() if now is None else now
    q = (now - _last_request) > QUIET_MIN * 60 and not busy()
    if q and _stats["quiet_since"] is None:
        _stats["quiet_since"] = now
    elif not q:
        _stats["quiet_since"] = None
    return q


def sleep(seconds):
    """Use instead of time.sleep(seconds) in a loop that goes online: after the pause, waits (offline) while quiet."""
    time.sleep(max(0.0, float(seconds)))
    while quiet():
        time.sleep(CHECK_SEC)


def _dns_packet():
    """One small DNS question (for 'railway.app'), so the packet is ordinary, harmless traffic."""
    qid = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    name = b"".join(bytes([len(p)]) + p for p in (b"railway", b"app")) + b"\x00"
    return header + name + struct.pack(">HH", 1, 1)


def ping():
    """Send the keep-awake packet (no answer needed). Never raises."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(2)
            s.sendto(_dns_packet(), HEARTBEAT_TO)
        _stats["heartbeats"] += 1
        return True
    except Exception:
        return False


_hb_started = False


def _heartbeat_loop():
    while True:
        time.sleep(HEARTBEAT_SEC)
        if busy():
            ping()


def _ensure_heartbeat():
    global _hb_started
    if _hb_started:
        return
    with _lock:
        if _hb_started:
            return
        _hb_started = True
    threading.Thread(target=_heartbeat_loop, daemon=True, name="keep-awake").start()


def start():
    """Called once by main.py: the heartbeat also covers jobs that only the extra busy checks see."""
    _ensure_heartbeat()


def state():
    """For the admin page."""
    now = time.time()
    return {"quiet": quiet(now), "quiet_min": QUIET_MIN, "busy": busy(),
            "minutes_since_last_request": round((now - _last_request) / 60.0, 1),
            "heartbeats_sent": _stats["heartbeats"]}
