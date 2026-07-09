"""
XHS 小红书社工信息采集 - API 路由
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.auth import get_current_active_user
from api.db.mongodb import init_mongo, get_db
from api.dao import xhs as xhs_dao
from api.dao import projects as projects_dao
from api.schemas.pagination import (
    PageResponse,
)
from core.logger import get_logger

logger = get_logger("xhs_router")
from api.models.xhs_schema import (
    XhsCookieCreate,
    XhsCookieUpdate,
    XhsCookieOut,
    XhsCookieDetail,
    XhsSearchTaskCreate,
    XhsSearchTaskOut,
    XhsNoteOut,
    XhsNoteDetailOut,
    XhsProfileOut,
    XhsSearchResponse,
    XhsNoteTagging,
    XhsNoteUserInfo,
    XhsDetailTagging,
)


router = APIRouter(dependencies=[Depends(get_current_active_user)])

init_mongo()


# ==================== 辅助函数 ====================

def _cookie_out(doc: dict) -> XhsCookieOut:
    return XhsCookieOut(
        id=str(doc.get("_id")),
        account_name=doc.get("account_name"),
        is_active=doc.get("is_active", False),
        is_enabled=doc.get("is_enabled", True),
        is_valid=doc.get("is_valid"),
        last_verified_at=doc.get("last_verified_at"),
        last_used_at=doc.get("last_used_at"),
        cooldown_until=doc.get("cooldown_until"),
        lease_count=doc.get("lease_count", 0),
        success_count=doc.get("success_count", 0),
        failure_count=doc.get("failure_count", 0),
        consecutive_failures=doc.get("consecutive_failures", 0),
        quarantined_at=doc.get("quarantined_at"),
        quarantine_reason=doc.get("quarantine_reason"),
        last_error=doc.get("last_error"),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
    )


def _cookie_detail(doc: dict) -> XhsCookieDetail:
    return XhsCookieDetail(
        id=str(doc.get("_id")),
        account_name=doc.get("account_name"),
        cookie_string=doc.get("cookie_string", ""),
        is_active=doc.get("is_active", False),
        is_enabled=doc.get("is_enabled", True),
        is_valid=doc.get("is_valid"),
        last_verified_at=doc.get("last_verified_at"),
        last_used_at=doc.get("last_used_at"),
        cooldown_until=doc.get("cooldown_until"),
        lease_count=doc.get("lease_count", 0),
        success_count=doc.get("success_count", 0),
        failure_count=doc.get("failure_count", 0),
        consecutive_failures=doc.get("consecutive_failures", 0),
        quarantined_at=doc.get("quarantined_at"),
        quarantine_reason=doc.get("quarantine_reason"),
        last_error=doc.get("last_error"),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
    )


def _task_out(doc: dict) -> XhsSearchTaskOut:
    return XhsSearchTaskOut(
        id=str(doc.get("_id")),
        project_id=doc.get("project_id"),
        keyword=doc.get("keyword"),
        max_notes=doc.get("max_notes", 20),
        attention_threshold=doc.get("attention_threshold", 60),
        status=doc.get("status", "pending"),
        notes_count=doc.get("notes_count", 0),
        suspicious_count=doc.get("suspicious_count", 0),
        profiles_count=doc.get("profiles_count", 0),
        error_message=doc.get("error_message"),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
    )


def _note_out(doc: dict) -> XhsNoteOut:
    user_data = doc.get("user", {})
    tagging_data = doc.get("tagging")
    
    return XhsNoteOut(
        id=str(doc.get("_id")),
        project_id=doc.get("project_id"),
        task_id=doc.get("task_id"),
        note_id=doc.get("note_id"),
        xsec_token=doc.get("xsec_token"),  # 用于前端跳转
        xsec_source=doc.get("xsec_source"),
        title=doc.get("title", ""),
        desc=doc.get("desc", ""),
        liked_count=doc.get("liked_count", "0"),
        user=XhsNoteUserInfo(
            user_id=user_data.get("user_id", ""),
            nickname=user_data.get("nickname", ""),
            avatar=user_data.get("avatar"),
        ),
        cover=doc.get("cover"),
        publish_time_text=doc.get("publish_time_text"),
        tagging=XhsNoteTagging(**tagging_data) if tagging_data else None,
        created_at=doc.get("created_at"),
    )


def _detail_out(doc: dict) -> XhsNoteDetailOut:
    tagging_data = doc.get("tagging")
    return XhsNoteDetailOut(
        id=str(doc.get("_id")),
        note_id=doc.get("note_id"),
        project_id=doc.get("project_id"),
        xsec_token=doc.get("xsec_token"),
        xsec_source=doc.get("xsec_source"),
        content=doc.get("content"),
        comments_summary=doc.get("comments_summary"),
        tagging=XhsDetailTagging(**tagging_data) if tagging_data else None,
        created_at=doc.get("created_at"),
    )


def _profile_out(doc: dict) -> XhsProfileOut:
    """转换数据库文档为 API 输出格式"""
    return XhsProfileOut(
        id=str(doc.get("_id")),
        project_id=doc.get("project_id", ""),
        task_id=doc.get("task_id", ""),
        user_id=doc.get("user_id", ""),
        finding_id=doc.get("finding_id"),
        nickname=doc.get("nickname", ""),
        avatar_url=doc.get("avatar_url") or doc.get("avatar"),  # 兼容旧字段
        # Agent 分析结果
        basic_info=doc.get("basic_info"),
        stats=doc.get("stats"),
        identity=doc.get("identity"),
        bio_analysis=doc.get("bio_analysis"),
        device_info=doc.get("device_info"),
        avatar_analysis=doc.get("avatar_analysis"),
        gender_analysis=doc.get("gender_analysis"),
        personality_profile=doc.get("personality_profile"),
        notes_analysis=doc.get("notes_analysis"),
        company_identification=doc.get("company_identification"),
        keyword_relevance=doc.get("keyword_relevance"),
        attack_surface=doc.get("attack_surface"),
        social_graph=doc.get("social_graph"),
        timeline=doc.get("timeline"),
        profile_summary=doc.get("profile_summary"),
        attention_score=doc.get("attention_score", 0),
        recommended_actions=doc.get("recommended_actions", []),
        tags=doc.get("tags", []),
        # 兼容
        note_ids=doc.get("note_ids", []),
        notes_count=doc.get("notes_count", 0),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
    )


# ==================== Cookie 管理 ====================

@router.post("/cookies", response_model=XhsCookieOut)
async def create_cookie(body: XhsCookieCreate):
    """添加或更新账号 Cookie"""
    db = get_db()
    doc = await xhs_dao.create_cookie(
        db,
        account_name=body.account_name,
        cookie_string=body.cookie_string,
    )
    return _cookie_out(doc)


@router.get("/cookies", response_model=PageResponse)
async def list_cookies(limit: int = 50, skip: int = 0):
    """列出所有账号"""
    db = get_db()
    docs, total = await xhs_dao.list_cookies(db, limit=limit, skip=skip)
    return PageResponse.build(
        items=[_cookie_out(d) for d in docs],
        total=total,
        page=1,
        page_size=limit,
    )


@router.get("/cookies/{account_name}", response_model=XhsCookieOut)
async def get_cookie(account_name: str):
    """获取账号基本信息（不含 Cookie 字符串）"""
    db = get_db()
    doc = await xhs_dao.get_cookie_by_name(db, account_name)
    if not doc:
        raise HTTPException(status_code=404, detail="账号不存在")
    return _cookie_out(doc)


@router.get("/cookies/{account_name}/detail", response_model=XhsCookieDetail)
async def get_cookie_detail(account_name: str):
    """获取账号完整详情（含 Cookie 字符串）"""
    db = get_db()
    doc = await xhs_dao.get_cookie_by_name(db, account_name)
    if not doc:
        raise HTTPException(status_code=404, detail="账号不存在")
    return _cookie_detail(doc)


@router.put("/cookies/{account_name}", response_model=XhsCookieOut)
async def update_cookie(account_name: str, body: XhsCookieUpdate):
    """更新账号（可修改账号名称、Cookie 字符串、激活状态）"""
    db = get_db()
    
    # 检查账号是否存在
    doc = await xhs_dao.get_cookie_by_name(db, account_name)
    if not doc:
        raise HTTPException(status_code=404, detail="账号不存在")
    
    patch = {}
    if body.cookie_string is not None:
        patch["cookie_string"] = body.cookie_string
    if body.is_active is not None:
        patch["is_active"] = body.is_active
    if body.is_enabled is not None:
        patch["is_enabled"] = body.is_enabled
    if body.new_account_name is not None:
        # 检查新名称是否已存在
        if body.new_account_name != account_name:
            existing = await xhs_dao.get_cookie_by_name(db, body.new_account_name)
            if existing:
                raise HTTPException(status_code=400, detail=f"账号名 {body.new_account_name} 已存在")
            patch["account_name"] = body.new_account_name
    
    if not patch:
        return _cookie_out(doc)
    
    doc = await xhs_dao.update_cookie(db, account_name, patch)
    return _cookie_out(doc)


@router.delete("/cookies/{account_name}")
async def delete_cookie(account_name: str):
    """删除账号"""
    db = get_db()
    deleted = await xhs_dao.delete_cookie(db, account_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="账号不存在")
    return {"ok": True}


@router.post("/cookies/{account_name}/verify", response_model=XhsCookieOut)
async def verify_cookie(account_name: str):
    """验证账号 Cookie 有效性"""
    db = get_db()
    
    doc = await xhs_dao.get_cookie_by_name(db, account_name)
    if not doc:
        raise HTTPException(status_code=404, detail="账号不存在")
    
    cookie_string = doc.get("cookie_string")
    if not cookie_string:
        raise HTTPException(status_code=400, detail="Cookie 为空")
    
    # 验证 Cookie — 优先用 V2（不需要浏览器，秒级完成）
    is_valid = False
    try:
        from crawler_tools.xhs_client_v2 import XhsClientV2
        v2 = XhsClientV2(cookie_string)
        is_valid = await v2.pong()
        if is_valid:
            logger.info(f"[XHS] V2 Cookie 验证通过: {account_name}")
    except Exception as e:
        logger.warning(f"[XHS] V2 验证失败，fallback: {e}")

    # Fallback: MediaCrawler（Playwright 浏览器验证）
    if not is_valid:
        try:
            from crawler_tools.xhs_crawler import XhsCrawler, CrawlerConfig
            from api.services.runtime_config import get_runtime_config_section

            config = CrawlerConfig.from_dict(await get_runtime_config_section("xhs_crawler"))
            config.set_cookie(account_name, cookie_string)
            config.active_account = account_name
            
            crawler = XhsCrawler(config)
            login_result = await crawler.login_by_account(account_name)
            is_valid = login_result.success
            await crawler.close()
        except Exception:
            is_valid = False
    
    doc = await xhs_dao.set_cookie_valid(db, account_name, is_valid)
    return _cookie_out(doc)


@router.post("/cookies/{account_name}/activate", response_model=XhsCookieOut)
async def activate_cookie(account_name: str):
    """激活指定账号"""
    db = get_db()
    
    doc = await xhs_dao.get_cookie_by_name(db, account_name)
    if not doc:
        raise HTTPException(status_code=404, detail="账号不存在")
    
    doc = await xhs_dao.activate_cookie(db, account_name)
    return _cookie_out(doc)


@router.get("/runtime/status")
async def get_xhs_runtime_status():
    """获取小红书采集运行时状态：账号池、代理池。"""
    from api.services.xhs_runtime import get_xhs_runtime_status as get_runtime_status

    return await get_runtime_status(get_db())


@router.post("/runtime/sign-test")
async def test_xhs_signer(account_name: str | None = None, verify_network: bool = False):
    """测试 xhsvm.js 签名脚本可用性，可选使用账号做网络 pong。"""
    from api.services.xhs_runtime import test_xhs_signer

    return await test_xhs_signer(
        db=get_db(),
        account_name=account_name,
        verify_network=verify_network,
    )


# ==================== 搜索流水线 ====================

@router.post("/search", response_model=XhsSearchResponse)
async def create_search_task(body: XhsSearchTaskCreate, background_tasks: BackgroundTasks):
    """创建搜索任务（自动触发全流程）"""
    db = get_db()
    
    # 验证项目存在
    project = await projects_dao.get_project(db, body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 创建任务
    task_doc = await xhs_dao.create_search_task(
        db,
        project_id=body.project_id,
        keyword=body.keyword,
        max_notes=body.max_notes,
        attention_threshold=body.attention_threshold,
    )
    
    task_id = str(task_doc["_id"])
    
    # 后台运行流水线
    async def run_pipeline_task():
        from api.services.xhs_pipeline import run_xhs_pipeline
        from api.services.runtime_config import get_runtime_app_config

        runtime_config = await get_runtime_app_config()
        await run_xhs_pipeline(
            db=get_db(),
            app_config=runtime_config,
            task_id=task_id,
            project_id=body.project_id,
            keyword=body.keyword,
            max_notes=body.max_notes,
            attention_threshold=body.attention_threshold,
        )
    
    background_tasks.add_task(asyncio.create_task, run_pipeline_task())
    
    return XhsSearchResponse(
        task=_task_out(task_doc),
        message="搜索任务已创建，正在后台执行",
    )


@router.get("/tasks/{task_id}", response_model=XhsSearchTaskOut)
async def get_search_task(task_id: str):
    """获取搜索任务状态"""
    db = get_db()
    doc = await xhs_dao.get_search_task(db, task_id)
    if not doc:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _task_out(doc)




# ==================== 数据查询 ====================

@router.get("/notes/{note_id}", response_model=XhsNoteOut)
async def get_note(note_id: str):
    """获取单个笔记"""
    db = get_db()
    doc = await xhs_dao.get_note(db, note_id)
    if not doc:
        raise HTTPException(status_code=404, detail="笔记不存在")
    return _note_out(doc)


@router.get("/notes/{note_id}/detail", response_model=XhsNoteDetailOut)
async def get_note_detail(note_id: str):
    """获取笔记详情"""
    db = get_db()
    doc = await xhs_dao.get_note_detail(db, note_id)
    if not doc:
        raise HTTPException(status_code=404, detail="笔记详情不存在")
    return _detail_out(doc)


@router.get("/profiles/{profile_id}", response_model=XhsProfileOut)
async def get_profile(profile_id: str):
    """获取人物画像详情"""
    db = get_db()
    doc = await xhs_dao.get_profile_by_id(db, profile_id)
    if not doc:
        raise HTTPException(status_code=404, detail="人物画像不存在")
    return _profile_out(doc)


@router.delete("/profiles/{profile_id}")
async def delete_profile(profile_id: str):
    """删除人物画像"""
    db = get_db()
    deleted = await xhs_dao.delete_profile(db, profile_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="人物画像不存在")
    return {"ok": True, "message": "人物画像已删除"}


# ==================== 视觉分析 SSE 接口 ====================

import uuid
from typing import Dict
import asyncio

# SSE 任务管理器
_sse_tasks: Dict[str, dict] = {}  # task_id -> {"cancelled": bool, "status": str, "stage": str}


def _create_sse_task() -> str:
    """创建 SSE 任务并返回 task_id"""
    task_id = str(uuid.uuid4())[:8]
    _sse_tasks[task_id] = {
        "cancelled": False, 
        "status": "running",
        "stage": "init",  # init, screenshot, vision, format, save
        "can_cancel": True,  # 是否可以立即取消
    }
    return task_id


def _update_task_stage(task_id: str, stage: str, can_cancel: bool = True):
    """更新任务阶段"""
    if task_id in _sse_tasks:
        _sse_tasks[task_id]["stage"] = stage
        _sse_tasks[task_id]["can_cancel"] = can_cancel


def _cancel_sse_task(task_id: str) -> dict:
    """取消 SSE 任务，返回取消状态"""
    if task_id not in _sse_tasks:
        return {"success": False, "message": f"任务 {task_id} 不存在或已完成"}
    
    task = _sse_tasks[task_id]
    task["cancelled"] = True
    task["status"] = "cancelled"
    
    stage = task.get("stage", "unknown")
    can_cancel = task.get("can_cancel", True)
    
    if not can_cancel:
        return {
            "success": True,
            "message": f"任务已标记取消，但当前处于 {stage} 阶段，API 调用无法中断，将在完成后停止",
            "stage": stage,
            "immediate": False,
        }
    
    return {
        "success": True,
        "message": f"任务 {task_id} 已取消",
        "stage": stage,
        "immediate": True,
    }


def _is_task_cancelled(task_id: str) -> bool:
    """检查任务是否已取消"""
    return _sse_tasks.get(task_id, {}).get("cancelled", False)


def _cleanup_sse_task(task_id: str):
    """清理 SSE 任务"""
    if task_id in _sse_tasks:
        del _sse_tasks[task_id]


async def _flush_sse_generator(generator):
    """包装 SSE 生成器，确保每条消息立即发送"""
    async for chunk in generator:
        yield chunk
        # 添加一个微小的 sleep 让事件循环有机会发送数据
        await asyncio.sleep(0)


class VisionAnalysisRequest(BaseModel):
    user_url: str


async def vision_analysis_stream(user_url: str, db, task_id: str):
    """视觉分析流式生成器"""
    from api.services.xhs_vision_tools import screenshot_user_profile_stream, analyze_screenshots_with_vision_stream
    
    try:
        # 发送任务 ID 和初始状态
        yield f"data: {json.dumps({'type': 'init', 'task_id': task_id, 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
        
        # 检查取消
        if _is_task_cancelled(task_id):
            yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消', 'stage': 'init'}, ensure_ascii=False)}\n\n"
            return
        
        # ========== 截屏阶段 ==========
        _update_task_stage(task_id, "screenshot", can_cancel=True)
        yield f"data: {json.dumps({'type': 'status', 'message': '📸 开始截屏...', 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
        
        # 流式截屏
        screenshots = []
        avatar_url = None
        screenshot_error = None
        
        async for item in screenshot_user_profile_stream(user_url, db):
            if _is_task_cancelled(task_id):
                yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消', 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
                return
            
            if item.get("type") == "progress":
                yield f"data: {json.dumps({'type': 'status', 'message': item['message'], 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
            elif item.get("type") == "result":
                data = item.get("data", {})
                screenshots = data.get("screenshots", [])
                avatar_url = data.get("avatar_url")
                screenshot_error = data.get("error")
        
        if screenshot_error:
            yield f"data: {json.dumps({'type': 'error', 'message': screenshot_error}, ensure_ascii=False)}\n\n"
            return
        
        if not screenshots:
            yield f"data: {json.dumps({'type': 'error', 'message': '未获取到截图'}, ensure_ascii=False)}\n\n"
            return
        
        yield f"data: {json.dumps({'type': 'status', 'message': f'✅ 截屏完成，共 {len(screenshots)} 张', 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
        
        # ========== 视觉分析阶段 ==========
        _update_task_stage(task_id, "vision", can_cancel=False)  # API 调用中，无法立即取消
        yield f"data: {json.dumps({'type': 'status', 'message': '🔍 开始视觉分析...', 'stage': 'vision'}, ensure_ascii=False)}\n\n"
        
        # 流式视觉分析
        async for chunk in analyze_screenshots_with_vision_stream(screenshots):
            # 视觉分析中检查取消，但不会立即停止（API 已在运行）
            if _is_task_cancelled(task_id):
                yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消（视觉分析已完成当前请求）', 'stage': 'vision'}, ensure_ascii=False)}\n\n"
                return
            yield f"data: {json.dumps({'type': 'content', 'content': chunk}, ensure_ascii=False)}\n\n"
        
        yield f"data: {json.dumps({'type': 'done', 'message': '分析完成'}, ensure_ascii=False)}\n\n"
        
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
    finally:
        _cleanup_sse_task(task_id)


@router.post("/vision-analysis/stream")
async def stream_vision_analysis(
    request: VisionAnalysisRequest,
    db=Depends(get_db),
):
    """
    流式视觉分析接口（SSE）
    
    前端只需输入用户主页 URL，返回流式分析结果
    
    SSE 消息格式：
    - {"type": "task_id", "task_id": "xxx"}  # 首条消息，用于终止
    - {"type": "status", "message": "状态信息"}
    - {"type": "content", "content": "分析内容片段"}
    - {"type": "done", "message": "完成"}
    - {"type": "cancelled", "message": "任务已取消"}
    - {"type": "error", "message": "错误信息"}
    """
    task_id = _create_sse_task()
    return StreamingResponse(
        _flush_sse_generator(vision_analysis_stream(request.user_url, db, task_id)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream; charset=utf-8",
        },
    )


# ==================== 人物画像 SSE 接口 ====================

class ProfileGenerationRequest(BaseModel):
    user_url: str
    project_id: str
    keyword: str = ""


async def profile_generation_stream(user_url: str, project_id: str, keyword: str, db, task_id: str):
    """人物画像生成流式生成器（截屏 → 视觉分析 → Agent 格式化 → 入库）"""
    from api.services.xhs_vision_tools import (
        screenshot_user_profile_stream,
        analyze_screenshots_with_vision_stream,
    )
    from api.utils.json_extract import extract_json_object
    
    try:
        # 解析 user_id
        user_id = ""
        if "/user/profile/" in user_url:
            user_id = user_url.split("/user/profile/")[-1].split("?")[0].split("/")[0]
        
        if not user_id:
            yield f"data: {json.dumps({'type': 'error', 'message': 'URL 格式错误，无法解析 user_id'}, ensure_ascii=False)}\n\n"
            return
        
        # 发送初始信息
        yield f"data: {json.dumps({'type': 'init', 'task_id': task_id, 'user_id': user_id, 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
        
        # 检查取消
        if _is_task_cancelled(task_id):
            yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消', 'stage': 'init'}, ensure_ascii=False)}\n\n"
            return
        
        # ========== 阶段 1: 截屏（流式） ==========
        _update_task_stage(task_id, "screenshot", can_cancel=True)
        yield f"data: {json.dumps({'type': 'status', 'message': '📸 开始截屏...', 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
        
        screenshots = []
        avatar_url = None
        screenshot_error = None
        
        async for item in screenshot_user_profile_stream(user_url, db):
            if _is_task_cancelled(task_id):
                yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消', 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
                return
            
            if item.get("type") == "progress":
                yield f"data: {json.dumps({'type': 'status', 'message': item['message'], 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
            elif item.get("type") == "result":
                data = item.get("data", {})
                screenshots = data.get("screenshots", [])
                avatar_url = data.get("avatar_url")
                screenshot_error = data.get("error")
        
        if screenshot_error:
            yield f"data: {json.dumps({'type': 'error', 'message': screenshot_error}, ensure_ascii=False)}\n\n"
            return
        
        if not screenshots:
            yield f"data: {json.dumps({'type': 'error', 'message': '未获取到截图'}, ensure_ascii=False)}\n\n"
            return
        
        yield f"data: {json.dumps({'type': 'status', 'message': f'✅ 截屏完成，共 {len(screenshots)} 张', 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
        
        if avatar_url:
            yield f"data: {json.dumps({'type': 'avatar', 'avatar_url': avatar_url}, ensure_ascii=False)}\n\n"
        
        # ========== 阶段 2: 视觉分析（流式，API 调用中无法立即取消） ==========
        if _is_task_cancelled(task_id):
            yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消', 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
            return
        
        _update_task_stage(task_id, "vision", can_cancel=False)
        yield f"data: {json.dumps({'type': 'status', 'message': '🔍 开始视觉分析...', 'stage': 'vision'}, ensure_ascii=False)}\n\n"
        
        vision_analysis_chunks = []
        async for chunk in analyze_screenshots_with_vision_stream(screenshots):
            if _is_task_cancelled(task_id):
                # 视觉分析中取消，等当前 chunk 完成后停止
                yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消（视觉分析已完成当前请求）', 'stage': 'vision'}, ensure_ascii=False)}\n\n"
                return
            vision_analysis_chunks.append(chunk)
            yield f"data: {json.dumps({'type': 'content', 'content': chunk}, ensure_ascii=False)}\n\n"
        
        vision_analysis = "".join(vision_analysis_chunks)
        
        yield f"data: {json.dumps({'type': 'status', 'message': '✅ 视觉分析完成', 'stage': 'vision'}, ensure_ascii=False)}\n\n"
        
        # ========== 阶段 3: Agent 格式化（流式输出） ==========
        if _is_task_cancelled(task_id):
            yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消', 'stage': 'vision'}, ensure_ascii=False)}\n\n"
            return
        
        _update_task_stage(task_id, "format", can_cancel=False)
        
        # 先输出"开始结构化输出"提示
        yield f"data: {json.dumps({'type': 'status', 'message': '🤖 开始结构化输出...', 'stage': 'format'}, ensure_ascii=False)}\n\n"
        
        # 构建 Agent 输入
        input_text = f"""请基于以下信息生成用户画像:

搜索关键词: {keyword if keyword else "无"}

## 基础信息

用户ID: {user_id}
头像链接: {avatar_url if avatar_url else "未获取"}

## 用户主页视觉分析

{vision_analysis}

---

请根据视觉分析结果，生成完整的人物画像 JSON。
注意：nickname、stats（关注/粉丝/互动数）等信息需要从视觉分析中提取。"""
        
        # 调用 Agent（流式模式）
        from Sere1nGraph.graph.agents.factory import create_xhs_profile_agent
        from langchain_core.messages import HumanMessage
        from api.services.runtime_config import get_runtime_app_config

        runtime_config = await get_runtime_app_config()
        agent = await create_xhs_profile_agent(runtime_config, output_mode="sse")
        
        # 流式输出 Agent 内容
        format_content_chunks = []
        async for event in agent({"messages": [HumanMessage(content=input_text)]}):
            if _is_task_cancelled(task_id):
                yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消（格式化中断）', 'stage': 'format'}, ensure_ascii=False)}\n\n"
                return
            
            event_type = event.get("type")
            if event_type == "content":
                chunk = event.get("data", "")
                format_content_chunks.append(chunk)
                # 流式输出结构化内容（使用 format_content 类型区分）
                yield f"data: {json.dumps({'type': 'format_content', 'content': chunk}, ensure_ascii=False)}\n\n"
            elif event_type == "error":
                yield f"data: {json.dumps({'type': 'error', 'message': event.get('message', '格式化失败')}, ensure_ascii=False)}\n\n"
                return
        
        # 解析完整的 Agent 响应
        format_content = "".join(format_content_chunks)
        tagging = None
        
        if format_content.strip():
            try:
                tagging = extract_json_object(format_content.strip())
            except Exception:
                pass
        
        if not tagging:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Agent 格式化失败，无法解析 JSON'}, ensure_ascii=False)}\n\n"
            return
        
        yield f"data: {json.dumps({'type': 'status', 'message': '✅ 结构化输出完成', 'stage': 'format'}, ensure_ascii=False)}\n\n"
        
        # ========== 阶段 4: 入库 ==========
        if _is_task_cancelled(task_id):
            yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消', 'stage': 'format'}, ensure_ascii=False)}\n\n"
            return
        
        _update_task_stage(task_id, "save", can_cancel=True)
        yield f"data: {json.dumps({'type': 'status', 'message': '💾 正在保存到数据库...', 'stage': 'save'}, ensure_ascii=False)}\n\n"
        
        # 使用新的 DAO 函数直接保存分析结果
        profile = await xhs_dao.save_profile_from_vision(
            db,
            project_id=project_id,
            user_id=user_id,
            avatar_url=avatar_url,
            analysis_result=tagging,  # Agent 输出的 JSON 直接存储
        )
        
        yield f"data: {json.dumps({'type': 'status', 'message': '✅ 入库完成', 'stage': 'save'}, ensure_ascii=False)}\n\n"
        
        # ========== 完成 ==========
        yield f"data: {json.dumps({'type': 'done', 'message': '人物画像生成完成', 'user_id': user_id}, ensure_ascii=False)}\n\n"
        
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
    finally:
        _cleanup_sse_task(task_id)


@router.post("/profile/generate/stream")
async def stream_profile_generation(
    request: ProfileGenerationRequest,
    db=Depends(get_db),
):
    """
    人物画像生成接口（SSE 流式）
    
    输入用户主页 URL，自动完成：截屏 → 视觉分析 → Agent 格式化 → 入库
    
    SSE 消息格式：
    - {"type": "task_id", "task_id": "xxx"}  # 首条消息，用于终止
    - {"type": "status", "message": "状态信息"}
    - {"type": "avatar", "avatar_url": "头像链接"}
    - {"type": "content", "content": "视觉分析内容片段"}
    - {"type": "profile", "data": {...}}  # 最终的人物画像 JSON
    - {"type": "done", "message": "完成", "user_id": "xxx"}
    - {"type": "cancelled", "message": "任务已取消"}
    - {"type": "error", "message": "错误信息"}
    """
    # 验证项目存在
    project = await projects_dao.get_project(db, request.project_id)
    if not project:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': '项目不存在'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(
            error_stream(),
            media_type="text/event-stream",
        )
    
    task_id = _create_sse_task()
    return StreamingResponse(
        _flush_sse_generator(profile_generation_stream(request.user_url, request.project_id, request.keyword, db, task_id)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream; charset=utf-8",
        },
    )


# ==================== SSE 任务终止接口 ====================

@router.post("/sse/cancel/{task_id}")
async def cancel_sse_task(task_id: str):
    """
    终止 SSE 任务
    
    返回：
    - success: 是否成功标记取消
    - message: 提示信息
    - stage: 当前阶段（如果任务存在）
    - immediate: 是否可以立即取消（false 表示 API 调用中，需等待完成）
    """
    result = _cancel_sse_task(task_id)
    # 即使任务不存在也返回 200，让前端知道取消请求已处理
    return result


@router.get("/sse/tasks")
async def list_sse_tasks():
    """
    列出当前所有 SSE 任务（调试用）
    """
    return {"tasks": _sse_tasks}
