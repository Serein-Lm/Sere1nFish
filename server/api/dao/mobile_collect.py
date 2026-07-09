"""手机采集任务框架 — DAO。

- 任务定义 (mobile_collect_tasks): 自定义采集任务的配置真源。
- 采集结果 (mobile_collect_records): 按稳定 record_id + content_hash 增量入库。

增量语义 (参考 fofa_assets/persons upsert 模式):
- record 不存在  → 插入, is_new=True, 写 first_seen;
- 存在且 hash 变化 → 更新字段, is_changed=True, 刷新 last_seen;
- 存在且 hash 相同 → 仅刷新 last_seen, 不标记增量。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import (
    MOBILE_COLLECT_RECORDS_COLLECTION,
    MOBILE_COLLECT_TASKS_COLLECTION,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """幂等建立索引。"""
    tasks = db[MOBILE_COLLECT_TASKS_COLLECTION]
    await tasks.create_index("task_def_id", unique=True)
    await tasks.create_index([("project_id", 1), ("updated_at", -1)])

    records = db[MOBILE_COLLECT_RECORDS_COLLECTION]
    await records.create_index("record_id", unique=True)
    await records.create_index([("task_def_id", 1), ("last_seen", -1)])
    await records.create_index([("task_def_id", 1), ("is_new", 1)])
    await records.create_index([("project_id", 1), ("last_seen", -1)])


# ── 任务定义 CRUD ──────────────────────────────────────

async def create_task_def(db: AsyncIOMotorDatabase, payload: dict[str, Any]) -> dict[str, Any]:
    task_def_id = "mct_" + uuid.uuid4().hex[:16]
    now = _now()
    doc = {
        "task_def_id": task_def_id,
        **payload,
        "status": "idle",
        "last_run_task_id": None,
        "last_run_at": None,
        "created_at": now,
        "updated_at": now,
    }
    await db[MOBILE_COLLECT_TASKS_COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return doc


async def get_task_def(db: AsyncIOMotorDatabase, task_def_id: str) -> dict[str, Any] | None:
    return await db[MOBILE_COLLECT_TASKS_COLLECTION].find_one(
        {"task_def_id": task_def_id}, {"_id": 0}
    )


async def list_task_defs(
    db: AsyncIOMotorDatabase, *, project_id: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if project_id:
        query["project_id"] = project_id
    cursor = (
        db[MOBILE_COLLECT_TASKS_COLLECTION]
        .find(query, {"_id": 0})
        .sort("updated_at", -1)
        .limit(max(1, min(limit, 500)))
    )
    return [doc async for doc in cursor]


async def update_task_def(
    db: AsyncIOMotorDatabase, task_def_id: str, patch: dict[str, Any]
) -> dict[str, Any] | None:
    patch = {k: v for k, v in patch.items() if v is not None}
    patch["updated_at"] = _now()
    await db[MOBILE_COLLECT_TASKS_COLLECTION].update_one(
        {"task_def_id": task_def_id}, {"$set": patch}
    )
    return await get_task_def(db, task_def_id)


async def set_task_status(
    db: AsyncIOMotorDatabase,
    task_def_id: str,
    status: str,
    *,
    run_task_id: str | None = None,
) -> None:
    set_fields: dict[str, Any] = {"status": status, "updated_at": _now()}
    if run_task_id is not None:
        set_fields["last_run_task_id"] = run_task_id
        set_fields["last_run_at"] = _now()
    await db[MOBILE_COLLECT_TASKS_COLLECTION].update_one(
        {"task_def_id": task_def_id}, {"$set": set_fields}
    )


async def delete_task_def(db: AsyncIOMotorDatabase, task_def_id: str) -> int:
    result = await db[MOBILE_COLLECT_TASKS_COLLECTION].delete_one(
        {"task_def_id": task_def_id}
    )
    return result.deleted_count


# ── 采集结果增量入库 ───────────────────────────────────

def _stable_record_id(
    task_def_id: str, fields: dict[str, Any], dedup_key_fields: list[str]
) -> str:
    """由去重键派生稳定 record_id;无去重键时退回整条内容哈希。"""
    if dedup_key_fields:
        key_repr = "|".join(
            f"{k}={fields.get(k, '')}" for k in sorted(dedup_key_fields)
        )
    else:
        key_repr = json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str)
    raw = f"mcr:{task_def_id}:{key_repr}".encode("utf-8")
    return "mcr_" + hashlib.sha1(raw).hexdigest()[:20]


def stable_record_id(
    task_def_id: str, fields: dict[str, Any], dedup_key_fields: list[str]
) -> str:
    """公共封装:供 pipeline 等模块计算稳定去重键(到底检测用)。"""
    return _stable_record_id(task_def_id, fields, dedup_key_fields)


def _content_hash(fields: dict[str, Any]) -> str:
    raw = json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


async def upsert_record(
    db: AsyncIOMotorDatabase,
    *,
    task_def_id: str,
    project_id: str | None,
    fields: dict[str, Any],
    dedup_key_fields: list[str],
    screenshot_ids: list[str] | None = None,
    screenshot_urls: list[str] | None = None,
    keyword: str = "",
    run_task_id: str = "",
    score: int | None = None,
    subject_match: int | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    """增量 upsert 一条采集记录。返回 {record_id, is_new, is_changed}。"""
    record_id = _stable_record_id(task_def_id, fields, dedup_key_fields)
    content_hash = _content_hash(fields)
    now = _now()
    coll = db[MOBILE_COLLECT_RECORDS_COLLECTION]

    existing = await coll.find_one(
        {"record_id": record_id}, {"_id": 0, "content_hash": 1}
    )
    is_new = existing is None
    is_changed = (not is_new) and existing.get("content_hash") != content_hash

    set_fields: dict[str, Any] = {
        "record_id": record_id,
        "task_def_id": task_def_id,
        "project_id": project_id,
        "fields": fields,
        "content_hash": content_hash,
        "keyword": keyword,
        "last_seen": now,
        "latest_run_task_id": run_task_id,
        "is_new": is_new,
        "is_changed": is_changed,
    }
    if score is not None:
        set_fields["score"] = score
    if subject_match is not None:
        set_fields["subject_match"] = subject_match
    if source_url:
        set_fields["source_url"] = source_url

    update: dict[str, Any] = {
        "$set": set_fields,
        "$setOnInsert": {"first_seen": now},
    }
    add_to_set: dict[str, Any] = {}
    if run_task_id:
        add_to_set["run_task_ids"] = run_task_id
    if screenshot_ids:
        add_to_set["screenshot_ids"] = {"$each": screenshot_ids}
    if screenshot_urls:
        add_to_set["screenshot_urls"] = {"$each": screenshot_urls}
    if add_to_set:
        update["$addToSet"] = add_to_set

    await coll.update_one({"record_id": record_id}, update, upsert=True)
    return {"record_id": record_id, "is_new": is_new, "is_changed": is_changed}


async def list_records(
    db: AsyncIOMotorDatabase,
    *,
    task_def_id: str | None = None,
    project_id: str | None = None,
    only_incremental: bool = False,
    min_score: int | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    query: dict[str, Any] = {}
    if task_def_id:
        query["task_def_id"] = task_def_id
    if project_id:
        query["project_id"] = project_id
    if only_incremental:
        query["$or"] = [{"is_new": True}, {"is_changed": True}]
    if min_score is not None:
        query["score"] = {"$gte": min_score}
    total = await db[MOBILE_COLLECT_RECORDS_COLLECTION].count_documents(query)
    cursor = (
        db[MOBILE_COLLECT_RECORDS_COLLECTION]
        .find(query, {"_id": 0})
        .sort([("score", -1), ("last_seen", -1)])
        .skip(max(0, skip))
        .limit(max(1, min(limit, 200)))
    )
    return [doc async for doc in cursor], total
