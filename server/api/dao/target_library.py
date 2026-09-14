"""Global library projections; source content and version identities stay in place."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from pymongo import UpdateOne
from api.db.collections import (
    TARGETS_COLLECTION, PROJECT_TARGETS_COLLECTION, TARGET_RELATIONSHIPS_COLLECTION,
    PROJECTS_COLLECTION, SOURCE_DOCUMENT_LINKS_COLLECTION, SOURCE_DOCUMENTS_COLLECTION,
    SOURCE_DOCUMENT_VERSIONS_COLLECTION, MOBILE_COLLECT_RECORDS_COLLECTION,
    MOBILE_COLLECT_TASKS_COLLECTION, MOBILE_COLLECT_CHECKPOINTS_COLLECTION,
    TASKS_COLLECTION, BIDDING_RECORDS_COLLECTION,
)


async def ensure_indexes(db) -> None:
    await db[TARGETS_COLLECTION].create_index("library_identity.canonical_target_id", sparse=True)
    await db[TASKS_COLLECTION].create_index([("params.target_id", 1), ("started_at", -1)])
    await db[MOBILE_COLLECT_RECORDS_COLLECTION].create_index([("target_id", 1), ("last_seen_at", -1)])


async def inventory(db) -> tuple:
    targets, relations, edges, projects = await asyncio.gather(
        db[TARGETS_COLLECTION].find({}, {key: 1 for key in ["target_id", "canonical_name", "display_name", "identity_aliases", "target_type", "created_at", "library_identity"]}).to_list(None),
        db[PROJECT_TARGETS_COLLECTION].find({}, {"_id": 0, "goals": 0, "company_meta": 0}).to_list(None),
        db[TARGET_RELATIONSHIPS_COLLECTION].find({}, {"_id": 0}).to_list(None),
        db[PROJECTS_COLLECTION].find({}, {"name": 1}).to_list(None),
    )
    for item in targets:
        item.pop("_id", None)
    return targets, relations, edges, projects


async def save_identity_projection(db, targets: list[dict], mapping: dict[str, str], policy_version: int) -> None:
    now, operations = datetime.now(timezone.utc), []
    for target in targets:
        anchor = mapping[target["target_id"]]
        previous = target.get("library_identity") or {}
        if previous.get("canonical_target_id") == anchor and previous.get("policy_version") == policy_version:
            continue
        identity = {"canonical_target_id": anchor, "policy_version": policy_version, "resolved_at": now, "basis": "complete_name_identity_alias_and_direct_parent"}
        update: dict[str, Any] = {"$set": {"library_identity": identity}}
        if previous:
            update["$push"] = {"library_identity_history": previous}
        operations.append(UpdateOne({"target_id": target["target_id"]}, update))
    if operations:
        await db[TARGETS_COLLECTION].bulk_write(operations, ordered=False)


def _scope(rows: list[dict]) -> tuple[list[str], dict]:
    ids = [value for row in rows for value in row["member_target_ids"]]
    expression = {"$switch": {"branches": [{"case": {"$in": ["$target_id", row["member_target_ids"]]}, "then": row["target_id"]} for row in rows], "default": "$target_id"}}
    return ids, expression


async def _document_metrics(db, ids: list[str], identity: dict) -> dict:
    # Avoid one nested lookup per document (and loading entire archived payloads).
    links = await db[SOURCE_DOCUMENT_LINKS_COLLECTION].aggregate([
        {"$match": {"target_id": {"$in": ids}}},
        {"$group": {"_id": {"target_id": identity, "document_id": "$document_id"}}},
    ]).to_list(None)
    document_ids = list({row["_id"]["document_id"] for row in links})
    if not document_ids:
        return {}
    documents, versions = await asyncio.gather(
        db[SOURCE_DOCUMENTS_COLLECTION].find(
            {"document_id": {"$in": document_ids}},
            {"_id": 0, "document_id": 1, "first_seen_at": 1, "last_seen_at": 1},
        ).to_list(None),
        db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].aggregate([
            {"$match": {"document_id": {"$in": document_ids}, "status": "ready"}},
            {"$group": {"_id": "$document_id", "count": {"$sum": 1}}},
        ]).to_list(None),
    )
    by_id = {item["document_id"]: item for item in documents}
    version_counts = {item["_id"]: item["count"] for item in versions}
    result = {}
    for link in links:
        key = link["_id"]
        document = by_id.get(key["document_id"])
        if not document:
            continue
        item = result.setdefault(key["target_id"], {"document_count": 0, "version_count": 0, "change_count": 0, "first_archived_at": None, "last_archived_at": None})
        count = version_counts.get(key["document_id"], 0)
        item["document_count"] += 1
        item["version_count"] += count
        item["change_count"] += max(0, count - 1)
        for output, source, choose in (("first_archived_at", "first_seen_at", min), ("last_archived_at", "last_seen_at", max)):
            value = document.get(source)
            if value is not None:
                item[output] = choose(item[output], value) if item[output] is not None else value
    return result


async def archive_metrics(db, rows: list[dict]) -> dict:
    """Current-page summaries from four bulk reads, without raw source bodies."""
    if not rows:
        return {}
    ids, identity = _scope(rows)
    archives, mobiles = await asyncio.gather(
        _document_metrics(db, ids, identity),
        db[MOBILE_COLLECT_RECORDS_COLLECTION].aggregate([
            {"$match": {"target_id": {"$in": ids}, "superseded_by_record_id": {"$in": [None, ""]}}},
            {"$group": {"_id": identity, "mobile_count": {"$sum": 1}, "first_collected_at": {"$min": "$first_seen_at"}, "last_collected_at": {"$max": "$last_seen_at"}}},
        ]).to_list(None),
    )
    for item in mobiles:
        archives.setdefault(item.pop("_id"), {}).update(item)
    return archives


async def task_inventory(db) -> tuple:
    return await asyncio.gather(
        db[TASKS_COLLECTION].find({}, {"_id": 0, "task_id": 1, "project_id": 1, "task_type": 1, "status": 1, "started_at": 1, "completed_at": 1, "created_at": 1, "updated_at": 1, "params.target_id": 1, "params.company_name": 1, "params.task_def_id": 1, "params.incremental_scan": 1, "progress.stage": 1, "progress.message": 1, "progress.current": 1, "progress.total": 1, "result.identity.target_id": 1, "result.incremental_window": 1, "result.incremental_cursor_advanced": 1}).to_list(None),
        db[MOBILE_COLLECT_TASKS_COLLECTION].find({}, {"_id": 0, "task_def_id": 1, "project_id": 1, "target_id": 1, "target_ids": 1, "incremental_by_time": 1}).to_list(None),
        db[MOBILE_COLLECT_CHECKPOINTS_COLLECTION].find({"kind": "publication_time_window"}, {"_id": 0, "run_task_id": 1, "targets": 1, "since": 1, "until": 1}).to_list(None),
    )


async def owned_bidding_counts(db, rows: list[dict], ownership_scope) -> dict[str, int]:
    if not rows:
        return {}
    ids, _ = _scope(rows)
    mapping = {value: row["target_id"] for row in rows for value in row["member_target_ids"]}
    counts = {row["target_id"]: 0 for row in rows}
    # Search associations are only candidates; named purchaser/agency/winner
    # still has to pass the same ownership policy as project views.
    cursor = db[BIDDING_RECORDS_COLLECTION].find({"target_ids": {"$in": ids}}, {"_id": 0, "purchaser": 1, "agency": 1, "winner": 1})
    async for item in cursor:
        for anchor in {mapping[key] for key in ownership_scope.owner_ids(item) if key in mapping}:
            counts[anchor] += 1
    return counts


async def document_ids(db, target_ids: list[str]) -> list[str]:
    return await db[SOURCE_DOCUMENT_LINKS_COLLECTION].distinct("document_id", {"target_id": {"$in": target_ids}})


async def list_documents(db, target_ids: list[str], skip: int, limit: int) -> dict:
    ids = await document_ids(db, target_ids)
    query = {"document_id": {"$in": ids}}
    projection = {key: 1 for key in ["document_id", "canonical_url", "title", "source_type", "publish_time", "first_seen_at", "last_seen_at", "latest_version_id", "summary"]}
    projection["_id"] = 0
    items = await db[SOURCE_DOCUMENTS_COLLECTION].find(query, projection).sort([("last_seen_at", -1), ("document_id", 1)]).skip(skip).limit(limit).to_list(limit)
    return {"items": items, "total": await db[SOURCE_DOCUMENTS_COLLECTION].count_documents(query), "skip": skip, "limit": limit}


async def list_versions(db, target_ids: list[str], skip: int, limit: int, document_id: str = "") -> dict:
    ids = await document_ids(db, target_ids)
    if document_id:
        ids = [document_id] if document_id in ids else []
    query = {"document_id": {"$in": ids}}
    projection = {key: 1 for key in ["document_id", "version_id", "content_hash", "captured_at", "source_type", "status", "identity", "storage_object_ids"]}
    projection["_id"] = 0
    items = await db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].find(query, projection).sort([("captured_at", -1), ("version_id", 1)]).skip(skip).limit(limit).to_list(limit)
    return {"items": items, "total": await db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].count_documents(query), "skip": skip, "limit": limit}


async def list_mobile(db, target_ids: list[str], skip: int, limit: int) -> dict:
    query = {"target_id": {"$in": target_ids}, "superseded_by_record_id": {"$in": [None, ""]}}
    items = await db[MOBILE_COLLECT_RECORDS_COLLECTION].find(query, {"_id": 0}).sort([("last_seen_at", -1), ("record_id", 1)]).skip(skip).limit(limit).to_list(limit)
    return {"items": items, "total": await db[MOBILE_COLLECT_RECORDS_COLLECTION].count_documents(query), "skip": skip, "limit": limit}


async def previous_version(db, after: dict) -> dict | None:
    return await db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].find_one({"document_id": after["document_id"], "status": "ready", "$or": [{"captured_at": {"$lt": after["captured_at"]}}, {"captured_at": after["captured_at"], "version_id": {"$lt": after["version_id"]}}]}, {"_id": 0, "version_id": 1, "captured_at": 1, "content.text": 1}, sort=[("captured_at", -1), ("version_id", -1)])
