"""
学者学术联系 DAO

按稳定 id 唯一索引 + upsert 增量入库，同单位再次收集只处理新增/变更：
- scholar_articles : (project_id, article_id) -> 稳定 article doc id
- scholar_contacts : (project_id, email, article_id) -> 稳定 contact doc id

合规边界：contacts 按「文章」绑定公开学术通讯邮箱，不聚合整单位名单，不含个人电话。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import (
    SCHOLAR_ARTICLES_COLLECTION,
    SCHOLAR_CONTACTS_COLLECTION,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def scholar_article_id(project_id: str, article_id: str) -> str:
    raw = f"schart:{project_id}:{article_id}".encode("utf-8")
    return "sa_" + hashlib.sha1(raw).hexdigest()[:20]


def scholar_contact_id(project_id: str, email: str, article_id: str) -> str:
    raw = f"schcon:{project_id}:{email.lower()}:{article_id}".encode("utf-8")
    return "sc_" + hashlib.sha1(raw).hexdigest()[:20]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """幂等建立索引。"""
    arts = db[SCHOLAR_ARTICLES_COLLECTION]
    await arts.create_index("doc_id", unique=True)
    await arts.create_index([("project_id", 1), ("unit", 1)])
    await arts.create_index([("project_id", 1), ("target_id", 1)])
    await arts.create_index([("project_id", 1), ("target_ids", 1)])
    await arts.create_index("updated_at")

    cons = db[SCHOLAR_CONTACTS_COLLECTION]
    await cons.create_index("doc_id", unique=True)
    await cons.create_index([("project_id", 1), ("unit", 1)])
    await cons.create_index([("project_id", 1), ("target_id", 1)])
    await cons.create_index([("project_id", 1), ("target_ids", 1)])
    await cons.create_index([("project_id", 1), ("is_corresponding", 1)])
    await cons.create_index("email")
    await cons.create_index("updated_at")


async def upsert_articles_batch(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    unit: str,
    direction: str,
    articles: list[dict[str, Any]],
    task_id: str = "",
    target_id: str = "",
) -> dict[str, int]:
    """批量增量入库文章（articles 为 scholar_tools.Article asdict 列表）。"""
    if not articles:
        return {"inserted": 0, "updated": 0, "total": 0}

    coll = db[SCHOLAR_ARTICLES_COLLECTION]
    now = _now()
    inserted = updated = 0

    for a in articles:
        article_id = str(a.get("article_id") or "").strip()
        if not article_id:
            continue
        doc_id = scholar_article_id(project_id, article_id)
        unit_verified = bool(a.get("unit_verified", False))
        match_evidence = str(a.get("match_evidence") or "")
        set_fields = {
            "doc_id": doc_id,
            "project_id": project_id,
            "target_id": target_id,
            "article_id": article_id,
            "title": a.get("title", ""),
            "year": a.get("year"),
            "doi": a.get("doi"),
            "pmcid": a.get("pmcid"),
            "unit": a.get("unit") or unit,
            "direction": a.get("direction") or direction,
            "source_keys": a.get("source_keys", []),
            "landing_page": a.get("landing_page"),
            "latest_task_id": task_id,
            "updated_at": now,
        }
        if unit_verified:
            set_fields["unit_verified"] = True
            if match_evidence:
                set_fields["match_evidence"] = match_evidence
        set_on_insert: dict[str, Any] = {"created_at": now}
        if not unit_verified:
            set_on_insert.update(
                unit_verified=False,
                match_evidence=match_evidence,
            )
        update: dict[str, Any] = {
            "$set": set_fields,
            "$setOnInsert": set_on_insert,
        }
        additions: dict[str, Any] = {}
        if task_id:
            additions["task_ids"] = task_id
        if target_id:
            additions["target_ids"] = target_id
        if additions:
            update["$addToSet"] = additions
        result = await coll.update_one({"doc_id": doc_id}, update, upsert=True)
        if result.upserted_id is not None:
            inserted += 1
        elif result.modified_count:
            updated += 1

    return {"inserted": inserted, "updated": updated, "total": inserted + updated}


async def upsert_contacts_batch(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    unit: str,
    direction: str,
    contacts: list[dict[str, Any]],
    task_id: str = "",
    target_id: str = "",
) -> dict[str, int]:
    """批量增量入库联系渠道（contacts 为 scholar_tools.Contact asdict 列表）。"""
    if not contacts:
        return {"inserted": 0, "updated": 0, "total": 0}

    coll = db[SCHOLAR_CONTACTS_COLLECTION]
    now = _now()
    inserted = updated = 0

    for c in contacts:
        email = str(c.get("email") or "").strip().lower()
        article_id = str(c.get("article_id") or "").strip()
        if not email or not article_id:
            continue
        doc_id = scholar_contact_id(project_id, email, article_id)
        unit_verified = bool(c.get("unit_verified", False))
        evidence = str(c.get("evidence") or "")
        set_fields = {
            "doc_id": doc_id,
            "project_id": project_id,
            "target_id": target_id,
            "email": email,
            "article_id": article_id,
            "source_key": c.get("source_key", ""),
            "author_name": c.get("author_name"),
            "unit": c.get("unit") or unit,
            "direction": direction,
            "email_kind": c.get("email_kind") or "",
            "latest_task_id": task_id,
            "updated_at": now,
        }
        if unit_verified:
            set_fields["unit_verified"] = True
            if evidence:
                set_fields["evidence"] = evidence
        set_on_insert = {"created_at": now}
        if not unit_verified:
            set_on_insert.update(unit_verified=False, evidence=evidence)
        update: dict[str, Any] = {
            "$set": set_fields,
            "$setOnInsert": set_on_insert,
            "$max": {"is_corresponding": bool(c.get("is_corresponding"))},
        }
        additions: dict[str, Any] = {}
        if task_id:
            additions["task_ids"] = task_id
        if target_id:
            additions["target_ids"] = target_id
        if additions:
            update["$addToSet"] = additions
        result = await coll.update_one({"doc_id": doc_id}, update, upsert=True)
        if result.upserted_id is not None:
            inserted += 1
        elif result.modified_count:
            updated += 1

    return {"inserted": inserted, "updated": updated, "total": inserted + updated}


async def query_contacts(
    db: AsyncIOMotorDatabase,
    project_id: str,
    *,
    unit: str = "",
    target_id: str = "",
    only_corresponding: bool = False,
    only_verified: bool = False,
    limit: int = 20,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    query: dict[str, Any] = {"project_id": project_id}
    if unit:
        query["unit"] = unit
    if target_id:
        query["$or"] = [
            {"target_ids": target_id},
            {"target_id": target_id},
        ]
    if only_corresponding:
        query["is_corresponding"] = True
    if only_verified:
        query["unit_verified"] = True
    coll = db[SCHOLAR_CONTACTS_COLLECTION]
    total = await coll.count_documents(query)
    pipeline: list[dict[str, Any]] = [
        {"$match": query},
        {"$addFields": {
            "_kind_rank": {"$cond": [{"$eq": ["$email_kind", "personal"]}, 0, 1]},
            "_verified_rank": {"$cond": ["$unit_verified", 0, 1]},
        }},
        {"$sort": {
            "_verified_rank": 1,
            "_kind_rank": 1,
            "is_corresponding": -1,
            "updated_at": -1,
        }},
        {"$skip": skip},
        {"$limit": limit},
        {"$lookup": {
            "from": SCHOLAR_ARTICLES_COLLECTION,
            "let": {"aid": "$article_id", "pid": "$project_id"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$project_id", "$$pid"]},
                    {"$eq": ["$article_id", "$$aid"]},
                ]}}},
                {"$project": {"_id": 0, "title": 1, "doi": 1, "pmcid": 1, "landing_page": 1, "year": 1}},
                {"$limit": 1},
            ],
            "as": "_art",
        }},
        {"$addFields": {
            "article_title": {"$ifNull": [{"$arrayElemAt": ["$_art.title", 0]}, None]},
            "article_doi": {"$ifNull": [{"$arrayElemAt": ["$_art.doi", 0]}, None]},
            "article_pmcid": {"$ifNull": [{"$arrayElemAt": ["$_art.pmcid", 0]}, None]},
            "article_landing_page": {"$ifNull": [{"$arrayElemAt": ["$_art.landing_page", 0]}, None]},
            "article_year": {"$ifNull": [{"$arrayElemAt": ["$_art.year", 0]}, None]},
        }},
        {"$project": {"_id": 0, "_art": 0, "_kind_rank": 0, "_verified_rank": 0}},
    ]
    docs = await coll.aggregate(pipeline).to_list(length=limit)
    return docs, total


async def query_articles(
    db: AsyncIOMotorDatabase,
    project_id: str,
    *,
    unit: str = "",
    only_verified: bool = False,
    limit: int = 20,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    query: dict[str, Any] = {"project_id": project_id}
    if unit:
        query["unit"] = unit
    if only_verified:
        query["unit_verified"] = True
    coll = db[SCHOLAR_ARTICLES_COLLECTION]
    total = await coll.count_documents(query)
    cursor = (
        coll.find(query, {"_id": 0})
        .sort([("unit_verified", -1), ("updated_at", -1)])
        .skip(skip)
        .limit(limit)
    )
    return [doc async for doc in cursor], total


async def list_units(db: AsyncIOMotorDatabase, project_id: str) -> list[dict[str, Any]]:
    """按单位聚合已收集的联系/通讯计数，供前端概览与筛选。"""
    pipeline = [
        {"$match": {"project_id": project_id}},
        {"$group": {
            "_id": "$unit",
            "contacts": {"$sum": 1},
            "corresponding": {"$sum": {"$cond": ["$is_corresponding", 1, 0]}},
        }},
        {"$sort": {"contacts": -1}},
    ]
    result = await db[SCHOLAR_CONTACTS_COLLECTION].aggregate(pipeline).to_list(200)
    return [
        {"unit": d["_id"] or "", "contacts": d["contacts"],
         "corresponding": d["corresponding"]}
        for d in result
    ]
