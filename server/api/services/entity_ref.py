"""
统一关联层 — entity_ref{type, id}。

把散落在各集合的平台字段（company_meta_id / company_root_domain / finding_id /
asset_id / contact_id 等）收敛为规范化的实体引用 EntityRef，使"以人物/公司为中心的
大规模关联跳转"逻辑集中在统一层内部；调用侧（前端/AI/产物工厂）只消费稳定的
{type, id, label, meta} 结构，不再感知具体平台字段来源。

本模块是纯规范化/构建层：从已加载的领域文档构建引用，不做数据库查询、不写库，
因此可被 context_resolver（已加载全量文档）零额外开销地复用。
"""
from __future__ import annotations

from typing import Any


class EntityType:
    """规范化实体类型常量（关联跳转的统一命名）。"""

    PERSON = "person"
    COMPANY = "company"
    FINDING = "finding"
    ASSET = "asset"
    CONTACT_PROFILE = "contact_profile"
    PROJECT = "project"


def make_ref(
    entity_type: str,
    entity_id: str,
    *,
    label: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建一个稳定的实体引用。id 允许为空（如仅有 root_domain 的公司），由消费方按 meta 兜底跳转。"""
    return {
        "type": entity_type,
        "id": str(entity_id or ""),
        "label": str(label or ""),
        "meta": meta or {},
    }


# ── 单文档 → 引用 ─────────────────────────────────────

def ref_person(person: dict[str, Any]) -> dict[str, Any]:
    return make_ref(
        EntityType.PERSON,
        person.get("person_id", ""),
        label=person.get("name", ""),
        meta={"company": person.get("company", ""), "position": person.get("position", "")},
    )


def ref_company(
    company: dict[str, Any] | None = None,
    *,
    root_domain: str = "",
    name: str = "",
) -> dict[str, Any]:
    doc = company or {}
    return make_ref(
        EntityType.COMPANY,
        doc.get("meta_id", ""),
        label=doc.get("normalized_name") or name,
        meta={"root_domain": doc.get("root_domain") or root_domain},
    )


def ref_finding(finding: dict[str, Any]) -> dict[str, Any]:
    return make_ref(
        EntityType.FINDING,
        finding.get("finding_id", ""),
        label=finding.get("label") or finding.get("value", ""),
        meta={
            "source": finding.get("source", ""),
            "attention_score": finding.get("attention_score"),
            "has_copywriting": bool(finding.get("copywriting")),
        },
    )


def ref_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return make_ref(
        EntityType.ASSET,
        asset.get("asset_id", ""),
        label=asset.get("host") or asset.get("domain") or asset.get("ip", ""),
        meta={"port": asset.get("port", ""), "root_domain": asset.get("root_domain", "")},
    )


def ref_contact_profile(cp: dict[str, Any]) -> dict[str, Any]:
    return make_ref(
        EntityType.CONTACT_PROFILE,
        cp.get("contact_id", ""),
        label=cp.get("name", ""),
        meta={"platform": cp.get("platform", "")},
    )


# ── 聚合文档 → 关联引用集合 ───────────────────────────

# 关联引用集合的展开上限，避免单个实体挂载过多跳转项
_MAX_REF_ASSETS = 50


def _has_company(company: dict[str, Any] | None, root_domain: str, name: str) -> bool:
    return bool(company or root_domain or name)


def build_person_related_refs(
    *,
    person: dict[str, Any],
    company: dict[str, Any] | None,
    assets: list[dict[str, Any]] | None,
    findings: list[dict[str, Any]] | None,
    contact_profiles: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """从人物聚合上下文构建可跳转的关联引用（公司 / 发现 / 资产 / 接触画像）。"""
    root_domain = str(person.get("company_root_domain") or "").strip()
    company_name = str(person.get("company") or "").strip()
    refs: list[dict[str, Any]] = []
    if _has_company(company, root_domain, company_name):
        refs.append(ref_company(company, root_domain=root_domain, name=company_name))
    refs += [ref_finding(f) for f in (findings or [])]
    refs += [ref_asset(a) for a in (assets or [])[:_MAX_REF_ASSETS]]
    refs += [ref_contact_profile(c) for c in (contact_profiles or [])]
    return refs


def build_company_related_refs(
    *,
    company: dict[str, Any] | None,
    assets: list[dict[str, Any]] | None,
    related_persons: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """从公司聚合上下文构建可跳转的关联引用（关联人物 / 资产）。"""
    refs: list[dict[str, Any]] = [ref_person(p) for p in (related_persons or [])]
    refs += [ref_asset(a) for a in (assets or [])[:_MAX_REF_ASSETS]]
    return refs
