"""Database-backed configuration for Bailian full-duplex voice."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from api.services.runtime_config import (
    get_runtime_app_config,
    get_runtime_config_section,
)
from api.services.voice_models import (
    LATEST_REALTIME_VOICE_MODEL,
    SUPPORTED_REALTIME_VOICE_MODELS,
)

from .contracts import RealtimeVoiceConfig, SYSTEM_VOICES, TurnDetectionMode


class RealtimeVoiceConfigurationError(RuntimeError):
    pass


_WORKSPACE_DOMAINS = {
    "beijing": "cn-beijing.maas.aliyuncs.com",
    "singapore": "ap-southeast-1.maas.aliyuncs.com",
    "frankfurt": "eu-central-1.maas.aliyuncs.com",
}

_REGION_ALIASES = {
    "cn-beijing": "beijing",
    "china": "beijing",
    "bj": "beijing",
    "ap-southeast-1": "singapore",
    "sg": "singapore",
    "intl": "singapore",
    "eu-central-1": "frankfurt",
    "eu": "frankfurt",
}

_LEGACY_HOSTS = {
    "beijing": "dashscope.aliyuncs.com",
    "singapore": "dashscope-intl.aliyuncs.com",
}


def _normalise_region(value: object) -> str:
    region = str(value or "beijing").strip().lower().replace("_", "-")
    return _REGION_ALIASES.get(region, region)


def _workspace_name(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw and "." not in raw and "/" not in raw:
        return raw
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    host = parsed.hostname or ""
    return host.split(".", 1)[0]


def normalise_realtime_endpoint(
    value: object,
    *,
    workspace_id: str | None,
    region: str,
) -> str:
    """Return a WSS endpoint without model query parameters."""
    raw = str(value or "").strip()
    if raw:
        if "://" not in raw:
            raw = f"wss://{raw}"
        elif raw.lower().startswith("https://"):
            raw = f"wss://{raw[8:]}"
        parsed = urlsplit(raw)
        if parsed.scheme.lower() != "wss" or not parsed.hostname:
            raise RealtimeVoiceConfigurationError(
                "百炼全双工语音地址必须是有效的 WSS 地址"
            )
        return urlunsplit(
            ("wss", parsed.netloc, "/api-ws/v1/realtime", "", "")
        )

    if workspace_id:
        domain = _WORKSPACE_DOMAINS.get(region)
        if not domain:
            raise RealtimeVoiceConfigurationError(
                f"百炼全双工语音暂不支持区域: {region}"
            )
        return f"wss://{workspace_id}.{domain}/api-ws/v1/realtime"

    legacy_host = _LEGACY_HOSTS.get(region)
    if not legacy_host:
        raise RealtimeVoiceConfigurationError(
            "未配置百炼 workspace_id 或 realtime_base_ws"
        )
    return f"wss://{legacy_host}/api-ws/v1/realtime"


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(value if value is not None else default)))
    except (TypeError, ValueError) as exc:
        raise RealtimeVoiceConfigurationError("百炼全双工语音整数配置无效") from exc


def _bounded_float(
    value: object,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        return min(maximum, max(minimum, float(value if value is not None else default)))
    except (TypeError, ValueError) as exc:
        raise RealtimeVoiceConfigurationError("百炼全双工语音数值配置无效") from exc


async def load_realtime_voice_config() -> RealtimeVoiceConfig:
    app_config = await get_runtime_app_config()
    cosyvoice = await get_runtime_config_section("cosyvoice")
    bailian = await get_runtime_config_section("bailian")
    runtime = app_config.runtime

    api_key = cosyvoice.get("api_key") or bailian.get("api_key") or runtime.api_key
    if not api_key:
        raise RealtimeVoiceConfigurationError(
            "数据库 cosyvoice.api_key/bailian.api_key/runtime.api_key 未配置"
        )

    region = _normalise_region(cosyvoice.get("region") or bailian.get("region"))
    workspace_id = _workspace_name(
        cosyvoice.get("workspace_id") or bailian.get("workspace_id")
    ) or None
    endpoint_source = (
        cosyvoice.get("realtime_base_ws")
        or bailian.get("realtime_base_ws")
        or cosyvoice.get("base_ws")
        or bailian.get("base_ws")
    )
    endpoint = normalise_realtime_endpoint(
        endpoint_source,
        workspace_id=workspace_id,
        region=region,
    )

    model = str(
        cosyvoice.get("realtime_model") or LATEST_REALTIME_VOICE_MODEL
    ).strip()
    if model not in SUPPORTED_REALTIME_VOICE_MODELS:
        raise RealtimeVoiceConfigurationError(f"不支持的全双工语音模型: {model}")

    system_voice_ids = {voice_id for voice_id, _ in SYSTEM_VOICES}
    default_voice = str(
        cosyvoice.get("realtime_voice") or "longanqian"
    ).strip()
    if default_voice not in system_voice_ids:
        raise RealtimeVoiceConfigurationError(
            "realtime_voice 必须是百炼系统音色；复刻音色请在会话中选择"
        )

    default_mode = str(
        cosyvoice.get("realtime_turn_detection") or "smart_turn"
    ).strip()
    if default_mode not in {"smart_turn", "server_vad", "manual"}:
        raise RealtimeVoiceConfigurationError(
            "realtime_turn_detection 仅支持 smart_turn、server_vad 或 manual"
        )

    instructions = str(cosyvoice.get("realtime_instructions") or "").strip()
    if len(instructions) > 4000:
        raise RealtimeVoiceConfigurationError(
            "realtime_instructions 不能超过 4000 个字符"
        )

    return RealtimeVoiceConfig(
        provider=str(cosyvoice.get("realtime_provider") or "bailian_qwen_audio"),
        api_key=str(api_key),
        model=model,
        endpoint=endpoint,
        workspace_id=workspace_id,
        region=region,
        default_voice=default_voice,
        default_mode=default_mode,  # type: ignore[arg-type]
        default_instructions=instructions,
        max_history_turns=_bounded_int(
            cosyvoice.get("realtime_max_history_turns"),
            default=20,
            minimum=1,
            maximum=50,
        ),
        vad_threshold=_bounded_float(
            cosyvoice.get("realtime_vad_threshold"),
            default=0.5,
            minimum=-1.0,
            maximum=1.0,
        ),
        vad_silence_ms=_bounded_int(
            cosyvoice.get("realtime_vad_silence_ms"),
            default=800,
            minimum=200,
            maximum=6000,
        ),
        connect_timeout_seconds=_bounded_float(
            cosyvoice.get("realtime_connect_timeout_seconds"),
            default=15.0,
            minimum=3.0,
            maximum=60.0,
        ),
        session_timeout_seconds=_bounded_float(
            cosyvoice.get("realtime_session_timeout_seconds"),
            default=1800.0,
            minimum=60.0,
            maximum=7200.0,
        ),
        max_audio_chunk_bytes=_bounded_int(
            cosyvoice.get("realtime_max_audio_chunk_bytes"),
            default=64 * 1024,
            minimum=1280,
            maximum=512 * 1024,
        ),
        max_provider_message_bytes=_bounded_int(
            cosyvoice.get("realtime_max_provider_message_bytes"),
            default=4 * 1024 * 1024,
            minimum=256 * 1024,
            maximum=16 * 1024 * 1024,
        ),
    )
