"""
抖音社工信息采集 - API 路由
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.auth import get_current_active_user
from api.db.mongodb import init_mongo, get_db
from api.dao import douyin as douyin_dao
from api.dao import projects as projects_dao
from core.llm_params import disable_thinking_extra_body
from api.schemas.pagination import (
    PageResponse,
    DouyinSearchResultsListRequest,
    DouyinTaggedResultsListRequest,
    DouyinProfilesListRequest,
)


router = APIRouter(dependencies=[Depends(get_current_active_user)])

init_mongo()


# ==================== Pydantic 模型 ====================

class CookieCreate(BaseModel):
    account_name: str
    cookie_string: str


class CookieUpdate(BaseModel):
    cookie_string: str | None = None
    is_active: bool | None = None
    new_account_name: str | None = None


class CookieOut(BaseModel):
    id: str
    account_name: str
    is_active: bool
    is_valid: bool | None
    last_verified_at: str | None
    created_at: str | None
    updated_at: str | None


class CookieDetail(BaseModel):
    """Cookie 详情（含 cookie_string）"""
    id: str
    account_name: str
    cookie_string: str
    is_active: bool
    is_valid: bool | None
    last_verified_at: str | None
    created_at: str | None
    updated_at: str | None


class SearchResultOut(BaseModel):
    id: str
    project_id: str
    aweme_id: str
    keyword: str | None
    title: str | None
    nickname: str | None
    sec_uid: str | None
    user_id: str | None
    avatar: str | None
    cover_url: str | None
    ip_location: str | None
    user_profile_url: str | None
    aweme_url: str | None
    video_download_url: str | None
    liked_count: str | None
    collected_count: str | None
    comment_count: str | None
    share_count: str | None
    create_time_str: str | None
    created_at: str | None


class TaggedResultOut(BaseModel):
    id: str
    project_id: str
    aweme_id: str
    keyword: str | None
    title: str | None
    nickname: str | None
    sec_uid: str | None
    user_id: str | None
    avatar: str | None
    cover_url: str | None
    ip_location: str | None
    user_profile_url: str | None
    aweme_url: str | None
    liked_count: str | None
    collected_count: str | None
    comment_count: str | None
    share_count: str | None
    tag: str | None
    tag_reason: str | None
    confidence: str | None
    key_evidence: list[str] | None
    company_mentioned: str | None
    position_mentioned: str | None
    priority: int | None
    create_time_str: str | None
    created_at: str | None


class ProfileOut(BaseModel):
    id: str
    project_id: str
    sec_uid: str
    finding_id: str | None = None
    user_id: str | None
    nickname: str | None
    avatar_url: str | None
    user_profile_url: str | None
    ip_location: str | None
    # 打标信息
    sample_title: str | None
    tag_reason: str | None
    confidence: str | None
    key_evidence: list[str] | None
    company_mentioned: str | None
    position_mentioned: str | None
    priority: int | None
    aweme_count: int | None
    # 视觉分析
    profile_summary: str | None
    attention_score: int | None
    tags: list[str] | None
    screenshot_paths: list[str] | None
    # 时间
    created_at: str | None
    updated_at: str | None


class PotentialUserOut(BaseModel):
    sec_uid: str
    nickname: str | None
    user_id: str | None
    avatar: str | None
    user_profile_url: str | None
    tag_reason: str | None
    confidence: str | None
    priority: int | None
    aweme_count: int


class ScreenshotRequest(BaseModel):
    user_url: str
    max_screenshots: int = 5


class VisionAnalysisRequest(BaseModel):
    user_url: str
    project_id: str


class ProfileGenerationRequest(BaseModel):
    """人物画像生成请求"""
    user_url: str
    project_id: str
    keyword: str = ""


class PipelineRequest(BaseModel):
    """流水线请求"""
    keyword: str
    max_videos: int = 20
    publish_time: int = 0  # 0=不限, 1=一天内, 7=一周内, 180=半年内
    enable_profile: bool = True


class PipelineResult(BaseModel):
    """流水线结果"""
    project_id: str
    keyword: str
    videos_count: int
    potential_count: int
    profiles_count: int
    error: str | None


# ==================== 辅助函数 ====================

def _cookie_out(doc: dict) -> CookieOut:
    return CookieOut(
        id=str(doc.get("_id")),
        account_name=doc.get("account_name", ""),
        is_active=doc.get("is_active", False),
        is_valid=doc.get("is_valid"),
        last_verified_at=str(doc.get("last_verified_at")) if doc.get("last_verified_at") else None,
        created_at=str(doc.get("created_at")) if doc.get("created_at") else None,
        updated_at=str(doc.get("updated_at")) if doc.get("updated_at") else None,
    )


def _cookie_detail(doc: dict) -> CookieDetail:
    return CookieDetail(
        id=str(doc.get("_id")),
        account_name=doc.get("account_name", ""),
        cookie_string=doc.get("cookie_string", ""),
        is_active=doc.get("is_active", False),
        is_valid=doc.get("is_valid"),
        last_verified_at=str(doc.get("last_verified_at")) if doc.get("last_verified_at") else None,
        created_at=str(doc.get("created_at")) if doc.get("created_at") else None,
        updated_at=str(doc.get("updated_at")) if doc.get("updated_at") else None,
    )


def _search_result_out(doc: dict) -> SearchResultOut:
    return SearchResultOut(
        id=str(doc.get("_id")),
        project_id=doc.get("project_id", ""),
        aweme_id=doc.get("aweme_id", ""),
        keyword=doc.get("keyword"),
        title=doc.get("title"),
        nickname=doc.get("nickname"),
        sec_uid=doc.get("sec_uid"),
        user_id=doc.get("user_id"),
        avatar=doc.get("avatar"),
        cover_url=doc.get("cover_url"),
        ip_location=doc.get("ip_location"),
        user_profile_url=doc.get("user_profile_url"),
        aweme_url=doc.get("aweme_url"),
        video_download_url=doc.get("video_download_url"),
        liked_count=str(doc.get("liked_count")) if doc.get("liked_count") is not None else None,
        collected_count=str(doc.get("collected_count")) if doc.get("collected_count") is not None else None,
        comment_count=str(doc.get("comment_count")) if doc.get("comment_count") is not None else None,
        share_count=str(doc.get("share_count")) if doc.get("share_count") is not None else None,
        create_time_str=doc.get("create_time_str"),
        created_at=str(doc.get("created_at")) if doc.get("created_at") else None,
    )


def _tagged_result_out(doc: dict) -> TaggedResultOut:
    return TaggedResultOut(
        id=str(doc.get("_id")),
        project_id=doc.get("project_id", ""),
        aweme_id=doc.get("aweme_id", ""),
        keyword=doc.get("keyword"),
        title=doc.get("title"),
        nickname=doc.get("nickname"),
        sec_uid=doc.get("sec_uid"),
        user_id=doc.get("user_id"),
        avatar=doc.get("avatar"),
        cover_url=doc.get("cover_url"),
        ip_location=doc.get("ip_location"),
        user_profile_url=doc.get("user_profile_url"),
        aweme_url=doc.get("aweme_url"),
        liked_count=str(doc.get("liked_count")) if doc.get("liked_count") is not None else None,
        collected_count=str(doc.get("collected_count")) if doc.get("collected_count") is not None else None,
        comment_count=str(doc.get("comment_count")) if doc.get("comment_count") is not None else None,
        share_count=str(doc.get("share_count")) if doc.get("share_count") is not None else None,
        tag=doc.get("tag"),
        tag_reason=doc.get("tag_reason"),
        confidence=doc.get("confidence"),
        key_evidence=doc.get("key_evidence"),
        company_mentioned=doc.get("company_mentioned"),
        position_mentioned=doc.get("position_mentioned"),
        priority=doc.get("priority"),
        create_time_str=doc.get("create_time_str"),
        created_at=str(doc.get("created_at")) if doc.get("created_at") else None,
    )


def _profile_out(doc: dict) -> ProfileOut:
    return ProfileOut(
        id=str(doc.get("_id")),
        project_id=doc.get("project_id", ""),
        sec_uid=doc.get("sec_uid", ""),
        finding_id=doc.get("finding_id"),
        user_id=doc.get("user_id"),
        nickname=doc.get("nickname"),
        avatar_url=doc.get("avatar_url"),
        user_profile_url=doc.get("user_profile_url"),
        ip_location=doc.get("ip_location"),
        # 打标信息
        sample_title=doc.get("sample_title"),
        tag_reason=doc.get("tag_reason"),
        confidence=doc.get("confidence"),
        key_evidence=doc.get("key_evidence"),
        company_mentioned=doc.get("company_mentioned"),
        position_mentioned=doc.get("position_mentioned"),
        priority=doc.get("priority"),
        aweme_count=doc.get("aweme_count"),
        # 视觉分析
        profile_summary=doc.get("profile_summary"),
        attention_score=doc.get("attention_score"),
        tags=doc.get("tags"),
        screenshot_paths=doc.get("screenshot_paths"),
        # 时间
        created_at=str(doc.get("created_at")) if doc.get("created_at") else None,
        updated_at=str(doc.get("updated_at")) if doc.get("updated_at") else None,
    )


def _potential_user_out(doc: dict) -> PotentialUserOut:
    return PotentialUserOut(
        sec_uid=doc.get("_id", ""),
        nickname=doc.get("nickname"),
        user_id=doc.get("user_id"),
        avatar=doc.get("avatar"),
        user_profile_url=doc.get("user_profile_url"),
        tag_reason=doc.get("tag_reason"),
        confidence=doc.get("confidence"),
        priority=doc.get("priority"),
        aweme_count=doc.get("aweme_count", 0),
    )


# ==================== Cookie 管理 ====================

@router.post("/cookies", response_model=CookieOut)
async def create_cookie(body: CookieCreate):
    """添加或更新 Cookie"""
    db = get_db()
    doc = await douyin_dao.create_cookie(db, body.account_name, body.cookie_string)
    return _cookie_out(doc)


@router.get("/cookies", response_model=PageResponse)
async def list_cookies(limit: int = 50, skip: int = 0):
    """列出所有 Cookie"""
    db = get_db()
    docs, total = await douyin_dao.list_cookies(db, limit=limit, skip=skip)
    return PageResponse.build(
        items=[_cookie_out(d) for d in docs],
        total=total,
        page=1,
        page_size=limit,
    )


@router.get("/cookies/{account_name}", response_model=CookieOut)
async def get_cookie(account_name: str):
    """获取账号基本信息（不含 Cookie 字符串）"""
    db = get_db()
    doc = await douyin_dao.get_cookie_by_name(db, account_name)
    if not doc:
        raise HTTPException(status_code=404, detail="账号不存在")
    return _cookie_out(doc)


@router.get("/cookies/{account_name}/detail", response_model=CookieDetail)
async def get_cookie_detail(account_name: str):
    """获取账号完整详情（含 Cookie 字符串）"""
    db = get_db()
    doc = await douyin_dao.get_cookie_by_name(db, account_name)
    if not doc:
        raise HTTPException(status_code=404, detail="账号不存在")
    return _cookie_detail(doc)


@router.put("/cookies/{account_name}", response_model=CookieOut)
async def update_cookie(account_name: str, body: CookieUpdate):
    """更新账号（可修改账号名称、Cookie 字符串、激活状态）"""
    db = get_db()
    
    # 检查账号是否存在
    doc = await douyin_dao.get_cookie_by_name(db, account_name)
    if not doc:
        raise HTTPException(status_code=404, detail="账号不存在")
    
    patch = {}
    if body.cookie_string is not None:
        patch["cookie_string"] = body.cookie_string
    if body.is_active is not None:
        patch["is_active"] = body.is_active
    if body.new_account_name is not None:
        # 检查新名称是否已存在
        if body.new_account_name != account_name:
            existing = await douyin_dao.get_cookie_by_name(db, body.new_account_name)
            if existing:
                raise HTTPException(status_code=400, detail=f"账号名 {body.new_account_name} 已存在")
            patch["account_name"] = body.new_account_name
    
    if not patch:
        return _cookie_out(doc)
    
    doc = await douyin_dao.update_cookie(db, account_name, patch)
    return _cookie_out(doc)


@router.post("/cookies/{account_name}/activate", response_model=CookieOut)
async def activate_cookie(account_name: str):
    """激活指定 Cookie"""
    db = get_db()
    doc = await douyin_dao.activate_cookie(db, account_name)
    if not doc:
        raise HTTPException(status_code=404, detail="账号不存在")
    return _cookie_out(doc)


@router.post("/cookies/{account_name}/verify", response_model=CookieOut)
async def verify_cookie(account_name: str):
    """验证账号 Cookie 有效性（通过 DouyinCrawler 登录验证）"""
    db = get_db()
    
    doc = await douyin_dao.get_cookie_by_name(db, account_name)
    if not doc:
        raise HTTPException(status_code=404, detail="账号不存在")
    
    cookie_string = doc.get("cookie_string")
    if not cookie_string:
        raise HTTPException(status_code=400, detail="Cookie 为空")
    
    # 验证 Cookie（通过 DouyinCrawler 登录验证）
    is_valid = False
    try:
        from crawler_tools.douyin_crawler import DouyinCrawler, DouyinCrawlerConfig
        
        config = DouyinCrawlerConfig()
        config.set_cookie(account_name, cookie_string)
        config.active_account = account_name
        config.cdp_headless = True
        
        crawler = DouyinCrawler(config)
        login_result = await crawler.login_by_cookie_string(cookie_string)
        is_valid = login_result.success
        await crawler.close()
        
    except Exception as e:
        is_valid = False
    
    doc = await douyin_dao.set_cookie_valid(db, account_name, is_valid)
    return _cookie_out(doc)


@router.delete("/cookies/{account_name}")
async def delete_cookie(account_name: str):
    """删除 Cookie"""
    db = get_db()
    deleted = await douyin_dao.delete_cookie(db, account_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="账号不存在")
    return {"ok": True}


# ==================== 搜索结果 ====================

@router.post("/{project_id}/search-results")
async def list_search_results(project_id: str, body: DouyinSearchResultsListRequest | None = None):
    """列出搜索结果（分页）"""
    if body is None:
        body = DouyinSearchResultsListRequest(project_id=project_id)
    db = get_db()
    
    # 验证项目存在
    project = await projects_dao.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    docs, total = await douyin_dao.list_search_results(db, project_id, keyword=body.keyword, limit=body.limit, skip=body.skip)
    
    return PageResponse.build(
        items=[_search_result_out(d) for d in docs],
        total=total,
        page=body.page,
        page_size=body.page_size,
    )


@router.get("/{project_id}/search-results/{aweme_id}", response_model=SearchResultOut)
async def get_search_result(project_id: str, aweme_id: str):
    """获取单条搜索结果"""
    db = get_db()
    doc = await douyin_dao.get_search_result(db, project_id, aweme_id)
    if not doc:
        raise HTTPException(status_code=404, detail="搜索结果不存在")
    return _search_result_out(doc)


# ==================== 打标结果 ====================

@router.post("/{project_id}/tagged-results")
async def list_tagged_results(project_id: str, body: DouyinTaggedResultsListRequest | None = None):
    """列出打标结果（分页）"""
    if body is None:
        body = DouyinTaggedResultsListRequest(project_id=project_id)
    db = get_db()
    
    # 验证项目存在
    project = await projects_dao.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    docs, total = await douyin_dao.list_tagged_results(db, project_id, tag=body.tag, limit=body.limit, skip=body.skip)
    stats = await douyin_dao.count_tagged_results(db, project_id)
    
    return PageResponse.build(
        items=[_tagged_result_out(d) for d in docs],
        total=total,
        page=body.page,
        page_size=body.page_size,
        stats=stats,
    )


@router.get("/{project_id}/tagged-results/stats")
async def get_tagged_stats(project_id: str):
    """统计打标结果"""
    db = get_db()
    stats = await douyin_dao.count_tagged_results(db, project_id)
    return stats


@router.get("/{project_id}/potential-users")
async def get_potential_users(project_id: str):
    """获取潜在用户列表（去重）"""
    db = get_db()
    
    # 验证项目存在
    project = await projects_dao.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    docs = await douyin_dao.get_potential_users(db, project_id)
    
    return {
        "total": len(docs),
        "users": [_potential_user_out(d) for d in docs],
    }


# ==================== 用户画像 ====================

@router.post("/{project_id}/profiles")
async def list_profiles(project_id: str, body: DouyinProfilesListRequest | None = None):
    """列出用户画像（分页）"""
    if body is None:
        body = DouyinProfilesListRequest(project_id=project_id)
    db = get_db()
    
    # 验证项目存在
    project = await projects_dao.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    docs, total = await douyin_dao.list_profiles(db, project_id, limit=body.limit, skip=body.skip)
    
    return PageResponse.build(
        items=[_profile_out(d) for d in docs],
        total=total,
        page=body.page,
        page_size=body.page_size,
    )


@router.get("/profiles/{profile_id}", response_model=ProfileOut)
async def get_profile(profile_id: str):
    """获取单个用户画像"""
    db = get_db()
    doc = await douyin_dao.get_profile_by_id(db, profile_id)
    if not doc:
        raise HTTPException(status_code=404, detail="用户画像不存在")
    return _profile_out(doc)


@router.delete("/profiles/{profile_id}")
async def delete_profile(profile_id: str):
    """删除用户画像"""
    db = get_db()
    deleted = await douyin_dao.delete_profile(db, profile_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="用户画像不存在")
    return {"ok": True}


# ==================== 截图与视觉分析 ====================

import asyncio
import uuid
from typing import Dict

# SSE 任务管理
_sse_tasks: Dict[str, dict] = {}


def _create_sse_task() -> str:
    task_id = str(uuid.uuid4())[:8]
    _sse_tasks[task_id] = {"cancelled": False, "status": "running"}
    return task_id


def _is_task_cancelled(task_id: str) -> bool:
    return _sse_tasks.get(task_id, {}).get("cancelled", False)


def _cleanup_sse_task(task_id: str):
    if task_id in _sse_tasks:
        del _sse_tasks[task_id]


async def _screenshot_stream(user_url: str, cookie_str: str, max_screenshots: int, task_id: str):
    """截图流式生成器"""
    from crawler_tools.screenshot_tool import screenshot_douyin_profile_stream
    
    try:
        yield f"data: {json.dumps({'type': 'init', 'task_id': task_id}, ensure_ascii=False)}\n\n"
        
        async for item in screenshot_douyin_profile_stream(user_url, cookie_str, max_screenshots):
            if _is_task_cancelled(task_id):
                yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消'}, ensure_ascii=False)}\n\n"
                return
            
            if item.get("type") == "progress":
                yield f"data: {json.dumps({'type': 'progress', 'message': item['message']}, ensure_ascii=False)}\n\n"
            elif item.get("type") == "result":
                yield f"data: {json.dumps({'type': 'result', 'data': item['data']}, ensure_ascii=False)}\n\n"
        
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
    finally:
        _cleanup_sse_task(task_id)


@router.post("/screenshot/stream")
async def stream_screenshot(request: ScreenshotRequest):
    """截图用户主页（SSE 流式）"""
    db = get_db()
    
    # 获取激活的 Cookie
    cookie_doc = await douyin_dao.get_active_cookie(db)
    if not cookie_doc:
        raise HTTPException(status_code=400, detail="没有激活的 Cookie")
    
    cookie_str = cookie_doc.get("cookie_string", "")
    if not cookie_str:
        raise HTTPException(status_code=400, detail="Cookie 为空")
    
    task_id = _create_sse_task()
    
    return StreamingResponse(
        _screenshot_stream(request.user_url, cookie_str, request.max_screenshots, task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _vision_analysis_stream(user_url: str, project_id: str, cookie_str: str, task_id: str):
    """视觉分析流式生成器"""
    from crawler_tools.screenshot_tool import screenshot_douyin_profile_stream
    from openai import OpenAI
    from Sere1nGraph.graph.prompts.loader import load_prompt
    
    try:
        yield f"data: {json.dumps({'type': 'init', 'task_id': task_id, 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
        
        # 阶段1: 截图
        yield f"data: {json.dumps({'type': 'status', 'message': '📸 开始截屏...', 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
        
        screenshots = []
        async for item in screenshot_douyin_profile_stream(user_url, cookie_str, max_screenshots=5):
            if _is_task_cancelled(task_id):
                yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消'}, ensure_ascii=False)}\n\n"
                return
            
            if item.get("type") == "progress":
                yield f"data: {json.dumps({'type': 'status', 'message': item['message'], 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
            elif item.get("type") == "result":
                data = item.get("data", {})
                screenshots = data.get("screenshots", [])
                if data.get("error"):
                    yield f"data: {json.dumps({'type': 'error', 'message': data['error']}, ensure_ascii=False)}\n\n"
                    return
        
        if not screenshots:
            yield f"data: {json.dumps({'type': 'error', 'message': '未获取到截图'}, ensure_ascii=False)}\n\n"
            return
        
        yield f"data: {json.dumps({'type': 'status', 'message': f'✅ 截屏完成，共 {len(screenshots)} 张', 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
        
        # 阶段2: 视觉分析
        yield f"data: {json.dumps({'type': 'status', 'message': '🔍 开始视觉分析...', 'stage': 'vision'}, ensure_ascii=False)}\n\n"
        
        profile_prompt = load_prompt("douyin_profile/douyin_profile")
        
        from api.services.runtime_config import get_runtime_app_config

        runtime_config = await get_runtime_app_config()
        rt = runtime_config.runtime
        client = OpenAI(
            api_key=rt.api_key or "",
            base_url=rt.base_url or "",
        )
        
        content = []
        for screenshot in screenshots:
            base64_data = screenshot.get("base64", "")
            if base64_data:
                data_url = f"data:image/png;base64,{base64_data}"
                content.append({"type": "image_url", "image_url": {"url": data_url}})
        
        content.append({"type": "text", "text": profile_prompt})
        
        # 流式调用视觉模型
        vision_model = rt.models.vision
        stream = client.chat.completions.create(
            model=vision_model,
            messages=[{"role": "user", "content": content}],
            extra_body=disable_thinking_extra_body(
                {"vl_high_resolution_images": True}
            ),
            stream=True,
        )
        
        for chunk in stream:
            if _is_task_cancelled(task_id):
                yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消'}, ensure_ascii=False)}\n\n"
                return
            
            if chunk.choices and chunk.choices[0].delta.content:
                yield f"data: {json.dumps({'type': 'content', 'content': chunk.choices[0].delta.content}, ensure_ascii=False)}\n\n"
        
        yield f"data: {json.dumps({'type': 'done', 'message': '分析完成'}, ensure_ascii=False)}\n\n"
        
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
    finally:
        _cleanup_sse_task(task_id)


@router.post("/vision-analysis/stream")
async def stream_vision_analysis(request: VisionAnalysisRequest):
    """视觉分析（SSE 流式）"""
    db = get_db()
    
    # 验证项目存在
    project = await projects_dao.get_project(db, request.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 获取激活的 Cookie
    cookie_doc = await douyin_dao.get_active_cookie(db)
    if not cookie_doc:
        raise HTTPException(status_code=400, detail="没有激活的 Cookie")
    
    cookie_str = cookie_doc.get("cookie_string", "")
    if not cookie_str:
        raise HTTPException(status_code=400, detail="Cookie 为空")
    
    task_id = _create_sse_task()
    
    return StreamingResponse(
        _vision_analysis_stream(request.user_url, request.project_id, cookie_str, task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sse/cancel/{task_id}")
async def cancel_sse_task(task_id: str):
    """取消 SSE 任务"""
    if task_id not in _sse_tasks:
        return {"success": False, "message": f"任务 {task_id} 不存在或已完成"}
    
    _sse_tasks[task_id]["cancelled"] = True
    return {"success": True, "message": f"任务 {task_id} 已取消"}


# ==================== 人物画像生成 SSE 接口 ====================

async def _profile_generation_stream(
    user_url: str,
    project_id: str,
    keyword: str,
    cookie_str: str,
    task_id: str,
):
    """人物画像生成流式生成器（截屏 → 视觉分析 → Agent 格式化 → 入库）"""
    from crawler_tools.screenshot_tool import screenshot_douyin_profile_stream
    from openai import OpenAI
    from api.utils.json_extract import extract_json_object
    from Sere1nGraph.graph.prompts.loader import load_prompt
    
    try:
        # 解析 sec_uid
        sec_uid = ""
        if "/user/" in user_url:
            sec_uid = user_url.split("/user/")[-1].split("?")[0].split("/")[0]
        
        if not sec_uid:
            yield f"data: {json.dumps({'type': 'error', 'message': 'URL 格式错误，无法解析 sec_uid'}, ensure_ascii=False)}\n\n"
            return
        
        # 发送初始信息
        yield f"data: {json.dumps({'type': 'init', 'task_id': task_id, 'sec_uid': sec_uid, 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
        
        if _is_task_cancelled(task_id):
            yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消', 'stage': 'init'}, ensure_ascii=False)}\n\n"
            return
        
        # ========== 阶段 1: 截屏（流式） ==========
        _sse_tasks[task_id]["stage"] = "screenshot"
        yield f"data: {json.dumps({'type': 'status', 'message': '📸 开始截屏...', 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
        
        screenshots = []
        screenshot_error = None
        
        async for item in screenshot_douyin_profile_stream(
            user_url=user_url,
            cookie_str=cookie_str,
            max_screenshots=3,
            fix_layout=True,
        ):
            if _is_task_cancelled(task_id):
                yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消', 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
                return
            
            if item.get("type") == "progress":
                yield f"data: {json.dumps({'type': 'status', 'message': item['message'], 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
            elif item.get("type") == "result":
                data = item.get("data", {})
                screenshots = data.get("screenshots", [])
                screenshot_error = data.get("error")
        
        if screenshot_error:
            yield f"data: {json.dumps({'type': 'error', 'message': screenshot_error}, ensure_ascii=False)}\n\n"
            return
        
        if not screenshots:
            yield f"data: {json.dumps({'type': 'error', 'message': '未获取到截图'}, ensure_ascii=False)}\n\n"
            return
        
        yield f"data: {json.dumps({'type': 'status', 'message': f'✅ 截屏完成，共 {len(screenshots)} 张', 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
        
        # ========== 阶段 2: 视觉分析（流式） ==========
        if _is_task_cancelled(task_id):
            yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消', 'stage': 'screenshot'}, ensure_ascii=False)}\n\n"
            return
        
        _sse_tasks[task_id]["stage"] = "vision"
        yield f"data: {json.dumps({'type': 'status', 'message': '🔍 开始视觉分析...', 'stage': 'vision'}, ensure_ascii=False)}\n\n"
        
        # 构建视觉分析请求
        profile_prompt = load_prompt("douyin_profile/douyin_profile")
        
        # 添加关键词信息到 prompt
        if keyword:
            profile_prompt = f"搜索关键词: {keyword}\n\n" + profile_prompt
        
        content = []
        for screenshot in screenshots:
            base64_data = screenshot.get("base64", "")
            if base64_data:
                data_url = f"data:image/png;base64,{base64_data}"
                content.append({"type": "image_url", "image_url": {"url": data_url}})
        
        content.append({"type": "text", "text": profile_prompt})
        
        from api.services.runtime_config import get_runtime_app_config

        runtime_config = await get_runtime_app_config()
        rt = runtime_config.runtime
        api_key = rt.api_key or ""
        base_url = rt.base_url or ""
        vision_model = rt.models.vision

        client = OpenAI(api_key=api_key, base_url=base_url)
        
        stream = client.chat.completions.create(
            model=vision_model,
            messages=[{"role": "user", "content": content}],
            extra_body=disable_thinking_extra_body(
                {"vl_high_resolution_images": True}
            ),
            stream=True,
        )
        
        vision_chunks = []
        for chunk in stream:
            if _is_task_cancelled(task_id):
                yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消（视觉分析中断）', 'stage': 'vision'}, ensure_ascii=False)}\n\n"
                return
            
            if chunk.choices and chunk.choices[0].delta.content:
                chunk_content = chunk.choices[0].delta.content
                vision_chunks.append(chunk_content)
                yield f"data: {json.dumps({'type': 'content', 'content': chunk_content}, ensure_ascii=False)}\n\n"
        
        vision_result = "".join(vision_chunks)
        
        yield f"data: {json.dumps({'type': 'status', 'message': '✅ 视觉分析完成', 'stage': 'vision'}, ensure_ascii=False)}\n\n"
        
        # ========== 阶段 3: 解析并入库 ==========
        if _is_task_cancelled(task_id):
            yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消', 'stage': 'vision'}, ensure_ascii=False)}\n\n"
            return
        
        _sse_tasks[task_id]["stage"] = "save"
        yield f"data: {json.dumps({'type': 'status', 'message': '💾 正在保存到数据库...', 'stage': 'save'}, ensure_ascii=False)}\n\n"
        
        # 解析 JSON
        analysis_result = None
        try:
            analysis_result = extract_json_object(vision_result.strip())
        except Exception:
            pass
        
        if not analysis_result:
            yield f"data: {json.dumps({'type': 'error', 'message': '无法解析视觉分析结果为 JSON'}, ensure_ascii=False)}\n\n"
            return
        
        # 保存到数据库
        db = get_db()
        
        # 尝试从打标结果中获取该用户的爬取数据
        crawled_data = None
        tagged_doc = await db[douyin_dao.DOUYIN_TAGGED_RESULTS_COLLECTION].find_one({
            "project_id": project_id,
            "sec_uid": sec_uid,
        })
        if tagged_doc:
            crawled_data = {
                "nickname": tagged_doc.get("nickname"),
                "user_id": tagged_doc.get("user_id"),
                "avatar": tagged_doc.get("avatar"),
                "user_profile_url": tagged_doc.get("user_profile_url"),
                "ip_location": tagged_doc.get("ip_location"),
                "sample_title": tagged_doc.get("title"),
                "tag_reason": tagged_doc.get("tag_reason"),
                "confidence": tagged_doc.get("confidence"),
                "key_evidence": tagged_doc.get("key_evidence", []),
                "company_mentioned": tagged_doc.get("company_mentioned"),
                "position_mentioned": tagged_doc.get("position_mentioned"),
                "priority": tagged_doc.get("priority", 5),
            }
        
        profile = await douyin_dao.save_profile_from_vision(
            db,
            project_id=project_id,
            sec_uid=sec_uid,
            user_profile_url=user_url,
            avatar_url=crawled_data.get("avatar") if crawled_data else None,
            analysis_result=analysis_result,
            crawled_data=crawled_data,
        )
        
        yield f"data: {json.dumps({'type': 'status', 'message': '✅ 入库完成', 'stage': 'save'}, ensure_ascii=False)}\n\n"
        
        # ========== 完成 ==========
        yield f"data: {json.dumps({'type': 'done', 'message': '人物画像生成完成', 'sec_uid': sec_uid, 'profile_id': str(profile.get('_id'))}, ensure_ascii=False)}\n\n"
        
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
    finally:
        _cleanup_sse_task(task_id)


@router.post("/profile/generate/stream")
async def stream_profile_generation(request: ProfileGenerationRequest):
    """
    人物画像生成接口（SSE 流式）
    
    输入用户主页 URL，自动完成：截屏 → 视觉分析 → 入库
    
    SSE 消息格式：
    - {"type": "init", "task_id": "xxx", "sec_uid": "xxx"}
    - {"type": "status", "message": "状态信息", "stage": "screenshot/vision/save"}
    - {"type": "content", "content": "视觉分析内容片段"}
    - {"type": "done", "message": "完成", "sec_uid": "xxx", "profile_id": "xxx"}
    - {"type": "cancelled", "message": "任务已取消"}
    - {"type": "error", "message": "错误信息"}
    """
    db = get_db()
    
    # 验证项目存在
    project = await projects_dao.get_project(db, request.project_id)
    if not project:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': '项目不存在'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")
    
    # 获取激活的 Cookie
    cookie_doc = await douyin_dao.get_active_cookie(db)
    if not cookie_doc:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': '没有激活的 Cookie'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")
    
    cookie_str = cookie_doc.get("cookie_string", "")
    if not cookie_str:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Cookie 为空'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")
    
    task_id = _create_sse_task()
    _sse_tasks[task_id]["stage"] = "init"
    
    return StreamingResponse(
        _profile_generation_stream(
            request.user_url,
            request.project_id,
            request.keyword,
            cookie_str,
            task_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream; charset=utf-8",
        },
    )


# ==================== 流水线 API ====================

@router.post("/{project_id}/pipeline", response_model=PipelineResult)
async def run_pipeline(project_id: str, request: PipelineRequest):
    """
    运行完整流水线
    
    流程：搜索关键词 → 获取作品列表 → Agent 打标 → 筛选潜在员工 → 生成人物画像
    
    Args:
        project_id: 项目 ID
        request.keyword: 搜索关键词（如 "b站实习"）
        request.max_videos: 最大视频数（默认 20）
        request.publish_time: 发布时间筛选（0=不限, 1=一天内, 7=一周内, 180=半年内）
        request.enable_profile: 是否生成人物画像（默认 True）
    
    Returns:
        流水线执行结果
    """
    db = get_db()
    
    # 验证项目存在
    project = await projects_dao.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 运行流水线
    from api.services.douyin_pipeline import run_douyin_pipeline
    from api.services.runtime_config import get_runtime_app_config

    runtime_config = await get_runtime_app_config()
    result = await run_douyin_pipeline(
        db=db,
        app_config=runtime_config,
        project_id=project_id,
        keyword=request.keyword,
        max_videos=request.max_videos,
        publish_time=request.publish_time,
        enable_profile=request.enable_profile,
    )
    
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    
    return PipelineResult(
        project_id=result["project_id"],
        keyword=result["keyword"],
        videos_count=result["videos_count"],
        potential_count=result["potential_count"],
        profiles_count=result["profiles_count"],
        error=result.get("error"),
    )


async def _pipeline_stream(
    project_id: str,
    keyword: str,
    max_videos: int,
    publish_time: int,
    enable_profile: bool,
    task_id: str,
):
    """流水线流式生成器"""
    from api.services.douyin_pipeline import DouyinPipeline
    from api.services.runtime_config import get_runtime_app_config
    
    try:
        yield f"data: {json.dumps({'type': 'init', 'task_id': task_id, 'stage': 'search'}, ensure_ascii=False)}\n\n"
        
        db = get_db()
        runtime_config = await get_runtime_app_config()
        pipeline = DouyinPipeline(db, runtime_config)
        
        # 阶段 1-3: 统一工具流式编排
        _sse_tasks[task_id]["stage"] = "search"
        yield f"data: {json.dumps({'type': 'status', 'message': f'🔍 开始流式采集: {keyword}', 'stage': 'search'}, ensure_ascii=False)}\n\n"
        
        if _is_task_cancelled(task_id):
            yield f"data: {json.dumps({'type': 'cancelled', 'message': '任务已取消'}, ensure_ascii=False)}\n\n"
            return
        
        _sse_tasks[task_id]["stage"] = "tagging"
        yield f"data: {json.dumps({'type': 'status', 'message': '🏷️ 搜索结果将并发打标...', 'stage': 'tagging'}, ensure_ascii=False)}\n\n"

        if enable_profile:
            _sse_tasks[task_id]["stage"] = "profile"
            yield f"data: {json.dumps({'type': 'status', 'message': '👤 命中用户后将生成画像...', 'stage': 'profile'}, ensure_ascii=False)}\n\n"

        result = await pipeline.run_pipeline(
            project_id=project_id,
            keyword=keyword,
            max_videos=max_videos,
            publish_time=publish_time,
            enable_profile=enable_profile,
            task_id=task_id,
        )
        if result.get("error"):
            yield f"data: {json.dumps({'type': 'error', 'message': result['error']}, ensure_ascii=False)}\n\n"
            return
        
        # 完成
        yield f"data: {json.dumps({'type': 'done', 'message': '流水线完成', 'videos_count': result.get('videos_count', 0), 'potential_count': result.get('potential_count', 0), 'profiles_count': result.get('profiles_count', 0)}, ensure_ascii=False)}\n\n"
        
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
    finally:
        _cleanup_sse_task(task_id)
        # 关闭爬虫
        if hasattr(pipeline, '_crawler') and pipeline._crawler:
            await pipeline._crawler.close()


@router.post("/{project_id}/pipeline/stream")
async def stream_pipeline(project_id: str, request: PipelineRequest):
    """
    运行完整流水线（SSE 流式）
    
    流程：搜索关键词 → 获取作品列表 → Agent 打标 → 筛选潜在员工 → 生成人物画像
    
    SSE 消息格式：
    - {"type": "init", "task_id": "xxx", "stage": "search"}
    - {"type": "status", "message": "状态信息", "stage": "search/tagging/profile"}
    - {"type": "done", "message": "完成", "videos_count": N, "potential_count": N, "profiles_count": N}
    - {"type": "cancelled", "message": "任务已取消"}
    - {"type": "error", "message": "错误信息"}
    """
    db = get_db()
    
    # 验证项目存在
    project = await projects_dao.get_project(db, project_id)
    if not project:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': '项目不存在'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")
    
    # 检查 Cookie
    cookie_doc = await douyin_dao.get_active_cookie(db)
    if not cookie_doc:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': '没有激活的 Cookie'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")
    
    task_id = _create_sse_task()
    
    return StreamingResponse(
        _pipeline_stream(
            project_id,
            request.keyword,
            request.max_videos,
            request.publish_time,
            request.enable_profile,
            task_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream; charset=utf-8",
        },
    )
