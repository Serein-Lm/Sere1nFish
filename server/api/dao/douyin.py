"""
抖音社工信息采集 - DAO 层实现
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from api.db.collections import (
    DOUYIN_COOKIES_COLLECTION,
    DOUYIN_SEARCH_RESULTS_COLLECTION,
    DOUYIN_TAGGED_RESULTS_COLLECTION,
    DOUYIN_PROFILES_COLLECTION,
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
    doc = await db[DOUYIN_COOKIES_COLLECTION].find_one_and_update(
        {"account_name": account_name},
        {
            "$setOnInsert": {
                "account_name": account_name,
                "created_at": now,
            },
            "$set": {
                "cookie_string": cookie_string,
                "is_active": False,
                "is_valid": None,
                "last_verified_at": None,
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
    return await db[DOUYIN_COOKIES_COLLECTION].find_one({"account_name": account_name})


async def get_active_cookie(db: AsyncIOMotorDatabase) -> dict[str, Any] | None:
    """获取当前激活的 Cookie"""
    return await db[DOUYIN_COOKIES_COLLECTION].find_one({"is_active": True})


async def list_cookies(
    db: AsyncIOMotorDatabase,
    limit: int = 50,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """列出所有 Cookie，返回 (items, total)"""
    query: dict[str, Any] = {}
    total = await db[DOUYIN_COOKIES_COLLECTION].count_documents(query)
    cursor = db[DOUYIN_COOKIES_COLLECTION].find(query).sort("created_at", -1).skip(skip).limit(limit)
    items = [doc async for doc in cursor]
    return items, total


async def update_cookie(
    db: AsyncIOMotorDatabase,
    account_name: str,
    patch: dict[str, Any],
) -> dict[str, Any] | None:
    """更新 Cookie"""
    patch["updated_at"] = _now()
    return await db[DOUYIN_COOKIES_COLLECTION].find_one_and_update(
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
    })


async def activate_cookie(
    db: AsyncIOMotorDatabase,
    account_name: str,
) -> dict[str, Any] | None:
    """激活指定账号，取消其他账号激活状态"""
    existing = await db[DOUYIN_COOKIES_COLLECTION].find_one({"account_name": account_name})
    if not existing:
        return None
    
    # 取消其他账号的激活状态
    await db[DOUYIN_COOKIES_COLLECTION].update_many(
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
    result = await db[DOUYIN_COOKIES_COLLECTION].delete_one({"account_name": account_name})
    return bool(result.deleted_count)


# ==================== 搜索结果 ====================

async def create_search_result(
    db: AsyncIOMotorDatabase,
    project_id: str,
    keyword: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    """
    创建搜索结果记录（单条）
    
    根据 aweme_id 去重，已存在则更新
    """
    now = _now()
    aweme_id = item.get("aweme_id")
    
    doc = await db[DOUYIN_SEARCH_RESULTS_COLLECTION].find_one_and_update(
        {"project_id": project_id, "aweme_id": aweme_id},
        {
            "$setOnInsert": {
                "project_id": project_id,
                "aweme_id": aweme_id,
                "created_at": now,
            },
            "$set": {
                "keyword": keyword,
                "aweme_type": item.get("aweme_type"),
                "title": item.get("title"),
                "create_time": item.get("create_time"),
                "create_time_str": item.get("create_time_str"),
                "ip_location": item.get("ip_location"),
                "liked_count": item.get("liked_count"),
                "collected_count": item.get("collected_count"),
                "comment_count": item.get("comment_count"),
                "share_count": item.get("share_count"),
                "user_id": item.get("user_id"),
                "sec_uid": item.get("sec_uid"),
                "nickname": item.get("nickname"),
                "avatar": item.get("avatar"),
                "cover_url": item.get("cover_url"),
                "video_download_url": item.get("video_download_url"),
                "aweme_url": item.get("aweme_url"),
                "user_profile_url": item.get("user_profile_url"),
                "source_keyword": item.get("source_keyword", keyword),
                "updated_at": now,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc


async def create_search_results_batch(
    db: AsyncIOMotorDatabase,
    project_id: str,
    keyword: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    批量创建搜索结果（去重）
    
    Returns:
        {"inserted": int, "updated": int, "total": int}
    """
    if not items:
        return {"inserted": 0, "updated": 0, "total": 0}
    
    inserted = 0
    updated = 0
    
    for item in items:
        aweme_id = item.get("aweme_id")
        existing = await db[DOUYIN_SEARCH_RESULTS_COLLECTION].find_one({
            "project_id": project_id,
            "aweme_id": aweme_id,
        })
        
        await create_search_result(db, project_id, keyword, item)
        
        if existing:
            updated += 1
        else:
            inserted += 1
    
    return {"inserted": inserted, "updated": updated, "total": len(items)}


async def get_search_result(
    db: AsyncIOMotorDatabase,
    project_id: str,
    aweme_id: str,
) -> dict[str, Any] | None:
    """获取单条搜索结果"""
    return await db[DOUYIN_SEARCH_RESULTS_COLLECTION].find_one({
        "project_id": project_id,
        "aweme_id": aweme_id,
    })


async def list_search_results(
    db: AsyncIOMotorDatabase,
    project_id: str,
    keyword: str | None = None,
    limit: int = 50,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """列出搜索结果，返回 (items, total)"""
    query: dict[str, Any] = {"project_id": project_id}
    if keyword:
        query["keyword"] = keyword
    
    total = await db[DOUYIN_SEARCH_RESULTS_COLLECTION].count_documents(query)
    cursor = db[DOUYIN_SEARCH_RESULTS_COLLECTION].find(query).sort("created_at", -1).skip(skip).limit(limit)
    items = [doc async for doc in cursor]
    return items, total


async def count_search_results(
    db: AsyncIOMotorDatabase,
    project_id: str,
    keyword: str | None = None,
) -> int:
    """统计搜索结果数量"""
    query: dict[str, Any] = {"project_id": project_id}
    if keyword:
        query["keyword"] = keyword
    return await db[DOUYIN_SEARCH_RESULTS_COLLECTION].count_documents(query)


# ==================== 打标结果 ====================

async def create_tagged_result(
    db: AsyncIOMotorDatabase,
    project_id: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    """
    创建打标结果（单条）
    
    根据 aweme_id 去重
    """
    now = _now()
    aweme_id = item.get("aweme_id")
    
    doc = await db[DOUYIN_TAGGED_RESULTS_COLLECTION].find_one_and_update(
        {"project_id": project_id, "aweme_id": aweme_id},
        {
            "$setOnInsert": {
                "project_id": project_id,
                "aweme_id": aweme_id,
                "created_at": now,
            },
            "$set": {
                # 基础信息
                "keyword": item.get("keyword"),
                "title": item.get("title"),
                "nickname": item.get("nickname"),
                "sec_uid": item.get("sec_uid"),
                "user_id": item.get("user_id"),
                "avatar": item.get("avatar"),
                "cover_url": item.get("cover_url"),
                "ip_location": item.get("ip_location"),
                "user_profile_url": item.get("user_profile_url"),
                "aweme_url": item.get("aweme_url"),
                "liked_count": item.get("liked_count"),
                "collected_count": item.get("collected_count"),
                "comment_count": item.get("comment_count"),
                "share_count": item.get("share_count"),
                "create_time_str": item.get("create_time_str"),
                # 打标信息
                "tag": item.get("tag"),  # potential_employee / marketing / uncertain
                "tag_reason": item.get("tag_reason"),
                "confidence": item.get("confidence"),  # high / medium / low
                "key_evidence": item.get("key_evidence", []),
                "company_mentioned": item.get("company_mentioned"),
                "position_mentioned": item.get("position_mentioned"),
                "priority": item.get("priority", 5),
                "updated_at": now,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc


async def create_tagged_results_batch(
    db: AsyncIOMotorDatabase,
    project_id: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    批量创建打标结果（去重）
    
    Returns:
        {"inserted": int, "updated": int, "total": int}
    """
    if not items:
        return {"inserted": 0, "updated": 0, "total": 0}
    
    inserted = 0
    updated = 0
    
    for item in items:
        aweme_id = item.get("aweme_id")
        existing = await db[DOUYIN_TAGGED_RESULTS_COLLECTION].find_one({
            "project_id": project_id,
            "aweme_id": aweme_id,
        })
        
        await create_tagged_result(db, project_id, item)
        
        if existing:
            updated += 1
        else:
            inserted += 1
    
    return {"inserted": inserted, "updated": updated, "total": len(items)}


async def list_tagged_results(
    db: AsyncIOMotorDatabase,
    project_id: str,
    tag: str | None = None,  # potential_employee / marketing / uncertain
    limit: int = 50,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """列出打标结果，返回 (items, total)"""
    query: dict[str, Any] = {"project_id": project_id}
    if tag:
        query["tag"] = tag
    
    total = await db[DOUYIN_TAGGED_RESULTS_COLLECTION].count_documents(query)
    cursor = db[DOUYIN_TAGGED_RESULTS_COLLECTION].find(query).sort([
        ("priority", -1),
        ("created_at", -1),
    ]).skip(skip).limit(limit)
    items = [doc async for doc in cursor]
    return items, total


async def count_tagged_results(
    db: AsyncIOMotorDatabase,
    project_id: str,
    tag: str | None = None,
) -> dict[str, int]:
    """统计打标结果"""
    pipeline = [
        {"$match": {"project_id": project_id}},
        {"$group": {"_id": "$tag", "count": {"$sum": 1}}},
    ]
    
    cursor = db[DOUYIN_TAGGED_RESULTS_COLLECTION].aggregate(pipeline)
    results = {doc["_id"]: doc["count"] async for doc in cursor}
    
    return {
        "total": sum(results.values()),
        "potential_employee": results.get("potential_employee", 0),
        "marketing": results.get("marketing", 0),
        "uncertain": results.get("uncertain", 0),
    }


async def get_potential_users(
    db: AsyncIOMotorDatabase,
    project_id: str,
) -> list[dict[str, Any]]:
    """获取潜在员工用户列表（去重，包含完整爬取数据）"""
    pipeline = [
        {"$match": {"project_id": project_id, "tag": "potential_employee"}},
        {"$group": {
            "_id": "$sec_uid",
            "nickname": {"$first": "$nickname"},
            "user_id": {"$first": "$user_id"},
            "avatar": {"$first": "$avatar"},  # 头像 URL
            "user_profile_url": {"$first": "$user_profile_url"},
            "ip_location": {"$first": "$ip_location"},
            "sample_title": {"$first": "$title"},  # 示例作品标题
            "tag_reason": {"$first": "$tag_reason"},
            "confidence": {"$first": "$confidence"},
            "key_evidence": {"$first": "$key_evidence"},
            "company_mentioned": {"$first": "$company_mentioned"},
            "position_mentioned": {"$first": "$position_mentioned"},
            "priority": {"$max": "$priority"},
            "aweme_count": {"$sum": 1},
        }},
        {"$sort": {"priority": -1}},
    ]
    
    cursor = db[DOUYIN_TAGGED_RESULTS_COLLECTION].aggregate(pipeline)
    return [doc async for doc in cursor]


# ==================== 用户画像 ====================

async def create_or_update_profile(
    db: AsyncIOMotorDatabase,
    project_id: str,
    sec_uid: str,
    profile_data: dict[str, Any],
) -> dict[str, Any]:
    """
    创建或更新用户画像
    
    根据 sec_uid 去重
    """
    now = _now()
    
    doc = await db[DOUYIN_PROFILES_COLLECTION].find_one_and_update(
        {"project_id": project_id, "sec_uid": sec_uid},
        {
            "$setOnInsert": {
                "project_id": project_id,
                "sec_uid": sec_uid,
                "created_at": now,
            },
            "$set": {
                # 基础信息
                "nickname": profile_data.get("nickname"),
                "user_id": profile_data.get("user_id"),
                "avatar_url": profile_data.get("avatar") or profile_data.get("avatar_url"),
                "user_profile_url": profile_data.get("user_profile_url"),
                "ip_location": profile_data.get("ip_location"),
                # 打标信息
                "sample_title": profile_data.get("sample_title"),
                "tag_reason": profile_data.get("tag_reason"),
                "confidence": profile_data.get("confidence"),
                "key_evidence": profile_data.get("key_evidence", []),
                "company_mentioned": profile_data.get("company_mentioned"),
                "position_mentioned": profile_data.get("position_mentioned"),
                "priority": profile_data.get("priority", 5),
                "aweme_count": profile_data.get("aweme_count", 0),
                # 视觉分析结果（如有）
                "vision_analysis": profile_data.get("vision_analysis"),
                "screenshot_paths": profile_data.get("screenshot_paths", []),
                "updated_at": now,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc


async def create_profiles_batch(
    db: AsyncIOMotorDatabase,
    project_id: str,
    profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    批量创建用户画像（去重）
    
    Returns:
        {"inserted": int, "updated": int, "total": int}
    """
    if not profiles:
        return {"inserted": 0, "updated": 0, "total": 0}
    
    inserted = 0
    updated = 0
    
    for profile in profiles:
        sec_uid = profile.get("sec_uid")
        existing = await db[DOUYIN_PROFILES_COLLECTION].find_one({
            "project_id": project_id,
            "sec_uid": sec_uid,
        })
        
        await create_or_update_profile(db, project_id, sec_uid, profile)
        
        if existing:
            updated += 1
        else:
            inserted += 1
    
    return {"inserted": inserted, "updated": updated, "total": len(profiles)}


async def get_profile(
    db: AsyncIOMotorDatabase,
    project_id: str,
    sec_uid: str,
) -> dict[str, Any] | None:
    """获取用户画像"""
    return await db[DOUYIN_PROFILES_COLLECTION].find_one({
        "project_id": project_id,
        "sec_uid": sec_uid,
    })


async def get_profile_by_id(
    db: AsyncIOMotorDatabase,
    id_str: str,
) -> dict[str, Any] | None:
    """根据 MongoDB _id 获取用户画像"""
    oid = _oid(id_str)
    if not oid:
        return None
    return await db[DOUYIN_PROFILES_COLLECTION].find_one({"_id": oid})


async def list_profiles(
    db: AsyncIOMotorDatabase,
    project_id: str,
    limit: int = 50,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """列出用户画像，返回 (items, total)"""
    query: dict[str, Any] = {"project_id": project_id}
    total = await db[DOUYIN_PROFILES_COLLECTION].count_documents(query)
    cursor = db[DOUYIN_PROFILES_COLLECTION].find(query).sort([
        ("priority", -1),
        ("updated_at", -1),
    ]).skip(skip).limit(limit)
    items = [doc async for doc in cursor]
    return items, total


async def count_profiles(
    db: AsyncIOMotorDatabase,
    project_id: str,
) -> int:
    """统计用户画像数量"""
    return await db[DOUYIN_PROFILES_COLLECTION].count_documents({"project_id": project_id})


async def update_profile_vision(
    db: AsyncIOMotorDatabase,
    project_id: str,
    sec_uid: str,
    vision_analysis: str,
    screenshot_paths: list[str] | None = None,
) -> dict[str, Any] | None:
    """更新用户画像的视觉分析结果"""
    update_data = {
        "vision_analysis": vision_analysis,
        "updated_at": _now(),
    }
    if screenshot_paths:
        update_data["screenshot_paths"] = screenshot_paths
    
    return await db[DOUYIN_PROFILES_COLLECTION].find_one_and_update(
        {"project_id": project_id, "sec_uid": sec_uid},
        {"$set": update_data},
        return_document=ReturnDocument.AFTER,
    )


async def delete_profile(
    db: AsyncIOMotorDatabase,
    id_str: str,
) -> bool:
    """删除用户画像"""
    oid = _oid(id_str)
    if not oid:
        return False
    result = await db[DOUYIN_PROFILES_COLLECTION].delete_one({"_id": oid})
    return bool(result.deleted_count)


async def save_profile_from_vision(
    db: AsyncIOMotorDatabase,
    project_id: str,
    sec_uid: str,
    user_profile_url: str | None,
    avatar_url: str | None,
    analysis_result: dict[str, Any],
    crawled_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    从视觉分析结果保存完整画像
    
    Args:
        db: 数据库连接
        project_id: 项目 ID
        sec_uid: 用户安全 ID
        user_profile_url: 用户主页链接
        avatar_url: 头像链接（视觉分析提取的）
        analysis_result: Agent 输出的完整 JSON
        crawled_data: 爬取的原始数据（包含 nickname, user_id, avatar, ip_location 等）
    
    Returns:
        保存后的画像文档
    """
    now = _now()
    crawled = crawled_data or {}
    
    # 从分析结果中提取字段，优先使用爬取数据中的基础信息
    update_data = {
        # 基础信息 - 优先使用爬取数据
        "user_profile_url": user_profile_url or crawled.get("user_profile_url"),
        "avatar_url": avatar_url or crawled.get("avatar"),  # 头像 URL
        "user_id": crawled.get("user_id"),
        "ip_location": crawled.get("ip_location"),
        
        # 昵称 - 优先使用视觉分析结果，其次爬取数据
        "nickname": analysis_result.get("nickname") or crawled.get("nickname"),
        
        # 打标信息（来自爬取阶段）
        "sample_title": crawled.get("sample_title"),
        "tag_reason": crawled.get("tag_reason"),
        "confidence": crawled.get("confidence"),
        "key_evidence": crawled.get("key_evidence", []),
        "company_mentioned": crawled.get("company_mentioned"),
        "position_mentioned": crawled.get("position_mentioned"),
        "priority": crawled.get("priority", 5),
        "aweme_count": crawled.get("aweme_count", 0),
        
        # 视觉分析结果
        "basic_info": analysis_result.get("basic_info"),
        "stats": analysis_result.get("stats"),
        "identity": analysis_result.get("identity"),
        "bio_analysis": analysis_result.get("bio_analysis"),
        
        # 公司识别
        "company_identification": analysis_result.get("company_identification"),
        "keyword_relevance": analysis_result.get("keyword_relevance"),
        
        # 攻击面
        "attack_surface": analysis_result.get("attack_surface"),
        
        # 画像摘要
        "profile_summary": analysis_result.get("profile_summary"),
        "attention_score": analysis_result.get("attention_score", 0),
        "recommended_actions": analysis_result.get("recommended_actions", []),
        "tags": analysis_result.get("tags", []),
        
        # 原始分析结果
        "raw_analysis": analysis_result,
        "updated_at": now,
    }
    
    doc = await db[DOUYIN_PROFILES_COLLECTION].find_one_and_update(
        {"project_id": project_id, "sec_uid": sec_uid},
        {
            "$setOnInsert": {
                "project_id": project_id,
                "sec_uid": sec_uid,
                "created_at": now,
            },
            "$set": update_data,
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc
