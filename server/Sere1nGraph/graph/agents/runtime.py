"""
Agent 运行时核心模块。

核心职责：
1. 创建 LLM
2. 创建 Agent 节点，统一管理 MCP session 生命周期
3. 确保多个 agent 使用同一 MCP 时，各自有独立 session
4. 支持控制台输出和 SSE 流式输出两种模式
5. 统一超时控制和结构化输出解析（含 LLM 修复重试）
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable, AsyncGenerator, Literal

from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import MessagesState

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from ..config.models import AppConfig
from .streaming import process_agent_stream, console_event_handler, process_agent_stream_sse

from core.logger import get_logger
from core.llm_params import disable_thinking_extra_body

logger = get_logger("agent_runtime")

# 输出模式类型
OutputMode = Literal["silent", "console", "sse"]

# 默认超时（秒）
DEFAULT_AGENT_TIMEOUT = 500
DEFAULT_TOOL_TIMEOUT = 60


def _wrap_tools_with_error_handling(
    tools: list,
    *,
    tool_timeout: int = DEFAULT_TOOL_TIMEOUT,
    max_calls: int = 0,
    call_guard: Callable[
        [str, tuple[Any, ...], dict[str, Any]], str | None
    ] | None = None,
) -> list:
    """
    给每个工具包一层 try/except，异常时返回错误字符串而不是抛异常。
    连续错误超过阈值时抛异常中断 Agent（防止死循环）。
    支持 response_format='content_and_artifact' 的工具（返回元组）。
    
    对于容器级错误（Network.enable timed out 等），连续 3 次就中断，
    因为这类错误重试同一个容器也没用，需要尽快把控制权交给 worker 层做热切换。
    """
    import functools

    # 共享的连续错误计数器
    error_state = {
        "consecutive": 0,
        "max_consecutive": 4,          # 通用错误 4 次中断（原 5 次）
        "container_error_consecutive": 0,
        "max_container_errors": 3,     # 容器级错误 3 次中断
        "calls": 0,
    }

    # 容器级错误关键词（这类错误重试同一容器没意义）
    CONTAINER_ERROR_KEYWORDS = ("Network.enable timed out", "BrokenResource", "ClosedResource", "Could not connect", "server response: 403")

    wrapped = []
    for tool in tools:
        if not hasattr(tool, 'coroutine') and not hasattr(tool, 'func'):
            wrapped.append(tool)
            continue

        is_artifact = getattr(tool, 'response_format', None) == 'content_and_artifact'
        original_coroutine = getattr(tool, 'coroutine', None)
        original_func = getattr(tool, 'func', None)

        if original_coroutine:
            @functools.wraps(original_coroutine)
            async def safe_coroutine(*args, _orig=original_coroutine, _name=tool.name, _art=is_artifact, _es=error_state, **kwargs):
                _es["calls"] += 1
                if max_calls > 0 and _es["calls"] > max_calls:
                    message = (
                        f"MCP 工具调用预算已用完（最多 {max_calls} 次）。"
                        "请停止调用 MCP，并使用已获得的信息直接输出最终结果。"
                    )
                    return (message, "") if _art else message
                if call_guard:
                    blocked = call_guard(_name, args, kwargs)
                    if blocked:
                        return (blocked, "") if _art else blocked
                try:
                    call = _orig(*args, **kwargs)
                    result = (
                        await asyncio.wait_for(call, timeout=tool_timeout)
                        if tool_timeout > 0
                        else await call
                    )
                    _es["consecutive"] = 0  # 成功则重置
                    _es["container_error_consecutive"] = 0
                    return result
                except Exception as e:
                    _es["consecutive"] += 1
                    err_str = str(e)
                    detail = (
                        f"调用超过 {tool_timeout}s"
                        if isinstance(e, asyncio.TimeoutError)
                        else str(e)
                    )
                    err_msg = f"Tool '{_name}' error: {type(e).__name__}: {detail}"
                    logger.debug(f"[tool-wrapper] {err_msg} (连续错误: {_es['consecutive']})")

                    # 容器级错误：更快中断
                    is_container_err = any(k in err_str for k in CONTAINER_ERROR_KEYWORDS)
                    if is_container_err:
                        _es["container_error_consecutive"] += 1
                        if _es["container_error_consecutive"] >= _es["max_container_errors"]:
                            logger.error(
                                f"[tool-wrapper] 连续 {_es['container_error_consecutive']} 次容器级错误"
                                f"（Network.enable timed out），中断 Agent 触发热切换"
                            )
                            raise RuntimeError(
                                f"容器级错误连续 {_es['container_error_consecutive']} 次: {err_msg}"
                            )

                    # 通用连续错误阈值
                    if _es["consecutive"] >= _es["max_consecutive"]:
                        logger.error(f"[tool-wrapper] 连续 {_es['consecutive']} 次工具错误，中断 Agent")
                        raise RuntimeError(f"连续 {_es['consecutive']} 次工具调用失败: {err_msg}")
                    return (err_msg, "") if _art else err_msg
            tool.coroutine = safe_coroutine

        elif original_func:
            @functools.wraps(original_func)
            def safe_func(*args, _orig=original_func, _name=tool.name, _art=is_artifact, _es=error_state, **kwargs):
                _es["calls"] += 1
                if max_calls > 0 and _es["calls"] > max_calls:
                    message = (
                        f"MCP 工具调用预算已用完（最多 {max_calls} 次）。"
                        "请停止调用 MCP，并使用已获得的信息直接输出最终结果。"
                    )
                    return (message, "") if _art else message
                if call_guard:
                    blocked = call_guard(_name, args, kwargs)
                    if blocked:
                        return (blocked, "") if _art else blocked
                try:
                    result = _orig(*args, **kwargs)
                    _es["consecutive"] = 0
                    _es["container_error_consecutive"] = 0
                    return result
                except Exception as e:
                    _es["consecutive"] += 1
                    err_str = str(e)
                    err_msg = f"Tool '{_name}' error: {type(e).__name__}: {e}"
                    logger.debug(f"[tool-wrapper] {err_msg} (连续错误: {_es['consecutive']})")

                    is_container_err = any(k in err_str for k in CONTAINER_ERROR_KEYWORDS)
                    if is_container_err:
                        _es["container_error_consecutive"] += 1
                        if _es["container_error_consecutive"] >= _es["max_container_errors"]:
                            logger.error(
                                f"[tool-wrapper] 连续 {_es['container_error_consecutive']} 次容器级错误，中断 Agent"
                            )
                            raise RuntimeError(
                                f"容器级错误连续 {_es['container_error_consecutive']} 次: {err_msg}"
                            )

                    if _es["consecutive"] >= _es["max_consecutive"]:
                        logger.error(f"[tool-wrapper] 连续 {_es['consecutive']} 次工具错误，中断 Agent")
                        raise RuntimeError(f"连续 {_es['consecutive']} 次工具调用失败: {err_msg}")
                    return (err_msg, "") if _art else err_msg
            tool.func = safe_func

        wrapped.append(tool)
    return wrapped


def create_llm(
    app_config: AppConfig | None = None,
    model_name: str | None = None,
    temperature: float = 0,
    streaming: bool = True,
    extra_body: dict[str, Any] | None = None,
) -> ChatOpenAI:
    """
    基于配置创建 ChatOpenAI。

    自动注入全局 TokenTracker 的 callback，无需业务代码手动添加。
    """
    from ..observability import get_global_tracker

    kwargs: dict[str, Any] = {
        "temperature": temperature,
        "streaming": streaming,
        "stream_usage": streaming,
        "extra_body": disable_thinking_extra_body(extra_body),
    }

    # 注入观测层 callback
    tracker = get_global_tracker()
    kwargs["callbacks"] = [tracker.callback]
    
    if app_config:
        runtime = app_config.runtime
        # 优先用传入的 model_name，否则从 config.runtime.models.default 读取
        if not model_name:
            models_cfg = getattr(runtime, "models", None)
            if models_cfg:
                model_name = getattr(models_cfg, "default", None)
        kwargs["model"] = model_name or "qwen-max"
        
        if getattr(runtime, "base_url", None):
            kwargs["base_url"] = runtime.base_url
        if getattr(runtime, "api_key", None):
            kwargs["api_key"] = runtime.api_key
    else:
        if not model_name:
            raise ValueError("必须提供 app_config 或 model_name")
        kwargs["model"] = model_name

    return ChatOpenAI(**kwargs)


def create_agent_node(
    app_config: AppConfig,
    system_prompt: str,
    builtin_tools: list[Callable[..., Any]] | None = None,
    middleware: list[Any] | None = None,
    mcp_server_name: str | None = None,
    output_mode: OutputMode = "silent",
    streaming: bool = True,
    timeout: int = DEFAULT_AGENT_TIMEOUT,
    mcp_tool_limit: int = 0,
    max_attempts: int = 3,
    mcp_call_guard: Callable[
        [str, tuple[Any, ...], dict[str, Any]], str | None
    ] | None = None,
) -> Callable[[MessagesState], dict[str, Any] | AsyncGenerator[dict[str, Any], None]]:
    """
    创建 Agent 节点函数。
    
    参数：
    - app_config: 应用配置（包含 MCP 配置）
    - system_prompt: 系统提示词
    - builtin_tools: 内置工具列表
    - middleware: 中间件列表
    - mcp_server_name: MCP server 名称（如 "playwright", "xhs"）
    - output_mode: 输出模式
    - streaming: LLM 是否使用流式输出（默认 True，关闭可获得完整 token 统计）
    - timeout: Agent 执行超时秒数（默认从 config.runtime.agent_timeout 读取，fallback 500s，0 表示不限）
    
    返回：
    - output_mode="silent" 或 "console": 返回异步函数，执行后返回 {"messages": [...]}
    - output_mode="sse": 返回异步生成器函数，yield SSE 事件
    """
    from ..tools.mcp import build_mcp_connections
    
    llm = create_llm(app_config, streaming=streaming)
    base_tools = list(builtin_tools or [])
    middleware_list = list(middleware or [])

    # 超时：优先用参数传入的，否则从 config 读取
    if timeout == DEFAULT_AGENT_TIMEOUT:
        config_timeout = getattr(getattr(app_config, "runtime", None), "agent_timeout", 0)
        if config_timeout > 0:
            timeout = config_timeout

    mcp_connections = None
    if mcp_server_name:
        mcp_connections = build_mcp_connections(app_config, server_names=mcp_server_name)

    if output_mode == "sse":
        async def _stream_once(state: MessagesState) -> AsyncGenerator[dict[str, Any], None]:
            all_tools = list(base_tools)

            if mcp_connections and mcp_server_name:
                client = MultiServerMCPClient(mcp_connections)
                transport = mcp_connections[mcp_server_name].get("transport", "stdio")

                if transport == "stdio":
                    async with client.session(mcp_server_name) as session:
                        mcp_tools = await load_mcp_tools(session)
                        mcp_tools = _wrap_tools_with_error_handling(
                            mcp_tools,
                            max_calls=mcp_tool_limit,
                            call_guard=mcp_call_guard,
                        )
                        all_tools.extend(mcp_tools)
                        agent = create_agent(
                            model=llm,
                            tools=all_tools,
                            system_prompt=system_prompt,
                            middleware=middleware_list,
                        )
                        
                        async for event in process_agent_stream_sse(agent, state["messages"]):
                            yield event
                        return
                else:
                    mcp_tools = await client.get_tools()
                    mcp_tools = _wrap_tools_with_error_handling(
                        mcp_tools,
                        max_calls=mcp_tool_limit,
                        call_guard=mcp_call_guard,
                    )
                    all_tools.extend(mcp_tools)

            agent = create_agent(
                model=llm,
                tools=all_tools,
                system_prompt=system_prompt,
                middleware=middleware_list,
            )
            
            async for event in process_agent_stream_sse(agent, state["messages"]):
                yield event

        # SSE 模式同样执行统一超时，断开 MCP session 并释放浏览器资源。
        async def run_agent_sse(state: MessagesState) -> AsyncGenerator[dict[str, Any], None]:
            try:
                if timeout > 0:
                    async with asyncio.timeout(timeout):
                        async for event in _stream_once(state):
                            yield event
                else:
                    async for event in _stream_once(state):
                        yield event
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f"Agent 执行超时（{timeout}s）") from exc

        return run_agent_sse
    
    else:
        # Silent/Console 模式：返回异步函数（含统一重试）
        max_runtime_attempts = max(1, min(int(max_attempts or 1), 5))

        async def run_agent(state: MessagesState) -> dict[str, Any]:

            async def _single_attempt() -> dict[str, Any]:
                """单次执行：建立 MCP session → 加载工具 → 执行 agent"""
                all_tools = list(base_tools)

                async def _execute(tools_inner: list) -> dict[str, Any]:
                    agent = create_agent(
                        model=llm,
                        tools=tools_inner,
                        system_prompt=system_prompt,
                        middleware=middleware_list,
                    )
                    event_handler = console_event_handler if output_mode == "console" else None
                    coro = process_agent_stream(agent, state["messages"], on_event=event_handler)
                    if timeout > 0:
                        return await asyncio.wait_for(coro, timeout=timeout)
                    return await coro

                if mcp_connections and mcp_server_name:
                    client = MultiServerMCPClient(mcp_connections)
                    transport = mcp_connections[mcp_server_name].get("transport", "stdio")

                    if transport == "stdio":
                        async with client.session(mcp_server_name) as session:
                            mcp_tools = await load_mcp_tools(session)
                            mcp_tools = _wrap_tools_with_error_handling(
                                mcp_tools,
                                max_calls=mcp_tool_limit,
                                call_guard=mcp_call_guard,
                            )
                            all_tools.extend(mcp_tools)
                            return await _execute(all_tools)
                    else:
                        mcp_tools = await client.get_tools()
                        mcp_tools = _wrap_tools_with_error_handling(
                            mcp_tools,
                            max_calls=mcp_tool_limit,
                            call_guard=mcp_call_guard,
                        )
                        all_tools.extend(mcp_tools)

                return await _execute(all_tools)

            # 统一重试循环
            last_error = None
            for attempt in range(1, max_runtime_attempts + 1):
                try:
                    return await _single_attempt()
                except (ExceptionGroup, BaseExceptionGroup) as eg:
                    # MCP TaskGroup 异常 — 递归展开子异常，定位根因
                    sub_errors = []
                    needs_restart = False
                    def _flatten(exc, depth=0):
                        nonlocal needs_restart
                        if hasattr(exc, 'exceptions'):
                            for sub in exc.exceptions:
                                _flatten(sub, depth + 1)
                        else:
                            sub_errors.append(f"{'  '*depth}{type(exc).__name__}: {exc}")
                            err_name = type(exc).__name__
                            # 只有连接类异常才需要重启 Chrome
                            if any(k in err_name for k in ("BrokenResource", "ClosedResource", "ConnectionReset")):
                                needs_restart = True
                    _flatten(eg)
                    last_error = "\n".join(sub_errors)
                    logger.warning(
                        f"[runtime] 第 {attempt}/{max_runtime_attempts} 次执行失败 (TaskGroup):\n{last_error}"
                    )
                    # ToolException 等页面操作错误直接重试，不重启 Chrome
                    if needs_restart:
                        await _try_restart_chrome()
                except asyncio.TimeoutError:
                    last_error = f"超时({timeout}s)"
                    logger.warning(f"[runtime] 第 {attempt}/{max_runtime_attempts} 次执行超时")
                except Exception as e:
                    last_error = str(e)
                    err_type = type(e).__name__
                    logger.warning(f"[runtime] 第 {attempt}/{max_runtime_attempts} 次执行失败 ({err_type}): {last_error}")
                    # 连接类异常或超时类异常，尝试重启 Chrome
                    if any(k in str(e) for k in ("BrokenResource", "Broken", "timed out", "Network.enable", "连续", "Could not connect", "403")):
                        await _try_restart_chrome()

                if attempt < max_runtime_attempts:
                    wait = attempt * 2  # 递增等待
                    logger.info(f"[runtime] {wait}s 后重试...")
                    await asyncio.sleep(wait)

            # 所有重试都失败
            raise RuntimeError(
                f"Agent 执行失败（重试 {max_runtime_attempts} 次）: {last_error}"
            )

        async def _try_restart_chrome():
            """
            尝试通过容器 API 重启 Chrome 进程。
            同时上报错误给 DockerProvider，让 worker 层面能感知容器健康状态。
            """
            if not mcp_connections or not mcp_server_name:
                return
            try:
                # 从 MCP 配置中提取容器 API 地址
                cfg = mcp_connections.get(mcp_server_name, {})
                args = cfg.get("args", [])
                ws_endpoint = ""
                for arg in args:
                    if arg.startswith("--wsEndpoint="):
                        ws_endpoint = arg.split("=", 1)[1]
                        break
                if not ws_endpoint:
                    return
                # ws://localhost:8251/cdp-proxy → http://localhost:8251
                import re
                m = re.match(r"ws://([^/]+):(\d+)", ws_endpoint)
                if not m:
                    return
                api_url = f"http://{m.group(1)}:{m.group(2)}"

                # 从数据库配置读取容器 API Token
                headers = {}
                try:
                    from api.services.runtime_config import get_runtime_config_section

                    chrome_cfg = await get_runtime_config_section("chrome_docker")
                    token = chrome_cfg.get("api_token", "")
                    if token:
                        headers["Authorization"] = f"Bearer {token}"
                except Exception:
                    pass

                import httpx
                async with httpx.AsyncClient() as client:
                    resp = await client.post(f"{api_url}/chrome/restart", timeout=10, headers=headers)
                    if resp.status_code == 200:
                        logger.info("[runtime] Chrome 重启成功，等待就绪...")
                        await asyncio.sleep(3)
                    else:
                        logger.warning(f"[runtime] Chrome 重启返回 {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                logger.debug(f"[runtime] Chrome 重启失败（非致命）: {e}")

        return run_agent


# ═══════════════════════════════════════════
# 通用结构化输出解析（含 LLM 修复重试）
# ═══════════════════════════════════════════

def extract_structured_output(result: dict) -> dict | None:
    """
    从 agent 结果中提取结构化 JSON 输出。

    遍历消息列表（从后往前），找到第一个包含有效 JSON 的 AI 消息。
    """
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for msg in reversed(messages):
        content = getattr(msg, "content", None)
        if not isinstance(content, str) or not content.strip():
            continue
        try:
            return _extract_json(content.strip())
        except Exception:
            continue
    return None


def _extract_json(text: str) -> dict:
    """从文本中提取 JSON 对象（支持文本混排）"""
    s = text.strip()

    # 1. 直接解析整个文本
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # 2. 从 markdown code block 中提取（支持未闭合的 code block）
    for pattern in [
        r"```(?:json)?\s*(\{.*?\})\s*```",   # 标准闭合
        r"```(?:json)?\s*(\{.*\})",            # 未闭合（文本末尾）
    ]:
        m = re.search(pattern, s, flags=re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(1))
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass

    # 3. 括号平衡提取：找第一个 { 开始，匹配到对应的 }
    start = s.find("{")
    if start == -1:
        raise ValueError("未找到 JSON 对象")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(s)):
        c = s[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                candidate = s[start:i + 1]
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict):
                        return obj
                except Exception:
                    pass
                break

    # 4. fallback: 贪婪正则（兜底）
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        raise ValueError("未找到 JSON 对象")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("JSON 顶层不是对象")
    return obj


async def extract_with_retry(
    result: dict,
    app_config: AppConfig | None = None,
    max_retries: int = 1,
    system_prompt: str = "",
) -> dict | None:
    """
    提取结构化输出，解析失败时用 LLM 结合完整上下文和原始 system prompt 做修复。

    参数：
    - result: agent 执行结果 {"messages": [...]}
    - app_config: 用于创建修复 LLM
    - system_prompt: 原始 agent 的 system prompt（用于修复时保持一致的输出格式）
    """
    # 先尝试直接解析
    parsed = extract_structured_output(result)
    if parsed:
        return parsed

    if not app_config or max_retries <= 0:
        return None

    # 收集完整对话上下文
    messages = result.get("messages", []) if isinstance(result, dict) else []
    if not messages:
        return None

    # 构建上下文摘要
    context_parts = []
    for msg in messages:
        role = getattr(msg, "type", "") or type(msg).__name__
        content = getattr(msg, "content", "")
        if not content:
            continue
        if role in ("human", "HumanMessage"):
            context_parts.append(f"[用户] {content[:500]}")
        elif role in ("ai", "AIMessage"):
            context_parts.append(f"[AI] {content[:1000]}")
        elif role in ("tool", "ToolMessage"):
            tool_name = getattr(msg, "name", "") or getattr(msg, "tool_call_id", "tool")
            context_parts.append(f"[工具:{tool_name}] {content[:800]}")

    context_text = "\n".join(context_parts[-20:])

    # 找最后一条 AI 消息
    raw_content = None
    for msg in reversed(messages):
        content = getattr(msg, "content", None)
        if isinstance(content, str) and content.strip():
            raw_content = content.strip()
            break

    if not raw_content:
        return None

    logger.warning(f"[runtime] 结构化输出解析失败，尝试 LLM 修复 (内容前200字: {raw_content[:200]})")

    try:
        repair_llm = create_llm(app_config, streaming=False)

        # 用原始 system prompt 作为格式要求，附加上下文让 LLM 修复
        repair_system = system_prompt if system_prompt else ""
        repair_user = (
            "以上是你的任务要求。以下是你之前执行任务时的对话记录和工具调用结果。\n"
            "你已经完成了分析，但最终输出的格式不正确。\n"
            "请根据对话记录中的信息，严格按照任务要求的 JSON 格式重新输出结果。\n"
            "只输出 JSON，不要输出任何其他内容。\n\n"
            f"--- 对话记录 ---\n{context_text}\n\n"
            f"--- 你之前的输出（格式有误）---\n{raw_content}"
        )

        from langchain_core.messages import SystemMessage
        repair_messages = []
        if repair_system:
            repair_messages.append(SystemMessage(content=repair_system))
        repair_messages.append(HumanMessage(content=repair_user))

        from core.observability import observation_context
        with observation_context(phase="structured_repair", agent="structured_repair"):
            repair_result = await repair_llm.ainvoke(repair_messages)
        repaired = getattr(repair_result, "content", "")
        if repaired:
            parsed = _extract_json(repaired.strip())
            if parsed:
                logger.info("[runtime] LLM 修复成功")
                return parsed
    except Exception as e:
        logger.warning(f"[runtime] LLM 修复失败: {e}")

    return None
