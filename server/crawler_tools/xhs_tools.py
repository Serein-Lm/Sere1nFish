from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from langchain.tools import tool


@dataclass
class XhsRuntime:
    """In-memory runtime container for XHS crawling session."""

    browser_context: Any
    context_page: Any
    client: Any


def _ensure_mediacrawler_importable() -> None:
    """Ensure `MediaCrawler/` (repo-local) is importable as a top-level module."""

    # This file is at: <repo_root>/crawler_tools/xhs_tools.py
    # MediaCrawler is at: <repo_root>/MediaCrawler
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    mc_path = str(repo_root / "MediaCrawler")
    if mc_path not in sys.path:
        sys.path.insert(0, mc_path)


async def _create_xhs_runtime(
    *,
    enable_cdp_mode: bool = True,
    headless: bool = False,
    cdp_headless: bool = False,
    login_type: str = "qrcode",
    cookies: str = "",
    enable_ip_proxy: bool = False,
    ip_proxy_pool_count: int = 2,
    user_agent: Optional[str] = None,
) -> XhsRuntime:
    """Create browser + page + XiaoHongShuClient.

    This is an adapter-layer entrypoint that reuses MediaCrawler's login + signing approach,
    but does NOT write any data to MediaCrawler.store.

    Returns an XhsRuntime that must be closed via `xhs_close_runtime`.
    """

    _ensure_mediacrawler_importable()

    # Import MediaCrawler modules lazily to avoid import-time side effects.
    import config as mc_config
    from media_platform.xhs.client import XiaoHongShuClient
    from media_platform.xhs.login import XiaoHongShuLogin
    from proxy.proxy_ip_pool import create_ip_pool
    from tools import utils
    from tools.cdp_browser import CDPBrowserManager

    from playwright.async_api import async_playwright

    if user_agent is None:
        user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )

    playwright_proxy_format, httpx_proxy_format = None, None
    ip_proxy_pool = None
    if enable_ip_proxy:
        ip_proxy_pool = await create_ip_pool(ip_proxy_pool_count, enable_validate_ip=True)
        ip_proxy_info = await ip_proxy_pool.get_proxy()
        playwright_proxy_format, httpx_proxy_format = utils.format_proxy_info(ip_proxy_info)

    playwright_cm = async_playwright()
    playwright = await playwright_cm.__aenter__()

    cdp_manager: Optional[CDPBrowserManager] = None
    try:
        if enable_cdp_mode:
            cdp_manager = CDPBrowserManager()
            browser_context = await cdp_manager.launch_and_connect(
                playwright=playwright,
                playwright_proxy=playwright_proxy_format,
                user_agent=user_agent,
                headless=cdp_headless,
            )
        else:
            chromium = playwright.chromium
            if mc_config.SAVE_LOGIN_STATE:
                # follow upstream behavior
                import os
                user_data_dir = os.path.join(
                    os.getcwd(),
                    "browser_data",
                    mc_config.USER_DATA_DIR % mc_config.PLATFORM,
                )
                browser_context = await chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    accept_downloads=True,
                    headless=headless,
                    proxy=playwright_proxy_format,
                    viewport={"width": 1920, "height": 1080},
                    user_agent=user_agent,
                )
            else:
                browser = await chromium.launch(headless=headless, proxy=playwright_proxy_format)
                browser_context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent=user_agent,
                )
            await browser_context.add_init_script(path="MediaCrawler/libs/stealth.min.js")

        context_page = await browser_context.new_page()
        await context_page.goto("https://www.xiaohongshu.com")

        cookie_str, cookie_dict = utils.convert_cookies(await browser_context.cookies())
        client = XiaoHongShuClient(
            proxy=httpx_proxy_format,
            headers={
                "accept": "application/json, text/plain, */*",
                "accept-language": "zh-CN,zh;q=0.9",
                "cache-control": "no-cache",
                "content-type": "application/json;charset=UTF-8",
                "origin": "https://www.xiaohongshu.com",
                "pragma": "no-cache",
                "priority": "u=1, i",
                "referer": "https://www.xiaohongshu.com/",
                "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-site",
                "user-agent": user_agent,
                "Cookie": cookie_str,
            },
            playwright_page=context_page,
            cookie_dict=cookie_dict,
            proxy_ip_pool=ip_proxy_pool,
        )

        if not await client.pong():
            login_obj = XiaoHongShuLogin(
                login_type=login_type,
                login_phone="",
                browser_context=browser_context,
                context_page=context_page,
                cookie_str=cookies,
            )
            await login_obj.begin()
            await client.update_cookies(browser_context=browser_context)

        # Attach a best-effort cleanup hook.
        runtime = XhsRuntime(browser_context=browser_context, context_page=context_page, client=client)
        runtime._playwright_cm = playwright_cm  # type: ignore[attr-defined]
        runtime._cdp_manager = cdp_manager  # type: ignore[attr-defined]
        return runtime
    except Exception:
        # Ensure playwright is closed if partial init fails.
        try:
            if cdp_manager:
                await cdp_manager.cleanup(force=True)
        except Exception:
            pass
        try:
            await playwright_cm.__aexit__(None, None, None)
        except Exception:
            pass
        raise


async def xhs_close_runtime(runtime: XhsRuntime) -> None:
    """Close browser resources."""

    cdp_manager = getattr(runtime, "_cdp_manager", None)
    playwright_cm = getattr(runtime, "_playwright_cm", None)

    try:
        if cdp_manager:
            await cdp_manager.cleanup(force=True)
        else:
            await runtime.browser_context.close()
    finally:
        if playwright_cm is not None:
            await playwright_cm.__aexit__(None, None, None)


async def xhs_search_notes(
    *,
    runtime: XhsRuntime,
    keyword: str,
    page: int = 1,
    page_size: int = 20,
    sort_type: str = "popularity_descending",
) -> Dict[str, Any]:
    _ensure_mediacrawler_importable()
    from media_platform.xhs.field import SearchSortType
    from media_platform.xhs.help import get_search_id

    sort = SearchSortType(sort_type) if sort_type else SearchSortType.GENERAL
    return await runtime.client.get_note_by_keyword(
        keyword=keyword,
        search_id=get_search_id(),
        page=page,
        page_size=page_size,
        sort=sort,
    )


async def xhs_get_note_detail(
    *,
    runtime: XhsRuntime,
    note_id: str,
    xsec_source: str,
    xsec_token: str,
    enable_cookie_fallback: bool = True,
) -> Dict[str, Any]:
    """Get note detail; fallback to HTML parsing if API is empty."""

    try:
        detail = await runtime.client.get_note_by_id(note_id, xsec_source, xsec_token)
    except Exception:
        detail = {}

    if detail:
        detail.update({"xsec_token": xsec_token, "xsec_source": xsec_source})
        return detail

    if not enable_cookie_fallback:
        return {}

    detail = await runtime.client.get_note_by_id_from_html(
        note_id,
        xsec_source,
        xsec_token,
        enable_cookie=True,
    )
    if detail:
        detail.update({"xsec_token": xsec_token, "xsec_source": xsec_source})
    return detail or {}


async def xhs_get_note_comments(
    *,
    runtime: XhsRuntime,
    note_id: str,
    xsec_token: str,
    max_count: int = 10,
    crawl_interval_sec: float = 1.0,
) -> List[Dict[str, Any]]:
    collected: List[Dict[str, Any]] = []

    async def _collector(_note_id: str, comments: List[Dict[str, Any]]) -> None:
        if _note_id != note_id:
            return
        collected.extend(comments)

    await runtime.client.get_note_all_comments(
        note_id=note_id,
        xsec_token=xsec_token,
        crawl_interval=crawl_interval_sec,
        callback=_collector,
        max_count=max_count,
    )
    return collected


async def xhs_get_creator_info(
    *,
    runtime: XhsRuntime,
    creator_url_or_id: str,
) -> Dict[str, Any]:
    _ensure_mediacrawler_importable()
    from media_platform.xhs.help import parse_creator_info_from_url

    creator_info = parse_creator_info_from_url(creator_url_or_id)
    return await runtime.client.get_creator_info(
        user_id=creator_info.user_id,
        xsec_token=creator_info.xsec_token,
        xsec_source=creator_info.xsec_source,
    )


async def xhs_get_creator_notes(
    *,
    runtime: XhsRuntime,
    creator_url_or_id: str,
    max_notes: int = 30,
    crawl_interval_sec: float = 1.0,
) -> List[Dict[str, Any]]:
    """Fetch creator's notes list (note_id/xsec_token/xsec_source)."""

    _ensure_mediacrawler_importable()
    import config as mc_config
    from media_platform.xhs.help import parse_creator_info_from_url

    creator_info = parse_creator_info_from_url(creator_url_or_id)

    old_max = mc_config.CRAWLER_MAX_NOTES_COUNT
    mc_config.CRAWLER_MAX_NOTES_COUNT = max_notes
    try:
        notes: List[Dict[str, Any]] = []

        async def _collector(batch: List[Dict[str, Any]]) -> None:
            notes.extend(batch)

        await runtime.client.get_all_notes_by_creator(
            user_id=creator_info.user_id,
            crawl_interval=crawl_interval_sec,
            callback=_collector,
            xsec_token=creator_info.xsec_token,
            xsec_source=creator_info.xsec_source or "pc_feed",
        )
        return notes
    finally:
        mc_config.CRAWLER_MAX_NOTES_COUNT = old_max


@tool(
    "xhs_search",
    description=(
        "小红书关键词搜索（不落盘）。返回搜索列表原始数据 items。"
        "参数：keyword, page(默认1), page_size(默认20), sort_type(默认popularity_descending)。"
    ),
)
async def xhs_search_tool(
    keyword: str,
    page: int = 1,
    page_size: int = 20,
    sort_type: str = "popularity_descending",
) -> str:
    runtime = await _create_xhs_runtime()
    try:
        data = await xhs_search_notes(
            runtime=runtime,
            keyword=keyword,
            page=page,
            page_size=page_size,
            sort_type=sort_type,
        )
        return json.dumps(data, ensure_ascii=False)
    finally:
        await xhs_close_runtime(runtime)


@tool(
    "xhs_note_detail",
    description=(
        "小红书笔记详情（不落盘）。参数：note_url（必须包含 xsec_token / xsec_source），"
        "返回 note_card 结构。"
    ),
)
async def xhs_note_detail_tool(note_url: str) -> str:
    _ensure_mediacrawler_importable()
    from media_platform.xhs.help import parse_note_info_from_note_url

    url_info = parse_note_info_from_note_url(note_url)
    runtime = await _create_xhs_runtime()
    try:
        data = await xhs_get_note_detail(
            runtime=runtime,
            note_id=url_info.note_id,
            xsec_source=url_info.xsec_source,
            xsec_token=url_info.xsec_token,
        )
        return json.dumps(data, ensure_ascii=False)
    finally:
        await xhs_close_runtime(runtime)


@tool(
    "xhs_note_comments",
    description=(
        "小红书笔记评论（不落盘）。参数：note_url（必须包含 xsec_token / xsec_source），max_count(默认10)。"
    ),
)
async def xhs_note_comments_tool(note_url: str, max_count: int = 10) -> str:
    _ensure_mediacrawler_importable()
    from media_platform.xhs.help import parse_note_info_from_note_url

    url_info = parse_note_info_from_note_url(note_url)
    runtime = await _create_xhs_runtime()
    try:
        data = await xhs_get_note_comments(
            runtime=runtime,
            note_id=url_info.note_id,
            xsec_token=url_info.xsec_token,
            max_count=max_count,
        )
        return json.dumps(data, ensure_ascii=False)
    finally:
        await xhs_close_runtime(runtime)


@tool(
    "xhs_creator_info",
    description=(
        "小红书创作者信息（不落盘）。参数：creator_url_or_id（可以是 profile URL 或 24位 user_id）。"
    ),
)
async def xhs_creator_info_tool(creator_url_or_id: str) -> str:
    runtime = await _create_xhs_runtime()
    try:
        data = await xhs_get_creator_info(runtime=runtime, creator_url_or_id=creator_url_or_id)
        return json.dumps(data, ensure_ascii=False)
    finally:
        await xhs_close_runtime(runtime)


@tool(
    "xhs_creator_notes",
    description=(
        "小红书创作者笔记列表（不落盘）。参数：creator_url_or_id，max_notes(默认30)。返回 notes 数组。"
    ),
)
async def xhs_creator_notes_tool(creator_url_or_id: str, max_notes: int = 30) -> str:
    runtime = await _create_xhs_runtime()
    try:
        data = await xhs_get_creator_notes(
            runtime=runtime,
            creator_url_or_id=creator_url_or_id,
            max_notes=max_notes,
        )
        return json.dumps(data, ensure_ascii=False)
    finally:
        await xhs_close_runtime(runtime)
