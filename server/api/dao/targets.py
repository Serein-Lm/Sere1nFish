"""全局 Target 与项目目标关系 DAO。

Target 表示跨项目复用的真实实体（当前主要是公司/机构）；ProjectTarget 表示
某个项目为什么关注该实体，以及用哪些关键词、任务做增量采集。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from api.db.collections import PROJECT_TARGETS_COLLECTION, TARGETS_COLLECTION


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_target_name(value: str) -> str:
    """生成用于实体匹配的稳定名称键，不改变展示名称。"""
    text = str(value or "").strip().casefold()
    return re.sub(r"[\s\-_·•,，。.;；:：()（）\[\]【】]+", "", text)


def target_id_for_name(name: str, target_type: str = "company") -> str:
    key = normalize_target_name(name)
    if not key:
        raise ValueError("Target 名称不能为空")
    raw = f"target:{target_type}:{key}".encode("utf-8")
    return "tgt_" + hashlib.sha1(raw).hexdigest()[:20]


def project_target_id(project_id: str, target_id: str) -> str:
    raw = f"project-target:{project_id}:{target_id}".encode("utf-8")
    return "pt_" + hashlib.sha1(raw).hexdigest()[:20]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    targets = db[TARGETS_COLLECTION]
    await targets.create_index("target_id", unique=True)
    await targets.create_index([("target_type", 1), ("normalized_name", 1)])
    await targets.create_index("root_domain", sparse=True)
    await targets.create_index("aliases_normalized")

    links = db[PROJECT_TARGETS_COLLECTION]
    await links.create_index("project_target_id", unique=True)
    await links.create_index([("project_id", 1), ("updated_at", -1)])
    await links.create_index([("target_id", 1), ("updated_at", -1)])
    await links.create_index("task_def_ids")


async def get_target(
    db: AsyncIOMotorDatabase, target_id: str
) -> dict[str, Any] | None:
    if not target_id:
        return None
    return await db[TARGETS_COLLECTION].find_one(
        {"target_id": target_id}, {"_id": 0}
    )


async def find_target(
    db: AsyncIOMotorDatabase,
    *,
    name: str = "",
    root_domain: str = "",
    target_type: str = "company",
) -> dict[str, Any] | None:
    if root_domain:
        found = await db[TARGETS_COLLECTION].find_one(
            {"target_type": target_type, "root_domain": root_domain.strip().lower()},
            {"_id": 0},
        )
        if found:
            return found
    key = normalize_target_name(name)
    if not key:
        return None
    return await db[TARGETS_COLLECTION].find_one(
        {
            "target_type": target_type,
            "$or": [
                {"normalized_name": key},
                {"aliases_normalized": key},
            ],
        },
        {"_id": 0},
    )


async def upsert_target(
    db: AsyncIOMotorDatabase,
    *,
    name: str,
    target_type: str = "company",
    root_domain: str = "",
    aliases: list[str] | None = None,
    source: str = "",
) -> dict[str, Any]:
    """按根域名/规范名称复用 Target；不存在时创建稳定实体。"""
    display_name = str(name or "").strip()
    if not display_name:
        raise ValueError("Target 名称不能为空")
    root_domain = str(root_domain or "").strip().lower()
    existing = await find_target(
        db,
        name=display_name,
        root_domain=root_domain,
        target_type=target_type,
    )
    target_id = (
        str(existing.get("target_id"))
        if existing
        else target_id_for_name(display_name, target_type)
    )
    now = _now()
    alias_values = [
        value.strip()
        for value in [display_name, *(aliases or [])]
        if isinstance(value, str) and value.strip()
    ]
    alias_keys = [normalize_target_name(value) for value in alias_values]
    set_fields: dict[str, Any] = {
        "target_id": target_id,
        "target_type": target_type,
        "canonical_name": display_name,
        "normalized_name": normalize_target_name(display_name),
        "status": "active",
        "last_seen_at": now,
        "updated_at": now,
    }
    if root_domain:
        set_fields["root_domain"] = root_domain
    if source:
        set_fields["latest_source"] = source
    update: dict[str, Any] = {
        "$set": set_fields,
        "$setOnInsert": {"created_at": now, "first_seen_at": now},
    }
    if alias_values:
        update["$addToSet"] = {
            "aliases": {"$each": alias_values},
            "aliases_normalized": {"$each": alias_keys},
        }
    await db[TARGETS_COLLECTION].update_one(
        {"target_id": target_id}, update, upsert=True
    )
    return await get_target(db, target_id) or set_fields


async def link_project_target(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target: dict[str, Any],
    search_terms: list[str] | None = None,
    objectives: list[str] | None = None,
    task_def_id: str = "",
) -> dict[str, Any]:
    if not project_id:
        raise ValueError("project_id 不能为空")
    target_id = str(target.get("target_id") or "")
    if not target_id:
        raise ValueError("target_id 不能为空")
    relation_id = project_target_id(project_id, target_id)
    now = _now()
    update: dict[str, Any] = {
        "$set": {
            "project_target_id": relation_id,
            "project_id": project_id,
            "target_id": target_id,
            "target_type": target.get("target_type") or "company",
            "target_name": target.get("canonical_name") or "",
            "root_domain": target.get("root_domain") or "",
            "active": True,
            "last_seen_at": now,
            "updated_at": now,
        },
        "$setOnInsert": {"created_at": now, "first_seen_at": now},
    }
    additions: dict[str, Any] = {}
    terms = [str(term).strip() for term in (search_terms or []) if str(term).strip()]
    goals = [str(goal).strip() for goal in (objectives or []) if str(goal).strip()]
    if terms:
        additions["search_terms"] = {"$each": terms}
    if goals:
        additions["objectives"] = {"$each": goals}
    if task_def_id:
        additions["task_def_ids"] = task_def_id
    if additions:
        update["$addToSet"] = additions
    doc = await db[PROJECT_TARGETS_COLLECTION].find_one_and_update(
        {"project_target_id": relation_id},
        update,
        upsert=True,
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    return doc or {}


async def list_project_targets(
    db: AsyncIOMotorDatabase, project_id: str
) -> list[dict[str, Any]]:
    cursor = db[PROJECT_TARGETS_COLLECTION].find(
        {"project_id": project_id, "active": {"$ne": False}}, {"_id": 0}
    ).sort("updated_at", -1)
    return [doc async for doc in cursor]


async def list_target_projects(
    db: AsyncIOMotorDatabase, target_id: str
) -> list[dict[str, Any]]:
    cursor = db[PROJECT_TARGETS_COLLECTION].find(
        {"target_id": target_id, "active": {"$ne": False}}, {"_id": 0}
    ).sort("updated_at", -1)
    return [doc async for doc in cursor]


async def touch_project_target_collection(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str,
    run_task_id: str = "",
) -> None:
    relation_id = project_target_id(project_id, target_id)
    update: dict[str, Any] = {
        "$set": {"last_collected_at": _now(), "updated_at": _now()}
    }
    if run_task_id:
        update["$addToSet"] = {"run_task_ids": run_task_id}
    await db[PROJECT_TARGETS_COLLECTION].update_one(
        {"project_target_id": relation_id}, update
    )
