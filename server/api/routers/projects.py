from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends

from api.auth import get_current_active_user
from api.db.mongodb import init_mongo, get_db
from api.models.projects import (
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    ProjectAppendRequest,
    WebTaggingCreateRequest,
    WebTaggingResultOut,
    CompanyTaggingRequest,
)
from api.utils.json_extract import extract_json_object
from api.services.company_url import guess_url_from_company_name
from api.dao import projects as projects_dao
from api.dao import web_tagging as web_dao
from api.schemas.pagination import PageResponse, ProjectListRequest, WebTaggingListRequest


router = APIRouter(dependencies=[Depends(get_current_active_user)])

init_mongo()


def _project_out(doc: dict) -> ProjectOut:
    return ProjectOut(
        id=str(doc.get("_id")),
        name=doc.get("name"),
        description=doc.get("description"),
        target=doc.get("target"),
        contents=doc.get("contents") or [],
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
    )


def _tag_out(doc: dict) -> WebTaggingResultOut:
    raw = doc.get("data") or {}
    return WebTaggingResultOut(
        id=str(doc.get("_id")),
        project_id=str(doc.get("project_id")),
        url=doc.get("url"),
        task_id=doc.get("task_id", ""),
        created_at=doc.get("created_at"),
        data=raw,
    )


@router.post("", response_model=ProjectOut)
async def create_project(body: ProjectCreate):
    db = get_db()
    doc = await projects_dao.create_project(db, name=body.name, description=body.description)
    return _project_out(doc)


@router.post("/list")
async def list_projects(body: ProjectListRequest | None = None):
    """列出项目（分页）"""
    if body is None:
        body = ProjectListRequest()
    db = get_db()
    docs, total = await projects_dao.list_projects(db, limit=body.limit, skip=body.skip)
    return PageResponse.build(
        items=[_project_out(d) for d in docs],
        total=total,
        page=body.page,
        page_size=body.page_size,
    )


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str):
    db = get_db()
    doc = await projects_dao.get_project(db, project_id)
    if not doc:
        raise HTTPException(status_code=404, detail="项目不存在")
    return _project_out(doc)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(project_id: str, body: ProjectUpdate):
    db = get_db()

    patch: dict = {}
    if body.name is not None:
        patch["name"] = body.name
    if body.description is not None:
        patch["description"] = body.description

    if not patch:
        doc = await projects_dao.get_project(db, project_id)
        if not doc:
            raise HTTPException(status_code=404, detail="项目不存在")
        return _project_out(doc)

    doc = await projects_dao.update_project(db, project_id, patch=patch)
    if not doc:
        raise HTTPException(status_code=404, detail="项目不存在")
    return _project_out(doc)


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    """删除项目及其所有关联数据"""
    db = get_db()

    deleted = await projects_dao.delete_project(db, project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 级联删除所有关联数据
    from api.db.collections import (
        WEB_TAGS_COLLECTION, FINDINGS_COLLECTION, COPYWRITINGS_COLLECTION,
        PROFILES_COLLECTION, XHS_NOTES_COLLECTION, XHS_NOTE_DETAILS_COLLECTION,
        XHS_PROFILES_COLLECTION, XHS_SEARCH_TASKS_COLLECTION,
        DOUYIN_SEARCH_RESULTS_COLLECTION, DOUYIN_TAGGED_RESULTS_COLLECTION,
        DOUYIN_PROFILES_COLLECTION, TASKS_COLLECTION,
        URL_SCAN_TASKS_COLLECTION, URL_SCAN_RESULTS_COLLECTION,
        URL_SCAN_COPYWRITINGS_COLLECTION, COMPANY_SCAN_COLLECTION,
        PROFILE_COPYWRITINGS_COLLECTION,
        TOKEN_USAGE_RECORDS_COLLECTION,
        AUTO_CHAT_SESSIONS_COLLECTION,
        MOBILE_PROFILE_OBSERVATIONS_COLLECTION,
        SCHOLAR_ARTICLES_COLLECTION,
        SCHOLAR_CONTACTS_COLLECTION,
    )
    from api.dao import contact_profiles as contact_profiles_dao
    from api.dao import mobile_artifacts as mobile_artifacts_dao

    collections_to_clean = [
        TASKS_COLLECTION,
        FINDINGS_COLLECTION,
        COPYWRITINGS_COLLECTION,
        PROFILES_COLLECTION,
        WEB_TAGS_COLLECTION,
        XHS_NOTES_COLLECTION,
        XHS_NOTE_DETAILS_COLLECTION,
        XHS_PROFILES_COLLECTION,
        XHS_SEARCH_TASKS_COLLECTION,
        DOUYIN_SEARCH_RESULTS_COLLECTION,
        DOUYIN_TAGGED_RESULTS_COLLECTION,
        DOUYIN_PROFILES_COLLECTION,
        URL_SCAN_TASKS_COLLECTION,
        URL_SCAN_RESULTS_COLLECTION,
        URL_SCAN_COPYWRITINGS_COLLECTION,
        COMPANY_SCAN_COLLECTION,
        PROFILE_COPYWRITINGS_COLLECTION,
        TOKEN_USAGE_RECORDS_COLLECTION,
        AUTO_CHAT_SESSIONS_COLLECTION,
        MOBILE_PROFILE_OBSERVATIONS_COLLECTION,
        SCHOLAR_ARTICLES_COLLECTION,
        SCHOLAR_CONTACTS_COLLECTION,
        "task_logs",  # 观测层任务日志（按 project_id 级联清理）
    ]

    # 各集合级联删除相互独立，并行执行；单集合失败不影响其它
    results = await asyncio.gather(
        *(db[coll_name].delete_many({"project_id": project_id}) for coll_name in collections_to_clean),
        return_exceptions=True,
    )
    total_deleted = sum(
        r.deleted_count for r in results if not isinstance(r, BaseException)
    )

    mobile_cleanup, contact_cleanup = await asyncio.gather(
        mobile_artifacts_dao.delete_project_artifacts(db, project_id),
        contact_profiles_dao.delete_project_references(db, project_id),
    )
    total_deleted += (
        int(mobile_cleanup.get("screenshots_deleted") or 0)
        + int(mobile_cleanup.get("operations_deleted") or 0)
        + int(contact_cleanup.get("profiles_deleted") or 0)
    )

    # 同步清理 TokenTracker 内存中该项目的记录
    try:
        from Sere1nGraph.graph.observability import get_global_tracker
        tracker = get_global_tracker()
        tracker.evict_records(project_id=project_id)
        from core.observability import get_obs_logger
        get_obs_logger().evict_logs(project_id=project_id)
    except Exception:
        pass

    return {
        "ok": True,
        "deleted_records": total_deleted,
        "mobile_artifacts": mobile_cleanup,
        "contact_profiles": contact_cleanup,
    }


@router.post("/{project_id}/append", response_model=ProjectOut)
async def append_project_content(project_id: str, body: ProjectAppendRequest):
    db = get_db()

    doc = await projects_dao.append_project_content(
        db,
        project_id=project_id,
        content=body.content,
        target=body.target,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="项目不存在")

    return _project_out(doc)


@router.post("/web-tagging", response_model=WebTaggingResultOut)
async def create_web_tagging_record(body: WebTaggingCreateRequest):
    """调用浏览器打标 Agent（输入仅 url）并入库。"""
    db = get_db()

    project = await projects_dao.get_project(db, body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    from langchain_core.messages import HumanMessage
    from Sere1nGraph.graph.agents.factory import create_web_tagging_agent
    from Sere1nGraph.graph.agents.runtime import extract_with_retry
    from Sere1nGraph.graph.prompts.loader import load_prompt
    from api.services.runtime_config import get_runtime_app_config

    runtime_config = await get_runtime_app_config()
    agent = await create_web_tagging_agent(runtime_config, output_mode="silent", streaming=False)
    result = await agent({"messages": [HumanMessage(content=body.url)]})

    data = await extract_with_retry(result, runtime_config, system_prompt=load_prompt("web_tagging/web_tagging"))
    if not data:
        raise HTTPException(status_code=502, detail="Agent 未返回可解析的结构化内容")

    # 兜底注入 intro.url
    intro = data.get("intro")
    if isinstance(intro, dict):
        intro.setdefault("url", body.url)

    doc = await web_dao.insert_web_tagging_result(db, project_id=body.project_id, url=body.url, data=data)
    await projects_dao.touch_project(db, body.project_id)
    return _tag_out(doc)


@router.post("/company/web-tagging", response_model=WebTaggingResultOut)
async def create_company_web_tagging_record(body: CompanyTaggingRequest):
    """输入公司名，预处理为 URL 后调用 Web Tagging Agent 并入库。"""
    db = get_db()

    project = await projects_dao.get_project(db, body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    url = guess_url_from_company_name(body.company_name)
    if not url:
        raise HTTPException(status_code=400, detail="无法从 company_name 解析出 URL/域名，请直接提供 URL")

    return await create_web_tagging_record(WebTaggingCreateRequest(project_id=body.project_id, url=url))


@router.post("/{project_id}/web-tagging")
async def list_web_tagging(project_id: str, body: WebTaggingListRequest | None = None):
    """列出 Web Tagging 结果（分页）"""
    if body is None:
        body = WebTaggingListRequest(project_id=project_id)
    db = get_db()
    docs, total = await web_dao.list_web_tagging_results(db, project_id=project_id, limit=body.limit, skip=body.skip)
    return PageResponse.build(
        items=[_tag_out(d) for d in docs],
        total=total,
        page=body.page,
        page_size=body.page_size,
    )
