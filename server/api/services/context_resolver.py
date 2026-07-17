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
