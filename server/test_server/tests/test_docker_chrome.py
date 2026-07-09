"""
Chrome Docker 容器化测试

测试内容：
1. Docker 镜像构建验证
2. 容器生命周期（创建 → 连接 → 释放 → 销毁）
3. 通过 Docker Chrome 进行 Playwright 连接
4. Docker Chrome 截图（抖音）
5. 多容器并发测试
6. DouyinCrawler 通过 Docker Chrome 实际搜索

前置条件：
- Docker 已安装并运行
- chrome-browser 镜像已构建: docker build -t chrome-browser:latest ./chrome-browser
- douyin_cookie.txt 存在于 test_server/tests/ 目录

运行方式：
  python test_server/tests/test_docker_chrome.py
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

import httpx

# 确保项目根目录在 sys.path
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# 配置日志 - 显示详细的连接过程
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
# 让 browser_manager 的日志也输出
logging.getLogger("browser_manager").setLevel(logging.DEBUG)


# ── 工具函数 ──────────────────────────────────────────────

def load_cookie(filename: str = "douyin_cookie.txt") -> str:
    """从 txt 文件加载 cookie"""
    cookie_path = Path(__file__).parent / filename
    if not cookie_path.exists():
        print(f"❌ Cookie 文件不存在: {cookie_path}")
        return ""
    return cookie_path.read_text(encoding="utf-8").strip()


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ── 测试 1: Docker 镜像检查 ──────────────────────────────

async def test_docker_image_exists():
    """检查 chrome-browser 镜像是否已构建"""
    print_section("测试 1: Docker 镜像检查")

    try:
        import docker
        client = docker.from_env()
        images = client.images.list(name="chrome-browser")
        if images:
            img = images[0]
            print(f"✅ 镜像存在: chrome-browser")
            print(f"   ID: {img.id[:20]}")
            print(f"   Tags: {img.tags}")
            return True
        else:
            print("❌ 镜像不存在，请先构建:")
            print("   docker build -t chrome-browser:latest ./chrome-browser")
            return False
    except ImportError:
        print("❌ docker-py 未安装: pip install docker")
        return False
    except Exception as e:
        print(f"❌ Docker 连接失败: {e}")
        return False


# ── 测试 2: 容器生命周期 ─────────────────────────────────

async def test_container_lifecycle():
    """测试容器的创建 → 健康检查 → 释放 → 销毁"""
    print_section("测试 2: 容器生命周期")

    from browser_manager.provider import DockerProvider, ChromeDockerConfig

    config = ChromeDockerConfig(
        enabled=True,
        max_containers=2,
        idle_timeout=60,
        vnc_password="test123",
        api_token="sere1n@chrome2026",
    )
    provider = DockerProvider(config)

    try:
        # 创建
        print("📦 创建容器...")
        t0 = time.time()
        ws_url = await provider.get_cdp_endpoint(task_id="test-lifecycle")
        t1 = time.time()
        print(f"✅ 容器创建成功 ({t1-t0:.1f}s)")
        print(f"   CDP URL: {ws_url}")

        # 状态检查
        status = await provider.get_pool_status()
        print(f"   容器数: {len(status)}")
        for s in status:
            print(f"   - {s['container_name']}: {s['status']} (task={s['task_id']})")
            print(f"     noVNC: {s['novnc_url']}")

        # 释放
        print("🔓 释放容器...")
        await provider.release_cdp_endpoint(task_id="test-lifecycle")
        status = await provider.get_pool_status()
        for s in status:
            print(f"   - {s['container_name']}: {s['status']}")

        # 复用
        print("♻️  复用空闲容器...")
        ws_url2 = await provider.get_cdp_endpoint(task_id="test-reuse")
        print(f"✅ 复用成功: {ws_url2}")
        await provider.release_cdp_endpoint(task_id="test-reuse")

        print("✅ 生命周期测试通过")
        return True

    except Exception as e:
        print(f"❌ 生命周期测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        print("🗑️  销毁所有容器...")
        await provider.shutdown()
        print("✅ 容器已销毁")


# ── 测试 3: Playwright 连接 Docker Chrome ────────────────

async def test_playwright_connect():
    """通过 Playwright connect_over_cdp 连接 Docker Chrome"""
    print_section("测试 3: Playwright 连接 Docker Chrome")

    from browser_manager.provider import DockerProvider, ChromeDockerConfig
    from playwright.async_api import async_playwright

    config = ChromeDockerConfig(enabled=True, max_containers=1, vnc_password="test123", api_token="sere1n@chrome2026")
    provider = DockerProvider(config)

    try:
        ws_url = await provider.get_cdp_endpoint(task_id="test-playwright")
        print(f"   CDP URL: {ws_url}")

        async with async_playwright() as p:
            print("🔗 Playwright 连接中...")
            browser = await p.chromium.connect_over_cdp(ws_url)
            print(f"✅ 连接成功, browser version: {browser.version}")

            # 创建 context 和 page
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            page = await context.new_page()

            # 访问小红书
            print("🌐 访问 https://www.xiaohongshu.com ...")
            await page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded")
            title = await page.title()
            print(f"✅ 页面标题: {title}")

            # 检查指纹
            ua = await page.evaluate("navigator.userAgent")
            platform = await page.evaluate("navigator.platform")
            webdriver = await page.evaluate("navigator.webdriver")
            print(f"   UA: {ua[:80]}...")
            print(f"   Platform: {platform}")
            print(f"   webdriver: {webdriver}")

            await context.close()

        await provider.release_cdp_endpoint(task_id="test-playwright")
        print("✅ Playwright 连接测试通过")
        return True

    except Exception as e:
        print(f"❌ Playwright 连接测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        await provider.shutdown()


# ── 测试 4: Docker Chrome 截图 ───────────────────────────

async def test_docker_screenshot():
    """通过 Docker Chrome 对小红书页面截图"""
    print_section("测试 4: Docker Chrome 截图（小红书）")

    cookie_str = load_cookie("xhs_cookie.txt")
    if not cookie_str:
        print("⏭️  跳过（无 xhs_cookie.txt，请将小红书 Cookie 粘贴到 test_server/tests/xhs_cookie.txt）")
        return True

    from browser_manager.provider import DockerProvider, ChromeDockerConfig
    from playwright.async_api import async_playwright
    import base64

    config = ChromeDockerConfig(enabled=True, max_containers=1, vnc_password="test123", api_token="sere1n@chrome2026")
    provider = DockerProvider(config)

    try:
        ws_url = await provider.get_cdp_endpoint(task_id="test-screenshot")

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(ws_url)

            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )

            # 注入 stealth.js
            stealth_js = _project_root / "MediaCrawler" / "libs" / "stealth.min.js"
            if stealth_js.exists():
                await context.add_init_script(path=str(stealth_js))
                print("   已注入 stealth.js")

            # 注入 Cookie
            cookies = []
            for item in cookie_str.split(";"):
                item = item.strip()
                if "=" in item:
                    name, value = item.split("=", 1)
                    cookies.append({
                        "name": name.strip(),
                        "value": value.strip(),
                        "domain": ".xiaohongshu.com",
                        "path": "/",
                    })
            await context.add_cookies(cookies)
            print(f"   已注入 {len(cookies)} 个 Cookie")

            page = await context.new_page()

            url = "https://www.xiaohongshu.com"
            print(f"🌐 访问 {url} ...")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(5)

            # 截图
            print("📸 截图中...")
            screenshot_bytes = await page.screenshot()
            b64 = base64.b64encode(screenshot_bytes).decode()

            # 保存到文件
            output_dir = Path(__file__).parent / "xhs_data"
            output_dir.mkdir(exist_ok=True)
            output_path = output_dir / "docker_screenshot_test.png"
            output_path.write_bytes(screenshot_bytes)
            print(f"✅ 截图已保存: {output_path}")
            print(f"   大小: {len(screenshot_bytes)} bytes, base64: {len(b64)} chars")

            await context.close()

        await provider.release_cdp_endpoint(task_id="test-screenshot")
        print("✅ Docker 截图测试通过")
        return True

    except Exception as e:
        print(f"❌ Docker 截图测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        await provider.shutdown()


# ── 测试 5: 多容器并发 ───────────────────────────────────

async def test_concurrent_containers():
    """测试同时创建多个容器"""
    print_section("测试 5: 多容器并发")

    from browser_manager.provider import DockerProvider, ChromeDockerConfig

    config = ChromeDockerConfig(
        enabled=True,
        max_containers=3,
        idle_timeout=60,
        vnc_password="test123",
        api_token="sere1n@chrome2026",
    )
    provider = DockerProvider(config)

    try:
        # 并发获取 3 个容器
        print("📦 并发创建 3 个容器...")
        t0 = time.time()

        tasks = [
            provider.get_cdp_endpoint(task_id=f"concurrent-{i}")
            for i in range(3)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        t1 = time.time()

        success_count = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"   容器 {i}: ❌ {result}")
            else:
                print(f"   容器 {i}: ✅ {result}")
                success_count += 1

        print(f"   总耗时: {t1-t0:.1f}s, 成功: {success_count}/3")

        # 查看状态
        status = await provider.get_pool_status()
        print(f"   容器池状态: {len(status)} 个容器")
        for s in status:
            print(f"   - {s['container_name']}: {s['status']}")

        # 释放所有
        for i in range(3):
            await provider.release_cdp_endpoint(task_id=f"concurrent-{i}")

        print("✅ 并发测试通过" if success_count == 3 else "⚠️  部分容器创建失败")
        return success_count == 3

    except Exception as e:
        print(f"❌ 并发测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        await provider.shutdown()


# ── 测试 6: DouyinCrawler 通过 Docker Chrome 实际搜索 ────

async def test_douyin_crawler_docker():
    """
    使用 DouyinCrawler 通过 Docker Chrome 进行实际的抖音搜索。
    
    这个测试验证完整链路：
    DouyinCrawler → CDPBrowserManager → BrowserProvider → DockerProvider → 容器 Chrome
    → Cookie 登录 → 关键词搜索 → 返回结果
    """
    print_section("测试 6: DouyinCrawler Docker 实际搜索")

    cookie_str = load_cookie("douyin_cookie.txt")
    if not cookie_str:
        print("⏭️  跳过（无 Cookie 文件）")
        return True

    # 确保 config.json 中 docker 模式已启用
    import json
    config_path = _project_root / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    docker_enabled = cfg.get("chrome_docker", {}).get("enabled", False)
    if not docker_enabled:
        print("⏭️  跳过（config.json 中 chrome_docker.enabled=false）")
        return True

    print(f"   Docker 模式: {'启用' if docker_enabled else '禁用'}")
    print(f"   Cookie 长度: {len(cookie_str)} 字符")

    try:
        # 导入 DouyinCrawler
        from crawler_tools.douyin_crawler import DouyinCrawler, DouyinCrawlerConfig

        # 创建配置（使用 Docker 模式）
        dy_config = DouyinCrawlerConfig(
            headless=False,
            enable_cdp_mode=True,
            cdp_headless=False,
        )
        crawler = DouyinCrawler(config=dy_config, config_path=str(config_path))

        # Step 1: Cookie 登录
        print("\n📋 Step 1: Cookie 登录...")
        t0 = time.time()
        login_result = await crawler.login_by_cookie_string(cookie_str)
        t1 = time.time()

        if not login_result.success:
            print(f"❌ 登录失败: {login_result.message}")
            await crawler.close()
            return False

        print(f"✅ 登录成功 ({t1-t0:.1f}s): {login_result.message}")

        # Step 2: 关键词搜索
        keyword = "Python编程"
        count = 3
        print(f"\n🔍 Step 2: 搜索 '{keyword}' (数量={count})...")
        t2 = time.time()
        search_result = await crawler.search_videos(keyword=keyword, count=count)
        t3 = time.time()

        if not search_result.success:
            print(f"❌ 搜索失败: {search_result.message}")
            await crawler.close()
            return False

        print(f"✅ 搜索成功 ({t3-t2:.1f}s): 获取到 {search_result.total} 条结果")

        for i, item in enumerate(search_result.items, 1):
            print(f"   [{i}] {item.get('title', '')[:50]}")
            print(f"       作者: {item.get('nickname')} | 点赞: {item.get('liked_count')}")

        # Step 3: 关闭（会自动释放 Docker 容器）
        print("\n🔒 Step 3: 关闭爬虫（释放 Docker 容器）...")
        await crawler.close()
        print("✅ 爬虫已关闭")

        print(f"\n✅ DouyinCrawler Docker 搜索测试通过 (总耗时 {t3-t0:.1f}s)")
        return True

    except Exception as e:
        print(f"❌ DouyinCrawler Docker 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


# ── 测试 7: XhsCrawler 通过 Docker Chrome 实际搜索 ───────

async def test_xhs_crawler_docker():
    """
    使用 XhsCrawler 通过 Docker Chrome 进行实际的小红书搜索。

    完整链路：
    XhsCrawler → CDPBrowserManager → BrowserProvider → DockerProvider → 容器 Chrome
    → Cookie 登录 → 关键词搜索 → 返回结果
    """
    print_section("测试 7: XhsCrawler Docker 实际搜索")

    cookie_str = load_cookie("xhs_cookie.txt")
    if not cookie_str:
        print("⏭️  跳过（无 xhs_cookie.txt 文件，请将小红书 Cookie 粘贴到 test_server/tests/xhs_cookie.txt）")
        return True

    # 确保 config.json 中 docker 模式已启用
    import json
    config_path = _project_root / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    docker_enabled = cfg.get("chrome_docker", {}).get("enabled", False)
    if not docker_enabled:
        print("⏭️  跳过（config.json 中 chrome_docker.enabled=false）")
        return True

    print(f"   Docker 模式: {'启用' if docker_enabled else '禁用'}")
    print(f"   Cookie 长度: {len(cookie_str)} 字符")

    try:
        from crawler_tools.xhs_crawler import XhsCrawler, CrawlerConfig

        # 创建配置（使用 CDP/Docker 模式）
        xhs_config = CrawlerConfig(
            headless=False,
            enable_cdp_mode=True,
            cdp_headless=False,
        )
        crawler = XhsCrawler(config=xhs_config, config_path=str(config_path))

        # Step 1: Cookie 登录
        print("\n📋 Step 1: Cookie 登录...")
        t0 = time.time()
        login_result = await crawler.login_by_cookie_string(cookie_str)
        t1 = time.time()

        if not login_result.success:
            print(f"❌ 登录失败: {login_result.message}")
            await crawler.close()
            return False

        print(f"✅ 登录成功 ({t1-t0:.1f}s): {login_result.message}")

        # Step 2: 关键词搜索
        keyword = "b站实习"
        print(f"\n🔍 Step 2: 搜索 '{keyword}'...")
        t2 = time.time()
        search_result = await crawler.search_notes(keyword=keyword, page=1)
        t3 = time.time()

        if not search_result.success:
            print(f"❌ 搜索失败: {search_result.message}")
            await crawler.close()
            return False

        print(f"✅ 搜索成功 ({t3-t2:.1f}s): 获取到 {search_result.total} 条结果")

        for i, item in enumerate(search_result.items[:5], 1):
            print(f"   [{i}] {item.get('title', '')[:60]}")
            print(f"       作者: {item['user'].get('nickname', '')} | 点赞: {item.get('liked_count', '0')}")

        # Step 3: 关闭（会自动释放 Docker 容器）
        print("\n🔒 Step 3: 关闭爬虫（释放 Docker 容器）...")
        await crawler.close()
        print("✅ 爬虫已关闭")

        print(f"\n✅ XhsCrawler Docker 搜索测试通过 (总耗时 {t3-t0:.1f}s)")
        return True

    except Exception as e:
        print(f"❌ XhsCrawler Docker 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


# ── 测试 8: VNC 桌面检查（手动查看后关闭） ───────────────

async def test_vnc_inspection():
    """
    创建容器 → 打印 VNC/noVNC 地址 → 等待手动查看 → 按回车后销毁。
    用于验证 VNC 鉴权、桌面环境、Chrome 全屏等。
    """
    print_section("测试 8: VNC 桌面检查")

    from browser_manager.provider import DockerProvider, ChromeDockerConfig, _load_docker_config

    # 从 config.json 读取配置，保持密码一致，但强制启用 VNC
    config = _load_docker_config()
    config.max_containers = 1
    config.enable_vnc = True  # VNC 测试必须启用
    vnc_pwd = config.vnc_password or "(无密码)"
    provider = DockerProvider(config)

    try:
        print("📦 创建容器...")
        ws_url = await provider.get_cdp_endpoint(task_id="test-vnc")

        # 获取容器信息
        status = await provider.get_pool_status()
        if not status:
            print("❌ 无容器信息")
            return False

        info = status[0]
        container_name = info["container_name"]
        novnc_url = info["novnc_url"]

        print(f"\n✅ 容器 {container_name} 已启动")
        print(f"   CDP:   {ws_url}")
        print(f"   noVNC: {novnc_url}")
        print(f"   VNC 密码: {vnc_pwd}")
        print(f"\n🖥️  请在浏览器中打开 noVNC 地址查看桌面：")
        print(f"   {novnc_url}")
        print(f"\n   输入密码 {vnc_pwd} 即可看到全屏 Chrome")

        # 等待用户确认
        input("\n按回车键销毁容器并结束测试 > ")

        await provider.release_cdp_endpoint(task_id="test-vnc")
        print("✅ VNC 检查完成")
        return True

    except Exception as e:
        print(f"❌ VNC 检查失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        print("🗑️  销毁容器...")
        await provider.shutdown()
        print("✅ 容器已销毁")


# ── 测试 9: ENABLE_VNC 环境变量开关验证 ──────────────────

async def test_vnc_toggle():
    """
    验证 ENABLE_VNC 环境变量确实控制了 VNC 的启停。

    Phase 1: ENABLE_VNC=false → 容器启动后 VNC 端口(5900)不可达，
             /status 中不应有 vnc/novnc 进程
    Phase 2: ENABLE_VNC=true  → 容器启动后 VNC 端口(5900)可达，
             /status 中应有 vnc/novnc 进程且存活
    """
    print_section("测试 9: ENABLE_VNC 环境变量开关验证")

    import socket
    from browser_manager.provider import DockerProvider, ChromeDockerConfig

    async def _check_vnc_status(provider: DockerProvider, info: dict, expect_vnc: bool) -> bool:
        """
        检查容器的 VNC 状态是否符合预期。
        - 通过 /status API 检查进程列表
        - 通过 TCP 连接检查 VNC 端口
        """
        api_port = None
        vnc_port = None
        api_token = provider.config.api_token
        headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}
        # 从容器信息中提取端口
        for cid, cinfo in provider.containers.items():
            if cinfo.container_name == info["container_name"]:
                api_port = cinfo.api_port
                vnc_port = cinfo.vnc_port
                break

        if not api_port:
            print("   ❌ 无法获取容器端口信息")
            return False

        ok = True

        # 检查 1: /status API 中的进程列表
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"http://localhost:{api_port}/status",
                    timeout=5,
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    processes = data.get("processes", [])
                    proc_names = [p["name"] for p in processes]
                    has_vnc_proc = "vnc" in proc_names
                    has_novnc_proc = "novnc" in proc_names

                    print(f"   进程列表: {proc_names}")

                    if expect_vnc:
                        if has_vnc_proc and has_novnc_proc:
                            # 进一步检查进程是否存活
                            vnc_alive = next((p["alive"] for p in processes if p["name"] == "vnc"), False)
                            novnc_alive = next((p["alive"] for p in processes if p["name"] == "novnc"), False)
                            if vnc_alive and novnc_alive:
                                print("   ✅ VNC + noVNC 进程存在且存活")
                            else:
                                print(f"   ❌ VNC 进程状态异常: vnc.alive={vnc_alive}, novnc.alive={novnc_alive}")
                                ok = False
                        else:
                            print(f"   ❌ 期望有 vnc/novnc 进程，但未找到")
                            ok = False
                    else:
                        if not has_vnc_proc and not has_novnc_proc:
                            print("   ✅ 无 VNC/noVNC 进程（符合预期）")
                        else:
                            print(f"   ❌ 不应有 vnc/novnc 进程，但发现了: {proc_names}")
                            ok = False
                else:
                    print(f"   ❌ /status API 返回 {resp.status_code}")
                    ok = False
        except Exception as e:
            print(f"   ❌ /status API 请求失败: {e}")
            ok = False

        # 检查 2: TCP 连接 VNC 端口
        if vnc_port:
            vnc_reachable = False
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex(("localhost", vnc_port))
                vnc_reachable = (result == 0)
                sock.close()
            except Exception:
                vnc_reachable = False

            if expect_vnc:
                if vnc_reachable:
                    print(f"   ✅ VNC 端口 {vnc_port} 可达")
                else:
                    print(f"   ❌ VNC 端口 {vnc_port} 不可达（期望可达）")
                    ok = False
            else:
                if not vnc_reachable:
                    print(f"   ✅ VNC 端口 {vnc_port} 不可达（符合预期，未映射）")
                else:
                    print(f"   ⚠️  VNC 端口 {vnc_port} 意外可达（可能被其他服务占用）")
                    # 不算失败，可能是端口碰巧被占用

        return ok

    # ── Phase 1: ENABLE_VNC=false ──

    print("\n── Phase 1: ENABLE_VNC=false（VNC 应关闭）──")
    config_off = ChromeDockerConfig(
        enabled=True,
        max_containers=1,
        idle_timeout=60,
        enable_vnc=False,
        vnc_password="test123",
        api_token="sere1n@chrome2026",
    )
    provider_off = DockerProvider(config_off)
    phase1_ok = False

    try:
        print("📦 创建容器（VNC 关闭）...")
        ws_url = await provider_off.get_cdp_endpoint(task_id="vnc-off-test")
        print(f"   CDP: {ws_url}")

        # 等一下让进程稳定
        await asyncio.sleep(2)

        status = await provider_off.get_pool_status()
        if status:
            phase1_ok = await _check_vnc_status(provider_off, status[0], expect_vnc=False)
        else:
            print("   ❌ 无容器信息")

        await provider_off.release_cdp_endpoint(task_id="vnc-off-test")

    except Exception as e:
        print(f"   ❌ Phase 1 失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("🗑️  销毁 Phase 1 容器...")
        await provider_off.shutdown()

    # ── Phase 2: ENABLE_VNC=true ──

    print("\n── Phase 2: ENABLE_VNC=true（VNC 应启动）──")
    config_on = ChromeDockerConfig(
        enabled=True,
        max_containers=1,
        idle_timeout=60,
        enable_vnc=True,
        vnc_password="test123",
        api_token="sere1n@chrome2026",
    )
    provider_on = DockerProvider(config_on)
    phase2_ok = False

    try:
        print("📦 创建容器（VNC 开启）...")
        ws_url = await provider_on.get_cdp_endpoint(task_id="vnc-on-test")
        print(f"   CDP: {ws_url}")

        # VNC 启动需要一点时间
        await asyncio.sleep(3)

        status = await provider_on.get_pool_status()
        if status:
            phase2_ok = await _check_vnc_status(provider_on, status[0], expect_vnc=True)

            # 额外：检查 noVNC HTTP 是否可达
            info = status[0]
            novnc_url = info["novnc_url"]
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        novnc_url.replace("?autoconnect=true", ""),
                        timeout=5,
                        follow_redirects=True,
                    )
                    if resp.status_code == 200:
                        print(f"   ✅ noVNC Web 页面可达: {novnc_url}")
                    else:
                        print(f"   ⚠️  noVNC HTTP 返回 {resp.status_code}")
            except Exception as e:
                print(f"   ⚠️  noVNC HTTP 不可达: {e}")
        else:
            print("   ❌ 无容器信息")

        await provider_on.release_cdp_endpoint(task_id="vnc-on-test")

    except Exception as e:
        print(f"   ❌ Phase 2 失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("🗑️  销毁 Phase 2 容器...")
        await provider_on.shutdown()

    # ── 汇总 ──

    print(f"\n── 结果汇总 ──")
    print(f"   Phase 1 (VNC=off): {'✅ 通过' if phase1_ok else '❌ 失败'}")
    print(f"   Phase 2 (VNC=on):  {'✅ 通过' if phase2_ok else '❌ 失败'}")

    passed = phase1_ok and phase2_ok
    if passed:
        print("✅ ENABLE_VNC 环境变量开关验证通过")
    else:
        print("❌ ENABLE_VNC 环境变量开关验证失败")
    return passed


# ── 主入口 ────────────────────────────────────────────────

def print_menu():
    print("\n" + "="*60)
    print("  Chrome Docker 容器化测试")
    print("="*60)
    print("  1. Docker 镜像检查")
    print("  2. 容器生命周期（创建/复用/销毁）")
    print("  3. Playwright 连接 Docker Chrome")
    print("  4. Docker Chrome 截图（小红书，需要 Cookie）")
    print("  5. 多容器并发")
    print("  6. DouyinCrawler Docker 实际搜索（需要 Cookie）")
    print("  7. XhsCrawler Docker 实际搜索（需要 Cookie）")
    print("  8. VNC 桌面检查（手动查看）")
    print("  9. ENABLE_VNC 环境变量开关验证")
    print("  0. 运行全部（不含 8）")
    print("  q. 退出")
    print("="*60)


async def main():
    test_map = {
        "1": ("Docker 镜像检查", test_docker_image_exists),
        "2": ("容器生命周期", test_container_lifecycle),
        "3": ("Playwright 连接", test_playwright_connect),
        "4": ("Docker 截图（小红书）", test_docker_screenshot),
        "5": ("多容器并发", test_concurrent_containers),
        "6": ("DouyinCrawler Docker 搜索", test_douyin_crawler_docker),
        "7": ("XhsCrawler Docker 搜索", test_xhs_crawler_docker),
        "8": ("VNC 桌面检查", test_vnc_inspection),
        "9": ("ENABLE_VNC 开关验证", test_vnc_toggle),
    }

    while True:
        print_menu()
        choice = input("\n请选择测试项 > ").strip()

        if choice == "q":
            print("👋 退出")
            break
        elif choice == "0":
            # 运行全部（跳过 8，因为需要手动交互）
            results = {}
            for key, (name, func) in test_map.items():
                if key == "8":
                    continue
                results[name] = await func()

            print_section("测试结果汇总")
            for name, passed in results.items():
                status = "✅ 通过" if passed else "❌ 失败"
                print(f"  {status}  {name}")
        elif choice in test_map:
            name, func = test_map[choice]
            await func()
        else:
            print("无效选择")


if __name__ == "__main__":
    asyncio.run(main())
