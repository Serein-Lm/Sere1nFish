"""
人设库 API 路由（薄层）。

人设库全局化：默认不绑定项目，虚构人设按背景指纹稳定归并。
- POST /persons/generate：背景设定 → AI 生成虚构 PersonaProfile → 增量入库（后台执行）。
- POST /persons/{id}/enrich：读取已有 summary 和证据，研究新来源并持续升级。
- GET  /persons/tasks/{id}：读取生成或持续研究任务的持久化阶段与结果。
- POST /persons/collect ：兼容旧客户端，语义同 generate。
- GET  /persons         ：多维检索（公司/行业/职位/标签/关键词/置信度）分页。
- GET  /persons/{id}    ：查看单个人设。
- PUT  /persons/{id}    ：手动编辑归并。
- DELETE /persons/{id}  ：删除人设。

业务流程收敛在 service/dao，本层只做鉴权、请求/响应适配与调用。
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_current_active_user
from api.dao import persona_research_tasks as research_tasks_dao
from api.db.mongodb import get_db
from api.dao import persons as persons_dao
from core.background import spawn_background
from core.logger import get_logger

logger = get_logger("persons_router")

router = APIRouter(dependencies=[Depends(get_current_active_user)])


# ── 请求模型 ─────────────────────────────────────────

class PersonaGenerateRequest(BaseModel):
    background: str = Field(..., min_length=1, description="虚构人物的背景设定（必填）")
    count: int = Field(default=36, ge=1, le=60, description="本轮生成数量")
    industries: list[str] = Field(default_factory=list, description="可选行业提示，留空由 AI 探索")
    age_ranges: list[str] = Field(default_factory=list, description="可选年龄提示，留空由 AI 探索")
    personalities: list[str] = Field(default_factory=list, description="可选性格提示，留空由 AI 探索")
    name: str = Field(default="", description="可选虚构姓名偏好，留空则自动生成")
    company: str = Field(default="", description="可选组织设定")
    position: str = Field(default="", description="可选职位设定")
    extra: str = Field(default="", description="其他生成约束")
    project_id: str = Field(default="", description="可选溯源项目（人设库默认不绑定项目）")


class PersonaCollectRequest(BaseModel):
    """旧采集入口的兼容请求；只生成单个虚构人物。"""

    name: str = Field(..., min_length=1, description="虚构姓名偏好")
    company: str = Field(default="", description="可选组织设定")
    position: str = Field(default="", description="可选职位设定")
    extra: str = Field(default="", description="其他生成约束")
    project_id: str = Field(default="", description="可选溯源项目")


class PersonUpsertRequest(BaseModel):
    profile: dict[str, Any] = Field(..., description="人设档案字段（至少含 name）")
    project_id: str = Field(default="", description="可选溯源项目")


class PersonaEnrichRequest(BaseModel):
    extra: str = Field(default="", description="本轮持续研究的补充方向")
    project_id: str = Field(default="", description="可选溯源项目")


# ── 采集 ─────────────────────────────────────────────

@router.post("/generate")
async def generate(req: PersonaGenerateRequest):
    """根据背景设定批量生成不对应真实自然人的虚构人物。"""
    background = (req.background or "").strip()
    if not background:
        raise HTTPException(status_code=400, detail="背景设定不能为空")

    from api.services.persona_collect import generate_personas
    from api.services.runtime_config import get_runtime_app_config

    app_config = await get_runtime_app_config()
    task_id = "persona_" + uuid.uuid4().hex[:16]
    await research_tasks_dao.create_task(
        get_db(),
        task_id=task_id,
        task_type="generate",
        requested_count=req.count,
        project_id=req.project_id,
    )

    async def _run() -> None:
        db = get_db()
        try:
            result = await generate_personas(
                db,
                app_config,
                background=background,
                count=req.count,
                industries=req.industries,
                age_ranges=req.age_ranges,
                personalities=req.personalities,
                name=req.name,
                project_id=req.project_id,
                company=req.company,
                position=req.position,
                extra=req.extra,
                task_id=task_id,
                source="synthetic_research",
            )
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await research_tasks_dao.update_task(
                    db,
                    task_id,
                    status="cancelled",
                    stage="interrupted",
                    message="服务重载导致任务中断，请重新发起",
                    error="runtime_reloaded",
                )
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[persons] 生成失败 task={task_id}: {exc}")
            await research_tasks_dao.update_task(
                db,
                task_id,
                status="failed",
                stage="failed",
                message="人设研究或生成失败",
                failed_count=req.count,
                error=str(exc),
            )
        else:
            items = list(result.get("items") or [])
            await research_tasks_dao.update_task(
                db,
                task_id,
                status="completed",
                stage="completed",
                message=f"已完成人设生成，共 {len(items)} 条",
                completed_count=len(items),
                failed_count=len(result.get("errors") or []),
                result_person_ids=[str(item.get("person_id") or "") for item in items],
                details={
                    "verified_sources": len(result.get("source_urls") or []),
                    "research_summary": str(result.get("research_summary") or "")[:1000],
                },
            )

    spawn_background(_run(), name=f"persona_generate:{task_id}")
    return {
        "task_id": task_id,
        "status": "queued",
        "name": (req.name or "").strip(),
        "count": req.count,
        "is_fictional": True,
    }


@router.post("/collect", deprecated=True)
async def collect(req: PersonaCollectRequest):
    """兼容旧客户端：按已有线索生成一名不对应真实自然人的虚构人物。"""
    background_parts = ["生成一名完全虚构、但职业与生活背景内部一致的人物"]
    if req.company.strip():
        background_parts.append(f"组织背景为 {req.company.strip()}")
    if req.position.strip():
        background_parts.append(f"岗位背景为 {req.position.strip()}")
    if req.extra.strip():
        background_parts.append(req.extra.strip())
    return await generate(
        PersonaGenerateRequest(
            background="；".join(background_parts),
            count=1,
            name=req.name.strip(),
            company=req.company,
            position=req.position,
            extra=req.extra,
            project_id=req.project_id,
        )
    )


@router.post("/{person_id}/enrich")
async def enrich(person_id: str, req: PersonaEnrichRequest):
    """从已有摘要出发，为同一虚构人设持续补充新来源和新信息。"""
    db = get_db()
    existing = await persons_dao.get_person(db, person_id)
    if not existing:
        raise HTTPException(status_code=404, detail="人设不存在")
    if existing.get("is_fictional") is not True:
        raise HTTPException(status_code=400, detail="仅支持持续研究虚构人设")
    active_task = await research_tasks_dao.get_active_person_task(db, person_id)
    if active_task:
        return {
            "task_id": active_task["task_id"],
            "status": active_task["status"],
            "person_id": person_id,
            "profile_version": int(existing.get("profile_version") or 1),
        }

    from api.services.persona_collect import enrich_persona
    from api.services.runtime_config import get_runtime_app_config

    app_config = await get_runtime_app_config()
    task_id = "persona_enrich_" + uuid.uuid4().hex[:16]
    await research_tasks_dao.create_task(
        db,
        task_id=task_id,
        task_type="enrich",
        requested_count=1,
        person_id=person_id,
        project_id=req.project_id,
    )

    async def _run() -> None:
        task_db = get_db()
        try:
            updated = await enrich_persona(
                task_db,
                app_config,
                person_id=person_id,
                project_id=req.project_id,
                extra=req.extra,
                task_id=task_id,
            )
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await research_tasks_dao.update_task(
                    task_db,
                    task_id,
                    status="cancelled",
                    stage="interrupted",
                    message="服务重载导致持续研究中断，请重新发起",
                    error="runtime_reloaded",
                )
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[persons] 持续研究失败 task={task_id}: {exc}")
            await research_tasks_dao.update_task(
                task_db,
                task_id,
                status="failed",
                stage="failed",
                message="持续研究失败",
                failed_count=1,
                error=str(exc),
            )
        else:
            await research_tasks_dao.update_task(
                task_db,
                task_id,
                status="completed",
                stage="completed",
                message="人设资料已升级",
                completed_count=1,
                result_person_ids=[str(updated.get("person_id") or person_id)],
                details={
                    "profile_version": int(updated.get("profile_version") or 1),
                    "research_rounds": int(updated.get("research_rounds") or 1),
                },
            )

    spawn_background(_run(), name=f"persona_enrich:{task_id}")
    return {
        "task_id": task_id,
        "status": "queued",
        "person_id": person_id,
        "profile_version": int(existing.get("profile_version") or 1),
    }


# ── 检索 / CRUD ──────────────────────────────────────


@router.get("/tasks/{task_id}")
async def get_research_task(task_id: str):
    """读取批量生成或持续研究的持久化进度。"""
    task = await research_tasks_dao.get_task(get_db(), task_id)
    if not task:
        raise HTTPException(status_code=404, detail="人设研究任务不存在")
    return task

@router.get("")
async def list_persons(
    project_id: str = "",
    keyword: str = "",
    company: str = "",
    industry: str = "",
    position: str = "",
    personality: str = "",
    age_min: int | None = None,
    age_max: int | None = None,
    tags: str = "",
    min_confidence: float = 0.0,
    sort: str = "confidence_desc",
    limit: int = 20,
    skip: int = 0,
    summary_only: bool = False,
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
        personality=personality,
        age_min=age_min,
        age_max=age_max,
        tags=tag_list,
        min_confidence=min_confidence,
        sort=sort,
        limit=limit,
        skip=skip,
        summary_only=summary_only,
        is_fictional=True,
    )
    return {"items": items, "total": total, "limit": limit, "skip": skip}


@router.get("/{person_id}")
async def get_person(person_id: str):
    """查看单个人设。"""
    db = get_db()
    doc = await persons_dao.get_person(db, person_id)
    if not doc or doc.get("is_fictional") is not True:
        raise HTTPException(status_code=404, detail="人设不存在")
    return doc


@router.put("/{person_id}")
async def upsert_person(person_id: str, req: PersonUpsertRequest):
    """手动编辑归并人设（按 name+company 生成的 person_id 幂等归并）。"""
    db = get_db()
    profile = dict(req.profile or {})
    if not str(profile.get("name") or "").strip():
        raise HTTPException(status_code=400, detail="人设档案缺少 name")
    existing = await persons_dao.get_person(db, person_id)
    if existing:
        profile = {**existing, **profile}
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
