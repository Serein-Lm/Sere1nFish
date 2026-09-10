"""Validated runtime settings for distributed execution."""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import config as config_dao
from api.services.runtime_config import get_runtime_config_section

from .contracts import GatewayPolicy
from .validation import SUPPORTED_CAPABILITIES


DEFAULT_DISTRIBUTED_SCAN_CONFIG: dict[str, Any] = {
    "enabled": False,
    "kinds": ["http_probe", "browser_probe"],
    "project_ids": [],
    "fallback_local": True,
    "wait_seconds": 60,
    "poll_seconds": 0.5,
    "batch_sizes": {"http_probe": 20, "browser_probe": 1},
    "dispatch_concurrency": 8,
    "default_proxy_profile_id": "",
    "proxy_mode": "none",
}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _as_strings(value: Any) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set)):
        return frozenset()
    return frozenset(str(item).strip() for item in value if str(item).strip())


def normalize_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    kinds = _as_strings(config.get("kinds") or sorted(SUPPORTED_CAPABILITIES))
    kinds = kinds.intersection(SUPPORTED_CAPABILITIES)
    batch_sizes = config.get("batch_sizes")
    if not isinstance(batch_sizes, dict):
        batch_sizes = {}
    proxy_mode = str(config.get("proxy_mode") or "none").strip()
    if proxy_mode not in {"none", "best_effort", "required"}:
        proxy_mode = "none"
    return {
        "enabled": _as_bool(config.get("enabled"), False),
        "kinds": sorted(kinds or SUPPORTED_CAPABILITIES),
        "project_ids": sorted(_as_strings(config.get("project_ids"))),
        "fallback_local": _as_bool(config.get("fallback_local"), True),
        "wait_seconds": _as_float(config.get("wait_seconds"), 60.0, 5.0, 300.0),
        "poll_seconds": _as_float(config.get("poll_seconds"), 0.5, 0.1, 5.0),
        "batch_sizes": {
            "http_probe": _as_int(batch_sizes.get("http_probe"), 20, 1, 50),
            "browser_probe": _as_int(batch_sizes.get("browser_probe"), 1, 1, 10),
        },
        "dispatch_concurrency": _as_int(
            config.get("dispatch_concurrency"), 8, 1, 32
        ),
        "default_proxy_profile_id": str(
            config.get("default_proxy_profile_id") or ""
        ).strip(),
        "proxy_mode": proxy_mode,
    }


async def get_distributed_scan_settings() -> dict[str, Any]:
    return normalize_runtime_config(
        await get_runtime_config_section("distributed_scan")
    )


async def ensure_default_settings(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    existing = await config_dao.get_config(db, "distributed_scan")
    if existing:
        return normalize_runtime_config(dict(existing.get("config") or {}))
    await config_dao.set_config(
        db,
        "distributed_scan",
        dict(DEFAULT_DISTRIBUTED_SCAN_CONFIG),
    )
    return dict(DEFAULT_DISTRIBUTED_SCAN_CONFIG)


async def get_gateway_policy(kind: str) -> GatewayPolicy:
    settings = await get_distributed_scan_settings()
    return GatewayPolicy(
        enabled=settings["enabled"],
        kinds=frozenset(settings["kinds"]),
        project_ids=frozenset(settings["project_ids"]),
        fallback_local=settings["fallback_local"],
        wait_seconds=settings["wait_seconds"],
        poll_seconds=settings["poll_seconds"],
        batch_size=settings["batch_sizes"].get(kind, 1),
        dispatch_concurrency=settings["dispatch_concurrency"],
        proxy_profile_id=settings["default_proxy_profile_id"],
        proxy_mode=settings["proxy_mode"],
    )
