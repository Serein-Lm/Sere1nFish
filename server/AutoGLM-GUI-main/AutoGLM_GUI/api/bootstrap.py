"""Frontend bootstrap: config path, effective settings, and core API map."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from AutoGLM_GUI.config_manager import config_manager, init_runtime_config
from AutoGLM_GUI.schemas import ConfigResponse
from AutoGLM_GUI.version import APP_VERSION

router = APIRouter(prefix="/api", tags=["bootstrap"])

_VIDEO_DEFAULTS = {
    "maxSize": 1920,
    "bitRate": 8_000_000,
    "maxFps": 60,
    "downsizeOnError": False,
}

_CORE_ENDPOINTS: dict[str, str] = {
    "health": "GET /api/health",
    "bootstrap": "GET /api/bootstrap",
    "config_get": "GET /api/config",
    "config_save": "POST /api/config",
    "devices": "GET /api/devices",
    "screenshot": "POST /api/screenshot",
    "video_reset": "POST /api/video/reset",
    "control_tap": "POST /api/control/tap",
    "control_swipe": "POST /api/control/swipe",
    "tasks": "POST /api/tasks",
    "chat": "POST /api/chat",
    "layered_agent": "POST /api/layered-agent/run",
}


@router.get("/bootstrap")
async def frontend_bootstrap() -> dict[str, Any]:
    """Single entry for the web UI: where config lives + which APIs to call."""
    init_runtime_config(sync_env=False)
    config_manager.load_file_config()

    effective = config_manager.get_effective_config()
    source = config_manager.get_config_source()
    conflicts = config_manager.detect_conflicts()

    config_payload = ConfigResponse(
        base_url=effective.base_url,
        model_name=effective.model_name,
        api_key=effective.api_key if effective.api_key != "EMPTY" else "",
        max_tokens=effective.max_tokens,
        temperature=effective.temperature,
        top_p=effective.top_p,
        frequency_penalty=effective.frequency_penalty,
        lang=effective.lang,
        source=source.value,
        agent_type=effective.agent_type,
        agent_config_params=effective.agent_config_params,
        default_max_steps=effective.default_max_steps,
        layered_max_turns=effective.layered_max_turns,
        decision_base_url=effective.decision_base_url,
        decision_model_name=effective.decision_model_name,
        decision_api_key=effective.decision_api_key,
        conflicts=[
            {
                "field": c.field,
                "file_value": c.file_value,
                "override_value": c.override_value,
                "override_source": c.override_source.value,
            }
            for c in conflicts
        ]
        if conflicts
        else None,
    )

    return {
        "version": APP_VERSION,
        "config_path": str(config_manager.get_config_path()),
        "config_storage": "json",
        "config": config_payload.model_dump(),
        "video_defaults": _VIDEO_DEFAULTS,
        "coordinate_scales": {"agent": 1000, "api": 10000},
        "socketio_events": {
            "connect_device": "connect-device",
            "payload_keys": list(_VIDEO_DEFAULTS.keys()) + ["device_id", "port"],
        },
        "endpoints": _CORE_ENDPOINTS,
    }
