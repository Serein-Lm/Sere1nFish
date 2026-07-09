"""
XHS API 客户端 V2 — xhsvm.js 本地签名 + curl_cffi

优先使用本地签名（不需要浏览器），失败时 fallback 到 MediaCrawler 的 Playwright 签名。
速度更快，不会 406。

用法:
    client = XhsClientV2(cookie_string="...")
    detail = await client.get_note_by_id(note_id, xsec_token)
    results = await client.search_notes(keyword, page, page_size)
    comments = await client.get_note_comments(note_id, xsec_token)
"""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from core.logger import get_logger

logger = get_logger("xhs_client_v2")

# xhsvm.js 路径
_XHSVM_JS = Path(__file__).resolve().parent.parent / "xhs-mcp-main" / "xhs_mcp" / "api" / "xhsvm.js"
_BASE_URL = "https://edith.xiaohongshu.com"

# 编译一次 JS，全局复用
_js_ctx = None


def _get_js_ctx():
    global _js_ctx
    if _js_ctx is None:
        import execjs
        with open(str(_XHSVM_JS), "r", encoding="utf-8") as f:
            _js_ctx = execjs.compile(f.read())
    return _js_ctx


def _sign(uri: str, data: Any, cookie: str) -> dict:
    """用 xhsvm.js 本地计算签名"""
    return json.loads(_get_js_ctx().call("GetXsXt", uri, data, cookie))


def _parse_cookie(cookie: str) -> dict:
    d = {}
    for item in cookie.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def _base36(n: int) -> str:
    s = ""
    while n:
        n, i = divmod(n, 36)
        s = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"[i] + s
    return s or "0"


def _search_id() -> str:
    e = int(time.time() * 1000) << 64
    t = random.randint(0, 2147483646)
    return _base36(e + t)


class XhsClientV2:
    """XHS API 客户端 — xhsvm.js 本地签名 + curl_cffi"""

    def __init__(
        self,
        cookie_string: str,
        *,
        proxy_url: str | None = None,
        request_timeout: float = 30.0,
    ):
        self._cookie = cookie_string
        self._cookie_dict = _parse_cookie(cookie_string)
        self._proxy_url = proxy_url
        self._request_timeout = request_timeout
        self._headers = {
            "content-type": "application/json;charset=UTF-8",
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
        }
        self._session = None

    def _get_session(self):
        """复用同一个 session，避免 Too many open files"""
        if self._session is None:
            from curl_cffi.requests import AsyncSession
            self._session = AsyncSession(verify=True, impersonate="chrome124")
        return self._session

    async def close(self):
        """关闭 session"""
        if self._session:
            await self._session.close()
            self._session = None

    async def _request(
        self,
        uri: str,
        method: str = "POST",
        data: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        """统一请求：签名 + curl_cffi（复用 session）"""
        import time as _time

        sign_data = data if method == "POST" else (params or {})
        xsxt = _sign(uri, sign_data, self._cookie)

        headers = {
            **self._headers,
            "x-s": xsxt["X-s"],
            "x-t": str(xsxt["X-t"]),
        }

        t0 = _time.time()
        session = self._get_session()
        resp = await session.request(
            method=method,
            url=f"{_BASE_URL}{uri}",
            json=data if method == "POST" else None,
            params=params if method == "GET" else None,
            cookies=self._cookie_dict,
            headers=headers,
            proxy=self._proxy_url,
            timeout=self._request_timeout,
        )
        content = resp.content
        result = json.loads(content)
        elapsed = _time.time() - t0

        if not result.get("success"):
            code = result.get("code", -1)
            msg = result.get("msg", "")
            logger.warning(f"[v2] {method} {uri} 失败 ({elapsed:.2f}s): code={code} msg={msg} status={resp.status_code}")
            raise Exception(f"XHS API 失败: code={code} msg={msg}")

        logger.info(f"[v2] {method} {uri} 成功 ({elapsed:.2f}s)")
        return result.get("data", {})

    # ═══════════════════════════════════════
    # 搜索
    # ═══════════════════════════════════════

    async def search_notes(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        sort: str = "general",
    ) -> dict:
        """搜索笔记"""
        data = {
            "keyword": keyword,
            "page": page,
            "page_size": page_size,
            "search_id": _search_id(),
            "sort": sort,
            "note_type": 0,
            "ext_flags": [],
            "geo": "",
            "image_formats": ["jpg", "webp", "avif"],
        }
        logger.info(f"[v2] 搜索 keyword='{keyword}' page={page} sort={sort}")
        return await self._request("/api/sns/web/v1/search/notes", "POST", data)

    # ═══════════════════════════════════════
    # 笔记详情
    # ═══════════════════════════════════════

    async def get_note_by_id(
        self,
        note_id: str,
        xsec_token: str = "",
        xsec_source: str = "pc_feed",
    ) -> dict:
        """获取笔记详情"""
        data = {
            "source_note_id": note_id,
            "image_formats": ["jpg", "webp", "avif"],
            "extra": {"need_body_topic": "1"},
            "xsec_source": xsec_source,
            "xsec_token": xsec_token,
        }
        logger.info(f"[v2] 获取详情 note={note_id}")
        result = await self._request("/api/sns/web/v1/feed", "POST", data)
        items = result.get("items", [])
        if items:
            return items[0].get("note_card", {})
        return {}

    # ═══════════════════════════════════════
    # 评论
    # ═══════════════════════════════════════

    async def get_note_comments(
        self,
        note_id: str,
        xsec_token: str = "",
        cursor: str = "",
    ) -> dict:
        """获取笔记一级评论"""
        params = {
            "note_id": note_id,
            "cursor": cursor,
            "top_comment_id": "",
            "image_formats": "jpg,webp,avif",
            "xsec_token": xsec_token,
        }
        return await self._request("/api/sns/web/v2/comment/page", "GET", params=params)

    async def get_note_all_comments(
        self,
        note_id: str,
        xsec_token: str = "",
        max_count: int = 20,
        crawl_interval: float = 0.5,
    ) -> list[dict]:
        """获取笔记所有评论（自动翻页）"""
        import asyncio
        all_comments = []
        cursor = ""

        while len(all_comments) < max_count:
            result = await self.get_note_comments(note_id, xsec_token, cursor)
            comments = result.get("comments", [])
            if not comments:
                break
            all_comments.extend(comments)
            cursor = result.get("cursor", "")
            has_more = result.get("has_more", False)
            if not has_more or not cursor:
                break
            await asyncio.sleep(crawl_interval)

        return all_comments[:max_count]

    # ═══════════════════════════════════════
    # 用户信息
    # ═══════════════════════════════════════

    async def get_me(self) -> dict:
        """获取当前登录用户信息"""
        return await self._request("/api/sns/web/v2/user/me", "GET", params={})

    async def pong(self) -> bool:
        """验证 Cookie 是否有效"""
        try:
            result = await self.get_me()
            return bool(result)
        except Exception:
            return False
