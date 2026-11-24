"""Where people stop in the short dub (1.104.0).

The short dub page (/app) sends small notes while somebody works: which step (card) appeared, every message the app
showed them (the pop-ups and the message boxes, already in our own words), a browser error, and when they left the page
and at which step. Nothing the person wrote or uploaded is sent: no transcript, no translation, no file names on purpose
(the page sends only our messages, cut to 300 characters).

Kept per account on the data volume (DATA_DIR/short_trace/<uid>.jsonl), at most MAX_EVENTS notes and never older than
KEEP_DAYS days, so no database change is needed. The admin Activity tab shows the last step and the last messages next to
each new account. Deleted with the account. Nothing here raises.
"""
import json
import os
import re
import threading
import time
from pathlib import Path

from config import DATA_DIR

KEEP_DAYS = 30
MAX_EVENTS = 400                 # per account; older notes are dropped first
MAX_PER_CALL = 40
_UID = re.compile(r"[0-9A-Za-z-]{8,64}")
_KINDS = {"open", "step", "msg", "dialog", "jserror", "leave"}
_FOLDER = Path(DATA_DIR) / "short_trace"
_lock = threading.Lock()


def _file(uid):
    uid = str(uid or "").strip()
    return _FOLDER / f"{uid}.jsonl" if _UID.fullmatch(uid) else None


def _clean(ev, now):
    """One note as the page sent it -> a safe, short dict, or None."""
    if not isinstance(ev, dict):
        return None
    kind = str(ev.get("k") or "")
    if kind not in _KINDS:
        return None
    out = {"at": now, "k": kind}
    step = re.sub(r"[^0-9A-Za-z_.-]", "", str(ev.get("s") or ""))[:40]
    if step:
        out["s"] = step
    text = " ".join(str(ev.get("t") or "").split())[:300]
    if text:
        out["t"] = text
    level = str(ev.get("l") or "")
    if level in ("error", "success", "info", "warning"):
        out["l"] = level
    job = re.sub(r"[^0-9A-Za-z_-]", "", str(ev.get("j") or ""))[:64]
    if job:
        out["j"] = job
    try:
        secs = int(ev.get("d") or 0)
        if 0 < secs < 86400:
            out["d"] = secs             # seconds on the page (only for "leave")
    except (TypeError, ValueError):
        pass
    return out


def _read(p):
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for ln in lines:
        try:
            ev = json.loads(ln)
            if isinstance(ev, dict):
                out.append(ev)
        except Exception:
            pass
    return out


def add(uid, events):
    """Saves up to MAX_PER_CALL notes for this account. Returns how many were kept."""
    p = _file(uid)
    if p is None or not isinstance(events, list):
        return 0
    now = int(time.time())
    clean = [c for c in (_clean(e, now) for e in events[:MAX_PER_CALL]) if c]
    if not clean:
        return 0
    try:
        with _lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                for c in clean:
                    f.write(json.dumps(c, ensure_ascii=False) + "\n")
            if p.stat().st_size > MAX_EVENTS * 250:           # roughly too many: keep the newest ones that are recent enough
                keep = [e for e in _read(p) if e.get("at", 0) >= now - KEEP_DAYS * 86400][-MAX_EVENTS:]
                tmp = p.with_suffix(".tmp")
                tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in keep), encoding="utf-8")
                os.replace(tmp, p)
        return len(clean)
    except Exception as ex:
        print(f"[short_trace] could not save: {ex}")
        return 0


def events(uid, limit=MAX_EVENTS):
    """The notes of this account, oldest first (only the last KEEP_DAYS days)."""
    p = _file(uid)
    if p is None or not p.exists():
        return []
    since = time.time() - KEEP_DAYS * 86400
    return [e for e in _read(p) if e.get("at", 0) >= since][-limit:]


def summary(uid):
    """For the admin Activity tab: {"opened", "last_step", "left_at", "seconds", "messages": [last problems], "notes"},
    or None when this account never opened the short dub page."""
    evs = events(uid)
    if not evs:
        return None
    last_step = ""
    for e in evs:
        if e.get("k") == "step" and e.get("s"):
            last_step = e["s"]
    leave = next((e for e in reversed(evs) if e.get("k") == "leave"), None)
    problems = [e for e in evs if e.get("k") == "jserror" or (e.get("k") in ("msg", "dialog") and e.get("l") in ("error", "warning"))]
    return {"opened": sum(1 for e in evs if e.get("k") == "open"), "last_step": last_step,
            "left_at": (leave or {}).get("s") or "", "seconds": (leave or {}).get("d") or 0,
            "last_at": evs[-1].get("at"), "notes": len(evs),
            "messages": [{"at": e.get("at"), "k": e.get("k"), "l": e.get("l", ""), "s": e.get("s", ""), "t": e.get("t", "")}
                         for e in problems[-3:]]}


def forget(uid):
    p = _file(uid)
    if p is not None:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
