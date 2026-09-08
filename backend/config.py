import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR)))
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Server-owned API keys (from .env) ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
HF_TOKEN = os.environ.get("HF_TOKEN", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

# --- Local processing settings ---
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8")

# --- Gemini model lists (text + TTS) ---
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-preview-05-20",
    "gemini-2.0-flash",
    "gemini-flash-latest",
]

# --- Emotion handling ---
CANONICAL_EMOTIONS = [
    "neutral", "happy", "sad", "angry", "fearful", "surprised", "disgusted",
    "shouting", "whispering", "screaming", "yelling", "crying", "laughing",
    "sarcastic", "seductive", "narrative", "announcer", "conversational",
    "depressed", "anxious", "confident", "indifferent", "excited", "serious",
    "playful", "terrified", "relieved", "thoughtful", "mocking", "pleading",
    "commanding",
]

EMOTION_SYNONYMS = {
    "joy": "happy", "joyful": "happy", "cheerful": "happy", "glad": "happy",
    "sorrow": "sad", "sorrowful": "sad", "upset": "sad", "melancholy": "sad",
    "mad": "angry", "furious": "angry", "irritated": "angry", "rage": "angry",
    "scared": "fearful", "afraid": "fearful", "frightened": "fearful", "terrified": "terrified",
    "shocked": "surprised", "astonished": "surprised", "amazed": "surprised",
    "disgust": "disgusted", "revolted": "disgusted",
    "yell": "yelling", "shout": "shouting", "scream": "screaming", "whisper": "whispering",
    "weeping": "crying", "sobbing": "crying",
    "laugh": "laughing", "amused": "laughing",
    "sarcastic": "sarcastic", "ironic": "sarcastic", "mocking": "mocking",
    "flirty": "seductive", "sultry": "seductive",
    "storytelling": "narrative", "narration": "narrative",
    "news": "announcer", "broadcast": "announcer",
    "casual": "conversational", "chatty": "conversational",
    "gloomy": "depressed", "down": "depressed",
    "nervous": "anxious", "worried": "anxious", "uneasy": "anxious",
    "self-assured": "confident", "assured": "confident", "calm": "confident",
    "apathetic": "indifferent", "detached": "indifferent",
    "enthusiastic": "excited", "thrilled": "excited",
    "grave": "serious", "solemn": "serious",
    "fun": "playful", "teasing": "playful", "joking": "playful",
    "horrified": "terrified", "panicked": "terrified",
    "comforted": "relieved", "eased": "relieved",
    "reflective": "thoughtful", "pensive": "thoughtful",
    "begging": "pleading", "imploring": "pleading",
    "authoritative": "commanding", "bossy": "commanding",
}