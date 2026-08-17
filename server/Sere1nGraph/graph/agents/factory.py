"""
Agent 构建工厂。

职责：组装配置并调用 runtime.create_agent_node
"""

from __future__ import annotations

from typing import Any, Callable, AsyncGenerator
import asyncio
import uuid
from urllib.parse import urlsplit

from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.tools import tool, ToolRuntime
from langchain_core.messages import SystemMessage

from ..config.models import AppConfig
from ..prompts.loader import load_prompt
from .runtime import (
    OutputMode,
    RequireEvidenceToolMiddleware,
    create_agent_node,
    create_llm,
)
from ..tools.builtin import tianyancha_get_domain, tianyancha_get_bids

BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()
DEFAULT_WEB_TAGGING_MCP_TOOL_LIMIT = 6
PERSONA_RESEARCH_MCP_TOOLS = ("navigate_page", "evaluate_script")
TARGET_RESEARCH_MCP_TOOLS = ("navigate_page", "take_snapshot")
PERSONA_RESEARCH_MCP_TOOL_LIMIT = 24
PERSONA_RESEARCH_TOOL_OUTPUT_MAX_CHARS = 7000
RESEARCH_NAVIGATION_TIMEOUT_MS = 12_000
PERSONA_RESEARCH_SUMMARY_PROMPT = """\
把下面的浏览研究历史压缩为可继续执行的证据账本，最多 1200 个中文字符。
必须保留 mission_id、已经访问的每个来源 URL 与标题、各来源支持的具体事实、
已经覆盖和仍缺失的研究维度，以及下一步应访问的候选 URL。删除页面导航过程、
重复快照、工具状态和推理措辞，不得补写历史中不存在的来源或事实。

研究历史：
{messages}
"""
PERSONA_RESEARCH_RUNTIME_POLICY = """
# 紧凑浏览运行策略

- 运行时只提供 `navigate_page` 与 `evaluate_script`。不得尝试调用快照、截图、点击、表单或下载工具。
- 先用 3 个有差异的检索词打开搜索结果页；每个结果页只读取一次，并提取候选 URL。
- 导航由运行时固定为 12 秒预算；超时后不要重复等待，直接尝试读取已经加载的正文。
- 页面读取统一使用只读 `evaluate_script`；运行时适配器会忽略自定义 DOM 脚本，并固定返回当前 URL、标题、最多 4500 字符正文和 30 条候选链接。
- `evaluate_script` 不得发起 fetch/XHR，不得点击、提交表单、读取 Cookie 或本地存储。
- 单个页面读取返回 `Tool ... error` 时，记录该 URL 并立即切换到不同域名；不得在同一页面重复读取。
- 每个 URL 最多导航两次。维护来源证据账本，不要反复读取已经获得的页面内容。
- 实际读取 8 个跨站点有效正文来源并形成至少 12 条有 URL 关联的具体洞察后，立即输出最终 JSON。
"""
TARGET_RESEARCH_RUNTIME_POLICY = """
# 只读深研浏览策略

- 运行时只提供 `navigate_page` 与 `take_snapshot`。不得尝试调用脚本、截图、点击、表单或下载工具。
- 每轮只能调用一个工具，按“导航 -> 读取 -> 判断下一来源”的顺序执行。
- 先用 3 个有差异的检索词打开搜索结果页；每个结果页只读取一次，并提取候选 URL。
- 页面读取统一使用 `take_snapshot`；快照保留当前 URL、可访问文本与候选链接，不执行模型提供的页面脚本。
- 百科、问答、地图商户、企业目录和内容聚合页不得打开或计入来源；应直接选择官网、主管单位、政府、监管或机构一手页面。
- 单个页面读取返回 `Tool ... error` 时，记录该 URL 并立即切换到不同域名；不得在同一页面重复读取。
- 每个 URL 最多导航两次。维护来源证据账本，不要反复读取已经获得的页面内容。
- 实际读取 8 个跨站点有效正文来源并形成至少 12 条有 URL 关联的具体洞察后，立即输出最终 JSON。
"""
RESEARCH_PAGE_READ_FUNCTION = r"""() => {
  try { window.stop(); } catch (_) {}
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const root = document.querySelector('article, main, [role="main"], #content, .content, .main') || document.body || document.documentElement;
  const ignored = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'TEMPLATE']);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const chunks = [];
  let textLength = 0;
  let visitedTextNodes = 0;
  while (visitedTextNodes < 800 && textLength < 4500) {
    const node = walker.nextNode();
    if (!node) break;
    visitedTextNodes += 1;
    if (ignored.has(node.parentElement?.tagName || '')) continue;
    const value = clean(node.nodeValue);
    if (!value) continue;
    const chunk = value.slice(0, 4500 - textLength);
    chunks.push(chunk);
    textLength += chunk.length + 1;
  }
  const anchors = document.links || [];
  const links = [];
  for (let index = 0; index < Math.min(anchors.length, 200) && links.length < 30; index += 1) {
    const anchor = anchors[index];
    const url = String(anchor.href || '');
    if (!/^https?:\/\//i.test(url)) continue;
    const labelNode = anchor.querySelector('h1, h2, h3') || anchor.firstChild;
    const title = clean(anchor.getAttribute('aria-label') || anchor.title || labelNode?.textContent).slice(0, 160);
    links.push({ title, url });
  }
  return {
    url: location.href,
    title: clean(document.title).slice(0, 300),
    text: chunks.join(' ').slice(0, 4500),
    links,
  };
}"""
RESEARCH_PAGE_READ_SCRIPT = f"({RESEARCH_PAGE_READ_FUNCTION})()"
WEB_TAGGING_RUNTIME_POLICY = (
    "运行时浏览约束覆盖旧版提示词中的次数说明：浏览器工具最多调用 6 次；"
    "允许同站 HTTP 转 HTTPS 重试一次；遇到登录弹窗不得登录，"
    "只尝试关闭一次，弹窗再次出现时不得重复处理。联系入口是短文本菜单时，"
    "按技术群、商务联系、咨询热线的顺序只 hover 最相关入口，并读取工具返回的"
    "新快照中的电话、群号或二维码地址；一旦获得至少一个真实值，立即停止调用"
    "浏览器并输出最终 JSON，不要继续探索其它入口或重复读取同一页面状态。"
)


def _compact_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    marker = "\n...[工具结果已按运行时预算截断]...\n"
    available = max(0, max_chars - len(marker))
    head_size = int(available * 0.8)
    return value[:head_size] + marker + value[-(available - head_size):]


def _compact_persona_research_result(_tool_name: str, result: Any) -> Any:
    """Bound browser text before it becomes durable Agent conversation state."""
    max_chars = PERSONA_RESEARCH_TOOL_OUTPUT_MAX_CHARS

    def _compact_content(content: Any) -> Any:
        if isinstance(content, str):
            return _compact_text(content, max_chars)
        if not isinstance(content, list):
            return content

        remaining = max_chars
        compacted: list[Any] = []
        for block in content:
            if remaining <= 0:
                break
            if isinstance(block, str):
                text = _compact_text(block, remaining)
                compacted.append(text)
                remaining -= len(text)
                continue
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                text = _compact_text(block["text"], remaining)
                compacted.append({**block, "text": text})
                remaining -= len(text)
                continue
            compacted.append(block)
        return compacted

    if isinstance(result, tuple) and len(result) == 2:
        return _compact_content(result[0]), result[1]
    return _compact_content(result)


def _standardize_research_browser_call(
    tool_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Apply bounded navigation and page-read semantics to research calls."""
    if tool_name in {"navigate_page", "navigate"}:
        call_args = tuple(args)
        call_kwargs = dict(kwargs)
        if tool_name == "navigate":
            return call_args, call_kwargs
        if call_args and isinstance(call_args[0], dict):
            payload = dict(call_args[0])
            payload["timeout"] = RESEARCH_NAVIGATION_TIMEOUT_MS
            return (payload, *call_args[1:]), call_kwargs
        call_kwargs["timeout"] = RESEARCH_NAVIGATION_TIMEOUT_MS
        return call_args, call_kwargs

    if tool_name not in {"evaluate_script", "evaluate"}:
        return args, kwargs

    call_args = tuple(args)
    call_kwargs = dict(kwargs)
    if tool_name == "evaluate":
        if call_args and isinstance(call_args[0], dict):
            payload = dict(call_args[0])
            payload.clear()
            payload["script"] = RESEARCH_PAGE_READ_SCRIPT
            return (payload, *call_args[1:]), call_kwargs
        if call_args:
            return (RESEARCH_PAGE_READ_SCRIPT, *call_args[1:]), call_kwargs
        return (), {"script": RESEARCH_PAGE_READ_SCRIPT}

    call_kwargs.pop("args", None)
    if "function" in call_kwargs or not call_args:
        call_kwargs["function"] = RESEARCH_PAGE_READ_FUNCTION
        return call_args, call_kwargs
    if isinstance(call_args[0], dict):
        payload = dict(call_args[0])
        payload.pop("args", None)
        payload["function"] = RESEARCH_PAGE_READ_FUNCTION
        return (payload, *call_args[1:]), call_kwargs
    return (RESEARCH_PAGE_READ_FUNCTION, *call_args[1:]), call_kwargs


def _build_persona_research_guard(
) -> Callable[[str, tuple[Any, ...], dict[str, Any]], str | None]:
    navigation_counts: dict[str, int] = {}
    navigation_pending_read = False

    def _argument(
        name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        value = kwargs.get(name)
        if value is None and args and isinstance(args[0], dict):
            value = args[0].get(name)
        return str(value or "").strip()

    def _guard(
        tool_name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str | None:
        nonlocal navigation_pending_read
        if tool_name in {"navigate_page", "navigate"}:
            if navigation_pending_read:
                return "当前页面尚未读取；请先调用页面读取工具，再导航到下一来源。"
            url = _argument("url", args, kwargs)
            if not url:
                return None
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"}:
                return "只允许导航到公开 HTTP(S) 页面。"
            normalized = parsed._replace(fragment="").geturl()
            navigation_counts[normalized] = navigation_counts.get(normalized, 0) + 1
            if navigation_counts[normalized] > 2:
                return "该 URL 已达到两次导航上限，请使用现有证据或更换来源。"
            navigation_pending_read = True
            return None

        if tool_name == "take_snapshot":
            navigation_pending_read = False
            return None

        if tool_name in {"evaluate_script", "evaluate"}:
            script = _argument(
                "script" if tool_name == "evaluate" else "function",
                args,
                kwargs,
            )
            lowered = script.lower().replace(" ", "")
            unsafe_markers = (
                "fetch(",
                "xmlhttprequest",
                "newpromise",
                "setinterval(",
                ".click(",
                ".submit(",
                "document.cookie",
                "localstorage",
                "sessionstorage",
            )
            if len(script) > 3000 or any(marker in lowered for marker in unsafe_markers):
                return "脚本已被阻止：只允许紧凑、只读的 DOM 文本与链接提取。"
            navigation_pending_read = False
        return None

    return _guard


def _build_same_site_navigation_guard(
    allowed_url: str,
) -> Callable[[str, tuple[Any, ...], dict[str, Any]], str | None] | None:
    """限制浏览器 Agent 主动导航到目标站点之外。"""
    allowed_host = (urlsplit(allowed_url).hostname or "").lower().rstrip(".")
    if not allowed_host:
        return None

    def _guard(
        tool_name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str | None:
        if tool_name not in {"navigate_page", "new_page"}:
            return None
        candidate = kwargs.get("url")
        if not candidate and args and isinstance(args[0], dict):
            candidate = args[0].get("url")
        candidate = str(candidate or "").strip()
        if not candidate or candidate.startswith(("/", "about:blank")):
            return None
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            return None
        same_site = (
            host == allowed_host
            or host.endswith(f".{allowed_host}")
            or allowed_host.endswith(f".{host}")
        )
        if same_site:
            return None
        return (
            f"导航已阻止：{host} 不属于目标站点 {allowed_host}。"
            "请停止外部检索，基于当前页面和上游事实证据输出 JSON。"
        )

    return _guard


def _register_background_task(task: asyncio.Task[Any]) -> None:
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(lambda t: BACKGROUND_TASKS.discard(t))


async def create_xhs_agent(
    app_config: AppConfig,
    server_name: str = "xhs",
    output_mode: OutputMode = "silent",
) -> Callable:
    """创建小红书信息收集 Agent。"""
    return create_agent_node(
        app_config=app_config,
        model_workload="collection",
        system_prompt=load_prompt("xhs_collect/xhs_collect"),
        builtin_tools=[],
        middleware=[
            SummarizationMiddleware(
                model=create_llm(app_config, workload="collection"),
                trigger=("tokens", 2000),
                keep=("messages", 10),
            ),
        ],
        mcp_server_name=server_name,
        output_mode=output_mode,
    )


async def create_browser_agent(
    app_config: AppConfig,
    server_name: str = "chrome-devtools",
    output_mode: OutputMode = "silent",
) -> Callable:
    """基于 MCP 创建浏览器信息采集 Agent。"""
    return await create_web_tagging_agent(
        app_config=app_config,
        server_name=server_name,
        output_mode=output_mode,
    )


async def create_web_tagging_agent(
    app_config: AppConfig,
    server_name: str = "chrome-devtools",
    output_mode: OutputMode = "silent",
    streaming: bool = True,
    allowed_navigation_url: str = "",
    timeout: int = 90,
    mcp_tool_limit: int = DEFAULT_WEB_TAGGING_MCP_TOOL_LIMIT,
    max_attempts: int = 1,
) -> Callable:
    """官网社工打标 Agent（Web Tagging Agent）。"""
    model_run_limit = (
        max(6, min(12, int(mcp_tool_limit) + 4))
        if int(mcp_tool_limit) > 0
        else 10
    )
    return create_agent_node(
        app_config=app_config,
        model_workload="collection",
        system_prompt=(
            f"{load_prompt('web_tagging/web_tagging')}\n\n"
            f"# 运行时策略\n\n{WEB_TAGGING_RUNTIME_POLICY}"
        ),
        mcp_server_name=server_name,
        output_mode=output_mode,
        streaming=streaming,
        middleware=[
            ModelCallLimitMiddleware(
                run_limit=model_run_limit,
                exit_behavior="end",
            )
        ],
        timeout=timeout,
        mcp_tool_limit=mcp_tool_limit,
        max_attempts=max_attempts,
        mcp_call_guard=_build_same_site_navigation_guard(allowed_navigation_url),
    )


async def create_weixin_search_agent(
    app_config: AppConfig,
    server_name: str = "chrome-devtools",
    output_mode: OutputMode = "silent",
) -> Callable:
    """创建微信公众号搜索 Agent，用于搜索招投标相关信息。"""
    return create_agent_node(
        app_config=app_config,
        model_workload="collection",
        system_prompt=load_prompt("weixin_search/weixin_search"),
        builtin_tools=[],
        middleware=[
            SummarizationMiddleware(
                model=create_llm(app_config, workload="collection"),
                trigger=("tokens", 2000),
                keep=("messages", 5),
            ),
        ],
        mcp_server_name=server_name,
        output_mode=output_mode,
    )
async def create_bid_collect_agent(
    app_config: AppConfig,
    server_name: str = "chrome-devtools",
    output_mode: OutputMode = "silent",
) -> Callable:
    """
    创建招投标信息采集 Agent。
    ```
    """
    return create_agent_node(
        app_config=app_config,
        model_workload="collection",
        system_prompt=load_prompt("bid_collect/bid_collect"),
        builtin_tools=[tianyancha_get_bids],
        middleware=[
            SummarizationMiddleware(
                model=create_llm(app_config, workload="collection"),
                trigger=("tokens", 3000),
                keep=("messages", 8),
            ),
        ],
        mcp_server_name=server_name,
        output_mode=output_mode,
    )


async def create_company_normalize_agent(
    app_config: AppConfig,
    server_name: str = "chrome-devtools",
    output_mode: OutputMode = "silent",
) -> Callable:
    """
    创建公司名规范化 Agent。

    能力：AI 浏览器搜索（cn.bing.com）+ 天眼查 ICP 交叉验证，
    输出规范化公司全称与根域名（结构化 JSON，由 CompanyNormalization 约束）。
    复用 create_agent_node + chrome-devtools MCP，不另起浏览器。
    """
    return create_agent_node(
        app_config=app_config,
        model_workload="collection",
        system_prompt=load_prompt("company_normalize/company_normalize"),
        builtin_tools=[tianyancha_get_domain],
        middleware=[
            SummarizationMiddleware(
                model=create_llm(app_config, workload="collection"),
                trigger=("tokens", 3000),
                keep=("messages", 8),
            ),
        ],
        mcp_server_name=server_name,
        output_mode=output_mode,
    )


async def create_target_research_agent(
    app_config: AppConfig,
    server_name: str = "chrome-devtools",
    output_mode: OutputMode = "silent",
    mcp_result_observer: Callable[[str, Any], None] | None = None,
) -> Callable:
    """创建机构 Target 深研 Agent；仅使用项目 Chrome，不另起浏览器。"""
    return create_agent_node(
        app_config=app_config,
        model_workload="collection",
        system_prompt=(
            f"{load_prompt('target_research/target_research')}\n\n"
            f"{TARGET_RESEARCH_RUNTIME_POLICY}"
        ),
        builtin_tools=[],
        middleware=[
            RequireEvidenceToolMiddleware(),
            ModelCallLimitMiddleware(
                run_limit=24,
                exit_behavior="end",
            ),
            SummarizationMiddleware(
                model=create_llm(app_config, workload="collection"),
                trigger=("tokens", 10000),
                keep=("messages", 2),
                summary_prompt=PERSONA_RESEARCH_SUMMARY_PROMPT,
                trim_tokens_to_summarize=7000,
            ),
        ],
        mcp_server_name=server_name,
        mcp_server_profile="readonly_research",
        parallel_tool_calls=False,
        mcp_tool_names=TARGET_RESEARCH_MCP_TOOLS,
        mcp_tool_limit=20,
        mcp_tool_timeout=20,
        # MCP 工具超时后底层请求可能继续占用会话，立即交给外层换容器；
        # 页面级错误仍由官方工具转为文本，Agent 可正常切换来源。
        mcp_error_limit=1,
        timeout=900,
        # Browser transport retries are owned by target_research so every
        # retry gets a different managed Chrome container.
        max_attempts=1,
        output_mode=output_mode,
        mcp_call_transform=_standardize_research_browser_call,
        mcp_call_guard=_build_persona_research_guard(),
        mcp_result_transform=_compact_persona_research_result,
        mcp_result_observer=mcp_result_observer,
    )


async def create_xhs_note_tagging_agent(
    app_config: AppConfig,
    output_mode: OutputMode = "silent",
) -> Callable:
    """创建小红书笔记打标 Agent，用于分析搜索结果中的社工攻击面。"""
    return create_agent_node(
        app_config=app_config,
        model_workload="collection",
        system_prompt=load_prompt("xhs_note_tagging/xhs_note_tagging"),
        builtin_tools=[],
        middleware=None,
        mcp_server_name=None,
        output_mode=output_mode,
    )


async def create_xhs_detail_tagging_agent(
    app_config: AppConfig,
    output_mode: OutputMode = "silent",
) -> Callable:
    """创建小红书笔记详情打标 Agent，用于深度分析笔记内容。"""
    return create_agent_node(
        app_config=app_config,
        model_workload="collection",
        system_prompt=load_prompt("xhs_detail_tagging/xhs_detail_tagging"),
        builtin_tools=[],
        middleware=None,
        mcp_server_name=None,
        output_mode=output_mode,
    )


async def create_xhs_profile_agent(
    app_config: AppConfig,
    output_mode: OutputMode = "silent",
) -> Callable:
    """创建小红书人物画像 Agent，用于基于笔记生成用户画像。"""
    return create_agent_node(
        app_config=app_config,
        model_workload="collection",
        system_prompt=load_prompt("xhs_profile/xhs_profile"),
        builtin_tools=[],
        middleware=None,
        mcp_server_name=None,
        output_mode=output_mode,
    )


async def create_douyin_tagging_agent(
    app_config: AppConfig,
    output_mode: OutputMode = "silent",
) -> Callable:
    """创建抖音打标 Agent，用于分析搜索结果中的社工攻击面。"""
    return create_agent_node(
        app_config=app_config,
        model_workload="collection",
        system_prompt=load_prompt("douyin_profile/douyin_tagging"),
        builtin_tools=[],
        middleware=None,
        mcp_server_name=None,
        output_mode=output_mode,
    )


async def create_douyin_profile_agent(
    app_config: AppConfig,
    output_mode: OutputMode = "silent",
) -> Callable:
    """创建抖音人物画像 Agent，用于基于视觉分析生成用户画像。"""
    return create_agent_node(
        app_config=app_config,
        model_workload="collection",
        system_prompt=load_prompt("douyin_profile/douyin_profile"),
        builtin_tools=[],
        middleware=None,
        mcp_server_name=None,
        output_mode=output_mode,
    )


async def create_customer_service_agent(
    app_config: AppConfig,
    server_name: str = "chrome-devtools",
    output_mode: OutputMode = "silent",
) -> Callable:
    """基于 MCP 创建在线客服对话 Agent。"""
    return create_agent_node(
        app_config=app_config,
        system_prompt=load_prompt("browser_chat/browser_chat"),
        builtin_tools=[],
        middleware=None,
        mcp_server_name=server_name,
        output_mode=output_mode,
    )


def make_trigger_customer_service_tool(app_config: AppConfig) -> Callable[..., Any]:
    """返回触发客服流程的 tool。"""

    @tool("trigger_customer_service", description="启动独立的客服对话流程。")
    async def trigger_customer_service(context: str, runtime: ToolRuntime) -> str:
        """触发客服流程，后台异步执行。"""
        task_id = uuid.uuid4().hex
        
        async def _run_flow() -> str:
            cs_agent = await create_customer_service_agent(app_config)
            messages = [SystemMessage(content=f"客服上下文：\n{context}")] if context.strip() else []
            result = await cs_agent({"messages": messages})
            
            for msg in reversed(result.get("messages", [])):
                if isinstance(getattr(msg, "content", None), str) and msg.content.strip():
                    return msg.content.strip()
            return "客服流程已完成。"

        task = asyncio.create_task(_run_flow(), name=f"cs:{task_id}")
        _register_background_task(task)
        return f"客服流程已启动 (ID: {task_id})"

    return trigger_customer_service


async def create_hub_specialist_agent(
    app_config: AppConfig,
    *,
    system_prompt: str,
    tools: list[Callable[..., Any]],
    mcp_server_name: str | None = None,
    output_mode: OutputMode = "sse",
    summary_trigger_tokens: int = 3000,
    summary_keep_messages: int = 6,
    summary_prompt: str | None = None,
    summary_trim_tokens: int | None = 4000,
    timeout: int = 500,
    mcp_tool_limit: int = 0,
) -> Callable:
    """
    创建 AI 中枢的「专家子 Agent」（供 hub 路由图并行分发）。

    每个子 Agent 只携带一个内聚的只读工具组，并叠加 SummarizationMiddleware，
    让单个子 Agent 的上下文保持有界，避免单一「大而全」Agent 上下文爆炸。
    工具的调用与顺序由子 Agent 自主决定（ReAct，无固定编排）。
    """
    from .runtime import RequireEvidenceToolMiddleware

    return create_agent_node(
        app_config=app_config,
        model_workload="collection",
        system_prompt=system_prompt,
        builtin_tools=tools,
        middleware=[
            RequireEvidenceToolMiddleware(),
            SummarizationMiddleware(
                model=create_llm(app_config, workload="collection"),
                trigger=("tokens", summary_trigger_tokens),
                keep=("messages", summary_keep_messages),
                trim_tokens_to_summarize=summary_trim_tokens,
                **({"summary_prompt": summary_prompt} if summary_prompt else {}),
            ),
        ],
        mcp_server_name=mcp_server_name,
        output_mode=output_mode,
        timeout=timeout,
        mcp_tool_limit=mcp_tool_limit,
    )


async def create_assistant_agent(
    app_config: AppConfig,
    output_mode: OutputMode = "silent",
) -> Callable:
    """
    创建 AI 中枢「个人助手」ReAct Agent（无固定编排）。

    定位：数据库查询 + 路由分发 + 生成建议 的综合个人助手。
    携带全部只读数据查询工具（项目/任务/finding/人设/联系人/资产/会话/全局统计），
    以及技能加载与 Word 产物工具；由 Agent 自主决定调用哪些工具与顺序。
    """
    from ..tools.skill_tools import SKILL_TOOLS
    from ..tools.persona_tools import PERSONA_TOOLS
    from ..tools.word_tools import WORD_TOOLS
    from ..tools.context_tools import CONTEXT_TOOLS
    from ..tools.analysis_tools import ANALYSIS_TOOLS
    from ..tools.read_tools import READ_TOOLS
    from ..tools.artifact_tools import ARTIFACT_QUERY_TOOLS
    from ..tools.project_data_tools import PROJECT_DATA_TOOLS

    return create_agent_node(
        app_config=app_config,
        system_prompt=load_prompt("assistant/assistant"),
        builtin_tools=(
            SKILL_TOOLS + PERSONA_TOOLS + WORD_TOOLS
            + CONTEXT_TOOLS + ANALYSIS_TOOLS + READ_TOOLS
            + PROJECT_DATA_TOOLS + ARTIFACT_QUERY_TOOLS
        ),
        middleware=None,
        mcp_server_name=None,
        output_mode=output_mode,
    )


async def create_copywriting_agent(
    app_config: AppConfig,
    output_mode: OutputMode = "silent",
) -> Callable:
    """
    创建话术生成 ReAct Agent。

    Agent 拥有 skill tools（list/load/reference），自主决定加载哪些 skill。
    并携带人设库检索工具（search_personas/get_persona），可先拉取真实人物背景再生成话术。
    System prompt 包含 step-by-step 的思考框架（场景→话术→质疑→输出）。
    """
    from ..tools.skill_tools import SKILL_TOOLS
    from ..tools.persona_tools import PERSONA_TOOLS
    from ..tools.word_tools import WORD_TOOLS
    from ..tools.context_tools import CONTEXT_TOOLS
    from ..tools.analysis_tools import ANALYSIS_TOOLS

    return create_agent_node(
        app_config=app_config,
        system_prompt=load_prompt("copywriting/copywriting"),
        builtin_tools=(
            SKILL_TOOLS
            + PERSONA_TOOLS
            + WORD_TOOLS
            + CONTEXT_TOOLS
            + ANALYSIS_TOOLS
        ),
        middleware=[
            ToolCallLimitMiddleware(run_limit=1, exit_behavior="continue"),
            ModelCallLimitMiddleware(run_limit=3, exit_behavior="end"),
        ],
        mcp_server_name=None,
        output_mode=output_mode,
        timeout=120,
        max_attempts=1,
    )


async def create_profile_copywriting_agent(
    app_config: AppConfig,
    output_mode: OutputMode = "silent",
) -> Callable:
    """
    创建画像→话术生成 Agent。

    基于小红书人物画像，为每个人物生成多套针对性话术。
    复用 skill tools 与人设库检索工具，但使用专门的 profile_copywriting prompt。
    """
    from ..tools.skill_tools import SKILL_TOOLS
    from ..tools.persona_tools import PERSONA_TOOLS
    from ..tools.word_tools import WORD_TOOLS
    from ..tools.context_tools import CONTEXT_TOOLS
    from ..tools.analysis_tools import ANALYSIS_TOOLS

    return create_agent_node(
        app_config=app_config,
        model_workload="collection",
        system_prompt=load_prompt("profile_copywriting/profile_copywriting"),
        builtin_tools=SKILL_TOOLS + PERSONA_TOOLS + WORD_TOOLS + CONTEXT_TOOLS + ANALYSIS_TOOLS,
        middleware=None,
        mcp_server_name=None,
        output_mode=output_mode,
    )


async def create_persona_research_agent(
    app_config: AppConfig,
    server_name: str = "chrome-devtools",
    output_mode: OutputMode = "silent",
) -> Callable:
    """
    创建虚构人设背景研究 Agent。

    浏览器只研究行业、岗位与生活阶段的通用背景，不采集真实自然人身份；
    输出 PersonaResearchReport，后续由文本模型综合虚构人物原型。
    """
    return create_agent_node(
        app_config=app_config,
        model_workload="collection",
        system_prompt=(
            f"{load_prompt('persona_research/persona_research')}\n\n"
            f"{PERSONA_RESEARCH_RUNTIME_POLICY}"
        ),
        builtin_tools=[],
        middleware=[
            RequireEvidenceToolMiddleware(),
            ModelCallLimitMiddleware(
                run_limit=28,
                exit_behavior="end",
            ),
            SummarizationMiddleware(
                model=create_llm(app_config, workload="collection"),
                trigger=("tokens", 7500),
                keep=("messages", 2),
                summary_prompt=PERSONA_RESEARCH_SUMMARY_PROMPT,
                trim_tokens_to_summarize=5000,
            ),
        ],
        mcp_server_name=server_name,
        output_mode=output_mode,
        timeout=420,
        mcp_tool_limit=PERSONA_RESEARCH_MCP_TOOL_LIMIT,
        max_attempts=1,
        mcp_call_transform=_standardize_research_browser_call,
        mcp_call_guard=_build_persona_research_guard(),
        mcp_tool_names=PERSONA_RESEARCH_MCP_TOOLS,
        mcp_result_transform=_compact_persona_research_result,
    )


async def create_persona_collect_agent(
    app_config: AppConfig,
    server_name: str = "chrome-devtools",
    output_mode: OutputMode = "silent",
) -> Callable:
    """Backward-compatible alias for the background research Agent."""
    return await create_persona_research_agent(
        app_config,
        server_name=server_name,
        output_mode=output_mode,
    )
