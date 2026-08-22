"""项目级 Target 关系边持久化。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import UpdateOne

from api.db.collections import TARGET_RELATIONSHIPS_COLLECTION


UPSTREAM_DIRECTION = "upstream"
DOWNSTREAM_DIRECTION = "downstream"
LATERAL_DIRECTION = "lateral"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def relationship_id(
    project_id: str,
    subject_target_id: str,
    related_target_id: str,
    relation_type: str,
) -> str:
    values = (
        str(project_id or "").strip(),
        str(subject_target_id or "").strip(),
        str(related_target_id or "").strip(),
        str(relation_type or "").strip().casefold(),
    )
    if not all(values):
        raise ValueError("Target 关系缺少项目、主体、关联 Target 或关系类型")
    if values[1] == values[2]:
        raise ValueError("Target 不能与自身建立关系")
    raw = ":".join(("target-relationship", *values)).encode("utf-8")
    return "tr_" + hashlib.sha1(raw).hexdigest()[:20]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    collection = db[TARGET_RELATIONSHIPS_COLLECTION]
    await collection.create_index("relationship_id", unique=True)
    await collection.create_index(
        [("project_id", 1), ("subject_target_id", 1), ("active", 1)]
    )
    await collection.create_index(
        [("project_id", 1), ("related_target_id", 1), ("active", 1)]
    )
    await collection.create_index(
        [("project_id", 1), ("relation_type", 1), ("updated_at", -1)]
    )
    await collection.create_index("task_ids")
    await collection.create_index("research_ids")


def normalize_relationship(
    value: dict[str, Any],
    *,
    project_id: str,
    subject_target_id: str,
    subject_target_name: str,
    task_id: str,
    research_id: str,
) -> dict[str, Any]:
    related_target_id = str(value.get("related_target_id") or "").strip()
    relation_type = str(value.get("relation_type") or "").strip().casefold()
    direction = str(value.get("direction") or "").strip().casefold()
    if direction not in {
        UPSTREAM_DIRECTION,
        DOWNSTREAM_DIRECTION,
        LATERAL_DIRECTION,
    }:
        raise ValueError(f"不支持的 Target 关系方向: {direction}")
    rid = relationship_id(
        project_id,
        subject_target_id,
        related_target_id,
        relation_type,
    )
    try:
        confidence = float(value.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "relationship_id": rid,
        "project_id": str(project_id or "").strip(),
        "subject_target_id": str(subject_target_id or "").strip(),
        "subject_target_name": str(subject_target_name or "").strip()[:300],
        "related_target_id": related_target_id,
        "related_target_name": str(value.get("related_target_name") or "").strip()[:300],
        "relation_type": relation_type,
        "direction": direction,
        "summary": str(value.get("summary") or "").strip()[:3000],
        "confidence": max(0.0, min(confidence, 1.0)),
        "source_urls": list(
            dict.fromkeys(
                str(url or "").strip()
                for url in value.get("source_urls") or []
                if str(url or "").strip()
            )
        )[:20],
        "task_id": str(task_id or "").strip(),
        "research_id": str(research_id or "").strip(),
    }


async def sync_research_relationships(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    subject_target_id: str,
    subject_target_name: str,
    task_id: str,
    research_id: str,
    relationships: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace one Target's active research-derived edges idempotently."""
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in relationships:
        relation = normalize_relationship(
            item,
            project_id=project_id,
            subject_target_id=subject_target_id,
            subject_target_name=subject_target_name,
            task_id=task_id,
            research_id=research_id,
        )
        rid = relation["relationship_id"]
        if rid in seen:
            continue
        seen.add(rid)
        normalized.append(relation)

    now = _now()
    if normalized:
        operations = []
        for relation in normalized:
            set_fields = {
                key: value
                for key, value in relation.items()
                if key not in {"task_id", "research_id", "source_urls"}
            }
            set_fields.update(
                {
                    "source": "target_research",
                    "active": True,
                    "last_verified_at": now,
                    "updated_at": now,
                }
            )
            additions: dict[str, Any] = {
                "source_urls": {"$each": relation["source_urls"]}
            }
            if relation["task_id"]:
                additions["task_ids"] = relation["task_id"]
            if relation["research_id"]:
                additions["research_ids"] = relation["research_id"]
            operations.append(
                UpdateOne(
                    {"relationship_id": relation["relationship_id"]},
                    {
                        "$set": set_fields,
                        "$setOnInsert": {"created_at": now},
                        "$addToSet": additions,
                    },
                    upsert=True,
                )
            )
        await db[TARGET_RELATIONSHIPS_COLLECTION].bulk_write(
            operations,
            ordered=False,
        )

    stale_query: dict[str, Any] = {
        "project_id": project_id,
        "subject_target_id": subject_target_id,
        "active": {"$ne": False},
        "source": "target_research",
    }
    if seen:
        stale_query["relationship_id"] = {"$nin": sorted(seen)}
    await db[TARGET_RELATIONSHIPS_COLLECTION].update_many(
        stale_query,
        {
            "$set": {
                "active": False,
                "superseded_by_research_id": research_id,
                "updated_at": now,
            }
        },
    )
    return normalized


async def clone_project_relationships(
    db: AsyncIOMotorDatabase,
    *,
    source_project_id: str,
    destination_project_id: str,
    target_ids: list[str],
) -> int:
    """Clone relationship edges whose two endpoints belong to one partition."""
    normalized_ids = list(
        dict.fromkeys(
            str(value or "").strip()
            for value in target_ids
            if str(value or "").strip()
        )
    )
    if (
        not source_project_id
        or not destination_project_id
        or source_project_id == destination_project_id
        or not normalized_ids
    ):
        return 0

    relationships = await db[TARGET_RELATIONSHIPS_COLLECTION].find(
        {
            "project_id": source_project_id,
            "active": {"$ne": False},
            "subject_target_id": {"$in": normalized_ids},
            "related_target_id": {"$in": normalized_ids},
        },
        {"_id": 0},
    ).to_list(None)
    if not relationships:
        return 0

    now = _now()
    operations: list[UpdateOne] = []
    for source in relationships:
        subject_target_id = str(source.get("subject_target_id") or "").strip()
        related_target_id = str(source.get("related_target_id") or "").strip()
        relation_type = str(source.get("relation_type") or "").strip().casefold()
        rid = relationship_id(
            destination_project_id,
            subject_target_id,
            related_target_id,
            relation_type,
        )
        set_fields = {
            "relationship_id": rid,
            "project_id": destination_project_id,
            "subject_target_id": subject_target_id,
            "subject_target_name": str(source.get("subject_target_name") or "")[:300],
            "related_target_id": related_target_id,
            "related_target_name": str(source.get("related_target_name") or "")[:300],
            "relation_type": relation_type,
            "direction": str(source.get("direction") or "").strip().casefold(),
            "summary": str(source.get("summary") or "")[:3000],
            "confidence": float(source.get("confidence") or 0),
            "source": str(source.get("source") or "target_research"),
            "active": True,
            "last_verified_at": source.get("last_verified_at") or now,
            "updated_at": now,
        }
        additions: dict[str, Any] = {
            "merged_from_project_ids": source_project_id,
        }
        for field in ("source_urls", "task_ids", "research_ids"):
            values = list(
                dict.fromkeys(
                    str(value or "").strip()
                    for value in source.get(field) or []
                    if str(value or "").strip()
                )
            )
            if values:
                additions[field] = {"$each": values}
        operations.append(
            UpdateOne(
                {"relationship_id": rid},
                {
                    "$set": set_fields,
                    "$setOnInsert": {
                        "created_at": source.get("created_at") or now,
                    },
                    "$addToSet": additions,
                },
                upsert=True,
            )
        )

    await db[TARGET_RELATIONSHIPS_COLLECTION].bulk_write(
        operations,
        ordered=False,
    )
    return len(operations)


async def list_for_targets(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_ids: list[str],
) -> list[dict[str, Any]]:
    normalized_ids = list(
        dict.fromkeys(
            str(value or "").strip()
            for value in target_ids
            if str(value or "").strip()
        )
    )
    if not project_id or not normalized_ids:
        return []
    return await db[TARGET_RELATIONSHIPS_COLLECTION].find(
        {
            "project_id": project_id,
            "active": {"$ne": False},
            "$or": [
                {"subject_target_id": {"$in": normalized_ids}},
                {"related_target_id": {"$in": normalized_ids}},
            ],
        },
        {"_id": 0},
    ).sort([("confidence", -1), ("updated_at", -1)]).to_list(None)


def build_target_relationship_views(
    relationships: list[dict[str, Any]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Build compact directional projections for Target read models."""
    result: dict[str, dict[str, list[dict[str, Any]]]] = {}

    def add(target_id: str, key: str, value: dict[str, Any]) -> None:
        if not target_id:
            return
        bucket = result.setdefault(
            target_id,
            {
                "supervising_units": [],
                "supervised_units": [],
                "related_units": [],
            },
        )[key]
        if any(item.get("target_id") == value.get("target_id") for item in bucket):
            return
        bucket.append(value)

    for relation in relationships:
        direction = str(relation.get("direction") or "")
        subject_id = str(relation.get("subject_target_id") or "")
        related_id = str(relation.get("related_target_id") or "")
        common = {
            "relation_type": str(relation.get("relation_type") or ""),
            "summary": str(relation.get("summary") or ""),
            "confidence": float(relation.get("confidence") or 0),
            "source_urls": list(relation.get("source_urls") or []),
            "research_ids": list(relation.get("research_ids") or []),
        }
        if direction == LATERAL_DIRECTION:
            add(
                subject_id,
                "related_units",
                {
                    **common,
                    "target_id": related_id,
                    "target_name": str(relation.get("related_target_name") or ""),
                },
            )
            add(
                related_id,
                "related_units",
                {
                    **common,
                    "target_id": subject_id,
                    "target_name": str(relation.get("subject_target_name") or ""),
                },
            )
            continue
        if direction != UPSTREAM_DIRECTION:
            continue
        add(
            subject_id,
            "supervising_units",
            {
                **common,
                "target_id": related_id,
                "target_name": str(relation.get("related_target_name") or ""),
            },
        )
        add(
            related_id,
            "supervised_units",
            {
                **common,
                "target_id": subject_id,
                "target_name": str(relation.get("subject_target_name") or ""),
            },
        )
    return result
