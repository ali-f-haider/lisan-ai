from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

VIDEO_EXTENSIONS = ['.mp4', '.avi', '.mkv', '.mov', '.webm']

WHISPER_MODEL = "base"
WHISPER_DEVICE = "cpu"
WHISPER_COMPUTE = "int8"

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

# Pricing / usage constants
GEMINI_RATE_IN_PER_M = 0.30
GEMINI_RATE_OUT_PER_M = 2.50
ELEVEN_CREDITS_PER_CHAR = 1

SYNC_MODELS = ["lipsync-2", "lipsync-2-pro", "sync-3"]
