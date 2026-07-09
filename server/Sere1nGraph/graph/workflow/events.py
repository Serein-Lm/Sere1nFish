"""
统一 SSE 事件构造模块。

职责：
- 构造统一格式的 SSE 事件
- 管理路径栈（支持树形结构）
- 请求隔离（使用 contextvars）
"""

from __future__ import annotations

import time
from typing import Any
from contextvars import ContextVar


# ============ 上下文变量（请求隔离）============

_workflow_var: ContextVar[str] = ContextVar("workflow", default="")
_agent_var: ContextVar[str | None] = ContextVar("agent", default=None)
_path_stack_var: ContextVar[list[str]] = ContextVar("path_stack")


def _get_path_stack() -> list[str]:
    try:
        return _path_stack_var.get()
    except LookupError:
        stack = ["graph"]
        _path_stack_var.set(stack)
        return stack


# ============ 工具函数 ============

def _ts() -> int:
    """当前时间戳（毫秒）"""
    return int(time.time() * 1000)


def _path() -> str:
    """当前路径"""
    stack = _get_path_stack()
    return ".".join(stack) if stack else "graph"


def _id(path: str) -> str:
    """生成节点 ID"""
    workflow = _workflow_var.get() or "w"
    return f"{workflow}_{path}_{_ts()}"


# ============ 路径管理 ============

def set_workflow(name: str):
    """设置当前工作流并初始化路径栈"""
    _workflow_var.set(name)
    _agent_var.set(None)
    _path_stack_var.set(["graph"])


def set_agent(name: str | None):
    """设置当前 Agent"""
    _agent_var.set(name)


def get_current_agent() -> str | None:
    """获取当前 Agent 名称"""
    try:
        return _agent_var.get()
    except LookupError:
        return None


def set_path(path: str):
    """
    直接设置路径（用于并行节点）。
    
    示例:
        set_path("graph.agents.browser")  # 直接跳到 browser
        set_path("graph.agents.xhs")      # 直接跳到 xhs（与 browser 并行）
    """
    segments = path.split(".")
    _path_stack_var.set(segments)


def push_path(segment: str):
    """压入路径段（用于嵌套节点）"""
    stack = _get_path_stack().copy()
    stack.append(segment)
    _path_stack_var.set(stack)


def pop_path():
    """弹出路径段"""
    stack = _get_path_stack().copy()
    if len(stack) > 1:
        stack.pop()
    _path_stack_var.set(stack)


def reset():
    """重置上下文"""
    _workflow_var.set("")
    _agent_var.set(None)
    _path_stack_var.set(["graph"])


def get_current_workflow() -> str:
    """获取当前工作流名称"""
    return _workflow_var.get()


# ============ 事件构造 ============

def event(event_type: str, data: dict = None, path: str = None, agent: str = None) -> dict:
    """
    构造 SSE 事件（与 mock 格式一致）。
    
    参数:
        event_type: 事件类型（start/update/content/end/error/ping/final）
        data: 事件数据
        path: 节点路径（可选，默认使用当前路径）
        agent: Agent 名称（可选，默认使用当前 Agent）
    
    返回:
        SSE 事件字典
    """
    p = path or _path()
    workflow = _workflow_var.get() or ""
    current_agent = agent if agent is not None else get_current_agent()
    
    result = {
        "event": event_type,
        "id": _id(p),
        "path": p,
        "ts": _ts(),
        "data": data or {},
        "workflow": workflow
    }
    
    # 添加 agent 字段（如果有）
    if current_agent:
        result["agent"] = current_agent
    
    return result


def start(
    node_type: str,
    name: str,
    display_name: str,
    icon: str = None,
    description: str = None,
    **extra
) -> dict:
    """
    构造 start 事件。
    
    参数:
        node_type: 节点类型（graph/phase/agent/tool/subgraph）
        name: 内部名称
        display_name: 显示名称
        icon: 图标（可选）
        description: 描述（可选）
    """
    data = {
        "type": node_type,
        "name": name,
        "displayName": display_name,
    }
    if icon:
        data["icon"] = icon
    if description:
        data["description"] = description
    data.update(extra)
    return event("start", data)


def update(
    description: str = None,
    status: str = None,
    meta: dict = None
) -> dict:
    """构造 update 事件"""
    data = {}
    if description:
        data["description"] = description
    if status:
        data["status"] = status
    if meta:
        data["meta"] = meta
    return event("update", data)


def content(text: str) -> dict:
    """构造 content 事件（流式内容）"""
    return event("content", {"content": text})


def end(
    status: str = "success",
    duration: int = None,
    meta: dict = None
) -> dict:
    """构造 end 事件"""
    data = {"status": status}
    if duration is not None:
        data["duration"] = duration
    if meta:
        data["meta"] = meta
    return event("end", data)


def error(message: str, code: str = None) -> dict:
    """构造 error 事件"""
    data = {"status": "error", "error": message}
    if code:
        data["meta"] = {"code": code}
    return event("error", data)


def final(section: str, content: str, meta: dict = None) -> dict:
    """
    构造 final 事件（阶段性结果）。
    
    参数:
        section: 阶段标识（router/copywriting/summary）
        content: Markdown 格式的内容
        meta: 元数据（可选）
    """
    data = {
        "section": section,
        "content": content
    }
    if meta:
        data["meta"] = meta
    return event("final", data)


def ping() -> dict:
    """构造 ping 事件（心跳）"""
    workflow = _workflow_var.get() or ""
    
    return {
        "event": "ping",
        "id": "",
        "path": "",
        "ts": _ts(),
        "data": {},
        "workflow": workflow
    }
