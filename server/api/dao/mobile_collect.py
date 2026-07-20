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
from pymongo import ReturnDocument

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
    await records.create_index([("target_id", 1), ("last_seen", -1)])
    await records.create_index("source_document_id", sparse=True)
    await records.create_index(
        [("project_id", 1), ("target_id", 1), ("source_document_id", 1)]
    )


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


async def claim_task_run(
    db: AsyncIOMotorDatabase,
    task_def_id: str,
    *,
    run_task_id: str,
) -> dict[str, Any] | None:
    """原子占用一个空闲任务定义，避免手工与编排任务并发使用同一手机。"""
    now = _now()
    return await db[MOBILE_COLLECT_TASKS_COLLECTION].find_one_and_update(
        {"task_def_id": task_def_id, "status": {"$ne": "running"}},
        {
            "$set": {
                "status": "running",
                "last_run_task_id": run_task_id,
                "last_run_at": now,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


async def reset_interrupted_task_defs(db: AsyncIOMotorDatabase) -> int:
    """释放因服务重启而遗留为 running 的任务定义。"""
    result = await db[MOBILE_COLLECT_TASKS_COLLECTION].update_many(
        {"status": "running"},
        {"$set": {"status": "idle", "updated_at": _now()}},
    )
    return int(result.modified_count)


async def delete_task_def(db: AsyncIOMotorDatabase, task_def_id: str) -> int:
    result = await db[MOBILE_COLLECT_TASKS_COLLECTION].delete_one(
        {"task_def_id": task_def_id}
    )
    return result.deleted_count


async def backfill_task_target(
    db: AsyncIOMotorDatabase,
    *,
    task_def_id: str,
    target_id: str,
    target_name: str,
) -> int:
    """只补齐任务历史记录缺失的 Target，不覆盖已有实体归属。"""
    if not task_def_id or not target_id:
        return 0
    result = await db[MOBILE_COLLECT_RECORDS_COLLECTION].update_many(
        {
            "task_def_id": task_def_id,
            "$or": [
                {"target_id": {"$exists": False}},
                {"target_id": None},
                {"target_id": ""},
            ],
        },
        {
            "$set": {
                "target_id": target_id,
                "target_name": target_name,
                "updated_at": _now(),
            }
        },
    )
    return int(result.modified_count)


async def backfill_project_target_by_keywords(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    keywords: list[str],
    target_id: str,
    target_name: str,
) -> int:
    """恢复已删除任务留下的记录；调用侧必须只传明确包含 Target 的搜索词。"""
    terms = sorted({str(item).strip() for item in keywords if str(item).strip()})
    if not project_id or not terms or not target_id:
        return 0
    result = await db[MOBILE_COLLECT_RECORDS_COLLECTION].update_many(
        {
            "project_id": project_id,
            "keyword": {"$in": terms},
            "$or": [
                {"target_id": {"$exists": False}},
                {"target_id": None},
                {"target_id": ""},
            ],
        },
        {
            "$set": {
                "target_id": target_id,
                "target_name": target_name,
                "updated_at": _now(),
            }
        },
    )
    return int(result.modified_count)


# ── 采集结果增量入库 ───────────────────────────────────

def _stable_record_id(
    task_def_id: str,
    fields: dict[str, Any],
    dedup_key_fields: list[str],
    source_document_id: str = "",
) -> str:
    """由去重键派生稳定 record_id;无去重键时退回整条内容哈希。"""
    if source_document_id:
        key_repr = f"source_document={source_document_id}"
    elif dedup_key_fields:
        key_repr = "|".join(
            f"{k}={fields.get(k, '')}" for k in sorted(dedup_key_fields)
        )
    else:
        key_repr = json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str)
    raw = f"mcr:{task_def_id}:{key_repr}".encode("utf-8")
    return "mcr_" + hashlib.sha1(raw).hexdigest()[:20]


def stable_record_id(
    task_def_id: str,
    fields: dict[str, Any],
    dedup_key_fields: list[str],
    source_document_id: str = "",
) -> str:
    """公共封装:供 pipeline 等模块计算稳定去重键(到底检测用)。"""
    return _stable_record_id(
        task_def_id, fields, dedup_key_fields, source_document_id
    )


def _content_hash(fields: dict[str, Any], source_url: str | None = None) -> str:
    content: dict[str, Any] = fields
    if source_url:
        content = {"fields": fields, "source_url": source_url}
    raw = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


_EVIDENCE_ARRAY_FIELDS = (
    "run_task_ids",
    "screenshot_ids",
    "screenshot_urls",
    "browser_screenshot_ids",
    "browser_screenshot_urls",
    "discovery_screenshot_ids",
    "discovery_screenshot_urls",
)


async def _resolve_record_identity(
    collection: Any,
    *,
    task_def_id: str,
    fields: dict[str, Any],
    dedup_key_fields: list[str],
    source_document_id: str,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """Resolve a source document back to its earlier list discovery record.

    List extraction happens before browser-backed detail ingestion. When both
    stages describe the same item, the configured list dedup key remains the
    record identity and the immutable source document is attached to it. The
    third return value is a legacy duplicate that should be archived after its
    evidence has been folded into the canonical record.
    """
    source_record_id = _stable_record_id(
        task_def_id,
        fields,
        dedup_key_fields,
        source_document_id=source_document_id,
    )
    projection = {
        "_id": 0,
        "record_id": 1,
        "content_hash": 1,
        "fields": 1,
        "discovery_fields": 1,
        "source_document_id": 1,
        "superseded_by_record_id": 1,
        "first_seen": 1,
        **{field: 1 for field in _EVIDENCE_ARRAY_FIELDS},
    }
    if not source_document_id:
        existing = await collection.find_one(
            {"record_id": source_record_id}, projection
        )
        superseded_by = str((existing or {}).get("superseded_by_record_id") or "")
        if superseded_by:
            canonical = await collection.find_one(
                {"record_id": superseded_by}, projection
            )
            if canonical:
                return superseded_by, canonical, None
        return source_record_id, existing, None

    source_existing = await collection.find_one(
        {
            "task_def_id": task_def_id,
            "source_document_id": source_document_id,
            "superseded_by_record_id": {"$exists": False},
        },
        projection,
    )
    list_record_id = _stable_record_id(
        task_def_id,
        fields,
        dedup_key_fields,
        source_document_id="",
    )
    list_existing = await collection.find_one(
        {
            "record_id": list_record_id,
            "superseded_by_record_id": {"$exists": False},
        },
        projection,
    )

    if source_existing:
        canonical_id = str(source_existing.get("record_id") or source_record_id)
        duplicate = (
            list_existing
            if list_existing
            and str(list_existing.get("record_id") or list_record_id) != canonical_id
            else None
        )
        return canonical_id, source_existing, duplicate
    if list_existing:
        return list_record_id, list_existing, None
    return source_record_id, None, None


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
    source_document_id: str = "",
    source_document_version_id: str = "",
    target_id: str = "",
    target_name: str = "",
    browser_screenshot_ids: list[str] | None = None,
    browser_screenshot_urls: list[str] | None = None,
    discovery_screenshot_ids: list[str] | None = None,
    discovery_screenshot_urls: list[str] | None = None,
    discovery_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """增量 upsert 一条采集记录。返回 {record_id, is_new, is_changed}。"""
    content_hash = _content_hash(fields, source_url)
    now = _now()
    coll = db[MOBILE_COLLECT_RECORDS_COLLECTION]
    record_id, existing, legacy_duplicate = await _resolve_record_identity(
        coll,
        task_def_id=task_def_id,
        fields=discovery_fields or fields,
        dedup_key_fields=dedup_key_fields,
        source_document_id=source_document_id,
    )
    preserve_source_detail = bool(
        not source_document_id and (existing or {}).get("source_document_id")
    )
    is_new = existing is None
    is_changed = (
        (not is_new)
        and not preserve_source_detail
        and existing.get("content_hash") != content_hash
    )

    set_fields: dict[str, Any] = {
        "record_id": record_id,
        "task_def_id": task_def_id,
        "project_id": project_id,
        "keyword": keyword,
        "last_seen": now,
        "latest_run_task_id": run_task_id,
        "is_new": is_new,
        "is_changed": is_changed,
    }
    if preserve_source_detail:
        set_fields["discovery_fields"] = fields
        set_fields["discovery_content_hash"] = content_hash
    else:
        set_fields["fields"] = fields
        set_fields["content_hash"] = content_hash
    if score is not None and not preserve_source_detail:
        set_fields["score"] = score
    if subject_match is not None and not preserve_source_detail:
        set_fields["subject_match"] = subject_match
    if source_url and not preserve_source_detail:
        set_fields["source_url"] = source_url
    if source_document_id:
        set_fields["source_document_id"] = source_document_id
    if source_document_version_id:
        set_fields["source_document_version_id"] = source_document_version_id
    if source_document_id:
        archived_discovery_fields = (
            discovery_fields
            or (legacy_duplicate or {}).get("discovery_fields")
            or (legacy_duplicate or {}).get("fields")
            or (existing or {}).get("discovery_fields")
            or (
                (existing or {}).get("fields")
                if not (existing or {}).get("source_document_id")
                else None
            )
        )
        if archived_discovery_fields:
            set_fields["discovery_fields"] = archived_discovery_fields
    if target_id:
        set_fields["target_id"] = target_id
    if target_name:
        set_fields["target_name"] = target_name
    update: dict[str, Any] = {
        "$set": set_fields,
        "$setOnInsert": {"first_seen": now},
    }
    if legacy_duplicate and legacy_duplicate.get("first_seen"):
        existing_first_seen = (existing or {}).get("first_seen")
        duplicate_first_seen = legacy_duplicate["first_seen"]
        try:
            if not existing_first_seen or duplicate_first_seen < existing_first_seen:
                set_fields["first_seen"] = duplicate_first_seen
                update["$setOnInsert"].pop("first_seen", None)
        except TypeError:
            pass
    add_to_set: dict[str, Any] = {}
    if run_task_id:
        add_to_set["run_task_ids"] = run_task_id
    if screenshot_ids:
        add_to_set["screenshot_ids"] = {"$each": screenshot_ids}
    if screenshot_urls:
        add_to_set["screenshot_urls"] = {"$each": screenshot_urls}
    if browser_screenshot_ids:
        add_to_set["browser_screenshot_ids"] = {"$each": browser_screenshot_ids}
    if browser_screenshot_urls:
        add_to_set["browser_screenshot_urls"] = {"$each": browser_screenshot_urls}
    if discovery_screenshot_ids:
        add_to_set["discovery_screenshot_ids"] = {
            "$each": discovery_screenshot_ids
        }
    if discovery_screenshot_urls:
        add_to_set["discovery_screenshot_urls"] = {
            "$each": discovery_screenshot_urls
        }
    if legacy_duplicate:
        duplicate_id = str(legacy_duplicate.get("record_id") or "")
        if duplicate_id:
            add_to_set["merged_record_ids"] = duplicate_id
        for field in _EVIDENCE_ARRAY_FIELDS:
            values = list(legacy_duplicate.get(field) or [])
            if not values:
                continue
            current = add_to_set.get(field)
            if isinstance(current, dict):
                current["$each"] = list(dict.fromkeys([*current.get("$each", []), *values]))
            elif current:
                add_to_set[field] = {"$each": list(dict.fromkeys([current, *values]))}
            else:
                add_to_set[field] = {"$each": values}
    if add_to_set:
        update["$addToSet"] = add_to_set

    await coll.update_one({"record_id": record_id}, update, upsert=True)
    if legacy_duplicate:
        duplicate_id = str(legacy_duplicate.get("record_id") or "")
        if duplicate_id and duplicate_id != record_id:
            await coll.update_one(
                {"record_id": duplicate_id},
                {
                    "$set": {
                        "superseded_by_record_id": record_id,
                        "superseded_reason": "source_document_match",
                        "superseded_at": now,
                    }
                },
            )
    return {"record_id": record_id, "is_new": is_new, "is_changed": is_changed}


async def list_records(
    db: AsyncIOMotorDatabase,
    *,
    task_def_id: str | None = None,
    project_id: str | None = None,
    target_id: str | None = None,
    only_incremental: bool = False,
    archived_only: bool = False,
    min_score: int | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    query: dict[str, Any] = {"superseded_by_record_id": {"$exists": False}}
    if task_def_id:
        query["task_def_id"] = task_def_id
    if project_id:
        query["project_id"] = project_id
    if target_id:
        query["target_id"] = target_id
    if only_incremental:
        query["$or"] = [{"is_new": True}, {"is_changed": True}]
    if archived_only:
        query["source_document_id"] = {"$exists": True, "$nin": ["", None]}
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
