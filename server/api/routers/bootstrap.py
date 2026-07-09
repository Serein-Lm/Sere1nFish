"""前端一站式 bootstrap：数据库运行时配置 + 核心 API 地图。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from api.auth import get_current_active_user
from api.services.runtime_config import get_runtime_app_config

router = APIRouter(dependencies=[Depends(get_current_active_user)])

_MOBILE_ENDPOINTS: dict[str, str] = {
    "mobile_devices": "GET /api/v1/mobile/devices",
    "mobile_health": "GET /api/v1/mobile/devices/{device_id}/health",
    "mobile_screenshot_png": "GET /api/v1/mobile/devices/{device_id}/screenshot",
    "mobile_screenshot_json": "GET /api/v1/mobile/devices/{device_id}/screenshot?format=json",
    "mobile_video_reset": "POST /api/v1/mobile/video/reset",
    "mobile_tap": "POST /api/v1/mobile/devices/{device_id}/tap",
    "mobile_swipe": "POST /api/v1/mobile/devices/{device_id}/swipe",
    "mobile_overview": "GET /api/v1/mobile/overview",
    "mobile_pool": "GET /api/v1/mobile/pool",
    "mobile_agent_task": "POST /api/v1/mobile/agent/task",
    "mobile_events": "GET /api/v1/mobile/events",
}

_CORE_ENDPOINTS: dict[str, str] = {
    "health": "GET /health",
    "docs": "GET /docs",
    "auth_login": "POST /api/v1/auth/login",
    "auth_me": "GET /api/v1/auth/me",
    "bootstrap": "GET /api/v1/bootstrap",
    "config_all": "GET /api/v1/config",
    "config_llm": "GET|POST /api/v1/config/llm",
    "projects": "GET /api/v1/projects",
    "xhs_cookies": "GET /api/v1/xhs/cookies",
    "douyin_cookies": "GET /api/v1/douyin/cookies",
    "agent": "POST /api/v1/agent/...",
    **_MOBILE_ENDPOINTS,
}


def _mask_key(key: str | None) -> str:
    if not key or key == "EMPTY":
        return ""
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


@router.get("")
async def frontend_bootstrap() -> dict[str, Any]:
    """登录后首调：配置路径、脱敏 runtime、手机视频默认、坐标系、端点表。"""
    cfg = await get_runtime_app_config()
    rt = cfg.runtime
    video = cfg.mobile.video

    video_defaults = {
        "maxSize": video.max_size,
        "bitRate": video.bit_rate,
        "maxFps": video.max_fps,
        "downsizeOnError": video.downsize_on_error,
    }

    return {
        "api_version": "1.0.0",
        "config_path": "",
        "config_exists": True,
        "config_storage": "mongodb_encrypted",
        "runtime": {
            "base_url": rt.base_url,
            "api_key_masked": _mask_key(rt.api_key),
            "llm_configured": bool(rt.base_url),
            "models": {
                "default": rt.models.default,
                "vision": rt.models.vision,
                "mobile_planner": rt.models.mobile_planner_model,
                "mobile_executor": rt.models.mobile_executor_model,
                "mobile_screen": rt.models.mobile_screen_model,
                "mobile_chat": rt.models.mobile_chat_model,
            },
            "agent_timeout": rt.agent_timeout,
            "max_tokens": rt.max_tokens,
            "temperature": rt.temperature,
            "top_p": rt.top_p,
            "frequency_penalty": rt.frequency_penalty,
        },
        "mobile": {
            "adb_timeout": cfg.mobile.adb_timeout,
            "executor_max_tokens": cfg.mobile.executor_max_tokens,
            "video_defaults": video_defaults,
        },
        "coordinate_scales": {
            "agent": 1000,
            "api": 10000,
            "coord_space_values": ["pixel", "normalized_1000", "normalized_10000", "auto"],
        },
        "socketio": {
            "path": "/socket.io",
            "connect_device": "connect-device",
            "events": ["video-metadata", "video-data"],
            "payload_keys": ["device_id", "port", *video_defaults.keys()],
        },
        "endpoints": _CORE_ENDPOINTS,
        "capabilities": {
            "mobile_visualization": True,
            "mobile_control": True,
            "mobile_ai_task": bool(rt.base_url),
        },
    }
