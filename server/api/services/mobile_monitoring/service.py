"""Persistent mobile incremental monitors built on tasks and schedules."""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from api.dao import mobile_collect as collect_dao
from api.dao import schedules as schedules_dao
from api.models.mobile_collect import (
    CollectTaskDef,
    MobileMonitorCreate,
    MobileMonitorUpdate,
)
from api.services import scheduling
from api.services.mobile_collect_tasks import (
    MobileCollectTaskBusyError,
    start_mobile_collect_task,
)
from api.services.mobile_monitoring.providers import (
    MobileMonitorProviderRegistry,
    MonitorTargetContext,
)
from api.services.targets import require_project_target


MONITOR_KIND = "mobile_incremental_monitor"
MONITOR_CHANNEL = "wechat_official"


class MobileMonitorNotFoundError(ValueError):
    pass


class MobileMonitorConflictError(RuntimeError):
    pass


class MobileMonitorBusyError(RuntimeError):
    pass


def _monitor_key(request: MobileMonitorCreate) -> str:
    account_keys = sorted(item.casefold() for item in request.official_accounts)
    identity = "|".join(
        [
            request.project_id,
            request.target_id,
            MONITOR_CHANNEL,
            request.scope,
            *account_keys,
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _metadata(
    *,
    request: MobileMonitorCreate,
    monitor_id: str,
    monitor_key: str,
    target_name: str,
    task_def_id: str,
) -> dict[str, Any]:
    return {
        "kind": MONITOR_KIND,
        "monitor_id": monitor_id,
        "monitor_key": monitor_key,
        "channel": MONITOR_CHANNEL,
        "project_id": request.project_id,
        "scope_target_id": request.target_id,
        "scope_target_name": target_name,
        "scope": request.scope,
        "official_accounts": list(request.official_accounts),
        "device_id": request.device_id,
        "app_instance": request.app_instance,
        "task_def_id": task_def_id,
    }


def _as_monitor(
    schedule: dict[str, Any],
    task_def: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = dict(schedule.get("metadata") or {})
    task = task_def or {}
    return {
        "monitor_id": str(metadata.get("monitor_id") or ""),
        "schedule_id": str(schedule.get("schedule_id") or ""),
        "task_def_id": str(schedule.get("target_id") or ""),
        "name": str(schedule.get("name") or task.get("name") or ""),
        "channel": str(metadata.get("channel") or MONITOR_CHANNEL),
        "project_id": str(metadata.get("project_id") or task.get("project_id") or ""),
        "target_id": str(metadata.get("scope_target_id") or task.get("target_id") or ""),
        "target_name": str(
            metadata.get("scope_target_name") or task.get("target_name") or ""
        ),
        "scope": str(metadata.get("scope") or "target"),
        "official_accounts": list(metadata.get("official_accounts") or []),
        "device_id": str(metadata.get("device_id") or task.get("device_id") or ""),
        "app_instance": str(
            metadata.get("app_instance") or task.get("app_instance") or "primary"
        ),
        "trigger": dict(schedule.get("trigger") or {}),
        "enabled": bool(schedule.get("enabled")),
        "task_status": str(task.get("status") or "missing"),
        "last_run_task_id": task.get("last_run_task_id"),
        "last_run_at": task.get("last_run_at") or schedule.get("last_run"),
        "last_status": schedule.get("last_status"),
        "last_error": schedule.get("last_error"),
        "next_run": schedule.get("next_run"),
        "created_at": schedule.get("created_at"),
        "updated_at": schedule.get("updated_at"),
    }


async def _find_monitor_schedule(
    db: AsyncIOMotorDatabase,
    monitor_id: str,
) -> dict[str, Any]:
    items = await schedules_dao.list_schedules(
        db,
        metadata={"kind": MONITOR_KIND, "monitor_id": str(monitor_id or "").strip()},
        limit=2,
    )
    if not items:
        raise MobileMonitorNotFoundError("增量监控不存在")
    return items[0]


async def _find_existing_by_key(
    db: AsyncIOMotorDatabase,
    monitor_key: str,
) -> dict[str, Any] | None:
    items = await schedules_dao.list_schedules(
        db,
        metadata={"kind": MONITOR_KIND, "monitor_key": monitor_key},
        limit=1,
    )
    return items[0] if items else None


async def create_monitor(
    db: AsyncIOMotorDatabase,
    request: MobileMonitorCreate,
) -> dict[str, Any]:
    target = await require_project_target(
        db,
        project_id=request.project_id,
        target_id=request.target_id,
    )
    context = MonitorTargetContext(
        project_id=request.project_id,
        target_id=target["target_id"],
        target_name=target["target_name"],
    )
    provider = MobileMonitorProviderRegistry.resolve(MONITOR_CHANNEL)
    monitor_key = _monitor_key(request)
    existing = await _find_existing_by_key(db, monitor_key)
    if existing:
        task = await collect_dao.get_task_def(db, str(existing.get("target_id") or ""))
        return _as_monitor(existing, task)

    validated_task = CollectTaskDef(
        **provider.build_task_definition(request, context)
    ).model_dump()
    monitor_id = "mon_" + uuid.uuid4().hex[:16]
    validated_task.update(
        {
            "managed_by": MONITOR_KIND,
            "monitor_id": monitor_id,
            "monitor_channel": MONITOR_CHANNEL,
        }
    )
    task = await collect_dao.create_task_def(db, validated_task)
    task_def_id = str(task.get("task_def_id") or "")
    metadata = _metadata(
        request=request,
        monitor_id=monitor_id,
        monitor_key=monitor_key,
        target_name=context.target_name,
        task_def_id=task_def_id,
    )
    try:
        schedule = await scheduling.create_schedule(
            db,
            name=request.name or str(task.get("name") or "增量监控"),
            target_type="mobile_collect",
            target_id=task_def_id,
            trigger=request.trigger.model_dump(),
            enabled=request.enabled,
            metadata=metadata,
        )
    except DuplicateKeyError:
        await collect_dao.delete_task_def(db, task_def_id)
        existing = await _find_existing_by_key(db, monitor_key)
        if not existing:
            raise MobileMonitorConflictError("相同范围的增量监控已存在") from None
        existing_task = await collect_dao.get_task_def(
            db, str(existing.get("target_id") or "")
        )
        return _as_monitor(existing, existing_task)
    except Exception:
        await collect_dao.delete_task_def(db, task_def_id)
        raise
    return _as_monitor(schedule, task)


async def list_monitors(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str = "",
    target_id: str = "",
) -> list[dict[str, Any]]:
    metadata: dict[str, Any] = {"kind": MONITOR_KIND}
    if project_id:
        metadata["project_id"] = project_id
    if target_id:
        metadata["scope_target_id"] = target_id
    schedules = await schedules_dao.list_schedules(db, metadata=metadata)
    tasks = await collect_dao.get_task_defs_by_ids(
        db, [str(item.get("target_id") or "") for item in schedules]
    )
    return [
        _as_monitor(item, tasks.get(str(item.get("target_id") or "")))
        for item in schedules
    ]


async def get_monitor(
    db: AsyncIOMotorDatabase,
    monitor_id: str,
) -> dict[str, Any]:
    schedule = await _find_monitor_schedule(db, monitor_id)
    task = await collect_dao.get_task_def(db, str(schedule.get("target_id") or ""))
    return _as_monitor(schedule, task)


async def update_monitor(
    db: AsyncIOMotorDatabase,
    monitor_id: str,
    request: MobileMonitorUpdate,
) -> dict[str, Any]:
    schedule = await _find_monitor_schedule(db, monitor_id)
    metadata = dict(schedule.get("metadata") or {})
    task_def_id = str(schedule.get("target_id") or "")
    task = await collect_dao.get_task_def(db, task_def_id)
    if not task:
        raise MobileMonitorNotFoundError("增量监控的手机任务定义不存在")

    patch = request.model_dump(exclude_none=True)
    task_fields = {"name", "device_id", "official_accounts", "app_instance"}
    rebuild = bool(task_fields.intersection(patch))
    if rebuild and task.get("status") == "running":
        raise MobileMonitorBusyError("监控任务运行中，不能修改执行配置")

    target_request = MobileMonitorCreate(
        name=str(patch.get("name", schedule.get("name") or "")),
        project_id=str(metadata.get("project_id") or task.get("project_id") or ""),
        target_id=str(metadata.get("scope_target_id") or task.get("target_id") or ""),
        device_id=str(patch.get("device_id", metadata.get("device_id") or task.get("device_id") or "")),
        scope=str(metadata.get("scope") or "target"),
        official_accounts=patch.get(
            "official_accounts", metadata.get("official_accounts") or []
        ),
        app_instance=str(
            patch.get(
                "app_instance", metadata.get("app_instance") or task.get("app_instance") or "primary"
            )
        ),
        trigger=patch.get("trigger", schedule.get("trigger") or {}),
        enabled=bool(patch.get("enabled", schedule.get("enabled"))),
    )
    target = await require_project_target(
        db,
        project_id=target_request.project_id,
        target_id=target_request.target_id,
    )
    context = MonitorTargetContext(
        project_id=target_request.project_id,
        target_id=target["target_id"],
        target_name=target["target_name"],
    )
    new_metadata = _metadata(
        request=target_request,
        monitor_id=str(metadata.get("monitor_id") or monitor_id),
        monitor_key=_monitor_key(target_request),
        target_name=context.target_name,
        task_def_id=task_def_id,
    )
    schedule_patch: dict[str, Any] = {"metadata": new_metadata}
    for field in ("name", "trigger", "enabled"):
        if field in patch:
            schedule_patch[field] = patch[field]

    try:
        updated_schedule = await scheduling.update_schedule(
            db, str(schedule.get("schedule_id") or ""), schedule_patch
        )
    except DuplicateKeyError as exc:
        raise MobileMonitorConflictError("相同范围的增量监控已存在") from exc

    if rebuild:
        provider = MobileMonitorProviderRegistry.resolve(MONITOR_CHANNEL)
        rebuilt = CollectTaskDef(
            **provider.build_task_definition(target_request, context)
        ).model_dump()
        rebuilt.update(
            {
                "managed_by": MONITOR_KIND,
                "monitor_id": monitor_id,
                "monitor_channel": MONITOR_CHANNEL,
            }
        )
        updated_task = await collect_dao.update_task_def(db, task_def_id, rebuilt)
        if not updated_task:
            await schedules_dao.update_schedule(
                db,
                str(schedule.get("schedule_id") or ""),
                {
                    "name": schedule.get("name"),
                    "trigger": schedule.get("trigger"),
                    "enabled": schedule.get("enabled"),
                    "metadata": metadata,
                },
            )
            raise MobileMonitorNotFoundError("增量监控的手机任务定义不存在")
        task = updated_task
    return _as_monitor(updated_schedule, task)


async def run_monitor_now(
    db: AsyncIOMotorDatabase,
    monitor_id: str,
    *,
    requested_by: str = "",
) -> dict[str, Any]:
    schedule = await _find_monitor_schedule(db, monitor_id)
    try:
        return await start_mobile_collect_task(
            db,
            task_def_id=str(schedule.get("target_id") or ""),
            requested_by=requested_by,
            trigger="monitor_manual",
        )
    except MobileCollectTaskBusyError as exc:
        raise MobileMonitorBusyError(str(exc)) from exc


async def delete_monitor(
    db: AsyncIOMotorDatabase,
    monitor_id: str,
) -> None:
    schedule = await _find_monitor_schedule(db, monitor_id)
    task_def_id = str(schedule.get("target_id") or "")
    task = await collect_dao.get_task_def(db, task_def_id)
    if task and task.get("status") == "running":
        raise MobileMonitorBusyError("监控任务运行中，请先停止任务再删除")
    await scheduling.delete_schedule(db, str(schedule.get("schedule_id") or ""))
    if task and task.get("managed_by") == MONITOR_KIND:
        await collect_dao.delete_task_def(db, task_def_id)
