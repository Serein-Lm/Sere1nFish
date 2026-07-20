"""
学者学术联系 DAO

按稳定 id 唯一索引 + upsert 增量入库，同单位再次收集只处理新增/变更：
- scholar_articles : (project_id, article_id) -> 稳定 article doc id
- scholar_contacts : (project_id, email, article_id) -> 稳定 contact doc id

合规边界：contacts 按「文章」绑定公开学术通讯邮箱，不聚合整单位名单，不含个人电话。
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import UpdateOne

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


def scholar_article_url(article: dict[str, Any]) -> str:
    doi = str(article.get("doi") or "").strip()
    if doi.startswith("10."):
        return f"https://doi.org/{doi}"
    pmcid = str(article.get("pmcid") or "").strip().upper()
    if pmcid.startswith("PMC") and pmcid[3:].isdigit():
        return f"https://europepmc.org/article/PMC/{pmcid[3:]}"
    landing_page = str(article.get("landing_page") or "").strip()
    if landing_page.lower().startswith(("http://", "https://")):
        return landing_page
    return ""


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """幂等建立索引。"""
    arts = db[SCHOLAR_ARTICLES_COLLECTION]
    await arts.create_index("doc_id", unique=True)
    await arts.create_index(
        [("project_id", 1), ("article_id", 1)],
        unique=True,
    )
    await arts.create_index([("project_id", 1), ("unit", 1)])
    await arts.create_index([("project_id", 1), ("target_id", 1)])
    await arts.create_index([("project_id", 1), ("target_ids", 1)])
    await arts.create_index("updated_at")

    cons = db[SCHOLAR_CONTACTS_COLLECTION]
    await cons.create_index("doc_id", unique=True)
    await cons.create_index([("project_id", 1), ("article_id", 1)])
    await cons.create_index([("project_id", 1), ("unit", 1)])
    await cons.create_index([("project_id", 1), ("target_id", 1)])
    await cons.create_index([("project_id", 1), ("target_ids", 1)])
    await cons.create_index([("project_id", 1), ("is_corresponding", 1)])
    await cons.create_index("email")
    await cons.create_index("updated_at")


async def backfill_contact_article_urls(
    db: AsyncIOMotorDatabase,
    *,
    batch_size: int = 500,
) -> int:
    """幂等补齐旧联系记录的可访问文章来源；无来源记录写空值并保持隐藏。"""
    contacts = db[SCHOLAR_CONTACTS_COLLECTION]
    pending = await contacts.find(
        {"article_url": {"$exists": False}},
        {"_id": 0, "doc_id": 1, "project_id": 1, "article_id": 1},
    ).to_list(None)
    updated = 0
    safe_batch_size = max(50, min(int(batch_size), 1000))
    for offset in range(0, len(pending), safe_batch_size):
        batch = pending[offset : offset + safe_batch_size]
        project_ids = list({str(item.get("project_id") or "") for item in batch})
        article_ids = list({str(item.get("article_id") or "") for item in batch})
        articles = await db[SCHOLAR_ARTICLES_COLLECTION].find(
            {
                "project_id": {"$in": project_ids},
                "article_id": {"$in": article_ids},
            },
            {
                "_id": 0,
                "project_id": 1,
                "article_id": 1,
                "doi": 1,
                "pmcid": 1,
                "landing_page": 1,
            },
        ).to_list(None)
        article_urls = {
            (str(item.get("project_id") or ""), str(item.get("article_id") or "")):
                scholar_article_url(item)
            for item in articles
        }
        operations = [
            UpdateOne(
                {"doc_id": item["doc_id"]},
                {
                    "$set": {
                        "article_url": article_urls.get(
                            (
                                str(item.get("project_id") or ""),
                                str(item.get("article_id") or ""),
                            ),
                            "",
                        )
                    }
                },
            )
            for item in batch
            if item.get("doc_id")
        ]
        if operations:
            result = await contacts.bulk_write(operations, ordered=False)
            updated += int(result.modified_count)
    return updated


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

    article_ids = {
        str(contact.get("article_id") or "").strip()
        for contact in contacts
        if str(contact.get("email") or "").strip()
        and str(contact.get("article_id") or "").strip()
    }
    article_docs = await db[SCHOLAR_ARTICLES_COLLECTION].find(
        {
            "project_id": project_id,
            "article_id": {"$in": list(article_ids)},
        },
        {
            "_id": 0,
            "article_id": 1,
            "doi": 1,
            "pmcid": 1,
            "landing_page": 1,
        },
    ).to_list(None)
    article_urls = {
        str(article.get("article_id") or ""): scholar_article_url(article)
        for article in article_docs
        if scholar_article_url(article)
    }
    if not article_urls:
        return {"inserted": 0, "updated": 0, "total": 0}

    coll = db[SCHOLAR_CONTACTS_COLLECTION]
    now = _now()
    inserted = updated = 0

    for c in contacts:
        email = str(c.get("email") or "").strip().lower()
        article_id = str(c.get("article_id") or "").strip()
        if not email or article_id not in article_urls:
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
            "article_url": article_urls[article_id],
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


def _contact_article_join_stages() -> list[dict[str, Any]]:
    """为一页有效联系记录关联文章来源展示字段。"""
    return [
        {
            "$lookup": {
                "from": SCHOLAR_ARTICLES_COLLECTION,
                "let": {"aid": "$article_id", "pid": "$project_id"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$project_id", "$$pid"]},
                                    {"$eq": ["$article_id", "$$aid"]},
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "title": 1,
                            "doi": 1,
                            "pmcid": 1,
                            "landing_page": 1,
                            "year": 1,
                        }
                    },
                    {"$limit": 1},
                ],
                "as": "_art",
            }
        },
        {
            "$addFields": {
                "article_title": {
                    "$ifNull": [{"$arrayElemAt": ["$_art.title", 0]}, None]
                },
                "article_doi": {
                    "$ifNull": [{"$arrayElemAt": ["$_art.doi", 0]}, None]
                },
                "article_pmcid": {
                    "$ifNull": [{"$arrayElemAt": ["$_art.pmcid", 0]}, None]
                },
                "article_landing_page": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$_art.landing_page", 0]},
                        None,
                    ]
                },
                "article_year": {
                    "$ifNull": [{"$arrayElemAt": ["$_art.year", 0]}, None]
                },
            }
        },
    ]


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
    query: dict[str, Any] = {
        "project_id": project_id,
        "email": {"$nin": [None, ""]},
        "article_url": {"$regex": r"^https?://", "$options": "i"},
    }
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
    items_pipeline: list[dict[str, Any]] = [
        {"$match": query},
        {
            "$addFields": {
                "_kind_rank": {
                    "$cond": [{"$eq": ["$email_kind", "personal"]}, 0, 1]
                },
                "_verified_rank": {"$cond": ["$unit_verified", 0, 1]},
            }
        },
        {
            "$sort": {
                "_verified_rank": 1,
                "_kind_rank": 1,
                "is_corresponding": -1,
                "updated_at": -1,
            }
        },
        {"$skip": skip},
        {"$limit": limit},
        *_contact_article_join_stages(),
        {
            "$project": {
                "_id": 0,
                "_art": 0,
                "_kind_rank": 0,
                "_verified_rank": 0,
            }
        },
    ]
    docs, total = await asyncio.gather(
        coll.aggregate(items_pipeline).to_list(length=limit),
        coll.count_documents(query),
    )
    return docs, int(total)


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


async def count_contacts_by_target(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_ids: list[str],
) -> dict[str, int]:
    selected = [str(value or "").strip() for value in target_ids if str(value or "").strip()]
    counts = {target_id: 0 for target_id in selected}
    if not selected:
        return counts
    pipeline = [
        {"$match": {
            "project_id": project_id,
            "email": {"$nin": [None, ""]},
            "article_url": {"$regex": r"^https?://", "$options": "i"},
            "$or": [
                {"target_ids": {"$in": selected}},
                {"target_id": {"$in": selected}},
            ],
        }},
        {
            "$set": {
                "_resolved_target_ids": {
                    "$setUnion": [
                        {
                            "$cond": [
                                {"$isArray": "$target_ids"},
                                "$target_ids",
                                [],
                            ]
                        },
                        {
                            "$cond": [
                                {"$in": ["$target_id", selected]},
                                ["$target_id"],
                                [],
                            ]
                        },
                    ]
                }
            }
        },
        {"$unwind": "$_resolved_target_ids"},
        {"$match": {"_resolved_target_ids": {"$in": selected}}},
        {
            "$group": {
                "_id": "$_resolved_target_ids",
                "scholar_contact_count": {"$sum": 1},
            }
        },
    ]
    rows = await db[SCHOLAR_CONTACTS_COLLECTION].aggregate(pipeline).to_list(
        len(selected)
    )
    for row in rows:
        target_id = str(row.get("_id") or "")
        if target_id in counts:
            counts[target_id] = int(row.get("scholar_contact_count") or 0)
    return counts


async def list_units(db: AsyncIOMotorDatabase, project_id: str) -> list[dict[str, Any]]:
    """按单位聚合已收集的联系/通讯计数，供前端概览与筛选。"""
    pipeline = [
        {"$match": {
            "project_id": project_id,
            "email": {"$nin": [None, ""]},
            "article_url": {"$regex": r"^https?://", "$options": "i"},
        }},
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
