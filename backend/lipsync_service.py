import json
import shutil
import subprocess
import time
import urllib.request
import urllib.error
import uuid
from pathlib import Path

import urllib3

import r2_backup
from config import OUTPUT_DIR
from app_state import jobs_progress
from ffmpeg_utils import (
    compress_video_for_upload,
    mix_two_audio,
    mux_audio_into_video,
)
from media_paths import find_job_video, job_background_audio, job_reference_images

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
    progress["message"] = f"Uploading ({upload_path.stat().st_size // 1024 // 1024}MB)..."
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
            raise Exception("Lip-sync returned 404 on all known endpoints: this provider isn't enabled for this account/plan. Use another provider.")
        raise Exception(f"Lip-sync create HTTP {resp_status}: {resp_text[:500]}")

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
        progress["message"] = f"Lip-sync: {status}"
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
            raise Exception("Video > 20MB (provider limit). Use a shorter clip.")
        fields = {
            "model": model or "lipsync-2",
            "video": ("source.mp4", upload_path.read_bytes(), "video/mp4"),
            "audio": ("dub.mp3", audio_path.read_bytes(), "audio/mpeg"),
        }
        progress["message"] = "Uploading..."
        auth_header = {"x-api-key": sync_key}
        resp = http.request("POST", "https://api.sync.so/v2/generate", headers=auth_header, fields=fields, encode_multipart=True)
        if resp.status == 403:
            auth_header = {"Authorization": f"Bearer {sync_key}"}
            resp = http.request("POST", "https://api.sync.so/v2/generate", headers=auth_header, fields=fields, encode_multipart=True)
        if resp.status == 403:
            raise Exception("Lip-sync provider 403 on create: free-tier quota exhausted or key problem. Check the provider's billing.")
        if resp.status not in (200, 201, 202):
            raise Exception(f"Lip-sync provider HTTP {resp.status}: {resp.data.decode(errors='ignore')[:800]}")
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
                progress["message"] = f"Lip-sync: 403 (rate limit?), backing off 60s... (ID: {gen_id[:8]}...)"
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
                    f"Lip-sync provider error while polling generation {gen_id}. "
                    f"The ID was saved to outputs/lipsync_billed_{job_id}.txt."
                )
            continue

        status = (st.get("status") or "").upper()
        prog = st.get("progress_percent")
        if prog is not None:
            try: progress["percent"] = min(90, 20 + int(float(prog) * 0.7))
            except Exception: pass
        progress["message"] = f"Lip-sync: {status} (ID: {gen_id[:8]}...)"
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


# VEED Lip Sync 2.0, hosted on fal.ai -- full-frame video-to-video dubbing,
# billed per second of output video (see lipsyncCreditsPerSec in the admin
# pricing panel for what that costs the user). Uses fal's own Python client
# (fal-client, in requirements.txt) rather than hand-rolled HTTP: it handles
# auth, the file upload step, and the submit/poll/result queue flow, and was
# confirmed to install cleanly alongside this project's other pinned
# dependencies before adding it.
FAL_VEED_LIPSYNC_MODEL = "veed/lipsync/v2"


def _veed_lipsync(upload_path: Path, audio_path: Path, fal_key: str, raw_video: Path, progress: dict):
    import fal_client

    client = fal_client.SyncClient(key=fal_key)

    progress["message"] = "Uploading..."
    video_url = client.upload_file(str(upload_path))
    audio_url = client.upload_file(str(audio_path))
    progress["percent"] = 15

    progress["message"] = "Submitting to lip-sync engine..."
    handle = client.submit(
        FAL_VEED_LIPSYNC_MODEL,
        arguments={"video_url": video_url, "audio_url": audio_url},
    )
    progress["generation_id"] = handle.request_id
    progress["percent"] = 20

    completed = None
    for _ in range(240):  # up to ~20 minutes at 5s intervals
        time.sleep(5)
        status = handle.status()
        if isinstance(status, fal_client.Queued):
            progress["message"] = f"Lip-sync: queued (position {status.position})"
        elif isinstance(status, fal_client.InProgress):
            progress["message"] = "Lip-sync: processing..."
            progress["percent"] = min(85, progress["percent"] + 2)
        elif isinstance(status, fal_client.Completed):
            if status.error:
                raise Exception(f"Lip-sync failed: {status.error}")
            completed = status
            break
    if completed is None:
        raise Exception(f"Timed out (~20 min). Request ID: {handle.request_id}")

    progress["message"] = "Downloading result..."
    result = handle.get()
    video_obj = result.get("video") if isinstance(result, dict) else None
    video_url_out = video_obj.get("url") if isinstance(video_obj, dict) else None
    if not video_url_out:
        raise Exception(f"Unexpected response from lip-sync engine: {json.dumps(result)[:400]}")
    with urllib.request.urlopen(video_url_out, timeout=600) as resp:
        raw_video.write_bytes(resp.read())


# Alibaba Cloud Model Studio's Wan 3.0 video model, called in its
# reference_video + reference_audio dubbing mode -- confirmed working
# by hand (Ali tested several lip-sync providers; this is the one that
# held up). Model string, endpoint shape, and media/parameters schema are
# all from Alibaba's own docs:
# https://www.alibabacloud.com/help/en/model-studio/wan3-video-generation-guide
# Note this is a reference-guided GENERATION, not literal pixel-patching of
# just the mouth region -- so the prompt matters; it explicitly tells the
# model to keep everything except lip movement unchanged. Workspace-scoped
# endpoint (international/Singapore by default) -- needs DASHSCOPE_API_KEY
# plus DASHSCOPE_WORKSPACE_ID set on Railway.
WAN3_MODEL = "wan3.0-video"
# Verbatim -- Ali's own prompt, tested and confirmed working by hand. Do not
# edit without his sign-off.
WAN3_DUB_PROMPT = """IMPORTANT: THIS IS A LIP-SYNC TASK, NOT A SCENE REGENERATION TASK AND NOT A TRANSLATION TASK.
Use the provided reference video as the primary and authoritative visual reference.
Reproduce the video as identically as possible. Do NOT reinterpret or recreate the scene.

PRESERVE EXACTLY
The same two characters
Their exact facial appearances and identities
Their same clothing, colors, accessories, hair, beards, and physical characteristics
The same historical environment and background
The same positions and blocking
The same camera angle and camera position
The same framing and composition
The same camera movement
The same lighting, shadows, color, and visual style
The same gestures, body movements, facial expressions, eye movements, and acting
The same editing and cuts
The same overall visual appearance

Do not create a new version of the scene. Do not change the camera angle. Do not change the characters. Do not redesign anything.

AUDIO
The provided Arabic-language audio track is already finished — a complete, previously produced dub. Do NOT regenerate, resynthesize, retranslate, alter, or reinterpret this audio in any way.
Do NOT generate new dialogue or new voices.
Do NOT change the pitch, timbre, accent, pacing, or emotional delivery already present in the provided audio.
Use the provided Arabic audio track exactly as supplied, unchanged, as the target audio for this video.

ONLY CHANGE
Adjust the characters' lip and mouth movements so they visually match the timing and phonetics of the provided Arabic audio track.

ABSOLUTELY DO NOT
Change the characters
Change their faces
Change their clothes
Change the location
Change the camera
Change the shots
Change the acting
Change the lighting
Change the choreography
Add or remove actions
Add new characters
Change the audio in any way
Add music
Add subtitles
Add captions
Add text on screen
Add visual effects

The reference video is already the finished scene. Treat it as locked. The desired output is the SAME VIDEO with lips and mouth movements matching the provided Arabic audio instead of the original English audio."""


def _alibaba_wan3_lipsync(upload_path: Path, audio_path: Path, dashscope_key: str, workspace_id: str, region: str, raw_video: Path, progress: dict, job_id: str, ref_image_paths=None):
    if not workspace_id:
        raise Exception("Missing DASHSCOPE_WORKSPACE_ID (required for the Wan 3.0 lip-sync call).")
    base_url = f"https://{workspace_id}.{region}.maas.aliyuncs.com"

    # Wan 3.0 needs public HTTP(S) URLs for both references (no raw file
    # upload), so stage the already-compressed video and the dubbed audio
    # in R2 first via short-lived presigned URLs -- the bucket itself never
    # has to be made public. Cleaned up in `finally` either way.
    video_key = f"lipsync-tmp/{job_id}_{uuid.uuid4().hex[:8]}_video.mp4"
    audio_key = f"lipsync-tmp/{job_id}_{uuid.uuid4().hex[:8]}_audio.mp3"

    progress["message"] = "Staging files for lip-sync..."
    video_url = r2_backup.upload_temp_and_get_url(upload_path, video_key)
    if not video_url:
        raise Exception("Could not stage the video for the lip-sync provider (R2 storage not configured, or the upload failed).")
    audio_url = r2_backup.upload_temp_and_get_url(audio_path, audio_key)
    if not audio_url:
        r2_backup.delete_temp_object(video_key)
        raise Exception("Could not stage the audio for the lip-sync provider (R2 storage not configured, or the upload failed).")

    # Optional reference photos (Step 7's "reference photos" upload, see
    # /api/lipsync/reference-images in main.py) -- fed in as reference_image
    # entries to help Wan 3.0 keep the speaker's exact face/appearance,
    # reinforcing the "PRESERVE EXACTLY" identity instructions in the prompt
    # above. Best-effort: a photo that fails to stage is just skipped rather
    # than failing the whole job over a supplementary reference.
    image_keys = []
    image_media = []
    for img_path in (ref_image_paths or []):
        img_key = f"lipsync-tmp/{job_id}_{uuid.uuid4().hex[:8]}_ref{img_path.suffix.lower()}"
        img_url = r2_backup.upload_temp_and_get_url(img_path, img_key)
        if img_url:
            image_keys.append(img_key)
            image_media.append({"type": "reference_image", "url": img_url})

    try:
        progress["percent"] = 15
        progress["message"] = "Submitting to lip-sync engine..."
        body = json.dumps({
            "model": WAN3_MODEL,
            "input": {
                "prompt": WAN3_DUB_PROMPT,
                "media": image_media + [
                    {"type": "reference_video", "url": video_url},
                    {"type": "reference_audio", "url": audio_url},
                ],
            },
            "parameters": {
                "resolution": "720P",
                "ratio": "adaptive",
                # -1 = auto: preserves the reference video's own duration
                # instead of us having to compute/pass one.
                "duration": -1,
                "prompt_extend": True,
            },
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/api/v1/services/aigc/video-generation/video-synthesis",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {dashscope_key}",
                "X-DashScope-Async": "enable",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                created = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise Exception(f"Lip-sync create HTTP {e.code}: {e.read().decode(errors='ignore')[:500]}")

        task_id = (created.get("output") or {}).get("task_id")
        if not task_id:
            raise Exception(f"Create failed: {json.dumps(created)[:400]}")

        progress["generation_id"] = task_id
        progress["percent"] = 20

        video_url_out = None
        for _ in range(120):  # up to ~20 minutes at 10s intervals
            time.sleep(10)
            poll_req = urllib.request.Request(
                f"{base_url}/api/v1/tasks/{task_id}",
                headers={"Authorization": f"Bearer {dashscope_key}"},
            )
            try:
                with urllib.request.urlopen(poll_req, timeout=30) as r:
                    st = json.loads(r.read().decode())
            except Exception:
                continue
            output = st.get("output") or {}
            status = str(output.get("task_status") or "").upper()
            progress["message"] = f"Lip-sync: {status}"
            if status == "SUCCEEDED":
                video_url_out = output.get("video_url")
                break
            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                raise Exception(f"Job {status}: {json.dumps(output)[:400]}")
        if not video_url_out:
            raise Exception(f"Timed out (~20 min). Task ID: {task_id}")

        progress["message"] = "Downloading result..."
        with urllib.request.urlopen(video_url_out, timeout=600) as resp:
            raw_video.write_bytes(resp.read())
    finally:
        r2_backup.delete_temp_object(video_key)
        r2_backup.delete_temp_object(audio_key)
        for img_key in image_keys:
            r2_backup.delete_temp_object(img_key)


def lipsync_worker(job_id, provider, model, eleven_key, sync_key, fal_key="", dashscope_key="", dashscope_workspace="", dashscope_region="ap-southeast-1"):
    key = f"lipsync_{job_id}"
    upload_path = None
    try:
        jobs_progress[key] = {"status": "processing", "percent": 5, "message": "Preparing files...",
                              "error": None, "result": None, "generation_id": None}
        video_path = find_job_video(job_id)
        if video_path is None:
            raise Exception("Original video not found.")
        dubbed_audio = OUTPUT_DIR / f"{job_id}_final_dubbed.mp3"
        if not dubbed_audio.exists():
            raise Exception("Dubbed audio not found.")

        is_recover = provider == "synclabs" and model.startswith("recover:")
        raw_video = OUTPUT_DIR / f"lipsync_raw_{job_id}.mp4"

        if not is_recover:
            jobs_progress[key]["message"] = "Compressing video for upload..."
            upload_path = OUTPUT_DIR / f"lipsync_upload_{job_id}.mp4"
            compress_video_for_upload(video_path, upload_path)
            if provider == "synclabs":
                if not sync_key: raise Exception("Missing lip-sync engine key.")
                _synclabs_lipsync(upload_path, dubbed_audio, sync_key, model, raw_video, jobs_progress[key], job_id)
            elif provider == "veed":
                if not fal_key: raise Exception("Missing lip-sync engine key.")
                _veed_lipsync(upload_path, dubbed_audio, fal_key, raw_video, jobs_progress[key])
            elif provider == "wan3":
                if not dashscope_key: raise Exception("Missing lip-sync engine key.")
                ref_images = job_reference_images(job_id)
                _alibaba_wan3_lipsync(upload_path, dubbed_audio, dashscope_key, dashscope_workspace, dashscope_region, raw_video, jobs_progress[key], job_id, ref_images)
            else:
                if not eleven_key: raise Exception("Missing lip-sync engine key.")
                _elevenlabs_lipsync(upload_path, dubbed_audio, eleven_key, raw_video, jobs_progress[key])
        else:
            if not sync_key: raise Exception("Missing lip-sync engine key.")
            jobs_progress[key]["message"] = "Recovering existing generation..."
            _synclabs_lipsync(None, dubbed_audio, sync_key, model, raw_video, jobs_progress[key], job_id)

        jobs_progress[key]["percent"] = 92
        jobs_progress[key]["message"] = "Mixing background audio back in..."
        background = job_background_audio(job_id)
        final_video = OUTPUT_DIR / f"{job_id}_final_lipsync.mp4"
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
                                   "result": {"video": f"{job_id}_final_lipsync.mp4", "provider": provider}})
    except Exception as e:
        jobs_progress[key] = {"status": "error", "percent": 0, "message": str(e), "error": str(e),
                              "result": None, "generation_id": jobs_progress.get(key, {}).get("generation_id")}
    finally:
        if upload_path is not None:
            try: upload_path.unlink()
            except Exception: pass