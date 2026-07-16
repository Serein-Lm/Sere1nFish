"""永久来源文档、不可变版本与项目/Target 发现关联 DAO。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import (
    SOURCE_DOCUMENT_LINKS_COLLECTION,
    SOURCE_DOCUMENT_VERSIONS_COLLECTION,
    SOURCE_DOCUMENTS_COLLECTION,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def document_id_for_url(canonical_url: str) -> str:
    value = str(canonical_url or "").strip()
    if not value:
        raise ValueError("canonical_url 不能为空")
    return "doc_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def version_id_for_content(document_id: str, content_hash: str) -> str:
    raw = f"source-version:{document_id}:{content_hash}".encode("utf-8")
    return "dver_" + hashlib.sha256(raw).hexdigest()[:24]


def document_link_id(project_id: str, target_id: str, document_id: str) -> str:
    raw = f"source-link:{project_id}:{target_id}:{document_id}".encode("utf-8")
    return "dlnk_" + hashlib.sha256(raw).hexdigest()[:24]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    documents = db[SOURCE_DOCUMENTS_COLLECTION]
    await documents.create_index("document_id", unique=True)
    await documents.create_index("canonical_url", unique=True)
    await documents.create_index([("source_type", 1), ("last_seen_at", -1)])
    await documents.create_index("target_ids")

    versions = db[SOURCE_DOCUMENT_VERSIONS_COLLECTION]
    await versions.create_index("version_id", unique=True)
    await versions.create_index(
        [("document_id", 1), ("content_hash", 1)], unique=True
    )
    await versions.create_index([("document_id", 1), ("captured_at", -1)])
    await versions.create_index("storage_object_ids")

    links = db[SOURCE_DOCUMENT_LINKS_COLLECTION]
    await links.create_index("link_id", unique=True)
    await links.create_index([("project_id", 1), ("last_seen_at", -1)])
    await links.create_index([("target_id", 1), ("last_seen_at", -1)])
    await links.create_index([("document_id", 1), ("last_seen_at", -1)])
    await links.create_index("task_def_ids")


async def get_document(
    db: AsyncIOMotorDatabase, document_id: str
) -> dict[str, Any] | None:
    return await db[SOURCE_DOCUMENTS_COLLECTION].find_one(
        {"document_id": document_id}, {"_id": 0}
    )


async def get_document_by_url(
    db: AsyncIOMotorDatabase, canonical_url: str
) -> dict[str, Any] | None:
    return await db[SOURCE_DOCUMENTS_COLLECTION].find_one(
        {"canonical_url": canonical_url}, {"_id": 0}
    )


async def get_version(
    db: AsyncIOMotorDatabase, version_id: str
) -> dict[str, Any] | None:
    return await db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].find_one(
        {"version_id": version_id}, {"_id": 0}
    )


async def get_latest_version(
    db: AsyncIOMotorDatabase, document_id: str
) -> dict[str, Any] | None:
    document = await get_document(db, document_id)
    if not document:
        return None
    version_id = str(document.get("latest_version_id") or "")
    if version_id:
        return await get_version(db, version_id)
    return await db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].find_one(
        {"document_id": document_id, "status": "ready"},
        {"_id": 0},
        sort=[("captured_at", -1)],
    )


async def begin_version(
    db: AsyncIOMotorDatabase,
    *,
    version_id: str,
    document_id: str,
    content_hash: str,
    source_type: str,
) -> None:
    now = _now()
    await db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].update_one(
        {"version_id": version_id},
        {
            "$set": {
                "document_id": document_id,
                "content_hash": content_hash,
                "source_type": source_type,
                "status": "processing",
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now, "captured_at": now},
        },
        upsert=True,
    )


async def mark_version_ready(
    db: AsyncIOMotorDatabase,
    *,
    version_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    now = _now()
    await db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].update_one(
        {"version_id": version_id},
        {
            "$set": {
                **payload,
                "status": "ready",
                "error": "",
                "updated_at": now,
            }
        },
        upsert=True,
    )
    return await get_version(db, version_id) or payload


async def mark_version_error(
    db: AsyncIOMotorDatabase, version_id: str, error: str
) -> None:
    await db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].update_one(
        {"version_id": version_id},
        {
            "$set": {
                "status": "error",
                "error": str(error)[:2000],
                "updated_at": _now(),
            }
        },
    )


async def upsert_document(
    db: AsyncIOMotorDatabase,
    *,
    document_id: str,
    canonical_url: str,
    source_type: str,
    version: dict[str, Any],
    target_id: str = "",
) -> dict[str, Any]:
    now = _now()
    identity = version.get("identity") or {}
    content = version.get("content") or {}
    set_fields = {
        "document_id": document_id,
        "canonical_url": canonical_url,
        "source_type": source_type,
        "latest_version_id": version.get("version_id"),
        "latest_content_hash": version.get("content_hash"),
        "title": identity.get("title") or "",
        "account": identity.get("account") or "",
        "publish_time": identity.get("publish_time") or "",
        "summary": content.get("summary") or "",
        "contact_count": len(version.get("contacts") or []),
        "image_count": len(version.get("images") or []),
        "screenshot_count": len(version.get("screenshots") or []),
        "last_seen_at": now,
        "updated_at": now,
    }
    update: dict[str, Any] = {
        "$set": set_fields,
        "$setOnInsert": {"created_at": now, "first_seen_at": now},
    }
    if target_id:
        update["$addToSet"] = {"target_ids": target_id}
    await db[SOURCE_DOCUMENTS_COLLECTION].update_one(
        {"document_id": document_id}, update, upsert=True
    )
    return await get_document(db, document_id) or set_fields


async def link_document(
    db: AsyncIOMotorDatabase,
    *,
    document_id: str,
    version_id: str,
    project_id: str,
    target_id: str = "",
    target_name: str = "",
    task_def_id: str = "",
    run_task_id: str = "",
    keyword: str = "",
    score: int | None = None,
    subject_match: int | None = None,
    discovery_context: dict[str, Any] | None = None,
    contextual_analysis: dict[str, Any] | None = None,
    analysis_fingerprint: str = "",
) -> dict[str, Any]:
    """幂等累计一次文档发现关系，不复制文档原始内容。"""
    relation_id = document_link_id(project_id, target_id, document_id)
    now = _now()
    set_fields: dict[str, Any] = {
        "link_id": relation_id,
        "document_id": document_id,
        "latest_version_id": version_id,
        "project_id": project_id,
        "target_id": target_id,
        "target_name": target_name,
        "last_seen_at": now,
        "updated_at": now,
    }
    if score is not None:
        set_fields["latest_score"] = score
    if subject_match is not None:
        set_fields["latest_subject_match"] = subject_match
    if discovery_context:
        set_fields["latest_discovery_context"] = discovery_context
    if contextual_analysis:
        set_fields["latest_analysis"] = contextual_analysis
    if analysis_fingerprint:
        set_fields["analysis_fingerprint"] = analysis_fingerprint
    update: dict[str, Any] = {
        "$set": set_fields,
        "$setOnInsert": {"created_at": now, "first_seen_at": now},
    }
    additions: dict[str, Any] = {}
    if task_def_id:
        additions["task_def_ids"] = task_def_id
    if run_task_id:
        additions["run_task_ids"] = run_task_id
    if keyword:
        additions["keywords"] = keyword
    if additions:
        update["$addToSet"] = additions
    await db[SOURCE_DOCUMENT_LINKS_COLLECTION].update_one(
        {"link_id": relation_id}, update, upsert=True
    )
    return await db[SOURCE_DOCUMENT_LINKS_COLLECTION].find_one(
        {"link_id": relation_id}, {"_id": 0}
    ) or set_fields


async def get_links_for_document(
    db: AsyncIOMotorDatabase, document_id: str, *, project_id: str = ""
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"document_id": document_id}
    if project_id:
        query["project_id"] = project_id
    cursor = db[SOURCE_DOCUMENT_LINKS_COLLECTION].find(query, {"_id": 0}).sort(
        "last_seen_at", -1
    )
    return [doc async for doc in cursor]


async def get_document_link(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str,
    document_id: str,
) -> dict[str, Any] | None:
    if not project_id:
        return None
    return await db[SOURCE_DOCUMENT_LINKS_COLLECTION].find_one(
        {
            "link_id": document_link_id(project_id, target_id, document_id),
        },
        {"_id": 0},
    )


async def list_target_documents(
    db: AsyncIOMotorDatabase,
    target_id: str,
    *,
    project_id: str = "",
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[dict[str, Any]], int]:
    query: dict[str, Any] = {"target_id": target_id}
    if project_id:
        query["project_id"] = project_id
    total = await db[SOURCE_DOCUMENT_LINKS_COLLECTION].count_documents(query)
    links = await (
        db[SOURCE_DOCUMENT_LINKS_COLLECTION]
        .find(query, {"_id": 0})
        .sort("last_seen_at", -1)
        .skip(max(0, skip))
        .limit(max(1, min(limit, 200)))
        .to_list(max(1, min(limit, 200)))
    )
    document_ids = [str(link.get("document_id") or "") for link in links]
    documents = {
        doc["document_id"]: doc
        async for doc in db[SOURCE_DOCUMENTS_COLLECTION].find(
            {"document_id": {"$in": document_ids}}, {"_id": 0}
        )
    }
    return [
        {**link, "document": documents.get(str(link.get("document_id") or ""), {})}
        for link in links
    ], total
