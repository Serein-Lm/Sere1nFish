"""
Agent 流式输出处理模块。

职责：
- 统一处理 Agent/Graph 的流式输出（数据处理层）
- 支持 token 级别的流式输出（逐字输出）
- 支持工具调用进度显示
- 为前端提供结构化的数据
- 分离数据处理和输出展示

使用方式：
- stream_mode=["messages", "updates"] 同时获取 token 流和图更新
- messages: 获取 LLM token 级别的流式输出
- updates: 获取工具调用等图执行事件
"""

from __future__ import annotations

from typing import Any, Callable
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage


async def process_agent_stream(
    agent: Any,
    messages: list[Any],
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """
    处理 Agent 的流式输出，收集消息并生成事件（数据处理层）。
    
    使用 stream_mode=["messages", "updates"] 同时获取：
    - messages: LLM token 级别的流式输出（逐字）
    - updates: 图执行更新（工具调用等）
    
    参数：
    - agent: LangChain/LangGraph agent 实例
    - messages: 输入消息列表
    - on_event: 事件回调函数（可选），用于处理每个事件
    
    返回：
    - {"messages": [...]} 格式的结果
    
    事件类型：
    - tool_start: 工具开始调用
    - tool_end: 工具执行完成
    - content_start: 模型开始回复
    - content_chunk: 模型回复内容片段（逐字输出）
    - completed: 流式输出完成
    """
    collected_messages: list[Any] = []
    content_started = False
    seen_tools: set[str] = set()
    pending_tool_calls: dict[str, str] = {}  # tool_call_id -> tool_name
    
    # 使用组合 stream_mode 同时获取 token 流和图更新
    async for mode, chunk in agent.astream(
        {"messages": messages},
        stream_mode=["messages", "updates"]
    ):
        if mode == "messages":
            # messages 模式返回 (message_chunk, metadata) 元组
            msg_chunk, metadata = chunk
            
            # 处理 AIMessageChunk（token 级别的流式输出）
            if isinstance(msg_chunk, AIMessageChunk):
                # 检查是否有内容（排除工具调用的空内容）
                if msg_chunk.content:
                    if not content_started:
                        if on_event:
                            on_event({"type": "content_start"})
                        content_started = True
                    
                    # 发送 token 片段
                    if on_event:
                        on_event({
                            "type": "content_chunk",
                            "content": msg_chunk.content,
                        })
                
                # 检查工具调用（流式工具调用）
                if hasattr(msg_chunk, 'tool_call_chunks') and msg_chunk.tool_call_chunks:
                    for tool_chunk in msg_chunk.tool_call_chunks:
                        tool_name = tool_chunk.get('name')
                        tool_id = tool_chunk.get('id')
                        
                        if tool_name and tool_id and tool_id not in seen_tools:
                            seen_tools.add(tool_id)
                            pending_tool_calls[tool_id] = tool_name
                            if on_event:
                                on_event({
                                    "type": "tool_start",
                                    "tool_name": tool_name,
                                })
        
        elif mode == "updates":
            # updates 模式返回 {node_name: state_update} 字典
            for node_name, state_update in chunk.items():
                if node_name == "__interrupt__":
                    continue
                
                # 处理消息更新
                if isinstance(state_update, dict) and "messages" in state_update:
                    for msg in state_update["messages"]:
                        collected_messages.append(msg)
                        
                        # 处理完整的 AIMessage（包含工具调用）
                        if isinstance(msg, AIMessage):
                            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                                for tool_call in msg.tool_calls:
                                    tool_name = tool_call.get('name', 'unknown')
                                    tool_id = tool_call.get('id', tool_name)
                                    
                                    if tool_id not in seen_tools:
                                        seen_tools.add(tool_id)
                                        pending_tool_calls[tool_id] = tool_name
                                        if on_event:
                                            on_event({
                                                "type": "tool_start",
                                                "tool_name": tool_name,
                                            })
                        
                        # 处理工具执行完成
                        elif isinstance(msg, ToolMessage):
                            tool_call_id = getattr(msg, 'tool_call_id', None)
                            tool_name = pending_tool_calls.pop(tool_call_id, None)
                            if tool_name and on_event:
                                on_event({
                                    "type": "tool_end",
                                    "tool_name": tool_name,
                                })
                            # 重置 content_started，允许工具调用后继续流式输出
                            content_started = False
    
    # 发送完成事件
    if on_event:
        on_event({"type": "completed"})
    
    return {"messages": collected_messages}


def console_event_handler(event: dict[str, Any]) -> None:
    """
    控制台事件处理器（输出层）。
    
    将事件输出到控制台，格式化显示。
    
    参数：
    - event: 事件字典
    """
    event_type = event.get("type")
    
    if event_type == "tool_start":
        tool_name = event.get("tool_name", "unknown")
        print(f"\n🔧 调用工具: {tool_name}", end="", flush=True)
    
    elif event_type == "tool_end":
        print(f" ✅", flush=True)
    
    elif event_type == "content_start":
        print(f"\n\n📝 模型回复:\n", flush=True)
    
    elif event_type == "content_chunk":
        content = event.get("content", "")
        print(content, end="", flush=True)
    
    elif event_type == "completed":
        pass  # 完成事件不需要输出


async def process_agent_stream_console(
    agent: Any,
    messages: list[Any],
) -> dict[str, Any]:
    """
    处理 Agent 的流式输出并输出到控制台（便捷函数）。
    
    参数：
    - agent: LangChain agent 实例
    - messages: 输入消息列表
    
    返回：
    - {"messages": [...]} 格式的结果
    """
    return await process_agent_stream(agent, messages, on_event=console_event_handler)


def api_event_handler(events: list[dict[str, Any]]) -> Callable[[dict[str, Any]], None]:
    """
    API 事件处理器工厂（输出层）。
    
    将事件收集到列表中，用于 API 返回。
    
    参数：
    - events: 事件列表（用于收集事件）
    
    返回：
    - 事件处理函数
    
    示例：
    ```python
    events = []
    result = await process_agent_stream(agent, messages, on_event=api_event_handler(events))
    return {"result": result, "events": events}
    ```
    """
    def handler(event: dict[str, Any]) -> None:
        events.append(event)
    
    return handler


async def process_agent_stream_api(
    agent: Any,
    messages: list[Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    处理 Agent 的流式输出并返回事件列表（用于 API）。
    
    参数：
    - agent: LangChain agent 实例
    - messages: 输入消息列表
    
    返回：
    - (result, events) 元组
      - result: {"messages": [...]} 格式的结果
      - events: 事件列表
    
    示例：
    ```python
    result, events = await process_agent_stream_api(agent, messages)
    return {"result": result, "events": events}
    ```
    """
    events: list[dict[str, Any]] = []
    result = await process_agent_stream(agent, messages, on_event=api_event_handler(events))
    return result, events


from typing import AsyncGenerator


async def process_agent_stream_sse(
    agent: Any,
    messages: list[Any],
) -> AsyncGenerator[dict[str, Any], None]:
    """
    处理 Agent 的流式输出，生成 SSE 事件流。
    
    参数：
    - agent: LangChain/LangGraph agent 实例
    - messages: 输入消息列表
    
    Yields：
    - 事件字典，包含 type 和相关数据
    
    事件类型：
    - tool_start: {"type": "tool_start", "tool_name": "..."}
    - tool_end: {"type": "tool_end", "tool_name": "..."}
    - content: {"type": "content", "data": "..."}
    - done: {"type": "done"}
    - error: {"type": "error", "message": "..."}
    """
    try:
        seen_tools: set[str] = set()
        pending_tool_calls: dict[str, str] = {}
        
        async for mode, chunk in agent.astream(
            {"messages": messages},
            stream_mode=["messages", "updates"]
        ):
            if mode == "messages":
                msg_chunk, metadata = chunk
                
                if isinstance(msg_chunk, AIMessageChunk):
                    # 内容流
                    if msg_chunk.content:
                        if not (hasattr(msg_chunk, 'tool_call_chunks') and msg_chunk.tool_call_chunks):
                            yield {"type": "content", "data": msg_chunk.content}
                    
                    # 工具调用开始
                    if hasattr(msg_chunk, 'tool_call_chunks') and msg_chunk.tool_call_chunks:
                        for tc in msg_chunk.tool_call_chunks:
                            name, tid = tc.get('name'), tc.get('id')
                            if name and tid and tid not in seen_tools:
                                seen_tools.add(tid)
                                pending_tool_calls[tid] = name
                                yield {"type": "tool_start", "tool_name": name}
            
            elif mode == "updates":
                for node_name, state in chunk.items():
                    if node_name == "__interrupt__":
                        continue
                    if isinstance(state, dict) and "messages" in state:
                        for msg in state["messages"]:
                            if isinstance(msg, ToolMessage):
                                tid = getattr(msg, 'tool_call_id', None)
                                name = pending_tool_calls.pop(tid, None)
                                if name:
                                    yield {"type": "tool_end", "tool_name": name}
        
        yield {"type": "done"}
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        yield {"type": "error", "message": str(e)}
