"""
XHS 小红书社工信息采集 - DAO 层实现
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from api.db.collections import (
    XHS_COOKIES_COLLECTION,
    XHS_SEARCH_TASKS_COLLECTION,
    XHS_NOTES_COLLECTION,
    XHS_NOTE_DETAILS_COLLECTION,
    XHS_PROFILES_COLLECTION,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _oid(id_str: str) -> ObjectId | None:
    try:
        return ObjectId(id_str)
    except Exception:
        return None


# ==================== Cookie 管理 ====================

async def create_cookie(
    db: AsyncIOMotorDatabase,
    account_name: str,
    cookie_string: str,
) -> dict[str, Any]:
    """创建或更新 Cookie"""
    now = _now()
    doc = await db[XHS_COOKIES_COLLECTION].find_one_and_update(
        {"account_name": account_name},
        {
            "$setOnInsert": {
                "account_name": account_name,
                "created_at": now,
                "is_enabled": True,
                "lease_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "consecutive_failures": 0,
            },
            "$set": {
                "cookie_string": cookie_string,
                "is_active": False,
                "is_valid": None,
                "last_verified_at": None,
                "last_error": None,
                "cooldown_until": None,
                "consecutive_failures": 0,
                "quarantined_at": None,
                "quarantine_reason": None,
                "updated_at": now,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc


async def get_cookie_by_name(
    db: AsyncIOMotorDatabase,
    account_name: str,
) -> dict[str, Any] | None:
    """根据账号名获取 Cookie"""
    return await db[XHS_COOKIES_COLLECTION].find_one({"account_name": account_name})


async def get_active_cookie(db: AsyncIOMotorDatabase) -> dict[str, Any] | None:
    """获取当前激活的 Cookie"""
    return await db[XHS_COOKIES_COLLECTION].find_one({"is_active": True})


async def list_cookies(
    db: AsyncIOMotorDatabase,
    limit: int = 50,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """列出所有 Cookie，返回 (items, total)"""
    query: dict[str, Any] = {}
    total = await db[XHS_COOKIES_COLLECTION].count_documents(query)
    cursor = db[XHS_COOKIES_COLLECTION].find(query).sort("created_at", -1).skip(skip).limit(limit)
    items = [doc async for doc in cursor]
    return items, total


async def update_cookie(
    db: AsyncIOMotorDatabase,
    account_name: str,
    patch: dict[str, Any],
) -> dict[str, Any] | None:
    """更新 Cookie"""
    patch["updated_at"] = _now()
    return await db[XHS_COOKIES_COLLECTION].find_one_and_update(
        {"account_name": account_name},
        {"$set": patch},
        return_document=ReturnDocument.AFTER,
    )


async def set_cookie_valid(
    db: AsyncIOMotorDatabase,
    account_name: str,
    is_valid: bool,
) -> dict[str, Any] | None:
    """设置 Cookie 有效性"""
    return await update_cookie(db, account_name, {
        "is_valid": is_valid,
        "last_verified_at": _now(),
        "last_error": None if is_valid else "Cookie 验证失败",
        "consecutive_failures": 0 if is_valid else 1,
        "last_success_at": _now() if is_valid else None,
        "last_failure_at": None if is_valid else _now(),
    })


async def activate_cookie(
    db: AsyncIOMotorDatabase,
    account_name: str,
) -> dict[str, Any] | None:
    """激活指定账号，取消其他账号激活状态"""
    # 先检查账号是否存在
    existing = await db[XHS_COOKIES_COLLECTION].find_one({"account_name": account_name})
    if not existing:
        return None
    
    # 取消其他账号的激活状态
    await db[XHS_COOKIES_COLLECTION].update_many(
        {"is_active": True, "account_name": {"$ne": account_name}},
        {"$set": {"is_active": False, "updated_at": _now()}},
    )
    # 激活指定账号
    return await update_cookie(db, account_name, {"is_active": True})


async def delete_cookie(
    db: AsyncIOMotorDatabase,
    account_name: str,
) -> bool:
    """删除 Cookie"""
    result = await db[XHS_COOKIES_COLLECTION].delete_one({"account_name": account_name})
    return bool(result.deleted_count)


# ==================== 搜索任务 ====================

async def create_search_task(
    db: AsyncIOMotorDatabase,
    project_id: str,
    keyword: str,
    max_notes: int = 20,
    attention_threshold: int = 60,
) -> dict[str, Any]:
    """创建搜索任务"""
    now = _now()
    doc = {
        "project_id": project_id,
        "keyword": keyword,
        "max_notes": max_notes,
        "attention_threshold": attention_threshold,
        "status": "pending",
        "notes_count": 0,
        "suspicious_count": 0,
        "profiles_count": 0,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }
    result = await db[XHS_SEARCH_TASKS_COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def get_search_task(
    db: AsyncIOMotorDatabase,
    task_id: str,
) -> dict[str, Any] | None:
    """获取搜索任务"""
    oid = _oid(task_id)
    if not oid:
        return None
    return await db[XHS_SEARCH_TASKS_COLLECTION].find_one({"_id": oid})


async def update_search_task(
    db: AsyncIOMotorDatabase,
    task_id: str,
    patch: dict[str, Any],
) -> dict[str, Any] | None:
    """更新搜索任务"""
    oid = _oid(task_id)
    if not oid:
        return None
    patch["updated_at"] = _now()
    return await db[XHS_SEARCH_TASKS_COLLECTION].find_one_and_update(
        {"_id": oid},
        {"$set": patch},
        return_document=ReturnDocument.AFTER,
    )


async def list_search_tasks(
    db: AsyncIOMotorDatabase,
    project_id: str | None = None,
    limit: int = 50,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """列出搜索任务，返回 (items, total)"""
    query: dict[str, Any] = {}
    if project_id:
        query["project_id"] = project_id
    total = await db[XHS_SEARCH_TASKS_COLLECTION].count_documents(query)
    cursor = db[XHS_SEARCH_TASKS_COLLECTION].find(query).sort("created_at", -1).skip(skip).limit(limit)
    items = [doc async for doc in cursor]
    return items, total


# ==================== 笔记 ====================

async def create_note(
    db: AsyncIOMotorDatabase,
    note_data: dict[str, Any],
) -> dict[str, Any]:
    """创建笔记记录"""
    now = _now()
    note_data["created_at"] = now
    note_data["tagging"] = None
    result = await db[XHS_NOTES_COLLECTION].insert_one(note_data)
    note_data["_id"] = result.inserted_id
    return note_data


async def create_notes_batch(
    db: AsyncIOMotorDatabase,
    notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """批量创建笔记记录"""
    if not notes:
        return []
    now = _now()
    for note in notes:
        note["created_at"] = now
        note["tagging"] = None
    result = await db[XHS_NOTES_COLLECTION].insert_many(notes)
    for i, oid in enumerate(result.inserted_ids):
        notes[i]["_id"] = oid
    return notes


async def get_note(
    db: AsyncIOMotorDatabase,
    note_id: str,
) -> dict[str, Any] | None:
    """根据 note_id 获取笔记"""
    return await db[XHS_NOTES_COLLECTION].find_one({"note_id": note_id})


async def get_note_by_id(
    db: AsyncIOMotorDatabase,
    id_str: str,
) -> dict[str, Any] | None:
    """根据 MongoDB _id 获取笔记"""
    oid = _oid(id_str)
    if not oid:
        return None
    return await db[XHS_NOTES_COLLECTION].find_one({"_id": oid})


async def update_note_tagging(
    db: AsyncIOMotorDatabase,
    note_id: str,
    tagging: dict[str, Any],
) -> dict[str, Any] | None:
    """更新笔记打标结果"""
    return await db[XHS_NOTES_COLLECTION].find_one_and_update(
        {"note_id": note_id},
        {"$set": {"tagging": tagging}},
        return_document=ReturnDocument.AFTER,
    )


async def list_notes(
    db: AsyncIOMotorDatabase,
    project_id: str | None = None,
    task_id: str | None = None,
    is_suspicious: bool | None = None,
    limit: int = 50,
    skip: int = 0,
    sort_by: str = "relevance",  # relevance=关联度, created_at=创建时间
) -> tuple[list[dict[str, Any]], int]:
    """列出笔记（默认按关联度从高到低排序），返回 (items, total)"""
    query: dict[str, Any] = {}
    if project_id:
        query["project_id"] = project_id
    if task_id:
        query["task_id"] = task_id
    if is_suspicious is not None:
        query["tagging.is_suspicious"] = is_suspicious
    
    # 排序：默认按关联度从高到低
    if sort_by == "relevance":
        sort_field = [("tagging.keyword_relevance", -1), ("tagging.attention_score", -1), ("created_at", -1)]
    else:
        sort_field = [("created_at", -1)]
    
    total = await db[XHS_NOTES_COLLECTION].count_documents(query)
    cursor = db[XHS_NOTES_COLLECTION].find(query).sort(sort_field).skip(skip).limit(limit)
    items = [doc async for doc in cursor]
    return items, total


async def list_notes_by_task(
    db: AsyncIOMotorDatabase,
    task_id: str,
) -> list[dict[str, Any]]:
    """获取任务下所有笔记"""
    cursor = db[XHS_NOTES_COLLECTION].find({"task_id": task_id})
    return [doc async for doc in cursor]


async def get_note_by_user_id(
    db: AsyncIOMotorDatabase,
    task_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """获取用户的第一条笔记（用于获取 xsec_token）"""
    return await db[XHS_NOTES_COLLECTION].find_one({
        "task_id": task_id,
        "user.user_id": user_id,
    })


async def get_suspicious_notes_by_task(
    db: AsyncIOMotorDatabase,
    task_id: str,
    attention_threshold: int = 60,
) -> list[dict[str, Any]]:
    """获取任务下可疑笔记"""
    cursor = db[XHS_NOTES_COLLECTION].find({
        "task_id": task_id,
        "tagging.is_suspicious": True,
        "tagging.attention_score": {"$gte": attention_threshold},
    })
    return [doc async for doc in cursor]


# ==================== 笔记详情 ====================

async def create_note_detail(
    db: AsyncIOMotorDatabase,
    note_id: str,
    project_id: str,
    content: str | None = None,
    comments_summary: str | None = None,
    comments_data: list[dict[str, Any]] | None = None,
    images_urls: list[str] | None = None,
    xsec_token: str | None = None,
    xsec_source: str | None = None,
) -> dict[str, Any]:
    """创建笔记详情"""
    now = _now()
    doc = {
        "note_id": note_id,
        "project_id": project_id,
        "xsec_token": xsec_token,
        "xsec_source": xsec_source,
        "content": content,
        "comments_summary": comments_summary,
        "comments_data": comments_data or [],
        "images_urls": images_urls or [],
        "tagging": None,
        "created_at": now,
    }
    result = await db[XHS_NOTE_DETAILS_COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def get_note_detail(
    db: AsyncIOMotorDatabase,
    note_id: str,
) -> dict[str, Any] | None:
    """获取笔记详情（如果没有 xsec_token，从 notes 表关联查询）"""
    detail = await db[XHS_NOTE_DETAILS_COLLECTION].find_one({"note_id": note_id})
    if not detail:
        return None
    
    # 如果没有 xsec_token，从 notes 表获取
    if not detail.get("xsec_token"):
        note = await db[XHS_NOTES_COLLECTION].find_one({"note_id": note_id})
        if note:
            detail["xsec_token"] = note.get("xsec_token")
            detail["xsec_source"] = note.get("xsec_source")
    
    return detail


async def update_note_detail_tagging(
    db: AsyncIOMotorDatabase,
    note_id: str,
    tagging: dict[str, Any],
) -> dict[str, Any] | None:
    """更新笔记详情打标"""
    return await db[XHS_NOTE_DETAILS_COLLECTION].find_one_and_update(
        {"note_id": note_id},
        {"$set": {"tagging": tagging}},
        return_document=ReturnDocument.AFTER,
    )


async def list_note_details(
    db: AsyncIOMotorDatabase,
    project_id: str,
    limit: int = 50,
    skip: int = 0,
) -> list[dict[str, Any]]:
    """列出笔记详情"""
    cursor = db[XHS_NOTE_DETAILS_COLLECTION].find({"project_id": project_id}).sort("created_at", -1).skip(skip).limit(limit)
    return [doc async for doc in cursor]


# ==================== 人物画像 ====================

async def create_or_update_profile(
    db: AsyncIOMotorDatabase,
    project_id: str,
    task_id: str,
    user_id: str,
    nickname: str,
    avatar: str | None = None,
    note_ids: list[str] | None = None,
) -> dict[str, Any]:
    """创建或更新人物画像（旧接口，保留兼容）"""
    now = _now()
    doc = await db[XHS_PROFILES_COLLECTION].find_one_and_update(
        {"project_id": project_id, "user_id": user_id},
        {
            "$setOnInsert": {
                "project_id": project_id,
                "user_id": user_id,
                "created_at": now,
            },
            "$set": {
                "task_id": task_id,
                "nickname": nickname,
                "avatar": avatar,
                "updated_at": now,
            },
            "$addToSet": {"note_ids": {"$each": note_ids or []}},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    # 更新笔记数量
    notes_count = len(doc.get("note_ids", []))
    if doc.get("notes_count") != notes_count:
        doc = await db[XHS_PROFILES_COLLECTION].find_one_and_update(
            {"_id": doc["_id"]},
            {"$set": {"notes_count": notes_count}},
            return_document=ReturnDocument.AFTER,
        )
    return doc


async def save_profile_from_vision(
    db: AsyncIOMotorDatabase,
    project_id: str,
    user_id: str,
    avatar_url: str | None,
    analysis_result: dict[str, Any],
) -> dict[str, Any]:
    """
    从视觉分析结果保存人物画像（新接口）
    
    analysis_result 是 Agent 格式化输出的 JSON，直接存储到数据库
    """
    now = _now()
    
    # 从分析结果提取 nickname
    nickname = analysis_result.get("nickname", "") or ""
    
    # 构建更新数据 - 直接存储 Agent 输出的所有字段
    update_data = {
        "nickname": nickname,
        "avatar_url": avatar_url,
        "updated_at": now,
        # Agent 分析结果字段
        "basic_info": analysis_result.get("basic_info"),
        "stats": analysis_result.get("stats"),
        "identity": analysis_result.get("identity"),
        "bio_analysis": analysis_result.get("bio_analysis"),
        "device_info": analysis_result.get("device_info"),
        "avatar_analysis": analysis_result.get("avatar_analysis"),
        "gender_analysis": analysis_result.get("gender_analysis"),
        "personality_profile": analysis_result.get("personality_profile"),
        "notes_analysis": analysis_result.get("notes_analysis"),
        "company_identification": analysis_result.get("company_identification"),
        "keyword_relevance": analysis_result.get("keyword_relevance"),
        "attack_surface": analysis_result.get("attack_surface"),
        "social_graph": analysis_result.get("social_graph"),
        "timeline": analysis_result.get("timeline"),
        "profile_summary": analysis_result.get("profile_summary"),
        "attention_score": analysis_result.get("attention_score", 0),
        "recommended_actions": analysis_result.get("recommended_actions", []),
        "tags": analysis_result.get("tags", []),
    }
    
    doc = await db[XHS_PROFILES_COLLECTION].find_one_and_update(
        {"project_id": project_id, "user_id": user_id},
        {
            "$setOnInsert": {
                "project_id": project_id,
                "user_id": user_id,
                "task_id": "",
                "note_ids": [],
                "notes_count": 0,
                "created_at": now,
            },
            "$set": update_data,
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc


async def get_profile(
    db: AsyncIOMotorDatabase,
    project_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """获取人物画像"""
    return await db[XHS_PROFILES_COLLECTION].find_one({
        "project_id": project_id,
        "user_id": user_id,
    })


async def get_profile_by_id(
    db: AsyncIOMotorDatabase,
    id_str: str,
) -> dict[str, Any] | None:
    """根据 MongoDB _id 获取人物画像"""
    oid = _oid(id_str)
    if not oid:
        return None
    return await db[XHS_PROFILES_COLLECTION].find_one({"_id": oid})


async def delete_profile(
    db: AsyncIOMotorDatabase,
    id_str: str,
) -> bool:
    """删除人物画像"""
    oid = _oid(id_str)
    if not oid:
        return False
    result = await db[XHS_PROFILES_COLLECTION].delete_one({"_id": oid})
    return bool(result.deleted_count)


async def update_profile_tagging(
    db: AsyncIOMotorDatabase,
    project_id: str,
    user_id: str,
    tagging: dict[str, Any],
) -> dict[str, Any] | None:
    """更新人物画像打标 — 展开到顶层字段"""
    update_fields: dict[str, Any] = {"tagging": tagging, "updated_at": _now()}
    # 把 tagging 中的关键字段展开到顶层（前端直接读顶层）
    for key in [
        "basic_info", "stats", "identity", "bio_analysis", "device_info",
        "avatar_analysis", "gender_analysis", "personality_profile",
        "notes_analysis", "company_identification", "keyword_relevance",
        "attack_surface", "social_graph", "timeline",
        "profile_summary", "attention_score", "recommended_actions", "tags",
    ]:
        if key in tagging:
            update_fields[key] = tagging[key]

    return await db[XHS_PROFILES_COLLECTION].find_one_and_update(
        {"project_id": project_id, "user_id": user_id},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )


async def list_profiles(
    db: AsyncIOMotorDatabase,
    project_id: str,
    task_id: str | None = None,
    limit: int = 50,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """列出人物画像，返回 (items, total)"""
    query: dict[str, Any] = {"project_id": project_id}
    if task_id:
        query["task_id"] = task_id
    total = await db[XHS_PROFILES_COLLECTION].count_documents(query)
    cursor = db[XHS_PROFILES_COLLECTION].find(query).sort("updated_at", -1).skip(skip).limit(limit)
    items = [doc async for doc in cursor]
    return items, total


async def aggregate_users_from_notes(
    db: AsyncIOMotorDatabase,
    task_id: str,
) -> list[dict[str, Any]]:
    """从笔记中聚合用户信息"""
    pipeline = [
        {"$match": {"task_id": task_id, "tagging.is_suspicious": True}},
        {"$group": {
            "_id": "$user.user_id",
            "nickname": {"$first": "$user.nickname"},
            "avatar": {"$first": "$user.avatar"},
            "note_ids": {"$push": "$note_id"},
            "notes_count": {"$sum": 1},
            "max_attention_score": {"$max": "$tagging.attention_score"},
        }},
        {"$sort": {"max_attention_score": -1}},
    ]
    cursor = db[XHS_NOTES_COLLECTION].aggregate(pipeline)
    return [doc async for doc in cursor]
