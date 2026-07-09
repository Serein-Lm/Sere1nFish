"""
人设库 API 路由（薄层）。

人设库全局化：默认不绑定项目，person_id = f(name, company)。
- POST /persons/collect ：AI 浏览器采集单个人物 → PersonaProfile 结构化 → 增量入库（后台执行）。
- GET  /persons         ：多维检索（公司/行业/职位/标签/关键词/置信度）分页。
- GET  /persons/{id}    ：查看单个人设。
- PUT  /persons/{id}    ：手动编辑归并。
- DELETE /persons/{id}  ：删除人设。

业务流程收敛在 service/dao，本层只做鉴权、请求/响应适配与调用。
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_current_active_user
from api.db.mongodb import get_db
from api.dao import persons as persons_dao
from core.background import spawn_background
from core.logger import get_logger

logger = get_logger("persons_router")

router = APIRouter(dependencies=[Depends(get_current_active_user)])


# ── 请求模型 ─────────────────────────────────────────

class PersonaCollectRequest(BaseModel):
    name: str = Field(..., description="人物姓名（必填）")
    company: str = Field(default="", description="所属公司")
    position: str = Field(default="", description="职位")
    extra: str = Field(default="", description="其他线索")
    project_id: str = Field(default="", description="可选溯源项目（人设库默认不绑定项目）")


class PersonUpsertRequest(BaseModel):
    profile: dict[str, Any] = Field(..., description="人设档案字段（至少含 name）")
    project_id: str = Field(default="", description="可选溯源项目")


# ── 采集 ─────────────────────────────────────────────

@router.post("/collect")
async def collect(req: PersonaCollectRequest):
    """触发单个人物人设采集（后台执行），返回 task_id。"""
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="人物姓名不能为空")

    from api.services.persona_collect import collect_persona
    from api.services.runtime_config import get_runtime_app_config

    app_config = await get_runtime_app_config()
    task_id = "persona_" + uuid.uuid4().hex[:16]

    async def _run() -> None:
        db = get_db()
        try:
            await collect_persona(
                db,
                app_config,
                name=name,
                project_id=req.project_id,
                company=req.company,
                position=req.position,
                extra=req.extra,
                task_id=task_id,
                source="web",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[persons] 采集失败 task={task_id} name='{name}': {exc}")

    spawn_background(_run(), name=f"persona_collect:{task_id}")
    return {"task_id": task_id, "status": "running", "name": name}


# ── 检索 / CRUD ──────────────────────────────────────

@router.get("")
async def list_persons(
    project_id: str = "",
    keyword: str = "",
    company: str = "",
    industry: str = "",
    position: str = "",
    tags: str = "",
    min_confidence: float = 0.0,
    sort: str = "confidence_desc",
    limit: int = 20,
    skip: int = 0,
):
    """多维检索人设库（全局），project_id 可选按溯源筛选。"""
    db = get_db()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    items, total = await persons_dao.search_persons(
        db,
        project_id,
        keyword=keyword,
        company=company,
        industry=industry,
        position=position,
        tags=tag_list,
        min_confidence=min_confidence,
        sort=sort,
        limit=limit,
        skip=skip,
    )
    return {"items": items, "total": total, "limit": limit, "skip": skip}


@router.get("/{person_id}")
async def get_person(person_id: str):
    """查看单个人设。"""
    db = get_db()
    doc = await persons_dao.get_person(db, person_id)
    if not doc:
        raise HTTPException(status_code=404, detail="人设不存在")
    return doc


@router.put("/{person_id}")
async def upsert_person(person_id: str, req: PersonUpsertRequest):
    """手动编辑归并人设（按 name+company 生成的 person_id 幂等归并）。"""
    db = get_db()
    profile = dict(req.profile or {})
    if not str(profile.get("name") or "").strip():
        raise HTTPException(status_code=400, detail="人设档案缺少 name")
    doc = await persons_dao.upsert_person(
        db,
        profile=profile,
        project_id=req.project_id,
        source="manual",
    )
    return doc


@router.delete("/{person_id}")
async def delete_person(person_id: str):
    """删除人设。"""
    db = get_db()
    ok = await persons_dao.delete_person(db, person_id)
    return {"ok": ok, "person_id": person_id}
