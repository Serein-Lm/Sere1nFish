"""
应用主入口。

- 加载数据库运行时配置
- 调用对应的 workflow 并输出结果
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Optional

from .workflow.router import build_router_graph
from .agents.factory import BACKGROUND_TASKS


def _configure_langsmith_from_config(app_config) -> None:
    """
    根据 AppConfig.langsmith 配置 LangSmith 相关环境变量，
    以便按官方文档 Trace with LangGraph 的方式自动开启 tracing：
    https://docs.langchain.com/langsmith/trace-with-langgraph
    """
    langsmith = getattr(app_config, "langsmith", None)
    if not langsmith:
        return

    if getattr(langsmith, "enabled", False):
        os.environ["LANGSMITH_TRACING"] = "true"
        if langsmith.api_key:
            os.environ["LANGSMITH_API_KEY"] = langsmith.api_key
        if langsmith.project:
            os.environ["LANGSMITH_PROJECT"] = langsmith.project
        if langsmith.endpoint:
            os.environ["LANGSMITH_ENDPOINT"] = langsmith.endpoint


async def _load_app_config(config_path: Optional[str] = None):
    """从数据库运行时配置读取；文件配置入口已下线。"""
    if config_path:
        raise ValueError("本地配置文件入口已下线；请在前端配置页写入 MongoDB 加密配置。")

    from api.db.mongodb import init_mongo
    from api.services.runtime_config import get_runtime_app_config

    init_mongo()
    return await get_runtime_app_config()


async def _run_router_workflow(app_config, user_input: str):
    graph = await build_router_graph(app_config)
    return await graph.ainvoke(
        {
            "query": user_input,
            "classifications": [],
            "results": [],
            "final_answer": "",
            "copywriting": "",
        }
    )


def run(config_path: Optional[str] = None, user_input: str | None = None):
    """
    程序入口：加载数据库运行时配置并初始化 Router 图；必须由外部提供用户输入。
    """
    if not user_input or not str(user_input).strip():
        raise ValueError("user_input 不能为空")

    async def _run_once():
        app_config = await _load_app_config(config_path)
        _configure_langsmith_from_config(app_config)
        return await _run_router_workflow(app_config, user_input=str(user_input).strip())

    return asyncio.run(_run_once())


async def _cli_main(config_path: Optional[str]) -> None:
    """
    命令行主循环（异步版）：
    - 保持同一个事件循环常驻，避免每轮 asyncio.run 导致后台任务被取消；
    - 允许 trigger_customer_service(background=True) 的后台任务在用户输入间隙继续跑。
    """
    app_config = await _load_app_config(config_path)
    _configure_langsmith_from_config(app_config)

    print("启动路由查询（输入 exit / quit / 回车 结束）：")

    while True:
        try:
            user_input = (await asyncio.to_thread(input, "\n你: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n结束对话。")
            break

        if not user_input or user_input.lower() in {"exit", "quit", "q"}:
            print("结束对话。")
            break

        # 在同一个事件循环里跑一轮 Router（重要：不要每轮 asyncio.run）。
        result = await _run_router_workflow(app_config, user_input=user_input)
        if isinstance(result, dict):
            answer = result.get("final_answer") or result.get("messages") or result
        else:
            answer = result
        print(f"助手: {answer}")

    # 优雅收尾：给后台客服任务一点时间跑完；若仍未完成则取消。
    if BACKGROUND_TASKS:
        pending = [t for t in list(BACKGROUND_TASKS) if not t.done()]
        if pending:
            print(f"等待后台任务完成（{len(pending)} 个，最多 10 秒）...")
            done, still_pending = await asyncio.wait(pending, timeout=10)
            # 触发异常的任务这里不再抛出，只是确保收尾。
            _ = done
            if still_pending:
                print(f"仍有 {len(still_pending)} 个后台任务未完成，正在取消...")
                for t in still_pending:
                    t.cancel()
                await asyncio.gather(*still_pending, return_exceptions=True)


def main():
    """
    命令行入口：解析参数后启动一个简单的多轮对话 CLI。
    """
    parser = argparse.ArgumentParser(description="LangGraph 应用运行入口")
    parser.add_argument(
        "-c",
        "--config",
        dest="config_path",
        default=None,
        help="已下线：运行配置统一从 MongoDB 读取",
    )
    args = parser.parse_args()

    # 关键：只做一次 asyncio.run，保持事件循环常驻。
    asyncio.run(_cli_main(args.config_path))
if __name__ == "__main__":
    main()
