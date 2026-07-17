"""
统一项目 API — 嵌套 RESTful 路由

/projects/{project_id}/tasks — 任务管理
/projects/{project_id}/findings — findings 查询
/findings/{finding_id} — finding 详情
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, UploadFile, File, Form, Query
from pydantic import BaseModel, Field

from api.auth import get_current_active_user
from api.db.mongodb import init_mongo, get_db
from api.dao import projects as projects_dao
from api.dao import findings as findings_dao
from api.schemas.pagination import (
    PageResponse,
    TaskListRequest,
    FindingsQueryRequest,
    ProjectNotesListRequest,
    ProjectProfilesListRequest,
)
from core.background import spawn_background
from core.logger import get_logger

logger = get_logger("project_api")

router = APIRouter(dependencies=[Depends(get_current_active_user)])

init_mongo()

TASKS_COLLECTION = "tasks"


# ═══════════════════════════════════════════
# Pipeline 分发器（原 tasks.py，已合并到此）
# ═══════════════════════════════════════════

async def _dispatch_url_scan(task_id: str, project_id: str, params: dict):
    from api.services.info_collection.tuning import get_collection_runtime_tuning
    from api.services.url_scan_pipeline import UrlScanPipeline
    from api.services.runtime_config import get_runtime_app_config

    url_content = params.get("url_text", "")
    urls = params.get("urls", [])
    if urls:
        url_content = "\n".join(urls)
    db = get_db()
    runtime_config = await get_runtime_app_config()
    tuning = (await get_collection_runtime_tuning()).with_overrides(
        url_probe_concurrency=params.get("url_probe_concurrency"),
        url_scan_concurrency=params.get("url_scan_concurrency"),
        copywriting_concurrency=params.get("copywriting_concurrency"),
    )
    pipeline = UrlScanPipeline(db, runtime_config)
    result = await pipeline.run_pipeline(
        task_id=task_id, project_id=project_id, url_content=url_content,
        min_attention_score=params.get("min_attention_score", 40),
        probe_concurrency=tuning.url_probe_concurrency,
        scan_concurrency=tuning.url_scan_concurrency,
        copywriting_concurrency=tuning.copywriting_concurrency,
        enable_copywriting=params.get("enable_copywriting", True),
    )
    if result.get("status") == "error":
        raise RuntimeError(str(result.get("error") or "URL 扫描失败"))

async def _dispatch_xhs_search(task_id: str, project_id: str, params: dict):
    from api.services.xhs_pipeline import run_xhs_pipeline
    from api.services.runtime_config import get_runtime_app_config

    db = get_db()
    runtime_config = await get_runtime_app_config()
    await run_xhs_pipeline(
        db=db, app_config=runtime_config, task_id=task_id, project_id=project_id,
        keyword=params.get("keyword", ""), max_notes=params.get("max_notes", 20),
        attention_threshold=params.get("attention_threshold", 60),
    )

async def _dispatch_douyin_search(task_id: str, project_id: str, params: dict):
    from api.services.douyin_pipeline import run_douyin_pipeline
    from api.services.runtime_config import get_runtime_app_config

    db = get_db()
    runtime_config = await get_runtime_app_config()
    await run_douyin_pipeline(
        db=db, app_config=runtime_config, project_id=project_id,
        keyword=params.get("keyword", ""), max_videos=params.get("max_videos", 20),
        publish_time=params.get("publish_time", 0), task_id=task_id,
    )

async def _dispatch_web_tagging(task_id: str, project_id: str, params: dict):
    from api.services.web_tagging_pipeline import run_web_tagging_pipeline
    from api.services.runtime_config import get_runtime_app_config

    db = get_db()
    runtime_config = await get_runtime_app_config()
    await run_web_tagging_pipeline(
        db=db, app_config=runtime_config, project_id=project_id,
        company_name=params.get("company_name", ""), max_urls=params.get("max_urls", 50),
        max_tagging_urls=params.get("max_tagging_urls", 10), task_id=task_id,
    )

async def _dispatch_company_scan(task_id: str, project_id: str, params: dict):
    from api.services.company_scan_pipeline import CompanyScanPipeline
    from api.services.info_collection.tuning import get_collection_runtime_tuning
    from api.services.runtime_config import get_runtime_app_config

    db = get_db()
    runtime_config = await get_runtime_app_config()
    tuning = (await get_collection_runtime_tuning()).with_overrides(
        asset_probe_concurrency=params.get("asset_probe_concurrency"),
        url_probe_concurrency=params.get("url_probe_concurrency"),
        url_scan_concurrency=params.get("url_scan_concurrency"),
        copywriting_concurrency=params.get("copywriting_concurrency"),
        xhs_search_concurrency=params.get("xhs_search_concurrency"),
    )
    pipeline = CompanyScanPipeline(db, runtime_config)
    await pipeline.run_pipeline(
        task_id=task_id, project_id=project_id,
        company_name=params.get("company_name", ""),
        url_text=params.get("url_text", ""), urls=params.get("urls", []),
        enable_url_scan=params.get("enable_url_scan", True),
        enable_asset_discovery=params.get("enable_asset_discovery", True),
        enable_xhs=params.get("enable_xhs", True),
        enable_copywriting=params.get("enable_copywriting", True),
        xhs_max_notes=params.get("xhs_max_notes") or params.get("max_notes", 20),
        xhs_attention_threshold=params.get("xhs_attention_threshold") or params.get("attention_threshold", 60),
        min_attention_score=params.get("min_attention_score", 40),
        profile_copywriting_threshold=params.get("profile_copywriting_threshold", 60),
        fofa_size=params.get("fofa_size", 200),
        hunter_size=params.get("hunter_size", 200),
        asset_probe_concurrency=tuning.asset_probe_concurrency,
        incremental_scan=params.get("incremental_scan", False),
        url_probe_concurrency=tuning.url_probe_concurrency,
        url_scan_concurrency=tuning.url_scan_concurrency,
        copywriting_concurrency=tuning.copywriting_concurrency,
        xhs_search_concurrency=tuning.xhs_search_concurrency,
    )

async def _dispatch_fofa_collect(task_id: str, project_id: str, params: dict):
    from api.services.fofa_collect import run_fofa_collect
    from api.services.info_collection.tuning import get_collection_runtime_tuning
    from api.services.runtime_config import get_runtime_app_config

    db = get_db()
    runtime_config = await get_runtime_app_config()
    tuning = (await get_collection_runtime_tuning()).with_overrides(
        asset_probe_concurrency=params.get("probe_concurrency"),
        url_probe_concurrency=params.get("url_probe_concurrency"),
        url_scan_concurrency=params.get("url_scan_concurrency"),
        copywriting_concurrency=params.get("copywriting_concurrency"),
    )
    await run_fofa_collect(
        db=db, app_config=runtime_config, task_id=task_id, project_id=project_id,
        company_name=params.get("company_name", ""),
        fofa_size=params.get("fofa_size", 200),
        hunter_size=params.get("hunter_size", 200),
        enable_scan=params.get("enable_scan", True),
        min_attention_score=params.get("min_attention_score", 40),
        probe_concurrency=tuning.asset_probe_concurrency,
        incremental_scan=params.get("incremental_scan", False),
        url_probe_concurrency=tuning.url_probe_concurrency,
        url_scan_concurrency=tuning.url_scan_concurrency,
        copywriting_concurrency=tuning.copywriting_concurrency,
    )

async def _dispatch_scholar_contact(task_id: str, project_id: str, params: dict):
    from api.services.scholar_contact_pipeline import run_scholar_contact_collect
    from api.services.runtime_config import get_runtime_app_config

    db = get_db()
    runtime_config = await get_runtime_app_config()
    await run_scholar_contact_collect(
        db, runtime_config, task_id=task_id, project_id=project_id,
        unit=params.get("unit", ""), direction=params.get("direction", ""),
        unit_en=params.get("unit_en", ""), limit=params.get("limit", 10),
        enable_chrome_pmc=params.get("enable_chrome_pmc", False),
        dry_run=params.get("dry_run", False),
        bulk=params.get("bulk", False),
        max_articles=params.get("max_articles", 2000),
    )

async def _dispatch_mobile_collect(task_id: str, project_id: str, params: dict):
    from api.services.mobile_collect_pipeline import _dispatch_mobile_collect as _run

    await _run(task_id, project_id, params)

TASK_DISPATCHERS: dict[str, Any] = {
    "url_scan": _dispatch_url_scan,
    "xhs_search": _dispatch_xhs_search,
    "douyin_search": _dispatch_douyin_search,
    "web_tagging": _dispatch_web_tagging,
    "company_scan": _dispatch_company_scan,
    "fofa_collect": _dispatch_fofa_collect,
    "scholar_contact": _dispatch_scholar_contact,
    "mobile_collect": _dispatch_mobile_collect,
}

async def _execute_task(task_id: str, project_id: str, task_type: str, params: dict):
    """统一执行入口"""
    import time as _time
    from Sere1nGraph.graph.observability import get_global_tracker
    from core.observability import obs_log

    logger.notice(f"🚀 任务启动 | task={task_id} type={task_type} project={project_id}")
    obs_log(
        "任务启动", task_id=task_id, project_id=project_id, source="task_runner",
        level="notice", event="task_start", data={"task_type": task_type},
    )
    db = get_db()
    tracker = get_global_tracker()
    tracker.push_context(project_id=project_id, task_id=task_id, turn_id=task_id, task_type=task_type)
    t0 = _time.time()
    try:
        await db[TASKS_COLLECTION].update_one(
            {"task_id": task_id},
            {"$set": {"status": "running", "started_at": datetime.now(), "updated_at": datetime.now()}},
        )
        dispatcher = TASK_DISPATCHERS[task_type]
        await dispatcher(task_id, project_id, params)
        elapsed = _time.time() - t0
        await db[TASKS_COLLECTION].update_one(
            {"task_id": task_id},
            {"$set": {"status": "completed", "elapsed_ms": round(elapsed * 1000), "updated_at": datetime.now()}},
        )
        obs_log(
            f"任务完成 ({elapsed:.1f}s)", task_id=task_id, project_id=project_id,
            source="task_runner", level="notice", event="task_done",
            data={"task_type": task_type, "elapsed_ms": round(elapsed * 1000)},
        )
        logger.notice(f"✅ 任务完成 | task={task_id} ({elapsed:.1f}s)")
    except Exception as e:
        elapsed = _time.time() - t0
        await db[TASKS_COLLECTION].update_one(
            {"task_id": task_id},
            {"$set": {"status": "error", "error": str(e), "elapsed_ms": round(elapsed * 1000), "updated_at": datetime.now()}},
        )
        obs_log(
            f"任务失败: {e}", task_id=task_id, project_id=project_id,
            source="task_runner", level="error", event="task_error",
            data={"task_type": task_type, "error": str(e), "elapsed_ms": round(elapsed * 1000)},
        )
        logger.notice(f"❌ 任务失败 | task={task_id}: {e}")
    finally:
        tracker.pop_context()


# ═══════════════════════════════════════════
# 项目下的任务
# ═══════════════════════════════════════════

class TaskCreateRequest(BaseModel):
    task_type: str = Field(description="任务类型")
    params: dict[str, Any] = Field(default_factory=dict)


@router.get("/projects/{project_id}/assets")
async def list_project_assets(
    project_id: str,
    target_id: str = "",
    root_domain: str = "",
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict[str, Any]:
    """查询项目下已去重的 FOFA/Hunter 资产及最新存活状态。"""
    from api.dao import fofa_assets as assets_dao

    db = get_db()
    if not await projects_dao.get_project(db, project_id):
        raise HTTPException(404, "项目不存在")
    items = await assets_dao.query_assets(
        db,
        project_id,
        root_domain=root_domain,
        target_id=target_id,
        limit=limit,
    )
    total = await assets_dao.count_assets(
        db,
        project_id,
        root_domain=root_domain,
        target_id=target_id,
    )
    return {"items": items, "total": total}


@router.post("/projects/{project_id}/tasks")
async def create_task(project_id: str, req: TaskCreateRequest, background_tasks: BackgroundTasks):
    """下发任务（嵌套在项目下）"""
    dispatcher = TASK_DISPATCHERS.get(req.task_type)
    if not dispatcher:
        raise HTTPException(400, f"不支持的 task_type: {req.task_type}")

    db = get_db()
    # 验证项目存在
    project = await projects_dao.get_project(db, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    task_id = uuid.uuid4().hex[:12]
    task_doc = {
        "task_id": task_id,
        "project_id": project_id,
        "task_type": req.task_type,
        "params": req.params,
        "status": "pending",
        "progress": {},
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }
    await db[TASKS_COLLECTION].insert_one(task_doc)

    spawn_background(
        _execute_task(task_id, project_id, req.task_type, req.params),
        name=f"task:{task_id}",
    )

    return {"task_id": task_id, "task_type": req.task_type, "status": "pending"}


@router.post("/projects/{project_id}/tasks/upload")
async def create_task_with_file(
    project_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    task_type: str = Form(...),
    params_json: str = Form(default="{}"),
):
    """带文件上传的任务下发"""
    import json

    dispatcher = TASK_DISPATCHERS.get(task_type)
    if not dispatcher:
        raise HTTPException(400, f"不支持的 task_type: {task_type}")

    db = get_db()
    project = await projects_dao.get_project(db, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    content = await file.read()
    file_text = content.decode("utf-8", errors="ignore")

    try:
        params = json.loads(params_json) if params_json.strip() else {}
    except json.JSONDecodeError:
        params = {}

    FILE_FIELD_MAP = {"url_scan": "url_text", "company_scan": "url_text"}
    field_name = FILE_FIELD_MAP.get(task_type)
    if field_name:
        params[field_name] = file_text

    task_id = uuid.uuid4().hex[:12]
    task_doc = {
        "task_id": task_id,
        "project_id": project_id,
        "task_type": task_type,
        "params": params,
        "status": "pending",
        "progress": {},
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }
    await db[TASKS_COLLECTION].insert_one(task_doc)

    spawn_background(
        _execute_task(task_id, project_id, task_type, params),
        name=f"task:{task_id}",
    )

    return {"task_id": task_id, "task_type": task_type, "status": "pending"}


@router.post("/projects/{project_id}/tasks/list")
async def list_tasks(project_id: str, body: TaskListRequest | None = None):
    """列出项目下的任务（分页）"""
    if body is None:
        body = TaskListRequest(project_id=project_id)
    db = get_db()
    query: dict = {"project_id": project_id}
    if body.task_type:
        query["task_type"] = body.task_type
    total = await db[TASKS_COLLECTION].count_documents(query)
    cursor = db[TASKS_COLLECTION].find(query, {"_id": 0}).sort("created_at", -1).skip(body.skip).limit(body.limit)
    tasks = await cursor.to_list(body.limit)
    return PageResponse.build(
        items=tasks,
        total=total,
        page=body.page,
        page_size=body.page_size,
    )


@router.get("/projects/{project_id}/tasks/{task_id}")
async def get_task(project_id: str, task_id: str):
    """获取任务状态"""
    db = get_db()
    task = await db[TASKS_COLLECTION].find_one(
        {"task_id": task_id, "project_id": project_id}, {"_id": 0}
    )
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.delete("/projects/{project_id}/tasks/{task_id}")
async def delete_task(project_id: str, task_id: str):
    """删除任务及关联数据"""
    db = get_db()
    task = await db[TASKS_COLLECTION].find_one({"task_id": task_id, "project_id": project_id})
    if not task:
        raise HTTPException(404, "任务不存在")

    deleted_findings = await findings_dao.delete_findings_by_task(db, task_id)
    deleted_cw = await findings_dao.delete_copywritings_by_task(db, task_id)
    await db[TASKS_COLLECTION].delete_one({"task_id": task_id})

    # 清理该任务的 token 消耗记录 + 观测日志
    from api.db.collections import TOKEN_USAGE_RECORDS_COLLECTION
    from api.dao import task_logs as task_logs_dao
    await db[TOKEN_USAGE_RECORDS_COLLECTION].delete_many({"task_id": task_id})
    await task_logs_dao.delete_logs_by_task(db, task_id)
    try:
        from Sere1nGraph.graph.observability import get_global_tracker
        get_global_tracker().evict_records(task_id=task_id)
        from core.observability import get_obs_logger
        get_obs_logger().evict_logs(task_id=task_id)
    except Exception:
        pass

    return {"task_id": task_id, "deleted": True, "deleted_findings": deleted_findings, "deleted_copywritings": deleted_cw}


@router.delete("/projects/{project_id}/tasks")
async def batch_delete_tasks(project_id: str, status: str = ""):
    """批量删除任务"""
    db = get_db()
    query: dict = {"project_id": project_id}
    if status:
        query["status"] = status

    cursor = db[TASKS_COLLECTION].find(query, {"task_id": 1})
    tasks = await cursor.to_list(500)
    task_ids = [t["task_id"] for t in tasks]

    if not task_ids:
        return {"deleted_count": 0, "task_ids": []}

    # 批量 $in 删除，消除按任务逐条删除的 N+1
    deleted_findings, deleted_cw = await asyncio.gather(
        findings_dao.delete_findings_by_tasks(db, task_ids),
        findings_dao.delete_copywritings_by_tasks(db, task_ids),
    )

    result = await db[TASKS_COLLECTION].delete_many({"task_id": {"$in": task_ids}})

    # 清理这些任务的 token 消耗记录 + 观测日志
    from api.db.collections import TOKEN_USAGE_RECORDS_COLLECTION
    from api.dao import task_logs as task_logs_dao
    await db[TOKEN_USAGE_RECORDS_COLLECTION].delete_many({"task_id": {"$in": task_ids}})
    await task_logs_dao.delete_logs_by_tasks(db, task_ids)
    try:
        from Sere1nGraph.graph.observability import get_global_tracker
        tracker = get_global_tracker()
        for tid in task_ids:
            tracker.evict_records(task_id=tid)
        from core.observability import get_obs_logger
        get_obs_logger().evict_logs(task_ids=task_ids)
    except Exception:
        pass

    return {
        "deleted_count": result.deleted_count,
        "task_ids": task_ids,
        "deleted_findings": deleted_findings,
        "deleted_copywritings": deleted_cw,
    }


# ═══════════════════════════════════════════
# 项目下的 Findings
# ═══════════════════════════════════════════

@router.get("/projects/{project_id}/findings/summary")
async def get_findings_summary(project_id: str):
    """项目 findings 总览看板"""
    db = get_db()
    summary = await findings_dao.get_findings_summary(db, project_id)

    # 附加任务数
    tasks_count = await db[TASKS_COLLECTION].count_documents({"project_id": project_id})
    summary["tasks_count"] = tasks_count

    # 附加无风险 URL 数
    safe_count = await db["url_scan_results"].count_documents(
        {"project_id": project_id, "success": True, "has_findings": False}
    )
    summary["safe_count"] = safe_count

    return summary


@router.post("/projects/{project_id}/findings")
async def query_findings(project_id: str, body: FindingsQueryRequest | None = None):
    """分页查询 findings"""
    if body is None:
        body = FindingsQueryRequest(project_id=project_id)
    db = get_db()
    findings, total = await findings_dao.query_findings(
        db, project_id,
        source=body.source, task_id=body.task_id, finding_type=body.type,
        min_score=body.min_score, sort=body.sort, limit=body.limit, skip=body.skip,
    )

    response = PageResponse.build(
        items=findings,
        total=total,
        page=body.page,
        page_size=body.page_size,
    )

    if body.include_safe:
        cursor = db["url_scan_results"].find(
            {"project_id": project_id, "success": True, "has_findings": False},
            {"url": 1, "_id": 0},
        )
        safe_docs = await cursor.to_list(500)
        response["safe_urls"] = sorted(d["url"] for d in safe_docs)
        response["safe_count"] = len(safe_docs)

    return response


# ═══════════════════════════════════════════
# Finding 详情（顶层路由，finding_id 全局唯一）
# ═══════════════════════════════════════════

@router.get("/findings/{finding_id}")
async def get_finding(finding_id: str):
    """获取 finding 详情"""
    db = get_db()
    finding = await findings_dao.get_finding(db, finding_id)
    if not finding:
        raise HTTPException(404, "Finding 不存在")
    return finding


@router.get("/findings/{finding_id}/copywriting")
async def get_finding_copywriting(finding_id: str):
    """获取 finding 的话术"""
    db = get_db()
    cw = await findings_dao.get_copywriting(db, finding_id)
    if not cw:
        raise HTTPException(404, "话术不存在")
    return cw


@router.get("/findings/{finding_id}/profile")
async def get_finding_profile(finding_id: str):
    """获取 finding 的人物画像"""
    db = get_db()
    profile = await findings_dao.get_profile(db, finding_id)
    if not profile:
        raise HTTPException(404, "画像不存在")
    return profile


@router.get("/findings/{finding_id}/notes")
async def get_finding_notes(finding_id: str):
    """获取 finding 关联的笔记"""
    db = get_db()
    finding = await findings_dao.get_finding(db, finding_id)
    if not finding:
        raise HTTPException(404, "Finding 不存在")

    note_ids = finding.get("xhs_note_ids") or finding.get("note_ids") or []
    if not note_ids:
        return {"notes": []}

    from api.db.collections import XHS_NOTES_COLLECTION
    cursor = db[XHS_NOTES_COLLECTION].find(
        {"note_id": {"$in": note_ids}}, {"_id": 0}
    )
    notes = await cursor.to_list(100)
    return {"notes": notes}


# ═══════════════════════════════════════════
# 画像 → Finding → 话术 关联查询
# ═══════════════════════════════════════════

@router.get("/profiles/xhs/{user_id}/finding")
async def get_xhs_profile_finding(user_id: str, project_id: str = ""):
    """
    通过小红书 user_id 查关联的 finding。

    优先从画像表的 finding_id 字段直接查，fallback 到 findings 表反查。
    """
    db = get_db()
    from api.db.collections import XHS_PROFILES_COLLECTION

    # 优先：画像表直接有 finding_id
    pq: dict[str, Any] = {"user_id": user_id}
    if project_id:
        pq["project_id"] = project_id
    profile = await db[XHS_PROFILES_COLLECTION].find_one(pq, {"finding_id": 1, "_id": 0})
    fid = (profile or {}).get("finding_id")

    if fid:
        finding = await findings_dao.get_finding(db, fid)
        if finding:
            from api.db.collections import COPYWRITINGS_COLLECTION
            cw = await db[COPYWRITINGS_COLLECTION].find_one({"finding_id": fid}, {"_id": 0, "finding_id": 1})
            finding["has_copywriting"] = cw is not None
            return finding

    # fallback：从 findings 表按 xhs_user_id 反查
    query: dict[str, Any] = {"xhs_user_id": user_id, "source": "xhs"}
    if project_id:
        query["project_id"] = project_id
    cursor = db["findings"].find(query, {"_id": 0}).sort("attention_score", -1).limit(1)
    docs = await cursor.to_list(1)
    if not docs:
        raise HTTPException(404, f"未找到 user_id={user_id} 关联的 finding")

    finding = docs[0]
    from api.db.collections import COPYWRITINGS_COLLECTION
    cw = await db[COPYWRITINGS_COLLECTION].find_one({"finding_id": finding.get("finding_id", "")}, {"_id": 0, "finding_id": 1})
    finding["has_copywriting"] = cw is not None
    return finding


@router.get("/profiles/douyin/{sec_uid}/finding")
async def get_douyin_profile_finding(sec_uid: str, project_id: str = ""):
    """
    通过抖音 sec_uid 查关联的 finding。

    优先从画像表的 finding_id 字段直接查，fallback 到 findings 表反查。
    """
    db = get_db()
    from api.db.collections import DOUYIN_PROFILES_COLLECTION

    pq: dict[str, Any] = {"sec_uid": sec_uid}
    if project_id:
        pq["project_id"] = project_id
    profile = await db[DOUYIN_PROFILES_COLLECTION].find_one(pq, {"finding_id": 1, "_id": 0})
    fid = (profile or {}).get("finding_id")

    if fid:
        finding = await findings_dao.get_finding(db, fid)
        if finding:
            from api.db.collections import COPYWRITINGS_COLLECTION
            cw = await db[COPYWRITINGS_COLLECTION].find_one({"finding_id": fid}, {"_id": 0, "finding_id": 1})
            finding["has_copywriting"] = cw is not None
            return finding

    # fallback
    query: dict[str, Any] = {"douyin_sec_uid": sec_uid, "source": "douyin"}
    if project_id:
        query["project_id"] = project_id
    cursor = db["findings"].find(query, {"_id": 0}).sort("attention_score", -1).limit(1)
    docs = await cursor.to_list(1)
    if not docs:
        raise HTTPException(404, f"未找到 sec_uid={sec_uid} 关联的 finding")

    finding = docs[0]
    from api.db.collections import COPYWRITINGS_COLLECTION
    cw = await db[COPYWRITINGS_COLLECTION].find_one({"finding_id": finding.get("finding_id", "")}, {"_id": 0, "finding_id": 1})
    finding["has_copywriting"] = cw is not None
    return finding


@router.get("/profiles/{user_id}/copywriting")
async def get_profile_copywriting(user_id: str, source: str = "xhs", project_id: str = ""):
    """
    通过 user_id（xhs）或 sec_uid（douyin）直接查话术。

    内部流程：画像表 finding_id → copywriting，一步到位。
    """
    db = get_db()

    # 先从画像表拿 finding_id
    if source == "xhs":
        from api.db.collections import XHS_PROFILES_COLLECTION
        coll = XHS_PROFILES_COLLECTION
        pq: dict[str, Any] = {"user_id": user_id}
    else:
        from api.db.collections import DOUYIN_PROFILES_COLLECTION
        coll = DOUYIN_PROFILES_COLLECTION
        pq = {"sec_uid": user_id}

    if project_id:
        pq["project_id"] = project_id

    profile = await db[coll].find_one(pq, {"finding_id": 1, "_id": 0})
    fid = (profile or {}).get("finding_id")

    if not fid:
        # fallback: 从 findings 表反查
        if source == "xhs":
            fq: dict[str, Any] = {"xhs_user_id": user_id, "source": "xhs"}
        else:
            fq = {"douyin_sec_uid": user_id, "source": "douyin"}
        if project_id:
            fq["project_id"] = project_id
        cursor = db["findings"].find(fq, {"finding_id": 1, "_id": 0}).sort("attention_score", -1).limit(1)
        docs = await cursor.to_list(1)
        if docs:
            fid = docs[0].get("finding_id")

    if not fid:
        raise HTTPException(404, f"未找到 user_id={user_id} 关联的 finding")

    cw = await findings_dao.get_copywriting(db, fid)
    if not cw:
        raise HTTPException(404, f"finding_id={fid} 的话术不存在，可调用 POST /findings/{fid}/generate-copywriting 生成")
    return cw


# ═══════════════════════════════════════════
# 项目下的原始数据列表（notes / profiles / web-tagging）
# ═══════════════════════════════════════════

@router.post("/projects/{project_id}/notes")
async def list_project_notes(project_id: str, body: ProjectNotesListRequest | None = None):
    """项目下的小红书笔记列表（分页）"""
    if body is None:
        body = ProjectNotesListRequest(project_id=project_id)
    db = get_db()
    from api.dao import xhs as xhs_dao
    docs, total = await xhs_dao.list_notes(
        db, project_id=project_id, task_id=body.task_id or None,
        is_suspicious=body.is_suspicious, limit=body.limit, skip=body.skip, sort_by=body.sort_by,
    )
    # 去掉 _id
    for d in docs:
        d.pop("_id", None)
    return PageResponse.build(
        items=docs,
        total=total,
        page=body.page,
        page_size=body.page_size,
    )


@router.post("/projects/{project_id}/profiles")
async def list_project_profiles(project_id: str, body: ProjectProfilesListRequest | None = None):
    """项目下的人物画像列表（分页）"""
    if body is None:
        body = ProjectProfilesListRequest(project_id=project_id)
    db = get_db()
    from api.db.collections import XHS_PROFILES_COLLECTION
    query: dict = {"project_id": project_id}
    if body.min_score > 0:
        query["attention_score"] = {"$gte": body.min_score}

    sort_spec = [("attention_score", -1)] if body.sort == "score_desc" else [("updated_at", -1)]
    total = await db[XHS_PROFILES_COLLECTION].count_documents(query)
    cursor = db[XHS_PROFILES_COLLECTION].find(query, {"_id": 0}).sort(sort_spec).skip(body.skip).limit(body.limit)
    profiles = await cursor.to_list(body.limit)
    return PageResponse.build(
        items=profiles,
        total=total,
        page=body.page,
        page_size=body.page_size,
    )


# ═══════════════════════════════════════════
# 项目看板 + 聚合 API
# ═══════════════════════════════════════════


def _ensure_tracker_db(tracker):
    """确保旧 stats 路由也能读取 MongoDB 中的历史 token 数据。"""
    if getattr(tracker, "_db", None) is not None:
        return
    try:
        tracker.set_db(get_db())
    except Exception:
        return


@router.get("/projects/{project_id}/dashboard")
async def get_project_dashboard(project_id: str):
    """
    项目综合看板 — 一次请求拿到所有看板数据。

    聚合：findings 统计 + 任务统计 + 各数据源计数 + 高分 Top10 + token 消耗。
    聚合逻辑收敛在 api.services.analytics，供 router 与 AI 中枢分析工具复用。
    """
    from api.services import analytics

    return await analytics.resolve_project_dashboard(get_db(), project_id)


@router.get("/projects/{project_id}/timeline")
async def get_project_timeline(project_id: str, limit: int = 50):
    """
    项目时间线 — 按时间倒序展示所有活动（tasks/findings/notes/profiles）。
    """
    db = get_db()
    from api.db.collections import XHS_NOTES_COLLECTION, XHS_PROFILES_COLLECTION

    pid = {"project_id": project_id}
    # 四个数据源相互独立，并行拉取后在内存合并
    tasks_docs, findings_docs, notes_docs, profiles_docs = await asyncio.gather(
        db[TASKS_COLLECTION].find(
            pid,
            {"_id": 0, "task_id": 1, "task_type": 1, "status": 1, "created_at": 1, "updated_at": 1},
        ).sort("created_at", -1).limit(limit).to_list(limit),
        db["findings"].find(
            pid,
            {"_id": 0, "finding_id": 1, "source": 1, "type": 1, "label": 1, "attention_score": 1, "created_at": 1},
        ).sort("created_at", -1).limit(limit).to_list(limit),
        db[XHS_NOTES_COLLECTION].find(
            pid, {"_id": 0, "note_id": 1, "title": 1, "created_at": 1},
        ).sort("created_at", -1).limit(limit).to_list(limit),
        db[XHS_PROFILES_COLLECTION].find(
            pid, {"_id": 0, "user_id": 1, "nickname": 1, "attention_score": 1, "created_at": 1},
        ).sort("created_at", -1).limit(limit).to_list(limit),
    )

    events: list[dict] = []
    for doc in tasks_docs:
        events.append({"type": "task", "id": doc["task_id"], "label": f"{doc.get('task_type','')} 任务",
                        "status": doc.get("status"), "time": doc.get("updated_at") or doc.get("created_at")})
    for doc in findings_docs:
        events.append({"type": "finding", "id": doc["finding_id"], "label": doc.get("label", ""),
                        "source": doc.get("source"), "score": doc.get("attention_score", 0), "time": doc.get("created_at")})
    for doc in notes_docs:
        events.append({"type": "xhs_note", "id": doc.get("note_id"), "label": doc.get("title", ""), "time": doc.get("created_at")})
    for doc in profiles_docs:
        events.append({"type": "xhs_profile", "id": doc.get("user_id"), "label": doc.get("nickname", ""),
                        "score": doc.get("attention_score", 0), "time": doc.get("created_at")})

    events.sort(key=lambda e: e.get("time") or datetime.min, reverse=True)
    return {"events": events[:limit]}


@router.get("/projects/{project_id}/score-distribution")
async def get_score_distribution(project_id: str, source: str = ""):
    """关注度分数分布 — 按 10 分一档统计 findings 数量，用于直方图。"""
    db = get_db()
    match: dict[str, Any] = {"project_id": project_id}
    if source:
        match["source"] = source

    pipeline = [
        {"$match": match},
        {"$bucket": {
            "groupBy": "$attention_score",
            "boundaries": list(range(0, 101, 10)),
            "default": 100,
            "output": {"count": {"$sum": 1}},
        }},
    ]
    result = await db["findings"].aggregate(pipeline).to_list(20)
    return {"bins": [{"min": doc["_id"], "count": doc["count"]} for doc in result], "source": source or "all"}


@router.get("/projects/{project_id}/source-breakdown")
async def get_source_breakdown(project_id: str):
    """数据源分布 — 各 source 的 findings 数量 + 平均分 + 最高分。"""
    db = get_db()
    pipeline = [
        {"$match": {"project_id": project_id}},
        {"$group": {
            "_id": "$source",
            "count": {"$sum": 1},
            "avg_score": {"$avg": "$attention_score"},
            "max_score": {"$max": "$attention_score"},
            "min_score": {"$min": "$attention_score"},
        }},
        {"$sort": {"count": -1}},
    ]
    result = await db["findings"].aggregate(pipeline).to_list(20)
    return {"sources": [
        {"source": d["_id"], "count": d["count"],
         "avg_score": round(d.get("avg_score", 0) or 0, 1),
         "max_score": d.get("max_score", 0), "min_score": d.get("min_score", 0)}
        for d in result
    ]}


@router.get("/projects/{project_id}/type-breakdown")
async def get_type_breakdown(project_id: str, source: str = ""):
    """发现类型分布 — 各 type 的数量 + 平均分，可按 source 过滤。"""
    db = get_db()
    match: dict[str, Any] = {"project_id": project_id}
    if source:
        match["source"] = source
    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$type", "count": {"$sum": 1}, "avg_score": {"$avg": "$attention_score"}, "max_score": {"$max": "$attention_score"}}},
        {"$sort": {"count": -1}},
    ]
    result = await db["findings"].aggregate(pipeline).to_list(50)
    return {"types": [
        {"type": d["_id"], "count": d["count"], "avg_score": round(d.get("avg_score", 0) or 0, 1), "max_score": d.get("max_score", 0)}
        for d in result
    ], "source": source or "all"}


@router.get("/projects/{project_id}/high-value-targets")
async def get_high_value_targets(project_id: str, min_score: int = 60, limit: int = 20):
    """高价值目标 — 高分 findings + 是否有画像 + 是否有话术。"""
    db = get_db()
    from api.db.collections import COPYWRITINGS_COLLECTION, PROFILES_COLLECTION

    cursor = db["findings"].find(
        {"project_id": project_id, "attention_score": {"$gte": min_score}}, {"_id": 0},
    ).sort("attention_score", -1).limit(limit)
    findings = await cursor.to_list(limit)

    fids = [f["finding_id"] for f in findings if f.get("finding_id")]
    cw_ids = {doc["finding_id"] async for doc in db[COPYWRITINGS_COLLECTION].find({"finding_id": {"$in": fids}}, {"finding_id": 1, "_id": 0})}
    profile_ids = {doc["finding_id"] async for doc in db[PROFILES_COLLECTION].find({"finding_id": {"$in": fids}}, {"finding_id": 1, "_id": 0})}

    for f in findings:
        fid = f.get("finding_id", "")
        f["has_copywriting"] = fid in cw_ids
        f["has_profile"] = fid in profile_ids

    return {"items": findings, "total": len(findings), "min_score": min_score}


@router.get("/projects/{project_id}/copywriting-coverage")
async def get_copywriting_coverage(project_id: str):
    """话术覆盖率 — 多少 findings 已生成话术 vs 未生成。"""
    db = get_db()
    from api.db.collections import COPYWRITINGS_COLLECTION

    total_findings = await db["findings"].count_documents({"project_id": project_id})
    total_cw = await db[COPYWRITINGS_COLLECTION].count_documents({"project_id": project_id})

    high_total = await db["findings"].count_documents({"project_id": project_id, "attention_score": {"$gte": 60}})
    high_covered_pipeline = [
        {"$match": {"project_id": project_id, "attention_score": {"$gte": 60}}},
        {"$lookup": {"from": "copywritings", "localField": "finding_id", "foreignField": "finding_id", "as": "cw"}},
        {"$match": {"cw": {"$ne": []}}},
        {"$count": "count"},
    ]
    r = await db["findings"].aggregate(high_covered_pipeline).to_list(1)
    high_covered = r[0]["count"] if r else 0

    return {
        "total_findings": total_findings,
        "total_copywritings": total_cw,
        "coverage_rate": round(total_cw / total_findings * 100, 1) if total_findings else 0,
        "high_score": {
            "total": high_total, "covered": high_covered, "uncovered": high_total - high_covered,
            "coverage_rate": round(high_covered / high_total * 100, 1) if high_total else 0,
        },
    }


# ═══════════════════════════════════════════
# 观测层 + Skills
# ═══════════════════════════════════════════

@router.get("/stats/global")
async def get_global_stats():
    from Sere1nGraph.graph.observability import get_global_tracker
    tracker = get_global_tracker()
    _ensure_tracker_db(tracker)
    global_stats = await tracker.get_stats_async()
    project_list = await tracker.list_projects_async()
    return {"global": global_stats, "projects": project_list}


@router.get("/stats/hierarchy")
async def get_stats_hierarchy(project_id: str = ""):
    """完整层级树（全局 → 项目 → 任务 → 阶段），看板分层钻取用。"""
    from Sere1nGraph.graph.observability import get_global_tracker
    tracker = get_global_tracker()
    _ensure_tracker_db(tracker)
    return tracker.get_hierarchy(project_id or "")


@router.get("/stats/project/{project_id}")
async def get_project_stats(project_id: str):
    from Sere1nGraph.graph.observability import get_global_tracker
    tracker = get_global_tracker()
    _ensure_tracker_db(tracker)
    proj_stats = await tracker.get_stats_async(project_id=project_id)
    task_list = await tracker.list_tasks_async(project_id=project_id)
    return {"stats": proj_stats, "tasks": task_list}


@router.get("/stats/task/{task_id}")
async def get_task_stats(task_id: str):
    from Sere1nGraph.graph.observability import get_global_tracker
    tracker = get_global_tracker()
    _ensure_tracker_db(tracker)
    task_stats = await tracker.get_stats_async(task_id=task_id)
    agent_list = await tracker.list_agents_async(task_id=task_id)
    return {"stats": task_stats, "agents": agent_list}


@router.get("/stats/records")
async def get_stats_records(project_id: str = "", task_id: str = "", limit: int = 50):
    from Sere1nGraph.graph.observability import get_global_tracker
    tracker = get_global_tracker()
    _ensure_tracker_db(tracker)
    records = await tracker.get_records_async(project_id, task_id, limit)
    return {"records": records}


@router.get("/graph/skills", operation_id="list_graph_skills")
async def list_graph_skills():
    from Sere1nGraph.graph.skills import get_skill_registry
    registry = get_skill_registry()
    skills = [s.model_dump() for s in registry.list_all()]
    return {"skills": skills, "summary": registry.get_summary()}


@router.get("/graph/skills/{skill_id}", operation_id="get_graph_skill")
async def get_graph_skill(skill_id: str):
    from Sere1nGraph.graph.skills import get_skill_registry
    registry = get_skill_registry()
    skill = registry.load_skill(skill_id)
    if not skill:
        raise HTTPException(404, f"Skill '{skill_id}' 不存在")
    return skill.model_dump()
