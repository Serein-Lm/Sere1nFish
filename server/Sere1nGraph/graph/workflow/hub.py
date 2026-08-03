"""
AI 中枢路由工作流（复用 router.py 的 LangGraph 路由架构）。

设计动机：
- 单一「大而全」ReAct Agent 携带全部工具时，多轮工具调用会让上下文快速膨胀。
- 复用既有 router 的「分类 → 并行分发 → 汇总」骨架，把工具按领域拆到多个
  「专家子 Agent」，每个子 Agent 只携带内聚的只读工具组并叠加 SummarizationMiddleware，
  使单个子 Agent 上下文有界；子 Agent 内部的工具选择/顺序仍由其自主决定（ReAct，无固定编排）。

拓扑：START → classify → {data | persona | content | payload | osint}（Send 并行）→ synthesize → END

事件复用 router 既有词汇（router_start/classify_*/agent_*/synthesis_*），
因此 executor._convert_graph_event 无需改动即可渲染思维链。
"""

from __future__ import annotations

import operator
import re
import uuid
from typing import Any, Annotated, Literal
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from pydantic import BaseModel, Field

from ..agents.factory import create_hub_specialist_agent
from ..agents.runtime import REQUIRE_EVIDENCE_TOOL_MARKER, create_llm
from ..prompts.loader import load_prompt

HubTarget = Literal["data", "persona", "content", "payload", "osint"]
_PUBLIC_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_OSINT_INTENT_RE = re.compile(
    r"(?:\bOSINT\b|开源情报|背景调查|深入了解|仔细了解|"
    r"(?:公网|互联网|网上|公开).{0,12}(?:信息|资料|检索|搜索|调研|研究|调查))",
    re.IGNORECASE,
)
_PERSONA_COLLECTION_INTENT_RE = re.compile(
    r"(?:(?:主动|联网|公网|网上|持续|重新|补充|新增|创建|生成|采集|收集|爬取|研究)"
    r".{0,32}(?:人设|虚构画像)|(?:人设|虚构画像).{0,32}"
    r"(?:主动|联网|公网|网上|持续|重新|补充|新增|创建|生成|采集|收集|爬取|研究))",
    re.IGNORECASE,
)
_CURRENT_REQUEST_MARKER = "【本轮用户请求】"
_DIRECT_URL_AGENT_TIMEOUT_SECONDS = 90
_DIRECT_URL_MCP_TOOL_LIMIT = 4
_OSINT_AGENT_TIMEOUT_SECONDS = 420
_OSINT_MCP_TOOL_LIMIT = 20
_OSINT_SUMMARY_PROMPT = """你正在压缩一项尚未完成的人物公开情报研究。必须保留后续 Agent 完成任务所需的全部状态，不得改写任务目标。

请按以下结构总结：
1. 原始任务：逐字保留人物姓名、机构、职位、地点、用户目标、输出格式、时效要求和禁止事项。
2. 身份消歧：已确认与仍待确认的身份锚点。
3. 已核验事实：每项事实保留对应的完整 URL；明确 fact 与 inference。
4. 已执行动作：访问过的页面、已调用的数据库工具、成功和失败原因，避免重复操作。
5. 当前信号：时间、热点、有效期及来源 URL。
6. 人设与场景：已读取或新建的虚构人设 ID、选择理由、场景和话术关联。
7. 待完成事项：尚需检索、保存、关联产物或最终回答的具体步骤。

不要给用户作答，不要声称任务已经完成。以下是需要压缩的消息：
{messages}"""


class AgentInput(TypedDict):
    query: str


class AgentOutput(TypedDict):
    source: str
    result: str


class Classification(TypedDict):
    source: HubTarget
    query: str
    requires_tools: bool


class HubState(TypedDict):
    query: str
    classifications: list[Classification]
    results: Annotated[list[AgentOutput], operator.add]
    final_answer: str


class ClassificationResult(BaseModel):
    classifications: list[Classification] = Field(
        description="需要分发的专家列表，每项含 source 与定制子问题 query"
    )


def _compose_specialist_query(
    original_query: str,
    focused_query: str,
    *,
    requires_tools: bool = False,
) -> str:
    """保留用户的 URL、ID 和引用，同时附加分类器的领域聚焦。"""
    focused = focused_query.strip() or original_query
    tool_requirement = f"\n\n{REQUIRE_EVIDENCE_TOOL_MARKER}" if requires_tools else ""
    return (
        f"【用户原始请求（只用于保留上下文、URL、ID 和引用）】\n{original_query}\n\n"
        f"【你唯一要执行的聚焦任务】\n{focused}\n\n"
        "只回答聚焦任务；不要代替其他专家回答原始请求中的其他部分。"
        f"{tool_requirement}"
    )


def _has_direct_public_url(query: str) -> bool:
    """Detect a URL in the current turn, excluding URLs carried in chat history."""
    text = str(query or "")
    if _CURRENT_REQUEST_MARKER in text:
        text = text.rpartition(_CURRENT_REQUEST_MARKER)[2]
    return bool(_PUBLIC_URL_RE.search(text))


def _has_osint_intent(query: str) -> bool:
    """识别本轮明确的公网研究意图，不受历史消息中的措辞干扰。"""
    text = str(query or "")
    if _CURRENT_REQUEST_MARKER in text:
        text = text.rpartition(_CURRENT_REQUEST_MARKER)[2]
    return bool(_OSINT_INTENT_RE.search(text))


def _has_persona_collection_intent(query: str) -> bool:
    """识别本轮主动采集或持续升级虚构人设的请求。"""
    text = str(query or "")
    if _CURRENT_REQUEST_MARKER in text:
        text = text.rpartition(_CURRENT_REQUEST_MARKER)[2]
    return bool(_PERSONA_COLLECTION_INTENT_RE.search(text))


def _direct_url_classifications(query: str) -> list[Classification] | None:
    """Bypass one router-model call for the unambiguous exact-URL workflow."""
    if not _has_direct_public_url(query):
        return None
    return [{"source": "payload", "query": query, "requires_tools": True}]


def _osint_classifications(query: str) -> list[Classification] | None:
    """明确公网研究请求直接进入 OSINT，减少一次分类模型延迟和误路由。"""
    if not _has_osint_intent(query):
        return None
    return [{"source": "osint", "query": query, "requires_tools": True}]


def _persona_collection_classifications(
    query: str,
) -> list[Classification] | None:
    """主动人设研究直接进入具备浏览器能力的人设专家。"""
    if not _has_persona_collection_intent(query):
        return None
    return [{"source": "persona", "query": query, "requires_tools": True}]


def _build_synthesis_prompt(query: str, response_style: str) -> str:
    """Build an evidence-closed prompt for combining specialist outputs."""
    return (
        f"根据以下多个专家的结果，回答用户原始问题：{query}\n"
        "- 专家结果是本轮唯一事实来源；不得添加结果中没有出现的实体、数量、ID、状态、时间或建议依据\n"
        "- 某项结果为空、未找到或为 0 时必须原样表达，不得补造示例、历史值或离线记录\n"
        "- 专家结果互相冲突时明确指出冲突，不自行选择或补全\n"
        "- 合并关键信息，去重、保持条理\n"
        "- 完整保留结果中的 [[ref:...]] 跳转标记，不要改写或删除\n"
        "- 完整保留结果中的 [[artifact:...]] 产物标记和下载链接\n"
        "- 用简洁中文给出结论\n\n"
        f"{response_style}"
    )


def _specialist_tools(source: HubTarget) -> list[Any]:
    """Resolve tools from the same registry used by the auditable API catalog."""
    from ..tools.catalog import get_hub_tool_groups

    return list(get_hub_tool_groups()[source])


async def build_hub_graph(app_config: Any):
    """构建 AI 中枢路由图并按领域并行分发。"""
    from .streaming import emit_event, run_agent_with_sse, _ts
    from langchain_core.messages import HumanMessage  # noqa: F401  (供子 Agent 隐式使用)
    from core.observability import observation_context

    router_llm = create_llm(app_config)
    classify_prompt = load_prompt("hub/classify")
    response_style = load_prompt("hub/response_style")

    data_agent = await create_hub_specialist_agent(
        app_config,
        system_prompt=load_prompt("hub/data"),
        tools=_specialist_tools("data"),
        output_mode="sse",
    )
    persona_agent = await create_hub_specialist_agent(
        app_config,
        system_prompt=load_prompt("hub/persona"),
        tools=_specialist_tools("persona"),
        output_mode="sse",
    )
    content_agent = await create_hub_specialist_agent(
        app_config,
        system_prompt=load_prompt("hub/content"),
        tools=_specialist_tools("content"),
        output_mode="sse",
    )

    async def classify_query(state: HubState) -> dict:
        await emit_event({"type": "router_start", "timestamp": _ts()})
        await emit_event({"type": "classify_start", "timestamp": _ts()})

        classifications = _direct_url_classifications(state["query"])
        if classifications is None:
            classifications = _persona_collection_classifications(state["query"])
        if classifications is None:
            classifications = _osint_classifications(state["query"])
        if classifications is None:
            structured_llm = router_llm.with_structured_output(ClassificationResult)
            with observation_context(phase="hub_classify", agent="hub_classify"):
                result = await structured_llm.ainvoke(
                    [
                        {"role": "system", "content": classify_prompt},
                        {"role": "user", "content": state["query"]},
                    ]
                )

            classifications = result.classifications or [
                {"source": "data", "query": state["query"], "requires_tools": False}
            ]

        await emit_event({
            "type": "classify_end",
            "agents": [c["source"] for c in classifications],
            "timestamp": _ts(),
        })
        return {"classifications": classifications}

    def route_to_agents(state: HubState) -> list[Send]:
        requests: list[Send] = []
        original_query = state["query"]
        for classification in state["classifications"]:
            specialist_query = _compose_specialist_query(
                original_query,
                str(classification.get("query") or ""),
                requires_tools=bool(classification.get("requires_tools")),
            )
            requests.append(
                Send(classification["source"], {"query": specialist_query})
            )
        return requests

    async def query_data(state: AgentInput) -> dict:
        with observation_context(phase="hub_data", agent="hub_data"):
            text = await run_agent_with_sse("data", data_agent, state["query"])
        return {"results": [{"source": "data", "result": text}]}

    async def query_persona(state: AgentInput) -> dict:
        if _has_persona_collection_intent(state["query"]):
            return await _query_browser_specialist(
                source="persona",
                purpose="hub_persona_research",
                prompt_name="hub/persona",
                query=state["query"],
                timeout=420,
                mcp_tool_limit=20,
                summary_trigger_tokens=32_000,
                summary_keep_messages=18,
                summary_trim_tokens=None,
            )
        with observation_context(phase="hub_persona", agent="hub_persona"):
            text = await run_agent_with_sse("persona", persona_agent, state["query"])
        return {"results": [{"source": "persona", "result": text}]}

    async def query_content(state: AgentInput) -> dict:
        with observation_context(phase="hub_content", agent="hub_content"):
            text = await run_agent_with_sse("content", content_agent, state["query"])
        return {"results": [{"source": "content", "result": text}]}

    async def _query_browser_specialist(
        *,
        source: HubTarget,
        purpose: str,
        prompt_name: str,
        query: str,
        timeout: int,
        mcp_tool_limit: int,
        summary_trigger_tokens: int = 16_000,
        summary_keep_messages: int = 12,
        summary_prompt: str | None = None,
        summary_trim_tokens: int | None = 4000,
    ) -> dict:
        from api.services.info_collection.url_tools import _build_worker_chrome_config
        from browser_manager.provider import get_browser_provider

        provider = get_browser_provider()
        task_id = f"hub_{source}_{uuid.uuid4().hex[:16]}"
        cdp_url = await provider.get_cdp_endpoint(task_id=task_id, purpose=purpose)
        if not cdp_url:
            return {
                "results": [{
                    "source": source,
                    "result": f"{source.upper()} Agent 无法获取项目 Chrome，公网检索暂不可用。",
                }]
            }
        try:
            worker_config = _build_worker_chrome_config(app_config, cdp_url)
            browser_agent = await create_hub_specialist_agent(
                worker_config,
                system_prompt=load_prompt(prompt_name),
                tools=_specialist_tools(source),
                mcp_server_name="chrome-devtools",
                output_mode="sse",
                summary_trigger_tokens=summary_trigger_tokens,
                summary_keep_messages=summary_keep_messages,
                summary_prompt=summary_prompt,
                summary_trim_tokens=summary_trim_tokens,
                timeout=timeout,
                mcp_tool_limit=mcp_tool_limit,
            )
            with observation_context(phase=f"hub_{source}", agent=f"hub_{source}"):
                text = await run_agent_with_sse(source, browser_agent, query)
            return {"results": [{"source": source, "result": text}]}
        finally:
            try:
                await provider.release_cdp_endpoint(task_id)
            except Exception:
                pass

    async def query_payload(state: AgentInput) -> dict:
        direct_url_request = _has_direct_public_url(state["query"])
        return await _query_browser_specialist(
            source="payload",
            purpose="hub_payload",
            prompt_name="hub/payload",
            query=state["query"],
            timeout=_DIRECT_URL_AGENT_TIMEOUT_SECONDS if direct_url_request else 300,
            mcp_tool_limit=_DIRECT_URL_MCP_TOOL_LIMIT if direct_url_request else 12,
        )

    async def query_osint(state: AgentInput) -> dict:
        return await _query_browser_specialist(
            source="osint",
            purpose="hub_osint",
            prompt_name="hub/osint",
            query=state["query"],
            timeout=_OSINT_AGENT_TIMEOUT_SECONDS,
            mcp_tool_limit=_OSINT_MCP_TOOL_LIMIT,
            summary_trigger_tokens=48_000,
            summary_keep_messages=24,
            summary_prompt=_OSINT_SUMMARY_PROMPT,
            summary_trim_tokens=None,
        )

    async def synthesize_results(state: HubState) -> dict:
        await emit_event({"type": "synthesis_start", "timestamp": _ts()})

        results = state.get("results") or []
        if not results:
            await emit_event({"type": "synthesis_end", "timestamp": _ts()})
            await emit_event({
                "type": "router_end",
                "summary": "未获得任何专家结果。",
                "timestamp": _ts(),
            })
            return {"final_answer": "未获得任何专家结果。"}

        # 单专家结果直接透传，避免二次改写丢失 [[ref:...]] 标记
        if len(results) == 1:
            final_answer = results[0]["result"]
            await emit_event({
                "type": "synthesis_content",
                "data": final_answer,
                "timestamp": _ts(),
            })
            await emit_event({"type": "synthesis_end", "timestamp": _ts()})
            await emit_event({
                "type": "router_end",
                "summary": final_answer[:200],
                "timestamp": _ts(),
            })
            return {"final_answer": final_answer}

        formatted = [f"【{r['source']}】结果：\n{r['result']}" for r in results]
        final_answer = ""
        with observation_context(phase="hub_synthesize", agent="hub_synthesize"):
            async for chunk in router_llm.astream(
                [
                    {
                        "role": "system",
                        "content": _build_synthesis_prompt(
                            state["query"],
                            response_style,
                        ),
                    },
                    {"role": "user", "content": "\n\n".join(formatted)},
                ]
            ):
                if hasattr(chunk, "content") and chunk.content:
                    final_answer += chunk.content
                    await emit_event({
                        "type": "synthesis_content",
                        "data": chunk.content,
                        "timestamp": _ts(),
                    })

        await emit_event({"type": "synthesis_end", "timestamp": _ts()})
        await emit_event({
            "type": "router_end",
            "summary": final_answer[:200],
            "timestamp": _ts(),
        })
        return {"final_answer": final_answer}

    builder = (
        StateGraph(HubState)
        .add_node("classify", classify_query)
        .add_node("data", query_data)
        .add_node("persona", query_persona)
        .add_node("content", query_content)
        .add_node("payload", query_payload)
        .add_node("osint", query_osint)
        .add_node("synthesize", synthesize_results)
        .add_edge(START, "classify")
        .add_conditional_edges(
            "classify",
            route_to_agents,
            ["data", "persona", "content", "payload", "osint"],
        )
        .add_edge("data", "synthesize")
        .add_edge("persona", "synthesize")
        .add_edge("content", "synthesize")
        .add_edge("payload", "synthesize")
        .add_edge("osint", "synthesize")
        .add_edge("synthesize", END)
    )
    return builder.compile()
