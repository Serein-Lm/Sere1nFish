"""永久来源文档与 Target 聚类查询 API。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import User, get_current_active_user
from api.dao import target_research as target_research_dao
from api.dao import source_documents as source_dao
from api.dao import targets as targets_dao
from api.db.mongodb import get_db
from api.services.source_documents import get_source_document_detail
from api.services.targets import (
    assign_project_target_batches,
    get_project_target_summary,
    get_project_target_dashboard,
    list_project_target_branch,
    list_project_target_batches,
    list_project_target_options,
    list_project_target_summaries,
    list_project_target_summary_page,
    resolve_target,
)


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


class TargetResearchRequest(BaseModel):
    project_id: str = Field(min_length=1)
    scan_discovered_targets: bool = True
    rescan_root: bool = True
    max_related_targets: int = Field(default=8, ge=1, le=12)
    force_refresh: bool = True
    scan_params: dict = Field(default_factory=dict)


class TargetResearchBatchRequest(BaseModel):
    project_id: str = Field(min_length=1)
    target_names: list[str] = Field(min_length=1, max_length=100)
    concurrency: int = Field(default=4, ge=1, le=8)
    scan_discovered_targets: bool = True
    rescan_root: bool = True
    max_related_targets: int = Field(default=4, ge=1, le=12)
    force_refresh: bool = True
    scan_params: dict = Field(default_factory=dict)


class ProjectTargetBatchAssignRequest(BaseModel):
    project_id: str = Field(min_length=1)
    target_ids: list[str] = Field(min_length=1, max_length=500)
    batch_tags: list[str] = Field(default_factory=list, max_length=12)
    operation: Literal["add", "remove", "replace"] = "add"
    include_descendants: bool = True


@router.get("/targets")
async def list_targets(
    project_id: str = Query(min_length=1),
    compact: bool = Query(default=False),
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    batch_tag: str = Query(default="", max_length=40),
):
    if page is not None or q.strip() or batch_tag.strip():
        return await list_project_target_summary_page(
            get_db(),
            project_id,
            page=page or 1,
            page_size=page_size,
            query=q,
            batch_tag=batch_tag,
        )
    items = await list_project_target_summaries(
        get_db(),
        project_id,
        compact=compact,
    )
    return {"items": items, "total": len(items)}


@router.get("/targets/options")
async def list_target_options(project_id: str = Query(min_length=1)):
    items = await list_project_target_options(get_db(), project_id)
    return {"items": items, "total": len(items)}


@router.get("/targets/batches")
async def list_target_batches(project_id: str = Query(min_length=1)):
    items = await list_project_target_batches(get_db(), project_id)
    return {"items": items, "total": len(items)}


@router.post("/targets/batches/assign")
async def assign_target_batches(payload: ProjectTargetBatchAssignRequest):
    try:
        return await assign_project_target_batches(
            get_db(),
            project_id=payload.project_id,
            target_ids=payload.target_ids,
            batch_tags=payload.batch_tags,
            operation=payload.operation,
            include_descendants=payload.include_descendants,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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
            clear_relation=True,
        )
    return target


@router.post("/targets/research-batch")
async def create_target_research_batch(
    payload: TargetResearchBatchRequest,
    current_user: User = Depends(get_current_active_user),
):
    from api.services.target_research import (
        TargetResearchTargetNotFoundError,
        enqueue_target_research_batch,
    )

    try:
        return await enqueue_target_research_batch(
            get_db(),
            project_id=payload.project_id,
            target_names=payload.target_names,
            requested_by=current_user.username,
            concurrency=payload.concurrency,
            scan_discovered_targets=payload.scan_discovered_targets,
            rescan_root=payload.rescan_root,
            max_related_targets=payload.max_related_targets,
            force_refresh=payload.force_refresh,
            scan_params=payload.scan_params,
        )
    except TargetResearchTargetNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/targets/{target_id}")
async def get_target(target_id: str):
    target = await targets_dao.get_target(get_db(), target_id)
    if not target:
        raise HTTPException(404, "Target 不存在")
    projects = await targets_dao.list_target_projects(get_db(), target_id)
    return {**target, "projects": projects}


@router.get("/targets/{target_id}/branch")
async def list_target_branch(target_id: str, project_id: str = Query(min_length=1)):
    result = await list_project_target_branch(get_db(), project_id, target_id)
    if not result["root_target_id"]:
        raise HTTPException(404, "项目 Target 不存在")
    return result


@router.get("/targets/{target_id}/summary")
async def get_target_summary(target_id: str, project_id: str = Query(min_length=1)):
    item = await get_project_target_summary(
        get_db(),
        project_id=project_id,
        target_id=target_id,
    )
    if item is None:
        raise HTTPException(404, "项目 Target 不存在")
    return {"item": item}


@router.get("/targets/{target_id}/dashboard")
async def get_target_dashboard(target_id: str, project_id: str = Query(min_length=1)):
    item = await get_project_target_dashboard(
        get_db(),
        project_id=project_id,
        target_id=target_id,
    )
    if item is None:
        raise HTTPException(404, "项目 Target 不存在")
    return item


@router.get("/targets/{target_id}/research")
async def get_target_research(target_id: str, project_id: str = ""):
    if not await targets_dao.get_target(get_db(), target_id):
        raise HTTPException(404, "Target 不存在")
    item = await target_research_dao.get_latest_research(
        get_db(), target_id=target_id, project_id=project_id
    )
    return {"item": item}


@router.post("/targets/{target_id}/research")
async def create_target_research(
    target_id: str,
    payload: TargetResearchRequest,
    current_user: User = Depends(get_current_active_user),
):
    from api.services.target_research import (
        TargetResearchTargetNotFoundError,
        enqueue_target_research,
    )

    try:
        return await enqueue_target_research(
            get_db(),
            project_id=payload.project_id,
            target_id=target_id,
            requested_by=current_user.username,
            scan_discovered_targets=payload.scan_discovered_targets,
            rescan_root=payload.rescan_root,
            max_related_targets=payload.max_related_targets,
            force_refresh=payload.force_refresh,
            scan_params=payload.scan_params,
        )
    except TargetResearchTargetNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


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
        clear_relation=True,
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
