"""
统一上下文聚合层 — context resolver（只读）。

输入一个实体标识（person_id 或 公司 root_domain / company_meta_id / 公司名），
一次性解析出完整上下文包，供 AI 中枢 Agent 与 Word 产物工厂消费：

  人物画像 + 公司元信息 + 资产情报 + findings(含话术/资料) + 接触画像 + 关联人物

设计原则：
- 只读聚合层，不写库、不含平台协议细节；跳转/关联收敛在本层内部。
- 所有数据读取收敛在各 DAO（persons/company_meta/fofa_assets/findings/contact_profiles），
  调用侧只表达"解析某实体的完整上下文"这一语义。
- 输出结构稳定，字段命名与既有 API 一致，缺失来源以空值/空列表兜底而非报错。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import company_meta as company_meta_dao
from api.dao import contact_profiles as contact_profiles_dao
from api.dao import findings as findings_dao
from api.dao import fofa_assets as fofa_assets_dao
from api.dao import persons as persons_dao
from api.dao import bidding as bidding_dao
from api.services import entity_ref

# 聚合上限，避免单次解析拉取过多数据拖慢 AI/产物流程
_MAX_FINDINGS = 20
_MAX_ASSETS = 200
_MAX_CONTACT_PROFILES = 20
_MAX_RELATED_PERSONS = 20
_MAX_BIDDING_RECORDS = 50
_MAX_ENGAGEMENT_FINDINGS = 12
_MAX_ENGAGEMENT_WEBSITES = 12
_MAX_ENGAGEMENT_DOCUMENTS = 12
_MAX_ENGAGEMENT_PERSONAS = 8


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _collect_finding_ids(person: dict[str, Any]) -> list[str]:
    """从人设 sources[] 收敛去重的 finding_id 列表（保序）。"""
    seen: set[str] = set()
    ordered: list[str] = []
    for src in person.get("sources") or []:
        fid = str((src or {}).get("finding_id") or "").strip()
        if fid and fid not in seen:
            seen.add(fid)
            ordered.append(fid)
    return ordered


async def _resolve_findings_bundle(
    db: AsyncIOMotorDatabase,
    finding_ids: list[str],
    *,
    limit: int = _MAX_FINDINGS,
) -> list[dict[str, Any]]:
    """按 finding_id 逐条解析 finding + 关联话术 + 资料。"""
    bundle: list[dict[str, Any]] = []
    for fid in finding_ids[:limit]:
        finding = await findings_dao.get_finding(db, fid)
        if not finding:
            continue
        finding["copywriting"] = await findings_dao.get_copywriting(db, fid)
        finding["profile"] = await findings_dao.get_profile(db, fid)
        bundle.append(finding)
    return bundle


async def _resolve_company(
    db: AsyncIOMotorDatabase,
    *,
    company_meta_id: str = "",
    root_domain: str = "",
) -> dict[str, Any] | None:
    """优先按 company_meta_id 解析公司元信息，退化到 root_domain。"""
    if company_meta_id:
        doc = await company_meta_dao.get_company_meta_by_id(db, company_meta_id)
        if doc:
            return doc
    if root_domain:
        return await company_meta_dao.find_company_meta_by_root_domain(db, root_domain)
    return None


async def _resolve_assets(
    db: AsyncIOMotorDatabase,
    *,
    project_ids: list[str],
    root_domain: str = "",
    limit: int = _MAX_ASSETS,
) -> list[dict[str, Any]]:
    """解析公司资产：有项目上下文时按 (project, root_domain) 聚合去重，否则跨项目按 root_domain。"""
    if not root_domain:
        return []
    seen: set[str] = set()
    assets: list[dict[str, Any]] = []
    if project_ids:
        for pid in project_ids:
            for doc in await fofa_assets_dao.query_assets(db, pid, root_domain, limit=limit):
                aid = doc.get("asset_id")
                if aid and aid not in seen:
                    seen.add(aid)
                    assets.append(doc)
                if len(assets) >= limit:
                    return assets
    if not assets:
        assets = await fofa_assets_dao.query_assets_by_root_domain(db, root_domain, limit=limit)
    return assets[:limit]


async def resolve_person_context(
    db: AsyncIOMotorDatabase,
    person_id: str,
    *,
    findings_limit: int = _MAX_FINDINGS,
    assets_limit: int = _MAX_ASSETS,
) -> dict[str, Any] | None:
    """解析单个人物的完整上下文包。person 不存在时返回 None。"""
    person = await persons_dao.get_person(db, person_id)
    if not person:
        return None

    root_domain = str(person.get("company_root_domain") or "").strip()
    finding_ids = _collect_finding_ids(person)

    company = await _resolve_company(
        db,
        company_meta_id=str(person.get("company_meta_id") or "").strip(),
        root_domain=root_domain,
    )
    assets = await _resolve_assets(
        db,
        project_ids=list(person.get("project_ids") or []),
        root_domain=root_domain,
        limit=assets_limit,
    )
    findings = await _resolve_findings_bundle(db, finding_ids, limit=findings_limit)
    contact_profiles = await contact_profiles_dao.list_by_finding_ids(
        db, finding_ids, limit=_MAX_CONTACT_PROFILES
    )

    related_refs = entity_ref.build_person_related_refs(
        person=person,
        company=company,
        assets=assets,
        findings=findings,
        contact_profiles=contact_profiles,
    )

    return {
        "entity": {"type": "person", "id": person_id},
        "person": person,
        "company": company,
        "assets": assets,
        "assets_total": len(assets),
        "findings": findings,
        "findings_total": len(findings),
        "contact_profiles": contact_profiles,
        "related_refs": related_refs,
        "generated_at": _now_iso(),
    }


async def resolve_company_context(
    db: AsyncIOMotorDatabase,
    *,
    company_meta_id: str = "",
    root_domain: str = "",
    company_name: str = "",
    assets_limit: int = _MAX_ASSETS,
) -> dict[str, Any]:
    """解析公司维度上下文：公司元信息 + 资产 + 关联人物。"""
    company = await _resolve_company(
        db, company_meta_id=company_meta_id, root_domain=root_domain
    )
    resolved_root = root_domain or str((company or {}).get("root_domain") or "").strip()
    resolved_name = company_name or str((company or {}).get("normalized_name") or "").strip()

    assets = await _resolve_assets(
        db, project_ids=[], root_domain=resolved_root, limit=assets_limit
    )

    related_persons: list[dict[str, Any]] = []
    if resolved_name:
        related_persons, _ = await persons_dao.search_persons(
            db, company=resolved_name, limit=_MAX_RELATED_PERSONS
        )

    bidding_records = await bidding_dao.query_company_records(
        db,
        target_id=str((company or {}).get("target_id") or ""),
        company_name=resolved_name,
        limit=_MAX_BIDDING_RECORDS,
    )

    related_refs = entity_ref.build_company_related_refs(
        company=company,
        assets=assets,
        related_persons=related_persons,
    )

    return {
        "entity": {
            "type": "company",
            "id": company_meta_id or resolved_root or resolved_name,
        },
        "company": company,
        "root_domain": resolved_root,
        "assets": assets,
        "assets_total": len(assets),
        "related_persons": related_persons,
        "related_persons_total": len(related_persons),
        "bidding_records": bidding_records,
        "bidding_records_total": len(bidding_records),
        "related_refs": related_refs,
        "generated_at": _now_iso(),
    }


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    """Bound nested context without flattening evidence-bearing structures."""
    if depth >= 4:
        return str(value)[:1_000]
    if isinstance(value, str):
        return value[:2_000]
    if isinstance(value, list):
        return [_bounded_value(item, depth=depth + 1) for item in value[:30]]
    if isinstance(value, dict):
        return {
            str(key): _bounded_value(item, depth=depth + 1)
            for key, item in list(value.items())[:50]
        }
    return value


def _compact_finding(item: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: item.get(key)
        for key in (
            "finding_id",
            "project_id",
            "target_id",
            "source",
            "type",
            "channel",
            "role",
            "label",
            "value",
            "party_name",
            "party_role",
            "summary",
            "context",
            "evidence",
            "attention_score",
            "attention_reason",
            "url",
            "source_url",
            "source_document_id",
            "source_document_version_id",
        )
        if item.get(key) not in (None, "", [], {})
    }
    return _bounded_value(result)


def _compact_website(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    intro = data.get("intro") if isinstance(data.get("intro"), dict) else {}
    if not intro and isinstance(item.get("intro"), dict):
        intro = item["intro"]
    findings = [
        _compact_finding(value)
        for value in data.get("findings") or []
        if isinstance(value, dict)
    ]
    architecture = (
        data.get("site_architecture")
        or data.get("business_architecture")
        or data.get("navigation")
        or {}
    )
    return {
        "url": item.get("url"),
        "success": item.get("success"),
        "error": str(item.get("error") or "")[:500],
        "screenshot_object_id": item.get("screenshot_object_id")
        or data.get("screenshot_object_id"),
        "intro": {
            key: intro.get(key)
            for key in (
                "final_url",
                "domain",
                "site_name",
                "entity_name",
                "summary",
            )
            if intro.get(key)
        },
        "architecture": _bounded_value(architecture),
        "findings": findings[:2],
    }


def _compact_source_document(item: dict[str, Any]) -> dict[str, Any]:
    document = item.get("document") if isinstance(item.get("document"), dict) else {}
    return {
        "document_id": item.get("document_id") or document.get("document_id"),
        "version_id": item.get("latest_version_id") or document.get("latest_version_id"),
        "source_type": document.get("source_type"),
        "title": document.get("title"),
        "url": document.get("canonical_url"),
        "summary": document.get("summary"),
        "latest_subject_match": item.get("latest_subject_match"),
        "latest_score": item.get("latest_score"),
        "latest_analysis": _bounded_value(item.get("latest_analysis") or {}),
    }


def _compact_persona(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "person_id",
            "name",
            "age",
            "gender",
            "industry",
            "company",
            "department",
            "position",
            "position_level",
            "location",
            "summary",
            "background",
            "personality",
            "communication_style",
            "decision_style",
            "pain_points",
            "motivations",
            "interests",
            "tags",
            "risk_signals",
            "confidence",
            "profile_version",
        )
        if item.get(key) not in (None, "", [], {})
    }


def _compact_target(item: dict[str, Any] | None) -> dict[str, Any]:
    """Keep stable identity and business facts without duplicating DAO metadata."""
    if not item:
        return {}
    result = {
        key: item.get(key)
        for key in (
            "target_id",
            "target_type",
            "canonical_name",
            "aliases",
            "root_domain",
            "root_domains",
            "industry",
            "organization_type",
            "research_summary",
            "responsibilities",
            "services",
            "business_keywords",
            "key_people",
            "latest_research_id",
            "last_researched_at",
        )
        if item.get(key) not in (None, "", [], {})
    }
    return _bounded_value(result)


def _compact_project_target(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {}
    result = {
        key: item.get(key)
        for key in (
            "project_target_id",
            "project_id",
            "target_id",
            "target_name",
            "root_domain",
            "root_domains",
            "search_terms",
            "objectives",
            "relation",
            "root_target_id",
            "root_target_name",
            "parent_target_id",
            "parent_target_name",
            "relation_type",
            "relation_depth",
            "ownership_percent",
            "lineage_target_ids",
            "lineage_target_names",
            "last_collected_at",
        )
        if item.get(key) not in (None, "", [], {})
    }
    return _bounded_value(result)


def _compact_project_link(item: dict[str, Any]) -> dict[str, Any]:
    """Describe cross-project membership without copying channel search plans."""
    return {
        key: item.get(key)
        for key in (
            "project_target_id",
            "project_id",
            "target_id",
            "target_name",
            "active",
            "relation",
            "root_target_id",
            "root_target_name",
            "parent_target_id",
            "parent_target_name",
            "relation_type",
            "relation_depth",
            "ownership_percent",
            "last_collected_at",
        )
        if item.get(key) not in (None, "", [], {})
    }


def _compact_copywriting_summary(item: dict[str, Any] | None) -> dict[str, Any]:
    """Expose enough neighboring copy context without duplicating full scripts."""
    if not item:
        return {}
    scenario = item.get("scenario") if isinstance(item.get("scenario"), dict) else {}
    scripts = item.get("scripts") if isinstance(item.get("scripts"), list) else []
    return {
        "finding_id": item.get("finding_id"),
        "status": item.get("status"),
        "scenario_name": scenario.get("scenario_name"),
        "scenario_overview": str(scenario.get("scenario_overview") or "")[:500],
        "channels": list(
            dict.fromkeys(
                str(script.get("channel") or "").strip()
                for script in scripts
                if isinstance(script, dict) and str(script.get("channel") or "").strip()
            )
        ),
    }


def _compact_target_research(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {}
    result = {
        key: item.get(key)
        for key in (
            "research_id",
            "project_id",
            "target_id",
            "research_version",
            "canonical_name",
            "summary",
            "industry",
            "organization_type",
            "confidence",
            "researched_at",
        )
        if item.get(key) not in (None, "", [], {})
    }
    for key, limit in (
        ("responsibilities", 30),
        ("services", 30),
        ("aliases", 20),
        ("root_domains", 12),
        ("business_keywords", 40),
        ("public_contacts", 20),
        ("key_people", 20),
        ("related_targets", 20),
        ("sources", 30),
        ("evidence", 40),
    ):
        if item.get(key):
            result[key] = list(item.get(key) or [])[:limit]
    if item.get("search_terms_by_channel"):
        result["search_terms_by_channel"] = item["search_terms_by_channel"]
    return _bounded_value(result)


def _compact_person_intelligence(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {}
    result = {
        key: item.get(key)
        for key in (
            "intel_id",
            "name",
            "aliases",
            "organization",
            "position",
            "department",
            "location",
            "summary",
            "background",
            "research_areas",
            "public_contacts",
            "profile",
            "confidence",
            "target_id",
            "project_ids",
            "profile_version",
            "research_rounds",
            "last_researched_at",
            "artifact_ids",
            "engagement_plan",
        )
        if item.get(key) not in (None, "", [], {})
    }
    for key, limit in (
        ("sources", 30),
        ("evidence", 50),
        ("context_signals", 20),
        ("recommended_personas", 10),
        ("scenarios", 20),
        ("sample_copywritings", 12),
    ):
        if item.get(key):
            result[key] = list(item.get(key) or [])[:limit]
    return _bounded_value(result)


async def resolve_engagement_context(
    db: AsyncIOMotorDatabase,
    *,
    finding_id: str = "",
    target_id: str = "",
    target_name: str = "",
    project_id: str = "",
    person_intel_id: str = "",
    person_query: str = "",
) -> dict[str, Any]:
    """Build a bounded evidence package for a complete engagement strategy.

    This resolver only aggregates persisted facts. Role responsibility, business
    fit and stakeholder relationships remain explicit Agent inferences and must
    be grounded in the returned evidence or newly verified public sources.
    """
    from api.dao import person_intelligence as intelligence_dao
    from api.dao import source_documents as source_documents_dao
    from api.dao import target_research as target_research_dao
    from api.dao import targets as targets_dao
    from api.services import website_records

    selected_finding: dict[str, Any] | None = None
    selected_copywriting: dict[str, Any] | None = None
    selected_profile: dict[str, Any] | None = None
    if finding_id:
        selected_finding, selected_copywriting, selected_profile = await asyncio.gather(
            findings_dao.get_finding(db, finding_id),
            findings_dao.get_copywriting(db, finding_id),
            findings_dao.get_profile(db, finding_id),
        )
        if selected_finding:
            target_id = target_id or str(selected_finding.get("target_id") or "")
            project_id = project_id or str(selected_finding.get("project_id") or "")
            target_name = target_name or str(selected_finding.get("party_name") or "")

    explicit_intelligence: dict[str, Any] | None = None
    if person_intel_id:
        explicit_intelligence = await intelligence_dao.get_intelligence(
            db,
            person_intel_id,
        )
    elif person_query:
        intelligence_items, _ = await intelligence_dao.search_intelligence(
            db,
            keyword=str(person_query or "").strip(),
            target_id=target_id,
            project_id=project_id if not target_id else "",
            limit=5,
        )
        explicit_intelligence = intelligence_items[0] if intelligence_items else None

    if explicit_intelligence:
        target_id = target_id or str(explicit_intelligence.get("target_id") or "")
        target_name = target_name or str(
            explicit_intelligence.get("organization") or ""
        )
        project_ids = [
            str(value).strip()
            for value in explicit_intelligence.get("project_ids") or []
            if str(value).strip()
        ]
        if not project_id and project_ids:
            project_id = project_ids[0]

    target = await targets_dao.get_target(db, target_id) if target_id else None
    if target is None and target_name:
        target = await targets_dao.find_target(db, name=target_name)
    if target:
        target_id = str(target.get("target_id") or target_id)
        target_name = str(
            target.get("canonical_name")
            or target.get("display_name")
            or target.get("name")
            or target_name
        )

    project_links: list[dict[str, Any]] = []
    if target_id:
        project_links = await targets_dao.list_target_projects(db, target_id)
        if not project_id and project_links:
            project_id = str(project_links[0].get("project_id") or "")
    project_target = (
        await targets_dao.get_project_target(
            db, project_id=project_id, target_id=target_id
        )
        if project_id and target_id
        else None
    )

    async def _load_research() -> dict[str, Any] | None:
        if not target_id:
            return None
        item = await target_research_dao.get_latest_research(
            db, target_id=target_id, project_id=project_id
        )
        if item is None and project_id:
            item = await target_research_dao.get_latest_research(
                db, target_id=target_id
            )
        return item

    async def _load_findings() -> tuple[list[dict[str, Any]], int]:
        if not target_id:
            return [], 0
        result = await findings_dao.query_target_findings_with_copywriting(
            db,
            target_id,
            project_id=project_id,
            limit=_MAX_ENGAGEMENT_FINDINGS,
        )
        if not result[0] and project_id:
            result = await findings_dao.query_target_findings_with_copywriting(
                db,
                target_id,
                limit=_MAX_ENGAGEMENT_FINDINGS,
            )
        return result

    async def _load_websites() -> tuple[list[dict[str, Any]], int]:
        if not target_id:
            return [], 0
        project_candidates = list(
            dict.fromkeys(
                value
                for value in [
                    project_id,
                    *(str(link.get("project_id") or "") for link in project_links),
                ]
                if value
            )
        )[:4]
        if not project_candidates:
            return [], 0
        results = await asyncio.gather(
            *(
                website_records.list_website_records(
                    db,
                    project_id=value,
                    target_id=target_id,
                    limit=_MAX_ENGAGEMENT_WEBSITES,
                )
                for value in project_candidates
            )
        )
        by_url: dict[str, dict[str, Any]] = {}
        total = 0
        for items, count in results:
            total += int(count or 0)
            for item in items:
                url = str(item.get("url") or "")
                if url and url not in by_url:
                    by_url[url] = item
        return list(by_url.values())[:_MAX_ENGAGEMENT_WEBSITES], total

    async def _load_documents() -> tuple[list[dict[str, Any]], int]:
        if not target_id:
            return [], 0
        result = await source_documents_dao.list_target_documents(
            db,
            target_id,
            project_id=project_id,
            limit=_MAX_ENGAGEMENT_DOCUMENTS,
        )
        if not result[0] and project_id:
            result = await source_documents_dao.list_target_documents(
                db,
                target_id,
                limit=_MAX_ENGAGEMENT_DOCUMENTS,
            )
        return result

    async def _load_intelligence() -> dict[str, Any] | None:
        if explicit_intelligence is not None:
            return explicit_intelligence
        items, _ = await intelligence_dao.search_intelligence(
            db,
            keyword="",
            target_id=target_id,
            project_id=project_id if not target_id else "",
            limit=5,
        )
        return items[0] if items else None

    research, finding_result, website_result, document_result, intelligence = (
        await asyncio.gather(
            _load_research(),
            _load_findings(),
            _load_websites(),
            _load_documents(),
            _load_intelligence(),
        )
    )
    target_findings, target_finding_total = finding_result
    websites, website_total = website_result
    documents, document_total = document_result

    persona_ids = [
        str(item.get("person_id") or "")
        for item in (intelligence or {}).get("recommended_personas") or []
        if isinstance(item, dict) and item.get("person_id")
    ]
    personas = [
        item
        for item in await asyncio.gather(
            *(persons_dao.get_person(db, value) for value in persona_ids[:_MAX_ENGAGEMENT_PERSONAS])
        )
        if item
    ] if persona_ids else []
    if len(personas) < _MAX_ENGAGEMENT_PERSONAS:
        role_keyword = str(
            (intelligence or {}).get("position")
            or (intelligence or {}).get("department")
            or (research or {}).get("industry")
            or ""
        ).strip()
        candidates, _ = await persons_dao.search_persons(
            db,
            keyword=role_keyword,
            is_fictional=True,
            min_confidence=0.6,
            limit=_MAX_ENGAGEMENT_PERSONAS,
            summary_only=True,
        )
        known = {str(item.get("person_id") or "") for item in personas}
        personas.extend(
            item
            for item in candidates
            if str(item.get("person_id") or "") not in known
        )
        personas = personas[:_MAX_ENGAGEMENT_PERSONAS]

    exact_finding = _compact_finding(selected_finding or {})
    if selected_copywriting:
        exact_finding["existing_copywriting"] = _bounded_value(selected_copywriting)
    if selected_profile:
        exact_finding["existing_profile"] = _bounded_value(selected_profile)

    gaps: list[str] = []
    if not target:
        gaps.append("未解析到稳定 Target；机构身份和项目归属需要核验")
    if not research:
        gaps.append("缺少机构深研；需要补充职责、服务、组织结构和业务关键词")
    if not websites:
        gaps.append("缺少可用网站分析；需要核验官网栏目、应用系统与业务架构")
    if finding_id and not selected_finding:
        gaps.append(f"未找到指定 Finding：{finding_id}")
    if not intelligence:
        gaps.append("缺少真实人物情报；需要身份消歧、职责和公开来源核验")
    if not personas:
        gaps.append("缺少可复用虚构人设；需要按业务角色主动研究并构建")

    return {
        "context_type": "engagement_strategy",
        "scope": {
            "project_id": project_id,
            "target_id": target_id,
            "target_name": target_name,
            "finding_id": finding_id,
            "person_intel_id": str((intelligence or {}).get("intel_id") or person_intel_id),
        },
        "target": _compact_target(target),
        "project_target": _compact_project_target(project_target),
        "project_links": [
            _compact_project_link(item) for item in project_links[:10]
        ],
        "target_research": _compact_target_research(research),
        "selected_finding": exact_finding,
        "top_findings": [
            {
                **_compact_finding(item),
                **(
                    {
                        "existing_copywriting": _compact_copywriting_summary(
                            item.get("copywriting")
                        )
                    }
                    if item.get("copywriting")
                    else {}
                ),
            }
            for item in target_findings
            if str(item.get("finding_id") or "") != str(finding_id or "")
        ],
        "top_findings_total": target_finding_total,
        "websites": [_compact_website(item) for item in websites],
        "websites_total": website_total,
        "source_documents": [_compact_source_document(item) for item in documents],
        "source_documents_total": document_total,
        "person_intelligence": _compact_person_intelligence(intelligence),
        "persona_candidates": [_compact_persona(item) for item in personas],
        "evidence_rules": {
            "facts": "只能来自 Finding、Target 深研、网站、来源文档、人物情报或本轮核验网页",
            "inferences": "业务职责、管理范围、利益相关方和触达偏好必须标注为推断并写明依据",
            "identity": "真实人物情报与虚构沟通人设必须分离，禁止把虚构设定写成目标事实",
        },
        "missing_context": gaps,
        "generated_at": _now_iso(),
    }
