"""
AI 中枢路由工作流（复用 router.py 的 LangGraph 路由架构）。

设计动机：
- 单一「大而全」ReAct Agent 携带全部工具时，多轮工具调用会让上下文快速膨胀。
- 复用既有 router 的「分类 → 并行分发 → 汇总」骨架，把工具按领域拆到多个
  「专家子 Agent」，每个子 Agent 只携带内聚的只读工具组并叠加 SummarizationMiddleware，
  使单个子 Agent 上下文有界；子 Agent 内部的工具选择/顺序仍由其自主决定（ReAct，无固定编排）。

拓扑：START → classify → {data | persona | content}（Send 并行）→ synthesize → END

事件复用 router 既有词汇（router_start/classify_*/agent_*/synthesis_*），
因此 executor._convert_graph_event 无需改动即可渲染思维链。
"""

from __future__ import annotations

import operator
from typing import Any, Annotated, Literal
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from pydantic import BaseModel, Field

from ..agents.factory import create_hub_specialist_agent
from ..agents.runtime import create_llm

HubTarget = Literal["data", "persona", "content"]


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


# ============ 内联提示词（避免 DB 提示词缓存同步问题）============

CLASSIFY_PROMPT = """你是 AI 中枢的查询分类器，负责把用户请求分发给最相关的专家子 Agent。

## 可用专家

| source | 专长 | 适用场景 |
|---|---|---|
| data | 数据与态势分析 | 项目/任务进展、日志报错、finding 明细与统计、资产测绘、Token/全局观测、多项目对比 |
| persona | 人设与联系人 | 人设库检索、实体背景画像、手机联系人画像、手机操作记录 |
| content | 话术与产物 | 加载技能生成社工话术、生成人物背景 Word 文档、内容创作 |

## 分类规则

1. 只选**最相关**的专家，能一个解决就不要多选；可多选处理复合问题。
2. 为每个专家生成**聚焦其专长**的定制子问题（query）。
3. 纯闲聊/无需查库的问题，也至少分给 data，让它据实回答或说明无相关数据。

## 输出
返回 JSON：{"classifications": [{"source": "data", "query": "定制子问题"}]}"""

DATA_PROMPT = """你是 AI 中枢的「数据分析专家」。只读地查询平台数据并给出结论。

可用工具（自主决定调用哪些、什么顺序）：
- 项目/任务：list_projects、get_project、list_task_logs、list_recent_conversations
- 发现明细/统计：get_finding_detail、get_finding_copywriting、get_finding_profile、get_findings_summary、query_findings
- 项目态势/对比：get_project_dashboard、batch_get_project_dashboards
- 资产与全局：list_project_assets、get_global_stats
- 社媒采集：list_xhs_notes、list_xhs_note_details、list_xhs_profiles、list_douyin_search_results、list_douyin_profiles

要求：
- 先用工具取真实数据，再基于数据回答，不臆造。
- 对可跳转实体保留工具返回中的 [[ref:...]] 标记，便于中台一键打开。
- 输出简洁有条理的中文结论。"""

PERSONA_PROMPT = """你是 AI 中枢的「人设与联系人专家」。只读地检索人物/联系人背景。

可用工具（自主决定调用哪些、什么顺序）：
- 人设库：search_personas、get_persona
- 实体背景：get_entity_context
- 手机侧：list_contact_profiles、get_contact_profile、list_mobile_operations

要求：
- 先取真实数据再回答；对 person/finding/company 等可跳转实体保留 [[ref:...]] 标记。
- 输出简洁的中文人物/联系人背景摘要。"""

CONTENT_PROMPT = """你是 AI 中枢的「话术与产物专家」，负责社工话术生成与文档产物。

可用工具（自主决定调用哪些、什么顺序）：
- 技能：list_available_skills、load_skill、load_skill_reference
- 人设参考：search_personas、get_persona
- 产物：generate_word_document、generate_persona_word

要求：
- 生成话术前，先按需加载相关 skill，并可拉取真实人物背景增强针对性。
- 生成 Word 后返回下载链接；对人物保留 [[ref:...]] 标记。
- 输出简洁的中文结果。"""


def _data_tools() -> list[Any]:
    from ..tools.read_tools import (
        list_projects,
        get_project,
        list_task_logs,
        get_finding_detail,
        get_finding_copywriting,
        get_finding_profile,
        list_project_assets,
        list_recent_conversations,
        list_xhs_notes,
        list_xhs_note_details,
        list_xhs_profiles,
        list_douyin_search_results,
        list_douyin_profiles,
    )
    from ..tools.analysis_tools import ANALYSIS_TOOLS

    return [
        list_projects,
        get_project,
        list_task_logs,
        get_finding_detail,
        get_finding_copywriting,
        get_finding_profile,
        list_project_assets,
        list_recent_conversations,
        list_xhs_notes,
        list_xhs_note_details,
        list_xhs_profiles,
        list_douyin_search_results,
        list_douyin_profiles,
    ] + list(ANALYSIS_TOOLS)


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

    return list(SKILL_TOOLS) + list(WORD_TOOLS) + list(PERSONA_TOOLS)


async def build_hub_graph(app_config: Any):
    """构建 AI 中枢路由图：START → classify → {data|persona|content} → synthesize → END。"""
    from .streaming import emit_event, run_agent_with_sse, _ts
    from langchain_core.messages import HumanMessage  # noqa: F401  (供子 Agent 隐式使用)
    from core.observability import observation_context

    router_llm = create_llm(app_config)

    data_agent = await create_hub_specialist_agent(
        app_config, system_prompt=DATA_PROMPT, tools=_data_tools(), output_mode="sse"
    )
    persona_agent = await create_hub_specialist_agent(
        app_config, system_prompt=PERSONA_PROMPT, tools=_persona_tools(), output_mode="sse"
    )
    content_agent = await create_hub_specialist_agent(
        app_config, system_prompt=CONTENT_PROMPT, tools=_content_tools(), output_mode="sse"
    )

    async def classify_query(state: HubState) -> dict:
        await emit_event({"type": "router_start", "timestamp": _ts()})
        await emit_event({"type": "classify_start", "timestamp": _ts()})

        structured_llm = router_llm.with_structured_output(ClassificationResult)
        with observation_context(phase="hub_classify", agent="hub_classify"):
            result = await structured_llm.ainvoke(
                [
                    {"role": "system", "content": CLASSIFY_PROMPT},
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
        return [
            Send(c["source"], {"query": c["query"]})
            for c in state["classifications"]
        ]

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
        .add_node("synthesize", synthesize_results)
        .add_edge(START, "classify")
        .add_conditional_edges("classify", route_to_agents, ["data", "persona", "content"])
        .add_edge("data", "synthesize")
        .add_edge("persona", "synthesize")
        .add_edge("content", "synthesize")
        .add_edge("synthesize", END)
    )
    return builder.compile()
