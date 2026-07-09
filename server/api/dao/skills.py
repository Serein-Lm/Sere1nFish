"""
Skills 技能库 DAO — 企业级 CRUD / 分组 / 标签 / 分场景 / 审核

集合设计:
  skills          — 技能主文档（含 content_raw 全文、meta、审核状态）
  skill_categories — 场景/分类（树形，支持多层）
  skill_tags       — 标签字典（全局复用）

审核流:
  draft → pending_review → approved / rejected
  仅 admin 可审核；普通用户提交后自动进入 pending_review
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument, ASCENDING, DESCENDING

from api.db.collections import (
    SKILLS_COLLECTION,
    SKILL_CATEGORIES_COLLECTION,
    SKILL_TAGS_COLLECTION,
)


class SkillStatus(str, Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uid() -> str:
    return uuid4().hex[:16]


_NO_ID: dict[str, int] = {"_id": 0}


# ═══════════════════════════════════════════
#  索引（应用启动时幂等调用）
# ═══════════════════════════════════════════

async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    col = db[SKILLS_COLLECTION]
    await col.create_index("skill_id", unique=True)
    await col.create_index("slug", unique=True, sparse=True)
    await col.create_index("category")
    await col.create_index("tags")
    await col.create_index("status")
    await col.create_index([("category", ASCENDING), ("status", ASCENDING)])
    await col.create_index([("status", ASCENDING), ("updated_at", DESCENDING)])
    await col.create_index(
        [("name", "text"), ("slug", "text"), ("description", "text"), ("tags", "text")],
        default_language="none",
    )

    cat_col = db[SKILL_CATEGORIES_COLLECTION]
    await cat_col.create_index("category_id", unique=True)
    await cat_col.create_index("slug", unique=True)
    await cat_col.create_index("parent_id", sparse=True)

    tag_col = db[SKILL_TAGS_COLLECTION]
    await tag_col.create_index("tag_id", unique=True)
    await tag_col.create_index("name", unique=True)


# ═══════════════════════════════════════════
#  分类 CRUD
# ═══════════════════════════════════════════

async def create_category(
    db: AsyncIOMotorDatabase,
    *,
    slug: str,
    name: str,
    description: str = "",
    parent_id: str | None = None,
    sort_order: int = 0,
) -> dict[str, Any]:
    now = _now()
    doc = {
        "category_id": _uid(),
        "slug": slug,
        "name": name,
        "description": description,
        "parent_id": parent_id,
        "sort_order": sort_order,
        "created_at": now,
        "updated_at": now,
    }
    await db[SKILL_CATEGORIES_COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return doc


async def get_category(
    db: AsyncIOMotorDatabase, category_id: str
) -> Optional[dict[str, Any]]:
    return await db[SKILL_CATEGORIES_COLLECTION].find_one({"category_id": category_id}, _NO_ID)


async def get_category_by_slug(
    db: AsyncIOMotorDatabase, slug: str
) -> Optional[dict[str, Any]]:
    return await db[SKILL_CATEGORIES_COLLECTION].find_one({"slug": slug}, _NO_ID)


async def list_categories(
    db: AsyncIOMotorDatabase, *, parent_id: str | None = None
) -> list[dict[str, Any]]:
    q: dict[str, Any] = {}
    if parent_id is not None:
        q["parent_id"] = parent_id
    cursor = db[SKILL_CATEGORIES_COLLECTION].find(q, _NO_ID).sort("sort_order", ASCENDING)
    return [doc async for doc in cursor]


async def update_category(
    db: AsyncIOMotorDatabase,
    category_id: str,
    **fields: Any,
) -> Optional[dict[str, Any]]:
    fields["updated_at"] = _now()
    fields = {k: v for k, v in fields.items() if v is not None}
    return await db[SKILL_CATEGORIES_COLLECTION].find_one_and_update(
        {"category_id": category_id},
        {"$set": fields},
        projection=_NO_ID,
        return_document=ReturnDocument.AFTER,
    )


async def delete_category(db: AsyncIOMotorDatabase, category_id: str) -> bool:
    r = await db[SKILL_CATEGORIES_COLLECTION].delete_one({"category_id": category_id})
    return bool(r.deleted_count)


async def get_category_tree(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    """返回完整分类树（两级足够，可扩展递归）"""
    all_cats = await list_categories(db)
    by_parent: dict[str | None, list] = {}
    for c in all_cats:
        pid = c.get("parent_id")
        by_parent.setdefault(pid, []).append(c)

    def _build(pid: str | None) -> list[dict]:
        children = by_parent.get(pid, [])
        for ch in children:
            ch["children"] = _build(ch["category_id"])
        return children

    return _build(None)


# ═══════════════════════════════════════════
#  标签 CRUD
# ═══════════════════════════════════════════

async def create_tag(
    db: AsyncIOMotorDatabase,
    *,
    name: str,
    color: str = "",
    description: str = "",
) -> dict[str, Any]:
    now = _now()
    doc = {
        "tag_id": _uid(),
        "name": name,
        "color": color,
        "description": description,
        "created_at": now,
    }
    await db[SKILL_TAGS_COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return doc


async def list_tags(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    cursor = db[SKILL_TAGS_COLLECTION].find({}, _NO_ID).sort("name", ASCENDING)
    return [doc async for doc in cursor]


async def get_tag(db: AsyncIOMotorDatabase, tag_id: str) -> Optional[dict[str, Any]]:
    return await db[SKILL_TAGS_COLLECTION].find_one({"tag_id": tag_id}, _NO_ID)


async def get_tag_by_name(
    db: AsyncIOMotorDatabase, name: str
) -> Optional[dict[str, Any]]:
    return await db[SKILL_TAGS_COLLECTION].find_one({"name": name}, _NO_ID)


async def update_tag(
    db: AsyncIOMotorDatabase, tag_id: str, **fields: Any
) -> Optional[dict[str, Any]]:
    fields = {k: v for k, v in fields.items() if v is not None}
    return await db[SKILL_TAGS_COLLECTION].find_one_and_update(
        {"tag_id": tag_id},
        {"$set": fields},
        projection=_NO_ID,
        return_document=ReturnDocument.AFTER,
    )


async def delete_tag(db: AsyncIOMotorDatabase, tag_id: str) -> bool:
    tag = await get_tag(db, tag_id)
    if not tag:
        return False
    tag_name = tag["name"]
    await db[SKILLS_COLLECTION].update_many(
        {"tags": tag_name},
        {"$pull": {"tags": tag_name}},
    )
    r = await db[SKILL_TAGS_COLLECTION].delete_one({"tag_id": tag_id})
    return bool(r.deleted_count)


# ═══════════════════════════════════════════
#  Skill 主体 CRUD
# ═══════════════════════════════════════════

async def create_skill(
    db: AsyncIOMotorDatabase,
    *,
    slug: str,
    name: str,
    category: str,
    description: str = "",
    content_raw: str = "",
    tags: list[str] | None = None,
    triggers: list[str] | None = None,
    anti_triggers: list[str] | None = None,
    aliases: list[str] | None = None,
    requires: list[str] | None = None,
    related: list[str] | None = None,
    file_signals: list[str] | None = None,
    risk_signals: list[str] | None = None,
    priority: int = 0,
    meta: dict[str, Any] | None = None,
    status: str = SkillStatus.DRAFT,
    created_by: str = "system",
) -> dict[str, Any]:
    now = _now()
    doc = {
        "skill_id": _uid(),
        "slug": slug,
        "name": name,
        "category": category,
        "description": description,
        "content_raw": content_raw,
        "tags": tags or [],
        "triggers": triggers or [],
        "anti_triggers": anti_triggers or [],
        "aliases": aliases or [],
        "requires": requires or [],
        "related": related or [],
        "file_signals": file_signals or [],
        "risk_signals": risk_signals or [],
        "priority": priority,
        "meta": meta or {},
        "status": status,
        "created_by": created_by,
        "reviewed_by": None,
        "review_comment": None,
        "reviewed_at": None,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    await db[SKILLS_COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return doc


async def get_skill(
    db: AsyncIOMotorDatabase, skill_id: str
) -> Optional[dict[str, Any]]:
    return await db[SKILLS_COLLECTION].find_one({"skill_id": skill_id}, _NO_ID)


async def get_skill_by_slug(
    db: AsyncIOMotorDatabase, slug: str
) -> Optional[dict[str, Any]]:
    return await db[SKILLS_COLLECTION].find_one({"slug": slug}, _NO_ID)


async def update_skill(
    db: AsyncIOMotorDatabase,
    skill_id: str,
    **fields: Any,
) -> Optional[dict[str, Any]]:
    fields["updated_at"] = _now()
    fields = {k: v for k, v in fields.items() if v is not None}
    return await db[SKILLS_COLLECTION].find_one_and_update(
        {"skill_id": skill_id},
        {"$set": fields, "$inc": {"version": 1}},
        projection=_NO_ID,
        return_document=ReturnDocument.AFTER,
    )


async def delete_skill(db: AsyncIOMotorDatabase, skill_id: str) -> bool:
    r = await db[SKILLS_COLLECTION].delete_one({"skill_id": skill_id})
    return bool(r.deleted_count)


async def list_skills(
    db: AsyncIOMotorDatabase,
    *,
    category: str | None = None,
    tag: str | None = None,
    status: str | None = None,
    search: str | None = None,
    created_by: str | None = None,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "updated_at",
    sort_order: int = DESCENDING,
    include_content: bool = False,
) -> dict[str, Any]:
    """
    分页列表查询

    Returns:
        {"items": [...], "total": N, "page": P, "page_size": S, "pages": T}
    """
    q: dict[str, Any] = {}
    if category:
        q["category"] = category
    if tag:
        q["tags"] = tag
    if status:
        q["status"] = status
    if created_by:
        q["created_by"] = created_by
    if search:
        q["$text"] = {"$search": search}

    projection = {"_id": 0}
    if not include_content:
        projection["content_raw"] = 0

    col = db[SKILLS_COLLECTION]
    total = await col.count_documents(q)
    pages = max(1, (total + page_size - 1) // page_size)

    cursor = (
        col.find(q, projection)
        .sort(sort_by, sort_order)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    items = [doc async for doc in cursor]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


async def list_skills_by_category(
    db: AsyncIOMotorDatabase,
    *,
    status: str | None = "approved",
) -> dict[str, list[dict[str, Any]]]:
    """
    按分类分组返回（不含 content_raw，供前端概览 / agent 动态披露）
    """
    q: dict[str, Any] = {}
    if status:
        q["status"] = status

    cursor = db[SKILLS_COLLECTION].find(
        q, {"_id": 0, "content_raw": 0}
    ).sort([("category", ASCENDING), ("priority", DESCENDING)])

    result: dict[str, list[dict[str, Any]]] = {}
    async for doc in cursor:
        cat = doc.get("category", "uncategorized")
        result.setdefault(cat, []).append(doc)
    return result


# ═══════════════════════════════════════════
#  审核流
# ═══════════════════════════════════════════

async def submit_for_review(
    db: AsyncIOMotorDatabase, skill_id: str
) -> Optional[dict[str, Any]]:
    return await db[SKILLS_COLLECTION].find_one_and_update(
        {"skill_id": skill_id, "status": {"$in": [SkillStatus.DRAFT, SkillStatus.REJECTED]}},
        {"$set": {"status": SkillStatus.PENDING_REVIEW, "updated_at": _now()}},
        projection=_NO_ID,
        return_document=ReturnDocument.AFTER,
    )


async def review_skill(
    db: AsyncIOMotorDatabase,
    skill_id: str,
    *,
    approved: bool,
    reviewer: str,
    comment: str = "",
) -> Optional[dict[str, Any]]:
    now = _now()
    new_status = SkillStatus.APPROVED if approved else SkillStatus.REJECTED
    return await db[SKILLS_COLLECTION].find_one_and_update(
        {"skill_id": skill_id, "status": SkillStatus.PENDING_REVIEW},
        {
            "$set": {
                "status": new_status,
                "reviewed_by": reviewer,
                "review_comment": comment,
                "reviewed_at": now,
                "updated_at": now,
            }
        },
        projection=_NO_ID,
        return_document=ReturnDocument.AFTER,
    )


async def archive_skill(
    db: AsyncIOMotorDatabase, skill_id: str
) -> Optional[dict[str, Any]]:
    return await db[SKILLS_COLLECTION].find_one_and_update(
        {"skill_id": skill_id},
        {"$set": {"status": SkillStatus.ARCHIVED, "updated_at": _now()}},
        projection=_NO_ID,
        return_document=ReturnDocument.AFTER,
    )


# ═══════════════════════════════════════════
#  批量 / 同步
# ═══════════════════════════════════════════

async def upsert_skill_by_slug(
    db: AsyncIOMotorDatabase,
    slug: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """按 slug 幂等写入（同步脚本专用）"""
    now = _now()
    data["slug"] = slug
    data["updated_at"] = now

    return await db[SKILLS_COLLECTION].find_one_and_update(
        {"slug": slug},
        {
            "$set": data,
            "$setOnInsert": {
                "skill_id": _uid(),
                "created_at": now,
                "version": 1,
            },
        },
        upsert=True,
        projection=_NO_ID,
        return_document=ReturnDocument.AFTER,
    )


async def upsert_category_by_slug(
    db: AsyncIOMotorDatabase,
    slug: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    now = _now()
    data["slug"] = slug
    data["updated_at"] = now

    return await db[SKILL_CATEGORIES_COLLECTION].find_one_and_update(
        {"slug": slug},
        {
            "$set": data,
            "$setOnInsert": {
                "category_id": _uid(),
                "created_at": now,
            },
        },
        upsert=True,
        projection=_NO_ID,
        return_document=ReturnDocument.AFTER,
    )


async def bulk_upsert_tags(
    db: AsyncIOMotorDatabase,
    tag_names: list[str],
) -> int:
    """批量创建不存在的标签，返回新增数量"""
    if not tag_names:
        return 0
    existing = {
        doc["name"]
        async for doc in db[SKILL_TAGS_COLLECTION].find(
            {"name": {"$in": tag_names}}, {"name": 1}
        )
    }
    new_tags = [n for n in tag_names if n not in existing]
    if not new_tags:
        return 0
    now = _now()
    docs = [
        {"tag_id": _uid(), "name": n, "color": "", "description": "", "created_at": now}
        for n in new_tags
    ]
    await db[SKILL_TAGS_COLLECTION].insert_many(docs)
    return len(docs)


async def count_by_status(db: AsyncIOMotorDatabase) -> dict[str, int]:
    """统计各状态的 skill 数量"""
    pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    result = {}
    async for doc in db[SKILLS_COLLECTION].aggregate(pipeline):
        result[doc["_id"]] = doc["count"]
    return result
