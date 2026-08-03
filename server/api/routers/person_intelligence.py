"""人物 OSINT 情报 API（只读薄层）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import get_current_active_user
from api.db.mongodb import get_db
from api.services import person_intelligence as intelligence_service


router = APIRouter(dependencies=[Depends(get_current_active_user)])


@router.get("")
async def list_intelligence(
    keyword: str = "",
    organization: str = "",
    target_id: str = "",
    project_id: str = "",
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    sort: str = Query(default="updated_desc", pattern="^(updated_desc|confidence_desc|name_asc)$"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=200),
    summary_only: bool = False,
):
    items, total = await intelligence_service.list_person_intelligence(
        get_db(),
        keyword=keyword.strip(),
        organization=organization.strip(),
        target_id=target_id.strip(),
        project_id=project_id.strip(),
        min_confidence=min_confidence,
        sort=sort,
        skip=skip,
        limit=limit,
        summary_only=summary_only,
    )
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/{intel_id}")
async def get_intelligence(intel_id: str):
    item = await intelligence_service.get_person_intelligence(get_db(), intel_id.strip())
    if not item:
        raise HTTPException(status_code=404, detail="人物情报不存在")
    return item
