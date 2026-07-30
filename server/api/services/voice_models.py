"""Central model capability registry for Bailian voice features."""

from __future__ import annotations


LATEST_TTS_VOICE_MODEL = "qwen-audio-3.0-tts-flash"
LATEST_REALTIME_VOICE_MODEL = "qwen-audio-3.0-realtime-plus"

SUPPORTED_TTS_VOICE_MODELS = frozenset(
    {
        "qwen-audio-3.0-tts-flash",
        "qwen-audio-3.0-tts-plus",
        "cosyvoice-v3.5-flash",
        "cosyvoice-v3.5-plus",
        "cosyvoice-v3-flash",
        "cosyvoice-v3-plus",
        "cosyvoice-v2",
    }
)

SUPPORTED_REALTIME_VOICE_MODELS = frozenset(
    {
        "qwen-audio-3.0-realtime-plus",
        "qwen-audio-3.0-realtime-flash",
    }
)

SUPPORTED_VOICE_ENROLLMENT_MODELS = (
    SUPPORTED_TTS_VOICE_MODELS | SUPPORTED_REALTIME_VOICE_MODELS
)


def is_tts_voice_model(model: str | None) -> bool:
    return bool(model and model in SUPPORTED_TTS_VOICE_MODELS)


def is_realtime_voice_model(model: str | None) -> bool:
    return bool(model and model in SUPPORTED_REALTIME_VOICE_MODELS)
