from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.exceptions import HTTPException
from faster_whisper import WhisperModel
from elevenlabs.client import ElevenLabs
from pathlib import Path
from pydantic import BaseModel
from typing import List, Dict
import uuid
import shutil
import threading
import subprocess
import urllib.request
import urllib.error
import json
import re
import time
import base64
import urllib3
import sys

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = FastAPI()

model = WhisperModel("base", device="cpu", compute_type="int8")

jobs_progress = {}
eleven_client = None
diarization_pipelines = {}

GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-flash-latest"
]

CANONICAL_EMOTIONS = [
    "neutral", "happy", "sad", "angry", "fearful", "surprised", "disgusted",
    "shouting", "whispering", "screaming", "yelling", "crying", "laughing",
    "sarcastic", "seductive", "narrative", "announcer", "conversational",
    "depressed", "anxious", "confident", "indifferent", "excited", "serious",
    "playful", "terrified", "relieved", "thoughtful", "mocking", "pleading",
    "commanding"
]

EMOTION_SYNONYMS = {
    "fear": "fearful", "scared": "fearful", "frightened": "fearful", "scary": "fearful",
    "whisper": "whispering", "whispers": "whispering",
    "shout": "shouting", "shouts": "shouting",
    "yell": "yelling", "yells": "yelling",
    "scream": "screaming", "screams": "screaming",
    "cry": "crying", "cries": "crying",
    "laugh": "laughing", "laughs": "laughing",
    "joy": "happy", "joyful": "happy", "glad": "happy", "cheerful": "happy",
    "sadness": "sad", "sorrow": "sad", "unhappy": "sad",
    "anger": "angry", "mad": "angry", "furious": "angry",
    "surprise": "surprised", "shocked": "surprised", "astonished": "surprised",
    "disgust": "disgusted",
    "calm": "neutral", "flat": "neutral", "none": "neutral",
    "excitement": "excited",
    "anxiety": "anxious", "nervous": "anxious", "worried": "anxious",
    "confidence": "confident", "assertive": "confident",
    "sarcasm": "sarcastic", "ironic": "sarcastic",
    "bored": "indifferent", "indifference": "indifferent",
    "depression": "depressed", "hopeless": "depressed",
    "relief": "relieved",
    "thought": "thoughtful", "thinking": "thoughtful", "reflective": "thoughtful",
    "mock": "mocking", "teasing": "mocking",
    "plead": "pleading", "begging": "pleading", "desperate": "pleading",
    "command": "commanding", "authoritative": "commanding", "strict": "commanding",
    "storytelling": "narrative", "narrator": "narrative",
    "news": "announcer", "professional": "announcer",
    "casual": "conversational",
    "flirty": "seductive", "romantic": "seductive",
    "panic": "terrified", "horror": "terrified",
    "fun": "playful", "joking": "playful",
    "grave": "serious"
}


def normalize_emotion(value):
    if not value:
        return "neutral"
    text = str(value).lower().strip().strip('.!?,')
    if text in CANONICAL_EMOTIONS:
        return text
    if text in EMOTION_SYNONYMS:
        return EMOTION_SYNONYMS[text]
    for emotion in CANONICAL_EMOTIONS:
        if emotion in text:
            return emotion
    for key, mapped in EMOTION_SYNONYMS.items():
        if key in text:
            return mapped
    return "neutral"


def call_gemini(api_key: str, payload: dict, timeout: int = 120):
    last_error = None
    payload_bytes = json.dumps(payload).encode("utf-8")
    for model_name in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        for attempt in range(3):
            request = urllib.request.Request(url, data=payload_bytes, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.load(response), None
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode(errors="ignore")
                except Exception:
                    body = str(e)
                last_error = f"[{model_name}] HTTP {e.code}: {body}"
                if e.code in [429, 503] and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                break
            except Exception as e:
                last_error = f"[{model_name}] {str(e)}"
                if attempt < 2:
                    time.sleep(3)
                    continue
                break
    return None, last_error


class Segment(BaseModel):
    segment_id: str
    start: float
    end: float
    speaker: str = "Speaker 1"
    gender: str = "male"
    emotion: str = "neutral"
    text: str = ""
    arabic_text: str = ""


class GenerateRequest(BaseModel):
    segments: List[Segment]
    elevenlabs_api_key: str
    default_voice_id: str = ""
    speaker_voices: Dict[str, str] = {}
    tempo_mode: str = "excellent"
    duration_mode: str = "exact"
    total_duration: float = 0.0
    cloned_voice_ids: List[str] = []


class VoicesRequest(BaseModel):
    api_key: str


class TranslateRequest(BaseModel):
    segments: List[Segment]
    gemini_api_key: str


class CloneRequest(BaseModel):
    job_id: str
    elevenlabs_api_key: str
    segments: List[Segment]
    speakers_to_clone: List[str] = []


class AnalyzeRequest(BaseModel):
    job_id: str
    segments: List[Segment]


class EmotionRequest(BaseModel):
    job_id: str
    segments: List[Segment]
    gemini_api_key: str


class MergeVideoRequest(BaseModel):
    job_id: str


def get_media_duration(file_path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path)
    ]
    result = subprocess.check_output(cmd).decode().strip()
    return float(result)


def run_ffmpeg(cmd):
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.decode(errors="ignore") if e.stderr else str(e)
        raise Exception(error_message)


def extract_audio_from_video(video_path: str, output_audio_path: str):
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "44100",
        "-ac", "2",
        str(output_audio_path)
    ]
    run_ffmpeg(cmd)


def separate_vocals(input_audio_path: str, output_dir: str):
    cmd = [
        sys.executable, "-m", "demucs.separate",
        "--two-stems", "vocals",
        "-o", str(output_dir),
        str(input_audio_path)
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        error_message = e.stderr if e.stderr else str(e)
        raise Exception(f"Demucs vocal separation failed: {error_message}")

    input_name = Path(input_audio_path).stem
    vocals_path = Path(output_dir) / "htdemucs" / input_name / "vocals.wav"
    background_path = Path(output_dir) / "htdemucs" / input_name / "no_vocals.wav"

    if not vocals_path.exists():
        htdemucs_dir = Path(output_dir) / "htdemucs"
        if htdemucs_dir.exists():
            for subfolder in htdemucs_dir.iterdir():
                if subfolder.is_dir():
                    vp = subfolder / "vocals.wav"
                    bp = subfolder / "no_vocals.wav"
                    if vp.exists():
                        vocals_path = vp
                        background_path = bp
                        break
    return str(vocals_path), str(background_path)


def normalize_audio_for_diarization(input_path: str) -> str:
    input_file = Path(input_path)
    normalized_path = input_file.with_name(input_file.stem + "_normalized.wav")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_file),
        "-ac", "1",
        "-ar", "16000",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        str(normalized_path)
    ]
    run_ffmpeg(cmd)
    return str(normalized_path)


def split_segment(segment, max_duration=15.0):
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
        text1 = " ".join(words[:mid_word])
        text2 = " ".join(words[mid_word:])
        return [
            {"start": start, "end": mid, "text": text1},
            {"start": mid, "end": end, "text": text2}
        ]
    results = []
    total_chars = sum(len(sentence) for sentence in sentences if sentence.strip())
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
        import torch
    except Exception as e:
        raise Exception(f"Missing dependency: {e}. Run: pip install soundfile")

    global diarization_pipelines
    pipeline = diarization_pipelines.get(hf_token)
    if pipeline is None:
        pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=hf_token)
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
        word_start = getattr(word, "start", None)
        word_end = getattr(word, "end", None)
        if word_start is None or word_end is None:
            continue
        word_mid = (float(word_start) + float(word_end)) / 2.0
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

    smoothed = []
    for i, (spk, words) in enumerate(groups):
        if len(words) == 1 and i > 0 and i < len(groups) - 1:
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
    """Join consecutive same-speaker rows when the first does NOT end with . ! ?
    and the two are contiguous (gap <= 0.25s). Fixes sentences cut in half."""
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
            "is_video": False, "has_background": False
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

        if hf_token:
            try:
                jobs_progress[job_id]["status_text"] = "Detecting speakers..."
                jobs_progress[job_id]["percent"] = 15
                turns = get_speaker_turns(audio_path, hf_token, speaker_count)
                for turn in sorted(turns, key=lambda x: x["start"]):
                    raw_speaker = turn["speaker"]
                    if raw_speaker not in speaker_label_map:
                        speaker_label_map[raw_speaker] = f"Speaker {len(speaker_label_map) + 1}"
                jobs_progress[job_id]["detected_speakers"] = len(speaker_label_map)
                if speaker_count and len(speaker_label_map) < int(speaker_count):
                    warning = f"Requested {speaker_count} speakers, but only {len(speaker_label_map)} were detected."
            except Exception as e:
                warning = str(e)

        jobs_progress[job_id]["status_text"] = "Transcribing audio..."
        jobs_progress[job_id]["percent"] = 25 if hf_token else 15

        # NEW: VAD filtering + no conditioning on previous text = fewer hallucinations
        # and better boundaries on music/noise intros.
        segments_gen, info = model.transcribe(
            str(audio_path),
            beam_size=5,
            language="en",
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False
        )
        total_duration = float(info.duration) if info.duration else 1.0

        raw_segments = []
        base_percent = 25 if hf_token else 15
        for segment in segments_gen:
            raw_segments.append(segment)
            percent = base_percent + int((segment.end / total_duration) * (95 - base_percent))
            jobs_progress[job_id]["percent"] = min(percent, 95)

        jobs_progress[job_id]["status_text"] = "Building segments (splitting at speaker changes)..."
        result = []
        seg_index = 0

        for segment in raw_segments:
            segment_words = getattr(segment, "words", None) or []
            groups = group_words_by_speaker(segment_words, turns) if turns else []

            if groups:
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
                            ]
                        })
                        seg_index += 1
            else:
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
                        "segment_id": f"seg_{seg_index}", "start": part["start"], "end": part["end"],
                        "text": part["text"], "speaker": speaker, "gender": "male",
                        "emotion": "neutral", "arabic_text": "", "words": part_words
                    })
                    seg_index += 1

        # NEW: merge same-speaker rows that were cut mid-sentence
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
            "full_duration": 0.0, "status_text": "Error", "warning": None, "detected_speakers": 0,
            "is_video": False, "has_background": False
        }


def generate_worker(req: GenerateRequest):
    global eleven_client
    try:
        jobs_progress["generate"] = {"status": "processing", "percent": 0, "result": None, "error": None}
        api_key = req.elevenlabs_api_key.strip()
        if not api_key:
            raise Exception("Missing ElevenLabs API key.")

        eleven_client = ElevenLabs(api_key=api_key)
        total_segments = len(req.segments)
        if total_segments == 0:
            raise Exception("No segments found.")

        sorted_segments = sorted(req.segments, key=lambda s: s.start)
        generated_files = []

        for i, seg in enumerate(sorted_segments):
            if not seg.arabic_text.strip():
                continue

            voice_id = req.speaker_voices.get(seg.speaker, "").strip()
            if not voice_id:
                voice_id = req.default_voice_id.strip()
            if not voice_id:
                raise Exception(f"No voice assigned for speaker: {seg.speaker}")

            target_duration = max(seg.end - seg.start, 0.5)
            tts_text = f"[{seg.emotion}] {seg.arabic_text}"

            response = eleven_client.text_to_speech.convert(text=tts_text, voice_id=voice_id, model_id="eleven_v3")
            if isinstance(response, bytes):
                audio_bytes = response
            else:
                audio_bytes = b"".join(chunk for chunk in response if chunk)

            raw_filename = f"{seg.segment_id}_raw.mp3"
            raw_path = OUTPUT_DIR / raw_filename
            raw_path.write_bytes(audio_bytes)

            actual_duration = get_media_duration(raw_path)
            if actual_duration <= 0:
                actual_duration = target_duration

            required_tempo = actual_duration / target_duration
            if req.tempo_mode == "excellent":
                min_tempo, max_tempo = 0.95, 1.10
            elif req.tempo_mode == "good":
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

            stretched_filename = f"{seg.segment_id}_stretched.mp3"
            stretched_path = OUTPUT_DIR / stretched_filename

            if abs(tempo - 1.0) > 0.02:
                ffmpeg_cmd = ["ffmpeg", "-y", "-i", str(raw_path), "-filter:a", f"atempo={tempo:.6f}", str(stretched_path)]
                run_ffmpeg(ffmpeg_cmd)
            else:
                shutil.copy(raw_path, stretched_path)

            stretched_duration = get_media_duration(stretched_path)
            if stretched_duration <= 0:
                stretched_duration = target_duration

            generated_files.append({
                "file": stretched_filename,
                "start": seg.start,
                "end": seg.end,
                "speaker": seg.speaker,
                "duration": stretched_duration,
                "tempo_warning": needs_warning
            })
            jobs_progress["generate"]["percent"] = int(((i + 1) / total_segments) * 90)

        if not generated_files:
            raise Exception("No Arabic text found.")
        jobs_progress["generate"]["percent"] = 92

        generated_files.sort(key=lambda item: item["start"])
        max_segment_end = max(item["end"] for item in generated_files)
        max_played_end = max(item["start"] + item["duration"] for item in generated_files)

        if req.duration_mode == "extend":
            final_duration = max(req.total_duration, max_played_end, max_segment_end)
        else:
            if req.total_duration > 0:
                final_duration = req.total_duration
            else:
                final_duration = max(max_segment_end, max_played_end)

        adjusted_files = []
        cut_count = 0
        for i, item in enumerate(generated_files):
            if i + 1 < len(generated_files):
                allowed_end = generated_files[i + 1]["start"] - 0.02
            else:
                allowed_end = final_duration
            allowed_duration = allowed_end - item["start"]
            if allowed_duration <= 0.05:
                continue
            if item["duration"] > allowed_duration + 0.05:
                cut_count += 1
            item["allowed_duration"] = min(item["duration"], allowed_duration)
            adjusted_files.append(item)

        if not adjusted_files:
            raise Exception("No generated segments fit.")

        inputs = ["-f", "lavfi", "-t", str(final_duration), "-i", "anullsrc=r=44100:cl=stereo"]
        filter_parts = []
        for idx, item in enumerate(adjusted_files):
            input_index = idx + 1
            inputs.extend(["-i", str(OUTPUT_DIR / item["file"])])
            delay_ms = int(item["start"] * 1000)
            allowed = max(item["allowed_duration"], 0.05)
            if item["duration"] > allowed + 0.05:
                fade_start = max(0, allowed - 0.2)
                filter_parts.append(
                    f"[{input_index}]aformat=channel_layouts=stereo,"
                    f"atrim=0:{allowed:.3f},"
                    f"afade=t=out:st={fade_start:.3f}:d=0.2,"
                    f"asetpts=PTS-STARTPTS,"
                    f"adelay={delay_ms}|{delay_ms},"
                    f"apad[a{idx}]"
                )
            else:
                filter_parts.append(
                    f"[{input_index}]aformat=channel_layouts=stereo,"
                    f"atrim=0:{allowed:.3f},"
                    f"asetpts=PTS-STARTPTS,"
                    f"adelay={delay_ms}|{delay_ms},"
                    f"apad[a{idx}]"
                )

        mix_inputs = "".join([f"[a{i}]" for i in range(len(adjusted_files))])
        filter_parts.append(f"[0]{mix_inputs}amix=inputs={len(adjusted_files) + 1}:duration=first:normalize=0[out]")
        filter_complex = ";".join(filter_parts)

        output_file = OUTPUT_DIR / "final_dubbed.mp3"
        ffmpeg_cmd = ["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[out]", "-t", str(final_duration), str(output_file)]
        jobs_progress["generate"]["percent"] = 97
        run_ffmpeg(ffmpeg_cmd)

        if req.cloned_voice_ids:
            for vid in req.cloned_voice_ids:
                try:
                    eleven_client.voices.delete(voice_id=vid)
                except Exception:
                    pass

        warning_count = sum(1 for f in adjusted_files if f.get("tempo_warning"))
        result = {
            "status": "success", "output_folder": str(OUTPUT_DIR), "final_file": str(output_file),
            "segments_generated": len(adjusted_files), "tempo_warnings": warning_count,
            "duration_cuts": cut_count, "final_duration": round(final_duration, 2)
        }
        jobs_progress["generate"]["status"] = "done"
        jobs_progress["generate"]["percent"] = 100
        jobs_progress["generate"]["result"] = result
        jobs_progress["generate"]["error"] = None

    except Exception as e:
        jobs_progress["generate"] = {"status": "error", "percent": 0, "error": str(e), "result": None}


def detect_emotions_worker(job_id: str, input_path: str, api_key: str, segments: list):
    try:
        jobs_progress[f"emotions_{job_id}"] = {
            "status": "processing", "percent": 0, "current": 0,
            "total": len(segments), "emotions": {}, "errors": []
        }
        emotions_result = {}
        errors = []

        for i, seg in enumerate(segments):
            try:
                jobs_progress[f"emotions_{job_id}"]["current"] = i + 1
                jobs_progress[f"emotions_{job_id}"]["percent"] = int(((i + 1) / len(segments)) * 100)
                if i > 0:
                    time.sleep(0.5)

                segment_file = OUTPUT_DIR / f"emotion_{job_id}_{seg.segment_id}.mp3"
                duration = seg.end - seg.start
                if duration < 0.3:
                    emotions_result[seg.segment_id] = "neutral"
                    jobs_progress[f"emotions_{job_id}"]["emotions"] = emotions_result.copy()
                    continue

                cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(seg.start),
                    "-t", str(duration),
                    "-i", str(input_path),
                    "-ar", "16000",
                    "-ac", "1",
                    str(segment_file)
                ]
                run_ffmpeg(cmd)

                with open(segment_file, "rb") as f:
                    audio_b64 = base64.b64encode(f.read()).decode()

                prompt = (
                    "Listen to this audio clip carefully. "
                    "What emotion or speaking style is the speaker expressing in their voice tone? "
                    "Reply with ONLY one word from this exact list: "
                    + ", ".join(CANONICAL_EMOTIONS) +
                    ". Do not add any other text."
                )
                payload = {
                    "contents": [{
                        "parts": [
                            {"inline_data": {"mime_type": "audio/mpeg", "data": audio_b64}},
                            {"text": prompt}
                        ]
                    }]
                }

                data, err = call_gemini(api_key, payload, timeout=60)

                if data is None:
                    errors.append(f"{seg.segment_id}: {err}")
                    emotions_result[seg.segment_id] = "neutral"
                    jobs_progress[f"emotions_{job_id}"]["emotions"] = emotions_result.copy()
                    jobs_progress[f"emotions_{job_id}"]["errors"] = errors
                    continue

                result_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                detected_emotion = normalize_emotion(result_text)

                emotions_result[seg.segment_id] = detected_emotion
                jobs_progress[f"emotions_{job_id}"]["emotions"] = emotions_result.copy()

                try:
                    segment_file.unlink()
                except Exception:
                    pass

            except Exception as e:
                errors.append(f"{seg.segment_id}: {str(e)}")
                emotions_result[seg.segment_id] = "neutral"
                jobs_progress[f"emotions_{job_id}"]["emotions"] = emotions_result.copy()
                jobs_progress[f"emotions_{job_id}"]["errors"] = errors

        jobs_progress[f"emotions_{job_id}"]["status"] = "done"
        jobs_progress[f"emotions_{job_id}"]["percent"] = 100
        jobs_progress[f"emotions_{job_id}"]["emotions"] = emotions_result
        jobs_progress[f"emotions_{job_id}"]["errors"] = errors

    except Exception as e:
        jobs_progress[f"emotions_{job_id}"] = {
            "status": "error", "percent": 0, "error": str(e), "emotions": {}, "errors": []
        }


@app.get("/", response_class=HTMLResponse)
def home():
    return (BASE_DIR / "index.html").read_text(encoding="utf-8")


@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...), speaker_count: str = Form(""), hf_token: str = Form("")):
    job_id = str(uuid.uuid4())
    file_ext = Path(file.filename).suffix.lower()
    is_video = file_ext in ['.mp4', '.avi', '.mkv', '.mov', '.webm']
    if is_video:
        input_path = UPLOAD_DIR / f"{job_id}{file_ext}"
    else:
        input_path = UPLOAD_DIR / f"{job_id}.mp3"

    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    speaker_count_value = None
    if speaker_count.strip():
        try:
            speaker_count_value = max(1, int(speaker_count.strip()))
        except Exception:
            speaker_count_value = None

    jobs_progress[job_id] = {
        "status": "starting", "percent": 0, "segments": [], "full_duration": 0.0,
        "status_text": "Starting...", "warning": None, "detected_speakers": 0,
        "is_video": is_video, "has_background": False
    }
    thread = threading.Thread(target=transcribe_worker, args=(job_id, str(input_path), hf_token.strip(), speaker_count_value))
    thread.daemon = True
    thread.start()
    return {"job_id": job_id}


@app.get("/api/progress/{job_id}")
def get_progress(job_id: str):
    return jobs_progress.get(job_id, {
        "status": "not_found", "percent": 0, "segments": [], "full_duration": 0.0,
        "status_text": "", "warning": None, "detected_speakers": 0,
        "is_video": False, "has_background": False
    })


@app.get("/api/source/{job_id}")
def source_audio(job_id: str):
    """Serves the best available source audio for per-row preview:
    clean vocals first, then the uploaded/extracted audio."""
    candidates = [
        UPLOAD_DIR / f"{job_id}_separated" / "htdemucs" / f"{job_id}_audio" / "vocals.wav",
        UPLOAD_DIR / f"{job_id}.mp3",
        UPLOAD_DIR / f"{job_id}_audio.wav",
    ]
    for ext in ['.mp4', '.mov', '.webm', '.avi', '.mkv']:
        candidates.append(UPLOAD_DIR / f"{job_id}{ext}")

    for path in candidates:
        if path.exists():
            suffix = path.suffix.lower()
            if suffix == ".wav":
                media = "audio/wav"
            elif suffix == ".mp3":
                media = "audio/mpeg"
            elif suffix in [".mp4", ".mov", ".webm"]:
                media = "video/mp4"
            else:
                media = "application/octet-stream"
            return FileResponse(path=path, media_type=media, filename=path.name)
    raise HTTPException(status_code=404, detail="Source audio not found")


@app.post("/api/voices")
async def get_voices(req: VoicesRequest):
    try:
        api_key = req.api_key.strip()
        request = urllib.request.Request("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": api_key})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        voices = []
        for voice in data.get("voices", []):
            labels = voice.get("labels") or {}
            voices.append({"voice_id": voice.get("voice_id"), "name": voice.get("name", "Unnamed"), "gender": labels.get("gender", ""), "category": voice.get("category", "")})
        return {"voices": voices}
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode(errors="ignore")
        except Exception:
            error_body = str(e)
        return {"error": f"ElevenLabs API error {e.code}: {error_body}"}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/translate")
async def translate_segments(req: TranslateRequest):
    api_key = req.gemini_api_key.strip()
    if not api_key:
        return {"error": "Missing Gemini API key."}
    if not req.segments:
        return {"error": "No segments to translate."}

    segments_for_prompt = []
    for seg in req.segments:
        segments_for_prompt.append({
            "segment_id": seg.segment_id, "start": seg.start, "end": seg.end,
            "duration": round(seg.end - seg.start, 2), "english_text": seg.text, "speaker": seg.speaker
        })

    prompt = f"""You are a professional Arabic translator and voice dubbing specialist.
Translate the following English audio segments into Modern Standard Arabic (MSA).
CRITICAL RULES FOR TIMING:
Arabic takes about 20% longer to speak than English.
You MUST keep the translation very short for short segments.
Rule of thumb: Maximum 2 to 2.5 words per second of duration.
Example: If duration is 1.5 seconds, use maximum 3 words. If 2 seconds, max 4-5 words.
Do not add filler words. Be extremely concise to fit the time limit.
OTHER RULES:
Translate into clear, natural MSA Arabic suitable for voice dubbing.
Add full Tashkeel (Arabic diacritics) to every word.
Detect the emotion or speaking style of each line and use ONLY one tag from this exact list:
{', '.join(CANONICAL_EMOTIONS)}
Preserve the core meaning, but prioritize fitting the time limit.
Return ONLY valid JSON. No explanations.
Return JSON array:
[
{{"segment_id": "...", "arabic_text": "Arabic text with Tashkeel", "emotion": "neutral"}}
]
Segments:
{json.dumps(segments_for_prompt, ensure_ascii=False)}"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 8192, "responseMimeType": "application/json"}
    }

    data, err = call_gemini(api_key, payload, timeout=120)
    if data is None:
        return {"error": f"Gemini API failed on all models. Last error: {err}"}

    result_text = data["candidates"][0]["content"]["parts"][0]["text"]
    result_text = result_text.strip()
    if result_text.startswith("```"):
        result_text = result_text.split("\n", 1)[1]
    if result_text.endswith("```"):
        result_text = result_text.rsplit("```", 1)[0]
    result_text = result_text.strip()
    translated_segments = json.loads(result_text)

    for item in translated_segments:
        if isinstance(item, dict):
            item["emotion"] = normalize_emotion(item.get("emotion", ""))

    return {"status": "success", "translated_segments": translated_segments}


@app.post("/api/analyze_speakers")
async def analyze_speakers(req: AnalyzeRequest):
    job_id = req.job_id
    audio_path = None
    for ext in ['.mp3', '.mp4', '.avi', '.mkv', '.mov', '.webm']:
        candidate = UPLOAD_DIR / f"{job_id}{ext}"
        if candidate.exists():
            audio_path = candidate
            break
    vocals_path = UPLOAD_DIR / f"{job_id}_separated" / "htdemucs" / f"{job_id}_audio" / "vocals.wav"
    if vocals_path.exists():
        audio_path = vocals_path
    if not audio_path:
        return {"error": "Audio file not found. Please transcribe again."}

    speakers = list(set(s.speaker for s in req.segments if s.text.strip()))
    analysis = []
    for speaker in speakers:
        speaker_segs = [s for s in req.segments if s.speaker == speaker]
        total_time = sum(s.end - s.start for s in speaker_segs)
        if total_time >= 10.0:
            status = "good"
            message = f"{total_time:.1f}s available"
        elif total_time >= 3.0:
            status = "warning"
            message = f"{total_time:.1f}s available (quality may be reduced)"
        else:
            status = "poor"
            message = f"{total_time:.1f}s available (quality will likely be very poor)"
        analysis.append({"speaker": speaker, "total_time": round(total_time, 1), "status": status, "message": message})
    analysis.sort(key=lambda x: x["speaker"])
    return {"analysis": analysis}


@app.post("/api/detect_emotions")
async def detect_emotions(req: EmotionRequest):
    api_key = req.gemini_api_key.strip()
    job_id = req.job_id

    audio_path = None
    for ext in ['.mp3', '.mp4', '.avi', '.mkv', '.mov', '.webm']:
        candidate = UPLOAD_DIR / f"{job_id}{ext}"
        if candidate.exists():
            audio_path = candidate
            break
    vocals_path = UPLOAD_DIR / f"{job_id}_separated" / "htdemucs" / f"{job_id}_audio" / "vocals.wav"
    if vocals_path.exists():
        audio_path = vocals_path
    if not audio_path:
        return {"error": "Audio file not found. Please transcribe again."}
    if not api_key:
        return {"error": "Missing Gemini API key."}
    if not req.segments:
        return {"error": "No segments found."}

    jobs_progress[f"emotions_{job_id}"] = {
        "status": "starting", "percent": 0, "current": 0,
        "total": len(req.segments), "emotions": {}, "errors": []
    }
    thread = threading.Thread(target=detect_emotions_worker, args=(job_id, str(audio_path), api_key, req.segments))
    thread.daemon = True
    thread.start()
    return {"job_id": job_id}


@app.get("/api/progress/emotions/{job_id}")
def get_emotions_progress(job_id: str):
    return jobs_progress.get(f"emotions_{job_id}", {
        "status": "not_found", "percent": 0, "current": 0,
        "total": 0, "emotions": {}, "errors": []
    })


@app.post("/api/clone")
async def clone_voices(req: CloneRequest):
    api_key = req.elevenlabs_api_key.strip()
    job_id = req.job_id

    audio_path = None
    for ext in ['.mp3', '.mp4', '.avi', '.mkv', '.mov', '.webm']:
        candidate = UPLOAD_DIR / f"{job_id}{ext}"
        if candidate.exists():
            audio_path = candidate
            break
    vocals_path = UPLOAD_DIR / f"{job_id}_separated" / "htdemucs" / f"{job_id}_audio" / "vocals.wav"
    if vocals_path.exists():
        audio_path = vocals_path
    if not audio_path:
        return {"error": "Audio file not found. Please transcribe again."}

    cloned_voices = {}
    speakers = list(set(s.speaker for s in req.segments if s.text.strip()))
    if req.speakers_to_clone:
        speakers = [s for s in speakers if s in req.speakers_to_clone]

    for speaker in speakers:
        try:
            safe_speaker = "".join(c for c in speaker if c.isalnum()).strip()
            if not safe_speaker:
                safe_speaker = "speaker"
            speaker_segs = [s for s in req.segments if s.speaker == speaker]
            speaker_segs.sort(key=lambda s: s.end - s.start, reverse=True)
            cut_files = []
            total_time = 0
            for seg in speaker_segs:
                if total_time >= 20.0:
                    break
                dur = seg.end - seg.start
                if dur < 0.5:
                    continue
                cut_file = OUTPUT_DIR / f"clone_{job_id}_{safe_speaker}_{seg.segment_id}.mp3"
                cmd = ["ffmpeg", "-y", "-ss", str(seg.start), "-t", str(dur), "-i", str(audio_path), "-ar", "44100", "-ac", "1", str(cut_file)]
                run_ffmpeg(cmd)
                cut_files.append(cut_file)
                total_time += dur

            if not cut_files:
                cloned_voices[speaker] = f"ERROR: No audio found for {speaker}."
                continue

            concat_file = OUTPUT_DIR / f"clone_{job_id}_{safe_speaker}.mp3"
            list_file = OUTPUT_DIR / f"clone_{job_id}_{safe_speaker}.txt"
            with open(list_file, "w", encoding="utf-8") as f:
                for cf in cut_files:
                    safe_path = str(cf).replace("\\", "/").replace("'", "'\\''")
                    f.write(f"file '{safe_path}'\n")
            cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(concat_file)]
            run_ffmpeg(cmd)

            with open(concat_file, "rb") as f:
                file_data = f.read()
            http = urllib3.PoolManager()
            response = http.request(
                'POST',
                'https://api.elevenlabs.io/v1/voices/add',
                headers={'xi-api-key': api_key},
                fields={
                    'name': f"Cloned_{speaker}",
                    'files': (f"{safe_speaker}.mp3", file_data, 'audio/mpeg'),
                    'labels': '{}'
                }
            )
            if response.status == 200:
                data = json.loads(response.data.decode())
                cloned_voices[speaker] = data['voice_id']
            else:
                raise Exception(f"API error {response.status}: {response.data.decode()}")

            for cf in cut_files:
                try:
                    cf.unlink()
                except Exception:
                    pass
            try:
                list_file.unlink()
            except Exception:
                pass
            try:
                concat_file.unlink()
            except Exception:
                pass
        except Exception as e:
            cloned_voices[speaker] = f"ERROR: {str(e)}"

    warnings = []
    for speaker in cloned_voices:
        if not cloned_voices[speaker].startswith("ERROR"):
            speaker_segs = [s for s in req.segments if s.speaker == speaker]
            total_available = sum(s.end - s.start for s in speaker_segs)
            if total_available < 3.0:
                warnings.append(f"{speaker}: Only {total_available:.1f}s of audio available. Clone quality will likely be very poor.")
            elif total_available < 10.0:
                warnings.append(f"{speaker}: Only {total_available:.1f}s of audio available. Clone quality may be reduced.")

    return {"status": "success", "cloned_voices": cloned_voices, "warnings": warnings}


@app.post("/api/merge_video")
async def merge_video(req: MergeVideoRequest):
    try:
        job_id = req.job_id
        video_path = None
        for ext in ['.mp4', '.avi', '.mkv', '.mov', '.webm']:
            candidate = UPLOAD_DIR / f"{job_id}{ext}"
            if candidate.exists():
                video_path = candidate
                break
        if not video_path:
            return {"error": "Original video file not found."}

        dubbed_audio_path = OUTPUT_DIR / "final_dubbed.mp3"
        if not dubbed_audio_path.exists():
            return {"error": "Dubbed audio not found. Please generate audio first."}

        background_path = None
        possible_background = UPLOAD_DIR / f"{job_id}_separated" / "htdemucs" / f"{job_id}_audio" / "no_vocals.wav"
        if possible_background.exists():
            background_path = possible_background

        video_duration = get_media_duration(video_path)

        if background_path:
            mixed_audio_path = OUTPUT_DIR / f"mixed_audio_{job_id}.wav"
            cmd = [
                "ffmpeg", "-y",
                "-i", str(dubbed_audio_path),
                "-i", str(background_path),
                "-filter_complex",
                "[0:a]volume=1.0[dubbed];[1:a]volume=0.8[bg];[dubbed][bg]amix=inputs=2:duration=first:normalize=0[out]",
                "-map", "[out]",
                "-t", str(video_duration),
                str(mixed_audio_path)
            ]
            run_ffmpeg(cmd)
            final_audio_path = mixed_audio_path
        else:
            final_audio_path = dubbed_audio_path

        output_video_path = OUTPUT_DIR / "final_dubbed_video.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(final_audio_path),
            "-c:v", "copy",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-t", str(video_duration),
            str(output_video_path)
        ]
        run_ffmpeg(cmd)

        if background_path and mixed_audio_path.exists():
            try:
                mixed_audio_path.unlink()
            except Exception:
                pass

        return {
            "status": "success",
            "video_file": str(output_video_path),
            "audio_file": str(dubbed_audio_path),
            "has_background": background_path is not None
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/generate")
async def generate(req: GenerateRequest):
    current = jobs_progress.get("generate")
    if current and current.get("status") in ["starting", "processing"]:
        return {"error": "Generation is already running. Please wait."}
    jobs_progress["generate"] = {"status": "starting", "percent": 0, "result": None, "error": None}
    thread = threading.Thread(target=generate_worker, args=(req,))
    thread.daemon = True
    thread.start()
    return {"job_id": "generate"}


@app.get("/api/download/{filename}")
def download(filename: str):
    file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = "audio/mpeg"
    if filename.endswith(".mp4"):
        media_type = "video/mp4"
    return FileResponse(path=file_path, media_type=media_type, filename=filename)