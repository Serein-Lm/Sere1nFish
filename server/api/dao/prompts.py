"""
Prompts 提示词库 DAO — 企业级 CRUD / 分组 / 标签 / 审核

集合设计:
  prompts           — 提示词主文档
  prompt_categories  — 分类
  prompt_tags        — 标签字典

审核流同 Skills: draft → pending_review → approved / rejected
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument, ASCENDING, DESCENDING

from api.db.collections import (
    PROMPTS_COLLECTION,
    PROMPT_CATEGORIES_COLLECTION,
    PROMPT_TAGS_COLLECTION,
)


class PromptStatus(str, Enum):
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
#  索引
# ═══════════════════════════════════════════

async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    col = db[PROMPTS_COLLECTION]
    await col.create_index("prompt_id", unique=True)
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

    cat_col = db[PROMPT_CATEGORIES_COLLECTION]
    await cat_col.create_index("category_id", unique=True)
    await cat_col.create_index("slug", unique=True)
    await cat_col.create_index("parent_id", sparse=True)

    tag_col = db[PROMPT_TAGS_COLLECTION]
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
    await db[PROMPT_CATEGORIES_COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return doc


async def get_category(
    db: AsyncIOMotorDatabase, category_id: str
) -> Optional[dict[str, Any]]:
    return await db[PROMPT_CATEGORIES_COLLECTION].find_one({"category_id": category_id}, _NO_ID)


async def get_category_by_slug(
    db: AsyncIOMotorDatabase, slug: str
) -> Optional[dict[str, Any]]:
    return await db[PROMPT_CATEGORIES_COLLECTION].find_one({"slug": slug}, _NO_ID)


async def list_categories(
    db: AsyncIOMotorDatabase, *, parent_id: str | None = None
) -> list[dict[str, Any]]:
    q: dict[str, Any] = {}
    if parent_id is not None:
        q["parent_id"] = parent_id
    cursor = db[PROMPT_CATEGORIES_COLLECTION].find(q, _NO_ID).sort("sort_order", ASCENDING)
    return [doc async for doc in cursor]


async def update_category(
    db: AsyncIOMotorDatabase,
    category_id: str,
    **fields: Any,
) -> Optional[dict[str, Any]]:
    fields["updated_at"] = _now()
    fields = {k: v for k, v in fields.items() if v is not None}
    return await db[PROMPT_CATEGORIES_COLLECTION].find_one_and_update(
        {"category_id": category_id},
        {"$set": fields},
        projection=_NO_ID,
        return_document=ReturnDocument.AFTER,
    )


async def delete_category(db: AsyncIOMotorDatabase, category_id: str) -> bool:
    r = await db[PROMPT_CATEGORIES_COLLECTION].delete_one({"category_id": category_id})
    return bool(r.deleted_count)


async def get_category_tree(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
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
    await db[PROMPT_TAGS_COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return doc


async def list_tags(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    cursor = db[PROMPT_TAGS_COLLECTION].find({}, _NO_ID).sort("name", ASCENDING)
    return [doc async for doc in cursor]


async def get_tag(db: AsyncIOMotorDatabase, tag_id: str) -> Optional[dict[str, Any]]:
    return await db[PROMPT_TAGS_COLLECTION].find_one({"tag_id": tag_id}, _NO_ID)


async def get_tag_by_name(
    db: AsyncIOMotorDatabase, name: str
) -> Optional[dict[str, Any]]:
    return await db[PROMPT_TAGS_COLLECTION].find_one({"name": name}, _NO_ID)


async def update_tag(
    db: AsyncIOMotorDatabase, tag_id: str, **fields: Any
) -> Optional[dict[str, Any]]:
    fields = {k: v for k, v in fields.items() if v is not None}
    return await db[PROMPT_TAGS_COLLECTION].find_one_and_update(
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
    await db[PROMPTS_COLLECTION].update_many(
        {"tags": tag_name},
        {"$pull": {"tags": tag_name}},
    )
    r = await db[PROMPT_TAGS_COLLECTION].delete_one({"tag_id": tag_id})
    return bool(r.deleted_count)


# ═══════════════════════════════════════════
#  Prompt 主体 CRUD
# ═══════════════════════════════════════════

async def create_prompt(
    db: AsyncIOMotorDatabase,
    *,
    slug: str,
    name: str,
    category: str,
    description: str = "",
    content: str = "",
    system_prompt: str = "",
    user_prompt_template: str = "",
    variables: list[str] | None = None,
    tags: list[str] | None = None,
    model_hint: str = "",
    temperature: float | None = None,
    max_tokens: int | None = None,
    meta: dict[str, Any] | None = None,
    status: str = PromptStatus.DRAFT,
    created_by: str = "system",
) -> dict[str, Any]:
    now = _now()
    doc = {
        "prompt_id": _uid(),
        "slug": slug,
        "name": name,
        "category": category,
        "description": description,
        "content": content,
        "system_prompt": system_prompt,
        "user_prompt_template": user_prompt_template,
        "variables": variables or [],
        "tags": tags or [],
        "model_hint": model_hint,
        "temperature": temperature,
        "max_tokens": max_tokens,
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
    await db[PROMPTS_COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return doc


async def get_prompt(
    db: AsyncIOMotorDatabase, prompt_id: str
) -> Optional[dict[str, Any]]:
    return await db[PROMPTS_COLLECTION].find_one({"prompt_id": prompt_id}, _NO_ID)


async def get_prompt_by_slug(
    db: AsyncIOMotorDatabase, slug: str
) -> Optional[dict[str, Any]]:
    return await db[PROMPTS_COLLECTION].find_one({"slug": slug}, _NO_ID)


async def update_prompt(
    db: AsyncIOMotorDatabase,
    prompt_id: str,
    **fields: Any,
) -> Optional[dict[str, Any]]:
    fields["updated_at"] = _now()
    fields = {k: v for k, v in fields.items() if v is not None}
    return await db[PROMPTS_COLLECTION].find_one_and_update(
        {"prompt_id": prompt_id},
        {"$set": fields, "$inc": {"version": 1}},
        projection=_NO_ID,
        return_document=ReturnDocument.AFTER,
    )


async def delete_prompt(db: AsyncIOMotorDatabase, prompt_id: str) -> bool:
    r = await db[PROMPTS_COLLECTION].delete_one({"prompt_id": prompt_id})
    return bool(r.deleted_count)


async def list_prompts(
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
) -> dict[str, Any]:
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

    col = db[PROMPTS_COLLECTION]
    total = await col.count_documents(q)
    pages = max(1, (total + page_size - 1) // page_size)

    cursor = (
        col.find(q, {"_id": 0})
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


# ═══════════════════════════════════════════
#  审核流
# ═══════════════════════════════════════════

async def submit_for_review(
    db: AsyncIOMotorDatabase, prompt_id: str
) -> Optional[dict[str, Any]]:
    return await db[PROMPTS_COLLECTION].find_one_and_update(
        {"prompt_id": prompt_id, "status": {"$in": [PromptStatus.DRAFT, PromptStatus.REJECTED]}},
        {"$set": {"status": PromptStatus.PENDING_REVIEW, "updated_at": _now()}},
        projection=_NO_ID,
        return_document=ReturnDocument.AFTER,
    )


async def review_prompt(
    db: AsyncIOMotorDatabase,
    prompt_id: str,
    *,
    approved: bool,
    reviewer: str,
    comment: str = "",
) -> Optional[dict[str, Any]]:
    now = _now()
    new_status = PromptStatus.APPROVED if approved else PromptStatus.REJECTED
    return await db[PROMPTS_COLLECTION].find_one_and_update(
        {"prompt_id": prompt_id, "status": PromptStatus.PENDING_REVIEW},
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


async def archive_prompt(
    db: AsyncIOMotorDatabase, prompt_id: str
) -> Optional[dict[str, Any]]:
    return await db[PROMPTS_COLLECTION].find_one_and_update(
        {"prompt_id": prompt_id},
        {"$set": {"status": PromptStatus.ARCHIVED, "updated_at": _now()}},
        projection=_NO_ID,
        return_document=ReturnDocument.AFTER,
    )


# ═══════════════════════════════════════════
#  批量 / 同步
# ═══════════════════════════════════════════

async def upsert_prompt_by_slug(
    db: AsyncIOMotorDatabase,
    slug: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    now = _now()
    data["slug"] = slug
    data["updated_at"] = now

    return await db[PROMPTS_COLLECTION].find_one_and_update(
        {"slug": slug},
        {
            "$set": data,
            "$setOnInsert": {
                "prompt_id": _uid(),
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

    return await db[PROMPT_CATEGORIES_COLLECTION].find_one_and_update(
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
    if not tag_names:
        return 0
    existing = {
        doc["name"]
        async for doc in db[PROMPT_TAGS_COLLECTION].find(
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
    await db[PROMPT_TAGS_COLLECTION].insert_many(docs)
    return len(docs)


async def count_by_status(db: AsyncIOMotorDatabase) -> dict[str, int]:
    pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    result = {}
    async for doc in db[PROMPTS_COLLECTION].aggregate(pipeline):
        result[doc["_id"]] = doc["count"]
    return result
