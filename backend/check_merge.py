import sys, subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
UP = BASE / "uploads"
OUT = BASE / "outputs"
VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm", ".avi")

job = sys.argv[1].strip() if len(sys.argv) > 1 else ""
if not job:
    print("Uploads folder content:")
    for p in sorted(UP.iterdir()):
        print("  ", p.name)
    job = input("Paste the job id: ").strip()

print("\n--- 1) Looking for separated background (no_vocals.wav) ---")
cands = sorted(UP.glob(f"{job}_separated/**/no_vocals.wav")) + sorted(UP.glob(f"**/{job}*/no_vocals.wav"))
bg = cands[0] if cands else None
print("Found:", bg if bg else "NONE")

src = None
if bg is None:
    print("\n--- 2) Not found: running vocal separation now (may take a few minutes) ---")
    for p in sorted(UP.glob(f"{job}*")):
        if p.suffix.lower() in VIDEO_EXTS + (".wav", ".mp3") and "_separated" not in p.name:
            src = p
            break
    if src is None:
        print("ERROR: source media for this job not found in uploads.")
        sys.exit(1)
    print("Source:", src.name)
    subprocess.run([sys.executable, "-m", "demucs", "-n", "htdemucs", "-o", str(UP / f"{job}_separated"), str(src)], check=True)
    cands = sorted(UP.glob(f"{job}_separated/**/no_vocals.wav"))
    bg = cands[0] if cands else None
    print("Background after separation:", bg if bg else "STILL NONE -> separation failed")
    if bg is None:
        sys.exit(1)

dub = OUT / "final_dubbed.mp3"
video = None
for p in sorted(UP.glob(f"{job}*")):
    if p.suffix.lower() in VIDEO_EXTS and "_separated" not in p.name:
        video = p
        break
print("\n--- 3) Rebuilding final_dubbed_video.mp4 ---")
print("Dub:", dub.name if dub.exists() else "MISSING", "| Video:", video.name if video else "MISSING")
if not dub.exists() or video is None:
    sys.exit(1)

mixed = OUT / "merge_mixed_repair.wav"
subprocess.run(["ffmpeg", "-y", "-i", str(dub), "-i", str(bg), "-filter_complex",
                "[0:a]volume=1.0[d];[1:a]volume=0.8[b];[d][b]amix=inputs=2:duration=first:normalize=0[out]",
                "-map", "[out]", str(mixed)], check=True, capture_output=True)
final = OUT / "final_dubbed_video.mp4"
subprocess.run(["ffmpeg", "-y", "-i", str(video), "-i", str(mixed),
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", str(final)],
               check=True, capture_output=True)
mixed.unlink(missing_ok=True)
print("DONE:", final, "- play it: dubbed vocals + original background.")