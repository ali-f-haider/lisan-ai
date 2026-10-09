"""The on/off switch of the site-wide "Under Construction" access gate.

The switch lives in the database (set in the admin panel). Reading it can fail for a moment (a slow or unreachable
database). Such a failure must never change the answer: the gate keeps showing what it last knew, and that last known
value is also saved on disk, so it survives a restart. Only a server that has never once read the switch (a brand-new
install, nothing saved on disk) closes the gate until the first successful read, which is the safe side for a site
that is meant to be private.

Reads never hold up a page: once a value is known, a stale value is refreshed in the background while the request is
answered with the value it already has."""
import json
import os
import threading
import time
import uuid
from pathlib import Path


class GateSwitch:
    def __init__(self, read, path, ttl=20.0, retry=10.0, clock=time.time, background=True, log=print):
        """read() returns True/False (the saved switch) or None (no saved value yet), and RAISES when it cannot read."""
        self._read = read
        self._path = Path(path)
        self._ttl = float(ttl)
        self._retry = float(retry)
        self._clock = clock
        self._background = background
        self._log = log
        self._lock = threading.Lock()
        self._busy = False
        self._next = 0.0
        self._value = self._load()           # None = never known

    # ---- the value saved on disk
    def _load(self):
        try:
            v = json.loads(self._path.read_text(encoding="utf-8")).get("enabled")
            return v if isinstance(v, bool) else None
        except Exception:
            return None

    def _store(self, value):
        tmp = self._path.with_name(f"{self._path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({"enabled": bool(value)}), encoding="utf-8")
            os.replace(tmp, self._path)
        except Exception as ex:               # noqa: BLE001 - a missing disk copy only loses the restart safety
            self._log(f"[site-gate] could not save the last known switch: {ex}")
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    # ---- reading the switch
    def _refresh(self):
        try:
            try:
                got = self._read()
            except Exception as ex:           # noqa: BLE001 - any failure means "keep what we knew"
                with self._lock:
                    self._next = self._clock() + self._retry
                    kept = self._value
                self._log(f"[site-gate] could not read the switch ({ex}); keeping "
                          f"{'the last known value: ' + ('on' if kept else 'off') if kept is not None else 'it on (never read yet)'}")
                return
            value = True if got is None else bool(got)         # no saved value = the default, on
            with self._lock:
                changed = value != self._value
                self._value = value
                self._next = self._clock() + self._ttl
            if changed:
                self._store(value)
        finally:
            with self._lock:
                self._busy = False

    def enabled(self):
        """True when the gate must be enforced right now."""
        start = False
        with self._lock:
            if self._clock() >= self._next and not self._busy:
                self._busy = start = True
            known = self._value is not None
        if start:
            if known and self._background:
                threading.Thread(target=self._refresh, name="site-gate-refresh", daemon=True).start()
            else:
                self._refresh()               # nothing known yet: wait for the first answer
        with self._lock:
            return True if self._value is None else self._value

    def apply(self, value):
        """The admin just saved the switch: use it at once instead of waiting for the next refresh."""
        value = bool(value)
        with self._lock:
            changed = value != self._value
            self._value = value
            self._next = self._clock() + self._ttl
        if changed:
            self._store(value)
