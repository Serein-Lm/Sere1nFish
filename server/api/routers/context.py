"""
统一上下文聚合层 API（只读）。

输入实体标识（person_id 或 公司标识），一次性返回完整上下文包：
人物画像 + 公司元信息 + 资产 + findings(话术/资料) + 接触画像 + 关联人物。

聚合逻辑收敛在 api.services.context_resolver，本层只做鉴权与请求/响应适配。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_current_active_user
from api.db.mongodb import get_db
from api.services import context_resolver

router = APIRouter(dependencies=[Depends(get_current_active_user)])


@router.get("/person/{person_id}")
async def get_person_context(person_id: str) -> dict:
    """解析单个人物的完整上下文包。"""
    bundle = await context_resolver.resolve_person_context(get_db(), person_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="人物不存在")
    return bundle


@router.get("/company")
async def get_company_context(
    company_meta_id: str = "",
    root_domain: str = "",
    company: str = "",
) -> dict:
    """解析公司维度上下文（公司元信息 + 资产 + 关联人物）。"""
    if not (company_meta_id or root_domain or company):
        raise HTTPException(
            status_code=400,
            detail="需提供 company_meta_id、root_domain 或 company 之一",
        )
    return await context_resolver.resolve_company_context(
        get_db(),
        company_meta_id=company_meta_id,
        root_domain=root_domain,
        company_name=company,
    )


@router.get("/relations")
async def get_entity_relations(
    entity_type: str,
    id: str = "",
    root_domain: str = "",
    company: str = "",
) -> dict:
    """返回实体的规范化关联引用（轻量跳转图），供“以人物/公司为中心的关联跳转”。"""
    db = get_db()
    if entity_type == "person":
        bundle = await context_resolver.resolve_person_context(db, id)
        if not bundle:
            raise HTTPException(status_code=404, detail="人物不存在")
    elif entity_type == "company":
        if not (id or root_domain or company):
            raise HTTPException(
                status_code=400,
                detail="company 实体需提供 id(meta_id)、root_domain 或 company 之一",
            )
        bundle = await context_resolver.resolve_company_context(
            db, company_meta_id=id, root_domain=root_domain, company_name=company
        )
    else:
        raise HTTPException(status_code=400, detail="entity_type 仅支持 person 或 company")
    return {
        "entity": bundle.get("entity"),
        "related_refs": bundle.get("related_refs", []),
        "generated_at": bundle.get("generated_at"),
    }
