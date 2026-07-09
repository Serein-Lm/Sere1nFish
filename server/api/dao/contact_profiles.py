"""
系统3a — 人物画像 MongoDB DAO。

集合 contact_profiles 文档结构:
{
  contact_id: str,        # 唯一标识(如 "device_id:联系人名" 或手动指定)
  name: str,
  platform: str,          # wechat / ...
  device_id: str | None,
  persona: {              # 沉淀的画像(随聊天不断更新)
    background, personality, communication_style, summary,
    tone, reply_pattern, common_phrases: [...], risk_signals: [...],
    interests: [...], tags: [...]
  },
  project_links: [{ project_id, finding_id, first_seen_at, updated_at }],
  observations: [{ ts, content, source }],   # 历次聊天观察(保留最近 N 条)
  created_at, updated_at
}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import CONTACT_PROFILES_COLLECTION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[CONTACT_PROFILES_COLLECTION]
    try:
        await coll.create_index("contact_id", unique=True)
    except Exception:
        pass
    await coll.create_index("device_id")
    await coll.create_index("project_id")
    await coll.create_index("project_ids")
    await coll.create_index("project_links.project_id")
    await coll.create_index("project_links.finding_id")
    await coll.create_index("updated_at")


async def upsert_profile(
    db: AsyncIOMotorDatabase,
    contact_id: str,
    data: dict[str, Any],
    *,
    project_id: str | None = None,
) -> dict[str, Any] | None:
    payload = dict(data)
    payload["contact_id"] = contact_id
    payload["updated_at"] = _now()
    if project_id:
        payload["project_id"] = project_id
    update: dict[str, Any] = {
        "$set": payload,
        "$setOnInsert": {"created_at": _now()},
    }
    if project_id:
        update["$addToSet"] = {"project_ids": project_id}
    await db[CONTACT_PROFILES_COLLECTION].update_one(
        {"contact_id": contact_id},
        update,
        upsert=True,
    )
    return await get_profile(db, contact_id)


async def merge_persona(
    db: AsyncIOMotorDatabase,
    contact_id: str,
    persona_patch: dict[str, Any],
    *,
    name: str | None = None,
    platform: str | None = None,
    device_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any] | None:
    """把画像字段合并进 persona.* (不覆盖整个 persona)。"""
    set_fields: dict[str, Any] = {"updated_at": _now()}
    for key, value in persona_patch.items():
        set_fields[f"persona.{key}"] = value
    if name:
        set_fields["name"] = name
    if platform:
        set_fields["platform"] = platform
    if device_id:
        set_fields["device_id"] = device_id
    if project_id:
        set_fields["project_id"] = project_id
    update: dict[str, Any] = {
        "$set": set_fields,
        "$setOnInsert": {"created_at": _now(), "contact_id": contact_id},
    }
    if project_id:
        update["$addToSet"] = {"project_ids": project_id}
    await db[CONTACT_PROFILES_COLLECTION].update_one(
        {"contact_id": contact_id},
        update,
        upsert=True,
    )
    return await get_profile(db, contact_id)


async def append_observation(
    db: AsyncIOMotorDatabase,
    contact_id: str,
    observation: dict[str, Any],
    *,
    project_id: str | None = None,
    keep: int = 20,
) -> None:
    """追加一条聊天观察,只保留最近 keep 条。"""
    obs = dict(observation)
    if project_id:
        obs["project_id"] = project_id
    update: dict[str, Any] = {
        "$push": {"observations": {"$each": [obs], "$slice": -keep}},
        "$set": {"updated_at": _now()},
        "$setOnInsert": {"created_at": _now(), "contact_id": contact_id},
    }
    if project_id:
        update["$set"]["project_id"] = project_id
        update["$addToSet"] = {"project_ids": project_id}
    await db[CONTACT_PROFILES_COLLECTION].update_one(
        {"contact_id": contact_id},
        update,
        upsert=True,
    )


async def link_project_finding(
    db: AsyncIOMotorDatabase,
    contact_id: str,
    *,
    project_id: str,
    finding_id: str,
) -> None:
    """Link a shared contact profile to one project's stable mobile finding."""
    coll = db[CONTACT_PROFILES_COLLECTION]
    now = _now()
    existing = await coll.find_one(
        {"contact_id": contact_id, "project_links.project_id": project_id},
        {"project_links.$": 1, "_id": 0},
    )
    first_seen_at = now
    if existing and existing.get("project_links"):
        first_seen_at = existing["project_links"][0].get("first_seen_at") or now
    await coll.update_one(
        {"contact_id": contact_id},
        {
            "$pull": {"project_links": {"project_id": project_id}},
            "$set": {"updated_at": now},
            "$setOnInsert": {"created_at": now, "contact_id": contact_id},
        },
        upsert=True,
    )
    await coll.update_one(
        {"contact_id": contact_id},
        {
            "$addToSet": {
                "project_ids": project_id,
                "project_links": {
                    "project_id": project_id,
                    "finding_id": finding_id,
                    "first_seen_at": first_seen_at,
                    "updated_at": now,
                },
            },
            "$set": {
                "project_id": project_id,
                "latest_finding_id": finding_id,
                "updated_at": now,
            },
        },
        upsert=True,
    )


async def get_profile(
    db: AsyncIOMotorDatabase, contact_id: str
) -> dict[str, Any] | None:
    return await db[CONTACT_PROFILES_COLLECTION].find_one(
        {"contact_id": contact_id}, {"_id": 0}
    )


async def list_by_finding_ids(
    db: AsyncIOMotorDatabase,
    finding_ids: list[str],
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """按关联的 finding_id 反查接触画像（供上下文聚合层解析人物的手机聊天画像）。"""
    ids = [fid for fid in (finding_ids or []) if fid]
    if not ids:
        return []
    cursor = (
        db[CONTACT_PROFILES_COLLECTION]
        .find(
            {
                "$or": [
                    {"project_links.finding_id": {"$in": ids}},
                    {"latest_finding_id": {"$in": ids}},
                ]
            },
            {"_id": 0},
        )
        .sort("updated_at", -1)
        .limit(limit)
    )
    return [doc async for doc in cursor]


async def list_profiles(
    db: AsyncIOMotorDatabase,
    *,
    device_id: str | None = None,
    project_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if device_id:
        query["device_id"] = device_id
    if project_id:
        query["$or"] = [
            {"project_id": project_id},
            {"project_ids": project_id},
            {"project_links.project_id": project_id},
        ]
    cursor = (
        db[CONTACT_PROFILES_COLLECTION]
        .find(query, {"_id": 0})
        .sort("updated_at", -1)
        .limit(limit)
    )
    return [doc async for doc in cursor]


async def delete_profile(db: AsyncIOMotorDatabase, contact_id: str) -> bool:
    result = await db[CONTACT_PROFILES_COLLECTION].delete_one(
        {"contact_id": contact_id}
    )
    return result.deleted_count > 0


async def delete_project_references(
    db: AsyncIOMotorDatabase,
    project_id: str,
) -> dict[str, int]:
    """Remove one project's references without deleting shared contact profiles."""
    coll = db[CONTACT_PROFILES_COLLECTION]
    query = {
        "$or": [
            {"project_id": project_id},
            {"project_ids": project_id},
            {"project_links.project_id": project_id},
            {"observations.project_id": project_id},
        ]
    }
    docs = [doc async for doc in coll.find(query)]

    deleted = 0
    updated = 0
    observations_removed = 0
    for doc in docs:
        project_ids = [pid for pid in (doc.get("project_ids") or []) if pid != project_id]
        observations = doc.get("observations") or []
        kept_observations = [
            obs for obs in observations if obs.get("project_id") != project_id
        ]
        project_links = doc.get("project_links") or []
        kept_project_links = [
            link for link in project_links if link.get("project_id") != project_id
        ]
        observations_removed += len(observations) - len(kept_observations)

        only_deleted_project = (
            not project_ids
            and not kept_project_links
            and doc.get("project_id") in (None, project_id)
            and not kept_observations
        )
        if only_deleted_project:
            result = await coll.delete_one({"_id": doc["_id"]})
            deleted += result.deleted_count
            continue

        update: dict[str, Any] = {
            "$set": {
                "project_ids": project_ids,
                "project_links": kept_project_links,
                "observations": kept_observations,
                "updated_at": _now(),
            }
        }
        if doc.get("project_id") == project_id:
            if project_ids:
                update["$set"]["project_id"] = project_ids[0]
            else:
                update["$unset"] = {"project_id": ""}

        result = await coll.update_one({"_id": doc["_id"]}, update)
        updated += result.modified_count

    return {
        "profiles_deleted": deleted,
        "profiles_updated": updated,
        "observations_removed": observations_removed,
    }
