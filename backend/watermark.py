"""Watermark for the free tier.

People who only use the free starting credits (no subscription and no credit pack ever bought) get the Lisan AI logo,
semi-transparent, in the bottom-right corner of the videos they make. Sound is never touched, and audio files are
never marked. Made with ffmpeg and the PNG next to this module (watermark_logo.png, already semi-transparent).
Who is "free" is decided by main.py; this module only does the marking. Nothing here raises: every function returns
{"ok": bool, "reason": str}.

Switch it off for everybody with the environment variable WATERMARK_FREE=0. WATERMARK_EXEMPT_UIDS (comma separated user
ids) lists accounts that are never marked, for test accounts.
"""
import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGO = HERE / "watermark_logo.png"

ENABLED = os.environ.get("WATERMARK_FREE", "1").strip().lower() not in ("0", "off", "no", "false")
EXEMPT_UIDS = {u.strip().lower() for u in os.environ.get("WATERMARK_EXEMPT_UIDS", "").split(",") if u.strip()}

LOGO_WIDTH_SHARE = 0.11         # logo width as a share of the video width
LOGO_MIN_WIDTH = 64
LOGO_MARGIN_SHARE = 0.025
TIMEOUT_SEC = int(os.environ.get("WATERMARK_TIMEOUT_SEC", "1500") or 1500)
PRESET = os.environ.get("WATERMARK_PRESET", "veryfast").strip() or "veryfast"    # x264 speed: ultrafast is quicker and larger


def exempt(uid):
    return str(uid or "").strip().lower() in EXEMPT_UIDS


def _probe(path):
    """{"w", "h", "duration", "audio"} of a media file (rotation applied), or None."""
    j = None
    for entries in ("stream=codec_type,width,height:stream_tags=rotate:stream_side_data=rotation:format=duration",
                    "stream=codec_type,width,height:format=duration"):      # the second one for an older ffprobe
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
                               capture_output=True, timeout=60)
            if r.returncode == 0:
                j = json.loads(r.stdout.decode("utf-8", "ignore") or "{}")
                if j.get("streams") is not None:
                    break
        except Exception:
            j = None
    if j is None:
        return None
    out = {"w": 0, "h": 0, "duration": None, "audio": False}
    try:
        out["duration"] = float((j.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        pass
    got_video = False
    for s in j.get("streams") or []:
        if s.get("codec_type") == "audio":
            out["audio"] = True
        elif s.get("codec_type") == "video" and not got_video:
            got_video = True
            w, h = int(s.get("width") or 0), int(s.get("height") or 0)
            rot = 0
            try:
                rot = int(float((s.get("tags") or {}).get("rotate") or 0))
            except (TypeError, ValueError):
                rot = 0
            for sd in s.get("side_data_list") or []:
                if "rotation" in sd:
                    try:
                        rot = int(float(sd["rotation"]))
                    except (TypeError, ValueError):
                        pass
            if abs(rot) % 180 == 90:
                w, h = h, w
            out["w"], out["h"] = w, h
    return out


def _run(cmd, timeout):
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode("utf-8", "ignore")[-300:].strip() or f"ffmpeg exit {r.returncode}")


def apply_video(src):
    """Marks the video file `src` IN PLACE (picture and sound). Returns {"ok", "reason"}; the file is untouched on failure."""
    src = Path(src)
    tmp = src.with_name(src.stem + ".wm.tmp.mp4")
    try:
        if not LOGO.exists():
            return {"ok": False, "reason": "watermark_logo.png is missing"}
        info = _probe(src)
        if not info or not info["w"] or not info["h"]:
            return {"ok": False, "reason": "could not read the video"}
        W, H, dur = info["w"], info["h"], info["duration"]
        lw = max(LOGO_MIN_WIDTH, int(round(W * LOGO_WIDTH_SHARE / 2.0)) * 2)
        mg = max(8, int(round(W * LOGO_MARGIN_SHARE)))
        fc = (f"[1:v]scale={lw}:-1,format=rgba[lg];[0:v][lg]overlay=x=W-w-{mg}:y=H-h-{mg},format=yuv420p[vout]")
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-i", str(LOGO), "-filter_complex", fc,
               "-map", "[vout]", "-map", "0:a?", "-c:a", "copy",
               "-c:v", "libx264", "-preset", PRESET, "-crf", "21", "-movflags", "+faststart", str(tmp)]
        _run(cmd, TIMEOUT_SEC)
        if not tmp.exists() or tmp.stat().st_size < 1000:
            return {"ok": False, "reason": "no output"}
        chk = _probe(tmp)
        if not chk or (dur and chk["duration"] and abs(chk["duration"] - dur) > max(1.5, dur * 0.02)):
            return {"ok": False, "reason": "the marked video has the wrong length"}
        os.replace(tmp, src)
        return {"ok": True, "reason": "logo"}
    except Exception as ex:
        return {"ok": False, "reason": str(ex)[:300]}
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
