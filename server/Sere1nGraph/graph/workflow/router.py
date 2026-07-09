"""
多源知识路由工作流：
- Router 节点对用户查询进行分类，决定调用哪些 Agent；
- Agent 使用 SSE 模式，事件通过 streaming 模块处理；
- Synthesize 节点汇总各 Agent 输出，返回最终答案。
"""

from __future__ import annotations

import operator
from typing import Any, Annotated, Literal
from typing_extensions import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from pydantic import BaseModel, Field

from ..agents.factory import (
    create_browser_agent,
    create_xhs_agent,
    create_weixin_search_agent,
    create_bid_collect_agent,
)
from ..agents.runtime import create_llm

RouteTarget = Literal["browser", "xhs", "weixin", "bid"]


class AgentInput(TypedDict):
    query: str


class AgentOutput(TypedDict):
    source: str
    result: str


class Classification(TypedDict):
    source: RouteTarget
    query: str


class RouterState(TypedDict):
    query: str
    classifications: list[Classification]
    results: Annotated[list[AgentOutput], operator.add]
    final_answer: str
    copywriting: str  # 文案生成结果


class ClassificationResult(BaseModel):
    classifications: list[Classification] = Field(
        description="List of agent routes with tailored sub-questions"
    )


async def build_router_graph(app_config: Any):
    """
    构建支持并发的 Router 图：START -> classify -> {browser | xhs | weixin | bid} -> synthesize -> copywriting -> END
    """
    from .streaming import emit_event, run_agent_with_sse, _ts
    from ..prompts.loader import load_prompt
    from .copywriting import build_copywriting_subgraph
    
    router_llm = create_llm(app_config)
    classify_prompt = load_prompt("router/classify")
    
    # 创建 Agent（SSE 模式，用于流式输出）
    browser_agent = await create_browser_agent(app_config, output_mode="sse")
    xhs_agent = await create_xhs_agent(app_config, output_mode="sse")
    weixin_agent = await create_weixin_search_agent(app_config, output_mode="sse")
    bid_agent = await create_bid_collect_agent(app_config, output_mode="sse")

    async def classify_query(state: RouterState) -> dict:
        # Router 子图开始
        await emit_event({"type": "router_start", "timestamp": _ts()})
        await emit_event({"type": "classify_start", "timestamp": _ts()})
        
        structured_llm = router_llm.with_structured_output(ClassificationResult)
        result = await structured_llm.ainvoke(
            [
                {"role": "system", "content": classify_prompt},
                {"role": "user", "content": state["query"]},
            ]
        )
        
        await emit_event({
            "type": "classify_end",
            "agents": [c["source"] for c in result.classifications],
            "timestamp": _ts()
        })
        
        return {"classifications": result.classifications}

    def route_to_agents(state: RouterState) -> list[Send]:
        return [
            Send(c["source"], {"query": c["query"]})
            for c in state["classifications"]
        ]

    async def query_browser(state: AgentInput) -> dict:
        text = await run_agent_with_sse("browser", browser_agent, state["query"])
        return {"results": [{"source": "browser", "result": text}]}

    async def query_xhs(state: AgentInput) -> dict:
        text = await run_agent_with_sse("xhs", xhs_agent, state["query"])
        return {"results": [{"source": "xhs", "result": text}]}

    async def query_weixin(state: AgentInput) -> dict:
        text = await run_agent_with_sse("weixin", weixin_agent, state["query"])
        return {"results": [{"source": "weixin", "result": text}]}

    async def query_bid(state: AgentInput) -> dict:
        text = await run_agent_with_sse("bid", bid_agent, state["query"])
        return {"results": [{"source": "bid", "result": text}]}

    async def synthesize_results(state: RouterState) -> dict:
        await emit_event({"type": "synthesis_start", "timestamp": _ts()})
        
        if not state["results"]:
            await emit_event({"type": "synthesis_end", "timestamp": _ts()})
            # Router 子图结束
            await emit_event({
                "type": "router_end",
                "summary": "未从任何知识源获得结果。",
                "timestamp": _ts()
            })
            return {"final_answer": "未从任何知识源获得结果。"}

        formatted = [f"来源 {r['source']}:\n{r['result']}" for r in state["results"]]

        final_answer = ""
        async for chunk in router_llm.astream(
            [
                {
                    "role": "system",
                    "content": (
                        f"根据以下多源结果回答原始问题：{state['query']}\n"
                        "- 合并关键信息，避免重复\n"
                        "- 保持简洁条理"
                    ),
                },
                {"role": "user", "content": "\n\n".join(formatted)},
            ]
        ):
            if hasattr(chunk, 'content') and chunk.content:
                final_answer += chunk.content
                await emit_event({
                    "type": "synthesis_content",
                    "data": chunk.content,
                    "timestamp": _ts()
                })
        
        await emit_event({"type": "synthesis_end", "timestamp": _ts()})
        # Router 子图结束
        await emit_event({
            "type": "router_end",
            "summary": final_answer[:200] + "..." if len(final_answer) > 200 else final_answer,
            "timestamp": _ts()
        })
        
        return {"final_answer": final_answer}
    
    async def run_copywriting_subgraph(state: RouterState) -> dict:
        """调用文案生成 subgraph"""
        await emit_event({"type": "copywriting_start", "timestamp": _ts()})
        
        copywriting_graph = await build_copywriting_subgraph(app_config)
        
        result = await copywriting_graph.ainvoke({
            "synthesis_result": state["final_answer"],
            "scenario": "",
            "script": "",
            "objection_handling": "",
            "final_copywriting": ""
        })
        
        final_copywriting = result["final_copywriting"]
        await emit_event({
            "type": "copywriting_end",
            "summary": final_copywriting[:200] + "..." if len(final_copywriting) > 200 else final_copywriting,
            "timestamp": _ts()
        })
        
        return {"copywriting": final_copywriting}

    builder = (
        StateGraph(RouterState)
        .add_node("classify", classify_query)
        .add_node("browser", query_browser)
        .add_node("xhs", query_xhs)
        .add_node("weixin", query_weixin)
        .add_node("bid", query_bid)
        .add_node("synthesize", synthesize_results)
        .add_node("copywriting", run_copywriting_subgraph)
        .add_edge(START, "classify")
        .add_conditional_edges("classify", route_to_agents, ["browser", "xhs", "weixin", "bid"])
        .add_edge("browser", "synthesize")
        .add_edge("xhs", "synthesize")
        .add_edge("weixin", "synthesize")
        .add_edge("bid", "synthesize")
        .add_edge("synthesize", "copywriting")
        .add_edge("copywriting", END)
    )
    return builder.compile()
