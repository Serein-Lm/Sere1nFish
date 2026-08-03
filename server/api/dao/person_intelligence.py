"""真实人物公开情报 DAO。"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from api.dao.targets import normalize_target_name
from api.db.collections import PERSON_INTELLIGENCE_COLLECTION


_SUMMARY_PROJECTION = {
    "_id": 0,
    "intel_id": 1,
    "name": 1,
    "organization": 1,
    "position": 1,
    "department": 1,
    "location": 1,
    "summary": 1,
    "confidence": 1,
    "target_id": 1,
    "project_ids": 1,
    "source_count": 1,
    "evidence_count": 1,
    "signal_count": 1,
    "scenario_count": 1,
    "copywriting_count": 1,
    "artifact_count": 1,
    "profile_version": 1,
    "research_rounds": 1,
    "last_researched_at": 1,
    "updated_at": 1,
}
_SCALAR_FIELDS = (
    "name",
    "organization",
    "position",
    "department",
    "location",
    "summary",
    "background",
    "confidence",
    "target_id",
)
_LIST_FIELDS = (
    "aliases",
    "research_areas",
    "project_ids",
    "task_ids",
    "artifact_ids",
)
_OBJECT_LIST_KEYS: dict[str, tuple[str, ...]] = {
    "sources": ("url",),
    "public_contacts": ("channel", "value"),
    "affiliations": ("organization", "name", "role", "position"),
    "career_history": ("organization", "position", "period", "start", "end"),
    "evidence": ("evidence_id", "dimension", "finding", "evidence_type"),
    "context_signals": ("signal_id", "signal_type", "title", "observed_at"),
    "recommended_personas": ("person_id",),
    "scenarios": ("scenario_id", "title", "objective"),
    "sample_copywritings": ("copywriting_id", "title", "channel", "content"),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def intelligence_id(name: str, organization: str) -> str:
    """以姓名和机构生成稳定身份，避免同名人物跨机构碰撞。"""
    name_key = normalize_target_name(name)
    organization_key = normalize_target_name(organization)
    if not name_key or not organization_key:
        raise ValueError("人物姓名和机构不能为空")
    raw = f"person-intelligence:{name_key}:{organization_key}".encode("utf-8")
    return "poi_" + hashlib.sha1(raw).hexdigest()[:20]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[PERSON_INTELLIGENCE_COLLECTION]
    await coll.create_index("intel_id", unique=True)
    await coll.create_index([("normalized_name", 1), ("normalized_organization", 1)])
    await coll.create_index([("organization", 1), ("updated_at", -1)])
    await coll.create_index([("target_id", 1), ("updated_at", -1)])
    await coll.create_index([("project_ids", 1), ("updated_at", -1)])
    await coll.create_index("sources.url")
    await coll.create_index([("confidence", -1), ("updated_at", -1)])


def _clean_strings(values: Iterable[Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(value).strip()
            for value in values
            if value is not None and str(value).strip()
        )
    )


def _object_key(item: dict[str, Any], fields: tuple[str, ...]) -> str:
    identity_field = fields[0] if fields else ""
    identity_value = str(item.get(identity_field) or "").strip().casefold()
    if identity_field.endswith("_id") and identity_value:
        return identity_value
    values = [str(item.get(field) or "").strip().casefold() for field in fields]
    if not any(values):
        return hashlib.sha1(
            json.dumps(item, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
    return "\x1f".join(values)


def _merge_object_lists(
    old_values: Iterable[Any],
    new_values: Iterable[Any],
    key_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for raw in [*list(old_values or []), *list(new_values or [])]:
        if not isinstance(raw, dict):
            continue
        item = {str(key): value for key, value in raw.items() if value not in (None, "", [])}
        if not item:
            continue
        key = _object_key(item, key_fields)
        previous = merged.get(key, {})
        combined = dict(previous)
        for field, value in item.items():
            if isinstance(value, list):
                combined[field] = _clean_strings([*(previous.get(field) or []), *value])
            elif value not in (None, ""):
                combined[field] = value
        merged[key] = combined
    return list(merged.values())


def _merge_mapping(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    result = dict(old or {})
    for key, value in (new or {}).items():
        if isinstance(value, list):
            old_list = result.get(str(key))
            if not isinstance(old_list, list):
                old_list = []
            result[str(key)] = _clean_strings([*old_list, *value])
        elif isinstance(value, dict):
            result[str(key)] = _merge_mapping(
                result.get(str(key)) if isinstance(result.get(str(key)), dict) else {},
                value,
            )
        elif value not in (None, ""):
            result[str(key)] = value
    return result


def _stable_id(prefix: str, *values: Any) -> str:
    raw = "\x1f".join(str(value or "").strip().casefold() for value in values)
    return f"{prefix}_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _assign_structured_ids(document: dict[str, Any]) -> None:
    for item in document.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        item["evidence_id"] = item.get("evidence_id") or _stable_id(
            "ev", item.get("dimension"), item.get("finding"), item.get("evidence_type")
        )
    for item in document.get("context_signals") or []:
        if not isinstance(item, dict):
            continue
        item["signal_id"] = item.get("signal_id") or _stable_id(
            "sig", item.get("signal_type"), item.get("title"), item.get("observed_at")
        )
    for item in document.get("scenarios") or []:
        if not isinstance(item, dict):
            continue
        item["scenario_id"] = item.get("scenario_id") or _stable_id(
            "scn", item.get("title"), item.get("objective")
        )
    for item in document.get("sample_copywritings") or []:
        if not isinstance(item, dict):
            continue
        item["copywriting_id"] = item.get("copywriting_id") or _stable_id(
            "cwp", item.get("title"), item.get("channel"), item.get("content")
        )


def build_lineage(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """从结构化事实和方案自动生成可查询的稳定溯源关系图。"""
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}

    def add_node(node_id: str, node_type: str, label: str, **meta: Any) -> None:
        nodes[node_id] = {
            "node_id": node_id,
            "node_type": node_type,
            "label": label or node_id,
            **{key: value for key, value in meta.items() if value not in (None, "", [])},
        }

    def add_edge(source: str, target: str, relation: str) -> None:
        if source not in nodes or target not in nodes:
            return
        edge_id = _stable_id("edge", source, relation, target)
        edges[edge_id] = {
            "edge_id": edge_id,
            "source": source,
            "target": target,
            "relation": relation,
        }

    person_node = f"person:{document.get('intel_id') or ''}"
    add_node(person_node, "person", str(document.get("name") or "人物"))
    organization_node = "organization:" + (
        str(document.get("target_id") or "")
        or _stable_id("org", document.get("organization"))
    )
    add_node(
        organization_node,
        "organization",
        str(document.get("organization") or "机构"),
        target_id=document.get("target_id"),
    )
    add_edge(person_node, organization_node, "affiliated_with")

    source_nodes: dict[str, str] = {}
    for source in document.get("sources") or []:
        url = str(source.get("url") or "")
        if not url:
            continue
        node_id = "source:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
        source_nodes[url] = node_id
        add_node(node_id, "source", str(source.get("title") or url), url=url)

    for evidence in document.get("evidence") or []:
        node_id = "evidence:" + str(evidence.get("evidence_id") or "")
        add_node(
            node_id,
            "evidence",
            str(evidence.get("dimension") or "证据"),
            evidence_type=evidence.get("evidence_type"),
            confidence=evidence.get("confidence"),
        )
        add_edge(node_id, person_node, "describes")
        for url in evidence.get("source_urls") or []:
            add_edge(source_nodes.get(str(url), ""), node_id, "supports")

    for signal in document.get("context_signals") or []:
        node_id = "signal:" + str(signal.get("signal_id") or "")
        add_node(
            node_id,
            "signal",
            str(signal.get("title") or "时间信号"),
            observed_at=signal.get("observed_at"),
            expires_at=signal.get("expires_at"),
        )
        add_edge(node_id, person_node, "contextualizes")
        for url in signal.get("source_urls") or []:
            add_edge(source_nodes.get(str(url), ""), node_id, "supports")

    for persona in document.get("recommended_personas") or []:
        node_id = "persona:" + str(persona.get("person_id") or "")
        add_node(node_id, "persona", str(persona.get("name") or persona.get("person_id") or "人设"))
        add_edge(person_node, node_id, "matched_to")

    scenario_nodes: dict[str, str] = {}
    for scenario in document.get("scenarios") or []:
        scenario_id = str(scenario.get("scenario_id") or "")
        node_id = "scenario:" + scenario_id
        scenario_nodes[scenario_id] = node_id
        add_node(node_id, "scenario", str(scenario.get("title") or "沟通场景"), priority=scenario.get("priority"))
        add_edge(node_id, person_node, "targets")
        for persona_id in scenario.get("persona_ids") or []:
            add_edge("persona:" + str(persona_id), node_id, "informs")
        for url in scenario.get("source_urls") or []:
            add_edge(source_nodes.get(str(url), ""), node_id, "informs")

    for copywriting in document.get("sample_copywritings") or []:
        node_id = "copywriting:" + str(copywriting.get("copywriting_id") or "")
        add_node(node_id, "copywriting", str(copywriting.get("title") or "话术"), channel=copywriting.get("channel"))
        scenario_ids = list(copywriting.get("scenario_ids") or [])
        if scenario_ids:
            for scenario_id in scenario_ids:
                add_edge(scenario_nodes.get(str(scenario_id), ""), node_id, "produces")
        else:
            add_edge(person_node, node_id, "produces")
        for url in copywriting.get("source_urls") or []:
            add_edge(source_nodes.get(str(url), ""), node_id, "grounds")

    for artifact_id in document.get("artifact_ids") or []:
        node_id = "artifact:" + str(artifact_id)
        add_node(node_id, "artifact", str(artifact_id), artifact_id=artifact_id)
        add_edge(person_node, node_id, "documented_by")

    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def merge_intelligence(
    existing: dict[str, Any] | None,
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """按字段语义合并新一轮研究，不覆盖历史证据集合。"""
    previous = deepcopy(existing or {})
    incoming = deepcopy(payload or {})
    _assign_structured_ids(previous)
    _assign_structured_ids(incoming)
    merged = {key: value for key, value in previous.items() if key != "_id"}
    moment = now or _now()

    for field in _SCALAR_FIELDS:
        value = incoming.get(field)
        if value not in (None, ""):
            merged[field] = value
    for field in _LIST_FIELDS:
        merged[field] = _clean_strings(
            [*(previous.get(field) or []), *(incoming.get(field) or [])]
        )
    for field, keys in _OBJECT_LIST_KEYS.items():
        merged[field] = _merge_object_lists(
            previous.get(field) or [], incoming.get(field) or [], keys
        )

    merged["profile"] = _merge_mapping(
        previous.get("profile") if isinstance(previous.get("profile"), dict) else {},
        incoming.get("profile") if isinstance(incoming.get("profile"), dict) else {},
    )
    merged["engagement_plan"] = _merge_mapping(
        previous.get("engagement_plan")
        if isinstance(previous.get("engagement_plan"), dict)
        else {},
        incoming.get("engagement_plan")
        if isinstance(incoming.get("engagement_plan"), dict)
        else {},
    )
    merged["intel_id"] = str(incoming.get("intel_id") or previous.get("intel_id") or "")
    merged["normalized_name"] = normalize_target_name(str(merged.get("name") or ""))
    merged["normalized_organization"] = normalize_target_name(
        str(merged.get("organization") or "")
    )
    merged["source_count"] = len(merged.get("sources") or [])
    merged["evidence_count"] = len(merged.get("evidence") or [])
    merged["signal_count"] = len(merged.get("context_signals") or [])
    merged["scenario_count"] = len(merged.get("scenarios") or [])
    merged["copywriting_count"] = len(merged.get("sample_copywritings") or [])
    merged["artifact_count"] = len(merged.get("artifact_ids") or [])
    merged["profile_version"] = int(previous.get("profile_version") or 0) + 1
    merged["research_rounds"] = int(previous.get("research_rounds") or 0) + 1
    merged["last_researched_at"] = moment
    merged["updated_at"] = moment
    merged.setdefault("created_at", moment)
    _assign_structured_ids(merged)
    merged["lineage"] = build_lineage(merged)
    return merged


async def upsert_intelligence(
    db: AsyncIOMotorDatabase,
    payload: dict[str, Any],
    *,
    max_retries: int = 4,
) -> dict[str, Any]:
    """使用 profile_version 乐观锁归并，避免并行研究覆盖证据。"""
    intel_id = str(payload.get("intel_id") or "")
    if not intel_id:
        intel_id = intelligence_id(
            str(payload.get("name") or ""), str(payload.get("organization") or "")
        )
    normalized = {**payload, "intel_id": intel_id}
    coll = db[PERSON_INTELLIGENCE_COLLECTION]

    for _ in range(max(1, max_retries)):
        existing = await coll.find_one({"intel_id": intel_id}, {"_id": 0})
        merged = merge_intelligence(existing, normalized)
        query: dict[str, Any] = {"intel_id": intel_id}
        if existing:
            query["profile_version"] = int(existing.get("profile_version") or 0)
        try:
            result = await coll.find_one_and_update(
                query,
                {"$set": merged},
                upsert=not bool(existing),
                return_document=ReturnDocument.AFTER,
                projection={"_id": 0},
            )
        except DuplicateKeyError:
            continue
        if result:
            return result
    raise RuntimeError("人物情报发生并发更新冲突，请重试")


async def get_intelligence(
    db: AsyncIOMotorDatabase, intel_id: str
) -> dict[str, Any] | None:
    if not intel_id:
        return None
    return await db[PERSON_INTELLIGENCE_COLLECTION].find_one(
        {"intel_id": intel_id}, {"_id": 0}
    )


async def search_intelligence(
    db: AsyncIOMotorDatabase,
    *,
    keyword: str = "",
    organization: str = "",
    target_id: str = "",
    project_id: str = "",
    min_confidence: float = 0.0,
    sort: str = "updated_desc",
    skip: int = 0,
    limit: int = 20,
    summary_only: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    query: dict[str, Any] = {}
    if organization:
        query["organization"] = {"$regex": re.escape(organization), "$options": "i"}
    if target_id:
        query["target_id"] = target_id
    if project_id:
        query["project_ids"] = project_id
    if min_confidence > 0:
        query["confidence"] = {"$gte": min(1.0, max(0.0, min_confidence))}
    if keyword:
        rx = {"$regex": re.escape(keyword), "$options": "i"}
        query["$or"] = [
            {"name": rx},
            {"aliases": rx},
            {"organization": rx},
            {"position": rx},
            {"department": rx},
            {"summary": rx},
            {"research_areas": rx},
        ]
    sort_spec = {
        "confidence_desc": [("confidence", -1), ("updated_at", -1)],
        "name_asc": [("name", 1), ("organization", 1)],
    }.get(sort, [("updated_at", -1)])
    safe_skip = max(0, int(skip))
    safe_limit = max(1, min(int(limit), 200))
    coll = db[PERSON_INTELLIGENCE_COLLECTION]
    total = await coll.count_documents(query)
    cursor = (
        coll.find(query, _SUMMARY_PROJECTION if summary_only else {"_id": 0})
        .sort(sort_spec)
        .skip(safe_skip)
        .limit(safe_limit)
    )
    return await cursor.to_list(safe_limit), total


async def attach_artifact(
    db: AsyncIOMotorDatabase,
    *,
    intel_id: str,
    artifact_id: str,
) -> dict[str, Any] | None:
    """把生成产物接入人物溯源图，不增加研究轮次。"""
    coll = db[PERSON_INTELLIGENCE_COLLECTION]
    now = _now()
    updated = await coll.find_one_and_update(
        {"intel_id": intel_id},
        {
            "$addToSet": {"artifact_ids": artifact_id},
            "$inc": {"profile_version": 1},
            "$set": {"updated_at": now},
        },
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if not updated:
        return None
    updated["artifact_count"] = len(updated.get("artifact_ids") or [])
    lineage = build_lineage(updated)
    await coll.update_one(
        {"intel_id": intel_id, "profile_version": updated["profile_version"]},
        {"$set": {"lineage": lineage, "artifact_count": updated["artifact_count"]}},
    )
    updated["lineage"] = lineage
    return updated
