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

from api.db.collections import (
    BIDDING_RECORD_LINKS_COLLECTION,
    BIDDING_RECORDS_COLLECTION,
)


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
    "contact_candidates",
    "contact_candidate_count",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def bidding_record_link_id(
    project_id: str,
    target_id: str,
    record_id: str,
) -> str:
    digest = hashlib.sha256(
        f"{project_id}:{target_id}:{record_id}".encode("utf-8")
    ).hexdigest()
    return "bidlink_" + digest[:24]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    collection = db[BIDDING_RECORDS_COLLECTION]
    await collection.create_index("record_id", unique=True)
    await collection.create_index([("procurement_id", 1), ("published_on", -1)])
    await collection.create_index([("bid_type_codes", 1), ("published_on", -1)])
    await collection.create_index([("project_ids", 1), ("published_on", -1)])
    await collection.create_index([("target_ids", 1), ("published_on", -1)])
    await collection.create_index([("query_names", 1), ("published_on", -1)])
    await collection.create_index("task_ids")
    await collection.create_index("detail_url", sparse=True)
    await collection.create_index("updated_at")
    links = db[BIDDING_RECORD_LINKS_COLLECTION]
    await links.create_index("link_id", unique=True)
    await links.create_index(
        [("project_id", 1), ("target_id", 1), ("published_on", -1)]
    )
    await links.create_index([("record_id", 1), ("last_seen_at", -1)])
    await links.create_index([("procurement_id", 1), ("last_seen_at", -1)])
    await links.create_index("task_ids")


def _content_hash(record: dict[str, Any]) -> str:
    payload = {
        key: record.get(key)
        for key in (
            "provider_record_id",
            "provider_uuid",
            "bid_type_codes",
            "procurement_id",
            "procurement_title",
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
            "contact_candidates",
            "contact_candidate_count",
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
    query_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not records:
        return {
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "total": 0,
            "links_total": 0,
        }

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
        fields.setdefault("attachment_urls", [])
        fields.setdefault("attachments", [])
        fields.setdefault("contact_candidates", [])
        fields.setdefault("contact_candidate_count", 0)
        fields["content_hash"] = _content_hash(fields)
        prepared[record_id] = fields

    if not prepared:
        return {
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "total": 0,
            "links_total": 0,
        }

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
    link_operations: list[UpdateOne] = []
    if project_id and target_id:
        query_window = {
            key: value
            for key, value in dict(query_meta or {}).items()
            if key in {"publish_start", "publish_end", "lookback_days", "bid_types"}
        }
        for record_id, fields in prepared.items():
            link_id = bidding_record_link_id(project_id, target_id, record_id)
            additions: dict[str, Any] = {}
            if query_name:
                additions["query_names"] = query_name
            if task_id:
                additions["task_ids"] = task_id
            if query_window:
                additions["query_windows"] = query_window
            type_codes = [
                str(item)
                for item in fields.get("bid_type_codes") or []
                if str(item)
            ]
            if type_codes:
                additions["bid_type_codes"] = {"$each": type_codes}
            link_operations.append(
                UpdateOne(
                    {"link_id": link_id},
                    {
                        "$set": {
                            "link_id": link_id,
                            "record_id": record_id,
                            "project_id": project_id,
                            "target_id": target_id,
                            "latest_task_id": task_id,
                            "latest_query_name": query_name,
                            "latest_query_window": query_window,
                            "procurement_id": str(fields.get("procurement_id") or ""),
                            "published_on": str(fields.get("published_on") or ""),
                            "last_seen_at": now,
                            "updated_at": now,
                        },
                        "$setOnInsert": {"created_at": now, "first_seen_at": now},
                        "$addToSet": additions,
                    },
                    upsert=True,
                )
            )
    if link_operations:
        await db[BIDDING_RECORD_LINKS_COLLECTION].bulk_write(
            link_operations,
            ordered=False,
        )
    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "total": len(prepared),
        "links_total": len(link_operations),
    }


async def clone_project_links(
    db: AsyncIOMotorDatabase,
    *,
    source_project_id: str,
    destination_project_id: str,
    target_ids: list[str],
) -> int:
    """Copy project/Target announcement links and retain global archive records."""
    normalized_target_ids = list(dict.fromkeys(value for value in target_ids if value))
    if not source_project_id or not destination_project_id or not normalized_target_ids:
        return 0
    cursor = db[BIDDING_RECORD_LINKS_COLLECTION].find(
        {
            "project_id": source_project_id,
            "target_id": {"$in": normalized_target_ids},
        },
        {"_id": 0},
    )
    operations: list[UpdateOne] = []
    record_ids: list[str] = []
    async for source in cursor:
        record_id = str(source.get("record_id") or "")
        target_id = str(source.get("target_id") or "")
        if not record_id or not target_id:
            continue
        clone = dict(source)
        clone["link_id"] = bidding_record_link_id(
            destination_project_id,
            target_id,
            record_id,
        )
        clone["project_id"] = destination_project_id
        clone["migrated_from_project_id"] = source_project_id
        operations.append(
            UpdateOne(
                {"link_id": clone["link_id"]},
                {"$setOnInsert": clone},
                upsert=True,
            )
        )
        record_ids.append(record_id)
    if not operations:
        return 0
    result = await db[BIDDING_RECORD_LINKS_COLLECTION].bulk_write(
        operations,
        ordered=False,
    )
    await db[BIDDING_RECORDS_COLLECTION].update_many(
        {"record_id": {"$in": list(dict.fromkeys(record_ids))}},
        {"$addToSet": {"project_ids": destination_project_id}},
    )
    return int(result.upserted_count or 0)


async def query_record_links(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str = "",
    target_id: str = "",
    record_id: str = "",
    limit: int = 100,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """读取精确的 Project/Target/公告关联，供后续聚合分析使用。"""
    query: dict[str, Any] = {}
    if project_id:
        query["project_id"] = project_id
    if target_id:
        query["target_id"] = target_id
    if record_id:
        query["record_id"] = record_id
    collection = db[BIDDING_RECORD_LINKS_COLLECTION]
    total = await collection.count_documents(query)
    cursor = (
        collection.find(query, {"_id": 0})
        .sort([("published_on", -1), ("updated_at", -1)])
        .skip(max(0, skip))
        .limit(max(1, limit))
    )
    return [doc async for doc in cursor], total


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
    await db[BIDDING_RECORD_LINKS_COLLECTION].delete_many(
        {"project_id": project_id}
    )
    return int(result.modified_count)
