"""
统一 SSE 执行器。

职责：
- 提供统一的执行入口
- 转换内部事件为统一格式

配置在 registry.py 中管理。
"""

from __future__ import annotations

from typing import AsyncGenerator, Any
from langchain_core.messages import HumanMessage

from . import events
from .streaming import set_event_queue
from .registry import (
    get_agent_factory,
    get_graph_builder,
    get_workflow_meta,
    list_all_workflows,
    workflow_exists as _workflow_exists,
    is_agent,
    is_graph,
)


# ============ 查询接口 ============

def list_workflows() -> list[dict]:
    """
    列出所有可用工作流。
    
    返回:
        [{"name": "browser", "displayName": "🌐 官网采集", "type": "agent", ...}, ...]
    """
    return list_all_workflows()


def workflow_exists(name: str) -> bool:
    """检查工作流是否存在"""
    return _workflow_exists(name)


# ============ 统一执行入口 ============

async def execute_stream(
    workflow: str,
    query: str,
    app_config: Any,
    options: dict = None
) -> AsyncGenerator[dict, None]:
    """
    统一 SSE 执行入口。
    
    参数:
        workflow: 工作流标识（browser/xhs/weixin/bid/router/copywriting）
        query: 用户输入
        app_config: 应用配置
        options: 可选参数
    
    Yields:
        dict: SSE 事件
    """
    options = options or {}
    events.set_workflow(workflow)
    start_ts = events._ts()
    
    try:
        meta = get_workflow_meta(workflow)
        if not meta:
            yield events.error(f"未知工作流: {workflow}", "NOT_FOUND")
            return
        
        # 发送 start 事件
        yield events.start(
            "graph", workflow, meta["displayName"],
            icon=meta.get("icon"),
            description=meta.get("description")
        )
        
        # 根据类型执行
        if is_agent(workflow):
            async for e in _run_agent(workflow, query, app_config):
                yield e
        elif is_graph(workflow):
            async for e in _run_graph(workflow, query, app_config, options):
                yield e
        
        # 发送 end 事件
        duration = events._ts() - start_ts
        yield events.end("success", duration=duration)
    
    except Exception as ex:
        import traceback
        traceback.print_exc()
        yield events.error(str(ex), "EXCEPTION")
        yield events.end("error", duration=events._ts() - start_ts)
    
    finally:
        events.reset()


# ============ Agent 执行 ============

async def _run_agent(
    name: str,
    query: str,
    app_config: Any
) -> AsyncGenerator[dict, None]:
    """执行单 Agent"""
    meta = get_workflow_meta(name)
    factory = get_agent_factory(name)
    
    if not factory or not meta:
        yield events.error(f"Agent 不存在: {name}", "NOT_FOUND")
        return
    
    # 设置 agent 上下文
    events.set_agent(name)
    
    # 设置 agent 路径
    events.set_path(f"graph.agents.{name}")
    start_ts = events._ts()
    yield events.start(
        "agent", name, meta["displayName"],
        icon=meta.get("icon")
    )
    
    # 创建 SSE 模式的 Agent
    agent = await factory(app_config, output_mode="sse")
    tool_index = 0
    final_text_parts: list[str] = []
    
    # 执行并转换事件
    async for e in agent({"messages": [HumanMessage(content=query)]}):
        t = e.get("type")
        
        if t == "tool_start":
            tool_name = e.get("tool_name", "unknown")
            events.set_path(f"graph.agents.{name}.tools.{tool_name}")
            yield events.start("tool", tool_name, tool_name)
        
        elif t == "tool_end":
            yield events.end("success")
            events.set_path(f"graph.agents.{name}")  # 回到 agent 层级
            tool_index += 1
        
        elif t == "content":
            text = e.get("data", "")
            if isinstance(text, list):
                text = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in text
                )
            if text:
                final_text_parts.append(text)
                yield events.content(text)
        
        elif t == "error":
            yield events.error(e.get("message", "未知错误"))
    
    # 结束 agent 节点
    duration = events._ts() - start_ts
    yield events.end("success", duration=duration)
    events.set_path("graph")
    events.set_agent(None)

    # 汇总 agent 输出为最终结果，供前端展示与会话留存
    final_text = "".join(final_text_parts).strip()
    yield events.final(
        section="result",
        content=final_text or "（本次未生成文本内容）",
        meta={"sectionTitle": "📝 回复"}
    )


# ============ Graph 执行 ============

async def _run_graph(
    name: str,
    query: str,
    app_config: Any,
    options: dict
) -> AsyncGenerator[dict, None]:
    """执行 Graph 工作流"""
    import asyncio
    
    result = get_graph_builder(name)
    if not result:
        yield events.error(f"Graph 不存在: {name}", "NOT_FOUND")
        return
    
    builder, initial_state = result
    
    # 构建 Graph
    graph = await builder(app_config)
    
    # 设置事件队列
    queue: asyncio.Queue = asyncio.Queue()
    set_event_queue(queue)
    
    try:
        # 构建初始状态
        state = {**initial_state, **options}
        # 设置 query（不同 Graph 可能用不同字段）
        if "query" in state:
            state["query"] = query
        elif "synthesis_result" in state:
            state["synthesis_result"] = query
        
        # 启动 Graph 执行
        task = asyncio.create_task(graph.ainvoke(state))
        
        # 收集并转换事件
        while not task.done():
            try:
                e = queue.get_nowait()
                async for converted in _convert_graph_event(e):
                    yield converted
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.01)
        
        # 处理剩余事件
        while not queue.empty():
            e = await queue.get()
            async for converted in _convert_graph_event(e):
                yield converted
        
        # 获取结果
        result = task.result()
        
        # 输出最终结果（使用 final 事件）
        if result.get("final_answer"):
            events.set_path("graph")
            yield events.final(
                section="result",
                content=result["final_answer"],
                meta={"sectionTitle": "📝 最终结果"}
            )
        
        if result.get("copywriting"):
            events.set_path("graph")
            yield events.final(
                section="copywriting_result",
                content=result["copywriting"],
                meta={"sectionTitle": "✍️ 文案结果"}
            )
        
        # 发送最终总结
        events.set_path("graph")
        yield events.final(
            section="summary",
            content="工作流执行完成",
            meta={
                "sectionTitle": "📋 执行总结",
                "success": True
            }
        )
    
    finally:
        set_event_queue(None)


async def _convert_graph_event(e: dict) -> AsyncGenerator[dict, None]:
    """转换 Graph 内部事件为统一格式"""
    t = e.get("type", "")
    
    # ============ Router 子图事件 ============
    
    if t == "router_start":
        events.set_path("graph.router")
        yield events.start(
            "subgraph", "router", "📊 Router - 信息采集",
            meta={"phase": "router"}
        )
    
    elif t == "router_end":
        yield events.end("success")
        # 不再发送 router 阶段的 final 卡片：完整结果由 _run_graph 以 section="result" 输出，
        # 避免出现「截断摘要 + 完整结果」两张重复卡片。
        events.set_path("graph")
    
    elif t == "classify_start":
        events.set_path("graph.router.classify")
        yield events.start(
            "node", "classify", "🎯 分析查询",
            description="正在理解用户意图...",
            meta={"subgraph": "router"}
        )
    
    elif t == "classify_end":
        agents = e.get("agents", [])
        yield events.update(
            description=f"已选择: {', '.join(agents)}",
            meta={"agents": agents}
        )
        yield events.end("success")
        events.set_path("graph.router")
    
    elif t == "agent_start":
        name = e.get("agent_name", "")
        display = e.get("agent_display_name", name)
        events.set_agent(name)
        events.set_path(f"graph.router.{name}")  # 在 router 子图下
        yield events.start(
            "agent", name, display,
            meta={"subgraph": "router", "parallel": True}
        )
    
    elif t == "agent_error":
        error_msg = e.get("error", "未知错误")
        yield events.error(error_msg)
    
    elif t == "agent_end":
        status = e.get("status", "success")
        yield events.end(status)
        events.set_path("graph.router")
        events.set_agent(None)
    
    elif t == "agent_content":
        text = e.get("data", "")
        if text:
            yield events.content(text)
    
    elif t == "agent_tool_start":
        agent_name = events.get_current_agent() or ""
        tool = e.get("tool_name", "")
        tool_display = e.get("tool_display_name", tool)
        events.set_path(f"graph.router.{agent_name}.tools.{tool}")
        yield events.start("tool", tool, tool_display)
    
    elif t == "agent_tool_end":
        agent_name = events.get_current_agent() or ""
        yield events.end("success")
        events.set_path(f"graph.router.{agent_name}")
    
    elif t == "synthesis_start":
        events.set_path("graph.router.synthesize")
        yield events.start(
            "node", "synthesize", "📝 汇总结果",
            meta={"subgraph": "router"}
        )
    
    elif t == "synthesis_content":
        text = e.get("data", "")
        if text:
            yield events.content(text)
    
    elif t == "synthesis_end":
        yield events.end("success")
        events.set_path("graph.router")
    
    # ============ Copywriting 子图事件 ============
    
    elif t == "copywriting_start":
        events.set_path("graph.copywriting")
        yield events.start(
            "subgraph", "copywriting", "✍️ Copywriting - 文案生成",
            meta={"phase": "copywriting"}
        )
    
    elif t == "copywriting_end":
        yield events.end("success")
        # 发送 copywriting 阶段的 final 事件
        events.set_path("graph")
        yield events.final(
            section="copywriting",
            content=e.get("summary", "文案生成完成"),
            meta={"phase": "copywriting"}
        )
    
    elif t == "copywriting_scenario_start":
        events.set_path("graph.copywriting.scenario")
        yield events.start(
            "node", "scenario", "🎬 场景伪造",
            meta={"subgraph": "copywriting"}
        )
    
    elif t == "copywriting_scenario_content":
        text = e.get("data", "")
        if text:
            yield events.content(text)
    
    elif t == "copywriting_scenario_end":
        yield events.end("success")
        events.set_path("graph.copywriting")
    
    elif t == "copywriting_script_start":
        events.set_path("graph.copywriting.script")
        yield events.start(
            "node", "script", "💬 话术生成",
            meta={"subgraph": "copywriting"}
        )
    
    elif t == "copywriting_script_content":
        text = e.get("data", "")
        if text:
            yield events.content(text)
    
    elif t == "copywriting_script_end":
        yield events.end("success")
        events.set_path("graph.copywriting")
    
    elif t == "copywriting_objection_start":
        events.set_path("graph.copywriting.objection")
        yield events.start(
            "node", "objection", "🛡️ 质疑应对",
            meta={"subgraph": "copywriting"}
        )
    
    elif t == "copywriting_objection_content":
        text = e.get("data", "")
        if text:
            yield events.content(text)
    
    elif t == "copywriting_objection_end":
        yield events.end("success")
        events.set_path("graph.copywriting")
    
    elif t == "copywriting_finalize_start":
        events.set_path("graph.copywriting.finalize")
        yield events.start(
            "node", "finalize", "📄 整合文案",
            meta={"subgraph": "copywriting"}
        )
    
    elif t == "copywriting_finalize_content":
        text = e.get("data", "")
        if text:
            yield events.content(text)
    
    elif t == "copywriting_finalize_end":
        yield events.end("success")
        events.set_path("graph.copywriting")
