"""
Agent 构建工厂。

职责：组装配置并调用 runtime.create_agent_node
"""

from __future__ import annotations

from typing import Any, Callable, AsyncGenerator
import asyncio
import uuid

from langchain.agents.middleware import SummarizationMiddleware
from langchain.tools import tool, ToolRuntime
from langchain_core.messages import SystemMessage

from ..config.models import AppConfig
from ..prompts.loader import load_prompt
from .runtime import create_agent_node, create_llm, OutputMode
from ..tools.builtin import tianyancha_get_domain, tianyancha_get_bids, tianyancha_get_bids_mock

BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


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
        system_prompt=load_prompt("xhs_collect/xhs_collect"),
        builtin_tools=[],
        middleware=[
            SummarizationMiddleware(
                model=create_llm(app_config),
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
) -> Callable:
    """官网社工打标 Agent（Web Tagging Agent）。"""
    return create_agent_node(
        app_config=app_config,
        system_prompt=load_prompt("web_tagging/web_tagging"),
        mcp_server_name=server_name,
        output_mode=output_mode,
        streaming=streaming,
    )


async def create_weixin_search_agent(
    app_config: AppConfig,
    server_name: str = "chrome-devtools",
    output_mode: OutputMode = "silent",
) -> Callable:
    """创建微信公众号搜索 Agent，用于搜索招投标相关信息。"""
    return create_agent_node(
        app_config=app_config,
        system_prompt=load_prompt("weixin_search/weixin_search"),
        builtin_tools=[],
        middleware=[
            SummarizationMiddleware(
                model=create_llm(app_config),
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
        system_prompt=load_prompt("bid_collect/bid_collect"),
        builtin_tools=[tianyancha_get_bids_mock],
        middleware=[
            SummarizationMiddleware(
                model=create_llm(app_config),
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
        system_prompt=load_prompt("company_normalize/company_normalize"),
        builtin_tools=[tianyancha_get_domain],
        middleware=[
            SummarizationMiddleware(
                model=create_llm(app_config),
                trigger=("tokens", 3000),
                keep=("messages", 8),
            ),
        ],
        mcp_server_name=server_name,
        output_mode=output_mode,
    )


async def create_xhs_note_tagging_agent(
    app_config: AppConfig,
    output_mode: OutputMode = "silent",
) -> Callable:
    """创建小红书笔记打标 Agent，用于分析搜索结果中的社工攻击面。"""
    return create_agent_node(
        app_config=app_config,
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
    output_mode: OutputMode = "sse",
) -> Callable:
    """
    创建 AI 中枢的「专家子 Agent」（供 hub 路由图并行分发）。

    每个子 Agent 只携带一个内聚的只读工具组，并叠加 SummarizationMiddleware，
    让单个子 Agent 的上下文保持有界，避免单一「大而全」Agent 上下文爆炸。
    工具的调用与顺序由子 Agent 自主决定（ReAct，无固定编排）。
    """
    return create_agent_node(
        app_config=app_config,
        system_prompt=system_prompt,
        builtin_tools=tools,
        middleware=[
            SummarizationMiddleware(
                model=create_llm(app_config),
                trigger=("tokens", 3000),
                keep=("messages", 6),
            ),
        ],
        mcp_server_name=None,
        output_mode=output_mode,
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

    return create_agent_node(
        app_config=app_config,
        system_prompt=load_prompt("assistant/assistant"),
        builtin_tools=(
            SKILL_TOOLS + PERSONA_TOOLS + WORD_TOOLS
            + CONTEXT_TOOLS + ANALYSIS_TOOLS + READ_TOOLS
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
        builtin_tools=SKILL_TOOLS + PERSONA_TOOLS + WORD_TOOLS + CONTEXT_TOOLS + ANALYSIS_TOOLS,
        middleware=None,
        mcp_server_name=None,
        output_mode=output_mode,
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
        system_prompt=load_prompt("profile_copywriting/profile_copywriting"),
        builtin_tools=SKILL_TOOLS + PERSONA_TOOLS + WORD_TOOLS + CONTEXT_TOOLS + ANALYSIS_TOOLS,
        middleware=None,
        mcp_server_name=None,
        output_mode=output_mode,
    )


async def create_persona_collect_agent(
    app_config: AppConfig,
    server_name: str = "chrome-devtools",
    output_mode: OutputMode = "silent",
) -> Callable:
    """
    创建人设收集 Agent。

    能力：AI 浏览器搜索公开渠道收集真实人物信息（公司/职位/教育/背景等），
    输出结构化人物档案（由 PersonaProfile 约束）。
    复用 create_agent_node + chrome-devtools MCP，并携带 tianyancha_get_domain 补全公司域名。
    """
    return create_agent_node(
        app_config=app_config,
        system_prompt=load_prompt("persona_collect/persona_collect"),
        builtin_tools=[tianyancha_get_domain],
        middleware=[
            SummarizationMiddleware(
                model=create_llm(app_config),
                trigger=("tokens", 3000),
                keep=("messages", 8),
            ),
        ],
        mcp_server_name=server_name,
        output_mode=output_mode,
    )
