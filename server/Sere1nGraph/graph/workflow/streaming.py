"""
Graph 流式输出处理模块

职责：
- 提供事件队列，用于 Graph 内部 Agent 发送事件
- 使用 contextvars 实现请求隔离

注意：
- 统一 SSE 执行入口在 executor.py
- 事件格式定义在 events.py
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any
from datetime import datetime


# ============ 显示名称映射 ============

AGENT_DISPLAY_NAMES = {
    "browser": "🌐 官网采集",
    "xhs": "📱 小红书",
    "weixin": "💬 微信公众号",
    "bid": "📋 招投标",
    # AI 中枢专家子 Agent
    "data": "📊 数据分析",
    "persona": "🎭 人设与联系人",
    "content": "✍️ 话术与产物",
    "payload": "📦 载荷研究与交付",
}

TOOL_DISPLAY_NAMES = {
    "navigate": "浏览器导航",
    "click": "点击元素",
    "fill": "填写表单",
    "screenshot": "截图",
    "evaluate": "执行脚本",
    "xhs_search": "小红书搜索",
    "tianyancha_get_bids": "查询招投标信息",
    "tianyancha_get_domain": "查询企业域名",
    "generate_payload_word": "生成载荷 Word",
    "get_artifact_content": "读取历史产物",
    "list_my_artifacts": "查询历史产物",
}


def _ts() -> str:
    return datetime.now().isoformat()


def _exception_message(exc: BaseException) -> str:
    """提取 ExceptionGroup 的叶子异常，避免只返回 TaskGroup 包装信息。"""
    leaves: list[str] = []

    def _collect(current: BaseException) -> None:
        children = getattr(current, "exceptions", None)
        if children:
            for child in children:
                _collect(child)
            return
        message = str(current).strip() or type(current).__name__
        leaves.append(f"{type(current).__name__}: {message}")

    _collect(exc)
    return "; ".join(dict.fromkeys(leaves))[:800]


# ============ 基于 ContextVar 的事件队列（用户隔离）============

# 每个异步请求自动拥有独立的队列，无需手动管理 session
_event_queue_var: ContextVar[asyncio.Queue[dict[str, Any]] | None] = ContextVar(
    "event_queue", default=None
)


def get_event_queue() -> asyncio.Queue[dict[str, Any]] | None:
    """获取当前请求的事件队列"""
    return _event_queue_var.get()


def set_event_queue(queue: asyncio.Queue[dict[str, Any]] | None):
    """设置当前请求的事件队列"""
    _event_queue_var.set(queue)


async def emit_event(event: dict[str, Any]):
    """发送事件到当前请求的队列"""
    queue = _event_queue_var.get()
    if queue:
        await queue.put(event)


# ============ Agent 流式执行（供 router.py 使用）============

async def run_agent_with_sse(source: str, agent: Any, query: str) -> str:
    """
    执行 Agent 并发送 SSE 事件到队列。
    
    参数：
    - source: Agent 名称（browser, xhs, weixin, bid）
    - agent: SSE 模式的 Agent 函数
    - query: 查询内容
    
    返回：
    - Agent 输出的内容
    """
    from langchain_core.messages import HumanMessage
    
    # 发送 agent_start
    await emit_event({
        "type": "agent_start",
        "agent_name": source,
        "agent_display_name": AGENT_DISPLAY_NAMES.get(source, source),
        "timestamp": _ts()
    })
    
    streamed_content = ""
    final_content = ""
    error_message = None
    
    try:
        async for event in agent({"messages": [HumanMessage(content=query)]}):
            event_type = event.get("type")
            
            if event_type == "tool_start":
                await emit_event({
                    "type": "agent_tool_start",
                    "agent_name": source,
                    "tool_name": event.get("tool_name"),
                    "tool_display_name": TOOL_DISPLAY_NAMES.get(event.get("tool_name", ""), event.get("tool_name", "")),
                    "timestamp": _ts()
                })
            elif event_type == "tool_end":
                await emit_event({
                    "type": "agent_tool_end",
                    "agent_name": source,
                    "tool_name": event.get("tool_name"),
                    "timestamp": _ts()
                })
            elif event_type == "content":
                streamed_content += event.get("data", "")
                await emit_event({
                    "type": "agent_content",
                    "agent_name": source,
                    "data": event.get("data", ""),
                    "timestamp": _ts()
                })
            elif event_type == "result":
                final_content = str(event.get("data") or "").strip()
            elif event_type == "error":
                error_message = event.get("message", "未知错误")
                await emit_event({
                    "type": "agent_error",
                    "agent_name": source,
                    "error": error_message,
                    "timestamp": _ts()
                })
            elif event_type == "done":
                # Agent 正常完成
                pass
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        error_message = _exception_message(e)
        await emit_event({
            "type": "agent_error",
            "agent_name": source,
            "error": error_message,
            "timestamp": _ts()
        })
    
    finally:
        # 无论成功还是失败，都发送 agent_end
        await emit_event({
            "type": "agent_end",
            "agent_name": source,
            "agent_display_name": AGENT_DISPLAY_NAMES.get(source, source),
            "status": "error" if error_message else "success",
            "timestamp": _ts()
        })
    
    return final_content or streamed_content or (
        f"执行出错: {error_message}" if error_message else "未获取到内容"
    )
