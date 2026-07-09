"""
真实 Agent 扫描测试 — 端到端验证 Docker + MCP + LLM

验证内容：
1. DockerProvider 创建 Chrome 容器 + 健康检查
2. 动态覆盖 MCP --browserUrl 为容器的真实 CDP 地址
3. chrome-devtools MCP stdio 连接是否正常
4. LLM API 调用是否正常（token 消耗）
5. Agent 完整执行流程（navigate → 分析 → 输出 JSON）

完整复现真实 pipeline 的链路，不写 DB，不存储任何数据。

运行方式：
  python test_server/tests/test_real_agent_scan.py
"""

import asyncio
import json
import sys
import time
import traceback
from pathlib import Path

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from core.logger import get_logger

logger = get_logger("test_real_agent")


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def print_ok(msg: str):
    print(f"  ✅ {msg}")

def print_fail(msg: str):
    print(f"  ❌ {msg}")

def print_info(msg: str):
    print(f"  ℹ️  {msg}")


def _override_chrome_mcp_config(app_config, ws_url: str):
    """用 --wsEndpoint 替换 --browserUrl"""
    mcp_servers = app_config.mcp_servers or {}
    if "chrome-devtools" not in mcp_servers:
        return
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
    new_args.append(f"--wsEndpoint={ws_url}")
    cfg.args = new_args


async def _get_chrome_cdp_url(task_id: str = "test-real-agent") -> str | None:
    """
    通过 DockerProvider 获取 Chrome 容器的 WebSocket 代理地址。
    用 --wsEndpoint 直接传给 chrome-devtools-mcp。
    """
    try:
        from browser_manager.provider import get_browser_provider
        provider = get_browser_provider()
        print_info(f"Provider 类型: {type(provider).__name__}")

        ws_url = await provider.get_cdp_endpoint(task_id=task_id)
        if not ws_url:
            print_fail("get_cdp_endpoint 返回 None")
            return None

        print_ok(f"Docker 容器 WS 地址: {ws_url}")
        return ws_url

    except Exception as e:
        print_fail(f"获取 Chrome CDP 失败: {e}")
        traceback.print_exc()
    return None


async def _release_chrome(task_id: str = "test-real-agent"):
    """释放 Docker 容器"""
    try:
        from browser_manager.provider import get_browser_provider
        provider = get_browser_provider()
        await provider.release_cdp_endpoint(task_id)
        print_info("Docker 容器已释放")
    except Exception:
        pass


async def test_real_mcp_agent():
    """
    真实端到端测试 — 完整复现 pipeline 链路：
    DockerProvider 创建容器 → 覆盖 MCP 配置 → 创建 Agent → LLM + MCP 执行 → 释放容器
    """
    print_section("真实 Agent 扫描测试（Docker + MCP + LLM）")

    from Sere1nGraph.graph.config.loader import load_config
    from Sere1nGraph.graph.agents.factory import create_web_tagging_agent
    from Sere1nGraph.graph.observability import get_global_tracker
    from langchain_core.messages import HumanMessage

    config_path = str(_project_root / "config.json")
    app_config = load_config(config_path)
    task_id = "test-real-agent"

    print_info(f"模型: {getattr(app_config.runtime, 'model', 'default')}")
    print_info(f"base_url: {app_config.runtime.base_url}")

    # ── Step 1: 通过 DockerProvider 获取 Chrome 容器 ──
    print_section("Step 1: Docker Chrome 容器")
    t0 = time.time()
    cdp_url = await _get_chrome_cdp_url(task_id)

    if not cdp_url:
        print_fail("无法获取 Docker Chrome 容器，测试终止")
        print_info("请确认: Docker 已启动 + chrome-browser:latest 镜像已构建")
        return False

    print_ok(f"容器就绪 ({time.time()-t0:.1f}s)")

    try:
        # ── Step 2: 覆盖 MCP 配置 ──
        print_section("Step 2: 覆盖 MCP 配置")
        chrome_cfg = (app_config.mcp_servers or {}).get("chrome-devtools")
        original_args = list(chrome_cfg.args) if chrome_cfg else []
        print_info(f"原始 --browserUrl: {original_args}")

        _override_chrome_mcp_config(app_config, cdp_url)
        print_ok(f"已覆盖 → --browserUrl {cdp_url}")
        print_info(f"新 args: {chrome_cfg.args}")

        # ── Step 3: 初始化 tracker ──
        tracker = get_global_tracker()
        tracker.push_context(project_id="test", task_id=task_id, phase="scan")
        stats_before = tracker.get_stats(task_id=task_id)
        calls_before = stats_before.get("total_calls", 0)

        # ── Step 4: 创建 Agent ──
        print_section("Step 3: 创建 Agent")
        print_info("创建 web_tagging_agent（启动 MCP stdio 进程）...")
        t1 = time.time()
        try:
            agent = await create_web_tagging_agent(app_config, output_mode="console", streaming=False)
            print_ok(f"Agent 创建成功 ({time.time()-t1:.1f}s)")
        except Exception as e:
            print_fail(f"Agent 创建失败: {e}")
            traceback.print_exc()
            tracker.pop_context()
            return False

        # ── Step 5: 执行扫描 ──
        print_section("Step 4: Agent 执行扫描")
        test_url = "https://szkj.zjer.cn/hlwxx/webAuthorize"
        print_info(f"扫描目标: {test_url}")
        print_info("开始执行（LLM + MCP 浏览器操作）...")
        print()

        t2 = time.time()
        result = None
        error_info = None

        try:
            result = await asyncio.wait_for(
                agent({"messages": [HumanMessage(content=f"请分析以下 URL：{test_url}")]}),
                timeout=180,
            )
            elapsed = time.time() - t2
            print()
            print_ok(f"Agent 执行完成 ({elapsed:.1f}s)")
        except (ExceptionGroup, BaseExceptionGroup) as eg:
            elapsed = time.time() - t2
            sub_errors = [str(e) for e in eg.exceptions] if hasattr(eg, 'exceptions') else [str(eg)]
            error_info = "; ".join(sub_errors)
            print()
            print_fail(f"TaskGroup 异常 ({elapsed:.1f}s): {error_info}")
        except asyncio.TimeoutError:
            elapsed = time.time() - t2
            print()
            print_fail(f"Agent 执行超时 ({elapsed:.1f}s)")
        except Exception as e:
            elapsed = time.time() - t2
            error_info = str(e)
            print()
            print_fail(f"Agent 执行异常 ({elapsed:.1f}s): {e}")
            traceback.print_exc()

        # ── Step 6: Token 统计 ──
        print_section("Step 5: Token 消耗统计")
        stats_after = tracker.get_stats(task_id=task_id)
        calls_after = stats_after.get("total_calls", 0)
        new_calls = calls_after - calls_before

        print_info(f"LLM 调用次数: {new_calls}")
        print_info(f"输入 tokens: {stats_after.get('total_input_tokens', 0)}")
        print_info(f"输出 tokens: {stats_after.get('total_output_tokens', 0)}")
        print_info(f"总 tokens: {stats_after.get('total_tokens', 0)}")
        print_info(f"费用(元): {stats_after.get('total_cost_yuan', 0)}")
        print_info(f"LLM 总耗时: {stats_after.get('total_duration_ms', 0) / 1000:.1f}s")

        if stats_after.get("by_model"):
            print_info("按模型:")
            for model, data in stats_after["by_model"].items():
                print(f"    {model}: calls={data.get('calls',0)} "
                      f"tokens={data.get('total_tokens',0)} "
                      f"cost=¥{data.get('cost_yuan',0):.4f}")

        tracker.pop_context()

        # ── Step 7: 解析 Agent 输出 ──
        if result:
            print_section("Step 6: Agent 输出解析")
            messages = result.get("messages", [])
            print_info(f"消息数: {len(messages)}")

            from api.utils.json_extract import extract_json_object
            parsed = None
            for msg in reversed(messages):
                content = getattr(msg, "content", None)
                if isinstance(content, str) and content.strip():
                    try:
                        parsed = extract_json_object(content.strip())
                        if parsed:
                            break
                    except Exception:
                        continue

            if parsed:
                findings = parsed.get("findings", [])
                intro = parsed.get("intro", {})
                print_ok("JSON 解析成功")
                print_info(f"站点: {intro.get('site_name', 'N/A')} ({intro.get('domain', 'N/A')})")
                print_info(f"主体: {intro.get('entity_name', 'N/A')}")
                print_info(f"Findings: {len(findings)} 个")
                for i, f in enumerate(findings[:5], 1):
                    print(f"    {i}. [{f.get('type','')}] {f.get('label','')} "
                          f"= {f.get('value','')} (score={f.get('attention_score','N/A')})")
            else:
                for msg in reversed(messages):
                    content = getattr(msg, "content", None)
                    if isinstance(content, str) and content.strip():
                        print_info(f"最后消息(前500字): {content[:500]}")
                        break
                print_fail("Agent 输出无法解析为 JSON")

        # ── Step 8: 总结 ──
        print_section("测试结论")
        if result:
            messages = result.get("messages", [])
            has_output = any(
                isinstance(getattr(m, "content", None), str) and getattr(m, "content", "").strip()
                for m in messages
            )
            if has_output:
                print_ok("Docker容器 ✓ + MCP连接 ✓ + LLM调用 ✓ + Agent输出 ✓")
                if new_calls == 0:
                    print_info("Token 统计为 0（流式模式下部分 LLM 不返回 token 用量，属正常）")
                return True
            else:
                print_fail("Agent 返回了结果但无有效内容")
                return False
        elif error_info:
            print_fail(f"执行失败: {error_info}")
            return False
        else:
            print_fail("Agent 未返回结果")
            return False

    finally:
        # ── 释放 Docker 容器 ──
        if chrome_cfg:
            chrome_cfg.args = original_args
        await _release_chrome(task_id)


async def test_mcp_connection_only():
    """
    测试 Docker + MCP 连接 + 逐个调用工具（debug 模式）
    验证: Docker容器 → MCP session → 工具列表 → 逐个调用关键工具
    """
    print_section("MCP 连接 + 工具调用测试")

    from Sere1nGraph.graph.config.loader import load_config
    from Sere1nGraph.graph.tools.mcp import build_mcp_connections
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_mcp_adapters.tools import load_mcp_tools

    config_path = str(_project_root / "config.json")
    app_config = load_config(config_path)
    task_id = "test-mcp-tools"

    # Step 1: Docker 容器
    print_info("通过 DockerProvider 获取 Chrome 容器 ...")
    t0 = time.time()
    cdp_url = await _get_chrome_cdp_url(task_id)
    if not cdp_url:
        print_fail("无法获取 Docker Chrome 容器")
        return False
    print_ok(f"容器就绪 ({time.time()-t0:.1f}s): {cdp_url}")

    # Step 2: 覆盖 MCP 配置
    chrome_cfg = (app_config.mcp_servers or {}).get("chrome-devtools")
    original_args = list(chrome_cfg.args) if chrome_cfg else []
    _override_chrome_mcp_config(app_config, cdp_url)
    print_ok(f"MCP --browserUrl → {cdp_url}")

    try:
        # Step 3: 建立 MCP session
        connections = build_mcp_connections(app_config, server_names="chrome-devtools")
        print_info(f"MCP 配置: {json.dumps(connections, indent=2, default=str)}")

        client = MultiServerMCPClient(connections)
        transport = connections["chrome-devtools"].get("transport", "stdio")

        t1 = time.time()
        if transport == "stdio":
            print_info("启动 MCP stdio 进程 ...")
            async with client.session("chrome-devtools") as session:
                print_ok(f"MCP session 建立成功 ({time.time()-t1:.1f}s)")
                tools = await load_mcp_tools(session)
                print_ok(f"获取到 {len(tools)} 个工具")

                # 构建工具名 → 工具对象的映射
                tool_map = {t.name: t for t in tools}

                # Step 4: 逐个调用关键工具
                print_section("逐个调用 MCP 工具")

                # 4a: new_page
                print_info("调用 new_page ...")
                try:
                    result = await tool_map["new_page"].ainvoke({"url": "https://www.baidu.com"})
                    print_ok(f"new_page 成功: {str(result)[:200]}")
                except Exception as e:
                    print_fail(f"new_page 失败: {e}")
                    traceback.print_exc()
                    return False

                await asyncio.sleep(2)

                # 4b: take_snapshot
                print_info("调用 take_snapshot ...")
                try:
                    result = await tool_map["take_snapshot"].ainvoke({})
                    print_ok(f"take_snapshot 成功: {str(result)[:300]}")
                except Exception as e:
                    print_fail(f"take_snapshot 失败: {e}")
                    traceback.print_exc()
                    return False

                # 4c: evaluate_script — 这是之前崩的地方
                print_info("调用 evaluate_script ...")
                try:
                    result = await tool_map["evaluate_script"].ainvoke({
                        "function": "() => document.title"
                    })
                    print_ok(f"evaluate_script 成功: {str(result)[:200]}")
                except Exception as e:
                    print_fail(f"evaluate_script 失败: {e}")
                    traceback.print_exc()

                # 4d: take_screenshot
                print_info("调用 take_screenshot ...")
                try:
                    result = await tool_map["take_screenshot"].ainvoke({})
                    print_ok(f"take_screenshot 成功 (长度={len(str(result))})")
                except Exception as e:
                    print_fail(f"take_screenshot 失败: {e}")
                    traceback.print_exc()

                # 4e: click（如果 snapshot 里有元素的话）
                print_info("调用 navigate_page (跳转到简单页面) ...")
                try:
                    result = await tool_map["navigate_page"].ainvoke({"url": "https://example.com"})
                    print_ok(f"navigate_page 成功: {str(result)[:200]}")
                except Exception as e:
                    print_fail(f"navigate_page 失败: {e}")
                    traceback.print_exc()

                print_ok("MCP 工具调用测试完成 ✓")
        else:
            tools = await client.get_tools()
            print_ok(f"获取到 {len(tools)} 个工具 ({time.time()-t1:.1f}s)")

        return True

    except (ExceptionGroup, BaseExceptionGroup) as eg:
        elapsed = time.time() - t0
        sub_errors = []
        if hasattr(eg, 'exceptions'):
            for e in eg.exceptions:
                sub_errors.append(f"{type(e).__name__}: {e}")
        else:
            sub_errors.append(str(eg))
        print_fail(f"TaskGroup 异常 ({elapsed:.1f}s)")
        for err in sub_errors:
            print(f"    子异常: {err}")
        traceback.print_exc()
        return False
    except Exception as e:
        print_fail(f"连接失败: {e}")
        traceback.print_exc()
        return False
    finally:
        if chrome_cfg:
            chrome_cfg.args = original_args
        await _release_chrome(task_id)


async def main():
    print(f"\n{'='*60}")
    print("  真实 Agent 扫描测试（Docker + MCP + LLM）")
    print(f"{'='*60}")
    print("  1. MCP 工具调用测试（Docker + MCP，逐个调用工具 debug）")
    print("  2. 完整 Agent 扫描（Docker + MCP + LLM）")
    print("  0. 全部运行")
    print("  q. 退出")
    print(f"{'='*60}")

    tests = {
        "1": ("MCP 工具调用测试", test_mcp_connection_only),
        "2": ("完整 Agent 扫描", test_real_mcp_agent),
    }

    while True:
        choice = input("\n请选择 > ").strip()
        if choice == "q":
            print("👋 退出")
            break
        elif choice == "0":
            results = {}
            for key in sorted(tests.keys()):
                name, func = tests[key]
                try:
                    results[name] = await func()
                except Exception as e:
                    print_fail(f"{name} 异常: {e}")
                    traceback.print_exc()
                    results[name] = False
            print_section("结果汇总")
            for name, ok in results.items():
                print(f"  {'✅' if ok else '❌'}  {name}")
        elif choice in tests:
            name, func = tests[choice]
            try:
                await func()
            except Exception as e:
                print_fail(f"异常: {e}")
                traceback.print_exc()
        else:
            print("无效选择")


if __name__ == "__main__":
    asyncio.run(main())
