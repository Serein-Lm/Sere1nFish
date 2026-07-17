"""
公司元信息 DAO

存储公司名规范化结果：规范化全称、根域名、别名、置信度、来源。
按 (project_id, 原始输入名) 稳定 meta_id 做 upsert，支持复用与更新。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import COMPANY_META_COLLECTION


def _now() -> datetime:
    return datetime.now(timezone.utc)


def company_meta_id(project_id: str, input_name: str) -> str:
    """一个 (project, 原始公司名) 对应确定的元信息 id。"""
    raw = f"company:{project_id}:{input_name.strip()}".encode("utf-8")
    return "cm_" + hashlib.sha1(raw).hexdigest()[:20]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """幂等建立索引。"""
    coll = db[COMPANY_META_COLLECTION]
    await coll.create_index("meta_id", unique=True)
    await coll.create_index([("project_id", 1), ("normalized_name", 1)])
    await coll.create_index("root_domain")
    await coll.create_index("target_id", sparse=True)
    await coll.create_index([("project_id", 1), ("relation.parent_target_id", 1)])


async def upsert_company_meta(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    input_name: str,
    normalized_name: str,
    root_domain: str,
    aliases: list[str] | None = None,
    confidence: float | None = None,
    source: str = "",
    task_id: str = "",
    target_id: str = "",
    icp_domains: list[str] | None = None,
    relation: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """写入/更新一条公司元信息，返回最新文档。"""
    meta_id = company_meta_id(project_id, input_name)
    now = _now()
    set_fields = {
        "meta_id": meta_id,
        "project_id": project_id,
        "input_name": input_name.strip(),
        "normalized_name": normalized_name.strip(),
        "root_domain": root_domain.strip(),
        "aliases": aliases or [],
        "confidence": confidence,
        "source": source,
        "latest_task_id": task_id,
        "updated_at": now,
    }
    if target_id:
        set_fields["target_id"] = target_id
    if icp_domains is not None:
        set_fields["icp_domains"] = list(
            dict.fromkeys(str(domain).strip() for domain in icp_domains if str(domain).strip())
        )
    if relation is not None:
        set_fields["relation"] = relation
    if provenance is not None:
        set_fields["provenance"] = provenance
    await db[COMPANY_META_COLLECTION].update_one(
        {"meta_id": meta_id},
        {"$set": set_fields, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return await get_company_meta(db, project_id, input_name) or set_fields


async def get_company_meta(
    db: AsyncIOMotorDatabase,
    project_id: str,
    input_name: str,
) -> dict[str, Any] | None:
    """按原始输入名读取已缓存的公司元信息（供增量复用）。"""
    meta_id = company_meta_id(project_id, input_name)
    return await db[COMPANY_META_COLLECTION].find_one({"meta_id": meta_id}, {"_id": 0})


async def get_company_meta_by_id(
    db: AsyncIOMotorDatabase,
    meta_id: str,
) -> dict[str, Any] | None:
    """按稳定 meta_id 直接读取公司元信息（供上下文聚合层按 person.company_meta_id 解析）。"""
    if not meta_id:
        return None
    return await db[COMPANY_META_COLLECTION].find_one({"meta_id": meta_id}, {"_id": 0})


async def find_company_meta_by_root_domain(
    db: AsyncIOMotorDatabase,
    root_domain: str,
) -> dict[str, Any] | None:
    """按根域名读取一条公司元信息（取最近更新的一条）。"""
    if not root_domain:
        return None
    return await db[COMPANY_META_COLLECTION].find_one(
        {"root_domain": root_domain.strip()}, {"_id": 0}, sort=[("updated_at", -1)]
    )
