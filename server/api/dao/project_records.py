"""Whitelisted read access for project-scoped collections without dedicated list DAOs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import (
    COMPANY_META_COLLECTION,
    COMPANY_SCAN_COLLECTION,
    COPYWRITINGS_COLLECTION,
    PROFILE_COPYWRITINGS_COLLECTION,
    PROFILES_COLLECTION,
    TOKEN_USAGE_RECORDS_COLLECTION,
    URL_SCAN_COPYWRITINGS_COLLECTION,
    URL_SCAN_FINDINGS_COLLECTION,
    URL_SCAN_RESULTS_COLLECTION,
    URL_SCAN_TASKS_COLLECTION,
)


@dataclass(frozen=True)
class ProjectRecordSource:
    collection: str
    sort_field: str


PROJECT_RECORD_SOURCES: dict[str, ProjectRecordSource] = {
    "company_meta": ProjectRecordSource(COMPANY_META_COLLECTION, "updated_at"),
    "company_scans": ProjectRecordSource(COMPANY_SCAN_COLLECTION, "updated_at"),
    "copywritings": ProjectRecordSource(COPYWRITINGS_COLLECTION, "created_at"),
    "profiles": ProjectRecordSource(PROFILES_COLLECTION, "updated_at"),
    "profile_copywritings": ProjectRecordSource(PROFILE_COPYWRITINGS_COLLECTION, "_id"),
    "token_usage": ProjectRecordSource(TOKEN_USAGE_RECORDS_COLLECTION, "timestamp"),
    "url_scan_tasks": ProjectRecordSource(URL_SCAN_TASKS_COLLECTION, "updated_at"),
    "url_scan_results": ProjectRecordSource(URL_SCAN_RESULTS_COLLECTION, "_id"),
    "url_scan_findings": ProjectRecordSource(URL_SCAN_FINDINGS_COLLECTION, "_id"),
    "url_scan_copywritings": ProjectRecordSource(URL_SCAN_COPYWRITINGS_COLLECTION, "_id"),
}


async def list_project_records(
    db: AsyncIOMotorDatabase,
    source: str,
    project_id: str,
    *,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """Read one registered collection; arbitrary collection names are rejected."""
    spec = PROJECT_RECORD_SOURCES.get(str(source or "").strip())
    if spec is None:
        raise ValueError(f"未知项目记录源: {source!r}")
    query = {"project_id": project_id}
    bounded_limit = max(1, min(int(limit or 50), 200))
    collection = db[spec.collection]
    total = await collection.count_documents(query)
    cursor = (
        collection.find(query, {"_id": 0})
        .sort(spec.sort_field, -1)
        .skip(max(0, int(skip or 0)))
        .limit(bounded_limit)
    )
    return await cursor.to_list(bounded_limit), total
