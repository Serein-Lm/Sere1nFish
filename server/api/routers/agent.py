"""
Agent 路由 - 统一 SSE 接口 + AI 中枢对话留存
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.auth import User, get_current_active_user
from api.db.mongodb import get_db
from api.dao import ai_hub as ai_hub_dao
from api.services.runtime_config import get_runtime_app_config

router = APIRouter(dependencies=[Depends(get_current_active_user)])


# ============ 数据模型 ============

class StreamRequest(BaseModel):
    """统一流式请求"""
    workflow: str = Field(..., description="工作流标识")
    query: str = Field(..., description="用户查询")
    options: dict = Field(default_factory=dict, description="可选参数")
    conversation_id: str = Field(default="", description="可选会话ID，传入则留存本轮对话")


class WorkflowInfo(BaseModel):
    """工作流信息"""
    name: str
    displayName: str
    type: str
    icon: str | None = None
    description: str = ""


class ConversationCreate(BaseModel):
    title: str = Field(default="", description="会话标题")


class ConversationRename(BaseModel):
    title: str = Field(..., description="新标题")


class MessageAppend(BaseModel):
    role: str = Field(..., description="角色 user/assistant")
    content: str = Field(default="", description="消息内容")
    workflow: str = Field(default="", description="所用工作流")
    meta: dict = Field(default_factory=dict, description="额外元信息（如产物链接）")


# ============ 会话归属校验 ============

async def _get_owned_conversation(
    db, conversation_id: str, current_user: User
) -> dict[str, Any]:
    """获取会话并校验归属；不存在返回 404，非本人拥有返回 403。

    历史遗留（owner 为空）的会话视为可访问，避免旧数据不可用。
    """
    conv = await ai_hub_dao.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    owner = getattr(current_user, "username", "") or ""
    conv_owner = conv.get("owner") or ""
    if conv_owner and owner and conv_owner != owner:
        raise HTTPException(status_code=403, detail="无权访问该会话")
    return conv


# ============ 工作流 ============

@router.get("/workflows")
async def get_workflows() -> dict[str, list[WorkflowInfo]]:
    """获取所有可用工作流。"""
    from Sere1nGraph.graph.workflow.executor import list_workflows

    workflows = list_workflows()
    return {"workflows": workflows}


@router.get("/tools")
async def get_tools() -> dict[str, Any]:
    """返回 AI 中枢工具分配与查询接口完整性审计。"""
    from Sere1nGraph.graph.tools.catalog import get_hub_tool_catalog

    app_config = await get_runtime_app_config()
    chrome_configured = "chrome-devtools" in (app_config.mcp_servers or {})
    return get_hub_tool_catalog(chrome_configured=chrome_configured)


def _extract_final_text(event: dict[str, Any], sections: dict[str, str]) -> None:
    """从 SSE 事件中累积最终回复文本（按 section 归并）。"""
    if event.get("event") != "final":
        return
    data = event.get("data") or {}
    section = str(data.get("section") or "_default")
    if section == "summary":
        return
    content = data.get("content")
    if content:
        sections[section] = str(content)


@router.post("/stream")
async def stream(
    request: StreamRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """
    统一 SSE 流式接口。

    请求体:
        - workflow: 工作流标识（browser/xhs/weixin/bid/router/copywriting）
        - query: 用户查询
        - options: 可选参数
        - conversation_id: 可选，传入则留存 user query 与最终回复
    """
    from Sere1nGraph.graph.workflow.executor import execute_stream, workflow_exists

    if not workflow_exists(request.workflow):
        raise HTTPException(
            status_code=400,
            detail=f"未知工作流: {request.workflow}"
        )
    app_config = await get_runtime_app_config()

    conversation_id = (request.conversation_id or "").strip()
    owner = getattr(current_user, "username", "") or ""
    request_options = request.options or {}
    display_query = str(request_options.get("display_query") or request.query)

    # 会话留存：先落库 user query（会话存在且属于当前用户才留存）
    if conversation_id:
        db = get_db()
        conv = await _get_owned_conversation(db, conversation_id, current_user)
        await ai_hub_dao.append_message(
            db,
            conversation_id=conversation_id,
            role="user",
            content=display_query,
            workflow=request.workflow,
        )
        # 首条消息用 query 作为会话标题
        if not conv.get("message_count") and (not conv.get("title") or conv.get("title") == "新会话"):
            await ai_hub_dao.rename_conversation(db, conversation_id, display_query[:40])

    async def event_generator():
        """生成 SSE 事件流，并在结束后落库最终回复。"""
        from core.observability import observation_context
        from api.services.artifact_context import artifact_context

        sections: dict[str, str] = {}
        opts = request_options
        project_id = str(opts.get("project_id") or "").strip()
        references = opts.get("references") if isinstance(opts.get("references"), list) else []
        attribution_id = conversation_id or ""
        artifact_run = None
        try:
            with observation_context(
                project_id=project_id,
                task_id=attribution_id,
                turn_id=attribution_id,
                phase=request.workflow,
                agent=request.workflow,
                task_type=request.workflow,
            ), artifact_context(
                owner=owner,
                is_admin=bool(getattr(current_user, "is_admin", False)),
                conversation_id=conversation_id,
                project_id=project_id,
                channel="web",
                references=references,
            ) as artifact_run:
                async for event in execute_stream(
                    workflow=request.workflow,
                    query=request.query,
                    app_config=app_config,
                    options=request.options,
                ):
                    _extract_final_text(event, sections)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            error_event = {
                "event": "error",
                "id": "",
                "path": "",
                "ts": 0,
                "data": {"error": str(e), "status": "error"}
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
        finally:
            if conversation_id and sections:
                final_text = "\n\n".join(v for v in sections.values() if v)
                if final_text.strip():
                    try:
                        await ai_hub_dao.append_message(
                            get_db(),
                            conversation_id=conversation_id,
                            role="assistant",
                            content=final_text,
                            workflow=request.workflow,
                            meta={
                                "artifacts": list(artifact_run.created) if artifact_run else [],
                                "references": references,
                            },
                        )
                    except Exception:
                        pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ============ AI 中枢对话留存 ============

@router.get("/conversations")
async def list_conversations(
    current_user: Annotated[User, Depends(get_current_active_user)],
    limit: int = 50,
) -> dict[str, Any]:
    """列出当前用户的会话（按更新时间倒序）。"""
    owner = getattr(current_user, "username", "") or ""
    items = await ai_hub_dao.list_conversations(get_db(), owner=owner, limit=limit)
    return {"items": items, "total": len(items)}


@router.post("/conversations")
async def create_conversation(
    req: ConversationCreate,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, Any]:
    """新建会话。"""
    owner = getattr(current_user, "username", "") or ""
    return await ai_hub_dao.create_conversation(get_db(), title=req.title, owner=owner)


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, Any]:
    """获取会话元信息 + 消息列表（仅限本人会话）。"""
    db = get_db()
    conv = await _get_owned_conversation(db, conversation_id, current_user)
    messages = await ai_hub_dao.list_messages(db, conversation_id)
    return {"conversation": conv, "messages": messages}


@router.put("/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: str,
    req: ConversationRename,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, Any]:
    """重命名会话（仅限本人会话）。"""
    db = get_db()
    await _get_owned_conversation(db, conversation_id, current_user)
    doc = await ai_hub_dao.rename_conversation(db, conversation_id, req.title)
    if not doc:
        raise HTTPException(status_code=404, detail="会话不存在")
    return doc


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, Any]:
    """删除会话及其消息（仅限本人会话）。"""
    db = get_db()
    await _get_owned_conversation(db, conversation_id, current_user)
    result = await ai_hub_dao.delete_conversation(db, conversation_id)
    return {"ok": True, **result}


@router.post("/conversations/{conversation_id}/messages")
async def append_message(
    conversation_id: str,
    req: MessageAppend,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, Any]:
    """显式追加一条消息（仅限本人会话）。首条用户消息自动作为会话标题。"""
    db = get_db()
    conv = await _get_owned_conversation(db, conversation_id, current_user)
    doc = await ai_hub_dao.append_message(
        db,
        conversation_id=conversation_id,
        role=req.role,
        content=req.content,
        workflow=req.workflow,
        meta=req.meta,
    )
    if (
        req.role == "user"
        and not conv.get("message_count")
        and (not conv.get("title") or conv.get("title") == "新会话")
        and req.content.strip()
    ):
        await ai_hub_dao.rename_conversation(db, conversation_id, req.content[:40])
    return doc
