"""
统一 Findings DAO

所有数据源（web_tagging、xhs、douyin）的 findings 统一存储和查询。
每个 finding 都有 project_id、task_id、source、finding_id。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import FINDINGS_COLLECTION, COPYWRITINGS_COLLECTION, PROFILES_COLLECTION


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Findings CRUD ──

async def insert_finding(db: AsyncIOMotorDatabase, finding: dict[str, Any]) -> None:
    """插入单个 finding"""
    finding.setdefault("created_at", _now())
    await db[FINDINGS_COLLECTION].insert_one(finding)


async def insert_findings_batch(db: AsyncIOMotorDatabase, findings: list[dict[str, Any]]) -> int:
    """批量插入 findings"""
    if not findings:
        return 0
    now = _now()
    for f in findings:
        f.setdefault("created_at", now)
    result = await db[FINDINGS_COLLECTION].insert_many(findings)
    return len(result.inserted_ids)


async def query_findings(
    db: AsyncIOMotorDatabase,
    project_id: str,
    source: str = "",
    task_id: str = "",
    finding_type: str = "",
    min_score: int = 0,
    sort: str = "score_desc",
    limit: int = 20,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """
    分页查询 findings，返回 (findings, total)
    """
    query: dict[str, Any] = {"project_id": project_id}
    if source:
        query["source"] = source
    if task_id:
        query["task_id"] = task_id
    if finding_type:
        query["type"] = finding_type
    if min_score > 0:
        query["attention_score"] = {"$gte": min_score}

    sort_map = {
        "score_desc": [("attention_score", -1)],
        "score_asc": [("attention_score", 1)],
        "time_desc": [("created_at", -1)],
    }
    sort_spec = sort_map.get(sort, [("attention_score", -1)])

    total = await db[FINDINGS_COLLECTION].count_documents(query)
    cursor = db[FINDINGS_COLLECTION].find(query, {"_id": 0}).sort(sort_spec).skip(skip).limit(limit)
    findings = await cursor.to_list(limit)

    return findings, total


async def get_finding(db: AsyncIOMotorDatabase, finding_id: str) -> dict[str, Any] | None:
    """获取单个 finding"""
    return await db[FINDINGS_COLLECTION].find_one({"finding_id": finding_id}, {"_id": 0})


def mobile_profile_finding_id(project_id: str, contact_id: str) -> str:
    """Stable finding id for one mobile contact profile within a project."""
    raw = f"mobile:{project_id}:{contact_id}".encode("utf-8")
    return "mp_" + hashlib.sha1(raw).hexdigest()[:20]


def _mobile_profile_score(persona: dict[str, Any] | None) -> int:
    persona = persona or {}
    score = 35
    for key in ("summary", "background", "personality", "communication_style"):
        if persona.get(key):
            score += 8
    score += min(len(persona.get("interests") or []) * 3, 12)
    score += min(len(persona.get("tags") or []) * 4, 16)
    if persona.get("common_phrases"):
        score += 6
    if persona.get("risk_signals"):
        score += 10
    return max(0, min(score, 100))


async def upsert_mobile_profile_finding(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    contact_id: str,
    task_id: str | None = None,
    device_id: str | None = None,
    platform: str | None = None,
    name: str | None = None,
    persona: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    attention_score: int | None = None,
) -> dict[str, Any]:
    """Create/update the unified finding for a mobile contact profile.

    A project/contact pair maps to one deterministic finding id. The finding is
    the queryable project-level index; the current rich profile snapshot is
    stored in ``profiles`` and granular evidence lives in mobile observations.
    """
    finding_id = mobile_profile_finding_id(project_id, contact_id)
    existing = await get_finding(db, finding_id)
    persona = persona or {}
    display_name = name or persona.get("name") or contact_id
    score = attention_score if attention_score is not None else _mobile_profile_score(persona)
    summary = (
        persona.get("summary")
        or persona.get("communication_style")
        or persona.get("background")
        or ""
    )
    now = _now()
    set_fields = {
        "finding_id": finding_id,
        "project_id": project_id,
        "task_id": task_id or (existing or {}).get("task_id", ""),
        "latest_task_id": task_id,
        "source": "mobile",
        "type": "contact_profile",
        "channel": "mobile_chat_profile",
        "label": f"手机聊天画像: {display_name}",
        "value": display_name,
        "contact_id": contact_id,
        "device_id": device_id,
        "platform": platform,
        "has_profile": True,
        "attention_score": score,
        "attention_reason": summary,
        "context": f"手机聊天联系人 {display_name} 的画像沉淀",
        "evidence": evidence or {},
        "updated_at": now,
    }
    update: dict[str, Any] = {
        "$set": set_fields,
        "$setOnInsert": {"created_at": now},
    }
    if task_id:
        update["$addToSet"] = {"task_ids": task_id}
    await db[FINDINGS_COLLECTION].update_one(
        {"finding_id": finding_id},
        update,
        upsert=True,
    )
    return await get_finding(db, finding_id) or set_fields


async def upsert_contact_finding(
    db: AsyncIOMotorDatabase, finding: dict[str, Any]
) -> dict[str, Any]:
    """按 finding_id 幂等 upsert 一个联系方式类 finding。

    finding 需已含确定性 finding_id(同一项目同一联系方式映射同一条),
    避免多次采集重复插入。created_at 仅首次写入,task_ids 累积。
    """
    finding_id = finding["finding_id"]
    now = _now()
    task_id = finding.get("task_id")
    set_fields = {k: v for k, v in finding.items() if k not in ("created_at",)}
    set_fields["updated_at"] = now
    update: dict[str, Any] = {
        "$set": set_fields,
        "$setOnInsert": {"created_at": now},
    }
    if task_id:
        update["$addToSet"] = {"task_ids": task_id}
    await db[FINDINGS_COLLECTION].update_one(
        {"finding_id": finding_id}, update, upsert=True
    )
    return await get_finding(db, finding_id) or set_fields


async def get_findings_summary(db: AsyncIOMotorDatabase, project_id: str) -> dict[str, Any]:
    """项目 findings 总览统计"""
    pipeline = [
        {"$match": {"project_id": project_id}},
        {"$facet": {
            "total": [{"$count": "count"}],
            "by_source": [{"$group": {"_id": "$source", "count": {"$sum": 1}}}],
            "by_type": [{"$group": {"_id": "$type", "count": {"$sum": 1}}}],
            "score_high": [{"$match": {"attention_score": {"$gte": 70}}}, {"$count": "count"}],
            "score_medium": [{"$match": {"attention_score": {"$gte": 40, "$lt": 70}}}, {"$count": "count"}],
            "score_low": [{"$match": {"attention_score": {"$lt": 40}}}, {"$count": "count"}],
        }},
    ]
    result = await db[FINDINGS_COLLECTION].aggregate(pipeline).to_list(1)
    if not result:
        return {"total": 0, "by_source": {}, "by_type": {}, "score_distribution": {"high": 0, "medium": 0, "low": 0}}

    r = result[0]
    return {
        "total": r["total"][0]["count"] if r["total"] else 0,
        "by_source": {item["_id"]: item["count"] for item in r["by_source"] if item["_id"]},
        "by_type": {item["_id"]: item["count"] for item in r["by_type"] if item["_id"]},
        "score_distribution": {
            "high": r["score_high"][0]["count"] if r["score_high"] else 0,
            "medium": r["score_medium"][0]["count"] if r["score_medium"] else 0,
            "low": r["score_low"][0]["count"] if r["score_low"] else 0,
        },
    }


async def delete_findings_by_task(db: AsyncIOMotorDatabase, task_id: str) -> int:
    """删除任务关联的所有 findings"""
    r = await db[FINDINGS_COLLECTION].delete_many({"task_id": task_id})
    return r.deleted_count


async def delete_findings_by_project(db: AsyncIOMotorDatabase, project_id: str) -> int:
    """删除项目的所有 findings"""
    r = await db[FINDINGS_COLLECTION].delete_many({"project_id": project_id})
    return r.deleted_count


async def delete_findings_by_tasks(db: AsyncIOMotorDatabase, task_ids: list[str]) -> int:
    """批量删除多个任务关联的 findings（单次 $in，避免 N+1）"""
    if not task_ids:
        return 0
    r = await db[FINDINGS_COLLECTION].delete_many({"task_id": {"$in": task_ids}})
    return r.deleted_count


# ── Copywriting ──

async def insert_copywriting(db: AsyncIOMotorDatabase, doc: dict[str, Any]) -> None:
    doc.setdefault("created_at", _now())
    await db[COPYWRITINGS_COLLECTION].insert_one(doc)


async def get_copywriting(db: AsyncIOMotorDatabase, finding_id: str) -> dict[str, Any] | None:
    return await db[COPYWRITINGS_COLLECTION].find_one({"finding_id": finding_id}, {"_id": 0})


async def delete_copywritings_by_task(db: AsyncIOMotorDatabase, task_id: str) -> int:
    r = await db[COPYWRITINGS_COLLECTION].delete_many({"task_id": task_id})
    return r.deleted_count


async def delete_copywritings_by_tasks(db: AsyncIOMotorDatabase, task_ids: list[str]) -> int:
    """批量删除多个任务关联的话术（单次 $in，避免 N+1）"""
    if not task_ids:
        return 0
    r = await db[COPYWRITINGS_COLLECTION].delete_many({"task_id": {"$in": task_ids}})
    return r.deleted_count


# ── Profile ──

async def upsert_profile(db: AsyncIOMotorDatabase, finding_id: str, profile: dict[str, Any]) -> None:
    profile["finding_id"] = finding_id
    profile.setdefault("updated_at", _now())
    await db[PROFILES_COLLECTION].update_one(
        {"finding_id": finding_id},
        {"$set": profile, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )


async def get_profile(db: AsyncIOMotorDatabase, finding_id: str) -> dict[str, Any] | None:
    return await db[PROFILES_COLLECTION].find_one({"finding_id": finding_id}, {"_id": 0})
