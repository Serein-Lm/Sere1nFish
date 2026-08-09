"""Unified read service for the live mobile device pool.

The HTTP API and AI Hub tools share this service so device discovery,
reservation state, EasyTier metadata, and persisted display metadata keep the
same semantics.  Expensive screenshot health probes are opt-in.
"""
from __future__ import annotations

import asyncio
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase


async def list_mobile_device_statuses(
    db: AsyncIOMotorDatabase,
    *,
    group_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return the current device pool enriched with persisted metadata."""
    from api.dao import device_metadata as metadata_dao
    from api.dao import mobile_execution_leases as execution_leases_dao
    from core.mobile.pool import DevicePool

    items = await asyncio.to_thread(DevicePool.get_instance().list_pool)
    keys = [str(item.get("device_key") or item.get("device_id") or "") for item in items]
    metadata, active_leases = await asyncio.gather(
        metadata_dao.get_metadata_map(db, {key for key in keys if key}),
        execution_leases_dao.list_active(db),
    )
    leases_by_device = {
        str(lease.get("device_key") or ""): lease for lease in active_leases
    }

    enriched: list[dict[str, Any]] = []
    for raw, key in zip(items, keys):
        item = dict(raw)
        meta = metadata.get(key) or {}
        item.setdefault("device_key", key)
        execution = leases_by_device.get(key)
        item["executing"] = execution is not None
        item["execution"] = (
            {
                field: execution.get(field)
                for field in ("task_id", "owner", "kind", "expires_at")
            }
            if execution
            else None
        )
        item["meta"] = {
            "display_name": meta.get("display_name"),
            "note": meta.get("note", ""),
            "tags": list(meta.get("tags") or []),
            "group_id": meta.get("group_id"),
        }
        if group_id == "ungrouped" and item["meta"].get("group_id"):
            continue
        if group_id not in (None, "ungrouped") and item["meta"].get("group_id") != group_id:
            continue
        enriched.append(item)
    return enriched


async def get_mobile_device_status(
    db: AsyncIOMotorDatabase,
    device_id: str,
    *,
    probe_health: bool = False,
) -> dict[str, Any] | None:
    """Resolve one device by active id, stable key, or EasyTier IP."""
    identifier = str(device_id or "").strip()
    if not identifier:
        return None

    items = await list_mobile_device_statuses(db)
    item = next(
        (
            candidate
            for candidate in items
            if identifier
            in {
                str(candidate.get("device_id") or ""),
                str(candidate.get("device_key") or ""),
                str(candidate.get("network_ip") or ""),
            }
        ),
        None,
    )
    if item is None:
        return None

    result = dict(item)
    if probe_health and result.get("online"):
        from core.mobile.manager import MobileDeviceManager

        try:
            health = await asyncio.wait_for(
                asyncio.to_thread(
                    MobileDeviceManager().health,
                    str(result.get("device_id") or identifier),
                ),
                timeout=15,
            )
            result["health"] = {
                "online": health.online,
                "screenshot_ready": health.screenshot_ready,
                "input_ready": health.input_ready,
                "current_app_ready": health.current_app_ready,
                "capture_failed": health.capture_failed,
                "error": health.error,
            }
        except asyncio.TimeoutError:
            result["health"] = {
                "online": False,
                "screenshot_ready": False,
                "input_ready": False,
                "current_app_ready": False,
                "capture_failed": True,
                "error": "health probe timeout",
            }
        except Exception as exc:  # noqa: BLE001
            result["health"] = {
                "online": False,
                "screenshot_ready": False,
                "input_ready": False,
                "current_app_ready": False,
                "capture_failed": True,
                "error": str(exc),
            }
    return result
