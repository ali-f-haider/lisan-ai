import re
import threading
import time
import traceback
from pathlib import Path

import torch
from faster_whisper import WhisperModel

from config import UPLOAD_DIR, WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE
from app_state import jobs_progress, diarization_pipelines
from ffmpeg_utils import (
    extract_audio_from_video,
    normalize_audio_for_diarization,
    separate_vocals,
    detect_silence_gaps,
)
from vad_utils import run_vad_timing_checks

# Match the container's 4 vCPUs — prevents thread oversubscription
# (the "calm CPU but 3-4x slower" bug).
torch.set_num_threads(4)


def _trim_memory():
    """Ask glibc to actually hand freed heap memory back to the OS.

    Dropping a Python/PyTorch object and calling gc.collect() frees it at
    the application level, but glibc's malloc keeps that freed space inside
    the process (to reuse later) instead of returning it to the OS -- so
    Railway's memory graph can keep showing the old high-water mark even
    though nothing is actually using that RAM anymore. malloc_trim(0) forces
    glibc to release what it can. Safe no-op if it's ever unavailable."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def _fadvise_dontneed(path):
    """Hints the kernel that this process is done with a file's contents for
    now, so it can drop that file's pages from the OS page cache -- without
    deleting the file itself. This is a DIFFERENT problem from _trim_memory
    above: that one reclaims heap memory Python/PyTorch allocated; this one
    targets file-backed cache built up by ffmpeg/faster-whisper/pyannote
    reading these audio files off disk during a job.

    Why this exists: intermediate job files (extracted audio, separated
    vocals, etc.) are deliberately kept on disk for CLEANUP_RETENTION_HOURS
    (6 hours, see main.py) so a user can resume/download mid-session work --
    but nothing was telling the kernel it could stop caching their contents
    in RAM during that whole window, once a job is actually done reading
    them. That's a real, billable cost on Railway (memory is metered per
    MB/minute, and page cache counts toward the reported "used" figure the
    same as real memory) -- not just a cosmetic dashboard number. This does
    NOT touch the file on disk at all, only its cached copy in RAM, so the
    6-hour retention window for resuming/downloading is completely
    unaffected.

    posix_fadvise(DONTNEED) only advises the kernel; it's a normal,
    unprivileged operation any process can do on files it can read -- unlike
    forcing a global cache drop (/proc/sys/vm/drop_caches), which needs
    host-level root that a shared platform like Railway never grants a
    single tenant's container. If the kernel later needs that file's data
    again (e.g. a later export step reads background_path), it just reads
    it fresh off disk -- slightly slower once, never broken. Safe no-op on
    any platform/filesystem where this isn't supported."""
    try:
        import os
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)
    except Exception:
        pass


# Loaded lazily on first use, and released after each job, instead of
# staying resident in RAM for the entire lifetime of the process --
# large-v3 is a big model and most of the time nobody is transcribing.
_model = None
_model_lock = threading.Lock()


def _get_model():
    """Load the Whisper model on first use (thread-safe), and reuse it
    for the rest of this job. cpu_threads=2 so Whisper shares the CPU
    peacefully with speaker detection running in parallel."""
    global _model
    with _model_lock:
        if _model is None:
            _model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE,
                                  compute_type=WHISPER_COMPUTE, cpu_threads=2)
        return _model


def _release_model():
    """Drop the loaded Whisper model so its memory is freed once a job's
    transcription step is done -- it isn't needed again until the next
    job requests it via _get_model()."""
    global _model
    with _model_lock:
        _model = None
    import gc
    gc.collect()
    _trim_memory()


def _release_diarization_pipeline(hf_token):
    """Drop the cached pyannote diarization pipeline for this token so its
    memory is freed once this job's speaker-detection step is done. Unlike
    the Whisper model, this pipeline used to stay in `diarization_pipelines`
    for the entire lifetime of the process once any job used speaker
    detection -- that's a major reason idle memory usage kept climbing."""
    diarization_pipelines.pop(hf_token, None)
    import gc
    gc.collect()
    _trim_memory()


def split_segment(segment, max_duration=15.0):
    """Fallback splitter used when diarization is unavailable."""
    text = segment.text.strip()
    start = segment.start
    end = segment.end
    duration = end - start
    if duration <= max_duration and len(text) < 200:
        return [{"start": start, "end": end, "text": text}]
    sentences = re.split(r'(?<=[.!?؟])\s+', text)
    if len(sentences) <= 1:
        if duration <= max_duration:
            return [{"start": start, "end": end, "text": text}]
        mid = start + duration / 2
        words = text.split()
        mid_word = len(words) // 2
        return [
            {"start": start, "end": mid, "text": " ".join(words[:mid_word])},
            {"start": mid, "end": end, "text": " ".join(words[mid_word:])},
        ]
    results = []
    total_chars = sum(len(s) for s in sentences if s.strip())
    current_time = start
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        sentence_duration = (len(sentence) / total_chars) * duration
        sentence_end = current_time + sentence_duration
        results.append({"start": round(current_time, 2), "end": round(sentence_end, 2), "text": sentence})
        current_time = sentence_end
    return results


def get_speaker_turns(input_path: str, hf_token: str, speaker_count):
    try:
        from pyannote.audio import Pipeline
        import soundfile as sf
    except Exception as e:
        raise Exception(f"Missing dependency: {e}. Run: pip install soundfile")

    pipeline = diarization_pipelines.get(hf_token)
    if pipeline is None:
        pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=hf_token)
        diarization_pipelines[hf_token] = pipeline

    normalized_path = normalize_audio_for_diarization(input_path)
    audio_data, sample_rate = sf.read(normalized_path, dtype='float32')
    if len(audio_data.shape) > 1:
        audio_data = audio_data.mean(axis=1)
    waveform = torch.from_numpy(audio_data).unsqueeze(0)

    kwargs = {}
    if speaker_count:
        kwargs["num_speakers"] = int(speaker_count)
        kwargs["min_speakers"] = int(speaker_count)
        kwargs["max_speakers"] = int(speaker_count)

    result = pipeline({"waveform": waveform, "sample_rate": sample_rate}, **kwargs)
    if hasattr(result, "speaker_diarization"):
        diarization = result.speaker_diarization
    elif hasattr(result, "exclusive_speaker_diarization"):
        diarization = result.exclusive_speaker_diarization
    elif hasattr(result, "annotation"):
        diarization = result.annotation
    else:
        diarization = result

    turns = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        turns.append({"start": float(turn.start), "end": float(turn.end), "speaker": str(speaker)})
    return turns


def speaker_at_time(time_point: float, turns: list):
    best_speaker = None
    best_distance = None
    for turn in turns:
        if turn["start"] <= time_point <= turn["end"]:
            return turn["speaker"]
        distance = min(abs(time_point - turn["start"]), abs(time_point - turn["end"]))
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_speaker = turn["speaker"]
    return best_speaker


def assign_speaker_by_words(segment_words, part_start: float, part_end: float, turns: list):
    if not segment_words:
        return None
    counts = {}
    for word in segment_words:
        ws = getattr(word, "start", None)
        we = getattr(word, "end", None)
        if ws is None or we is None:
            continue
        word_mid = (float(ws) + float(we)) / 2.0
        if part_start <= word_mid <= part_end:
            speaker = speaker_at_time(word_mid, turns)
            if speaker:
                counts[speaker] = counts.get(speaker, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def assign_speaker_for_segment(start: float, end: float, turns: list):
    best_speaker = None
    best_overlap = 0.0
    for turn in turns:
        overlap = min(end, turn["end"]) - max(start, turn["start"])
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = turn["speaker"]
    return best_speaker


def group_words_by_speaker(segment_words, turns):
    """Split one Whisper segment's words into consecutive same-speaker groups."""
    if not turns or not segment_words:
        return []
    groups = []
    current_speaker = None
    current_words = []
    for w in segment_words:
        ws = getattr(w, "start", None)
        we = getattr(w, "end", None)
        if ws is None or we is None:
            continue
        wmid = (float(ws) + float(we)) / 2.0
        spk = speaker_at_time(wmid, turns)
        if spk is None:
            spk = assign_speaker_for_segment(float(ws), float(we), turns)
        if spk is None:
            spk = current_speaker
        if current_speaker is None:
            current_speaker = spk
        if spk != current_speaker:
            if current_words:
                groups.append((current_speaker, current_words))
            current_speaker = spk
            current_words = [w]
        else:
            current_words.append(w)
    if current_words:
        groups.append((current_speaker, current_words))

    # Smooth 1-word diarization flickers sandwiched between the same speaker
    smoothed = []
    for i, (spk, words) in enumerate(groups):
        if len(words) == 1 and 0 < i < len(groups) - 1:
            prev_spk = groups[i - 1][0]
            next_spk = groups[i + 1][0]
            if prev_spk == next_spk and prev_spk != spk and smoothed:
                smoothed[-1] = (prev_spk, smoothed[-1][1] + words)
                continue
        smoothed.append((spk, words))

    final = []
    for spk, words in smoothed:
        if final and final[-1][0] == spk:
            final[-1] = (spk, final[-1][1] + words)
        else:
            final.append((spk, words))
    return final


def chunk_words_by_duration(words, max_duration=15.0):
    chunks = []
    cur = []
    cur_start = None
    for w in words:
        if cur_start is None:
            cur_start = float(w.start)
        if cur and (float(w.end) - cur_start) > max_duration:
            chunks.append(cur)
            cur = [w]
            cur_start = float(w.start)
        else:
            cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def merge_mid_sentence_rows(rows):
    """Re-join consecutive same-speaker rows cut mid-sentence (no . ! ? at the end)."""
    merged = []
    for row in rows:
        if merged:
            prev = merged[-1]
            gap = row["start"] - prev["end"]
            prev_ends_sentence = bool(re.search(r'[.!?؟]\s*$', prev["text"]))
            if prev["speaker"] == row["speaker"] and gap <= 0.25 and not prev_ends_sentence:
                prev["end"] = row["end"]
                prev["text"] = (prev["text"] + " " + row["text"]).strip()
                prev["words"] = prev.get("words", []) + row.get("words", [])
                continue
        merged.append(row)
    return merged


def transcribe_worker(job_id: str, input_path: str, hf_token: str, speaker_count):
    try:
        jobs_progress[job_id] = {
            "status": "processing", "percent": 0, "segments": [], "full_duration": 0.0,
            "status_text": "Starting...", "warning": None, "detected_speakers": 0,
            "is_video": False, "has_background": False,
        }

        file_path = Path(input_path)
        is_video = file_path.suffix.lower() in ['.mp4', '.avi', '.mkv', '.mov', '.webm']
        audio_path = input_path
        background_path = None
        extracted_audio = None

        if is_video:
            jobs_progress[job_id]["status_text"] = "Extracting audio from video..."
            jobs_progress[job_id]["percent"] = 3
            jobs_progress[job_id]["is_video"] = True
            extracted_audio = UPLOAD_DIR / f"{job_id}_audio.wav"
            extract_audio_from_video(input_path, str(extracted_audio))
            audio_path = str(extracted_audio)

            jobs_progress[job_id]["status_text"] = "Separating vocals from background (this takes a while)..."
            jobs_progress[job_id]["percent"] = 8
            try:
                vocals_path, background_path = separate_vocals(audio_path, str(UPLOAD_DIR / f"{job_id}_separated"))
                audio_path = vocals_path
                jobs_progress[job_id]["has_background"] = True
                jobs_progress[job_id]["background_path"] = background_path
            except Exception as e:
                jobs_progress[job_id]["warning"] = f"Vocal separation failed: {str(e)}. Using full audio instead."
                jobs_progress[job_id]["has_background"] = False

        turns = []
        speaker_label_map = {}
        warning = jobs_progress[job_id].get("warning")

        # ---------- Speaker detection runs IN PARALLEL with transcription ----------
        diar_result = {"turns": [], "error": None}

        def diarize():
            if not hf_token:
                return
            try:
                diar_result["turns"] = get_speaker_turns(audio_path, hf_token, speaker_count)
            except Exception as e:
                diar_result["error"] = str(e)

        diar_thread = None
        if hf_token:
            torch.set_num_threads(4)  # diarization gets core #4...
            jobs_progress[job_id]["status_text"] = "Detecting speakers + transcribing in parallel..."
            jobs_progress[job_id]["percent"] = 15
            diar_thread = threading.Thread(target=diarize, daemon=True)
            diar_thread.start()

        # ...Whisper gets core #2 (cpu_threads=1 at model init)
        jobs_progress[job_id]["status_text"] = "Transcribing audio (speakers detected in background)..."
        segments_gen, info = _get_model().transcribe(
            str(audio_path),
            beam_size=5,
            language="en",
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        total_duration = float(info.duration) if info.duration else 1.0

        raw_segments = []
        span = 55 if diar_thread is not None else 75
        try:
            for segment in segments_gen:
                raw_segments.append(segment)
                jobs_progress[job_id]["percent"] = min(15 + int((segment.end / total_duration) * span), 15 + span)
        finally:
            # Free the Whisper model's RAM now that decoding is done -- it's
            # not needed again until the next job (speaker detection above
            # runs on a separate pyannote pipeline, not this model).
            _release_model()

        # ---------- Wait for speaker detection (max 5 min) with live timer ----------
        if diar_thread is not None:
            waited = 0
            while diar_thread.is_alive() and waited < 300:
                diar_thread.join(timeout=30)
                waited += 30
                jobs_progress[job_id]["status_text"] = f"Finishing speaker detection... ({waited}s elapsed)"
                jobs_progress[job_id]["percent"] = min(70 + waited // 10, 85)

            turns = []
            try:
                if diar_thread.is_alive():
                    warning = (warning + " | " if warning else "") + \
                        "Speaker detection timed out; all lines assigned to Speaker 1."
                elif diar_result["error"]:
                    warning = (warning + " | " if warning else "") + \
                        f"Speaker detection failed: {diar_result['error']}. All lines assigned to Speaker 1."

                turns = diar_result["turns"]
                for turn in sorted(turns, key=lambda x: x["start"]):
                    raw_speaker = turn["speaker"]
                    if raw_speaker not in speaker_label_map:
                        speaker_label_map[raw_speaker] = f"Speaker {len(speaker_label_map) + 1}"
                jobs_progress[job_id]["detected_speakers"] = len(speaker_label_map)
                if speaker_count and len(speaker_label_map) < int(speaker_count):
                    warning = (warning + " | " if warning else "") + \
                        f"Requested {speaker_count} speakers, but only {len(speaker_label_map)} were detected."
            finally:
                # Free the diarization pipeline's RAM now that speaker detection
                # is done for this job -- it isn't needed again until the next
                # job that requests speaker detection. In a finally block (like
                # _release_model above) so it fires even if something in the
                # turns-processing above raises -- the pipeline must never be
                # left stranded in memory just because one job's results were
                # malformed.
                _release_diarization_pipeline(hf_token)

        jobs_progress[job_id]["status_text"] = "Building segments (splitting at speaker changes)..."
        jobs_progress[job_id]["percent"] = 90
        result = []
        seg_index = 0

        for segment in raw_segments:
            segment_words = getattr(segment, "words", None) or []
            groups = group_words_by_speaker(segment_words, turns) if turns else []

            if groups:
                # One row per speaker turn, with exact word timestamps
                for raw_speaker, words in groups:
                    for chunk in chunk_words_by_duration(words, 15.0):
                        speaker = speaker_label_map.get(raw_speaker, "Speaker 1") if raw_speaker else "Speaker 1"
                        text = " ".join((w.word or "").strip() for w in chunk).strip()
                        if not text:
                            continue
                        result.append({
                            "segment_id": f"seg_{seg_index}",
                            "start": round(float(chunk[0].start), 2),
                            "end": round(float(chunk[-1].end), 2),
                            "text": text,
                            "speaker": speaker,
                            "gender": "male",
                            "emotion": "neutral",
                            "arabic_text": "",
                            "words": [
                                {"word": w.word, "start": round(float(w.start), 3), "end": round(float(w.end), 3)}
                                for w in chunk
                            ],
                        })
                        seg_index += 1
            else:
                # No diarization: old behaviour (split long segments by sentences)
                split_parts = split_segment(segment, max_duration=15.0)
                for part in split_parts:
                    speaker = "Speaker 1"
                    part_words = []
                    if turns:
                        raw_speaker = assign_speaker_by_words(segment_words, part["start"], part["end"], turns)
                        if not raw_speaker:
                            raw_speaker = assign_speaker_for_segment(part["start"], part["end"], turns)
                        if raw_speaker:
                            speaker = speaker_label_map.get(raw_speaker, "Speaker 1")
                    for w in segment_words:
                        ws = getattr(w, "start", None)
                        we = getattr(w, "end", None)
                        if ws is None or we is None:
                            continue
                        mid = (float(ws) + float(we)) / 2.0
                        if part["start"] - 0.01 <= mid <= part["end"] + 0.01:
                            part_words.append({"word": w.word, "start": round(float(ws), 3), "end": round(float(we), 3)})
                    result.append({
                        "segment_id": f"seg_{seg_index}",
                        "start": part["start"], "end": part["end"],
                        "text": part["text"], "speaker": speaker, "gender": "male",
                        "emotion": "neutral", "arabic_text": "", "words": part_words,
                    })
                    seg_index += 1

        result = merge_mid_sentence_rows(result)

        # Attach real, audio-measured silence windows to each segment, as a
        # fallback signal for the frontend's auto-split-at-pauses feature.
        # Whisper's own per-word timestamps come from an attention-based DTW
        # alignment that isn't silence-aware -- a genuine ~1-2s pause between
        # phrases can come back with the surrounding words' timestamps
        # nearly touching, hiding the pause from a word-gap-only check. This
        # measures silence directly from the audio instead, so it still
        # catches the pause even when the word timestamps don't show it.
        # Best-effort: a silence-detection hiccup should never break an
        # otherwise-finished transcription.
        try:
            all_silences = detect_silence_gaps(audio_path)
            for seg in result:
                seg_gaps = [
                    g for g in all_silences
                    if g["start"] > seg["start"] + 0.15 and g["end"] < seg["end"] - 0.05
                    and (g["end"] - g["start"]) >= 0.6
                ]
                if seg_gaps:
                    seg["pause_gaps"] = seg_gaps
        except Exception as e:
            print(f"[transcribe] silence-gap detection failed, skipping: {e}")

        # Cross-check Whisper's own word timestamps against real voice-
        # activity data (the same Silero VAD model faster-whisper already
        # uses internally), two ways: (1) a word-to-word gap Whisper's
        # timing claims is empty, but where VAD finds speech-like
        # probability -- the word timestamp may be wrong rather than this
        # being a real pause; (2) a word whose OWN claimed span shows
        # almost no real voice activity at all, right next to an unusually
        # large gap -- meaning it's not just mistimed but anchored to
        # roughly the wrong point in the audio entirely (found on a real
        # clip: Whisper placed a word ~3.4s from where it's actually
        # spoken, next to a long non-speech stretch that confused the
        # alignment). Both write into seg["suspect_gaps"] for manual
        # review, never auto-correct, since a wrong duration here would
        # otherwise silently feed a paid TTS generation. See vad_utils.py
        # for why the second check is deliberately narrow.
        #
        # run_vad_timing_checks decodes the audio and runs the VAD model
        # only once for both checks together (they used to each do this
        # independently, doubling the memory this step needs for no
        # reason). Best-effort, same as the silence-gap block above: never
        # breaks an otherwise-finished transcription. gc.collect() +
        # _trim_memory() afterward for the same reason _release_model()
        # and _release_diarization_pipeline() above call them -- this step
        # decodes the whole audio file into memory, and without an
        # explicit trim, Railway's memory graph keeps showing that as the
        # process's high-water mark even after Python's own GC has freed
        # it, because glibc doesn't hand freed heap back to the OS on its
        # own. This was the very last heavy allocation in the job before
        # this fix, with nothing after it to trim -- worth checking if
        # idle memory looks elevated again after a future change here.
        try:
            run_vad_timing_checks(result, audio_path)
        except Exception as e:
            print(f"[transcribe] VAD timing checks failed, skipping: {e}")
        finally:
            import gc
            gc.collect()
            _trim_memory()

        # This job is done reading every audio file it touched -- drop their
        # cached pages now instead of leaving them in RAM for the full
        # CLEANUP_RETENTION_HOURS window before the file itself is deleted
        # (see _fadvise_dontneed's docstring above for why this matters and
        # why it's safe). The files themselves are untouched -- a later
        # step (e.g. the mix/export step reading background_path) just
        # re-reads from disk as normal if it runs before deletion.
        for p in {input_path, audio_path, extracted_audio, background_path}:
            if p:
                _fadvise_dontneed(p)

        jobs_progress[job_id]["segments"] = result
        jobs_progress[job_id]["status"] = "done"
        jobs_progress[job_id]["percent"] = 100
        jobs_progress[job_id]["full_duration"] = total_duration
        jobs_progress[job_id]["status_text"] = "Done"
        jobs_progress[job_id]["warning"] = warning

    except Exception as e:
        jobs_progress[job_id] = {
            "status": "error", "percent": 0, "error": str(e), "segments": [],
            "full_duration": 0.0, "status_text": "Error", "warning": None,
            "detected_speakers": 0, "is_video": False, "has_background": False,
            "error_trace": traceback.format_exc(),
        }