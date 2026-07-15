"""
XHS 小红书主页截图 + 视觉分析工具

提供截图和视觉分析功能，供 xhs_pipeline 调用
"""
from __future__ import annotations

import asyncio
import base64
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Callable

from motor.motor_asyncio import AsyncIOMotorDatabase
from openai import OpenAI

from core.logger import get_logger
from core.llm_params import disable_thinking_extra_body

logger = get_logger("xhs_vision")


async def _get_vision_runtime() -> tuple[str, str, str] | None:
    """Return vision model, base_url and api_key from encrypted DB config."""
    from api.services.runtime_config import get_runtime_app_config

    app_config = await get_runtime_app_config()
    runtime = getattr(app_config, "runtime", None)
    if not runtime:
        return None
    models = getattr(runtime, "models", None)
    vision_model = getattr(models, "vision", "qwen3.7-plus") if models else "qwen3.7-plus"
    base_url = getattr(runtime, "base_url", "") or ""
    api_key = getattr(runtime, "api_key", "") or ""
    return vision_model, base_url, api_key


def _run_async_config(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("当前处于 async 上下文，请调用对应的 async 视觉分析函数")


async def screenshot_user_profile(user_url: str, db: AsyncIOMotorDatabase) -> dict[str, Any]:
    """
    访问小红书用户主页并截屏（同时提取头像链接）
    
    注意：所有操作在单次浏览器会话中完成，不会重复打开浏览器
    
    Args:
        user_url: 用户主页 URL
        db: 数据库连接（用于获取 Cookie）
    
    Returns:
        {
            "screenshots": [...],  # Base64 编码的截图列表
            "avatar_url": "头像链接",  # 从页面提取的头像 URL
            "error": None
        }
    """
    result = {"screenshots": [], "avatar_url": None, "error": None}
    async for item in screenshot_user_profile_stream(user_url, db):
        if item.get("type") == "result":
            result = item.get("data", result)
    return result


async def screenshot_user_profile_stream(
    user_url: str, 
    db: AsyncIOMotorDatabase,
    max_screenshots: int = 10,  # 最大截图数量，防止无限滚动
) -> AsyncGenerator[dict[str, Any], None]:
    """
    访问小红书用户主页并截屏（流式版本，带进度回调）
    
    智能滚动：自动检测是否滑到底部，滑不动时自动停止
    
    Args:
        user_url: 用户主页 URL
        db: 数据库连接（用于获取 Cookie）
        max_screenshots: 最大截图数量（默认 10，防止无限滚动）
    
    Yields:
        {"type": "progress", "message": "进度信息"}
        {"type": "result", "data": {...}}  # 最终结果
    """
    import random
    from playwright.async_api import async_playwright
    from api.dao import xhs as xhs_dao
    
    if "xiaohongshu.com" not in user_url:
        yield {"type": "result", "data": {"screenshots": [], "avatar_url": None, "error": "URL 格式错误"}}
        return
    
    screenshots = []
    avatar_url = None
    
    # 从数据库获取 Cookie
    yield {"type": "progress", "message": "正在获取 Cookie..."}
    
    try:
        active_cookie = await xhs_dao.get_active_cookie(db)
        if not active_cookie:
            yield {"type": "result", "data": {"screenshots": [], "avatar_url": None, "error": "数据库中没有激活的 Cookie"}}
            return
        
        cookie_string = active_cookie.get("cookie_string")
        if not cookie_string:
            yield {"type": "result", "data": {"screenshots": [], "avatar_url": None, "error": "Cookie 为空"}}
            return
    except Exception as e:
        yield {"type": "result", "data": {"screenshots": [], "avatar_url": None, "error": f"获取 Cookie 失败: {e}"}}
        return
    
    # 解析 Cookie
    cookies = []
    for item in cookie_string.split(";"):
        item = item.strip()
        if "=" in item:
            name, value = item.split("=", 1)
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".xiaohongshu.com",
                "path": "/",
            })
    
    if not cookies:
        yield {"type": "result", "data": {"screenshots": [], "avatar_url": None, "error": "Cookie 解析失败"}}
        return
    
    stealth_js = Path(__file__).resolve().parents[2] / "MediaCrawler" / "libs" / "stealth.min.js"
    
    try:
        yield {"type": "progress", "message": "正在启动浏览器..."}
        
        _remote_browser = False
        _remote_task_id = None

        async with async_playwright() as p:
            # 尝试 Docker 模式：连接远程 Chrome
            try:
                from browser_manager import get_browser_provider
                provider = get_browser_provider()
                _remote_task_id = f"xhs-screenshot-{id(user_url)}"
                cdp_endpoint = await provider.get_cdp_endpoint(task_id=_remote_task_id, purpose="xhs_screenshot")
                if cdp_endpoint:
                    browser = await p.chromium.connect_over_cdp(cdp_endpoint)
                    _remote_browser = True
                    yield {"type": "progress", "message": "已连接远程 Chrome 容器"}
                else:
                    browser = await p.chromium.launch(
                        headless=False,
                        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                    )
            except Exception:
                browser = await p.chromium.launch(
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                )
            
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            
            if stealth_js.exists():
                await context.add_init_script(path=str(stealth_js))
            
            await context.add_cookies(cookies)
            page = await context.new_page()
            
            yield {"type": "progress", "message": "正在访问用户主页..."}
            
            await page.goto(user_url, wait_until="domcontentloaded", timeout=60000)
            
            yield {"type": "progress", "message": "等待页面加载..."}
            await asyncio.sleep(5)
            
            # 提取头像链接
            yield {"type": "progress", "message": "正在提取头像链接..."}
            
            avatar_selectors = [
                'img[class*="avatar"]',
                'img[class*="Avatar"]',
                'img[src*="avatar"]',
                'img[alt*="头像"]',
            ]
            
            for selector in avatar_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        src = await element.get_attribute("src")
                        if src and "avatar" in src.lower():
                            avatar_url = src
                            break
                except Exception:
                    continue
            
            # 智能滚动截图：滑到底部自动停止
            screenshot_count = 0
            consecutive_no_scroll = 0  # 连续无法滚动的次数
            max_no_scroll_attempts = 2  # 连续 2 次滑不动就停止
            
            while screenshot_count < max_screenshots:
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
                
                screenshot_bytes = await page.screenshot()
                base64_image = base64.b64encode(screenshot_bytes).decode("utf-8")
                screenshots.append({"base64": base64_image, "format": "png"})
                
                # 检查是否已经到底部
                is_at_bottom = (
                    scroll_info_before["scrollTop"] + scroll_info_before["clientHeight"] 
                    >= scroll_info_before["scrollHeight"] - 10  # 允许 10px 误差
                )
                
                if is_at_bottom:
                    yield {"type": "progress", "message": "已滚动到页面底部，停止截图"}
                    break
                
                # 滚动页面
                yield {"type": "progress", "message": "滚动页面..."}
                await asyncio.sleep(random.uniform(0.3, 0.7))
                await page.mouse.move(random.randint(400, 1200), random.randint(300, 700))
                await page.mouse.wheel(0, 800)
                await asyncio.sleep(random.uniform(1.5, 2.5))
                
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
                    yield {"type": "progress", "message": f"页面未滚动（{consecutive_no_scroll}/{max_no_scroll_attempts}）"}
                    
                    if consecutive_no_scroll >= max_no_scroll_attempts:
                        yield {"type": "progress", "message": "连续多次无法滚动，停止截图"}
                        break
                else:
                    consecutive_no_scroll = 0  # 重置计数
            
            yield {"type": "progress", "message": f"截图完成，共 {len(screenshots)} 张"}
            yield {"type": "progress", "message": "正在关闭浏览器..."}
            await browser.close()
            
            # 释放 Docker 容器
            if _remote_browser and _remote_task_id:
                try:
                    await provider.release_cdp_endpoint(task_id=_remote_task_id)
                except Exception:
                    pass
        
        yield {"type": "result", "data": {"screenshots": screenshots, "avatar_url": avatar_url, "error": None}}
        
    except Exception as e:
        # Target crashed / 渲染进程崩溃 → 上报错误并触发容器热切换
        error_msg = str(e)
        if _remote_browser and _remote_task_id:
            try:
                from browser_manager import get_browser_provider
                prov = get_browser_provider()
                await prov.report_error(task_id=_remote_task_id, error_msg=error_msg)
                if await prov.should_hot_swap(task_id=_remote_task_id):
                    logger.warning(f"[XHS-截图] 容器崩溃，触发热切换 | task={_remote_task_id}")
                    await prov.hot_swap_container(task_id=_remote_task_id, purpose="xhs_screenshot")
                else:
                    await prov.release_cdp_endpoint(task_id=_remote_task_id)
            except Exception:
                try:
                    from browser_manager import get_browser_provider
                    await get_browser_provider().release_cdp_endpoint(task_id=_remote_task_id)
                except Exception:
                    pass
        yield {"type": "result", "data": {"screenshots": [], "avatar_url": None, "error": f"截屏失败: {error_msg}"}}


async def save_screenshots_to_files(
    screenshots: list[dict[str, str]],
    user_id: str,
    output_dir: str | None = None,
    *,
    project_id: str = "",
) -> list[str]:
    """将截图写入统一存储，返回稳定的鉴权读取 URL。"""
    _ = output_dir  # 旧参数保留兼容，不再写本地目录。
    from api.storage import get_object_storage

    storage = await get_object_storage()
    saved_urls: list[str] = []
    kind = "xhs_note_screenshot" if user_id.startswith("note_") else "xhs_profile_screenshot"
    
    for idx, screenshot in enumerate(screenshots):
        base64_data = screenshot.get("base64", "")
        img_format = screenshot.get("format", "png")
        
        if not base64_data:
            continue
        
        object_id = "xss_" + uuid.uuid4().hex
        stored = await storage.store_bytes(
            base64.b64decode(base64_data),
            kind=kind,
            filename=f"{object_id}.{img_format}",
            object_id=object_id,
            content_type=f"image/{'jpeg' if img_format in {'jpg', 'jpeg'} else img_format}",
            project_id=project_id,
            subject_id=user_id,
            source="xhs_screenshot",
            source_id=object_id,
            meta={"user_id": user_id, "index": idx + 1},
        )
        saved_urls.append(f"/api/v1/storage/objects/{stored['object_id']}/content")

    return saved_urls


def _load_prompt(prompt_name: str) -> str:
    """从运行时 prompt 库加载小红书视觉 prompt。"""
    from Sere1nGraph.graph.prompts.loader import load_prompt

    return load_prompt(f"xhs_profile_vl/{prompt_name}")


async def analyze_screenshots_with_vision_async(screenshots: list[dict[str, str]]) -> str:
    """使用视觉模型分析截图"""
    runtime = await _get_vision_runtime()

    if not runtime:
        return "配置未找到"

    vision_model, base_url, api_key = runtime
    if not api_key:
        return "视觉模型 API Key 未配置"
    
    prompt = _load_prompt("vision_analysis")
    
    client = OpenAI(api_key=api_key, base_url=base_url)
    
    content = []
    for screenshot in screenshots:
        base64_data = screenshot.get("base64", "")
        img_format = screenshot.get("format", "png")
        if base64_data:
            mime_type = f"image/{img_format}"
            data_url = f"data:{mime_type};base64,{base64_data}"
            content.append({"type": "image_url", "image_url": {"url": data_url}})
    
    content.append({"type": "text", "text": prompt})
    
    completion = client.chat.completions.create(
        model=vision_model,
        messages=[{"role": "user", "content": content}],
        extra_body=disable_thinking_extra_body({"vl_high_resolution_images": True}),
    )
    
    return completion.choices[0].message.content


def analyze_screenshots_with_vision(screenshots: list[dict[str, str]]) -> str:
    """同步兼容入口。业务 async 路径应调用 analyze_screenshots_with_vision_async。"""
    return _run_async_config(analyze_screenshots_with_vision_async(screenshots))


async def get_user_profile_vision_analysis(
    user_url: str,
    db: AsyncIOMotorDatabase,
    save_files: bool = False,
    project_id: str = "",
) -> dict[str, Any]:
    """
    获取用户主页的视觉分析结果（供 xhs_pipeline 调用）
    
    Args:
        user_url: 用户主页 URL
        db: 数据库连接（用于获取 Cookie）
        save_files: 是否保存截图文件
        
    Returns:
        {
            "success": bool,
            "user_id": str,
            "avatar_url": str | None,  # 用户头像链接
            "vision_analysis": str,
            "screenshot_paths": list[str],
            "error": str | None,
        }
    """
    result = {
        "success": False,
        "user_id": "",
        "avatar_url": None,
        "vision_analysis": "",
        "screenshot_paths": [],
        "error": None,
    }
    
    # 提取 user_id
    if "/user/profile/" in user_url:
        result["user_id"] = user_url.split("/user/profile/")[-1].split("?")[0].split("/")[0]
    
    try:
        # 截屏（同时提取头像链接）
        logger.info(f"[视觉分析] 开始截图 | user={result['user_id']} | url={user_url}")
        screenshot_result = await screenshot_user_profile(user_url, db)
        
        if screenshot_result.get("error"):
            result["error"] = screenshot_result["error"]
            logger.warning(f"[视觉分析] 截图失败 | user={result['user_id']} | error={result['error']}")
            return result
        
        screenshots = screenshot_result.get("screenshots", [])
        avatar_url = screenshot_result.get("avatar_url")
        
        if not screenshots:
            result["error"] = "未获取到截图"
            logger.warning(f"[视觉分析] 截图为空 | user={result['user_id']}")
            return result
        
        logger.info(
            f"[视觉分析] 截图完成 | user={result['user_id']} | "
            f"截图={len(screenshots)}张 | avatar={'有' if avatar_url else '无'}"
        )
        
        # 保存头像链接
        result["avatar_url"] = avatar_url
        
        # 保存文件（可选）
        if save_files and result["user_id"]:
            result["screenshot_paths"] = await save_screenshots_to_files(
                screenshots,
                result["user_id"],
                project_id=project_id,
            )
        
        # 视觉分析
        logger.info(f"[视觉分析] 开始 VL 分析 | user={result['user_id']} | 截图={len(screenshots)}张")
        vision_analysis = await analyze_screenshots_with_vision_async(screenshots)
        result["vision_analysis"] = vision_analysis
        
        result["success"] = True
        logger.info(
            f"[视觉分析] 完成 | user={result['user_id']} | "
            f"分析长度={len(vision_analysis)}字"
        )
        
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"[视觉分析] 异常 | user={result['user_id']} | error={e}")
    
    return result



async def analyze_screenshots_with_vision_stream(screenshots: list[dict[str, str]]):
    """使用视觉模型流式分析截图（用于 SSE）"""
    runtime = await _get_vision_runtime()

    if not runtime:
        yield "配置未找到"
        return

    vision_model, base_url, api_key = runtime
    if not api_key:
        yield "视觉模型 API Key 未配置"
        return
    
    prompt = _load_prompt("vision_analysis")
    
    client = OpenAI(api_key=api_key, base_url=base_url)
    
    content = []
    for screenshot in screenshots:
        base64_data = screenshot.get("base64", "")
        img_format = screenshot.get("format", "png")
        if base64_data:
            mime_type = f"image/{img_format}"
            data_url = f"data:{mime_type};base64,{base64_data}"
            content.append({"type": "image_url", "image_url": {"url": data_url}})
    
    content.append({"type": "text", "text": prompt})
    
    # 流式调用
    stream = client.chat.completions.create(
        model=vision_model,
        messages=[{"role": "user", "content": content}],
        extra_body=disable_thinking_extra_body({"vl_high_resolution_images": True}),
        stream=True,
        stream_options={"include_usage": True},
    )
    
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


# ═══════════════════════════════════════════
# 笔记详情截屏 + 视觉分析（API 406 兜底方案）
# ═══════════════════════════════════════════

async def screenshot_note_detail(
    note_id: str,
    xsec_token: str = "",
    db: AsyncIOMotorDatabase | None = None,
    cookie_string: str = "",
    cdp_endpoint: str = "",
) -> dict[str, Any]:
    """
    截屏小红书笔记详情页

    URL 格式: https://www.xiaohongshu.com/explore/{note_id}?xsec_token={token}

    Args:
        note_id: 笔记 ID
        xsec_token: xsec_token（搜索阶段已获取）
        db: 数据库连接（可选，用于获取 Cookie）
        cookie_string: 直接传入 Cookie 字符串（优先级高于 db）
        cdp_endpoint: 外部传入的 CDP 地址（复用已有容器，不传则自动申请）

    Returns:
        {"screenshots": [...], "error": None}
    """
    result = {"screenshots": [], "error": None}
    async for item in screenshot_note_detail_stream(
        note_id, xsec_token, db,
        cookie_string=cookie_string, cdp_endpoint=cdp_endpoint,
    ):
        if item.get("type") == "result":
            result = item.get("data", result)
    return result


async def screenshot_note_detail_stream(
    note_id: str,
    xsec_token: str = "",
    db: AsyncIOMotorDatabase | None = None,
    max_screenshots: int = 5,
    cookie_string: str = "",
    cdp_endpoint: str = "",
) -> AsyncGenerator[dict[str, Any], None]:
    """
    截屏小红书笔记详情页（流式版本）

    容器生命周期由调用方管理：
    - 传入 cdp_endpoint: 复用已有容器，只做 new_context → 截屏 → close context
    - 不传 cdp_endpoint: 自己申请容器，截完释放（单次模式）
    """
    import random
    from urllib.parse import quote
    from playwright.async_api import async_playwright

    note_url = f"https://www.xiaohongshu.com/explore/{note_id}"
    if xsec_token:
        note_url += f"?xsec_token={quote(xsec_token, safe='')}"

    yield {"type": "progress", "message": f"准备截屏笔记: {note_url}"}

    # Cookie
    cookies = []
    _cookie_str = cookie_string
    if not _cookie_str and db:
        try:
            from api.dao import xhs as xhs_dao
            active_cookie = await xhs_dao.get_active_cookie(db)
            if active_cookie:
                _cookie_str = active_cookie.get("cookie_string", "")
        except Exception:
            pass

    if _cookie_str:
        for item in _cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                name, value = item.split("=", 1)
                cookies.append({
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": ".xiaohongshu.com",
                    "path": "/",
                })

    stealth_js = Path(__file__).resolve().parents[1] / "MediaCrawler" / "libs" / "stealth.min.js"
    if not stealth_js.exists():
        stealth_js = Path(__file__).resolve().parents[2] / "MediaCrawler" / "libs" / "stealth.min.js"

    screenshots = []
    # 是否由本函数自己管理容器（单次模式）
    _own_container = not cdp_endpoint
    _task_id = f"xhs-note-screenshot-{note_id}"
    provider = None

    try:
        from browser_manager import get_browser_provider
        provider = get_browser_provider()

        # 如果没传 cdp_endpoint，自己申请一个
        if not cdp_endpoint:
            yield {"type": "progress", "message": "正在申请 Chrome 容器..."}
            cdp_endpoint = await provider.get_cdp_endpoint(
                task_id=_task_id, purpose="xhs_screenshot"
            )
            if not cdp_endpoint:
                yield {"type": "result", "data": {"screenshots": [], "error": "无法获取 Chrome 容器"}}
                return

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(cdp_endpoint)
            yield {"type": "progress", "message": "已连接 Chrome 容器"}

            # 每次截屏用独立 context（隔离 cookie，关掉就清理干净）
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )

            if stealth_js.exists():
                await context.add_init_script(path=str(stealth_js))

            if cookies:
                await context.add_cookies(cookies)

            page = await context.new_page()

            yield {"type": "progress", "message": "正在访问笔记详情页..."}
            await page.goto(note_url, wait_until="domcontentloaded", timeout=60000)

            yield {"type": "progress", "message": "等待页面加载..."}
            await asyncio.sleep(5)

            # 智能滚动截图
            screenshot_count = 0
            consecutive_no_scroll = 0

            while screenshot_count < max_screenshots:
                scroll_info_before = await page.evaluate("""
                    () => ({
                        scrollTop: window.scrollY || document.documentElement.scrollTop,
                        scrollHeight: document.documentElement.scrollHeight,
                        clientHeight: document.documentElement.clientHeight
                    })
                """)

                screenshot_count += 1
                yield {"type": "progress", "message": f"截取第 {screenshot_count} 张截图..."}

                screenshot_bytes = await page.screenshot()
                base64_image = base64.b64encode(screenshot_bytes).decode("utf-8")
                screenshots.append({"base64": base64_image, "format": "png"})

                is_at_bottom = (
                    scroll_info_before["scrollTop"] + scroll_info_before["clientHeight"]
                    >= scroll_info_before["scrollHeight"] - 10
                )
                if is_at_bottom:
                    yield {"type": "progress", "message": "已到页面底部"}
                    break

                await asyncio.sleep(random.uniform(0.3, 0.7))
                await page.mouse.wheel(0, 800)
                await asyncio.sleep(random.uniform(1.5, 2.5))

                scroll_info_after = await page.evaluate("""
                    () => ({ scrollTop: window.scrollY || document.documentElement.scrollTop })
                """)

                if abs(scroll_info_after["scrollTop"] - scroll_info_before["scrollTop"]) < 10:
                    consecutive_no_scroll += 1
                    if consecutive_no_scroll >= 2:
                        yield {"type": "progress", "message": "无法继续滚动，停止"}
                        break
                else:
                    consecutive_no_scroll = 0

            yield {"type": "progress", "message": f"截图完成，共 {len(screenshots)} 张"}

            # 关掉 context（关掉 tab + 清理 cookie），容器保持存活
            await context.close()
            # 断开 playwright 连接（不影响容器里的 Chrome 进程）
            await browser.close()

        # 单次模式：自己申请的容器，用完释放
        if _own_container and provider:
            try:
                await provider.release_cdp_endpoint(task_id=_task_id)
                yield {"type": "progress", "message": "Chrome 容器已释放"}
            except Exception:
                pass

        yield {"type": "result", "data": {"screenshots": screenshots, "error": None}}

    except Exception as e:
        if _own_container and provider:
            try:
                await provider.release_cdp_endpoint(task_id=_task_id)
            except Exception:
                pass
        yield {"type": "result", "data": {"screenshots": [], "error": f"笔记截屏失败: {e}"}}


async def analyze_note_screenshots_with_vision_async(screenshots: list[dict[str, str]]) -> str:
    """使用视觉模型分析笔记详情截图"""
    runtime = await _get_vision_runtime()
    if not runtime:
        return "配置未找到"

    vision_model, base_url, api_key = runtime
    if not api_key:
        return "视觉模型 API Key 未配置"

    from Sere1nGraph.graph.prompts.loader import load_prompt

    prompt = load_prompt("xhs_note_detail_vl/note_detail_analysis")

    client = OpenAI(api_key=api_key, base_url=base_url)

    content = []
    for screenshot in screenshots:
        base64_data = screenshot.get("base64", "")
        img_format = screenshot.get("format", "png")
        if base64_data:
            mime_type = f"image/{img_format}"
            data_url = f"data:{mime_type};base64,{base64_data}"
            content.append({"type": "image_url", "image_url": {"url": data_url}})

    content.append({"type": "text", "text": prompt})

    completion = client.chat.completions.create(
        model=vision_model,
        messages=[{"role": "user", "content": content}],
        extra_body=disable_thinking_extra_body({"vl_high_resolution_images": True}),
    )

    return completion.choices[0].message.content


def analyze_note_screenshots_with_vision(screenshots: list[dict[str, str]]) -> str:
    """同步兼容入口。业务 async 路径应调用 analyze_note_screenshots_with_vision_async。"""
    return _run_async_config(analyze_note_screenshots_with_vision_async(screenshots))


async def get_note_detail_vision_analysis(
    note_id: str,
    xsec_token: str = "",
    db: AsyncIOMotorDatabase | None = None,
    save_files: bool = False,
    project_id: str = "",
) -> dict[str, Any]:
    """
    获取笔记详情的视觉分析结果（API 406 兜底方案）

    Args:
        note_id: 笔记 ID
        xsec_token: xsec_token
        db: 数据库连接
        save_files: 是否保存截图文件

    Returns:
        {"success": bool, "vision_analysis": str, "screenshot_paths": [], "error": str|None}
    """
    result = {
        "success": False,
        "note_id": note_id,
        "vision_analysis": "",
        "screenshot_paths": [],
        "error": None,
    }

    try:
        screenshot_result = await screenshot_note_detail(note_id, xsec_token, db)

        if screenshot_result.get("error"):
            result["error"] = screenshot_result["error"]
            return result

        screenshots = screenshot_result.get("screenshots", [])
        if not screenshots:
            result["error"] = "未获取到截图"
            return result

        if save_files:
            result["screenshot_paths"] = await save_screenshots_to_files(
                screenshots,
                f"note_{note_id}",
                project_id=project_id,
            )

        vision_analysis = await analyze_note_screenshots_with_vision_async(screenshots)
        result["vision_analysis"] = vision_analysis
        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result
