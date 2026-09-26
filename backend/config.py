import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Bump this by hand whenever a batch of changes ships -- shown in the
# corner of the admin dashboard (/admin) so it's easy to confirm what's
# actually live. Patch (last number) for a fix, minor (middle number) when
# a feature is added, e.g. 1.2.0 -> 1.2.1 for a bugfix-only deploy, or
# 1.2.0 -> 1.3.0 when a new feature ships.
APP_VERSION = "1.23.4"


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
# Separate admin-panel password. If ADMIN_PASSWORD isn't set in the
# environment, admin login falls back to APP_PASSWORD (the previous
# behavior), so nothing breaks until you choose to set a distinct one.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "") or APP_PASSWORD

# Sentry error monitoring: reports unhandled exceptions from the live server
# so problems surface immediately instead of waiting for a user to report a
# bug. Reads SENTRY_DSN from the environment if it's set on Railway (so the
# DSN can be rotated without a code change); falls back to the project's
# current DSN so monitoring works even before that env var is added.
SENTRY_DSN = os.environ.get("SENTRY_DSN", "https://c5cc9438122a8fbbefd5ce57dfcb30b2@o4512124657926144.ingest.de.sentry.io/4512124665856080")

# --- Cloudflare R2 (optional off-site backup of finished dubbing outputs) ---
# All four must be set for backups to run; r2_backup.py no-ops entirely if
# any is missing, so a deployment that hasn't set these up yet is unaffected.
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "")

# --- fal.ai (hosts the VEED Lip Sync 2.0 model used for Step 7 lip-sync) ---
FAL_API_KEY = os.environ.get("FAL_API_KEY", "")

# --- Alibaba Cloud Model Studio -- Wan 3.0 (model string "wan3.0-video"),
# the lip-sync provider replacing VEED, see LIPSYNC_ENABLED below. Called in
# its reference_video + reference_audio dubbing mode:
# https://www.alibabacloud.com/help/en/model-studio/wan3-video-generation-guide
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
# Model Studio's international (outside mainland China) endpoints are
# scoped to a specific Workspace ID as part of the URL itself, not just the
# API key -- found in the Model Studio console (Workspace settings, or
# visible in the console's own URL). Required for the Wan 3.0 lip-sync call
# to work at all; /api/lipsync will fail cleanly with a clear error if this
# is blank.
DASHSCOPE_WORKSPACE_ID = os.environ.get("DASHSCOPE_WORKSPACE_ID", "")
# Region the workspace above actually lives in. Confirmed with Alibaba
# Cloud support directly (Sept 2026) for this workspace: "eu-central-1",
# not "ap-southeast-1" -- the wrong region here is what caused the earlier
# 401 InvalidApiKey error (the workspace-scoped subdomain simply doesn't
# recognize a key issued for a different region). Only change this if your
# workspace moves or support says otherwise.
DASHSCOPE_REGION = os.environ.get("DASHSCOPE_REGION", "eu-central-1")

# --- Alibaba Cloud Model Studio -- a SEPARATE Singapore-region workspace,
# used ONLY by the admin "Compare Voice Providers" tool (Qwen-Audio-TTS
# voice cloning + Arabic synthesis, see qwen_voice_service.py). Deliberately
# NOT the same key/workspace as DASHSCOPE_API_KEY/DASHSCOPE_WORKSPACE_ID
# above -- those are scoped to eu-central-1 and used only for the real
# Wan 3.0 lip-sync pipeline. Voice cloning (creating a cloned voice AND
# synthesizing speech from one) is documented by Alibaba as available only
# in the Beijing and Singapore regions, not eu-central-1 -- confirmed via
# Alibaba's own docs (Sept 2026) after the eu-central-1 workspace turned out
# not to support it at all. This needs its own key + workspace created in
# ap-southeast-1 (Model Studio console). qwen_voice_service.py checks both
# of these are set and returns a clear "not configured" error instead of
# guessing if either is blank.
DASHSCOPE_SG_API_KEY = os.environ.get("DASHSCOPE_SG_API_KEY", "")
DASHSCOPE_SG_WORKSPACE_ID = os.environ.get("DASHSCOPE_SG_WORKSPACE_ID", "")

# Step 7 (lip-sync) now runs on Wan 3.0 (see DASHSCOPE_API_KEY above)
# instead of VEED Lip Sync 2.0 -- VEED's face regeneration visibly altered
# the person (trimmed beard, changed skin) on real footage; Wan 3.0 was
# hand-tested against several lip-sync providers and was the one that held
# up. /api/lipsync checks this flag and refuses the request while it's
# off. Set back to False if Wan 3.0 turns out to be unreliable in practice.
LIPSYNC_ENABLED = True

# Was TEMPORARILY True (2026-09-25) so Ali could check Step 7's progress bar
# / loading-animation text and positioning without spending real money or
# waiting ~15 minutes per real Wan 3.0 call. He confirmed the UI looks right
# (progress bar, loading animation, RUNNING message, Choose File button
# style) and asked to turn real lip-sync back on -- set back to False
# (2026-09-25). While True, /api/lipsync would instead run a fast (~15
# second) simulated progress sequence with no real API call, no credit
# charge, and the original video copied through as a stand-in result. See
# lipsync_service.py's _simulate_lipsync() if this needs to be re-enabled.
LIPSYNC_TEST_MODE = False

# --- Contact form (optional; the /api/contact endpoint still validates
# and rate-limits input without this, it just won't actually deliver mail
# until you set RESEND_API_KEY) ---
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
CONTACT_TO_EMAIL = os.environ.get("CONTACT_TO_EMAIL", "contact@lisanai.org")

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
# Base emotions/styles (kept exactly as before, nothing removed) plus the
# ElevenLabs v3 audio-tag categories that were missing: pacing/delivery speed
# ([slowly], [rushed], [drawn out], [hesitant], [stammering]), volume/softness
# ([softly], [booming]), and additional distinct emotional nuances the v3 docs
# list as their own tags ([sorrowful], [frustrated], [annoyed], [appalled],
# [awe], [regretful], [resigned], [curious], [deadpan], [tired]).
CANONICAL_EMOTIONS = [
    "neutral", "happy", "sad", "angry", "fearful", "surprised", "disgusted",
    "shouting", "whispering", "screaming", "yelling", "crying", "laughing",
    "sarcastic", "seductive", "narrative", "announcer", "conversational",
    "depressed", "anxious", "confident", "indifferent", "excited", "serious",
    "playful", "terrified", "relieved", "thoughtful", "mocking", "pleading",
    "commanding",
    # --- pacing / delivery speed ---
    "slowly", "rushed", "drawn out", "hesitant", "stammering",
    # --- volume / softness ---
    "softly", "booming",
    # --- additional distinct emotional nuances ---
    "sorrowful", "frustrated", "annoyed", "appalled", "awe", "regretful",
    "resigned", "curious", "deadpan", "tired",
]

EMOTION_SYNONYMS = {
    "joy": "happy", "joyful": "happy", "cheerful": "happy", "glad": "happy",
    "sorrow": "sad", "upset": "sad", "melancholy": "sad",
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
    # --- pacing / delivery speed ---
    "slow": "slowly", "drawn-out": "drawn out", "elongated": "drawn out",
    "lingering": "drawn out", "hurried": "rushed", "hasty": "rushed",
    "quick": "rushed", "stammers": "stammering", "stuttering": "stammering",
    "hesitantly": "hesitant",
    # --- volume / softness ---
    "gentle": "softly", "gentle voice": "softly", "hushed": "softly",
    "quiet": "softly", "soft": "softly", "loud": "booming", "thunderous": "booming",
    # --- additional distinct emotional nuances ---
    "grief-stricken": "sorrowful", "grieving": "sorrowful", "mournful": "sorrowful",
    "exasperated": "frustrated", "aggravated": "frustrated",
    "irked": "annoyed", "peeved": "annoyed",
    "shocked and disgusted": "appalled", "outraged": "appalled",
    "in awe": "awe", "amazed and awed": "awe", "wonderstruck": "awe",
    "apologetic": "regretful", "remorseful": "regretful",
    "defeated": "resigned", "accepting": "resigned",
    "inquisitive": "curious", "intrigued": "curious",
    "flat": "deadpan", "flatly": "deadpan", "monotone": "deadpan",
    "exhausted": "tired", "weary": "tired", "worn out": "tired",
}