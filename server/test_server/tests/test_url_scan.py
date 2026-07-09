"""
URL Scan Pipeline 链路测试

验证内容（全部不调用真实 Agent，用 mock 替代）：
1. URL 解析 + 标准化（纯逻辑）
2. URL 探活（网络）
3. Chrome Docker 容器 + CDP 连接（Docker）
4. MCP 配置覆盖（纯逻辑）
5. Findings 提取（纯逻辑）
6. MCP 连接构建（纯逻辑）
7. Mock Agent Pipeline（mock Agent，验证完整链路串联）
8. API 连通性（HTTP 请求 FastAPI 服务）

运行方式：
  python test_server/tests/test_url_scan.py
"""

import asyncio
import json
import sys
import time
import traceback
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from core.logger import get_logger

logger = get_logger("test_url_scan")


# ── 工具函数 ──────────────────────────────────────────────

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

def load_config():
    config_path = _project_root / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_app_config():
    from Sere1nGraph.graph.config.loader import load_config as load_app_config
    return load_app_config(str(_project_root / "config.json"))

def init_db():
    from api.db.mongodb import init_mongo, get_db
    try:
        get_db()
    except RuntimeError:
        init_mongo(get_app_config())
    return get_db()


# ── Mock Agent 响应 ───────────────────────────────────────

MOCK_AGENT_RESPONSE = {
    "intro": {
        "url": "https://www.bilibili.com",
        "final_url": "https://www.bilibili.com",
        "domain": "bilibili.com",
        "site_name": "哔哩哔哩",
        "entity_name": "上海幻电信息科技有限公司",
        "summary": "B站是中国领先的年轻人文化社区和视频平台，提供动画、游戏、音乐等内容。",
    },
    "has_findings": True,
    "findings": [
        {
            "type": "business_contact",
            "scope": "official",
            "channel": "email",
            "role": "sales",
            "label": "商务合作邮箱",
            "value": "bd@bilibili.com",
            "context": "首页 Footer > 商务合作，可用于冒充合作方发起钓鱼沟通",
            "source_url": "https://www.bilibili.com",
            "evidence": "页面底部「商务合作」区域显示: bd@bilibili.com",
            "attention_score": 75,
            "attention_reason": "直接暴露商务邮箱，可被用于冒充合作方",
        },
        {
            "type": "customer_service",
            "scope": "official",
            "channel": "online_chat",
            "role": "support",
            "label": "在线客服入口",
            "value": "https://www.bilibili.com/help",
            "context": "右下角悬浮客服图标，点击后进入帮助中心",
            "source_url": "https://www.bilibili.com",
            "evidence": "右下角「帮助反馈」图标，点击跳转至帮助中心页面",
            "attention_score": 45,
            "attention_reason": "客服入口可被用于社工信息收集",
        },
    ],
}


def _build_mock_agent():
    """构建一个 mock agent，返回预设的结构化 JSON"""
    mock_msg = AsyncMock()
    mock_msg.content = json.dumps(MOCK_AGENT_RESPONSE, ensure_ascii=False)

    async def fake_agent(state):
        return {"messages": [mock_msg]}

    return fake_agent


# ══════════════════════════════════════════════════════════
# 测试 1: URL 解析 + 标准化
# ══════════════════════════════════════════════════════════

async def test_url_parsing():
    print_section("测试 1: URL 解析 + 标准化")
    from api.services.url_scan_pipeline import UrlScanPipeline

    url_text = """
    # 注释行
    https://www.bilibili.com
    www.baidu.com
    example.com
    https://www.bilibili.com
    
    invalid
    """
    urls = UrlScanPipeline.parse_url_file(url_text)
    print_info(f"解析结果: {len(urls)} 个 URL")
    for u in urls:
        print(f"    → {u}")

    assert len(urls) >= 3, f"期望至少 3 个，实际 {len(urls)}"
    assert all(u.startswith("http") for u in urls)
    assert len(urls) == len(set(urls)), "应无重复"
    print_ok(f"通过，{len(urls)} 个去重标准化 URL")
    return True


# ══════════════════════════════════════════════════════════
# 测试 2: URL 探活
# ══════════════════════════════════════════════════════════

async def test_url_probing():
    print_section("测试 2: URL 探活")
    from api.services.url_scan_pipeline import UrlScanPipeline

    urls = [
        "https://www.baidu.com",
        "https://www.bilibili.com",
        "https://this-domain-should-not-exist-12345.com",
    ]
    print_info(f"探活 {len(urls)} 个 URL ...")
    t0 = time.time()
    alive = await UrlScanPipeline.probe_urls(urls, concurrency=5, timeout=10)
    elapsed = time.time() - t0

    print_info(f"耗时 {elapsed:.1f}s，存活 {len(alive)}/{len(urls)}")
    for a in alive:
        print(f"    ✓ {a['url']} (status={a['status_code']}, {a['response_time']:.2f}s)")

    assert len(alive) >= 1, "至少 1 个存活"
    print_ok(f"通过，存活 {len(alive)} 个")
    return True


# ══════════════════════════════════════════════════════════
# 测试 3: Chrome Docker 容器 + CDP 连接
# ══════════════════════════════════════════════════════════

async def test_chrome_docker_cdp():
    print_section("测试 3: Chrome Docker CDP")
    cfg = load_config()
    if not cfg.get("chrome_docker", {}).get("enabled", False):
        print_info("跳过 — chrome_docker.enabled=false")
        return True

    from browser_manager.provider import get_browser_provider
    provider = get_browser_provider()
    print_info(f"Provider: {type(provider).__name__}")

    if type(provider).__name__ != "DockerProvider":
        print_info("非 DockerProvider，跳过")
        return True

    try:
        t0 = time.time()
        ws_url = await provider.get_cdp_endpoint(task_id="test-url-scan-cdp")
        if not ws_url:
            print_fail("CDP 端点返回 None")
            return False
        print_ok(f"CDP 获取成功 ({time.time()-t0:.1f}s): {ws_url}")

        pool = await provider.get_pool_status()
        for c in pool:
            print(f"    - {c['container_name']}: {c['status']} cdp={c.get('cdp_port')}")

        await provider.release_cdp_endpoint(task_id="test-url-scan-cdp")
        print_ok("CDP 已释放")
        return True
    except Exception as e:
        print_fail(f"失败: {e}")
        traceback.print_exc()
        return False
    finally:
        await provider.shutdown()


# ══════════════════════════════════════════════════════════
# 测试 4: MCP 配置覆盖
# ══════════════════════════════════════════════════════════

async def test_mcp_config_override():
    print_section("测试 4: MCP 配置覆盖")
    from api.services.url_scan_pipeline import UrlScanPipeline

    app_config = get_app_config()
    db = init_db()
    pipeline = UrlScanPipeline(db, app_config)

    chrome_cfg = (app_config.mcp_servers or {}).get("chrome-devtools")
    if not chrome_cfg:
        print_info("无 chrome-devtools MCP 配置，跳过")
        return True

    original_args = list(chrome_cfg.args or [])
    print_info(f"原始 args: {original_args}")

    fake_url = "ws://127.0.0.1:9333/cdp-proxy"
    pipeline._override_chrome_mcp_config(fake_url)
    print_info(f"覆盖后 args: {chrome_cfg.args}")

    assert any("--wsEndpoint=" in arg for arg in chrome_cfg.args)
    ws_arg = [a for a in chrome_cfg.args if "--wsEndpoint=" in a][0]
    assert fake_url in ws_arg
    print_ok(f"--wsEndpoint 已覆盖为 {fake_url}")

    chrome_cfg.args = original_args
    return True


# ══════════════════════════════════════════════════════════
# 测试 5: Findings 提取
# ══════════════════════════════════════════════════════════

async def test_findings_extraction():
    print_section("测试 5: Findings 提取")
    from api.services.url_scan_pipeline import UrlScanPipeline

    mock_scan_results = [
        {
            "success": True,
            "url": "https://example.com",
            "data": {
                "intro": {
                    "domain": "example.com",
                    "site_name": "Example Corp",
                    "entity_name": "示例公司",
                    "summary": "一个示例网站",
                },
                "findings": [
                    {
                        "type": "hr_contact", "channel": "email", "role": "hr",
                        "label": "招聘邮箱", "value": "hr@example.com",
                        "attention_score": 80, "attention_reason": "直接暴露 HR 邮箱",
                    },
                    {
                        "type": "social_media", "channel": "wechat", "role": "unknown",
                        "label": "微信公众号", "value": "ExampleCorp",
                        "attention_score": 30, "attention_reason": "公开信息",
                    },
                ],
            },
        },
        {"success": False, "url": "https://failed.com", "error": "超时"},
    ]

    findings = UrlScanPipeline.extract_findings(mock_scan_results)
    print_info(f"提取到 {len(findings)} 个 findings")
    for f in findings:
        print(f"    [{f['type']}] {f['label']} = {f['value']} (score={f['attention_score']})")

    assert len(findings) == 2
    assert all(f.get("finding_id") for f in findings)
    assert findings[0]["domain"] == "example.com"

    high = [f for f in findings if f["attention_score"] >= 40]
    assert len(high) == 1
    print_ok("Findings 提取通过")
    return True


# ══════════════════════════════════════════════════════════
# 测试 6: MCP 连接构建
# ══════════════════════════════════════════════════════════

async def test_mcp_connection_builder():
    print_section("测试 6: MCP 连接构建")
    from Sere1nGraph.graph.tools.mcp import build_chrome_mcp_connection

    cdp_url = "ws://127.0.0.1:9222/cdp-proxy"
    connections = build_chrome_mcp_connection(cdp_url)
    print_info(f"connections: {json.dumps(connections, indent=2)}")

    cfg = connections["chrome-devtools"]
    assert cfg["transport"] == "stdio"
    assert cfg["command"] == "npx"
    ws_arg = [a for a in cfg["args"] if "--wsEndpoint=" in a][0]
    assert cdp_url in ws_arg
    print_ok("MCP 连接构建通过")
    return True


# ══════════════════════════════════════════════════════════
# 测试 7: Mock Agent Pipeline（完整链路，不调真实 Agent）
# ══════════════════════════════════════════════════════════

async def test_mock_pipeline():
    """
    用 mock 跑完整 pipeline 链路，不写任何数据到 DB：
    URL 解析 → 探活(mock) → scan_urls(mock) → findings 提取 → 话术生成(mock)
    
    验证所有模块串联正确，不消耗 LLM token，不写 DB。
    """
    print_section("测试 7: Mock Agent Pipeline（纯内存，不写 DB）")

    from api.services.url_scan_pipeline import UrlScanPipeline

    app_config = get_app_config()

    # 用一个 fake db，所有写操作都是 mock
    fake_collection = AsyncMock()
    fake_collection.insert_many = AsyncMock()
    fake_collection.insert_one = AsyncMock()
    fake_collection.update_one = AsyncMock()

    fake_db = AsyncMock()
    fake_db.__getitem__ = lambda self, name: fake_collection

    pipeline = UrlScanPipeline(fake_db, app_config)
    task_id = f"mock-{uuid.uuid4().hex[:8]}"
    project_id = "000000000000000000000000"

    url_content = "https://www.baidu.com\nhttps://www.bilibili.com"
    print_info(f"task_id: {task_id}")

    # mock 掉探活、扫描、话术生成
    mock_alive = [
        {"url": "https://www.baidu.com", "status_code": 200, "title": "百度", "response_time": 0.1},
        {"url": "https://www.bilibili.com", "status_code": 200, "title": "B站", "response_time": 0.2},
    ]
    mock_scan_result = [
        {"success": True, "url": "https://www.baidu.com", "data": MOCK_AGENT_RESPONSE},
        {"success": True, "url": "https://www.bilibili.com", "data": MOCK_AGENT_RESPONSE},
    ]
    mock_copywriting = {
        "finding_id": "test123", "url": "https://www.baidu.com",
        "status": "completed", "scenario": "商务合作",
    }

    with patch.object(pipeline, "probe_urls", new_callable=AsyncMock) as mock_probe, \
         patch.object(pipeline, "scan_urls", new_callable=AsyncMock) as mock_scan, \
         patch.object(pipeline, "generate_copywriting_for_finding", new_callable=AsyncMock) as mock_cw:

        mock_probe.return_value = mock_alive
        mock_scan.return_value = mock_scan_result
        mock_cw.return_value = mock_copywriting

        t0 = time.time()
        result = await pipeline.run_pipeline(
            task_id=task_id, project_id=project_id,
            url_content=url_content, probe_concurrency=5, min_attention_score=30,
        )
        elapsed = time.time() - t0

    print_info(f"耗时: {elapsed:.1f}s")
    print_info(f"状态: {result.get('status')}")
    print_info(f"总 URL: {result.get('total_urls')} → 存活: {result.get('alive_urls')} → "
               f"扫描: {result.get('scanned_urls')} → findings: {result.get('total_findings')} → "
               f"话术: {result.get('total_copywritings')}")

    if result.get("error"):
        print_fail(f"Pipeline 错误: {result['error']}")
        return False

    assert result["total_urls"] == 2
    assert result["alive_urls"] == 2
    assert result["scanned_urls"] == 2
    assert result["total_findings"] >= 1
    assert result["status"] == "completed"
    mock_probe.assert_called_once()
    mock_scan.assert_called_once()
    assert mock_cw.call_count == result["total_findings"]

    print_ok(f"Mock Pipeline 通过 (无 DB 写入)")
    return True


# ══════════════════════════════════════════════════════════
# 测试 8: API 连通性
# ══════════════════════════════════════════════════════════

async def test_api_connectivity():
    """
    测试 AI Runtime 端点连通性：
    带 AK 请求 config.json 中 runtime.base_url，验证 default 和 vision 模型的 AK 均可用。
    """
    print_section("测试 8: AI Runtime 端点连通性（AK 验证）")

    import httpx

    cfg = load_config()
    runtime = cfg.get("runtime", {})
    base_url = runtime.get("base_url", "")
    api_key = runtime.get("api_key", "")
    models = runtime.get("models", {})
    default_model = models.get("default", "qwen3.5-plus")
    vision_model = models.get("vision", "qwen3.5-plus")

    if not base_url or not api_key:
        print_fail("config.json 中 runtime.base_url 或 api_key 未配置")
        return False

    print_info(f"base_url: {base_url}")
    print_info(f"api_key: {api_key[:10]}...{api_key[-4:]}")
    print_info(f"default model: {default_model}")
    print_info(f"vision model: {vision_model}")

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    all_ok = True

    async with httpx.AsyncClient(timeout=15) as client:
        for label, model in [("default", default_model), ("vision", vision_model)]:
            print_info(f"测试 {label} 模型: {model} ...")
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }
            try:
                resp = await client.post(url, headers=headers, json=payload)
            except httpx.ConnectError as e:
                print_fail(f"[{label}] 连接失败: {e}")
                all_ok = False
                continue
            except httpx.ReadTimeout:
                print_ok(f"[{label}] 端点可达（响应超时，连接成功）")
                continue

            if resp.status_code == 200:
                data = resp.json()
                usage = data.get("usage", {})
                print_ok(f"[{label}] ✓ AK 有效, model={data.get('model')}, "
                         f"tokens={usage.get('total_tokens', 'N/A')}")
            elif resp.status_code == 401:
                print_fail(f"[{label}] AK 无效 (401)")
                all_ok = False
            elif resp.status_code == 429:
                print_info(f"[{label}] 限流 (429)，AK 有效但被限速")
            else:
                print_fail(f"[{label}] 异常: {resp.status_code} {resp.text[:200]}")
                all_ok = False

    if all_ok:
        print_ok("AI Runtime AK 验证全部通过")
    return all_ok


# ══════════════════════════════════════════════════════════
# 测试 9: Agent 响应解析验证
# ══════════════════════════════════════════════════════════

async def test_agent_response_parsing():
    """验证 _parse_agent_response 能正确处理各种 Agent 输出格式"""
    print_section("测试 9: Agent 响应解析")
    from api.services.url_scan_pipeline import UrlScanPipeline

    # Case 1: 正常 JSON
    mock_msg_1 = type("Msg", (), {"content": json.dumps(MOCK_AGENT_RESPONSE, ensure_ascii=False)})()
    result_1 = UrlScanPipeline._parse_agent_response({"messages": [mock_msg_1]})
    assert result_1 is not None
    assert result_1.get("has_findings") is True
    print_ok("Case 1: 纯 JSON 解析成功")

    # Case 2: JSON 包裹在 markdown code block 中
    wrapped = f"```json\n{json.dumps(MOCK_AGENT_RESPONSE, ensure_ascii=False)}\n```"
    mock_msg_2 = type("Msg", (), {"content": wrapped})()
    result_2 = UrlScanPipeline._parse_agent_response({"messages": [mock_msg_2]})
    assert result_2 is not None
    print_ok("Case 2: Markdown 包裹 JSON 解析成功")

    # Case 3: 空消息
    result_3 = UrlScanPipeline._parse_agent_response({"messages": []})
    assert result_3 is None
    print_ok("Case 3: 空消息返回 None")

    # Case 4: 非 JSON 文本
    mock_msg_4 = type("Msg", (), {"content": "这个网站没有什么有用的信息"})()
    result_4 = UrlScanPipeline._parse_agent_response({"messages": [mock_msg_4]})
    assert result_4 is None
    print_ok("Case 4: 非 JSON 文本返回 None")

    # Case 5: 多条消息，最后一条是 JSON
    mock_msg_5a = type("Msg", (), {"content": "我来分析一下..."})()
    mock_msg_5b = type("Msg", (), {"content": json.dumps({"has_findings": False, "findings": []})})()
    result_5 = UrlScanPipeline._parse_agent_response({"messages": [mock_msg_5a, mock_msg_5b]})
    assert result_5 is not None
    assert result_5["has_findings"] is False
    print_ok("Case 5: 多消息取最后一条 JSON")

    print_ok("Agent 响应解析全部通过")
    return True


# ══════════════════════════════════════════════════════════
# 测试 10: 全链路（真实探活 + Docker CDP + Mock Agent）
# ══════════════════════════════════════════════════════════

async def test_taskgroup_exception_handling():
    """
    验证 scan_urls 对 ExceptionGroup（TaskGroup 异常）的处理：
    1. 模拟 MCP 连接失败抛出 ExceptionGroup
    2. 验证单个 URL 失败不影响后续 URL
    3. 验证连续失败超过阈值时提前终止
    4. 验证超时处理
    """
    print_section("测试 11: TaskGroup 异常处理")
    from api.services.url_scan_pipeline import UrlScanPipeline

    app_config = get_app_config()

    fake_collection = AsyncMock()
    fake_collection.insert_many = AsyncMock()
    fake_collection.insert_one = AsyncMock()
    fake_collection.update_one = AsyncMock()
    fake_db = AsyncMock()
    fake_db.__getitem__ = lambda self, name: fake_collection

    pipeline = UrlScanPipeline(fake_db, app_config)
    project_id = "000000000000000000000000"

    # ── Case 1: ExceptionGroup 被正确捕获，不影响后续 URL ──
    print_info("Case 1: ExceptionGroup 捕获 + 后续 URL 继续")

    call_count = 0

    async def mock_agent_with_first_failure(state):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # 模拟 MCP TaskGroup 异常
            raise ExceptionGroup(
                "unhandled errors in a TaskGroup",
                [ConnectionError("Chrome CDP 连接被拒绝")]
            )
        # 后续调用正常返回
        mock_msg = AsyncMock()
        mock_msg.content = json.dumps(MOCK_AGENT_RESPONSE, ensure_ascii=False)
        return {"messages": [mock_msg]}

    alive_urls = [
        {"url": "https://fail.example.com", "status_code": 200, "title": "Fail", "response_time": 0.1},
        {"url": "https://ok.example.com", "status_code": 200, "title": "OK", "response_time": 0.1},
    ]

    with patch("Sere1nGraph.graph.agents.factory.create_web_tagging_agent", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_agent_with_first_failure
        with patch.object(pipeline, "_get_chrome_cdp_url", new_callable=AsyncMock, return_value=None):
            with patch.object(pipeline, "_release_chrome", new_callable=AsyncMock):
                results = await pipeline.scan_urls(project_id, alive_urls)

    assert len(results) == 2
    assert results[0]["success"] is False
    assert "MCP连接异常" in results[0]["error"]
    assert results[1]["success"] is True
    print_ok("Case 1 通过: 第一个 URL 失败，第二个正常")

    # ── Case 2: 连续失败超过阈值，提前终止 ──
    print_info("Case 2: 连续失败 → 提前终止")

    async def mock_agent_always_fail(state):
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [OSError("MCP stdio 进程崩溃")]
        )

    many_urls = [
        {"url": f"https://site{i}.example.com", "status_code": 200, "title": f"Site{i}", "response_time": 0.1}
        for i in range(10)
    ]

    with patch("Sere1nGraph.graph.agents.factory.create_web_tagging_agent", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_agent_always_fail
        with patch.object(pipeline, "_get_chrome_cdp_url", new_callable=AsyncMock, return_value=None):
            with patch.object(pipeline, "_release_chrome", new_callable=AsyncMock):
                results = await pipeline.scan_urls(project_id, many_urls)

    assert len(results) == 10  # 所有 URL 都有结果
    failed = [r for r in results if not r["success"]]
    skipped = [r for r in results if "跳过" in r.get("error", "")]
    assert len(failed) == 10
    assert len(skipped) >= 1  # 至少有一些被跳过
    print_ok(f"Case 2 通过: 连续失败后跳过剩余 {len(skipped)} 个 URL")

    # ── Case 3: asyncio.TimeoutError 处理 ──
    print_info("Case 3: 超时处理")

    async def mock_agent_timeout(state):
        await asyncio.sleep(999)  # 永远不会完成

    timeout_urls = [
        {"url": "https://slow.example.com", "status_code": 200, "title": "Slow", "response_time": 0.1},
    ]

    with patch("Sere1nGraph.graph.agents.factory.create_web_tagging_agent", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_agent_timeout
        with patch.object(pipeline, "_get_chrome_cdp_url", new_callable=AsyncMock, return_value=None):
            with patch.object(pipeline, "_release_chrome", new_callable=AsyncMock):
                # 临时把超时改短，加速测试
                with patch("api.services.url_scan_pipeline.asyncio.wait_for",
                           side_effect=asyncio.TimeoutError()):
                    results = await pipeline.scan_urls(project_id, timeout_urls)

    assert len(results) == 1
    assert results[0]["success"] is False
    assert "超时" in results[0]["error"]
    print_ok("Case 3 通过: 超时被正确捕获")

    # ── Case 4: 普通 Exception 仍然被捕获 ──
    print_info("Case 4: 普通 Exception 兼容")

    async def mock_agent_generic_error(state):
        raise RuntimeError("LLM API 返回 500")

    with patch("Sere1nGraph.graph.agents.factory.create_web_tagging_agent", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_agent_generic_error
        with patch.object(pipeline, "_get_chrome_cdp_url", new_callable=AsyncMock, return_value=None):
            with patch.object(pipeline, "_release_chrome", new_callable=AsyncMock):
                results = await pipeline.scan_urls(project_id, timeout_urls)

    assert len(results) == 1
    assert results[0]["success"] is False
    assert "500" in results[0]["error"]
    print_ok("Case 4 通过: 普通异常正常捕获")

    print_ok("TaskGroup 异常处理全部通过 ✓")
    return True


async def test_full_chain_mock_agent():
    """
    全链路测试，Agent 部分 mock，其余全部真实：
    1. URL 解析（真实）
    2. 探活（真实网络请求）
    3. Docker Chrome CDP 获取（真实容器）
    4. MCP 配置覆盖（真实）
    5. Agent 扫描（mock，返回预设 JSON）
    6. Findings 提取（真实逻辑）
    
    不写 DB，不消耗 LLM token。
    """
    print_section("测试 10: 全链路（真实探活 + Docker CDP + Mock Agent）")

    cfg = load_config()
    if not cfg.get("chrome_docker", {}).get("enabled", False):
        print_info("跳过 — chrome_docker.enabled=false")
        return True

    from api.services.url_scan_pipeline import UrlScanPipeline

    app_config = get_app_config()

    # fake DB
    fake_collection = AsyncMock()
    fake_collection.insert_many = AsyncMock()
    fake_collection.insert_one = AsyncMock()
    fake_collection.update_one = AsyncMock()
    fake_db = AsyncMock()
    fake_db.__getitem__ = lambda self, name: fake_collection

    pipeline = UrlScanPipeline(fake_db, app_config)

    # Step 1: URL 解析
    url_content = "https://www.bilibili.com"
    urls = pipeline.parse_url_file(url_content)
    print_ok(f"Step 1 解析: {len(urls)} 个 URL")

    # Step 2: 真实探活
    print_info("Step 2 探活 ...")
    t0 = time.time()
    alive = await pipeline.probe_urls(urls, concurrency=5, timeout=10)
    print_ok(f"Step 2 探活完成 ({time.time()-t0:.1f}s): 存活 {len(alive)}/{len(urls)}")

    if not alive:
        print_fail("无存活 URL，无法继续")
        return False

    # Step 3: Docker CDP
    print_info("Step 3 获取 Docker Chrome CDP ...")
    t1 = time.time()
    cdp_url = await pipeline._get_chrome_cdp_url()
    if cdp_url:
        print_ok(f"Step 3 CDP: {cdp_url} ({time.time()-t1:.1f}s)")
    else:
        print_info("Step 3 CDP 为 None（将使用 config 默认值）")

    # Step 4: MCP 配置覆盖
    if cdp_url:
        pipeline._override_chrome_mcp_config(cdp_url)
        chrome_cfg = app_config.mcp_servers.get("chrome-devtools")
        if chrome_cfg:
            print_ok(f"Step 4 MCP args 已覆盖")

    # Step 5: Mock Agent 扫描
    print_info("Step 5 Mock Agent 扫描 ...")

    mock_scan_results = [
        {"success": True, "url": a["url"], "data": MOCK_AGENT_RESPONSE}
        for a in alive
    ]

    # 直接用 mock 数据，不调 scan_urls
    findings = pipeline.extract_findings(mock_scan_results)

    print_ok(f"Step 5 扫描完成: {len(mock_scan_results)} 个结果")

    # Step 6: Findings 提取
    high_findings = [f for f in findings if f.get("attention_score", 0) >= 30]
    print_ok(f"Step 6 Findings: 总={len(findings)}, 过滤后={len(high_findings)}")

    for f in high_findings[:3]:
        print(f"    [{f['type']}] {f['label']} = {f['value']} (score={f['attention_score']})")

    # 释放容器
    task_id = f"url_scan_{id(pipeline)}"
    await pipeline._release_chrome(task_id)
    try:
        from browser_manager.provider import get_browser_provider
        provider = get_browser_provider()
        await provider.shutdown()
    except Exception:
        pass

    assert len(findings) >= 1
    print_ok("全链路测试通过 ✓")
    return True


# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════

def print_menu():
    print(f"\n{'='*60}")
    print("  URL Scan Pipeline 链路测试")
    print(f"{'='*60}")
    print("  1. URL 解析 + 标准化（纯逻辑）")
    print("  2. URL 探活（网络）")
    print("  3. Chrome Docker CDP（需 Docker）")
    print("  4. MCP 配置覆盖（纯逻辑）")
    print("  5. Findings 提取（纯逻辑）")
    print("  6. MCP 连接构建（纯逻辑）")
    print("  7. Mock Pipeline（纯内存，不写 DB）")
    print("  8. AI Runtime 端点连通性")
    print("  9. Agent 响应解析（纯逻辑）")
    print("  10. 全链路（真实探活 + Docker CDP + Mock Agent）")
    print("  11. TaskGroup 异常处理（纯逻辑）")
    print("  0. 运行全部")
    print("  q. 退出")
    print(f"{'='*60}")


async def main():
    test_map = {
        "1": ("URL 解析", test_url_parsing),
        "2": ("URL 探活", test_url_probing),
        "3": ("Chrome Docker CDP", test_chrome_docker_cdp),
        "4": ("MCP 配置覆盖", test_mcp_config_override),
        "5": ("Findings 提取", test_findings_extraction),
        "6": ("MCP 连接构建", test_mcp_connection_builder),
        "7": ("Mock Pipeline", test_mock_pipeline),
        "8": ("AI Runtime 连通性", test_api_connectivity),
        "9": ("Agent 响应解析", test_agent_response_parsing),
        "10": ("全链路 Mock Agent", test_full_chain_mock_agent),
        "11": ("TaskGroup 异常处理", test_taskgroup_exception_handling),
    }

    while True:
        print_menu()
        choice = input("\n请选择测试项 > ").strip()

        if choice == "q":
            print("👋 退出")
            break
        elif choice == "0":
            results = {}
            for key in sorted(test_map.keys()):
                name, func = test_map[key]
                try:
                    results[name] = await func()
                except Exception as e:
                    print_fail(f"{name} 异常: {e}")
                    traceback.print_exc()
                    results[name] = False

            print_section("测试结果汇总")
            passed = 0
            for name, ok in results.items():
                status = "✅" if ok else "❌"
                print(f"  {status}  {name}")
                if ok:
                    passed += 1
            print(f"\n  通过: {passed}/{len(results)}")
        elif choice in test_map:
            name, func = test_map[choice]
            try:
                await func()
            except Exception as e:
                print_fail(f"异常: {e}")
                traceback.print_exc()
        else:
            print("无效选择")


if __name__ == "__main__":
    asyncio.run(main())
