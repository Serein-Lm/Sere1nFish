"""
通用截图工具

支持小红书、抖音等平台的用户主页截图
可配置等待时间、滚动参数等
"""

import asyncio
import base64
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator


@dataclass
class ScreenshotConfig:
    """截图配置"""
    # 页面加载等待时间（秒）
    page_load_wait: float = 5.0
    # 滚动前等待时间（秒）
    scroll_before_wait: tuple[float, float] = (0.3, 0.7)
    # 滚动后等待时间（秒）
    scroll_after_wait: tuple[float, float] = (1.5, 2.5)
    # 最大截图数量
    max_screenshots: int = 5
    # 连续无法滚动的最大次数
    max_no_scroll_attempts: int = 2
    # 滚动距离
    scroll_distance: int = 800
    # 是否无头模式
    headless: bool = False
    # 视口大小
    viewport_width: int = 1920
    viewport_height: int = 1080
    # Cookie 域名
    cookie_domain: str = ".douyin.com"
    # stealth.js 路径（可选）
    stealth_js_path: Path | None = None
    # 布局修复：先向右滚动再等待恢复（用于抖音页面布局异常）
    fix_layout_scroll: bool = False
    # 布局修复后等待时间（秒）
    fix_layout_wait: float = 20.0
    # 布局修复滚动距离
    fix_layout_scroll_distance: int = 1200


# 预设配置
DOUYIN_CONFIG = ScreenshotConfig(
    page_load_wait=30.0,  # 抖音加载较慢，等待 30 秒
    scroll_before_wait=(0.5, 1.0),
    scroll_after_wait=(2.0, 3.0),
    max_screenshots=5,
    cookie_domain=".douyin.com",
    viewport_width=1920,
    viewport_height=1080,
)

XHS_CONFIG = ScreenshotConfig(
    page_load_wait=5.0,
    scroll_before_wait=(0.3, 0.7),
    scroll_after_wait=(1.5, 2.5),
    max_screenshots=10,
    cookie_domain=".xiaohongshu.com",
)


async def screenshot_page_stream(
    url: str,
    cookie_str: str,
    config: ScreenshotConfig | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    访问页面并截屏（流式版本）
    
    Args:
        url: 页面 URL
        cookie_str: Cookie 字符串
        config: 截图配置，不提供则使用默认配置
    
    Yields:
        {"type": "progress", "message": "进度信息"}
        {"type": "result", "data": {"screenshots": [...], "error": None}}
    """
    from playwright.async_api import async_playwright
    
    if config is None:
        config = ScreenshotConfig()
    
    screenshots = []
    
    # 解析 Cookie
    cookies = []
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            name, value = item.split("=", 1)
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": config.cookie_domain,
                "path": "/",
            })
    
    if not cookies:
        yield {"type": "result", "data": {"screenshots": [], "error": "Cookie 解析失败"}}
        return
    
    try:
        yield {"type": "progress", "message": "正在启动浏览器..."}
        
        async with async_playwright() as p:
            # 尝试 Docker 模式：连接远程 Chrome
            _remote_browser = False
            _remote_task_id = None
            try:
                from browser_manager import get_browser_provider
                provider = get_browser_provider()
                _remote_task_id = f"screenshot-{id(url)}"
                cdp_endpoint = await provider.get_cdp_endpoint(task_id=_remote_task_id)
                if cdp_endpoint:
                    browser = await p.chromium.connect_over_cdp(cdp_endpoint)
                    _remote_browser = True
                    yield {"type": "progress", "message": "已连接远程 Chrome 容器"}
                else:
                    browser = await p.chromium.launch(
                        headless=config.headless,
                        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                    )
            except Exception:
                browser = await p.chromium.launch(
                    headless=config.headless,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                )
            
            context = await browser.new_context(
                viewport={"width": config.viewport_width, "height": config.viewport_height},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            
            # 加载 stealth.js
            if config.stealth_js_path and config.stealth_js_path.exists():
                await context.add_init_script(path=str(config.stealth_js_path))
            
            await context.add_cookies(cookies)
            page = await context.new_page()
            
            yield {"type": "progress", "message": f"正在访问: {url}"}
            
            # 使用 domcontentloaded，抖音页面 networkidle 容易超时
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            
            yield {"type": "progress", "message": f"等待页面稳定 ({config.page_load_wait}s)..."}
            await asyncio.sleep(config.page_load_wait)
            
            # 抖音页面布局修复：尝试只截取内容区域
            content_element = None
            if config.fix_layout_scroll:
                yield {"type": "progress", "message": "查找内容区域..."}
                # 尝试找到主内容区域
                content_element = await page.query_selector('.XA9ZQ2av')
                if content_element:
                    yield {"type": "progress", "message": "找到内容区域 .XA9ZQ2av，将只截取该区域"}
                else:
                    yield {"type": "progress", "message": "未找到内容区域，截取整个页面"}
            
            # 重置滚动位置
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.5)
            
            # 垂直滚动截图
            screenshot_count = 0
            consecutive_no_scroll = 0
            
            while screenshot_count < config.max_screenshots:
                # 获取当前滚动位置
                scroll_info_before = await page.evaluate("""
                    () => {
                        return {
                            scrollTop: window.scrollY || document.documentElement.scrollTop,
                            scrollHeight: document.documentElement.scrollHeight,
                            clientHeight: document.documentElement.clientHeight
                        }
                    }
                """)
                
                # 截图
                screenshot_count += 1
                yield {"type": "progress", "message": f"正在截取第 {screenshot_count} 张截图..."}
                
                # 如果有内容元素，只截取该元素；否则截取整个页面
                if content_element:
                    screenshot_bytes = await content_element.screenshot()
                else:
                    screenshot_bytes = await page.screenshot()
                base64_image = base64.b64encode(screenshot_bytes).decode("utf-8")
                screenshots.append({"base64": base64_image, "format": "png"})
                
                # 检查是否已经到底部
                is_at_bottom = (
                    scroll_info_before["scrollTop"] + scroll_info_before["clientHeight"] 
                    >= scroll_info_before["scrollHeight"] - 10
                )
                
                if is_at_bottom:
                    yield {"type": "progress", "message": "已滚动到页面底部，停止截图"}
                    break
                
                # 如果还需要更多截图，向下滚动
                if screenshot_count < config.max_screenshots:
                    yield {"type": "progress", "message": "向下滚动..."}
                    await asyncio.sleep(config.scroll_before_wait[0])
                    await page.evaluate(f"window.scrollBy(0, {config.scroll_distance})")
                    await asyncio.sleep(config.scroll_after_wait[0])
                    
                    # 获取滚动后的位置
                    scroll_info_after = await page.evaluate("""
                        () => {
                            return {
                                scrollTop: window.scrollY || document.documentElement.scrollTop
                            }
                        }
                    """)
                    
                    # 检查是否滚动成功
                    if abs(scroll_info_after["scrollTop"] - scroll_info_before["scrollTop"]) < 10:
                        consecutive_no_scroll += 1
                        yield {"type": "progress", "message": f"页面未滚动（{consecutive_no_scroll}/{config.max_no_scroll_attempts}）"}
                        
                        if consecutive_no_scroll >= config.max_no_scroll_attempts:
                            yield {"type": "progress", "message": "连续多次无法滚动，停止截图"}
                            break
                    else:
                        consecutive_no_scroll = 0
            
            yield {"type": "progress", "message": f"截图完成，共 {len(screenshots)} 张"}
            yield {"type": "progress", "message": "正在关闭浏览器..."}
            await browser.close()
            
            # 释放 Docker 容器
            if _remote_browser and _remote_task_id:
                try:
                    await provider.release_cdp_endpoint(task_id=_remote_task_id)
                except Exception:
                    pass
        
        yield {"type": "result", "data": {"screenshots": screenshots, "error": None}}
        
    except Exception as e:
        # Target crashed / 渲染进程崩溃 → 上报错误并触发容器热切换
        error_msg = str(e)
        if _remote_browser and _remote_task_id:
            try:
                from browser_manager import get_browser_provider
                prov = get_browser_provider()
                await prov.report_error(task_id=_remote_task_id, error_msg=error_msg)
                if await prov.should_hot_swap(task_id=_remote_task_id):
                    await prov.hot_swap_container(task_id=_remote_task_id)
                else:
                    await prov.release_cdp_endpoint(task_id=_remote_task_id)
            except Exception:
                try:
                    from browser_manager import get_browser_provider
                    await get_browser_provider().release_cdp_endpoint(task_id=_remote_task_id)
                except Exception:
                    pass
        yield {"type": "result", "data": {"screenshots": [], "error": f"截屏失败: {error_msg}"}}


async def screenshot_page(
    url: str,
    cookie_str: str,
    config: ScreenshotConfig | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    访问页面并截屏（非流式版本）
    
    Args:
        url: 页面 URL
        cookie_str: Cookie 字符串
        config: 截图配置
        verbose: 是否打印进度
    
    Returns:
        {"screenshots": [...], "error": None}
    """
    result = {"screenshots": [], "error": None}
    async for item in screenshot_page_stream(url, cookie_str, config):
        if item.get("type") == "progress":
            if verbose:
                print(f"  {item.get('message')}")
        elif item.get("type") == "result":
            result = item.get("data", result)
    return result


def save_screenshots(
    screenshots: list[dict],
    user_id: str,
    output_dir: Path | str,
) -> list[str]:
    """
    保存截图到文件
    
    Args:
        screenshots: 截图列表
        user_id: 用户ID（用于文件名）
        output_dir: 输出目录
    
    Returns:
        保存的文件路径列表
    """
    if isinstance(output_dir, str):
        output_dir = Path(output_dir)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    saved_paths = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    for idx, screenshot in enumerate(screenshots):
        base64_data = screenshot.get("base64", "")
        img_format = screenshot.get("format", "png")
        
        if not base64_data:
            continue
        
        filename = f"{user_id}_{timestamp}_{idx + 1}.{img_format}"
        filepath = output_dir / filename
        
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(base64_data))
        
        saved_paths.append(str(filepath))
    
    return saved_paths


# ==================== 抖音专用函数 ====================

async def screenshot_douyin_profile(
    user_url: str,
    cookie_str: str,
    max_screenshots: int = 3,
    page_load_wait: float = 30.0,
    fix_layout: bool = False,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    抖音用户主页截图
    
    Args:
        user_url: 用户主页 URL (https://www.douyin.com/user/{sec_uid})
        cookie_str: Cookie 字符串
        max_screenshots: 最大截图数量
        page_load_wait: 页面加载等待时间（秒），默认 30 秒
        fix_layout: 是否启用布局修复（先向右滚动，等待20秒恢复后再截图）
        verbose: 是否打印进度
    
    Returns:
        {"screenshots": [...], "error": None}
    """
    if "douyin.com" not in user_url:
        return {"screenshots": [], "error": "URL 格式错误，需要抖音链接"}
    
    # 获取 stealth.js 路径
    repo_root = Path(__file__).resolve().parents[1]
    stealth_js = repo_root / "MediaCrawler" / "libs" / "stealth.min.js"
    
    config = ScreenshotConfig(
        page_load_wait=page_load_wait,
        scroll_before_wait=(0.5, 1.0),
        scroll_after_wait=(5.0, 6.0),  # 滚动后等待更长时间
        max_screenshots=max_screenshots,
        scroll_distance=1200,  # 滚动距离加大
        cookie_domain=".douyin.com",
        viewport_width=2560,
        viewport_height=1440,
        stealth_js_path=stealth_js if stealth_js.exists() else None,
        fix_layout_scroll=fix_layout,
        fix_layout_wait=20.0,
        fix_layout_scroll_distance=1200,
    )
    
    return await screenshot_page(user_url, cookie_str, config, verbose)


async def screenshot_douyin_profile_stream(
    user_url: str,
    cookie_str: str,
    max_screenshots: int = 3,
    page_load_wait: float = 30.0,
    fix_layout: bool = False,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    抖音用户主页截图（流式版本）
    
    Args:
        user_url: 用户主页 URL
        cookie_str: Cookie 字符串
        max_screenshots: 最大截图数量
        page_load_wait: 页面加载等待时间（秒）
        fix_layout: 是否启用布局修复（先向右滚动，等待20秒恢复后再截图）
    """
    if "douyin.com" not in user_url:
        yield {"type": "result", "data": {"screenshots": [], "error": "URL 格式错误，需要抖音链接"}}
        return
    
    repo_root = Path(__file__).resolve().parents[1]
    stealth_js = repo_root / "MediaCrawler" / "libs" / "stealth.min.js"
    
    config = ScreenshotConfig(
        page_load_wait=page_load_wait,
        scroll_before_wait=(0.5, 1.0),
        scroll_after_wait=(5.0, 6.0),  # 滚动后等待更长时间
        max_screenshots=max_screenshots,
        scroll_distance=1200,  # 滚动距离加大
        cookie_domain=".douyin.com",
        viewport_width=2560,
        viewport_height=1440,
        stealth_js_path=stealth_js if stealth_js.exists() else None,
        fix_layout_scroll=fix_layout,
        fix_layout_wait=20.0,
        fix_layout_scroll_distance=1200,
    )
    
    async for item in screenshot_page_stream(user_url, cookie_str, config):
        yield item
