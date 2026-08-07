"""全局 Target 与项目目标关系 DAO。

Target 表示跨项目复用的真实实体（当前主要是公司/机构）；ProjectTarget 表示
某个项目为什么关注该实体，以及用哪些关键词、任务做增量采集。
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument, UpdateOne

from api.db.collections import PROJECT_TARGETS_COLLECTION, TARGETS_COLLECTION


_PROJECT_TARGET_RELATION_FIELDS = (
    "relation",
    "root_target_id",
    "root_target_name",
    "parent_target_id",
    "parent_target_name",
    "relation_type",
    "relation_depth",
    "ownership_percent",
    "relation_source",
    "lineage_target_ids",
    "lineage_target_names",
)

_PROJECT_TARGET_RELATION_PROJECTION = {
    "_id": 0,
    "project_target_id": 1,
    "project_id": 1,
    "target_id": 1,
    "target_name": 1,
    "root_target_id": 1,
    "root_target_name": 1,
    "parent_target_id": 1,
    "parent_target_name": 1,
    "relation_type": 1,
    "relation_depth": 1,
    "ownership_percent": 1,
    "relation_source": 1,
    "lineage_target_ids": 1,
    "lineage_target_names": 1,
    "batch_tags": 1,
}

MAX_TARGET_BATCH_TAGS = 12
MAX_TARGET_BATCH_TAG_LENGTH = 40


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_target_name(value: str) -> str:
    """生成用于实体匹配的稳定名称键，不改变展示名称。"""
    text = str(value or "").strip().casefold()
    return re.sub(r"[\s\-_·•,，。.;；:：()（）\[\]【】]+", "", text)


def normalize_batch_tags(values: list[str] | tuple[str, ...] | str | None) -> list[str]:
    """Validate and deduplicate user-facing ProjectTarget batch labels."""
    raw_values = [values] if isinstance(values, str) else list(values or [])
    tags: list[str] = []
    for value in raw_values:
        tag = re.sub(r"\s+", " ", str(value or "")).strip()
        if not tag:
            continue
        if len(tag) > MAX_TARGET_BATCH_TAG_LENGTH:
            raise ValueError(
                f"Target 批次标签不能超过 {MAX_TARGET_BATCH_TAG_LENGTH} 个字符"
            )
        if tag not in tags:
            tags.append(tag)
    if len(tags) > MAX_TARGET_BATCH_TAGS:
        raise ValueError(f"单个 Target 最多关联 {MAX_TARGET_BATCH_TAGS} 个批次标签")
    return tags


def target_id_for_name(name: str, target_type: str = "company") -> str:
    key = normalize_target_name(name)
    if not key:
        raise ValueError("Target 名称不能为空")
    raw = f"target:{target_type}:{key}".encode("utf-8")
    return "tgt_" + hashlib.sha1(raw).hexdigest()[:20]


def project_target_id(project_id: str, target_id: str) -> str:
    raw = f"project-target:{project_id}:{target_id}".encode("utf-8")
    return "pt_" + hashlib.sha1(raw).hexdigest()[:20]


def _ownership_percent(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0 or parsed > 100:
        return None
    return parsed


def build_project_target_relation_view(
    relation: dict[str, Any],
    relations_by_target: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a stable Target-to-root relationship snapshot for read models."""
    target_id = str(relation.get("target_id") or "")
    target_name = str(relation.get("target_name") or "")

    lineage_ids = [
        str(value or "").strip()
        for value in relation.get("lineage_target_ids") or []
        if str(value or "").strip()
    ]
    if not lineage_ids:
        lineage_ids = []
        current_id = target_id
        visited: set[str] = set()
        while current_id and current_id not in visited:
            visited.add(current_id)
            lineage_ids.append(current_id)
            current = relations_by_target.get(current_id, {})
            current_id = str(current.get("parent_target_id") or "")
        lineage_ids.reverse()
    elif target_id and target_id not in lineage_ids:
        lineage_ids.append(target_id)

    root_target_id = str(relation.get("root_target_id") or "")
    if not root_target_id:
        root_target_id = lineage_ids[0] if lineage_ids else target_id
    if root_target_id and root_target_id not in lineage_ids:
        lineage_ids.insert(0, root_target_id)

    stored_names = [
        str(value or "").strip()
        for value in relation.get("lineage_target_names") or []
    ]
    lineage_names: list[str] = []
    for index, lineage_id in enumerate(lineage_ids):
        lineage_relation = relations_by_target.get(lineage_id, {})
        fallback_name = stored_names[index] if index < len(stored_names) else ""
        lineage_names.append(
            str(lineage_relation.get("target_name") or fallback_name or lineage_id)
        )

    root_relation = relations_by_target.get(root_target_id, {})
    root_target_name = str(
        relation.get("root_target_name")
        or root_relation.get("target_name")
        or (lineage_names[0] if lineage_names else target_name)
    )
    is_primary = not target_id or target_id == root_target_id

    effective_percent: float | None = None
    if not is_primary and len(lineage_ids) > 1:
        effective = 1.0
        complete = True
        for child_id in lineage_ids[1:]:
            child_relation = relations_by_target.get(child_id, {})
            percent = _ownership_percent(child_relation.get("ownership_percent"))
            if percent is None:
                complete = False
                break
            effective *= percent / 100.0
        if complete:
            effective_percent = round(effective * 100.0, 4)

    direct_percent = _ownership_percent(relation.get("ownership_percent"))
    relation_type = str(relation.get("relation_type") or "").casefold()
    if is_primary:
        control_kind = "primary"
    elif (
        effective_percent is not None
        and math.isclose(
            effective_percent,
            100.0,
            rel_tol=0,
            abs_tol=0.0001,
        )
    ) or "wholly_owned" in relation_type:
        control_kind = "wholly_owned"
    elif effective_percent is not None and effective_percent > 50:
        control_kind = "controlled"
    elif any(marker in relation_type for marker in ("control", "subsidiary")):
        control_kind = "controlled"
    else:
        control_kind = "related"

    try:
        relation_depth = max(0, int(relation.get("relation_depth") or 0))
    except (TypeError, ValueError):
        relation_depth = max(0, len(lineage_ids) - 1)
    if not relation_depth and not is_primary:
        relation_depth = max(1, len(lineage_ids) - 1)

    return {
        "target_id": target_id,
        "target_name": target_name,
        "root_target_id": root_target_id,
        "root_target_name": root_target_name,
        "parent_target_id": str(relation.get("parent_target_id") or ""),
        "parent_target_name": str(relation.get("parent_target_name") or ""),
        "relation_type": str(relation.get("relation_type") or ""),
        "relation_depth": relation_depth,
        "ownership_percent": direct_percent,
        "effective_ownership_percent": effective_percent,
        "control_kind": control_kind,
        "is_primary": is_primary,
        "lineage_target_ids": lineage_ids,
        "lineage_target_names": lineage_names,
    }


async def get_project_target_relation_views(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Load requested Target relations and the ancestors needed for ownership."""
    selected_ids = list(
        dict.fromkeys(
            str(value or "").strip()
            for value in target_ids
            if str(value or "").strip()
        )
    )
    if not project_id or not selected_ids:
        return {}

    collection = db[PROJECT_TARGETS_COLLECTION]
    requested = await collection.find(
        {
            "project_id": project_id,
            "active": {"$ne": False},
            "target_id": {"$in": selected_ids},
        },
        _PROJECT_TARGET_RELATION_PROJECTION,
    ).to_list(len(selected_ids))
    relations_by_target = {
        str(item.get("target_id") or ""): item
        for item in requested
        if str(item.get("target_id") or "")
    }

    ancestor_ids = {
        str(value or "").strip()
        for relation in requested
        for value in [
            relation.get("root_target_id"),
            relation.get("parent_target_id"),
            *(relation.get("lineage_target_ids") or []),
        ]
        if str(value or "").strip()
    }
    missing_ids = sorted(ancestor_ids.difference(relations_by_target))
    if missing_ids:
        ancestors = await collection.find(
            {
                "project_id": project_id,
                "active": {"$ne": False},
                "target_id": {"$in": missing_ids},
            },
            _PROJECT_TARGET_RELATION_PROJECTION,
        ).to_list(len(missing_ids))
        relations_by_target.update(
            {
                str(item.get("target_id") or ""): item
                for item in ancestors
                if str(item.get("target_id") or "")
            }
        )

    return {
        target_id: build_project_target_relation_view(
            relation,
            relations_by_target,
        )
        for target_id in selected_ids
        if (relation := relations_by_target.get(target_id)) is not None
    }


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    targets = db[TARGETS_COLLECTION]
    await targets.create_index("target_id", unique=True)
    await targets.create_index([("target_type", 1), ("normalized_name", 1)])
    await targets.create_index("root_domain", sparse=True)
    await targets.create_index("root_domains", sparse=True)
    await targets.create_index("aliases_normalized")
    await targets.create_index("identity_aliases_normalized")

    links = db[PROJECT_TARGETS_COLLECTION]
    await links.create_index("project_target_id", unique=True)
    await links.create_index([("project_id", 1), ("updated_at", -1)])
    await links.create_index([("target_id", 1), ("updated_at", -1)])
    await links.create_index("task_def_ids")
    await links.create_index(
        [("project_id", 1), ("parent_target_id", 1), ("relation_depth", 1)]
    )
    await links.create_index(
        [("project_id", 1), ("root_target_id", 1), ("relation_depth", 1)]
    )
    await links.create_index([("project_id", 1), ("batch_tags", 1), ("active", 1)])


def trusted_identity_aliases(target: dict[str, Any]) -> list[str]:
    """Keep only canonical names and the deterministic name that seeded the Target."""
    target_id = str(target.get("target_id") or "")
    canonical_name = str(target.get("canonical_name") or "").strip()
    candidates = list(
        dict.fromkeys(
            value.strip()
            for value in [canonical_name, *(target.get("aliases") or [])]
            if isinstance(value, str) and value.strip()
        )
    )
    return [
        value
        for value in candidates
        if value == canonical_name
        or target_id_for_name(value, str(target.get("target_type") or "company"))
        == target_id
    ]


async def rebuild_identity_aliases(db: AsyncIOMotorDatabase) -> int:
    """Idempotently remove legacy search aliases from Target identity matching."""
    collection = db[TARGETS_COLLECTION]
    updates: list[UpdateOne] = []
    async for target in collection.find(
        {},
        {
            "_id": 0,
            "target_id": 1,
            "target_type": 1,
            "canonical_name": 1,
            "aliases": 1,
            "identity_aliases": 1,
            "identity_aliases_normalized": 1,
        },
    ):
        aliases = trusted_identity_aliases(target)
        normalized = list(
            dict.fromkeys(normalize_target_name(value) for value in aliases)
        )
        if (
            aliases == list(target.get("identity_aliases") or [])
            and normalized
            == list(target.get("identity_aliases_normalized") or [])
        ):
            continue
        updates.append(
            UpdateOne(
                {"target_id": str(target.get("target_id") or "")},
                {
                    "$set": {
                        "identity_aliases": aliases,
                        "identity_aliases_normalized": normalized,
                        "updated_at": _now(),
                    }
                },
            )
        )
    if not updates:
        return 0
    result = await collection.bulk_write(updates, ordered=False)
    return int(result.modified_count or 0)


async def get_target(
    db: AsyncIOMotorDatabase, target_id: str
) -> dict[str, Any] | None:
    if not target_id:
        return None
    return await db[TARGETS_COLLECTION].find_one(
        {"target_id": target_id}, {"_id": 0}
    )


async def find_target_exact_name(
    db: AsyncIOMotorDatabase,
    *,
    name: str,
    target_type: str = "company",
) -> dict[str, Any] | None:
    """Resolve one Target only by its canonical normalized name."""
    key = normalize_target_name(name)
    if not key:
        return None
    return await db[TARGETS_COLLECTION].find_one(
        {"target_type": target_type, "normalized_name": key},
        {"_id": 0},
    )


async def find_target(
    db: AsyncIOMotorDatabase,
    *,
    name: str = "",
    root_domain: str = "",
    target_type: str = "company",
) -> dict[str, Any] | None:
    key = normalize_target_name(name)
    if key:
        exact = await find_target_exact_name(
            db,
            name=name,
            target_type=target_type,
        )
        if exact:
            return exact
        alias = await db[TARGETS_COLLECTION].find_one(
            {
                "target_type": target_type,
                "identity_aliases_normalized": key,
            },
            {"_id": 0},
        )
        if alias:
            return alias
    if not root_domain:
        return None
    normalized_domain = root_domain.strip().lower()
    return await db[TARGETS_COLLECTION].find_one(
        {
            "target_type": target_type,
            "$or": [
                {"root_domain": normalized_domain},
                {"root_domains": normalized_domain},
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
    root_domains: list[str] | None = None,
    aliases: list[str] | None = None,
    source: str = "",
    normalization_version: int | None = None,
    match_aliases: bool = True,
    preferred_target_id: str = "",
    identity_aliases: list[str] | None = None,
    preserve_canonical_name: bool = False,
) -> dict[str, Any]:
    """Upsert one entity; domains are metadata, never an identity key."""
    display_name = str(name or "").strip()
    if not display_name:
        raise ValueError("Target 名称不能为空")
    root_domain = str(root_domain or "").strip().lower()
    alias_values = [
        value.strip()
        for value in [display_name, *(aliases or [])]
        if isinstance(value, str) and value.strip()
    ]
    alias_keys = list(
        dict.fromkeys(normalize_target_name(value) for value in alias_values)
    )
    identity_alias_values = list(
        dict.fromkeys(
            value.strip()
            for value in [display_name, *(identity_aliases or [])]
            if isinstance(value, str) and value.strip()
        )
    )
    identity_alias_keys = list(
        dict.fromkeys(
            normalize_target_name(value) for value in identity_alias_values
        )
    )
    existing = (
        await get_target(db, preferred_target_id)
        if str(preferred_target_id or "").strip()
        else None
    )
    if existing is None and match_aliases:
        existing = await find_target(
            db,
            name=display_name,
            target_type=target_type,
        )
    elif existing is None:
        # Legal entities in a control tree may share a brand alias or related
        # domain. Only an exact normalized legal name may reuse an identity.
        existing = await find_target_exact_name(
            db,
            name=display_name,
            target_type=target_type,
        )
    target_id = (
        str(existing.get("target_id"))
        if existing
        else target_id_for_name(display_name, target_type)
    )
    now = _now()
    canonical_name = display_name
    if existing and (source != "company_normalize" or preserve_canonical_name):
        canonical_name = str(existing.get("canonical_name") or display_name)
    set_fields: dict[str, Any] = {
        "target_id": target_id,
        "target_type": target_type,
        "canonical_name": canonical_name,
        "normalized_name": normalize_target_name(canonical_name),
        "status": "active",
        "last_seen_at": now,
        "updated_at": now,
    }
    if root_domain:
        set_fields["root_domain"] = root_domain
    if root_domains is not None or root_domain:
        set_fields["root_domains"] = list(
            dict.fromkeys(
                str(value).strip().lower()
                for value in [
                    *((existing or {}).get("root_domains") or []),
                    (existing or {}).get("root_domain") or "",
                    root_domain,
                    *(root_domains or []),
                ]
                if str(value).strip()
            )
        )[:12]
    if normalization_version is not None:
        set_fields["normalization_version"] = int(normalization_version)
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
            "identity_aliases": {"$each": identity_alias_values},
            "identity_aliases_normalized": {"$each": identity_alias_keys},
        }
    await db[TARGETS_COLLECTION].update_one(
        {"target_id": target_id}, update, upsert=True
    )
    return await get_target(db, target_id) or set_fields


async def enrich_target_from_research(
    db: AsyncIOMotorDatabase,
    *,
    target_id: str,
    summary: str,
    industry: str = "",
    organization_type: str = "",
    responsibilities: list[str] | None = None,
    services: list[str] | None = None,
    business_keywords: list[str] | None = None,
    key_people: list[dict[str, Any]] | None = None,
    research_id: str = "",
) -> dict[str, Any] | None:
    """把最新机构深研摘要挂到 Target，完整证据仍保存在研究版本表。"""
    now = _now()
    fields: dict[str, Any] = {
        "research_summary": str(summary or "").strip()[:12000],
        "industry": str(industry or "").strip()[:300],
        "organization_type": str(organization_type or "").strip()[:300],
        "responsibilities": [str(value).strip() for value in responsibilities or [] if str(value).strip()][:80],
        "services": [str(value).strip() for value in services or [] if str(value).strip()][:80],
        "business_keywords": [str(value).strip() for value in business_keywords or [] if str(value).strip()][:100],
        "key_people": list(key_people or [])[:50],
        "last_researched_at": now,
        "updated_at": now,
    }
    if research_id:
        fields["latest_research_id"] = research_id
    return await db[TARGETS_COLLECTION].find_one_and_update(
        {"target_id": target_id},
        {"$set": fields},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


async def merge_target_research_identity(
    db: AsyncIOMotorDatabase,
    *,
    target_id: str,
    aliases: list[str] | None = None,
    root_domains: list[str] | None = None,
) -> dict[str, Any] | None:
    """补齐研究得到的身份元数据，同时严格保留现有 Target 身份。"""
    current = await get_target(db, target_id)
    if not current:
        return None
    alias_values = list(
        dict.fromkeys(
            str(value).strip()
            for value in aliases or []
            if str(value).strip()
        )
    )[:50]
    alias_keys = list(
        dict.fromkeys(normalize_target_name(value) for value in alias_values)
    )
    domain_values = list(
        dict.fromkeys(
            str(value).strip().lower()
            for value in [
                current.get("root_domain") or "",
                *(current.get("root_domains") or []),
                *(root_domains or []),
            ]
            if str(value).strip()
        )
    )[:12]
    now = _now()
    fields: dict[str, Any] = {
        "root_domains": domain_values,
        "latest_source": "target_research",
        "last_seen_at": now,
        "updated_at": now,
    }
    if not current.get("root_domain") and domain_values:
        fields["root_domain"] = domain_values[0]
    update: dict[str, Any] = {"$set": fields}
    if alias_values:
        update["$addToSet"] = {
            "aliases": {"$each": alias_values},
            "aliases_normalized": {"$each": alias_keys},
        }
    return await db[TARGETS_COLLECTION].find_one_and_update(
        {"target_id": target_id},
        update,
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


async def link_project_target(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target: dict[str, Any],
    search_terms: list[str] | None = None,
    search_terms_by_channel: dict[str, list[str]] | None = None,
    objectives: list[str] | None = None,
    task_def_id: str = "",
    relation: dict[str, Any] | None = None,
    batch_tags: list[str] | tuple[str, ...] | str | None = None,
    replace_search_terms: bool = False,
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
            "root_domains": list(
                dict.fromkeys(
                    value
                    for value in [
                        target.get("root_domain") or "",
                        *list(target.get("root_domains") or []),
                    ]
                    if value
                )
            ),
            "active": True,
            "last_seen_at": now,
            "updated_at": now,
        },
        "$setOnInsert": {"created_at": now, "first_seen_at": now},
    }
    additions: dict[str, Any] = {}
    terms = [str(term).strip() for term in (search_terms or []) if str(term).strip()]
    goals = [str(goal).strip() for goal in (objectives or []) if str(goal).strip()]
    channel_map: dict[str, list[str]] = {}
    for channel, channel_terms in (search_terms_by_channel or {}).items():
        channel_key = re.sub(r"[^a-z0-9_]", "", str(channel).strip().lower())
        values = list(
            dict.fromkeys(
                str(term).strip()
                for term in (channel_terms or [])
                if str(term).strip()
            )
        )
        if channel_key and values:
            channel_map[channel_key] = values
    if replace_search_terms:
        update["$set"]["search_terms"] = list(dict.fromkeys(terms))
        update["$set"]["search_terms_by_channel"] = channel_map
    elif terms:
        additions["search_terms"] = {"$each": terms}
    if goals:
        additions["objectives"] = {"$each": goals}
    if task_def_id:
        additions["task_def_ids"] = task_def_id
    resolved_batch_tags = (
        normalize_batch_tags(batch_tags) if batch_tags is not None else []
    )
    if relation and batch_tags is None:
        anchor_ids = list(
            dict.fromkeys(
                str(value or "").strip()
                for value in (
                    relation.get("parent_target_id"),
                    relation.get("root_target_id"),
                )
                if str(value or "").strip()
            )
        )
        if anchor_ids:
            anchor = await db[PROJECT_TARGETS_COLLECTION].find_one(
                {
                    "project_id": project_id,
                    "target_id": {"$in": anchor_ids},
                    "active": {"$ne": False},
                },
                {"_id": 0, "batch_tags": 1},
                sort=[("relation_depth", -1)],
            )
            if anchor:
                resolved_batch_tags = normalize_batch_tags(
                    anchor.get("batch_tags") or []
                )
    if resolved_batch_tags:
        additions["batch_tags"] = {"$each": resolved_batch_tags}
    if not replace_search_terms:
        for channel_key, values in channel_map.items():
            additions[f"search_terms_by_channel.{channel_key}"] = {"$each": values}
    if relation:
        relation_doc = {
            str(key): value
            for key, value in relation.items()
            if value is not None and str(key).strip()
        }
        update["$set"]["relation"] = relation_doc
        for key in (
            "root_target_id",
            "root_target_name",
            "parent_target_id",
            "parent_target_name",
            "relation_type",
            "relation_depth",
            "ownership_percent",
            "relation_source",
            "lineage_target_ids",
            "lineage_target_names",
        ):
            if key in relation_doc:
                update["$set"][key] = relation_doc[key]
    else:
        # A directly selected project Target takes precedence over stale or
        # circular hierarchy metadata previously written to the same relation.
        update["$unset"] = {
            field: "" for field in _PROJECT_TARGET_RELATION_FIELDS
        }
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


async def get_project_target(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str,
) -> dict[str, Any] | None:
    if not project_id or not target_id:
        return None
    return await db[PROJECT_TARGETS_COLLECTION].find_one(
        {"project_id": project_id, "target_id": target_id},
        {"_id": 0},
    )


async def list_project_target_children(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    parent_target_id: str,
    relation_depth: int = 1,
) -> list[dict[str, Any]]:
    if not project_id or not parent_target_id:
        return []
    cursor = db[PROJECT_TARGETS_COLLECTION].find(
        {
            "project_id": project_id,
            "parent_target_id": parent_target_id,
            "relation_depth": relation_depth,
            "active": {"$ne": False},
        },
        {"_id": 0},
    ).sort("target_name", 1)
    return [doc async for doc in cursor]


async def list_project_target_descendants(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    root_target_id: str,
    max_depth: int = 2,
) -> list[dict[str, Any]]:
    """读取项目中根 Target 下的全资关联单位，兼容旧的第一层记录。"""
    if not project_id or not root_target_id:
        return []
    safe_depth = max(1, min(int(max_depth or 1), 2))
    cursor = db[PROJECT_TARGETS_COLLECTION].find(
        {
            "project_id": project_id,
            "active": {"$ne": False},
            "target_id": {"$ne": root_target_id},
            "relation_depth": {"$gte": 1, "$lte": safe_depth},
            "$or": [
                {"root_target_id": root_target_id},
                {
                    "root_target_id": {"$exists": False},
                    "parent_target_id": root_target_id,
                },
            ],
        },
        {"_id": 0},
    ).sort([("relation_depth", 1), ("target_name", 1)])
    return [doc async for doc in cursor]


async def list_project_targets(
    db: AsyncIOMotorDatabase,
    project_id: str,
    *,
    summary_only: bool = False,
) -> list[dict[str, Any]]:
    projection: dict[str, int] = {"_id": 0}
    if summary_only:
        projection.update(
            {
                "project_target_id": 1,
                "project_id": 1,
                "target_id": 1,
                "target_type": 1,
                "target_name": 1,
                "root_domain": 1,
                "root_domains": 1,
                "search_terms": 1,
                "search_terms_by_channel": 1,
                "root_target_id": 1,
                "root_target_name": 1,
                "parent_target_id": 1,
                "parent_target_name": 1,
                "relation_type": 1,
                "relation_depth": 1,
                "ownership_percent": 1,
                "relation_source": 1,
                "lineage_target_ids": 1,
                "lineage_target_names": 1,
                "batch_tags": 1,
                "run_task_ids": 1,
                "task_def_ids": 1,
                "last_collected_at": 1,
            }
        )
    cursor = db[PROJECT_TARGETS_COLLECTION].find(
        {"project_id": project_id, "active": {"$ne": False}}, projection
    ).sort("updated_at", -1)
    return [doc async for doc in cursor]


async def update_project_target_batch_tags(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_ids: list[str],
    batch_tags: list[str],
    operation: str,
) -> dict[str, int]:
    """Apply one normalized batch-label mutation to ProjectTarget relations."""
    normalized_target_ids = list(
        dict.fromkeys(
            str(value or "").strip()
            for value in target_ids
            if str(value or "").strip()
        )
    )
    normalized_tags = normalize_batch_tags(batch_tags)
    if not project_id or not normalized_target_ids:
        return {"matched_count": 0, "modified_count": 0}

    if operation == "add":
        if not normalized_tags:
            raise ValueError("新增批次标签时至少需要一个标签")
        update: dict[str, Any] = {
            "$addToSet": {"batch_tags": {"$each": normalized_tags}},
            "$set": {"updated_at": _now()},
        }
    elif operation == "remove":
        if not normalized_tags:
            raise ValueError("移除批次标签时至少需要一个标签")
        update = {
            "$pull": {"batch_tags": {"$in": normalized_tags}},
            "$set": {"updated_at": _now()},
        }
    elif operation == "replace":
        update = {
            "$set": {
                "batch_tags": normalized_tags,
                "updated_at": _now(),
            }
        }
    else:
        raise ValueError(f"不支持的批次标签操作: {operation}")

    result = await db[PROJECT_TARGETS_COLLECTION].update_many(
        {
            "project_id": project_id,
            "target_id": {"$in": normalized_target_ids},
            "active": {"$ne": False},
        },
        update,
    )
    return {
        "matched_count": int(result.matched_count or 0),
        "modified_count": int(result.modified_count or 0),
    }


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
