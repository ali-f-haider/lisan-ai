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
)

# Match the container's 4 vCPUs — prevents thread oversubscription
# (the "calm CPU but 3-4x slower" bug).
torch.set_num_threads(4)

# Loaded once at import. cpu_threads=2 so Whisper shares the CPU
# peacefully with speaker detection running in parallel.
model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE,
                     compute_type=WHISPER_COMPUTE, cpu_threads=2)


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
        segments_gen, info = model.transcribe(
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
        for segment in segments_gen:
            raw_segments.append(segment)
            jobs_progress[job_id]["percent"] = min(15 + int((segment.end / total_duration) * span), 15 + span)

        # ---------- Wait for speaker detection (max 5 min) with live timer ----------
        if diar_thread is not None:
            waited = 0
            while diar_thread.is_alive() and waited < 300:
                diar_thread.join(timeout=30)
                waited += 30
                jobs_progress[job_id]["status_text"] = f"Finishing speaker detection... ({waited}s elapsed)"
                jobs_progress[job_id]["percent"] = min(70 + waited // 10, 85)

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