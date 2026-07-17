"""
AI 中枢路由工作流（复用 router.py 的 LangGraph 路由架构）。

设计动机：
- 单一「大而全」ReAct Agent 携带全部工具时，多轮工具调用会让上下文快速膨胀。
- 复用既有 router 的「分类 → 并行分发 → 汇总」骨架，把工具按领域拆到多个
  「专家子 Agent」，每个子 Agent 只携带内聚的只读工具组并叠加 SummarizationMiddleware，
  使单个子 Agent 上下文有界；子 Agent 内部的工具选择/顺序仍由其自主决定（ReAct，无固定编排）。

拓扑：START → classify → {data | persona | content | payload}（Send 并行）→ synthesize → END

事件复用 router 既有词汇（router_start/classify_*/agent_*/synthesis_*），
因此 executor._convert_graph_event 无需改动即可渲染思维链。
"""

from __future__ import annotations

import operator
import uuid
from typing import Any, Annotated, Literal
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from pydantic import BaseModel, Field

from ..agents.factory import create_hub_specialist_agent
from ..agents.runtime import create_llm
from ..prompts.loader import load_prompt

HubTarget = Literal["data", "persona", "content", "payload"]


class AgentInput(TypedDict):
    query: str


class AgentOutput(TypedDict):
    source: str
    result: str


class Classification(TypedDict):
    source: HubTarget
    query: str


class HubState(TypedDict):
    query: str
    classifications: list[Classification]
    results: Annotated[list[AgentOutput], operator.add]
    final_answer: str


class ClassificationResult(BaseModel):
    classifications: list[Classification] = Field(
        description="需要分发的专家列表，每项含 source 与定制子问题 query"
    )


def _compose_specialist_query(original_query: str, focused_query: str) -> str:
    """保留用户的 URL、ID 和引用，同时附加分类器的领域聚焦。"""
    focused = focused_query.strip() or original_query
    return (
        f"【用户原始请求】\n{original_query}\n\n"
        f"【分类器聚焦任务】\n{focused}"
    )


def _data_tools() -> list[Any]:
    from ..tools.read_tools import READ_TOOLS
    from ..tools.analysis_tools import ANALYSIS_TOOLS
    from ..tools.artifact_tools import ARTIFACT_QUERY_TOOLS
    from ..tools.project_data_tools import PROJECT_DATA_TOOLS

    return (
        list(READ_TOOLS)
        + list(ANALYSIS_TOOLS)
        + list(PROJECT_DATA_TOOLS)
        + list(ARTIFACT_QUERY_TOOLS)
    )


def _persona_tools() -> list[Any]:
    from ..tools.persona_tools import PERSONA_TOOLS
    from ..tools.context_tools import CONTEXT_TOOLS
    from ..tools.read_tools import (
        list_contact_profiles,
        get_contact_profile,
        list_mobile_operations,
    )

    return list(PERSONA_TOOLS) + list(CONTEXT_TOOLS) + [
        list_contact_profiles,
        get_contact_profile,
        list_mobile_operations,
    ]


def _content_tools() -> list[Any]:
    from ..tools.skill_tools import SKILL_TOOLS
    from ..tools.word_tools import WORD_TOOLS
    from ..tools.persona_tools import PERSONA_TOOLS
    from ..tools.artifact_tools import ARTIFACT_QUERY_TOOLS

    return (
        list(SKILL_TOOLS)
        + list(WORD_TOOLS)
        + list(PERSONA_TOOLS)
        + list(ARTIFACT_QUERY_TOOLS)
    )


def _payload_tools() -> list[Any]:
    from ..tools.analysis_tools import ANALYSIS_TOOLS
    from ..tools.artifact_tools import ARTIFACT_QUERY_TOOLS
    from ..tools.context_tools import CONTEXT_TOOLS
    from ..tools.persona_tools import PERSONA_TOOLS
    from ..tools.project_data_tools import PROJECT_DATA_TOOLS
    from ..tools.read_tools import READ_TOOLS
    from ..tools.skill_tools import SKILL_TOOLS
    from ..tools.word_tools import PAYLOAD_WORD_TOOLS

    return (
        list(READ_TOOLS)
        + list(ANALYSIS_TOOLS)
        + list(PERSONA_TOOLS)
        + list(CONTEXT_TOOLS)
        + list(PROJECT_DATA_TOOLS)
        + list(ARTIFACT_QUERY_TOOLS)
        + list(SKILL_TOOLS)
        + list(PAYLOAD_WORD_TOOLS)
    )


async def build_hub_graph(app_config: Any):
    """构建 AI 中枢路由图并按领域并行分发。"""
    from .streaming import emit_event, run_agent_with_sse, _ts
    from langchain_core.messages import HumanMessage  # noqa: F401  (供子 Agent 隐式使用)
    from core.observability import observation_context

    router_llm = create_llm(app_config)
    classify_prompt = load_prompt("hub/classify")

    data_agent = await create_hub_specialist_agent(
        app_config, system_prompt=load_prompt("hub/data"), tools=_data_tools(), output_mode="sse"
    )
    persona_agent = await create_hub_specialist_agent(
        app_config, system_prompt=load_prompt("hub/persona"), tools=_persona_tools(), output_mode="sse"
    )
    content_agent = await create_hub_specialist_agent(
        app_config, system_prompt=load_prompt("hub/content"), tools=_content_tools(), output_mode="sse"
    )

    async def classify_query(state: HubState) -> dict:
        await emit_event({"type": "router_start", "timestamp": _ts()})
        await emit_event({"type": "classify_start", "timestamp": _ts()})

        structured_llm = router_llm.with_structured_output(ClassificationResult)
        with observation_context(phase="hub_classify", agent="hub_classify"):
            result = await structured_llm.ainvoke(
                [
                    {"role": "system", "content": classify_prompt},
                    {"role": "user", "content": state["query"]},
                ]
            )

        classifications = result.classifications or [
            {"source": "data", "query": state["query"]}
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
        with observation_context(phase="hub_persona", agent="hub_persona"):
            text = await run_agent_with_sse("persona", persona_agent, state["query"])
        return {"results": [{"source": "persona", "result": text}]}

    async def query_content(state: AgentInput) -> dict:
        with observation_context(phase="hub_content", agent="hub_content"):
            text = await run_agent_with_sse("content", content_agent, state["query"])
        return {"results": [{"source": "content", "result": text}]}

    async def query_payload(state: AgentInput) -> dict:
        from api.services.info_collection.url_tools import _build_worker_chrome_config
        from browser_manager.provider import get_browser_provider

        provider = get_browser_provider()
        task_id = f"hub_payload_{uuid.uuid4().hex[:16]}"
        cdp_url = await provider.get_cdp_endpoint(task_id=task_id, purpose="hub_payload")
        if not cdp_url:
            return {
                "results": [{
                    "source": "payload",
                    "result": "载荷 Agent 无法获取项目 Chrome，公网检索暂不可用。",
                }]
            }
        try:
            worker_config = _build_worker_chrome_config(app_config, cdp_url)
            payload_agent = await create_hub_specialist_agent(
                worker_config,
                system_prompt=load_prompt("hub/payload"),
                tools=_payload_tools(),
                mcp_server_name="chrome-devtools",
                output_mode="sse",
                summary_trigger_tokens=16_000,
                summary_keep_messages=12,
                timeout=300,
                mcp_tool_limit=12,
            )
            with observation_context(phase="hub_payload", agent="hub_payload"):
                text = await run_agent_with_sse("payload", payload_agent, state["query"])
            return {"results": [{"source": "payload", "result": text}]}
        finally:
            try:
                await provider.release_cdp_endpoint(task_id)
            except Exception:
                pass

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
                        "content": (
                            f"根据以下多个专家的结果，回答用户原始问题：{state['query']}\n"
                            "- 合并关键信息，去重、保持条理\n"
                            "- 完整保留结果中的 [[ref:...]] 跳转标记，不要改写或删除\n"
                            "- 完整保留结果中的 [[artifact:...]] 产物标记和下载链接\n"
                            "- 用简洁中文给出结论与建议"
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
        .add_node("synthesize", synthesize_results)
        .add_edge(START, "classify")
        .add_conditional_edges("classify", route_to_agents, ["data", "persona", "content", "payload"])
        .add_edge("data", "synthesize")
        .add_edge("persona", "synthesize")
        .add_edge("content", "synthesize")
        .add_edge("payload", "synthesize")
        .add_edge("synthesize", END)
    )
    return builder.compile()
