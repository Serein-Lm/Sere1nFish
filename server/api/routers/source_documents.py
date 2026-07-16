"""永久来源文档与 Target 聚类查询 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_current_active_user
from api.dao import source_documents as source_dao
from api.dao import targets as targets_dao
from api.db.mongodb import get_db
from api.services.source_documents import get_source_document_detail
from api.services.targets import list_project_target_summaries, resolve_target


router = APIRouter(dependencies=[Depends(get_current_active_user)])


class TargetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target_type: str = Field(default="company", max_length=50)
    root_domain: str = Field(default="", max_length=255)
    aliases: list[str] = Field(default_factory=list)
    project_id: str = ""


class ProjectTargetLinkRequest(BaseModel):
    project_id: str = Field(min_length=1)
    search_terms: list[str] = Field(default_factory=list)
    objectives: list[str] = Field(default_factory=list)
    task_def_id: str = ""


@router.get("/targets")
async def list_targets(project_id: str = Query(min_length=1)):
    items = await list_project_target_summaries(get_db(), project_id)
    return {"items": items, "total": len(items)}


@router.post("/targets")
async def create_target(payload: TargetCreateRequest):
    db = get_db()
    target = await resolve_target(
        db,
        target_name=payload.name,
        target_type=payload.target_type,
        root_domain=payload.root_domain,
        aliases=payload.aliases,
        source="api",
    )
    if not target:
        raise HTTPException(422, "无法创建 Target")
    if payload.project_id:
        await targets_dao.link_project_target(
            db,
            project_id=payload.project_id,
            target=target,
        )
    return target


@router.get("/targets/{target_id}")
async def get_target(target_id: str):
    target = await targets_dao.get_target(get_db(), target_id)
    if not target:
        raise HTTPException(404, "Target 不存在")
    projects = await targets_dao.list_target_projects(get_db(), target_id)
    return {**target, "projects": projects}


@router.post("/targets/{target_id}/projects")
async def link_target_project(target_id: str, payload: ProjectTargetLinkRequest):
    db = get_db()
    target = await targets_dao.get_target(db, target_id)
    if not target:
        raise HTTPException(404, "Target 不存在")
    return await targets_dao.link_project_target(
        db,
        project_id=payload.project_id,
        target=target,
        search_terms=payload.search_terms,
        objectives=payload.objectives,
        task_def_id=payload.task_def_id,
    )


@router.get("/targets/{target_id}/documents")
async def list_target_documents(
    target_id: str,
    project_id: str = "",
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
):
    if not await targets_dao.get_target(get_db(), target_id):
        raise HTTPException(404, "Target 不存在")
    items, total = await source_dao.list_target_documents(
        get_db(),
        target_id,
        project_id=project_id,
        skip=skip,
        limit=limit,
    )
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/source-documents/{document_id}")
async def get_source_document(
    document_id: str,
    project_id: str = "",
    version_id: str = "",
):
    detail = await get_source_document_detail(
        get_db(),
        document_id,
        project_id=project_id,
        version_id=version_id,
    )
    if not detail:
        raise HTTPException(404, "来源文档不存在")
    return detail
