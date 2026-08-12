"""Generic official-website source document provider.

Static HTML is fetched directly so large notice archives do not consume the
Chrome pool. The provider extracts the article body, downloads meaningful
images and attachments, and appends attachment text to the analysis corpus.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import re
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit

import aiohttp
from lxml import html as lxml_html
from PIL import Image

from core.logger import get_logger

from .contracts import (
    CapturedAttachment,
    CapturedDocument,
    CapturedImage,
    SourceDocumentError,
)
from .resources import (
    fetch_resource_with_retry,
    extract_attachment_text,
    is_attachment_link,
)
from .urls import canonicalize_source_url


logger = get_logger("source_document.web")

_MAX_HTML_BYTES = 8 * 1024 * 1024
_MAX_IMAGE_BYTES = 12 * 1024 * 1024
_MAX_IMAGES = 24
_MAX_ATTACHMENT_BYTES = 30 * 1024 * 1024
_MAX_TOTAL_ATTACHMENT_BYTES = 160 * 1024 * 1024
_MAX_ATTACHMENTS = 30
_MAIN_XPATHS = (
    "//article",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' left ') "
    "and contains(concat(' ', normalize-space(@class), ' '), ' fl ')]",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' articleCon ')]",
    "//*[@id='content']",
    "//*[@id='article']",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' TRS_Editor ')]",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' article-content ')]",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' article_content ')]",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' content-body ')]",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' detail-content ')]",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' news-content ')]",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' zwxl ')]",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' txt_con ')]",
)
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})日?(?!\d)")
_NOISE_XPATH = (
    ".//script|.//style|.//noscript|.//template|.//nav|.//header|.//footer|"
    ".//*[contains(concat(' ', normalize-space(@class), ' '), ' breadcrumb ')]|"
    ".//*[contains(concat(' ', normalize-space(@class), ' '), ' pagination ')]|"
    ".//*[contains(concat(' ', normalize-space(@class), ' '), ' share ')]"
)
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def _decode_html(data: bytes, content_type: str = "") -> str:
    charset_match = re.search(r"charset=([^;\s]+)", content_type, re.I)
    encodings = [charset_match.group(1).strip('"\'') if charset_match else ""]
    encodings.extend(["utf-8", "gb18030"])
    for encoding in encodings:
        if not encoding:
            continue
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _clean_text(value: str) -> str:
    return re.sub(r"[ \t\f\v]+", " ", re.sub(r"\n{3,}", "\n\n", value)).strip()


def _meta_value(root, *selectors: str) -> str:
    for selector in selectors:
        for node in root.xpath(selector):
            value = str(node or "").strip()
            if value:
                return value
    return ""


def _select_main(root):
    best = None
    best_size = 0
    for xpath in _MAIN_XPATHS:
        for node in root.xpath(xpath):
            size = len(_clean_text(node.text_content()))
            if size > best_size:
                best = node
                best_size = size
    if best is not None and best_size >= 80:
        return best
    bodies = root.xpath("//body")
    return bodies[0] if bodies else root


def parse_official_html(
    data: bytes,
    *,
    url: str,
    content_type: str = "text/html",
) -> dict:
    """Extract stable article metadata and evidence URLs from one HTML page."""
    decoded = _decode_html(data, content_type)
    try:
        root = lxml_html.fromstring(decoded, base_url=url)
    except (TypeError, ValueError) as exc:
        raise SourceDocumentError(f"HTML 解析失败: {exc}") from exc

    main = _select_main(root)
    links: list[dict[str, str]] = []
    seen_links: set[str] = set()
    for anchor in main.xpath(".//a[@href]"):
        href = str(anchor.get("href") or "").strip()
        if not href or href.lower().startswith(
            ("javascript:", "data:", "mailto:", "tel:")
        ):
            continue
        absolute = urljoin(url, href)
        if absolute in seen_links:
            continue
        seen_links.add(absolute)
        links.append(
            {
                "url": absolute,
                "label": _clean_text(anchor.text_content())[:300],
            }
        )

    image_urls: list[str] = []
    for image in main.xpath(".//img"):
        raw = str(
            image.get("data-src")
            or image.get("data-original")
            or image.get("src")
            or ""
        ).strip()
        if not raw or raw.startswith(("data:", "javascript:")):
            continue
        absolute = urljoin(url, raw)
        if absolute not in image_urls:
            image_urls.append(absolute)

    for node in main.xpath(_NOISE_XPATH):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    text = _clean_text(main.text_content())
    title = _meta_value(
        root,
        "//meta[@name='ArticleTitle']/@content",
        "//meta[@name='title']/@content",
        "//h1[1]//text()",
        "//h2[1]//text()",
        "//h3[1]//text()",
        "//h4[1]//text()",
        "//h5[1]//text()",
        "//meta[@property='og:title']/@content",
        "//title[1]//text()",
    )
    publish_time = _meta_value(
        root,
        "//meta[@property='article:published_time']/@content",
        "//meta[@name='PubDate']/@content",
        "//meta[@name='publishdate']/@content",
        "//meta[@name='date']/@content",
    )
    if not publish_time:
        match = _DATE_RE.search(_clean_text(root.text_content())[:20_000])
        if match:
            publish_time = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    account = _meta_value(
        root,
        "//meta[@name='author']/@content",
        "//meta[@name='SiteName']/@content",
        "//meta[@property='og:site_name']/@content",
    ) or str(urlsplit(url).hostname or "")
    return {
        "title": _clean_text(title)[:500],
        "account": _clean_text(account)[:300],
        "publish_time": _clean_text(publish_time)[:100],
        "text": text,
        "links": links,
        "image_urls": image_urls,
        "attachment_links": [link for link in links if is_attachment_link(link)],
    }


def _image_dimensions(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:
        return 0, 0


class OfficialWebDocumentProvider:
    source_type = "official_website_document"

    def supports(self, url: str) -> bool:
        try:
            parsed = urlsplit(url)
            return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        except ValueError:
            return False

    async def capture(self, url: str, *, task_id: str = "") -> CapturedDocument:
        canonical_url = canonicalize_source_url(url)
        timeout = aiohttp.ClientTimeout(total=120, connect=20, sock_read=90)
        connector = aiohttp.TCPConnector(
            limit=16,
            ttl_dns_cache=180,
            ssl=False,
        )
        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            trust_env=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/138.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            },
        ) as session:
            try:
                page = await fetch_resource_with_retry(
                    session,
                    canonical_url,
                    max_bytes=_MAX_HTML_BYTES,
                )
            except Exception as exc:  # noqa: BLE001
                raise SourceDocumentError(f"官网正文读取失败: {exc}") from exc
            if "html" not in page.content_type.lower() and not page.data.lstrip().startswith(
                (b"<!DOCTYPE", b"<html", b"<HTML")
            ):
                raise SourceDocumentError(
                    f"来源不是 HTML 页面: {page.content_type}"
                )
            parsed = parse_official_html(
                page.data,
                url=page.url,
                content_type=page.content_type,
            )
            images, image_errors = await self._download_images(
                session,
                parsed["image_urls"],
            )
            attachments, attachment_errors = await self._download_attachments(
                session,
                parsed["attachment_links"],
            )

        attachment_text = [
            f"【附件 {item.filename}】\n{item.extracted_text}"
            for item in attachments
            if item.extracted_text
        ]
        combined_text = parsed["text"]
        if attachment_text:
            combined_text += "\n\n" + "\n\n".join(attachment_text)
        return CapturedDocument(
            source_type=self.source_type,
            canonical_url=canonicalize_source_url(page.url),
            requested_url=url,
            title=parsed["title"],
            account=parsed["account"],
            publish_time=parsed["publish_time"],
            text=combined_text,
            raw_html=page.data,
            rendered_html=page.data,
            images=images,
            attachments=attachments,
            metadata={
                "http_status": 200,
                "final_url": page.url,
                "article_text": parsed["text"],
                "image_urls": parsed["image_urls"],
                "image_download_errors": image_errors,
                "images_truncated": max(0, len(parsed["image_urls"]) - _MAX_IMAGES),
                "attachment_urls": [
                    link["url"] for link in parsed["attachment_links"]
                ],
                "attachment_download_errors": attachment_errors,
                "attachments_truncated": max(
                    0,
                    len(parsed["attachment_links"]) - _MAX_ATTACHMENTS,
                ),
                "discovered_links": len(parsed["links"]),
            },
        )

    @staticmethod
    async def _download_images(
        session: aiohttp.ClientSession,
        urls: list[str],
    ) -> tuple[list[CapturedImage], list[str]]:
        semaphore = asyncio.Semaphore(8)

        async def _download(index: int, image_url: str):
            async with semaphore:
                try:
                    fetched = await fetch_resource_with_retry(
                        session,
                        image_url,
                        max_bytes=_MAX_IMAGE_BYTES,
                        attempts=2,
                    )
                    suffix = PurePosixPath(urlsplit(fetched.url).path).suffix.lower()
                    if (
                        not fetched.content_type.lower().startswith("image/")
                        and suffix not in _IMAGE_EXTENSIONS
                    ):
                        raise ValueError(f"非图片内容: {fetched.content_type}")
                    width, height = _image_dimensions(fetched.data)
                    if width and height and width < 160 and height < 160:
                        return None
                    return CapturedImage(
                        index=index,
                        source_url=fetched.url,
                        data=fetched.data,
                        content_type=fetched.content_type.split(";", 1)[0],
                        width=width,
                        height=height,
                        sha256=hashlib.sha256(fetched.data).hexdigest(),
                    )
                except Exception as exc:  # noqa: BLE001
                    return f"{image_url}: {exc}"

        results = await asyncio.gather(
            *(
                _download(index, image_url)
                for index, image_url in enumerate(urls[:_MAX_IMAGES])
            )
        )
        return (
            [item for item in results if isinstance(item, CapturedImage)],
            [str(item)[:800] for item in results if isinstance(item, str)],
        )

    @staticmethod
    async def _download_attachments(
        session: aiohttp.ClientSession,
        links: list[dict[str, str]],
    ) -> tuple[list[CapturedAttachment], list[str]]:
        attachments: list[CapturedAttachment] = []
        errors: list[str] = []
        remaining = _MAX_TOTAL_ATTACHMENT_BYTES
        for index, link in enumerate(links[:_MAX_ATTACHMENTS]):
            if remaining <= 0:
                errors.append("单篇文档附件累计超过 160 MiB 安全上限")
                break
            try:
                fetched = await fetch_resource_with_retry(
                    session,
                    link["url"],
                    max_bytes=min(_MAX_ATTACHMENT_BYTES, remaining),
                )
                remaining -= len(fetched.data)
                text, text_error, text_format = await asyncio.to_thread(
                    extract_attachment_text,
                    fetched.data,
                    filename=fetched.filename,
                    content_type=fetched.content_type,
                )
                attachments.append(
                    CapturedAttachment(
                        index=index,
                        source_url=fetched.url,
                        filename=fetched.filename,
                        data=fetched.data,
                        content_type=fetched.content_type,
                        label=str(link.get("label") or "")[:300],
                        sha256=hashlib.sha256(fetched.data).hexdigest(),
                        extracted_text=text,
                        text_format=text_format,
                        text_error=text_error,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{link['url']}: {exc}"[:1000])
        return attachments, errors
