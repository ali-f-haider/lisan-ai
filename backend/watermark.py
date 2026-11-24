"""Watermark for the free tier.

People who only use the free starting credits (no subscription and no credit pack ever bought) get the Lisan AI logo,
semi-transparent, in the bottom-right corner of the videos they make. Sound is never touched, and audio files are
never marked. Made with ffmpeg and the PNG next to this module (watermark_logo.png, already semi-transparent).
Who is "free" is decided by main.py; this module only does the marking. Nothing here raises: every function returns
{"ok": bool, "reason": str}.

Since 1.103.0 finish_video() is the one finishing step for every video (and long-dub audio) file we deliver: it adds
the free-tier logo when needed, the EU "AI modified" label when the customer chose one (see ai_label.py), and always a
hidden "AI-modified" note in the file information (AI_TAG), all in one pass. apply_video() is the logo-only form.

Switch it off for everybody with the environment variable WATERMARK_FREE=0. WATERMARK_EXEMPT_UIDS (comma separated user
ids) lists accounts that are never marked, for test accounts.
"""
import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGO = HERE / "watermark_logo.png"
LABEL = HERE / "static" / "eu_ai_modified.png"      # the European Commission's free "AI MODIFIED" icon (black), see ai_label.py
AI_TAG = "AI-modified content: Arabic dub made with Lisan AI (lisanai.org)"

ENABLED = os.environ.get("WATERMARK_FREE", "1").strip().lower() not in ("0", "off", "no", "false")
EXEMPT_UIDS = {u.strip().lower() for u in os.environ.get("WATERMARK_EXEMPT_UIDS", "").split(",") if u.strip()}

LOGO_WIDTH_SHARE = 0.11         # logo width as a share of the video width
LOGO_MIN_WIDTH = 64
LOGO_MARGIN_SHARE = 0.025
TIMEOUT_SEC = int(os.environ.get("WATERMARK_TIMEOUT_SEC", "1500") or 1500)
LABEL_HEIGHT_SHARE = 0.055      # label height as a share of the video's shorter side
LABEL_MIN_HEIGHT = 22
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


def _png_size(path):
    """(width, height) of a PNG from its header, or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    except Exception:
        return None


def _even(x):
    return max(2, int(round(x / 2.0)) * 2)


def _tag_args():
    return ["-metadata", f"comment={AI_TAG}", "-metadata", f"description={AI_TAG}"]


def apply_video(src):
    """Marks the video file `src` IN PLACE with the free-tier logo (and the hidden note). Returns {"ok", "reason"}."""
    return finish_video(src, logo=True)


def finish_video(src, logo=False, label=""):
    """Finishes the delivered file `src` IN PLACE: the free-tier logo (logo=True, bottom right), the EU "AI modified"
    label (label = "tl" / "tr" / "bl" / "br", "" for none) and always the hidden AI note. Pictures are only touched
    when a logo or label is asked for; otherwise the file is only copied with the note (fast, no quality change).
    An audio file only gets the note. On any failure the file is left exactly as it was.
    Returns {"ok", "reason", "logo": bool, "label": bool} (logo / label: really put on)."""
    src = Path(src)
    tmp = src.with_name(src.stem + ".wm.tmp" + (src.suffix or ".mp4"))
    res = {"ok": False, "reason": "", "logo": False, "label": False}
    try:
        info = _probe(src)
        if not info:
            res["reason"] = "could not read the file"
            return res
        W, H, dur = info["w"], info["h"], info["duration"]
        label = label if label in ("tl", "tr", "bl", "br") else ""
        want_logo, want_label = bool(logo), bool(label)
        if want_logo and not LOGO.exists():
            if not want_label:
                res["reason"] = "watermark_logo.png is missing"
                return res
            want_logo = False
        if want_label and not LABEL.exists():
            want_label = False
        if not W or not H:
            want_logo = want_label = False                 # an audio file: only the hidden note
        if not want_logo and not want_label:
            cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0", "-c", "copy"] + _tag_args()
            if src.suffix.lower() in (".mp4", ".m4a", ".mov"):
                cmd += ["-movflags", "+faststart"]
            _run(cmd + [str(tmp)], TIMEOUT_SEC)
        else:
            cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src)]
            parts, last, n = [], "0:v", 1
            mg = max(8, int(round(W * LOGO_MARGIN_SHARE)))
            logo_h = 0
            if want_logo:
                lw = max(LOGO_MIN_WIDTH, int(round(W * LOGO_WIDTH_SHARE / 2.0)) * 2)
                ps = _png_size(LOGO)
                logo_h = int(round(lw * ps[1] / ps[0])) if ps and ps[0] else lw
                cmd += ["-i", str(LOGO)]
                parts.append(f"[{n}:v]scale={lw}:-1,format=rgba[lg]")
                parts.append(f"[{last}][lg]overlay=x=W-w-{mg}:y=H-h-{mg}[v{n}]")
                last, n = f"v{n}", n + 1
            if want_label:
                ps = _png_size(LABEL) or (640, 139)
                lh = _even(max(LABEL_MIN_HEIGHT, min(W, H) * LABEL_HEIGHT_SHARE))
                lbw = _even(lh * ps[0] / ps[1])
                x = f"{mg}" if label in ("tl", "bl") else f"W-w-{mg}"
                if label in ("tl", "tr"):
                    y = f"{mg}"
                else:                                      # above the logo when both sit bottom right
                    y = f"H-h-{mg + (logo_h + mg // 2 if want_logo and label == 'br' else 0)}"
                cmd += ["-i", str(LABEL)]
                parts.append(f"[{n}:v]scale={lbw}:{lh},format=rgba[lb]")
                parts.append(f"[{last}][lb]overlay=x={x}:y={y}[v{n}]")
                last, n = f"v{n}", n + 1
            parts.append(f"[{last}]format=yuv420p[vout]")
            cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "0:a?", "-c:a", "copy",
                    "-c:v", "libx264", "-preset", PRESET, "-crf", "21"] + _tag_args() + ["-movflags", "+faststart", str(tmp)]
            _run(cmd, TIMEOUT_SEC)
        if not tmp.exists() or tmp.stat().st_size < 1000:
            res["reason"] = "no output"
            return res
        chk = _probe(tmp)
        if not chk or (dur and chk["duration"] and abs(chk["duration"] - dur) > max(1.5, dur * 0.02)):
            res["reason"] = "the finished file has the wrong length"
            return res
        os.replace(tmp, src)
        done = [k for k, v in (("logo", want_logo), ("label", want_label)) if v]
        res.update(ok=True, reason="+".join(done + ["tag"]), logo=want_logo, label=want_label)
        return res
    except Exception as ex:
        res["reason"] = str(ex)[:300]
        return res
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
