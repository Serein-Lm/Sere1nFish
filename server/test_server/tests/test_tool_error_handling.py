"""
测试: MCP 工具错误处理（ToolException 不再中断 Agent）

扫描 1 个 URL，验证工具错误被包装成字符串返回给 LLM，Agent 不会崩溃。

用法:
    python test_server/tests/test_tool_error_handling.py
"""
import asyncio
import logging
import sys
import time
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("test_tool_error")

TEST_URL = "https://competition.baidu.com"


async def main():
    from Sere1nGraph.graph.config.loader import load_config
    from Sere1nGraph.graph.agents.factory import create_web_tagging_agent
    from Sere1nGraph.graph.agents.runtime import extract_with_retry
    from Sere1nGraph.graph.prompts.loader import load_prompt
    from langchain_core.messages import HumanMessage
    from browser_manager.provider import get_browser_provider
    import copy

    config_path = str(_root / "config.json")
    app_config = load_config(config_path)

    # 申请 Docker 容器
    provider = get_browser_provider()
    task_id = "test-tool-error"
    cdp_url = await provider.get_cdp_endpoint(task_id=task_id, purpose="url_scan")
    if not cdp_url:
        logger.error("无法获取容器")
        return

    logger.info(f"容器就绪: {cdp_url}")

    # 覆盖 MCP 配置指向容器
    worker_config = copy.deepcopy(app_config)
    mcp_servers = worker_config.mcp_servers or {}
    if "chrome-devtools" in mcp_servers:
        cfg = mcp_servers["chrome-devtools"]
        new_args = []
        skip_next = False
        for arg in (cfg.args or []):
            if skip_next:
                skip_next = False
                continue
            if arg == "--browserUrl":
                skip_next = True
                continue
            if arg.startswith("--wsEndpoint"):
                continue
            new_args.append(arg)
        new_args.append(f"--wsEndpoint={cdp_url}")
        cfg.args = new_args

    logger.info(f"\n{'='*50}")
    logger.info(f"扫描 {TEST_URL}")
    logger.info(f"{'='*50}")

    t0 = time.time()
    try:
        agent = await create_web_tagging_agent(worker_config, streaming=False)
        result = await agent({"messages": [HumanMessage(content=f"请分析以下 URL：{TEST_URL}")]})
        elapsed = time.time() - t0

        _wt_prompt = load_prompt("web_tagging/web_tagging")
        tagging = await extract_with_retry(result, worker_config, system_prompt=_wt_prompt)

        if tagging:
            findings = tagging.get("findings", [])
            logger.info(f"\n✅ 扫描成功 ({elapsed:.1f}s)")
            logger.info(f"  findings: {len(findings)}")
        else:
            logger.warning(f"\n⚠️ 扫描完成但解析失败 ({elapsed:.1f}s)")

        # 检查消息里有没有工具错误被正确处理
        messages = result.get("messages", [])
        tool_errors = 0
        for msg in messages:
            content = getattr(msg, "content", "")
            if isinstance(content, str) and "Tool '" in content and "error:" in content:
                tool_errors += 1
                logger.info(f"  工具错误被正确处理: {content[:100]}")

        if tool_errors > 0:
            logger.info(f"\n✅ 共 {tool_errors} 个工具错误被正确包装（未中断 Agent）")
        else:
            logger.info(f"\n✅ 无工具错误（扫描顺利）")

    except Exception as e:
        elapsed = time.time() - t0
        logger.error(f"\n❌ Agent 执行失败 ({elapsed:.1f}s): {e}")

    # 释放容器
    await provider.release_cdp_endpoint(task_id=task_id)
    logger.info("容器已释放")


if __name__ == "__main__":
    asyncio.run(main())
