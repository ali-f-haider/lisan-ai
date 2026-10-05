import json
import base64
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
import urllib3
from elevenlabs.client import ElevenLabs
import inworld_service
from config import OUTPUT_DIR
from shortdub_paths import line_audio_path, clear_line_audio
from app_state import jobs_progress, usage_bucket
from resource_meter import metered as _metered      # measures what each job costs on Railway (see resource_meter.py)
from ffmpeg_utils import (
    get_media_duration,
    run_ffmpeg,
    cut_audio_segment,
    concat_audio_files,
    measure_loudness_db,
    measure_speech_loudness_db,
)
from media_paths import resolve_job_audio, resolve_job_speech, job_speech_spans
from user_errors import friendly_error as _friendly_error, UserError

def _orig_level_db(src, spans, start, duration):
    """Loudness of the original speaker in a line's time window: only the moments with spoken words count (laughter,
    applause and music in the window do not); falls back to the whole window when the speech map is not available."""
    v = None
    if spans:
        v = measure_speech_loudness_db(str(src), start, duration, spans)
    return v if v is not None else measure_loudness_db(str(src), start, duration)


eleven_client = None
# ElevenLabs TTS model used for every real generation call below (Arabic
# dubbing output + the admin "Compare Voice Providers" sample). Upgraded
# from eleven_v3 to eleven_v4 on 2026-09-28 (Ali's request, day of the v4
# launch) -- ElevenLabs' own docs describe v4 as strictly better across
# quality/accuracy/consistency/emotion/delivery/audio-tags/language
# coverage than v3, same audio-tag syntax (still plain [bracketed] text
# prepended to the line, nothing in this file's tag-building code needs to
# change), and PVCs/IVCs both fully supported. One named constant instead
# of the model id hardcoded separately at each call site, specifically so
# the next model upgrade is a one-line change instead of a grep-and-hope
# across the file.
TTS_MODEL_ID = "eleven_v4"
USER_GAINS = {}
ROOM_LAST = {}       # job_id -> what the room step did the last time the final MP3 was built
ROOM_SETTINGS = {}   # job_id -> {"mode": "auto"|"off"|"manual", "rt60": s, "wet_db": dB, "trim_db": dB (Auto only: louder/quieter than measured)}  (Step 5.5 "Room sound")

def _apply_room(job_id, output_file, items=None, segments=None):
    """Gives every line of the finished (dry) Arabic mix the room it was spoken in, measured phrase by phrase from the
    ORIGINAL recording (see room_acoustics.py: rooms are detected, grouped, and each group gets its own reverb).
    items = the lines that were mixed ({"sid", "start", "end", "allowed_duration", optional "orig_mid"}).
    segments = the job's lines with their English text (for the scene check: what kind of place is each stretch in, see
    scene_context.py; it only ever limits the measured reverb).
    The dry mix is kept as <job>_final_dry.mp3 so changing the setting never stacks one room on top of another.
    Never raises; on any problem the dry mix stays as it is.  Returns a small dict for the response."""
    try:
        import room_acoustics
        import shutil
        output_file = Path(output_file)
        dry = OUTPUT_DIR / f"{job_id}_final_dry.mp3"
        shutil.copyfile(output_file, dry)
        lines = []
        for it in (items or []):
            t0 = float(it["start"])
            t1 = t0 + float(it.get("allowed_duration") or it.get("duration") or 0.0) + 0.1
            mid = it.get("orig_mid")
            if mid is None:
                mid = (float(it["start"]) + float(it.get("end", it["start"]))) / 2.0
            lines.append({"sid": it.get("sid", ""), "t0": t0, "t1": t1, "orig_mid": float(mid)})
        settings = dict(ROOM_SETTINGS.get(job_id) or {"mode": "auto"})
        scene_info = None
        if str(settings.get("mode") or "auto").lower() == "auto":
            try:
                import scene_context
                if scene_context.ENABLED and segments:
                    from config import GEMINI_API_KEY
                    from media_paths import find_job_video
                    rows = [{"start": float(s.start), "end": float(s.end), "text": s.text} for s in segments if (getattr(s, "text", "") or "").strip()]
                    scene_info = scene_context.scenes_for_job(job_id, OUTPUT_DIR, rows, GEMINI_API_KEY, video_path=find_job_video(job_id))
                    if scene_info is not None:
                        settings["scenes"] = scene_info["scenes"]
            except Exception as ex_sc:
                print(f"[room] {job_id}: scene check skipped ({ex_sc})")
        res = room_acoustics.apply_to_file(job_id, dry, output_file, settings, OUTPUT_DIR, lines=lines)
        print(f"[room] {job_id}: {res.get('why')}" + (f" | scene: {scene_info['summary']}" if scene_info else ""))
        out = {"applied": bool(res.get("applied")), "why": res.get("why", ""), "profile": res.get("profile"),
               "assignment": res.get("assignment") or {}}
        if scene_info is not None:
            out["scene"] = str(scene_info.get("summary") or "")[:400]
            out["scene_cost_usd"] = float(scene_info.get("cost_usd") or 0.0)
        ROOM_LAST[job_id] = {"applied": out["applied"], "why": out["why"], "assignment": out["assignment"]}
        return out
    except Exception as ex:
        print(f"[room] {job_id}: skipped ({ex})")
        return {"applied": False, "why": f"skipped ({ex})"[:160]}

def friendly_error(e):
    # Shown to the customer: the real error goes to the server log, the customer gets a plain sentence.
    return _friendly_error(e, "voice")

def _emotion_tags(emotion) -> str:
    """Turn a (possibly multi-tag) 'happy, softly' emotion string into stacked
    ElevenLabs v3 audio tags '[happy][softly]' — combining tags is officially
    supported by stacking separate bracket groups, not commas inside one bracket."""
    parts = [p.strip() for p in str(emotion or "neutral").split(",") if p.strip()]
    if not parts:
        parts = ["neutral"]
    return "".join(f"[{p}]" for p in parts)

def _bake_gain(path: Path, gain_db: float) -> Path:
    """Apply a volume gain to a line file (returns final path, always .wav)."""
    if abs(gain_db) < 0.15:
        return path
    out = path.with_suffix(".wav")
    tmp = path.with_name(path.name + ".gaintmp.wav")
    run_ffmpeg([
        "ffmpeg", "-y", "-i", str(path),
        "-af", f"volume={gain_db:.2f}dB",
        "-ar", "44100", "-ac", "1", "-acodec", "pcm_s16le",
        str(tmp),
    ])
    try:
        if out != path and path.exists():
            path.unlink()
        tmp.replace(out)
        return out
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        return path

def fetch_voices(api_key: str) -> dict:
    try:
        request = urllib.request.Request("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": api_key})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        voices = []
        for voice in data.get("voices", []):
            labels = voice.get("labels") or {}
            voices.append({"voice_id": voice.get("voice_id"), "name": voice.get("name", "Unnamed"),
                           "gender": labels.get("gender", ""), "category": voice.get("category", ""),
                           "preview_url": voice.get("preview_url", ""),
                           "accent": labels.get("accent", ""), "age": labels.get("age", ""),
                           "use_case": labels.get("use_case", "")})
        return {"voices": voices}
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode(errors="ignore")
        except Exception:
            error_body = str(e)
        print(f"[voices] list failed: HTTP {e.code}: {str(error_body)[:300]}")
        return {"error": "We couldn't load the voice list. Please try again in a moment."}
    except Exception as e:
        print(f"[voices] list failed: {e}")
        return {"error": "We couldn't load the voice list. Please try again in a moment."}


def get_subscription_usage(api_key: str) -> dict:
    """Real character-quota, cloned-voice-slot, AND voice-cloning-credit
    usage for this ElevenLabs account, via GET /v1/user/subscription.
    Returns character_count, character_limit, tier,
    next_character_count_reset_unix, voice_slots_used, voice_limit,
    voice_add_edit_counter, and max_voice_add_edits -- or {"error": ...}
    if the call fails for any reason (bad key, network, unexpected
    response). Used by service_usage_monitor.py for the admin dashboard's
    usage monitoring, not by anything in the main dubbing pipeline.

    Three genuinely different numbers here, easy to conflate:
      - voice_slots_used / voice_limit: how many cloned voices are sitting
        in the account RIGHT NOW. Deleting a voice frees a slot -- this app
        deletes every cloned voice via cleanup_cloned_voices() as soon as a
        job is done with it, so this stays low even after heavy use.
      - voice_add_edit_counter / max_voice_add_edits: ElevenLabs' actual
        monthly quota for "create or edit a voice" operations. This does
        NOT free up when a voice is deleted -- it only resets on your
        billing cycle. This is the number that matches "I've used them all"
        even while voice_slots_used looks small, and it's what the admin
        dashboard's "Voice cloning credits" metric now shows.
      - character_count / character_limit: the completely separate TTS
        character budget, unrelated to either of the above."""
    try:
        request = urllib.request.Request(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": api_key},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
        return {
            "character_count": data.get("character_count"),
            "character_limit": data.get("character_limit"),
            "tier": data.get("tier"),
            "next_reset_unix": data.get("next_character_count_reset_unix"),
            "voice_slots_used": data.get("voice_slots_used"),
            "voice_limit": data.get("voice_limit"),
            "voice_add_edit_counter": data.get("voice_add_edit_counter"),
            "max_voice_add_edits": data.get("max_voice_add_edits"),
        }
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode(errors="ignore")
        except Exception:
            error_body = str(e)
        return {"error": f"ElevenLabs subscription check error {e.code}: {error_body}"}
    except Exception as e:
        return {"error": str(e)}


def search_voice_library(api_key: str, language=None, accent=None, gender=None, age=None,
                          category=None, high_quality=None, search=None,
                          voice_type="community", page_size=30, next_page_token=None) -> dict:
    """Search ElevenLabs' full Voice Library (GET /v2/voices) — separate from
    fetch_voices() above, which only lists voices already in your own account
    (GET /v1/voices). This searches ALL of ElevenLabs' shared/community voices,
    filterable by language, accent, gender, age, and category (pass
    category="professional" or high_quality=True for studio-grade voices only).
    Browsing/searching here never adds anything to your account and never
    touches your voice add/edit quota — only add_shared_voice() below does that.
    Pass next_page_token (from a previous call's response) to fetch the next
    page of results."""
    try:
        params = {"page_size": str(min(int(page_size or 30), 100))}
        if voice_type:
            params["voice_type"] = voice_type
        if category:
            params["category"] = category
        if gender:
            params["gender"] = gender
        if age:
            params["age"] = age
        if accent:
            params["accent"] = accent
        if search:
            params["search"] = search
        if high_quality:
            params["high_quality"] = "true"
        if next_page_token:
            params["next_page_token"] = next_page_token
        qs = urllib.parse.urlencode(params)
        if language:
            langs = language if isinstance(language, (list, tuple)) else [language]
            qs += "".join(f"&language={urllib.parse.quote(str(l))}" for l in langs if l)
        url = f"https://api.elevenlabs.io/v2/voices?{qs}"
        request = urllib.request.Request(url, headers={"xi-api-key": api_key})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        voices = []
        for voice in data.get("voices", []):
            labels = voice.get("labels") or {}
            sharing = voice.get("sharing") or {}
            voices.append({
                "voice_id": voice.get("voice_id"),
                "name": voice.get("name", "Unnamed"),
                "category": voice.get("category", ""),
                "recording_quality": voice.get("recording_quality", ""),
                "labels": {
                    "gender": labels.get("gender", ""),
                    "accent": labels.get("accent", ""),
                    "language": labels.get("language", ""),
                    "age": labels.get("age", ""),
                    "use_case": labels.get("use_case", ""),
                },
                "preview_url": voice.get("preview_url", ""),
                "public_owner_id": sharing.get("public_owner_id", ""),
                "is_owner": voice.get("is_owner", False),
            })
        return {"voices": voices, "has_more": data.get("has_more", False), "next_page_token": data.get("next_page_token")}
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode(errors="ignore")
        except Exception:
            error_body = str(e)
        print(f"[voices] list failed: HTTP {e.code}: {str(error_body)[:300]}")
        return {"error": "We couldn't load the voice list. Please try again in a moment."}
    except Exception as e:
        print(f"[voices] list failed: {e}")
        return {"error": "We couldn't load the voice list. Please try again in a moment."}


def add_shared_voice(api_key: str, public_owner_id: str, voice_id: str, new_name: str) -> dict:
    """Import one Voice Library voice found via search_voice_library() into your
    own ElevenLabs account (POST /v1/voices/add/{public_owner_id}/{voice_id}).
    This is a ONE-TIME action per voice you choose to keep — after this succeeds,
    the voice behaves exactly like any other account voice and shows up via
    fetch_voices() / your app's normal Step 4 voice pools from then on, with no
    need to add it again. NOTE: ElevenLabs' own docs do not state whether this
    counts against your monthly voice add/edit quota (the same 290/month limit
    cloning uses) — check your ElevenLabs subscription page's counter before and
    after your first real add here to confirm."""
    try:
        if not public_owner_id or not voice_id:
            return {"error": "This voice can't be added right now. Please choose a different one."}
        url = f"https://api.elevenlabs.io/v1/voices/add/{public_owner_id}/{voice_id}"
        body = json.dumps({"new_name": new_name or "Voice"}).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST",
                                          headers={"xi-api-key": api_key, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        return {"voice_id": data.get("voice_id"), "status": "success"}
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode(errors="ignore")
        except Exception:
            error_body = str(e)
        print(f"[voices] list failed: HTTP {e.code}: {str(error_body)[:300]}")
        return {"error": "We couldn't load the voice list. Please try again in a moment."}
    except Exception as e:
        print(f"[voices] list failed: {e}")
        return {"error": "We couldn't load the voice list. Please try again in a moment."}


def record_gemini(job_id, data):
    if not isinstance(data, dict):
        return
    u = data.get("usageMetadata") or {}
    b = usage_bucket(job_id)
    b["gemini_in"] += int(u.get("promptTokenCount", 0) or 0)
    b["gemini_out"] += int(u.get("candidatesTokenCount", 0) or 0)
    b["gemini_thoughts"] += int(u.get("thoughtsTokenCount", 0) or 0)

@_metered("shortdub_clone", lambda job_id, *a, **k: job_id)
def clone_voices(job_id: str, segments: list, api_key: str, speakers_to_clone: list = None) -> dict:
    audio_path = resolve_job_audio(job_id)
    if audio_path is None:
        return {"error": "We can't find this project's audio any more (projects are kept for a limited time). Please upload your video again."}
    source_duration = get_media_duration(audio_path)
    if source_duration <= 0:
        print(f"[voice-clone] could not read the duration of {audio_path}")
        return {"error": "We couldn't read the audio of this project. Please upload your video again."}
    cloned_voices = {}
    speakers = list(set(s.speaker for s in segments if (s.text or "").strip()))
    if speakers_to_clone:
        speakers = [s for s in speakers if s in speakers_to_clone]
    for speaker in speakers:
        cut_files = []
        concat_file = None
        try:
            safe_speaker = "".join(c for c in speaker if c.isalnum()).strip() or "speaker"
            speaker_segs = [
                s for s in segments
                if s.speaker == speaker and (s.text or "").strip() and (s.end - s.start) > 0.05
            ]
            if not speaker_segs:
                cloned_voices[speaker] = f"ERROR: {speaker} doesn't have enough clear speech to copy a voice. Choose a studio voice for this speaker in Step 4."
                continue
            speaker_segs.sort(key=lambda s: s.end - s.start, reverse=True)
            total_valid_duration = 0.0
            for seg in speaker_segs:
                if total_valid_duration >= 20.0:
                    break
                margin = 0.35
                start_time = max(0.0, float(seg.start) - margin)
                end_time = min(source_duration, float(seg.end) + margin)
                dur = end_time - start_time
                if dur < 0.2:
                    continue
                cut_file = OUTPUT_DIR / f"clone_{job_id}_{safe_speaker}_{seg.segment_id}.wav"
                try:
                    run_ffmpeg([
                        "ffmpeg", "-y",
                        "-ss", str(start_time),
                        "-i", str(audio_path),
                        "-t", str(dur),
                        "-vn", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le",
                        str(cut_file)
                    ])
                    if not cut_file.exists() or cut_file.stat().st_size < 2000:
                        if cut_file.exists():
                            cut_file.unlink()
                        continue
                    actual_dur = get_media_duration(cut_file)
                    if actual_dur >= 0.5:
                        cut_files.append(cut_file)
                        total_valid_duration += actual_dur
                    else:
                        if cut_file.exists():
                            cut_file.unlink()
                except Exception as cut_err:
                    print(f"Warning: failed to cut clone sample for {speaker}: {cut_err}")
                    if cut_file.exists():
                        cut_file.unlink()
                    continue
            if total_valid_duration < 1.0:
                try:
                    min_start = max(0.0, min(float(s.start) for s in speaker_segs) - 0.5)
                    max_end = min(source_duration, max(float(s.end) for s in speaker_segs) + 0.5)
                    fallback_dur = max_end - min_start
                    if fallback_dur >= 1.0:
                        fallback_file = OUTPUT_DIR / f"clone_{job_id}_{safe_speaker}_fallback.wav"
                        run_ffmpeg([
                            "ffmpeg", "-y",
                            "-ss", str(min_start), "-i", str(audio_path), "-t", str(fallback_dur),
                            "-vn", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le",
                            str(fallback_file)
                        ])
                        if fallback_file.exists() and fallback_file.stat().st_size > 2000:
                            fallback_actual = get_media_duration(fallback_file)
                            if fallback_actual >= 1.0:
                                for cf in cut_files:
                                    if cf.exists():
                                        cf.unlink()
                                cut_files = [fallback_file]
                                total_valid_duration = fallback_actual
                            else:
                                if fallback_file.exists():
                                    fallback_file.unlink()
                except Exception as fallback_err:
                    print(f"Warning: fallback clone cut failed for {speaker}: {fallback_err}")
            if total_valid_duration < 1.0 or not cut_files:
                cloned_voices[speaker] = f"ERROR: {speaker} doesn't have enough clear speech to copy a voice. Choose a studio voice for this speaker in Step 4."
                for cf in cut_files:
                    if cf.exists():
                        cf.unlink()
                continue
            concat_file = OUTPUT_DIR / f"clone_{job_id}_{safe_speaker}_final.wav"
            if len(cut_files) == 1:
                run_ffmpeg(["ffmpeg", "-y", "-i", str(cut_files[0]), "-vn", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le", str(concat_file)])
            else:
                inputs, labels = [], []
                for i, cf in enumerate(cut_files):
                    inputs.extend(["-i", str(cf)])
                    labels.append(f"[{i}:a]")
                filter_complex = "".join(labels) + f"concat=n={len(cut_files)}:v=0:a=1[out]"
                run_ffmpeg(["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex, "-map", "[out]", "-vn", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le", str(concat_file)])
            if not concat_file.exists() or concat_file.stat().st_size < 2000:
                cloned_voices[speaker] = f"ERROR: {speaker} doesn't have enough clear speech to copy a voice. Choose a studio voice for this speaker in Step 4."
                continue
            final_duration = get_media_duration(concat_file)
            if final_duration < 1.0:
                padded_file = OUTPUT_DIR / f"clone_{job_id}_{safe_speaker}_final_padded.wav"
                pad_needed = max(0.2, 1.15 - final_duration)
                run_ffmpeg(["ffmpeg", "-y", "-i", str(concat_file), "-af", f"apad=pad_dur={pad_needed}", "-vn", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le", str(padded_file)])
                if concat_file.exists():
                    concat_file.unlink()
                concat_file = padded_file
                final_duration = get_media_duration(concat_file)
            if final_duration < 1.0:
                cloned_voices[speaker] = f"ERROR: {speaker} doesn't have enough clear speech to copy a voice. Choose a studio voice for this speaker in Step 4."
                continue
            with open(concat_file, "rb") as f:
                file_data = f.read()
            http = urllib3.PoolManager()
            response = http.request(
                "POST", "https://api.elevenlabs.io/v1/voices/add",
                headers={"xi-api-key": api_key},
                fields={"name": f"Cloned_{speaker}", "files": (f"{safe_speaker}.wav", file_data, "audio/wav"), "labels": "{}"}
            )
            if response.status == 200:
                data = json.loads(response.data.decode())
                cloned_voices[speaker] = data["voice_id"]
                # Keep the reference sample so the user can download it this
                # session. ElevenLabs does not allow exporting the cloned voice
                # model itself (confirmed via their own docs) — this is the
                # isolated voice sample assembled from the user's own video
                # that was used to CREATE the clone, which is the closest real,
                # downloadable asset, and matches what ElevenLabs' own help
                # center recommends keeping for any future re-cloning. It gets
                # deleted by cleanup_voices below (called on session/project
                # end), same as the ElevenLabs-side voice itself.
                try:
                    sample_path = OUTPUT_DIR / f"voice_sample_{job_id}_{safe_speaker}.wav"
                    if concat_file != sample_path:
                        concat_file.replace(sample_path)
                    concat_file = None  # prevent the cleanup below from deleting it
                except Exception as _sample_err:
                    # Was a silent `pass` -- meaning a failure here left NO
                    # trace anywhere, and the finally block below still went on
                    # to delete concat_file (since it's not None on this path),
                    # so the user's clone succeeded but the downloadable sample
                    # silently vanished with nothing in the logs to explain why
                    # (caught 2026-09-28: Ali got a real "voice sample no longer
                    # available" 404 on the Inworld side of this same pattern,
                    # see inworld_service.clone_voices). Logging it doesn't fix
                    # the underlying cause (unknown yet), but it's the
                    # difference between being able to diagnose the next
                    # occurrence from Railway's logs versus not.
                    print(f"[voice-clone] WARNING: could not save downloadable reference sample for {speaker} (job {job_id}): {_sample_err}")
            else:
                raise Exception(f"API error {response.status}: {response.data.decode(errors='ignore')}")
        except Exception as e:
            cloned_voices[speaker] = f"ERROR: {friendly_error(e)}"
        finally:
            for cf in cut_files:
                if cf.exists():
                    try:
                        cf.unlink()
                    except Exception:
                        pass
            if concat_file is not None and concat_file.exists():
                try:
                    concat_file.unlink()
                except Exception:
                    pass
    warnings = []
    for speaker in cloned_voices:
        if not str(cloned_voices[speaker]).startswith("ERROR"):
            speaker_segs = [s for s in segments if s.speaker == speaker]
            total_available = sum(max(0, s.end - s.start) for s in speaker_segs)
            if total_available < 3.0:
                warnings.append(f"{speaker}: Only {total_available:.1f}s available. Clone quality will likely be poor.")
            elif total_available < 10.0:
                warnings.append(f"{speaker}: Only {total_available:.1f}s available. Clone quality may be reduced.")
    return {"status": "success", "cloned_voices": cloned_voices, "warnings": warnings}

def _mix_filter_part(input_index, allowed, delay_ms, gdb, trim):
    vol = f"volume={10 ** (gdb / 20.0):.4f}," if abs(gdb) > 0.05 else ""
    if trim:
        fade_start = max(0, allowed - 0.06)
        return (f"[{input_index}]{vol}aformat=channel_layouts=stereo,atrim=0:{allowed:.3f},"
                f"afade=t=out:st={fade_start:.3f}:d=0.06,asetpts=PTS-STARTPTS,"
                f"adelay={delay_ms}|{delay_ms},apad[a{input_index - 1}]")
    return (f"[{input_index}]{vol}aformat=channel_layouts=stereo,atrim=0:{allowed:.3f},"
            f"asetpts=PTS-STARTPTS,adelay={delay_ms}|{delay_ms},apad[a{input_index - 1}]")

def _meter_stage_generate(job_id):
    p = int((jobs_progress.get(f"generate_{job_id}") or {}).get("percent") or 0)
    return "voices" if p < 90 else "mix"


@_metered("shortdub_generate", lambda req, *a, **k: getattr(req, "job_id", ""), stage_of=_meter_stage_generate)
def generate_worker(req):
    global eleven_client
    overlap_flags = dict(getattr(req, 'overlap_allowed', None) or {})
    dead_space_flags = dict(getattr(req, 'dead_space_allowed', None) or {})
    # Keyed by job_id (not a single shared "generate" slot) so concurrent
    # generate jobs — two users, or two tabs — never clobber each other's
    # progress/result. Must match the key main.py's /api/generate and
    # /api/progress/generate use.
    _pk = f"generate_{req.job_id}"
    try:
        jobs_progress[_pk] = {"status": "processing", "percent": 0, "result": None, "error": None}
        total_segments = len(req.segments)
        if total_segments == 0:
            raise UserError("There are no lines to dub yet. Upload and transcribe a video first.")
        bucket = usage_bucket(req.job_id)
        sorted_segments = sorted(req.segments, key=lambda s: s.start)
        generated_files = []
        lines_meta = []
        src_for_loudness = resolve_job_speech(req.job_id)
        spans_for_loudness = job_speech_spans(req.job_id)
        # Only this job's previous takes may be removed. Other jobs and old
        # unscoped files are never used as a fallback.
        clear_line_audio(OUTPUT_DIR, req.job_id)
        for i, seg in enumerate(sorted_segments):
            if not seg.arabic_text.strip():
                continue
            target_duration = max(seg.end - seg.start, 0.5)
            tts_text = seg.arabic_text
            if req.tts_provider == "gemini":
                if not req.gemini_api_key:
                    raise UserError("Voice generation is temporarily unavailable. Please try again later.")
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={req.gemini_api_key}"
                payload = {"contents": [{"parts": [{"text": tts_text}]}],
                           "generationConfig": {"responseModalities": ["AUDIO"],
                                                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": req.gemini_voice or "Kore"}}}}}
                request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=120) as response:
                    data = json.load(response)
                record_gemini(req.job_id, data)
                audio_bytes = base64.b64decode(data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
                raw_filename = line_audio_path(OUTPUT_DIR, req.job_id, seg.segment_id, "raw", ".wav").name
            else:
                voice_id = req.speaker_voices.get(seg.speaker, "").strip() or req.default_voice_id.strip()
                if not voice_id:
                    raise UserError(f"No voice is selected for {seg.speaker}. Pick one in Step 4 and try again.")
                # Which engine actually created THIS speaker's voice_id --
                # resolved by main.py per-speaker BEFORE this thread started
                # (see req.speaker_voice_engines / req.default_voice_engine),
                # since a saved voice keeps belonging to whichever engine
                # cloned it even if the admin panel's Voice Engine switch
                # has since changed for NEW clones. Never assume "whatever
                # the admin panel currently says" here.
                if req.speaker_voices.get(seg.speaker, "").strip():
                    engine = req.speaker_voice_engines.get(seg.speaker) or "elevenlabs"
                else:
                    engine = req.default_voice_engine or "elevenlabs"
                if engine == "inworld":
                    api_key = req.inworld_api_key.strip()
                    if not api_key:
                        raise UserError("Voice generation is temporarily unavailable. Please try again later.")
                    tts_text = f"{inworld_service.instruction_tag(seg.emotion)}{seg.arabic_text}"
                    # Own counter, separate from eleven_chars -- so
                    # main.py's _watch_and_deduct() can charge this engine's
                    # own admin-configured rate (inworldCharsPerCredit) even
                    # when a job mixes speakers across both engines.
                    bucket["inworld_chars"] += len(tts_text)
                    audio_bytes = inworld_service.synthesize(voice_id, tts_text, api_key, language="ar")
                    raw_filename = line_audio_path(OUTPUT_DIR, req.job_id, seg.segment_id, "raw", ".mp3").name
                else:
                    api_key = req.elevenlabs_api_key.strip()
                    if not api_key:
                        raise UserError("Voice generation is temporarily unavailable. Please try again later.")
                    if eleven_client is None:
                        eleven_client = ElevenLabs(api_key=api_key)
                    tts_text = f"{_emotion_tags(seg.emotion)} {seg.arabic_text}"
                    bucket["eleven_chars"] += len(tts_text)
                    response = eleven_client.text_to_speech.convert(text=tts_text, voice_id=voice_id, model_id=TTS_MODEL_ID, language_code="ar")
                    audio_bytes = response if isinstance(response, bytes) else b"".join(chunk for chunk in response if chunk)
                    raw_filename = line_audio_path(OUTPUT_DIR, req.job_id, seg.segment_id, "raw", ".mp3").name
            raw_path = OUTPUT_DIR / raw_filename
            raw_path.write_bytes(audio_bytes)
            actual_duration = get_media_duration(raw_path)
            if actual_duration <= 0:
                actual_duration = target_duration
            required_tempo = actual_duration / target_duration
            # Each line carries its own tempo_mode now (set per-row in the
            # Step 5.5 table) instead of one global setting for the whole
            # job — fall back to the request's default only for a segment
            # saved before this field existed.
            seg_tempo_mode = getattr(seg, "tempo_mode", "") or req.tempo_mode
            if seg_tempo_mode == "excellent":
                min_tempo, max_tempo = 0.95, 1.10
            elif seg_tempo_mode == "good":
                min_tempo, max_tempo = 0.85, 1.25
            else:
                min_tempo, max_tempo = 0.75, 1.35
            needs_warning = False
            if required_tempo < min_tempo:
                tempo = 1.0
            elif required_tempo > max_tempo:
                tempo = max_tempo
                needs_warning = True
            else:
                tempo = required_tempo
            stretched_filename = line_audio_path(OUTPUT_DIR, req.job_id, seg.segment_id, "stretched", ".wav" if req.tts_provider == "gemini" else ".mp3").name
            stretched_path = OUTPUT_DIR / stretched_filename
            if abs(tempo - 1.0) > 0.02:
                run_ffmpeg(["ffmpeg", "-y", "-i", str(raw_path), "-filter:a", f"atempo={tempo:.6f}", str(stretched_path)])
            else:
                import shutil
                shutil.copy(raw_path, stretched_path)
            stretched_duration = get_media_duration(stretched_path)
            if stretched_duration <= 0:
                stretched_duration = target_duration
            # ---- Volume match: separated original vocals vs generated line ----
            orig_db = None
            dub_db = None
            auto_gain = 0.0
            try:
                if src_for_loudness is not None:
                    orig_db = _orig_level_db(src_for_loudness, spans_for_loudness, seg.start, target_duration)
                dub_db = measure_loudness_db(str(stretched_path))
                if orig_db is not None and dub_db is not None and orig_db > -60 and dub_db > -60:
                    auto_gain = max(-10.0, min(10.0, orig_db - dub_db))
            except Exception:
                auto_gain = 0.0
            lines_meta.append({"segment_id": seg.segment_id, "speaker": seg.speaker,
                               "start": seg.start, "end": seg.end,
                               "orig_db": round(orig_db, 1) if orig_db is not None else None,
                               "dub_db": round(dub_db, 1) if dub_db is not None else None,
                               "auto_gain_db": round(auto_gain, 1), "duration": round(stretched_duration, 3),
                               # Both filled in below, once the final mix is
                               # built -- tempo_warning is known now, but
                               # "trimmed" (this line's audio didn't fit its
                               # slot even after stretching, so the final mix
                               # cut it short) isn't known until the overlap/
                               # dead-space pass right before mixing.
                               "tempo_warning": needs_warning, "trimmed": False})
            generated_files.append({"file": stretched_filename, "sid": seg.segment_id, "start": seg.start,
                                    "end": seg.end, "speaker": seg.speaker, "duration": stretched_duration,
                                    "tempo_warning": needs_warning})
            jobs_progress[_pk]["percent"] = int(((i + 1) / total_segments) * 90)
        if not generated_files:
            raise UserError("None of your lines has Arabic text yet. Translate them (or type the Arabic) first.")
        USER_GAINS[req.job_id] = {lm["segment_id"]: float(lm["auto_gain_db"]) for lm in lines_meta}
        jobs_progress[_pk]["percent"] = 92
        generated_files.sort(key=lambda item: item["start"])
        max_segment_end = max(item["end"] for item in generated_files)
        max_played_end = max(item["start"] + item["duration"] for item in generated_files)
        if req.duration_mode == "extend":
            final_duration = max(req.total_duration, max_played_end, max_segment_end)
        else:
            final_duration = req.total_duration if req.total_duration > 0 else max(max_segment_end, max_played_end)
        adjusted_files = []
        cut_count = 0
        for i, item in enumerate(generated_files):
            # PATCHED: global overlap check
            allowed_end = final_duration
            if not dead_space_flags.get(item.get("sid", ""), False):
                allowed_end = min(allowed_end, item["end"])
            for _j in range(len(generated_files)):
                if _j == i: continue
                _other = generated_files[_j]
                _other_start = _other["start"]
                if overlap_flags.get(item.get("sid", ""), False) or overlap_flags.get(_other.get("sid", ""), False):
                    continue
                if _other_start > item["start"] and _other_start - 0.005 < allowed_end:
                    allowed_end = _other_start - 0.005
            allowed_duration = allowed_end - item["start"]
            if allowed_duration <= 0.02:
                item["trimmed"] = True  # no room at all -- dropped from the mix entirely
                continue
            if item["duration"] > allowed_duration + 0.02:
                cut_count += 1
                item["trimmed"] = True
            item["allowed_duration"] = min(item["duration"], allowed_duration)
            adjusted_files.append(item)
        if not adjusted_files:
            raise UserError("The dubbed lines don't fit inside the video's timeline. Try a higher Speed adjustment or allow overlap, then generate again.")
        # Carry the tempo/trim warnings back onto lines_meta (the per-line
        # data the Step 5.5 table actually renders) so the UI can flag which
        # rows need attention -- these were only known on generated_files,
        # a separate list built for the mixing step above.
        _trimmed_by_sid = {f.get("sid"): bool(f.get("trimmed")) for f in generated_files}
        for _lm in lines_meta:
            _lm["trimmed"] = _trimmed_by_sid.get(_lm["segment_id"], False)
        inputs = ["-f", "lavfi", "-t", str(final_duration), "-i", "anullsrc=r=44100:cl=stereo"]
        filter_parts = []
        active_gen = dict(USER_GAINS.get(req.job_id or "", {}))
        for idx, item in enumerate(adjusted_files):
            input_index = idx + 1
            inputs.extend(["-i", str(OUTPUT_DIR / item["file"])])
            delay_ms = int(item["start"] * 1000)
            allowed = max(item["allowed_duration"], 0.05)
            gdb = float(active_gen.get(item.get("sid", ""), 0.0) or 0.0)
            trim = item["duration"] > allowed + 0.02
            filter_parts.append(_mix_filter_part(input_index, allowed, delay_ms, gdb, trim))
        mix_inputs = "".join([f"[a{i}]" for i in range(len(adjusted_files))])
        filter_parts.append(f"[0]{mix_inputs}amix=inputs={len(adjusted_files) + 1}:duration=first:normalize=0[out]")
        filter_complex = ";".join(filter_parts)
        output_file = OUTPUT_DIR / f"{req.job_id}_final_dubbed.mp3"
        run_ffmpeg(["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[out]", "-t", str(final_duration), str(output_file)])
        _room = _apply_room(req.job_id, output_file, adjusted_files, sorted_segments)
        warning_count = sum(1 for f in adjusted_files if f.get("tempo_warning"))
        result = {"status": "success", "output_folder": str(OUTPUT_DIR), "final_file": str(output_file),
                  "segments_generated": len(adjusted_files), "tempo_warnings": warning_count,
                  "duration_cuts": cut_count, "final_duration": round(final_duration, 2),
                  # Legacy field name (predates Inworld) -- now the combined
                  # total across both engines' characters, for display and
                  # as a fallback total main.py's _watch_and_deduct() can use
                  # if it somehow can't read the bucket's own two counters
                  # directly (it normally does, and charges each engine's
                  # own rate separately -- this is just the display/fallback
                  # total, not what's actually charged).
                  "eleven_credits_used": bucket["eleven_chars"] + bucket["inworld_chars"], "lines": lines_meta,
                  "room": _room}
        jobs_progress[_pk].update({"status": "done", "percent": 100, "result": result, "error": None})
    except Exception as e:
        jobs_progress[_pk] = {"status": "error", "percent": 0, "error": friendly_error(e), "result": None}

def rebuild_final_mix(segments, total_duration, duration_mode="exact", job_id=None, flags=None, dead_space_flags=None):
    """Rebuild this job's final dubbed audio from existing line files (.wav OR .mp3), applying Step 5.5 gains."""
    overlap_flags = dict(flags or {})
    dead_space_flags = dict(dead_space_flags or {})
    active = dict(USER_GAINS.get(job_id or "", {}))
    segs = sorted([s for s in segments if (s.arabic_text or "").strip()], key=lambda s: s.start)
    items = []
    for s in segs:
        sp = line_audio_path(OUTPUT_DIR, job_id, s.segment_id, "stretched", ".wav")
        if not sp.exists():
            sp = line_audio_path(OUTPUT_DIR, job_id, s.segment_id, "stretched", ".mp3")
        if not sp.exists():
            continue
        items.append({"file": sp.name, "sid": s.segment_id, "start": s.start, "end": s.end,
                      "duration": get_media_duration(sp)})
    if not items:
        raise Exception("No generated line audio found. Run Generate once first.")
    max_segment_end = max(i["end"] for i in items)
    max_played_end = max(i["start"] + i["duration"] for i in items)
    if duration_mode == "extend":
        final_duration = max(total_duration, max_played_end, max_segment_end)
    else:
        final_duration = total_duration if total_duration > 0 else max(max_segment_end, max_played_end)
    adjusted = []
    cuts = 0
    for i, item in enumerate(items):
        # PATCHED: global overlap check
        allowed_end = final_duration
        if not dead_space_flags.get(item.get("sid", ""), False):
            allowed_end = min(allowed_end, item["end"])
        for _j in range(len(items)):
            if _j == i: continue
            _other = items[_j]
            _other_start = _other["start"]
            if overlap_flags.get(item.get("sid", ""), False) or overlap_flags.get(_other.get("sid", ""), False):
                continue
            if _other_start > item["start"] and _other_start - 0.005 < allowed_end:
                allowed_end = _other_start - 0.005
        allowed = allowed_end - item["start"]
        if allowed <= 0.02:
            item["trimmed"] = True  # no room at all -- dropped from the mix entirely
            continue
        if item["duration"] > allowed + 0.02:
            cuts += 1
            item["trimmed"] = True
        item["allowed_duration"] = min(item["duration"], allowed)
        adjusted.append(item)
    if not adjusted:
        raise UserError("The dubbed lines don't fit inside the video's timeline. Try a higher Speed adjustment or allow overlap, then generate again.")
    # Same "who's still cut/trimmed after this rebuild" list as
    # remix_with_offsets, for the Step 5.5 table's needs-attention marks.
    trimmed_segment_ids = [it["sid"] for it in items if it.get("trimmed")]
    inputs = ["-f", "lavfi", "-t", str(final_duration), "-i", "anullsrc=r=44100:cl=stereo"]
    filter_parts = []
    for idx, item in enumerate(adjusted):
        input_index = idx + 1
        inputs.extend(["-i", str(OUTPUT_DIR / item["file"])])
        delay_ms = int(item["start"] * 1000)
        allowed = max(item["allowed_duration"], 0.05)
        gdb = float(active.get(item["sid"], 0.0) or 0.0)
        trim = item["duration"] > allowed + 0.02
        filter_parts.append(_mix_filter_part(input_index, allowed, delay_ms, gdb, trim))
    mix_inputs = "".join([f"[a{i}]" for i in range(len(adjusted))])
    filter_parts.append(f"[0]{mix_inputs}amix=inputs={len(adjusted) + 1}:duration=first:normalize=0[out]")
    filter_complex = ";".join(filter_parts)
    output_file = OUTPUT_DIR / f"{job_id}_final_dubbed.mp3"
    run_ffmpeg(["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[out]", "-t", str(final_duration), str(output_file)])
    _apply_room(job_id, output_file, adjusted, segs)
    return {"segments_generated": len(adjusted), "duration_cuts": cuts,
            "final_duration": round(final_duration, 2), "trimmed_segment_ids": trimmed_segment_ids}

def _measure_line_loudness(job_id, seg, stretched, target_duration):
    """Loudness of the ORIGINAL speaker over this line's window vs. the freshly
    built Arabic line (mean dB, ffmpeg volumedetect) -- what the Step 5.5
    sliders are anchored to. Returns None if it can't be measured."""
    try:
        src = resolve_job_speech(job_id)
        orig_db = _orig_level_db(src, job_speech_spans(job_id), seg.start, target_duration) if src else None
        dub_db = measure_loudness_db(str(stretched))
        ok = orig_db is not None and dub_db is not None and orig_db > -60 and dub_db > -60
        return {"segment_id": seg.segment_id,
                "orig_db": round(orig_db, 1) if orig_db is not None else None,
                "dub_db": round(dub_db, 1) if dub_db is not None else None,
                "auto_gain_db": round(max(-10.0, min(10.0, orig_db - dub_db)), 1) if ok else 0.0}
    except Exception:
        return None

def regenerate_line(req):
    """Re-speak ONE segment with TTS, stretch it into its window, volume-match it, then rebuild the mix."""
    global eleven_client
    try:
        seg = req.segment
        if not (seg.arabic_text or "").strip():
            return {"error": "This line has no Arabic text yet."}
        voice_id = (req.voice_id or "").strip()
        if not voice_id:
            return {"error": f"No voice selected for {seg.speaker}. Pick one in Step 4 first."}
        bucket = usage_bucket(req.job_id)
        target_duration = max(seg.end - seg.start, 0.5)
        # Which engine created this specific voice_id -- resolved by
        # main.py before this call (see req.voice_engine's docstring on
        # RegenerateLineRequest). Same "belongs to whoever cloned it"
        # reasoning as generate_worker above.
        engine = req.voice_engine or "elevenlabs"
        if engine == "inworld":
            api_key = req.inworld_api_key.strip()
            if not api_key:
                return {"error": "Voice generation is temporarily unavailable. Please try again later."}
            tts_text = f"{inworld_service.instruction_tag(seg.emotion)}{seg.arabic_text}"
            bucket["inworld_chars"] += len(tts_text)
            audio_bytes = inworld_service.synthesize(voice_id, tts_text, api_key, language="ar")
        else:
            api_key = req.elevenlabs_api_key.strip()
            if not api_key:
                return {"error": "Voice generation is temporarily unavailable. Please try again later."}
            if eleven_client is None:
                eleven_client = ElevenLabs(api_key=api_key)
            tts_text = f"{_emotion_tags(seg.emotion)} {seg.arabic_text}"
            bucket["eleven_chars"] += len(tts_text)
            response = eleven_client.text_to_speech.convert(text=tts_text, voice_id=voice_id, model_id=TTS_MODEL_ID, language_code="ar")
            audio_bytes = response if isinstance(response, bytes) else b"".join(c for c in response if c)
        raw_path = line_audio_path(OUTPUT_DIR, req.job_id, seg.segment_id, "raw", ".mp3")
        raw_path.write_bytes(audio_bytes)
        actual = get_media_duration(raw_path)
        if actual <= 0:
            actual = target_duration
        required = actual / target_duration
        # Prefer the segment's own tempo_mode (Step 5.5 per-line setting);
        # fall back to the request-level value only if the segment predates
        # that field.
        seg_tempo_mode = getattr(seg, "tempo_mode", "") or req.tempo_mode
        if seg_tempo_mode == "excellent":
            min_tempo, max_tempo = 0.95, 1.10
        elif seg_tempo_mode == "good":
            min_tempo, max_tempo = 0.85, 1.25
        else:
            min_tempo, max_tempo = 0.75, 1.35
        warning = False
        if required < min_tempo:
            tempo = 1.0
        elif required > max_tempo:
            tempo = max_tempo
            warning = True
        else:
            tempo = required
        stretched = line_audio_path(OUTPUT_DIR, req.job_id, seg.segment_id, "stretched", ".wav")
        cmd = ["ffmpeg", "-y", "-i", str(raw_path)]
        if abs(tempo - 1.0) > 0.02:
            cmd += ["-filter:a", f"atempo={tempo:.6f}"]
        cmd += ["-acodec", "pcm_s16le", str(stretched)]
        run_ffmpeg(cmd)
        # Volume-match the re-spoken line to the original vocal slice (mix-time gain)
        line_info = _measure_line_loudness(req.job_id, seg, stretched, target_duration)
        if line_info and line_info.get("orig_db") is not None and line_info.get("dub_db") is not None:
            USER_GAINS.setdefault(req.job_id, {})[seg.segment_id] = line_info["auto_gain_db"]
        mix = rebuild_final_mix(req.segments, req.total_duration, req.duration_mode, job_id=req.job_id, flags=getattr(req, "overlap_allowed", None), dead_space_flags=getattr(req, "dead_space_allowed", None))
        return {"status": "success",
                "stretched_duration": round(get_media_duration(stretched), 2),
                "target": round(target_duration, 2),
                "tempo_warning": warning,
                "line": line_info,
                "mix": mix}
    except Exception as e:
        return {"error": friendly_error(e)}

def restretch_line(req):
    """Pure editing operation: re-apply the line's CURRENT Time Stretch
    setting to its already-generated raw audio and rebuild the mix — no new
    TTS call, no voice credits spent. This is what the Step 5.5 Time Stretch
    dropdown uses (it only changes how the existing take is time-warped to
    fit its slot); regenerate_line() above is for when the line needs to be
    re-spoken from scratch (new text, emotion, voice, or a changed time
    span from Step 2)."""
    try:
        seg = req.segment
        if not (seg.arabic_text or "").strip():
            return {"error": "This line has no Arabic text yet."}
        raw_path = line_audio_path(OUTPUT_DIR, req.job_id, seg.segment_id, "raw", ".mp3")
        if not raw_path.exists():
            raw_path = line_audio_path(OUTPUT_DIR, req.job_id, seg.segment_id, "raw", ".wav")
        if not raw_path.exists():
            return {"error": "not_generated"}
        target_duration = max(seg.end - seg.start, 0.5)
        actual = get_media_duration(raw_path)
        if actual <= 0:
            actual = target_duration
        required = actual / target_duration
        seg_tempo_mode = getattr(seg, "tempo_mode", "") or req.tempo_mode
        if seg_tempo_mode == "excellent":
            min_tempo, max_tempo = 0.95, 1.10
        elif seg_tempo_mode == "good":
            min_tempo, max_tempo = 0.85, 1.25
        else:
            min_tempo, max_tempo = 0.75, 1.35
        warning = False
        if required < min_tempo:
            tempo = 1.0
        elif required > max_tempo:
            tempo = max_tempo
            warning = True
        else:
            tempo = required
        stretched = line_audio_path(OUTPUT_DIR, req.job_id, seg.segment_id, "stretched", ".wav")
        cmd = ["ffmpeg", "-y", "-i", str(raw_path)]
        if abs(tempo - 1.0) > 0.02:
            cmd += ["-filter:a", f"atempo={tempo:.6f}"]
        cmd += ["-acodec", "pcm_s16le", str(stretched)]
        run_ffmpeg(cmd)
        # Refresh this line's measured loudness for the Step 5.5 sliders; the
        # user's current gain for the line is deliberately left untouched.
        line_info = _measure_line_loudness(req.job_id, seg, stretched, target_duration)
        mix = rebuild_final_mix(req.segments, req.total_duration, req.duration_mode, job_id=req.job_id, flags=getattr(req, "overlap_allowed", None), dead_space_flags=getattr(req, "dead_space_allowed", None))
        return {"status": "success",
                "stretched_duration": round(get_media_duration(stretched), 2),
                "target": round(target_duration, 2),
                "tempo_warning": warning,
                "line": line_info,
                "mix": mix}
    except Exception as e:
        return {"error": friendly_error(e)}

@_metered("shortdub_rebuild", lambda req, *a, **k: getattr(req, "job_id", ""))
def remix_with_offsets(req):
    """Rebuild this job's final dubbed audio applying per-segment time offsets AND Step 5.5 volume gains."""
    overlap_flags = dict(getattr(req, 'overlap_allowed', None) or {})
    dead_space_flags = dict(getattr(req, 'dead_space_allowed', None) or {})
    try:
        gains = dict(getattr(req, "gains", None) or {})
        if gains:
            # MERGE (not replace): a partial payload must never wipe the other lines' gains
            _ug = USER_GAINS.setdefault(req.job_id, {})
            for _k, _v in gains.items():
                try:
                    _ug[_k] = float(_v)
                except (TypeError, ValueError):
                    pass
        _rm = getattr(req, "room", None)
        if isinstance(_rm, dict) and _rm:
            _mode = str(_rm.get("mode") or "auto").lower()
            if _mode not in ("auto", "off", "manual"):
                _mode = "auto"
            _clean = {"mode": _mode}
            for _k in ("rt60", "wet_db", "trim_db"):
                try:
                    _clean[_k] = float(_rm.get(_k))
                except (TypeError, ValueError):
                    pass
            ROOM_SETTINGS[req.job_id] = _clean
        active = dict(USER_GAINS.get(req.job_id or "", {}))
        segs = sorted([s for s in req.segments if (s.arabic_text or "").strip()], key=lambda s: s.start)
        items = []
        for s in segs:
            sp = line_audio_path(OUTPUT_DIR, req.job_id, s.segment_id, "stretched", ".wav")
            if not sp.exists():
                sp = line_audio_path(OUTPUT_DIR, req.job_id, s.segment_id, "stretched", ".mp3")
            if not sp.exists():
                continue
            off = float((req.offsets or {}).get(s.segment_id, 0.0) or 0.0)
            items.append({"file": sp.name, "sid": s.segment_id, "start": max(0.0, s.start + off),
                          "end": s.end + off, "duration": get_media_duration(sp),
                          "orig_mid": (s.start + s.end) / 2.0})
        if not items:
            return {"error": "Generate the Arabic audio first, then try again."}
        max_segment_end = max(i["end"] for i in items)
        max_played_end = max(i["start"] + i["duration"] for i in items)
        if req.duration_mode == "extend":
            final_duration = max(req.total_duration, max_played_end, max_segment_end)
        else:
            final_duration = req.total_duration if req.total_duration > 0 else max(max_segment_end, max_played_end)
        adjusted = []
        cuts = 0
        for i, item in enumerate(items):
            # PATCHED: global overlap check
            allowed_end = final_duration
            if not dead_space_flags.get(item.get("sid", ""), False):
                allowed_end = min(allowed_end, item["end"])
            for _j in range(len(items)):
                if _j == i: continue
                _other = items[_j]
                _other_start = _other["start"]
                if overlap_flags.get(item.get("sid", ""), False) or overlap_flags.get(_other.get("sid", ""), False):
                    continue
                if _other_start > item["start"] and _other_start - 0.005 < allowed_end:
                    allowed_end = _other_start - 0.005
            allowed = allowed_end - item["start"]
            if allowed <= 0.02:
                item["trimmed"] = True  # no room at all -- dropped from the mix entirely
                continue
            if item["duration"] > allowed + 0.02:
                cuts += 1
                item["trimmed"] = True
            item["allowed_duration"] = min(item["duration"], allowed)
            adjusted.append(item)
        if not adjusted:
            return {"error": "The dubbed lines don't fit inside the video's timeline. Try a higher Speed adjustment or allow overlap, then generate again."}
        # Which lines are still cut/trimmed after this rebuild -- lets the
        # Step 5.5 table clear a line's "needs attention" mark the moment
        # the user's offset/overlap/dead-space change actually fixes it.
        trimmed_segment_ids = [it["sid"] for it in items if it.get("trimmed")]
        inputs = ["-f", "lavfi", "-t", str(final_duration), "-i", "anullsrc=r=44100:cl=stereo"]
        filter_parts = []
        for idx, item in enumerate(adjusted):
            input_index = idx + 1
            inputs.extend(["-i", str(OUTPUT_DIR / item["file"])])
            delay_ms = int(item["start"] * 1000)
            allowed = max(item["allowed_duration"], 0.05)
            gdb = float(active.get(item["sid"], 0.0) or 0.0)
            trim = item["duration"] > allowed + 0.02
            filter_parts.append(_mix_filter_part(input_index, allowed, delay_ms, gdb, trim))
        mix_inputs = "".join([f"[a{i}]" for i in range(len(adjusted))])
        filter_parts.append(f"[0]{mix_inputs}amix=inputs={len(adjusted) + 1}:duration=first:normalize=0[out]")
        filter_complex = ";".join(filter_parts)
        output_file = OUTPUT_DIR / f"{req.job_id}_final_dubbed.mp3"
        run_ffmpeg(["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[out]", "-t", str(final_duration), str(output_file)])
        _room = _apply_room(req.job_id, output_file, adjusted, segs)
        return {"status": "success", "segments_generated": len(adjusted), "duration_cuts": cuts,
                "final_duration": round(final_duration, 2), "trimmed_segment_ids": trimmed_segment_ids, "room": _room}
    except Exception as e:
        return {"error": friendly_error(e)}

def cleanup_cloned_voices(api_key: str, keep_ids: list = None) -> dict:
    keep = set(keep_ids or [])
    try:
        req = urllib.request.Request("https://api.elevenlabs.io/v1/voices?page_size=100", headers={"xi-api-key": api_key})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        deleted, errors = 0, []
        for v in data.get("voices", []):
            name = v.get("name") or ""
            vid = v.get("voice_id")
            if not name.startswith(("Cloned_", "Custom_")) or vid in keep:
                continue
            try:
                dreq = urllib.request.Request(f"https://api.elevenlabs.io/v1/voices/{vid}", method="DELETE", headers={"xi-api-key": api_key})
                with urllib.request.urlopen(dreq, timeout=30) as dr:
                    dr.read()
                deleted += 1
            except Exception as e:
                errors.append(f"{name}: {e}")
        return {"deleted": deleted, "errors": errors}
    except Exception as e:
        return {"deleted": 0, "errors": [str(e)]}


def delete_voice(voice_id: str, api_key: str) -> dict:
    """Deletes exactly ONE voice by id -- unlike cleanup_cloned_voices()
    above, which sweeps every Cloned_*/Custom_* voice in the whole
    account. Used by the admin "Compare Voice Providers" tool so a
    comparison run can never risk deleting a real user's in-flight cloned
    voice just because it happened to be running at the same time."""
    try:
        req = urllib.request.Request(f"https://api.elevenlabs.io/v1/voices/{voice_id}", method="DELETE", headers={"xi-api-key": api_key})
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        return {"ok": True}
    except Exception as e:
        print(f"[voices] delete failed: {e}")
        return {"ok": False, "error": "We couldn't delete that voice. Please try again."}


def generate_sample(voice_id: str, text: str, api_key: str) -> bytes:
    """Minimal one-off TTS call: given a voice_id and plain text, returns
    the generated audio bytes. Same model/language as the real dubbing
    pipeline (see generate_worker's TTS_MODEL_ID + language_code='ar' call)
    so the admin comparison tool is a fair, representative test -- but
    this bypasses all of the dubbing-specific machinery (timing, mixing,
    emotion-tag lookup, credit accounting). Only used by the admin
    "Compare Voice Providers" tool. Raises on failure; caller catches."""
    global eleven_client
    if eleven_client is None:
        eleven_client = ElevenLabs(api_key=api_key)
    response = eleven_client.text_to_speech.convert(text=text, voice_id=voice_id, model_id=TTS_MODEL_ID, language_code="ar")
    return response if isinstance(response, bytes) else b"".join(chunk for chunk in response if chunk)


def add_custom_voice(job_id: str, speaker: str, src_path, api_key: str):
    safe = "".join(c for c in speaker if c.isalnum()).strip() or "spk"
    wav = OUTPUT_DIR / f"custom_{job_id}_{safe}.wav"
    run_ffmpeg(["ffmpeg", "-y", "-i", str(src_path), "-vn", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le", str(wav)])
    dur = get_media_duration(wav)
    if dur > 20.5:
        try:
            wav.unlink()
        except Exception:
            pass
        return f"ERROR: This clip is {dur:.1f} seconds long. The maximum is 20 seconds. Please use a shorter recording."
    if dur < 1.0:
        pad = OUTPUT_DIR / f"custom_{job_id}_{safe}_pad.wav"
        run_ffmpeg(["ffmpeg", "-y", "-i", str(wav), "-af", f"apad=pad_dur={max(0.2, 1.15 - dur)}", "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le", str(pad)])
        try:
            wav.unlink()
        except Exception:
            pass
        wav = pad
    with open(wav, "rb") as f:
        file_data = f.read()
    http = urllib3.PoolManager()
    resp = http.request("POST", "https://api.elevenlabs.io/v1/voices/add",
                        headers={"xi-api-key": api_key},
                        fields={"name": f"Custom_{speaker}", "files": (wav.name, file_data, "audio/wav"), "labels": "{}"})
    try:
        wav.unlink()
    except Exception:
        pass
    if resp.status == 200:
        return json.loads(resp.data.decode()).get("voice_id", "ERROR: no voice_id returned")
    print(f"[custom-voice] the voice engine rejected the clip (status {resp.status})")
    return "ERROR: We couldn't create a voice from this clip. Please use a clear recording of one person speaking (1-20 seconds) without music."
