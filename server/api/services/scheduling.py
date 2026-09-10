"""Registry-driven scheduling service shared by API and background workers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import mobile_collect as collect_dao
from api.dao import schedules as schedules_dao
from api.services.mobile_collect_tasks import (
    MobileCollectTaskBusyError,
    start_mobile_collect_task,
)


class ScheduleNotFoundError(ValueError):
    pass


class ScheduleTargetNotFoundError(ValueError):
    pass


@dataclass(frozen=True)
class ScheduleTriggerResult:
    status: str
    task_id: str = ""
    message: str = ""


class ScheduleTargetAdapter(Protocol):
    target_type: str

    async def validate_target(
        self, db: AsyncIOMotorDatabase, target_id: str
    ) -> None: ...

    async def trigger(
        self, db: AsyncIOMotorDatabase, schedule: dict[str, Any]
    ) -> ScheduleTriggerResult: ...


class ScheduleTargetRegistry:
    """Select target implementations without exposing them to callers."""

    _adapters: dict[str, ScheduleTargetAdapter] = {}

    @classmethod
    def register(cls, adapter: ScheduleTargetAdapter) -> None:
        cls._adapters[adapter.target_type] = adapter

    @classmethod
    def resolve(cls, target_type: str) -> ScheduleTargetAdapter:
        adapter = cls._adapters.get(str(target_type or "").strip())
        if adapter is None:
            raise ValueError(f"不支持的调度目标类型: {target_type}")
        return adapter


class MobileCollectScheduleAdapter:
    target_type = "mobile_collect"

    async def validate_target(
        self, db: AsyncIOMotorDatabase, target_id: str
    ) -> None:
        if not await collect_dao.get_task_def(db, target_id):
            raise ScheduleTargetNotFoundError("目标采集任务定义不存在")

    async def trigger(
        self, db: AsyncIOMotorDatabase, schedule: dict[str, Any]
    ) -> ScheduleTriggerResult:
        target_id = str(schedule.get("target_id") or "")
        try:
            task = await start_mobile_collect_task(
                db,
                task_def_id=target_id,
                trigger="schedule",
                schedule_id=str(schedule.get("schedule_id") or ""),
            )
        except MobileCollectTaskBusyError as exc:
            return ScheduleTriggerResult(status="skipped_busy", message=str(exc))
        return ScheduleTriggerResult(
            status="dispatched",
            task_id=str(task.get("task_id") or ""),
        )


ScheduleTargetRegistry.register(MobileCollectScheduleAdapter())


async def create_schedule(
    db: AsyncIOMotorDatabase,
    *,
    name: str,
    target_type: str,
    target_id: str,
    trigger: dict[str, Any],
    enabled: bool = True,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    adapter = ScheduleTargetRegistry.resolve(target_type)
    await adapter.validate_target(db, target_id)
    schedules_dao.validate_trigger(trigger)
    return await schedules_dao.create_schedule(
        db,
        name=str(name or "").strip(),
        target_type=target_type,
        target_id=target_id,
        trigger=trigger,
        enabled=enabled,
        metadata=metadata,
    )


async def update_schedule(
    db: AsyncIOMotorDatabase,
    schedule_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    existing = await schedules_dao.get_schedule(db, schedule_id)
    if not existing:
        raise ScheduleNotFoundError("调度不存在")
    normalized = {key: value for key, value in patch.items() if value is not None}
    if "trigger" in normalized:
        trigger = normalized["trigger"]
        if hasattr(trigger, "model_dump"):
            trigger = trigger.model_dump()
        schedules_dao.validate_trigger(trigger)
        normalized["trigger"] = trigger
    updated = await schedules_dao.update_schedule(db, schedule_id, normalized)
    if not updated:
        raise ScheduleNotFoundError("调度不存在")
    return updated


async def delete_schedule(
    db: AsyncIOMotorDatabase,
    schedule_id: str,
) -> None:
    if not await schedules_dao.delete_schedule(db, schedule_id):
        raise ScheduleNotFoundError("调度不存在")


async def trigger_schedule(
    db: AsyncIOMotorDatabase,
    schedule: dict[str, Any],
) -> ScheduleTriggerResult:
    adapter = ScheduleTargetRegistry.resolve(str(schedule.get("target_type") or ""))
    return await adapter.trigger(db, schedule)
