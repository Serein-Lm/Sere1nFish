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
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument, UpdateOne

from api.db.collections import (
    MOBILE_COLLECT_CHECKPOINTS_COLLECTION,
    MOBILE_COLLECT_RECORDS_COLLECTION,
    MOBILE_COLLECT_TASKS_COLLECTION,
)
from api.dao.project_scope import project_scope_query


def _now() -> datetime:
    return datetime.now(timezone.utc)


RECORD_RANKING_VERSION = 1
RECORD_SORT_MODES = {"score_desc", "time_desc", "value_time"}
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_PUBLISH_TIME_KEYS = (
    "publish_time",
    "published_at",
    "publish_date",
    "published_time",
    "发布时间",
)


def parse_record_publish_time(
    value: Any,
    *,
    reference: datetime | None = None,
) -> datetime | None:
    """Normalize common article timestamps to a timezone-aware UTC datetime."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_SHANGHAI_TZ)
        return parsed.astimezone(timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text = "".join(
        character
        for character in unicodedata.normalize("NFKC", str(value))
        if unicodedata.category(character) != "Cf"
    ).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{10,13}", text):
        return parse_record_publish_time(int(text), reference=reference)

    reference_utc = reference or _now()
    if reference_utc.tzinfo is None:
        reference_utc = reference_utc.replace(tzinfo=timezone.utc)
    local_reference = reference_utc.astimezone(_SHANGHAI_TZ)
    relative_match = re.fullmatch(r"(\d+)\s*(分钟|小时|天)前", text)
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2)
        delta = {
            "分钟": timedelta(minutes=amount),
            "小时": timedelta(hours=amount),
            "天": timedelta(days=amount),
        }[unit]
        return (local_reference - delta).astimezone(timezone.utc)
    if text in {"刚刚", "现在"}:
        return reference_utc.astimezone(timezone.utc)

    day_match = re.fullmatch(
        r"(今天|昨天|前天)(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?",
        text,
    )
    if day_match:
        day_offset = {"今天": 0, "昨天": 1, "前天": 2}[day_match.group(1)]
        local_date = (local_reference - timedelta(days=day_offset)).date()
        parsed = datetime(
            local_date.year,
            local_date.month,
            local_date.day,
            int(day_match.group(2) or 0),
            int(day_match.group(3) or 0),
            int(day_match.group(4) or 0),
            tzinfo=_SHANGHAI_TZ,
        )
        return parsed.astimezone(timezone.utc)

    normalized = re.sub(r"\s+", " ", text)
    normalized = (
        normalized.replace("年", "-")
        .replace("月", "-")
        .replace("日", " ")
        .replace("时", ":")
        .replace("分", ":")
        .replace("秒", "")
        .replace("/", "-")
    ).strip().rstrip(":")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None
    if parsed is None:
        for pattern in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y.%m.%d %H:%M:%S",
            "%Y.%m.%d %H:%M",
            "%Y.%m.%d",
        ):
            try:
                parsed = datetime.strptime(normalized, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_SHANGHAI_TZ)
    return parsed.astimezone(timezone.utc)


def build_record_ranking_fields(
    fields: dict[str, Any],
    *,
    score: Any,
    last_seen: datetime | None = None,
    contact_count: int | None = None,
) -> dict[str, Any]:
    """Build the durable value-first, recency-second record ranking projection."""
    if contact_count is None:
        from core.mobile.collect.contacts import extract_contacts, record_text_blob

        contact_count = len(extract_contacts(record_text_blob(fields)))
    safe_contact_count = max(0, int(contact_count or 0))
    try:
        safe_score = max(0, min(100, int(score or 0)))
    except (TypeError, ValueError):
        safe_score = 0
    if safe_contact_count:
        value_tier = 3
    elif safe_score >= 80:
        value_tier = 2
    elif safe_score >= 40:
        value_tier = 1
    else:
        value_tier = 0

    publish_value = next(
        (
            fields.get(key)
            for key in _PUBLISH_TIME_KEYS
            if fields.get(key) not in (None, "")
        ),
        None,
    )
    published_at = parse_record_publish_time(publish_value, reference=last_seen)
    fallback_time = last_seen or _now()
    if fallback_time.tzinfo is None:
        fallback_time = fallback_time.replace(tzinfo=timezone.utc)
    ranking = {
        "ranking_version": RECORD_RANKING_VERSION,
        "value_tier": value_tier,
        "contact_count": safe_contact_count,
        "sort_time": published_at or fallback_time.astimezone(timezone.utc),
    }
    if published_at is not None:
        ranking["published_at"] = published_at
    return ranking


def normalize_record_sort_mode(value: str | None) -> str:
    normalized = str(value or "score_desc").strip().casefold()
    if normalized not in RECORD_SORT_MODES:
        raise ValueError(f"不支持的采集记录排序方式: {normalized}")
    return normalized


def record_sort_spec(sort_by: str | None) -> list[tuple[str, int]]:
    normalized = normalize_record_sort_mode(sort_by)
    if normalized == "value_time":
        return [
            ("value_tier", -1),
            ("sort_time", -1),
            ("score", -1),
            ("subject_match", -1),
            ("last_seen", -1),
            ("record_id", 1),
        ]
    if normalized == "time_desc":
        return [
            ("sort_time", -1),
            ("value_tier", -1),
            ("score", -1),
            ("last_seen", -1),
            ("record_id", 1),
        ]
    return [("score", -1), ("last_seen", -1), ("record_id", 1)]


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
    await records.create_index([("project_ids", 1), ("last_seen", -1)])
    await records.create_index([("target_id", 1), ("last_seen", -1)])
    await records.create_index("source_document_id", sparse=True)
    await records.create_index(
        [("project_id", 1), ("target_id", 1), ("source_document_id", 1)]
    )
    await records.create_index(
        [
            ("project_id", 1),
            ("target_id", 1),
            ("value_tier", -1),
            ("sort_time", -1),
            ("score", -1),
        ]
    )
    await records.create_index(
        [
            ("project_ids", 1),
            ("target_id", 1),
            ("value_tier", -1),
            ("sort_time", -1),
            ("score", -1),
        ]
    )

    checkpoints = db[MOBILE_COLLECT_CHECKPOINTS_COLLECTION]
    await checkpoints.create_index(
        [("run_task_id", 1), ("checkpoint_key", 1)],
        unique=True,
    )
    await checkpoints.create_index(
        [("task_def_id", 1), ("definition_fingerprint", 1), ("status", 1)]
    )


async def backfill_record_ranking_fields(
    db: AsyncIOMotorDatabase,
    *,
    batch_size: int = 500,
) -> int:
    """Idempotently add ranking projections to records created by older versions."""
    collection = db[MOBILE_COLLECT_RECORDS_COLLECTION]
    safe_batch_size = max(1, min(int(batch_size), 2_000))
    cursor = collection.find(
        {
            "superseded_by_record_id": {"$exists": False},
            "$or": [
                {"ranking_version": {"$ne": RECORD_RANKING_VERSION}},
                {"sort_time": {"$exists": False}},
            ],
        },
        {
            "_id": 0,
            "record_id": 1,
            "fields": 1,
            "score": 1,
            "last_seen": 1,
            "first_seen": 1,
        },
    ).batch_size(safe_batch_size)
    operations: list[UpdateOne] = []
    updated = 0

    async def flush() -> None:
        nonlocal updated
        if not operations:
            return
        batch = list(operations)
        operations.clear()
        await collection.bulk_write(batch, ordered=False)
        updated += len(batch)

    async for record in cursor:
        ranking = build_record_ranking_fields(
            dict(record.get("fields") or {}),
            score=record.get("score"),
            last_seen=record.get("last_seen") or record.get("first_seen") or _now(),
        )
        update: dict[str, Any] = {"$set": ranking}
        if "published_at" not in ranking:
            update["$unset"] = {"published_at": ""}
        operations.append(
            UpdateOne({"record_id": record.get("record_id")}, update)
        )
        if len(operations) >= safe_batch_size:
            await flush()
    await flush()
    return updated


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


async def reset_interrupted_task_defs(
    db: AsyncIOMotorDatabase,
    *,
    active_run_task_ids: set[str] | None = None,
) -> int:
    """释放无活跃分布式执行租约的遗留任务定义。"""
    query: dict[str, Any] = {"status": "running"}
    if active_run_task_ids:
        query["last_run_task_id"] = {"$nin": sorted(active_run_task_ids)}
    result = await db[MOBILE_COLLECT_TASKS_COLLECTION].update_many(
        query,
        {"$set": {"status": "idle", "updated_at": _now()}},
    )
    return int(result.modified_count)


# ── 关键词级持久化检查点 ───────────────────────────────

_CHECKPOINT_VOLATILE_FIELDS = {
    "status",
    "last_run_task_id",
    "last_run_at",
    "created_at",
    "updated_at",
    "parent_task_id",
}


def definition_fingerprint(task_def: dict[str, Any]) -> str:
    stable = {
        key: value
        for key, value in task_def.items()
        if key not in _CHECKPOINT_VOLATILE_FIELDS and not key.startswith("_")
    }
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def keyword_checkpoint_key(
    *,
    definition_fingerprint: str,
    keyword: str,
    target_id: str = "",
) -> str:
    raw = json.dumps(
        [definition_fingerprint, str(target_id or ""), str(keyword or "")],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def list_completed_checkpoint_keys(
    db: AsyncIOMotorDatabase,
    *,
    run_task_id: str,
    definition_fingerprint: str,
) -> set[str]:
    cursor = db[MOBILE_COLLECT_CHECKPOINTS_COLLECTION].find(
        {
            "run_task_id": run_task_id,
            "definition_fingerprint": definition_fingerprint,
            "status": "completed",
        },
        {"_id": 0, "checkpoint_key": 1},
    )
    return {
        str(item.get("checkpoint_key") or "")
        async for item in cursor
        if item.get("checkpoint_key")
    }


async def mark_keyword_checkpoint(
    db: AsyncIOMotorDatabase,
    *,
    run_task_id: str,
    task_def_id: str,
    definition_fingerprint: str,
    checkpoint_key: str,
    keyword: str,
    target_id: str,
    status: str,
    error: str = "",
    stats: dict[str, Any] | None = None,
) -> None:
    now = _now()
    fields: dict[str, Any] = {
        "run_task_id": run_task_id,
        "task_def_id": task_def_id,
        "definition_fingerprint": definition_fingerprint,
        "checkpoint_key": checkpoint_key,
        "keyword": keyword,
        "target_id": target_id,
        "status": status,
        "updated_at": now,
    }
    if status == "running":
        fields["started_at"] = now
    elif status == "captured":
        fields["captured_at"] = now
    elif status == "completed":
        fields["completed_at"] = now
    elif status == "failed":
        fields["failed_at"] = now
    if error:
        fields["error"] = error[:1000]
    if stats is not None:
        fields["stats"] = stats
    update: dict[str, Any] = {
        "$set": fields,
        "$setOnInsert": {"created_at": now},
    }
    unset_fields: dict[str, str] = {}
    if not error:
        unset_fields.update({"error": "", "failed_at": ""})
    if status != "completed":
        unset_fields["completed_at"] = ""
    if status not in {"captured", "completed"}:
        unset_fields["captured_at"] = ""
    if unset_fields:
        update["$unset"] = unset_fields
    await db[MOBILE_COLLECT_CHECKPOINTS_COLLECTION].update_one(
        {"run_task_id": run_task_id, "checkpoint_key": checkpoint_key},
        update,
        upsert=True,
    )


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
        "score": 1,
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
    if source_existing is None:
        source_existing = await collection.find_one(
            {
                "task_def_id": task_def_id,
                "source_document_id": source_document_id,
                "superseded_reason": "source_relevance_rejected",
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
    contact_count: int | None = None,
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
    ranking_unset: dict[str, str] = {}
    if not preserve_source_detail:
        ranking = build_record_ranking_fields(
            fields,
            score=score if score is not None else (existing or {}).get("score"),
            last_seen=now,
            contact_count=contact_count,
        )
        set_fields.update(ranking)
        if "published_at" not in ranking:
            ranking_unset["published_at"] = ""
    update: dict[str, Any] = {
        "$set": set_fields,
        "$setOnInsert": {"first_seen": now},
    }
    if ranking_unset:
        update["$unset"] = ranking_unset
    if project_id:
        update["$setOnInsert"]["project_id"] = project_id
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
    if project_id:
        add_to_set["project_ids"] = project_id
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
    if source_document_id:
        update.setdefault("$unset", {}).update(
            {
                "superseded_by_record_id": "",
                "superseded_reason": "",
                "superseded_at": "",
            }
        )

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


async def archive_rejected_source_records(
    db: AsyncIOMotorDatabase,
    *,
    task_def_id: str,
    project_id: str,
    source_document_id: str,
    target_id: str = "",
    reason: str = "source_relevance_rejected",
) -> list[str]:
    """Hide prior records when an immutable source fails a later relevance review."""
    if not task_def_id or not project_id or not source_document_id:
        return []
    query: dict[str, Any] = {
        "task_def_id": task_def_id,
        "project_id": project_id,
        "source_document_id": source_document_id,
        "$or": [
            {"superseded_by_record_id": {"$exists": False}},
            {"superseded_reason": reason},
        ],
    }
    if target_id:
        query["target_id"] = target_id
    collection = db[MOBILE_COLLECT_RECORDS_COLLECTION]
    records = await collection.find(query, {"_id": 0, "record_id": 1}).to_list(
        length=None
    )
    record_ids = [
        str(record.get("record_id") or "")
        for record in records
        if record.get("record_id")
    ]
    if not record_ids:
        return []
    now = _now()
    await collection.update_many(
        {"record_id": {"$in": record_ids}},
        {
            "$set": {
                "superseded_by_record_id": f"rejected:{source_document_id}",
                "superseded_reason": reason,
                "superseded_at": now,
                "updated_at": now,
            }
        },
    )
    return record_ids


async def attach_media_evidence(
    db: AsyncIOMotorDatabase,
    *,
    record_id: str,
    evidence_ids: list[str],
    storage_object_ids: list[str],
) -> None:
    """Attach social media evidence without changing the record content identity."""
    add_to_set: dict[str, Any] = {}
    if evidence_ids:
        add_to_set["media_evidence_ids"] = {"$each": list(dict.fromkeys(evidence_ids))}
    if storage_object_ids:
        add_to_set["media_storage_object_ids"] = {
            "$each": list(dict.fromkeys(storage_object_ids))
        }
    if not add_to_set:
        return
    await db[MOBILE_COLLECT_RECORDS_COLLECTION].update_one(
        {"record_id": record_id},
        {"$addToSet": add_to_set, "$set": {"updated_at": _now()}},
    )


async def list_records(
    db: AsyncIOMotorDatabase,
    *,
    task_def_id: str | None = None,
    project_id: str | None = None,
    target_id: str | None = None,
    only_incremental: bool = False,
    archived_only: bool = False,
    min_score: int | None = None,
    sort_by: str = "score_desc",
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    query: dict[str, Any] = {"superseded_by_record_id": {"$exists": False}}
    if task_def_id:
        query["task_def_id"] = task_def_id
    if target_id:
        query["target_id"] = target_id
    if only_incremental:
        query["$or"] = [{"is_new": True}, {"is_changed": True}]
    if archived_only:
        query["source_document_id"] = {"$exists": True, "$nin": ["", None]}
    if min_score is not None:
        query["score"] = {"$gte": min_score}
    if project_id:
        query = project_scope_query(project_id, query)
    total = await db[MOBILE_COLLECT_RECORDS_COLLECTION].count_documents(query)
    cursor = (
        db[MOBILE_COLLECT_RECORDS_COLLECTION]
        .find(query, {"_id": 0})
        .sort(record_sort_spec(sort_by))
        .skip(max(0, skip))
        .limit(max(1, min(limit, 200)))
    )
    return [doc async for doc in cursor], total
