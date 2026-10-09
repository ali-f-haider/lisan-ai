"""The last settings (prices, limits, switches) that were read from the database successfully.

When the database cannot be read for a moment (a timeout, a short outage) the server must go on with the settings the admin
saved, not with the built-in defaults. The defaults are only for a server that has never read the settings at all.
The last good copy is kept in memory and also on disk, so it survives a restart that happens during such a moment."""
import copy
import json
import os
import threading
import time
import uuid
from pathlib import Path


class LastGood:
    def __init__(self, path, clock=time.time, log=print):
        self._path = Path(path)
        self._clock = clock
        self._log = log
        self._lock = threading.Lock()
        self._text = None            # the saved settings as JSON text (compared to skip needless disk writes)
        self._config = None
        self._at = None

    def remember(self, config):
        """Keep this successfully read config as the last good one. Never raises."""
        try:
            text = json.dumps(config, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            return                                        # something unsaveable: keep the previous good copy
        with self._lock:
            if text == self._text:
                self._at = self._clock()
                return
            self._text, self._config, self._at = text, json.loads(text), self._clock()
            at = self._at
        self._write(text, at)

    def recall(self):
        """(config, seconds since it was read) of the last good copy, or (None, None) when there is none."""
        with self._lock:
            if self._config is None:
                self._load()
            if self._config is None:
                return None, None
            return copy.deepcopy(self._config), max(0.0, self._clock() - (self._at or self._clock()))

    # ---- disk copy
    def _write(self, text, at):
        tmp = self._path.with_name(f"{self._path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({"saved_at": at, "config": json.loads(text)}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._path)
        except Exception as ex:                           # noqa: BLE001 - only the restart safety is lost
            self._log(f"[settings] could not save the last good settings: {ex}")
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def _load(self):
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            config = data.get("config")
            if isinstance(config, dict) and config:
                self._config = config
                self._text = json.dumps(config, sort_keys=True, ensure_ascii=False)
                self._at = float(data.get("saved_at") or self._clock())
        except Exception:                                 # noqa: BLE001 - no usable disk copy
            pass
