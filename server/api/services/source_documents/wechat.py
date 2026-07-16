"""微信公众号文章浏览器 Provider。

复用项目 Chrome 容器池。上下文参数来自已验证的小红书浏览器运行配置：固定桌面
UA、stealth 初始化脚本、中文 locale 和上海时区。Provider 只负责可靠读取，不负责
持久化、Target 关联或业务评分。
"""
from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from PIL import Image
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from browser_manager import get_browser_provider
from core.logger import get_logger

from .contracts import (
    CapturedDocument,
    CapturedImage,
    CapturedScreenshot,
    SourceDocumentBlocked,
    SourceDocumentError,
)
from .urls import canonicalize_source_url


logger = get_logger("source_document.wechat")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
_VIEWPORT = {"width": 1280, "height": 900}
_MAX_SCREENSHOTS = 40


def _without_fragment(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _image_dimensions(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:
        return 0, 0


class WechatArticleProvider:
    source_type = "wechat_article"

    def __init__(self, *, max_attempts: int = 2) -> None:
        self.max_attempts = max(1, min(max_attempts, 3))
        self._stealth_path = (
            Path(__file__).resolve().parents[3]
            / "MediaCrawler"
            / "libs"
            / "stealth.min.js"
        )

    def supports(self, url: str) -> bool:
        try:
            return urlsplit(url).hostname == "mp.weixin.qq.com"
        except Exception:
            return False

    async def capture(self, url: str, *, task_id: str = "") -> CapturedDocument:
        canonical_url = canonicalize_source_url(url)
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await self._capture_once(
                    canonical_url,
                    requested_url=url,
                    task_id=f"{task_id or 'source'}-{attempt}",
                )
            except SourceDocumentBlocked as exc:
                last_error = exc
                logger.warning(
                    "微信公众号文章要求验证 attempt=%s/%s url=%s",
                    attempt,
                    self.max_attempts,
                    canonical_url,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "微信公众号文章读取失败 attempt=%s/%s url=%s error=%s",
                    attempt,
                    self.max_attempts,
                    canonical_url,
                    exc,
                )
            if attempt < self.max_attempts:
                await asyncio.sleep(0.6 * attempt)
        if isinstance(last_error, SourceDocumentBlocked):
            raise last_error
        raise SourceDocumentError(str(last_error or "微信公众号文章读取失败"))

    async def _capture_once(
        self,
        canonical_url: str,
        *,
        requested_url: str,
        task_id: str,
    ) -> CapturedDocument:
        provider = get_browser_provider()
        url_key = hashlib.sha1(canonical_url.encode("utf-8")).hexdigest()[:12]
        lease_id = f"wechat-article-{task_id}-{url_key}"
        cdp_endpoint = await provider.get_cdp_endpoint(
            task_id=lease_id, purpose="wechat_article"
        )
        if not cdp_endpoint:
            raise SourceDocumentError("无法获取 Chrome 容器")

        context: BrowserContext | None = None
        browser: Browser | None = None
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(cdp_endpoint)
                context = await browser.new_context(
                    viewport=_VIEWPORT,
                    user_agent=_UA,
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                )
                if self._stealth_path.is_file():
                    await context.add_init_script(path=str(self._stealth_path))
                page = await context.new_page()
                response = await page.goto(
                    canonical_url,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                await page.wait_for_timeout(2_000)
                if (
                    "wappoc_appmsgcaptcha" in page.url
                    or await page.locator("text=当前环境异常").count()
                ):
                    raise SourceDocumentBlocked("微信要求完成环境验证")
                if await page.locator("#js_content").count() == 0:
                    raise SourceDocumentError("页面未找到公众号正文节点")

                raw_html = await response.body() if response else b""
                rendered_html = (await page.content()).encode("utf-8")
                metadata = await self._extract_metadata(page)
                screenshots = await self._capture_screenshots(page)
                image_urls = metadata.pop("image_urls")
                images, image_errors = await self._download_images(
                    context, image_urls
                )
                return CapturedDocument(
                    source_type=self.source_type,
                    canonical_url=canonical_url,
                    requested_url=requested_url,
                    title=metadata.pop("title"),
                    account=metadata.pop("account"),
                    publish_time=metadata.pop("publish_time"),
                    text=metadata.pop("text"),
                    raw_html=raw_html,
                    rendered_html=rendered_html,
                    images=images,
                    screenshots=screenshots,
                    metadata={
                        **metadata,
                        "http_status": response.status if response else None,
                        "final_url": page.url,
                        "image_urls": image_urls,
                        "image_download_errors": image_errors,
                    },
                )
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            # async_playwright 退出时会断开 CDP transport；不要 browser.close()，
            # 否则会终止池中的 Chrome 进程，导致下一任务无法复用。
            try:
                await provider.release_cdp_endpoint(task_id=lease_id)
            except Exception:
                pass

    @staticmethod
    async def _extract_metadata(page: Page) -> dict:
        return await page.evaluate(
            """() => {
                const root = document.querySelector('#js_content');
                const images = Array.from(root?.querySelectorAll('img') || []);
                const meta = (selector) => document.querySelector(selector)?.getAttribute('content') || '';
                return {
                    title: (document.querySelector('#activity-name')?.textContent || meta('meta[property="og:title"]') || document.title || '').trim(),
                    account: (document.querySelector('#js_name')?.textContent || '').trim(),
                    publish_time: (document.querySelector('#publish_time')?.textContent || '').trim(),
                    text: (root?.innerText || '').trim(),
                    image_urls: [...new Set(images.map(img => img.dataset.src || img.src).filter(Boolean))],
                    image_elements: images.length,
                    scroll_height: document.documentElement.scrollHeight,
                    description: meta('meta[name="description"]') || meta('meta[property="og:description"]')
                };
            }"""
        )

    @staticmethod
    async def _capture_screenshots(page: Page) -> list[CapturedScreenshot]:
        screenshots: list[CapturedScreenshot] = []
        await page.evaluate("window.scrollTo(0, 0)")
        previous_y = -1
        for index in range(_MAX_SCREENSHOTS):
            data = await page.screenshot(type="jpeg", quality=72)
            screenshots.append(CapturedScreenshot(index=index, data=data))
            state = await page.evaluate(
                """() => {
                    const before = window.scrollY;
                    const maxY = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
                    window.scrollBy(0, Math.floor(window.innerHeight * 0.82));
                    return {before, maxY};
                }"""
            )
            await page.wait_for_timeout(180)
            current_y = await page.evaluate("window.scrollY")
            if current_y >= state["maxY"]:
                if current_y != state["before"]:
                    data = await page.screenshot(type="jpeg", quality=72)
                    screenshots.append(
                        CapturedScreenshot(index=len(screenshots), data=data)
                    )
                break
            if current_y == previous_y or current_y == state["before"]:
                break
            previous_y = current_y
        return screenshots

    @staticmethod
    async def _download_images(
        context: BrowserContext, image_urls: list[str]
    ) -> tuple[list[CapturedImage], list[dict[str, str | int]]]:
        semaphore = asyncio.Semaphore(8)

        async def _download(index: int, source_url: str) -> CapturedImage:
            async with semaphore:
                request_url = _without_fragment(source_url)
                last_error: Exception | None = None
                for attempt in range(2):
                    try:
                        response = await context.request.get(
                            request_url,
                            headers={
                                "Referer": "https://mp.weixin.qq.com/",
                                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                            },
                            timeout=30_000,
                        )
                        if not response.ok:
                            raise SourceDocumentError(
                                f"status={response.status}"
                            )
                        data = await response.body()
                        width, height = _image_dimensions(data)
                        return CapturedImage(
                            index=index,
                            source_url=source_url,
                            data=data,
                            content_type=response.headers.get(
                                "content-type", "image/jpeg"
                            ).split(";", 1)[0],
                            width=width,
                            height=height,
                            sha256=hashlib.sha256(data).hexdigest(),
                        )
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        if attempt == 0:
                            await asyncio.sleep(0.25)
                raise SourceDocumentError(
                    f"文章原图下载失败 index={index}: {last_error}"
                )

        if not image_urls:
            return [], []
        results = await asyncio.gather(
            *(_download(index, url) for index, url in enumerate(image_urls)),
            return_exceptions=True,
        )
        images: list[CapturedImage] = []
        errors: list[dict[str, str | int]] = []
        for index, result in enumerate(results):
            if isinstance(result, BaseException):
                errors.append(
                    {
                        "index": index,
                        "source_url": image_urls[index],
                        "error": str(result)[:500],
                    }
                )
            else:
                images.append(result)
        if errors:
            logger.warning(
                "微信公众号文章部分原图下载失败 failed=%s total=%s",
                len(errors),
                len(image_urls),
            )
        return images, errors
