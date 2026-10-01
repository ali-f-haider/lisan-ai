import json
import os
import random
import shutil
import subprocess
import time
import urllib.request
import urllib.error
import urllib.parse
import uuid
from pathlib import Path

import urllib3

import r2_backup
from config import OUTPUT_DIR, LIPSYNC_TEST_MODE, APP_VERSION
from app_state import jobs_progress
from user_errors import friendly_error as _friendly_error, UserError, GENERIC as _GENERIC
from ffmpeg_utils import (
    compress_video_for_upload,
    get_video_resolution,
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
    progress["message"] = "Getting your video ready..."
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
    progress["message"] = "Getting your video ready..."
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

    progress["message"] = "Starting lip-sync..."
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

    progress["message"] = "Getting your video ready..."
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
# Ali's own prompt, tested by hand outside Lisan on 2026-09-28 (v1.43.1):
# it uses Alibaba's documented edit syntax ("Video 1", "Audio 1", "Lip sync",
# "Voice timbre references Audio 1") and never mentions English. The previous
# caps-heavy version named "English" six times and English words still bled
# into the Arabic voice; this one did not. Do not edit without his sign-off.
# 2026-10-01 (Ali's sign-off): added a paragraph so that Wan leaves a character
# alone whose lips are not visible. Wan did not obey the first, long version
# ("do not turn them, do not move the camera ..."), so on 2026-10-01 Ali replaced
# it with the short version below: lip-sync only where the lips are originally
# visible, and say plainly that nothing is needed where a face is turned, bent
# down or covered. It still never mentions English.
# Media numbering is per type, so "Video 1"/"Audio 1" stay correct even when
# reference_image entries are prepended.
WAN3_DUB_PROMPT = """Edit the video: in Video 1, replace the characters' spoken dialogue with the speech in Audio 1, and adjust only their lip and mouth movements to match Audio 1. Lip sync if the lips are originally visible. Voice timbre references Audio 1. Keep the rest of the frame unchanged.

Audio 1 is a finished Arabic dialogue recording. It is the complete and only speech in the output. Play it exactly as supplied, with the same voices, wording, pacing and delivery. All speech is Arabic. Video 1's own audio track is discarded and must not be heard.

Keep Video 1 exactly as is: the same characters, faces, clothing, setting, camera position, framing, camera movement, lighting, colors, gestures, expressions, acting and cuts.

Some faces of the characters are not shown as they turn or bend or their faces are covered, here no lip syncing required. In those moments Audio 1 is still heard exactly as supplied.

No other dialogue, no voiceover, no background music, no subtitles, no captions, no on-screen text."""


# Wan 3.0's "resolution" parameter is an explicit output-size override, not
# something the model infers from the reference video -- confirmed against
# Alibaba's own docs (the "ratio" parameter only controls aspect ratio/
# orientation via "adaptive", it has nothing to do with pixel dimensions).
# This code used to hardcode "720P" regardless of the source video's own
# resolution, so a source under 720p (common for older/compressed clips)
# came back from Wan 3.0 upscaled to 720p -- a genuinely higher resolution
# than the original, which is exactly what Ali noticed. Picking the closest
# of the model's 3 supported tiers (480P/720P/1080P) to the actual source
# keeps the output from being upscaled past what the source really has.
def _wan3_resolution_tier(video_path: Path) -> str:
    res = get_video_resolution(video_path)
    if res is None:
        return "720P"  # unknown source resolution: keep the previous default
    width, height = res
    long_edge = max(width, height)
    if long_edge < 960:      # below halfway between 480p's 854 and 720p's 1280
        return "480P"
    if long_edge < 1600:     # below halfway between 720p's 1280 and 1080p's 1920
        return "720P"
    return "1080P"


# Wan 3.0's task response never returns the seed it used (confirmed against
# Alibaba's API reference), and a run with no seed just gets a random one we
# can't recover. So Lisan picks the seed itself, sends it, and records it
# here (Supabase table `lipsync_runs`, created with v1.44.0; status columns
# added in v1.44.1) so a
# good or bad result can be re-run with the exact same seed later.
# Best-effort by design: a failed insert (table missing, Supabase down, env
# vars unset) is logged and swallowed and can never fail or delay the job.
def _record_lipsync_run(job_id, task_id, seed, resolution, ref_image_count):
    supabase_url = os.environ.get("SUPABASE_URL", "")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not service_key:
        return
    try:
        row = {
            "job_id": job_id,
            "task_id": task_id,
            "seed": seed,
            "model": WAN3_MODEL,
            "app_version": APP_VERSION,
            "resolution": resolution,
            "reference_images": ref_image_count,
        }
        req = urllib.request.Request(
            f"{supabase_url}/rest/v1/lipsync_runs",
            data=json.dumps(row).encode("utf-8"),
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}",
                     "Content-Type": "application/json", "Prefer": "return=minimal"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as e:
        print(f"[lipsync] could not record lipsync_runs row for job {job_id}: {e}")


# Second half of the run record: once Wan reports the outcome, mark the row
# (matched by Wan's task id) succeeded / failed / canceled / timed_out and
# keep the failure text. Same best-effort rule as above -- never raises.
def _update_lipsync_run(task_id, status, error=None):
    supabase_url = os.environ.get("SUPABASE_URL", "")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not service_key or not task_id:
        return
    try:
        patch = {"status": status, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if error:
            patch["error"] = str(error)[:500]
        req = urllib.request.Request(
            f"{supabase_url}/rest/v1/lipsync_runs?task_id=eq.{urllib.parse.quote(str(task_id), safe='')}",
            data=json.dumps(patch).encode("utf-8"),
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}",
                     "Content-Type": "application/json", "Prefer": "return=minimal"},
            method="PATCH",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as e:
        print(f"[lipsync] could not update lipsync_runs status for task {task_id}: {e}")


def _alibaba_wan3_lipsync(upload_path: Path, audio_path: Path, dashscope_key: str, workspace_id: str, region: str, raw_video: Path, progress: dict, job_id: str, ref_image_paths=None, resolution=None):
    if not workspace_id:
        raise UserError(_LS_DOWN)
    base_url = f"https://{workspace_id}.{region}.maas.aliyuncs.com"

    # Wan 3.0 needs public HTTP(S) URLs for both references (no raw file
    # upload), so stage the already-compressed video and the dubbed audio
    # in R2 first via short-lived presigned URLs -- the bucket itself never
    # has to be made public. Cleaned up in `finally` either way.
    video_key = f"lipsync-tmp/{job_id}_{uuid.uuid4().hex[:8]}_video.mp4"
    audio_key = f"lipsync-tmp/{job_id}_{uuid.uuid4().hex[:8]}_audio.mp3"

    progress["message"] = "Preparing your files for lip-sync..."
    # Reverted 2026-09-28 (Ali): the 2026-09-27 change staged a MUTED copy of
    # the reference video here, on the theory that its original-language
    # audio track was leaking into the output alongside the Arabic dub.
    # Ali tested it and the result didn't work at all (worse than the
    # mixed-language result the mute was meant to fix), so back to staging
    # the video with its original audio intact. Instead trying: removing
    # "Change the audio in any way" from the prompt's ABSOLUTELY DO NOT list
    # below, on the theory that line may have been over-constraining the
    # model. See mute_video_copy in ffmpeg_utils.py if muting needs
    # revisiting later -- the function itself was left in place, just
    # unused here now.
    video_url = r2_backup.upload_temp_and_get_url(upload_path, video_key)
    if not video_url:
        print("[lipsync] could not stage the video in R2 (storage not configured, or the upload failed)")
        raise UserError(_LS_DOWN)
    audio_url = r2_backup.upload_temp_and_get_url(audio_path, audio_key)
    if not audio_url:
        r2_backup.delete_temp_object(video_key)
        print("[lipsync] could not stage the audio in R2 (storage not configured, or the upload failed)")
        raise UserError(_LS_DOWN)

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
        progress["message"] = "Starting lip-sync..."
        # Range documented by Alibaba: -1 or [0, 2147483647]. We always send
        # an explicit one (rather than -1/omitted) so it can be recorded.
        wan_seed = random.randint(0, 2147483647)
        # the user chooses the output resolution (480P / 720P / 1080P); without a choice it follows the source
        wan_resolution = resolution if resolution in ("480P", "720P", "1080P") else _wan3_resolution_tier(upload_path)
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
                "resolution": wan_resolution,
                "seed": wan_seed,
                "ratio": "adaptive",
                # -1 = auto: preserves the reference video's own duration
                # instead of us having to compute/pass one.
                "duration": -1,
                # False as of 2026-09-28 (was True): per Alibaba's own docs,
                # prompt_extend has an internal model "intelligently rewrite"
                # our prompt before generation -- the one documented place an
                # extra language model acts on this request at all. Ali saw
                # Wan 3.0 apparently judging his Arabic translation against
                # the original English and inserting back an English word it
                # thought was missing; turning this off removes that
                # rewrite step as a possible source of that behavior. The English
                # bleed was finally fixed in v1.43.1 by the rewritten
                # WAN3_DUB_PROMPT (documented edit syntax, no mention of
                # English), not by this parameter.
                "prompt_extend": False,
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
        progress["seed"] = wan_seed
        progress["resolution"] = wan_resolution
        print(f"[lipsync] job {job_id} wan task {task_id} seed={wan_seed} resolution={wan_resolution}")
        _record_lipsync_run(job_id, task_id, wan_seed, wan_resolution, len(image_media))

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
            if status == "RUNNING":
                # Ali tested this himself: RUNNING alone can sit for ~15 minutes
                # depending on model load, and with no extra text it looks frozen.
                progress["message"] = "Lip-sync is in progress. This can take up to 15 minutes, so please keep this page open."
            else:
                progress["message"] = "Lip-sync is waiting to start..." if status == "PENDING" else "Lip-sync is in progress..."
            if status == "SUCCEEDED":
                video_url_out = output.get("video_url")
                _update_lipsync_run(task_id, "succeeded")
                break
            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                _update_lipsync_run(task_id, status.lower(), json.dumps(output))
                raise Exception(f"Job {status}: {json.dumps(output)[:400]}")
        if not video_url_out:
            _update_lipsync_run(task_id, "timed_out", "No result after ~20 minutes")
            raise Exception(f"Timed out (~20 min). Task ID: {task_id}")

        progress["message"] = "Getting your video ready..."
        with urllib.request.urlopen(video_url_out, timeout=600) as resp:
            raw_video.write_bytes(resp.read())
    finally:
        r2_backup.delete_temp_object(video_key)
        r2_backup.delete_temp_object(audio_key)
        for img_key in image_keys:
            r2_backup.delete_temp_object(img_key)


# UI/UX test double for the real provider calls above -- see
# LIPSYNC_TEST_MODE in config.py. Runs through the same progress
# percentages/messages a real Wan 3.0 call goes through, but in ~15 seconds
# instead of ~15 minutes and with no real API call or charge. Copies the
# original video through unchanged as the "result" so the results
# player/download UI can be checked too -- just not real lip-sync output.
_LS_DOWN = "Lip-sync is temporarily unavailable. Please try again later."


def _ls_friendly(e) -> str:
    """Customer-safe lip-sync failure text. The raw error and a short reference go to the log."""
    msg = _friendly_error(e, "lipsync")
    if msg.startswith(_GENERIC):
        return msg.replace(_GENERIC, "We couldn't finish the lip-sync this time. Please try again in a moment.", 1)
    return msg


def _simulate_lipsync(source_video: Path, raw_video: Path, progress: dict):
    steps = [
        (15, "Preparing your files for lip-sync...", 2),
        (20, "Starting lip-sync...", 2),
        (35, "Lip-sync is waiting to start...", 2),
        (55, "Lip-sync is in progress. This can take up to 15 minutes, so please keep this page open.", 3),
        (75, "Lip-sync is in progress. This can take up to 15 minutes, so please keep this page open.", 3),
        (90, "Getting your video ready...", 2),
    ]
    for percent, message, delay in steps:
        progress["percent"] = percent
        progress["message"] = message
        time.sleep(delay)
    shutil.copy(source_video, raw_video)


def lipsync_worker(job_id, provider, model, eleven_key, sync_key, fal_key="", dashscope_key="", dashscope_workspace="", dashscope_region="ap-southeast-1", resolution=None):
    key = f"lipsync_{job_id}"
    upload_path = None
    try:
        jobs_progress[key] = {"status": "processing", "percent": 5, "message": "Preparing files...",
                              "error": None, "result": None, "generation_id": None}
        video_path = find_job_video(job_id)
        if video_path is None:
            raise UserError("We couldn't find your original video. Please upload it again.")
        dubbed_audio = OUTPUT_DIR / f"{job_id}_final_dubbed.mp3"
        if not dubbed_audio.exists():
            raise UserError("We couldn't find your dubbed audio. Please generate the dubbing again.")

        is_recover = provider == "synclabs" and model.startswith("recover:")
        raw_video = OUTPUT_DIR / f"lipsync_raw_{job_id}.mp4"

        if LIPSYNC_TEST_MODE:
            _simulate_lipsync(video_path, raw_video, jobs_progress[key])
        elif not is_recover:
            jobs_progress[key]["message"] = "Preparing your video..."
            upload_path = OUTPUT_DIR / f"lipsync_upload_{job_id}.mp4"
            compress_video_for_upload(video_path, upload_path)
            if provider == "synclabs":
                if not sync_key: raise UserError(_LS_DOWN)
                _synclabs_lipsync(upload_path, dubbed_audio, sync_key, model, raw_video, jobs_progress[key], job_id)
            elif provider == "veed":
                if not fal_key: raise UserError(_LS_DOWN)
                _veed_lipsync(upload_path, dubbed_audio, fal_key, raw_video, jobs_progress[key])
            elif provider == "wan3":
                if not dashscope_key: raise UserError(_LS_DOWN)
                ref_images = job_reference_images(job_id)
                _alibaba_wan3_lipsync(upload_path, dubbed_audio, dashscope_key, dashscope_workspace, dashscope_region, raw_video, jobs_progress[key], job_id, ref_images, resolution)
            else:
                if not eleven_key: raise UserError(_LS_DOWN)
                _elevenlabs_lipsync(upload_path, dubbed_audio, eleven_key, raw_video, jobs_progress[key])
        else:
            if not sync_key: raise UserError(_LS_DOWN)
            jobs_progress[key]["message"] = "Retrieving your lip-sync video..."
            _synclabs_lipsync(None, dubbed_audio, sync_key, model, raw_video, jobs_progress[key], job_id)

        jobs_progress[key]["percent"] = 92
        jobs_progress[key]["message"] = "Adding the background sound back..."
        background = job_background_audio(job_id)
        final_video = OUTPUT_DIR / f"{job_id}_final_lipsync.mp4"
        if background is not None:
            mixed = OUTPUT_DIR / f"lipsync_mixed_{job_id}.wav"
            bg_used = background
            _bp_temps = []
            _rx_path = None
            try:
                import bg_duck, zlib
                from config import UPLOAD_DIR
                _bp = bg_duck.prepare_background(background, background.parent / "vocals.wav", UPLOAD_DIR / f"{job_id}_audio.wav",
                                                 OUTPUT_DIR, f"lipsync_{job_id}", seed=zlib.crc32(str(job_id).encode("utf-8")))
                print(f"[bg-duck] {job_id}: lip-sync {_bp['note']}")
                bg_used = _bp["path"]
                _bp_temps = list(_bp["temps"])
                # laughter / applause / cheers (see bg_duck.prepare_reactions)
                _rx = bg_duck.prepare_reactions(background.parent / "vocals.wav", background.parent / "speech_spans.json",
                                                dubbed_audio, OUTPUT_DIR, f"lipsync_{job_id}", bed_level=_bp.get("bed_level"))
                print(f"[bg-duck] {job_id}: lip-sync {_rx['note']}")
                _rx_path = _rx["path"]
                _bp_temps += list(_rx["temps"])
            except Exception as _bd_ex:
                print(f"[bg-duck] {job_id}: lip-sync skipped ({_bd_ex})")
            # The voice is OUR finished dubbed audio, never the sound that came back inside the
            # provider's video (the provider re-renders the voice and it can pick up artifacts).
            mix_two_audio(dubbed_audio, bg_used, mixed, extra_audio=_rx_path)
            for _t in _bp_temps:
                try: _t.unlink()
                except Exception: pass
            mux_audio_into_video(raw_video, mixed, final_video)
            try: mixed.unlink()
            except Exception: pass
        else:
            # no background sound: the picture from the provider + our own dubbed audio (its sound is dropped)
            mux_audio_into_video(raw_video, dubbed_audio, final_video)
        try: raw_video.unlink()
        except Exception: pass

        jobs_progress[key].update({"status": "done", "percent": 100,
                                   "message": "Lip-sync complete.",
                                   "result": {"video": f"{job_id}_final_lipsync.mp4"}})
    except Exception as e:
        _msg = _ls_friendly(e)
        jobs_progress[key] = {"status": "error", "percent": 0, "message": _msg, "error": _msg,
                              "result": None, "generation_id": jobs_progress.get(key, {}).get("generation_id")}
    finally:
        if upload_path is not None:
            try: upload_path.unlink()
            except Exception: pass