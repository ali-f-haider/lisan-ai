"""Try the music repair on one file, without running a dub.

  python test_music_fill.py music.mp4 12 18            (needs FAL_API_KEY in the environment)

It silences the stretch 12 s to 18 s of the file (as the dub would), asks the AI model to fill that hole using the
music around it, and writes two files next to the input: <name>_hole.wav (before) and <name>_filled.wav (after).
Listen to both. Add GEMINI_API_KEY to the environment to let Gemini describe the music for the prompt, or pass a
prompt as the fourth argument.
"""
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

import music_fill as mf


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return
    src, a, b = Path(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
    prompt = sys.argv[4] if len(sys.argv) > 4 else None
    key = os.environ.get("FAL_API_KEY", "")
    if not key:
        print("Set FAL_API_KEY first (for example:  set FAL_API_KEY=...  on Windows).")
        return
    raw = src.with_name(src.stem + "_hole.pcm")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "2", "-ar", str(mf.RATE),
                    "-f", "s16le", str(raw)], check=True)
    pcm = np.fromfile(raw, dtype="<i2").reshape(-1, 2)
    pcm[int(a * mf.RATE):int(b * mf.RATE)] = 0
    pcm.tofile(raw)
    hole = src.with_name(src.stem + "_hole.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(mf.RATE), "-ac", "2", "-i", str(raw),
                    "-c:a", "pcm_s16le", str(hole)], check=True)
    raw.unlink()
    out = src.with_name(src.stem + "_filled.wav")
    info = mf.fill(hole, out, [(a, b)], key, gemini_key=os.environ.get("GEMINI_API_KEY", ""), prompt=prompt, log=print)
    print(info["reason"])
    print("prompt:", info.get("prompt"))
    for g in info["gaps"]:
        print(f"  {g['start']}-{g['end']} s: {'ok' if g['ok'] else 'NOT filled'} - {g['note']}")
    if info["filled"]:
        print("Written:", hole, "and", out)


if __name__ == "__main__":
    main()
