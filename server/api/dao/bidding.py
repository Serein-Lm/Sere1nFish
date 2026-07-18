"""招投标公告 DAO。

公告按稳定 record_id 全局去重，项目、Target 和任务只作为关联累积；正文、详情页和
附件存放在对象存储中，本集合仅保存可查询元数据、预览和对象引用。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import UpdateOne

from api.db.collections import BIDDING_RECORDS_COLLECTION


_PRESERVED_ARCHIVE_FIELDS = (
    "provider_payload_object_id",
    "provider_payload_url",
    "raw_content_object_id",
    "raw_content_url",
    "detail_html_object_id",
    "detail_html_url",
    "resolved_detail_url",
    "content_length",
    "content_preview",
    "detail_text_preview",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    collection = db[BIDDING_RECORDS_COLLECTION]
    await collection.create_index("record_id", unique=True)
    await collection.create_index([("project_ids", 1), ("published_on", -1)])
    await collection.create_index([("target_ids", 1), ("published_on", -1)])
    await collection.create_index([("query_names", 1), ("published_on", -1)])
    await collection.create_index("task_ids")
    await collection.create_index("detail_url", sparse=True)
    await collection.create_index("updated_at")


def _content_hash(record: dict[str, Any]) -> str:
    payload = {
        key: record.get(key)
        for key in (
            "provider_record_id",
            "provider_uuid",
            "title",
            "announcement_type",
            "stage",
            "published_on",
            "province",
            "purchaser",
            "agency",
            "amount",
            "winner",
            "enterprise_identity",
            "detail_url",
            "resolved_detail_url",
            "provider_url",
            "summary",
            "introduction",
            "content_length",
            "content_preview",
            "provider_payload_object_id",
            "raw_content_object_id",
            "detail_html_object_id",
            "attachment_urls",
            "attachments",
        )
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _attachment_key(attachment: dict[str, Any]) -> str:
    return str(
        attachment.get("source_url")
        or attachment.get("storage_object_id")
        or attachment.get("filename")
        or f"index:{attachment.get('index', '')}"
    )


def _merge_archive_evidence(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """临时下载失败时保留此前已成功归档的不可变证据引用。"""
    if not previous:
        return current

    merged = dict(current)
    for field in _PRESERVED_ARCHIVE_FIELDS:
        if not merged.get(field) and previous.get(field):
            merged[field] = previous[field]

    merged["attachment_urls"] = list(
        dict.fromkeys(
            [
                *[str(item) for item in previous.get("attachment_urls") or [] if item],
                *[str(item) for item in current.get("attachment_urls") or [] if item],
            ]
        )
    )

    attachments: dict[str, dict[str, Any]] = {}
    for raw in previous.get("attachments") or []:
        if isinstance(raw, dict):
            attachments[_attachment_key(raw)] = dict(raw)
    for raw in current.get("attachments") or []:
        if not isinstance(raw, dict):
            continue
        key = _attachment_key(raw)
        prior = attachments.get(key)
        if prior and prior.get("status") == "ready" and raw.get("status") != "ready":
            preserved = dict(prior)
            if raw.get("error"):
                preserved["latest_archive_error"] = str(raw["error"])
            attachments[key] = preserved
        else:
            attachments[key] = dict(raw)
    merged["attachments"] = list(attachments.values())
    return merged


async def upsert_records_batch(
    db: AsyncIOMotorDatabase,
    *,
    records: list[dict[str, Any]],
    project_id: str,
    target_id: str,
    task_id: str,
    query_name: str,
) -> dict[str, Any]:
    if not records:
        return {"inserted": 0, "updated": 0, "unchanged": 0, "total": 0}

    now = _now()
    prepared: dict[str, dict[str, Any]] = {}
    for raw_record in records:
        record_id = str(raw_record.get("record_id") or "").strip()
        if not record_id:
            continue
        fields = {
            key: value
            for key, value in raw_record.items()
            if key not in {"project_ids", "target_ids", "task_ids", "query_names"}
        }
        fields.update(
            {
                "record_id": record_id,
                "latest_project_id": project_id,
                "latest_target_id": target_id,
                "latest_task_id": task_id,
                "latest_query_name": query_name,
                "last_seen_at": now,
                "updated_at": now,
            }
        )
        fields["content_hash"] = _content_hash(fields)
        prepared[record_id] = fields

    if not prepared:
        return {"inserted": 0, "updated": 0, "unchanged": 0, "total": 0}

    ids = list(prepared)
    archive_projection = {
        "_id": 0,
        "record_id": 1,
        "content_hash": 1,
        "attachment_urls": 1,
        "attachments": 1,
        **{field: 1 for field in _PRESERVED_ARCHIVE_FIELDS},
    }
    existing = {
        str(doc.get("record_id") or ""): doc
        async for doc in db[BIDDING_RECORDS_COLLECTION].find(
            {"record_id": {"$in": ids}},
            archive_projection,
        )
    }
    inserted = 0
    updated = 0
    unchanged = 0
    operations: list[UpdateOne] = []
    for record_id, fields in prepared.items():
        previous = existing.get(record_id)
        fields = _merge_archive_evidence(fields, previous)
        fields["content_hash"] = _content_hash(fields)
        previous_hash = str((previous or {}).get("content_hash") or "") or None
        if previous_hash is None:
            inserted += 1
        elif previous_hash != fields["content_hash"]:
            updated += 1
        else:
            unchanged += 1

        additions: dict[str, Any] = {"query_names": query_name}
        if project_id:
            additions["project_ids"] = project_id
        if target_id:
            additions["target_ids"] = target_id
        if task_id:
            additions["task_ids"] = task_id
        operations.append(
            UpdateOne(
                {"record_id": record_id},
                {
                    "$set": fields,
                    "$setOnInsert": {"created_at": now},
                    "$addToSet": additions,
                },
                upsert=True,
            )
        )

    await db[BIDDING_RECORDS_COLLECTION].bulk_write(operations, ordered=False)
    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "total": len(prepared),
    }


async def query_records(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str = "",
    target_id: str = "",
    query_name: str = "",
    limit: int = 100,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    query: dict[str, Any] = {}
    if project_id:
        query["project_ids"] = project_id
    if target_id:
        query["target_ids"] = target_id
    if query_name:
        query["query_names"] = query_name
    collection = db[BIDDING_RECORDS_COLLECTION]
    total = await collection.count_documents(query)
    cursor = (
        collection.find(query, {"_id": 0})
        .sort([("published_on", -1), ("updated_at", -1)])
        .skip(max(0, skip))
        .limit(max(1, limit))
    )
    return [doc async for doc in cursor], total


async def query_company_records(
    db: AsyncIOMotorDatabase,
    *,
    target_id: str = "",
    company_name: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    query: dict[str, Any]
    if target_id and company_name:
        query = {"$or": [{"target_ids": target_id}, {"query_names": company_name}]}
    elif target_id:
        query = {"target_ids": target_id}
    elif company_name:
        query = {"query_names": company_name}
    else:
        return []
    cursor = (
        db[BIDDING_RECORDS_COLLECTION]
        .find(query, {"_id": 0})
        .sort([("published_on", -1), ("updated_at", -1)])
        .limit(max(1, limit))
    )
    return [doc async for doc in cursor]


async def detach_project(db: AsyncIOMotorDatabase, project_id: str) -> int:
    """删除项目时只解除关联，保留可被其他项目/Target 复用的永久公告。"""
    result = await db[BIDDING_RECORDS_COLLECTION].update_many(
        {"project_ids": project_id},
        {"$pull": {"project_ids": project_id}},
    )
    return int(result.modified_count)
