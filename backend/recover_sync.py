import sys, json, time, shutil, subprocess, urllib.request, urllib.error
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "outputs"
UP = BASE / "uploads"


def get(url, key):
    last = None
    for headers in ({"x-api-key": key}, {"Authorization": f"Bearer {key}"}):
        for u in (url + "?include=progress", url):
            req = urllib.request.Request(u, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.load(r), None
            except urllib.error.HTTPError as e:
                last = e.code
            except Exception as e:
                last = str(e)
    return None, last


def main():
    if len(sys.argv) < 3:
        print("Usage: python recover_sync.py <sync_api_key> <generation_id>")
        return
    key, gid = sys.argv[1].strip(), sys.argv[2].strip()
    url = f"https://api.sync.so/v2/generate/{gid}"

    out_url = None
    for attempt in range(120):
        st, err = get(url, key)
        if st is None:
            print("Poll error:", err)
            if err == 403:
                print("403: this generation is NOT readable with this key (different org, or purged).")
                print("Contact Sync Labs support with generation ID:", gid)
                return
            time.sleep(5)
            continue
        status = (st.get("status") or "").upper()
        print("Status:", status, st.get("progress_percent", ""))
        if status == "COMPLETED":
            out_url = st.get("outputUrl") or st.get("output_url")
            break
        if status in ("FAILED", "REJECTED"):
            print("Job failed/rejected:", st.get("error") or st.get("errorCode"))
            return
        time.sleep(5)
    else:
        print("Timed out waiting for completion.")
        return

    raw = OUT / "final_lipsync_raw.mp4"
    subprocess.run(["curl", "-s", "-S", "-L", "-o", str(raw), out_url], check=True)
    print("Downloaded:", raw)

    bgs = sorted(UP.glob("*_separated/htdemucs/*_audio/no_vocals.wav"))
    final = OUT / "final_lipsync.mp4"
    if bgs:
        bg = bgs[-1]
        mixed = OUT / "lipsync_mixed_recover.wav"
        subprocess.run(["ffmpeg", "-y", "-i", str(raw), "-i", str(bg),
                        "-filter_complex",
                        "[0:a]volume=1.0[d];[1:a]volume=0.8[b];[d][b]amix=inputs=2:duration=first:normalize=0[out]",
                        "-map", "[out]", str(mixed)],
                       check=True, capture_output=True)
        subprocess.run(["ffmpeg", "-y", "-i", str(raw), "-i", str(mixed),
                        "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0", "-shortest", str(final)],
                       check=True, capture_output=True)
        try: mixed.unlink()
        except Exception: pass
        print("DONE - final video WITH original background:", final)
    else:
        shutil.copy(raw, final)
        print("DONE - final video (dub only, no background found):", final)


main()