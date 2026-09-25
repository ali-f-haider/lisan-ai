"""Qwen-Audio-TTS/CosyVoice voice cloning + synthesis -- used ONLY by the
admin "Compare Voice Providers" tool (/api/admin/compare_voice_providers)
so Ali can put Qwen side-by-side with ElevenLabs on the same reference
clip and the same Arabic text, before deciding whether it's worth
building into the real dubbing pipeline. Nothing in the main dubbing
pipeline calls anything in this file.

Deliberately SEPARATE from lipsync_service.py's DashScope integration.
That one uses DASHSCOPE_API_KEY / DASHSCOPE_WORKSPACE_ID, a workspace
confirmed with Alibaba Cloud support to live in eu-central-1, for the
Wan 3.0 video-generation call only. Voice cloning is a completely
different Model Studio API family, and Alibaba's own documentation
(researched Sept 2026, see config.py's comment on DASHSCOPE_SG_API_KEY)
states it's available only in the Beijing and Singapore regions --
eu-central-1 does not support it at all. So this reads its own separate
DASHSCOPE_SG_API_KEY / DASHSCOPE_SG_WORKSPACE_ID, pointed at a workspace
Ali created specifically in ap-southeast-1 (Singapore) for this tool.
If those aren't set, every function below returns a clear "not
configured" error instead of silently trying (and failing) against the
wrong region.

Two different call styles on purpose:
  - create_voice() / delete_voice() are plain HTTP POSTs (same style as
    lipsync_service.py), against Alibaba's documented "customization"
    endpoint -- a normal synchronous REST call, well-documented down to
    the exact request/response JSON.
  - synthesize() uses the official `dashscope` Python SDK instead of a
    hand-rolled implementation. Alibaba's synthesis API for cloned voices
    is a WebSocket duplex protocol (run-task / continue-task / finish-task
    frames), and the full wire format isn't documented anywhere near
    completely enough to reimplement safely from scratch. The SDK is
    Alibaba's own supported client for exactly this call, so it's the
    conservative choice here.
"""
import json
import urllib.request
import urllib.error

from config import DASHSCOPE_SG_API_KEY, DASHSCOPE_SG_WORKSPACE_ID

REGION = "ap-southeast-1"
DEFAULT_MODEL = "qwen-audio-3.0-tts-flash"


def _configured():
    return bool(DASHSCOPE_SG_API_KEY and DASHSCOPE_SG_WORKSPACE_ID)


def _customization_url():
    return f"https://{DASHSCOPE_SG_WORKSPACE_ID}.{REGION}.maas.aliyuncs.com/api/v1/services/audio/tts/customization"


def _customization_call(body):
    req = urllib.request.Request(
        _customization_url(),
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {DASHSCOPE_SG_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def create_voice(audio_url: str, prefix: str = "cmpvoice", target_model: str = DEFAULT_MODEL, language_hints=None) -> dict:
    """Clones a voice from a PUBLICLY REACHABLE audio URL -- Alibaba's
    servers fetch the clip themselves during this call, there's no direct
    file-upload option on this endpoint (unlike ElevenLabs' multipart
    /v1/voices/add). Returns {"ok": True, "voice_id": ...} or
    {"ok": False, "error": ...}, never raises."""
    if not _configured():
        return {"ok": False, "error": "DASHSCOPE_SG_API_KEY / DASHSCOPE_SG_WORKSPACE_ID not set"}
    body = {
        "model": "voice-enrollment",
        "input": {
            "action": "create_voice",
            "target_model": target_model,
            "prefix": (prefix or "cmpvoice")[:10],
            "url": audio_url,
            "language_hints": language_hints or ["ar"],
        },
    }
    try:
        data = _customization_call(body)
        voice_id = (data.get("output") or {}).get("voice_id")
        if not voice_id:
            return {"ok": False, "error": f"No voice_id in Qwen response: {data}"}
        return {"ok": True, "voice_id": voice_id}
    except urllib.error.HTTPError as e:
        try:
            body_txt = e.read().decode(errors="ignore")
        except Exception:
            body_txt = str(e)
        return {"ok": False, "error": f"Qwen voice creation error {e.code}: {body_txt}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def delete_voice(voice_id: str) -> dict:
    """Best-effort cleanup of one cloned voice -- never raises. Called
    after every comparison run (success or failure) so test voices don't
    pile up in the Singapore workspace."""
    if not _configured() or not voice_id:
        return {"ok": False, "error": "not configured or no voice_id"}
    try:
        _customization_call({"model": "voice-enrollment", "input": {"action": "delete_voice", "voice_id": voice_id}})
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def synthesize(voice_id: str, text: str, target_model: str = DEFAULT_MODEL) -> dict:
    """Generates speech from a cloned voice_id via Qwen-Audio-TTS/
    CosyVoice, using the official dashscope SDK (see module docstring for
    why). Returns {"ok": True, "audio_bytes": <mp3 bytes>} or
    {"ok": False, "error": ...}, never raises -- a missing `dashscope`
    package, a wrong region, or an API-side failure all come back the
    same clean way so the admin comparison tool can show a readable
    error instead of a stack trace."""
    if not _configured():
        return {"ok": False, "error": "DASHSCOPE_SG_API_KEY / DASHSCOPE_SG_WORKSPACE_ID not set"}
    try:
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer
    except ImportError:
        return {"ok": False, "error": "The 'dashscope' package isn't installed on the server yet -- it was just added to requirements.txt and needs a fresh deploy to take effect."}
    try:
        dashscope.api_key = DASHSCOPE_SG_API_KEY
        dashscope.base_websocket_api_url = f"wss://{DASHSCOPE_SG_WORKSPACE_ID}.{REGION}.maas.aliyuncs.com/api-ws/v1/inference"
        synthesizer = SpeechSynthesizer(model=target_model, voice=voice_id)
        audio = synthesizer.call(text)
        if not audio:
            return {"ok": False, "error": "Qwen returned no audio data."}
        return {"ok": True, "audio_bytes": audio}
    except Exception as e:
        return {"ok": False, "error": str(e)}
