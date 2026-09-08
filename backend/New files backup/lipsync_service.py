import json
import shutil
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path

import urllib3

from config import OUTPUT_DIR
from app_state import jobs_progress
from ffmpeg_utils import (
    compress_video_for_upload,
    mix_two_audio,
    mux_audio_into_video,
)
from media_paths import find_job_video, job_background_audio

# One shared pool: the SAME client/headers for create AND poll
http = urllib3.PoolManager(timeout=urllib3.Timeout(connect=30, read=900))

ELEVEN_LIPSYNC_ENDPOINTS = [
    "https://api.elevenlabs.io/v1/lip-sync",
    "https://api.elevenlabs.io/v1/lip-sync/create",
    "https://api.elevenlabs.io/v1/lipsync",
]


def _curl_post_multipart(url, api_key, video_path, audio_path):
    cmd = [
        "curl", "-s", "-S", "-X", "POST", url,
        "-H", f"xi-api-key: {api_key}",
        "-H", "Connection: close",
        "-F", f"source=@{str(video_path)};type=video/mp4",
        "-F", f"audio=@{str(audio_path)};type=audio/mpeg",
        "-w", "\n%{http_code}"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except FileNotFoundError:
        raise Exception("System 'curl' not found on this machine.")
    except Exception as e:
        raise Exception(f"curl failed: {e}")
    if result.returncode not in (0, 22):
        raise Exception(f"curl failed (code {result.returncode}): {result.stderr[:300]}")
    output = result.stdout
    if "\n" in output:
        body, status_code = output.rsplit("\n", 1)
        try:
            return int(status_code.strip()), body
        except ValueError:
            return 0, output
    return 0, output


def _synclabs_get(gen_id, sync_key):
    urls = [
        f"https://api.sync.so/v2/generate/{gen_id}?include=progress",
        f"https://api.sync.so/v2/generate/{gen_id}",
        f"https://api.sync.so/v2/generations/{gen_id}",
    ]
    headers_list = [{"x-api-key": sync_key}, {"Authorization": f"Bearer {sync_key}"}]
    last_status = None
    for h in headers_list:
        for u in urls:
            try:
                resp = http.request("GET", u, headers=h)
            except Exception:
                continue
            last_status = resp.status
            if resp.status == 200:
                try:
                    return json.loads(resp.data.decode()), None
                except Exception:
                    continue
    return None, last_status


def _elevenlabs_lipsync(upload_path: Path, audio_path: Path, eleven_key: str, raw_video: Path, progress: dict):
    progress["message"] = f"Uploading to ElevenLabs via system uploader ({upload_path.stat().st_size // 1024 // 1024}MB)..."
    resp_status, resp_text, used_url = 0, "", None
    for url in ELEVEN_LIPSYNC_ENDPOINTS:
        resp_status, resp_text = _curl_post_multipart(url, eleven_key, upload_path, audio_path)
        used_url = url
        if resp_status == 200:
            break
        if resp_status == 404:
            continue
        break
    if resp_status != 200:
        if resp_status == 404:
            raise Exception("ElevenLabs lip-sync returned 404 on all known endpoints: the Lip Sync feature is not enabled for this account/plan (ElevenLabs confirmed there is no public lip-sync API). Use Sync Labs.")
        raise Exception(f"ElevenLabs create HTTP {resp_status}: {resp_text[:500]}")

    created = json.loads(resp_text)
    job_lsid = created.get("id") or created.get("lip_sync_id")
    if not job_lsid:
        raise Exception(f"Create failed: {resp_text[:400]}")

    progress["percent"] = 20
    progress["generation_id"] = job_lsid
    video_url = None
    for _ in range(240):
        time.sleep(5)
        cmd = ["curl", "-s", "-S", "-H", f"xi-api-key: {eleven_key}", f"{used_url.rstrip('/')}/{job_lsid}"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            continue
        try:
            st = json.loads(r.stdout)
        except Exception:
            continue
        status = str(st.get("status") or "").lower()
        progress["message"] = f"ElevenLabs: {status}"
        if status in ("completed", "succeeded", "success"):
            video_url = st.get("video_url") or st.get("download_url") or st.get("output_url") or st.get("url")
            break
        if status in ("failed", "error", "rejected"):
            raise Exception(f"Failed: {json.dumps(st)[:400]}")
    if video_url is None:
        raise Exception("Timed out (~20 min).")
    progress["message"] = "Downloading result..."
    cmd = ["curl", "-s", "-S", "-L", "-o", str(raw_video), video_url]
    subprocess.run(cmd, check=True, timeout=600)


def _synclabs_lipsync(upload_path: Path, audio_path: Path, sync_key: str, model: str, raw_video: Path, progress: dict, job_id: str):
    recover_id = None
    if model.startswith("recover:"):
        recover_id = model.split(":", 1)[1].strip()

    gen_id = recover_id

    if not recover_id:
        if upload_path.stat().st_size > 20 * 1024 * 1024:
            raise Exception("Video > 20MB (Sync Labs limit). Use a shorter clip.")
        fields = {
            "model": model or "lipsync-2",
            "video": ("source.mp4", upload_path.read_bytes(), "video/mp4"),
            "audio": ("dub.mp3", audio_path.read_bytes(), "audio/mpeg"),
        }
        progress["message"] = "Uploading to Sync Labs..."
        auth_header = {"x-api-key": sync_key}
        resp = http.request("POST", "https://api.sync.so/v2/generate", headers=auth_header, fields=fields, encode_multipart=True)
        if resp.status == 403:
            auth_header = {"Authorization": f"Bearer {sync_key}"}
            resp = http.request("POST", "https://api.sync.so/v2/generate", headers=auth_header, fields=fields, encode_multipart=True)
        if resp.status == 403:
            raise Exception("Sync Labs 403 on create: free-tier quota exhausted or key problem. Check sync.so billing.")
        if resp.status not in (200, 201, 202):
            raise Exception(f"Sync Labs HTTP {resp.status}: {resp.data.decode(errors='ignore')[:800]}")
        created = json.loads(resp.data.decode(errors="ignore"))
        gen_id = created.get("id")
        if not gen_id:
            raise Exception(f"Create failed: {json.dumps(created)[:400]}")

    progress["generation_id"] = gen_id
    progress["percent"] = 20
    output_url = None
    
    # NEW: Poll every 20 seconds to avoid triggering Sync Labs' WAF/rate-limits (which caused the 403)
    poll_interval = 20
    for attempt in range(120):
        time.sleep(poll_interval)
        st, code = _synclabs_get(gen_id, sync_key)
        if st is None:
            if code == 403:
                # WAF/Rate-limit block: back off for 60 seconds and retry, don't fail immediately
                progress["message"] = f"Sync Labs: 403 (rate limit?), backing off 60s... (ID: {gen_id[:8]}...)"
                time.sleep(60)
                continue
            
            # If it's another error after many retries, save the ID and fail gracefully
            if attempt > 10:
                billed_file = OUTPUT_DIR / f"lipsync_billed_{job_id}.txt"
                try:
                    billed_file.write_text(gen_id, encoding="utf-8")
                except Exception:
                    pass
                raise Exception(
                    f"Sync Labs error while polling generation {gen_id}. "
                    f"The ID was saved to outputs/lipsync_billed_{job_id}.txt."
                )
            continue
            
        status = (st.get("status") or "").upper()
        prog = st.get("progress_percent")
        if prog is not None:
            try: progress["percent"] = min(90, 20 + int(float(prog) * 0.7))
            except Exception: pass
        progress["message"] = f"Sync Labs: {status} (ID: {gen_id[:8]}...)"
        if status == "COMPLETED":
            output_url = st.get("outputUrl") or st.get("output_url")
            break
        if status in ("FAILED", "REJECTED"):
            raise Exception(f"Job {status}: {st.get('error') or st.get('errorCode')}")
    if not output_url:
        raise Exception("Timed out. Recover later with ID: " + gen_id)
    progress["message"] = "Downloading result..."
    with urllib.request.urlopen(output_url, timeout=600) as resp:
        raw_video.write_bytes(resp.read())


def lipsync_worker(job_id, provider, model, eleven_key, sync_key):
    key = f"lipsync_{job_id}"
    upload_path = None
    try:
        jobs_progress[key] = {"status": "processing", "percent": 5, "message": "Preparing files...",
                              "error": None, "result": None, "generation_id": None}
        video_path = find_job_video(job_id)
        if video_path is None:
            raise Exception("Original video not found.")
        dubbed_audio = OUTPUT_DIR / "final_dubbed.mp3"
        if not dubbed_audio.exists():
            raise Exception("Dubbed audio not found.")

        is_recover = provider == "synclabs" and model.startswith("recover:")
        raw_video = OUTPUT_DIR / f"lipsync_raw_{job_id}.mp4"

        if not is_recover:
            jobs_progress[key]["message"] = "Compressing video for upload..."
            upload_path = OUTPUT_DIR / f"lipsync_upload_{job_id}.mp4"
            compress_video_for_upload(video_path, upload_path)
            if provider == "synclabs":
                if not sync_key: raise Exception("Missing Sync Labs API key.")
                _synclabs_lipsync(upload_path, dubbed_audio, sync_key, model, raw_video, jobs_progress[key], job_id)
            else:
                if not eleven_key: raise Exception("Missing ElevenLabs API key.")
                _elevenlabs_lipsync(upload_path, dubbed_audio, eleven_key, raw_video, jobs_progress[key])
        else:
            if not sync_key: raise Exception("Missing Sync Labs API key.")
            jobs_progress[key]["message"] = "Recovering existing Sync Labs generation..."
            _synclabs_lipsync(None, dubbed_audio, sync_key, model, raw_video, jobs_progress[key], job_id)

        jobs_progress[key]["percent"] = 92
        jobs_progress[key]["message"] = "Mixing background audio back in..."
        background = job_background_audio(job_id)
        final_video = OUTPUT_DIR / "final_lipsync.mp4"
        if background is not None:
            mixed = OUTPUT_DIR / f"lipsync_mixed_{job_id}.wav"
            mix_two_audio(raw_video, background, mixed)
            mux_audio_into_video(raw_video, mixed, final_video)
            try: mixed.unlink()
            except Exception: pass
        else:
            shutil.copy(raw_video, final_video)
        try: raw_video.unlink()
        except Exception: pass

        jobs_progress[key].update({"status": "done", "percent": 100,
                                   "message": "Lip-sync complete.",
                                   "result": {"video": "final_lipsync.mp4", "provider": provider}})
    except Exception as e:
        jobs_progress[key] = {"status": "error", "percent": 0, "message": str(e), "error": str(e),
                              "result": None, "generation_id": jobs_progress.get(key, {}).get("generation_id")}
    finally:
        if upload_path is not None:
            try: upload_path.unlink()
            except Exception: pass