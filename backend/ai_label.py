"""The EU "AI modified" label a customer can put on their own videos (1.103.0).

EU AI Act, Article 50: whoever publishes an AI-changed video of real people may have to say so. The European
Commission offers free icons for that (digital-strategy.ec.europa.eu, "EU icons for labelling AI-generated content",
free to use, no attribution). We use its "AI MODIFIED" icon (black), saved as static/eu_ai_modified.png.
A dub changes the voice, not the whole video, so "modified" is the right one of the three.

Each account chooses once, on the Account page (Files tab): off (the default) or one of the four corners. It is then
put on every video that account makes (short dub merge, lip-sync, long dub). Audio files never get it. Separately,
every finished file always carries a hidden "AI-modified" note in its file information (watermark.AI_TAG).

Kept on the data volume (DATA_DIR/ai_label/<uid>.json), so no database change is needed. Nothing here raises.
"""
import json
import os
import re
from pathlib import Path

from config import DATA_DIR

CORNERS = ("tl", "tr", "bl", "br")      # top left, top right, bottom left, bottom right
_UID = re.compile(r"[0-9A-Za-z-]{8,64}")
_FOLDER = Path(DATA_DIR) / "ai_label"


def _file(uid):
    uid = str(uid or "").strip()
    return _FOLDER / f"{uid}.json" if _UID.fullmatch(uid) else None


def get(uid):
    """The corner this account chose ("tl", "tr", "bl", "br"), or "" when the label is off."""
    p = _file(uid)
    if p is None:
        return ""
    try:
        corner = (json.loads(p.read_text(encoding="utf-8")) or {}).get("corner") or ""
    except Exception:
        return ""
    return corner if corner in CORNERS else ""


def set_(uid, corner):
    """Saves the choice ("" switches it off). Returns the saved corner, or None when it could not be saved."""
    p = _file(uid)
    corner = corner if corner in CORNERS else ""
    if p is None:
        return None
    try:
        if not corner:
            p.unlink(missing_ok=True)
            return ""
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"corner": corner}), encoding="utf-8")
        os.replace(tmp, p)
        return corner
    except Exception as ex:
        print(f"[ai_label] could not save: {ex}")
        return None


def forget(uid):
    """Removes the choice of a deleted account."""
    p = _file(uid)
    if p is not None:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
