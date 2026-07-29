"""
人设库 DAO — 统一人物实体（persons 集合）。

设计：
- 人设库全局化：一个 (姓名, 公司) 映射到确定的 person_id，跨平台/跨项目增量归并；
- 人设库是独立能力，默认不绑定项目；project 仅作可选溯源写入 project_ids[]；
- 标量字段非空才覆盖，列表字段取并集，避免后采集覆盖前采集的有效信息；
- sources[] 保留每条信息来源溯源（source + 原始业务ID + finding_id）；
- 支持面向人设的检索：公司 / 行业 / 职位 / 标签 / 关键词 / 最低置信度。

文档结构:
{
  person_id, project_ids: [...], is_fictional, generation_brief, generation_key,
  name, gender, aliases: [...],
  company, company_root_domain, company_meta_id, industry,
  position, position_level, department, work_years,
  education: {school, degree, major, graduation_year},
  location, contact: {phone, email, wechat, other_social: [...]},
  background, personality, summary,
  interests: [...], tags: [...], risk_signals: [...],
  source_urls: [...], evidence: [...],
  sources: [{source, ref_id, finding_id, collected_at}],
  confidence, created_at, updated_at
}
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import PERSONS_COLLECTION

# 标量字段：非空才覆盖
_SCALAR_FIELDS = (
    "name", "gender", "company", "company_root_domain", "company_meta_id",
    "industry", "position", "position_level", "department", "work_years",
    "location", "background", "personality", "summary", "confidence", "age", "age_range",
    "region_type", "organization_context", "career_stage", "career_path", "life_stage",
    "work_context", "work_rhythm", "decision_style", "communication_style",
    "collaboration_style", "technology_attitude", "learning_style", "stress_response",
    "is_fictional", "generation_brief", "generation_key",
)
# 列表字段：取并集
_LIST_FIELDS = (
    "interests",
    "tags",
    "risk_signals",
    "aliases",
    "source_urls",
    "evidence",
    "information_preferences",
    "digital_habits",
    "motivations",
    "goals",
    "pain_points",
    "values",
    "behavior_patterns",
    "content_preferences",
    "purchase_considerations",
)
_OBJECT_LIST_FIELDS = ("research_evidence",)
# 嵌套对象字段：子字段非空才覆盖
_NESTED_FIELDS = ("education", "contact")
_SUMMARY_PROJECTION = {
    "_id": 0,
    "person_id": 1,
    "name": 1,
    "is_fictional": 1,
    "age": 1,
    "age_range": 1,
    "company": 1,
    "industry": 1,
    "position": 1,
    "location": 1,
    "summary": 1,
    "tags": 1,
    "confidence": 1,
    "profile_version": 1,
    "research_rounds": 1,
    "last_researched_at": 1,
    "updated_at": 1,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def person_id(name: str, company: str = "") -> str:
    """一个 (姓名, 公司) 对应确定的全局 person_id（人设库不绑定项目）。"""
    raw = f"person:{name.strip()}:{company.strip()}".encode("utf-8")
    return "ps_" + hashlib.sha1(raw).hexdigest()[:20]


def fictional_person_id(generation_key: str) -> str:
    """Map one normalized background brief to a stable synthetic person id."""
    raw = f"fictional-person:{generation_key.strip()}".encode("utf-8")
    return "ps_" + hashlib.sha1(raw).hexdigest()[:20]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """幂等建立索引，覆盖人设检索维度（全局，不以 project 为前缀）。"""
    coll = db[PERSONS_COLLECTION]
    await coll.create_index("person_id", unique=True)
    await coll.create_index("company")
    await coll.create_index("industry")
    await coll.create_index("position")
    await coll.create_index([("confidence", -1)])
    await coll.create_index("project_ids")
    await coll.create_index("company_root_domain")
    await coll.create_index("company_meta_id")
    await coll.create_index("tags")
    await coll.create_index("age")
    await coll.create_index("is_fictional")
    await coll.create_index("generation_key", unique=True, sparse=True)
    await coll.create_index("updated_at")


async def backfill_profile_versions(db: AsyncIOMotorDatabase) -> int:
    """Initialize version metadata for profiles created before incremental research."""
    coll = db[PERSONS_COLLECTION]
    versioned = await coll.update_many(
        {"profile_version": {"$exists": False}},
        {"$set": {"profile_version": 1}},
    )
    researched = await coll.update_many(
        {
            "is_fictional": True,
            "research_rounds": {"$exists": False},
        },
        [
            {
                "$set": {
                    "research_rounds": 1,
                    "last_researched_at": {
                        "$ifNull": ["$last_researched_at", {"$ifNull": ["$updated_at", "$created_at"]}]
                    },
                }
            }
        ],
    )
    return int(max(versioned.modified_count, researched.modified_count))


def _merge_set_fields(
    existing: dict[str, Any] | None, patch: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    """把新采集的档案字段拆成 ($set, $addToSet) 两部分。

    - 标量字段非空才覆盖，写入 $set；
    - 顶层列表字段返回给上层用 $addToSet/$each 原子并集（避免并发读改写丢更新）；
    - 嵌套对象字段子字段非空覆盖，写入 $set。
    """
    existing = existing or {}
    set_fields: dict[str, Any] = {"updated_at": _now()}
    list_add: dict[str, list[Any]] = {}

    for field in _SCALAR_FIELDS:
        value = patch.get(field)
        if field == "confidence":
            if value:
                set_fields[field] = value
        elif value not in (None, ""):
            set_fields[field] = value

    for field in _LIST_FIELDS:
        values = patch.get(field)
        if values:
            cleaned = [str(v) for v in values if v]
            if cleaned:
                list_add[field] = cleaned

    for field in _OBJECT_LIST_FIELDS:
        values = patch.get(field)
        if not isinstance(values, list):
            continue
        normalized: dict[str, dict[str, Any]] = {}
        for value in values:
            if hasattr(value, "model_dump"):
                value = value.model_dump()
            if not isinstance(value, dict):
                continue
            item = {
                "dimension": str(value.get("dimension") or "").strip(),
                "finding": str(value.get("finding") or "").strip(),
                "applicability": str(value.get("applicability") or "").strip(),
                "source_urls": sorted(
                    set(
                        str(url).strip()
                        for url in value.get("source_urls") or []
                        if str(url).strip()
                    )
                ),
            }
            if not all(item[key] for key in ("dimension", "finding", "applicability")):
                continue
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            normalized[key] = item
        if normalized:
            list_add[field] = list(normalized.values())

    for field in _NESTED_FIELDS:
        obj = patch.get(field)
        if isinstance(obj, dict):
            base = dict(existing.get(field) or {})
            for k, v in obj.items():
                if isinstance(v, list):
                    if v:
                        base[k] = sorted({*(base.get(k) or []), *[str(x) for x in v if x]})
                elif v not in (None, ""):
                    base[k] = v
            if base:
                set_fields[field] = base

    # A fictional profile must never retain contact details from an earlier
    # manual edit or a legacy collection run.
    if patch.get("is_fictional") is True:
        set_fields["contact"] = {
            "phone": "",
            "email": "",
            "wechat": "",
            "other_social": [],
        }
        set_fields["company_root_domain"] = ""

    return set_fields, list_add


def _replace_research_collections(
    existing: dict[str, Any] | None,
    profile: dict[str, Any],
    set_fields: dict[str, Any],
    list_add: dict[str, list[Any]],
) -> None:
    """Persist the already merged research profile as the authoritative list state.

    Persona research merges old and new facts before consistency review. Re-applying
    that result with ``$addToSet`` would retain legacy placeholders that the review
    intentionally removed, so enrichment replaces only the reviewed list fields.
    """
    if not existing:
        return
    for field in (*_LIST_FIELDS, *_OBJECT_LIST_FIELDS):
        if field not in profile:
            continue
        set_fields[field] = list(list_add.pop(field, []))


def _normalize_profile_sources(profile: dict[str, Any]) -> dict[str, Any]:
    """Separate model evidence sources from collection provenance records.

    ``PersonaProfile.sources`` is a list of public websites/platforms, while
    ``persons.sources`` stores structured ingestion provenance.  Keeping them
    under the same field silently discarded the model evidence during upsert.
    """
    normalized = dict(profile or {})
    raw_sources = normalized.pop("sources", None)
    if isinstance(raw_sources, list):
        source_urls = [
            value.strip()
            for value in raw_sources
            if isinstance(value, str) and value.strip()
        ]
        if source_urls:
            normalized["source_urls"] = [
                *list(normalized.get("source_urls") or []),
                *source_urls,
            ]
    return normalized


async def upsert_person(
    db: AsyncIOMotorDatabase,
    *,
    profile: dict[str, Any],
    project_id: str = "",
    source: str = "",
    ref_id: str = "",
    finding_id: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    """
    增量归并一条人设档案（全局，不强制绑定项目）。

    Args:
        profile: PersonaProfile.model_dump() 结果或等价 dict（至少含 name）。
        project_id: 可选溯源项目，非空时写入 project_ids[]。
        source: 信息来源（如 web / xhs / douyin / mobile）。
        ref_id: 原始业务ID（如 user_id / sec_uid / contact_id）。
        finding_id: 关联 finding。

    Returns:
        归并后的最新 person 文档。
    """
    profile = _normalize_profile_sources(profile)
    name = str(profile.get("name") or "").strip()
    if not name:
        raise ValueError("人设档案缺少 name，无法入库")
    company = str(profile.get("company") or "").strip()
    generation_key = str(profile.get("generation_key") or "").strip()
    pid = (
        fictional_person_id(generation_key)
        if profile.get("is_fictional") and generation_key
        else person_id(name, company)
    )

    existing = await get_person(db, pid)
    set_fields, list_add = _merge_set_fields(existing, profile)
    if source.startswith("synthetic_research"):
        _replace_research_collections(existing, profile, set_fields, list_add)
    set_fields["person_id"] = pid

    now = _now()
    update: dict[str, Any] = {
        "$set": set_fields,
        "$setOnInsert": {"created_at": now},
        "$inc": {"profile_version": 1},
    }
    if source.startswith("synthetic_research"):
        update["$inc"]["research_rounds"] = 1
        set_fields["last_researched_at"] = now
    add_to_set: dict[str, Any] = {
        field: {"$each": values} for field, values in list_add.items()
    }
    if project_id:
        add_to_set["project_ids"] = project_id
    if add_to_set:
        update["$addToSet"] = add_to_set
    if source or ref_id or finding_id:
        update["$push"] = {
            "sources": {
                "source": source,
                "ref_id": ref_id,
                "finding_id": finding_id,
                "task_id": task_id,
                "project_id": project_id,
                "collected_at": now.isoformat(),
            }
        }
    await db[PERSONS_COLLECTION].update_one({"person_id": pid}, update, upsert=True)
    return await get_person(db, pid) or set_fields


async def get_person(db: AsyncIOMotorDatabase, person_id_val: str) -> dict[str, Any] | None:
    return await db[PERSONS_COLLECTION].find_one({"person_id": person_id_val}, {"_id": 0})


async def search_persons(
    db: AsyncIOMotorDatabase,
    project_id: str = "",
    *,
    keyword: str = "",
    company: str = "",
    industry: str = "",
    position: str = "",
    personality: str = "",
    age_min: int | None = None,
    age_max: int | None = None,
    tags: list[str] | None = None,
    min_confidence: float = 0.0,
    sort: str = "confidence_desc",
    limit: int = 20,
    skip: int = 0,
    summary_only: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """面向人设的检索：全局库按公司/行业/职位/标签/关键词/置信度筛选，分页返回。

    project_id 可选：传入时按 project_ids 溯源筛选，不传则全库检索。
    """
    query: dict[str, Any] = {}
    if project_id:
        query["project_ids"] = project_id
    if company:
        query["company"] = {"$regex": company, "$options": "i"}
    if industry:
        query["industry"] = {"$regex": industry, "$options": "i"}
    if position:
        query["position"] = {"$regex": position, "$options": "i"}
    if personality:
        query["personality"] = {"$regex": personality, "$options": "i"}
    if age_min is not None or age_max is not None:
        age_query: dict[str, int] = {}
        if age_min is not None:
            age_query["$gte"] = max(18, int(age_min))
        if age_max is not None:
            age_query["$lte"] = min(75, int(age_max))
        query["age"] = age_query
    if tags:
        query["tags"] = {"$all": tags}
    if min_confidence > 0:
        query["confidence"] = {"$gte": min_confidence}
    if keyword:
        rx = {"$regex": keyword, "$options": "i"}
        query["$or"] = [
            {"name": rx}, {"company": rx}, {"position": rx},
            {"background": rx}, {"personality": rx}, {"summary": rx},
            {"generation_brief": rx}, {"career_path": rx}, {"life_stage": rx},
            {"decision_style": rx}, {"communication_style": rx},
            {"technology_attitude": rx}, {"pain_points": rx}, {"motivations": rx},
            {"content_preferences": rx}, {"tags": rx}, {"aliases": rx},
        ]

    sort_map = {
        "confidence_desc": [("confidence", -1)],
        "time_desc": [("updated_at", -1)],
    }
    sort_spec = sort_map.get(sort, [("confidence", -1)])

    total = await db[PERSONS_COLLECTION].count_documents(query)
    cursor = (
        db[PERSONS_COLLECTION]
        .find(query, _SUMMARY_PROJECTION if summary_only else {"_id": 0})
        .sort(sort_spec)
        .skip(skip)
        .limit(limit)
    )
    items = await cursor.to_list(limit)
    return items, total


async def delete_person(db: AsyncIOMotorDatabase, person_id_val: str) -> bool:
    result = await db[PERSONS_COLLECTION].delete_one({"person_id": person_id_val})
    return result.deleted_count > 0


async def delete_persons_by_project(db: AsyncIOMotorDatabase, project_id: str) -> int:
    """移除某项目对全局人设的溯源引用（不删除跨项目共享的人设）。"""
    result = await db[PERSONS_COLLECTION].update_many(
        {"project_ids": project_id},
        {"$pull": {"project_ids": project_id}},
    )
    return result.modified_count
