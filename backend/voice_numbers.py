"""Permanent numbers for "Male voice N" / "Female voice N".

A voice gets its number the first time it is listed and keeps it for good: the register only ever grows. A voice that
disappears from the provider's list keeps its number reserved (it is never given to another voice), and a new voice gets
the next free number. So a saved project or a remembered choice such as "Male voice 17" always means the same voice.

One register per engine (the two engines list different voices), stored next to the other data files. When a new engine's
register is created, voices are numbered in the order the caller hands them over (the first time ever for the original
list: by voice id, which is what the numbering was before this register existed).
"""
import json
import os
import tempfile
import threading
from pathlib import Path

from config import DATA_DIR

FILE = Path(DATA_DIR) / "voice_numbers.json"
_lock = threading.Lock()
_memory = {"data": None}
GENDERS = ("male", "female")


def _empty():
    return {"engines": {}}


def _restore():
    """The data volume lost the register: use the newest off-site copy when there is one (and it is sound), else None."""
    try:
        import r2_backup
        raw = r2_backup.restore_register_file(FILE.name)
        if not raw:
            return None
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("engines"), dict):
            return None
        _write(data)
        print("[voice-numbers] the register was missing; restored it from the off-site backup")
        return data
    except Exception as ex:
        print(f"[voice-numbers] could not restore the register from backup ({ex})")
        return None


def _read():
    """The saved register, an empty one when none exists yet, None when the file exists but cannot be trusted."""
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _restore() or _empty()
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("engines"), dict):
        return None
    return data


def _write(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, FILE)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def reset_memory():
    with _lock:
        _memory["data"] = None


def assign(engine, rows):
    """Adds "number" to every row that has a male/female gender (new voices are numbered in the order given) and returns the rows.

    Never raises. When the register cannot be read, numbers fall back to the position in the list (the old behaviour) and
    the damaged file is left untouched for a person to look at."""
    rows = list(rows or [])
    try:
        with _lock:
            data = _memory["data"] or _read()
            if data is None:
                print("[voice-numbers] the register file cannot be read; numbering by position until it is fixed")
                _positional(rows)
                return rows
            reg = data["engines"].setdefault(str(engine), {})
            dirty = False
            for g in GENDERS:
                known = reg.setdefault(g, {})
                for row in rows:
                    if str(row.get("gender") or "").lower() != g or not row.get("voice_id"):
                        continue
                    vid = str(row["voice_id"])
                    if vid not in known:
                        known[vid] = max(known.values(), default=0) + 1
                        dirty = True
                    row["number"] = known[vid]
            if dirty:
                try:
                    _write(data)
                except Exception as ex:
                    print(f"[voice-numbers] could not save the register ({ex}); these numbers may change on the next restart")
            _memory["data"] = data
    except Exception as ex:
        print(f"[voice-numbers] numbering failed ({ex}); numbering by position")
        _positional(rows)
    return rows


def _positional(rows):
    count = {g: 0 for g in GENDERS}
    for row in rows:
        g = str(row.get("gender") or "").lower()
        if g in count:
            count[g] += 1
            row["number"] = count[g]
