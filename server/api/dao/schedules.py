"""定时调度 — DAO 与触发时间计算。

轻量实现:不引入外部依赖。
- interval: 固定间隔秒;
- cron: 支持标准 5 段 (分 时 日 月 周),字段支持 '*'、单值、'a-b' 区间、'a,b' 列表、'*/n' 步进。

next_run 以 UTC 存储与比较。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import TASK_SCHEDULES_COLLECTION


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── cron 解析 (最小实现) ────────────────────────────────

def _parse_cron_field(field: str, lo: int, hi: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            base, step_str = part.split("/", 1)
            step = max(1, int(step_str))
        else:
            base = part
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            start_str, end_str = base.split("-", 1)
            start, end = int(start_str), int(end_str)
        else:
            start = end = int(base)
        for v in range(start, end + 1, step):
            if lo <= v <= hi:
                values.add(v)
    return values


def _cron_matches(expr: str, dt: datetime) -> bool:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron 表达式必须为 5 段: {expr!r}")
    minute, hour, dom, month, dow = parts
    if dt.minute not in _parse_cron_field(minute, 0, 59):
        return False
    if dt.hour not in _parse_cron_field(hour, 0, 23):
        return False
    if dt.day not in _parse_cron_field(dom, 1, 31):
        return False
    if dt.month not in _parse_cron_field(month, 1, 12):
        return False
    # cron: 0/7 均表示周日; python weekday() 周一=0,故转成 0=周日..6=周六
    py_dow = (dt.weekday() + 1) % 7
    allowed_dow = _parse_cron_field(dow, 0, 7)
    if 7 in allowed_dow:
        allowed_dow.add(0)
    if py_dow not in allowed_dow:
        return False
    return True


def compute_next_run(trigger: dict[str, Any], *, after: datetime | None = None) -> datetime:
    """根据触发器计算下一次运行时间(UTC)。"""
    base = (after or _now()).replace(microsecond=0)
    ttype = trigger.get("type", "interval")
    if ttype == "interval":
        seconds = max(30, int(trigger.get("interval_seconds") or 60))
        return base + timedelta(seconds=seconds)
    # cron: 从下一分钟起逐分钟扫描,最多向前 366 天
    expr = str(trigger.get("cron") or "").strip()
    if not expr:
        raise ValueError("cron 触发器缺少表达式")
    candidate = (base + timedelta(minutes=1)).replace(second=0, microsecond=0)
    limit = candidate + timedelta(days=366)
    while candidate <= limit:
        if _cron_matches(expr, candidate):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError(f"无法为 cron 计算下一次运行: {expr!r}")


def validate_trigger(trigger: dict[str, Any]) -> None:
    """校验触发器合法(不合法抛 ValueError)。"""
    compute_next_run(trigger)


# ── CRUD ──────────────────────────────────────────────

async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[TASK_SCHEDULES_COLLECTION]
    await coll.create_index("schedule_id", unique=True)
    await coll.create_index([("enabled", 1), ("next_run", 1)])
    await coll.create_index("target_id")


async def create_schedule(
    db: AsyncIOMotorDatabase,
    *,
    name: str,
    target_type: str,
    target_id: str,
    trigger: dict[str, Any],
    enabled: bool = True,
) -> dict[str, Any]:
    schedule_id = "sch_" + uuid.uuid4().hex[:16]
    now = _now()
    doc = {
        "schedule_id": schedule_id,
        "name": name,
        "target_type": target_type,
        "target_id": target_id,
        "trigger": trigger,
        "enabled": enabled,
        "last_run": None,
        "next_run": compute_next_run(trigger) if enabled else None,
        "created_at": now,
        "updated_at": now,
    }
    await db[TASK_SCHEDULES_COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return doc


async def get_schedule(db: AsyncIOMotorDatabase, schedule_id: str) -> dict[str, Any] | None:
    return await db[TASK_SCHEDULES_COLLECTION].find_one(
        {"schedule_id": schedule_id}, {"_id": 0}
    )


async def list_schedules(
    db: AsyncIOMotorDatabase, *, target_id: str | None = None
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if target_id:
        query["target_id"] = target_id
    cursor = db[TASK_SCHEDULES_COLLECTION].find(query, {"_id": 0}).sort("created_at", -1)
    return [doc async for doc in cursor]


async def update_schedule(
    db: AsyncIOMotorDatabase, schedule_id: str, patch: dict[str, Any]
) -> dict[str, Any] | None:
    patch = {k: v for k, v in patch.items() if v is not None}
    patch["updated_at"] = _now()
    if "trigger" in patch or "enabled" in patch:
        current = await get_schedule(db, schedule_id)
        if current:
            trigger = patch.get("trigger", current.get("trigger"))
            enabled = patch.get("enabled", current.get("enabled"))
            patch["next_run"] = compute_next_run(trigger) if enabled else None
    await db[TASK_SCHEDULES_COLLECTION].update_one(
        {"schedule_id": schedule_id}, {"$set": patch}
    )
    return await get_schedule(db, schedule_id)


async def delete_schedule(db: AsyncIOMotorDatabase, schedule_id: str) -> int:
    result = await db[TASK_SCHEDULES_COLLECTION].delete_one({"schedule_id": schedule_id})
    return result.deleted_count


async def list_due(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    """返回到期(enabled 且 next_run<=now)的调度。"""
    cursor = db[TASK_SCHEDULES_COLLECTION].find(
        {"enabled": True, "next_run": {"$lte": _now()}}, {"_id": 0}
    )
    return [doc async for doc in cursor]


async def mark_ran(db: AsyncIOMotorDatabase, schedule_id: str) -> None:
    """标记已运行并计算下一次运行时间。"""
    schedule = await get_schedule(db, schedule_id)
    if not schedule:
        return
    now = _now()
    next_run = compute_next_run(schedule["trigger"], after=now)
    await db[TASK_SCHEDULES_COLLECTION].update_one(
        {"schedule_id": schedule_id},
        {"$set": {"last_run": now, "next_run": next_run, "updated_at": now}},
    )
