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
    ATTACHMENT_EXTENSIONS,
    extract_html_links,
    extract_attachment_text,
    fetch_resource_with_retry,
    is_attachment_link,
)
from .urls import canonicalize_source_url


logger = get_logger("source_document.web")

_MAX_HTML_BYTES = 8 * 1024 * 1024
_MAX_IMAGE_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_IMAGE_BYTES = 512 * 1024 * 1024
_MAX_IMAGES = 160
_IMAGE_DOWNLOAD_CONCURRENCY = 8
_MAX_ATTACHMENT_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_ATTACHMENT_BYTES = 512 * 1024 * 1024
_MAX_ATTACHMENTS = 120
_ATTACHMENT_DOWNLOAD_CONCURRENCY = 4
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
_ATTACHMENT_ENDPOINT_RE = re.compile(
    r"(?:^|[/_?&=-])(?:attachment|download|file)(?:$|[/_?&=-])|"
    r"(?:file|attach)(?:id|name|path)=",
    re.I,
)
_ATTACHMENT_CONTENT_TYPES = {
    "application/msword",
    "application/octet-stream",
    "application/pdf",
    "application/rtf",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/zip",
    "text/csv",
    "text/plain",
}


def _is_public_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _looks_like_attachment_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return bool(
        PurePosixPath(parsed.path).suffix.lower() in ATTACHMENT_EXTENSIONS
        or _ATTACHMENT_ENDPOINT_RE.search(f"{parsed.path}?{parsed.query}")
    )


def _looks_like_attachment_response(
    *,
    requested_url: str,
    final_url: str,
    filename: str,
    content_type: str,
) -> bool:
    media_type = str(content_type or "").split(";", 1)[0].strip().lower()
    return bool(
        _looks_like_attachment_url(requested_url)
        or _looks_like_attachment_url(final_url)
        or is_attachment_link({"url": final_url, "label": filename})
        or media_type in _ATTACHMENT_CONTENT_TYPES
    )


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
    links = extract_html_links(main, url)
    attachment_links: list[dict[str, str]] = []
    attachment_urls: set[str] = set()
    main_link_urls = {str(link.get("url") or "") for link in links}
    for link in [*links, *extract_html_links(root, url)]:
        link_url = str(link.get("url") or "")
        explicit_attachment = bool(
            PurePosixPath(urlsplit(link_url).path).suffix.lower()
            in ATTACHMENT_EXTENSIONS
            or PurePosixPath(str(link.get("label") or "")).suffix.lower()
            in ATTACHMENT_EXTENSIONS
        )
        if (
            not link_url
            or not _is_public_http_url(link_url)
            or link_url in attachment_urls
            or not is_attachment_link(link)
            or (link_url not in main_link_urls and not explicit_attachment)
        ):
            continue
        attachment_urls.add(link_url)
        attachment_links.append(link)

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
        if _is_public_http_url(absolute) and absolute not in image_urls:
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
        "attachment_links": attachment_links,
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
        fetch_limit = (
            _MAX_ATTACHMENT_BYTES
            if _looks_like_attachment_url(canonical_url)
            else _MAX_HTML_BYTES
        )
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
                    max_bytes=fetch_limit,
                )
            except Exception as exc:  # noqa: BLE001
                raise SourceDocumentError(f"官网正文读取失败: {exc}") from exc
            is_html = "html" in page.content_type.lower() or page.data.lstrip().startswith(
                (b"<!DOCTYPE", b"<html", b"<HTML")
            )
            if not is_html and _looks_like_attachment_response(
                requested_url=canonical_url,
                final_url=page.url,
                filename=page.filename,
                content_type=page.content_type,
            ):
                extracted_text, text_error, text_format = await asyncio.to_thread(
                    extract_attachment_text,
                    page.data,
                    filename=page.filename,
                    content_type=page.content_type,
                )
                attachment = CapturedAttachment(
                    index=0,
                    source_url=page.url,
                    filename=page.filename,
                    data=page.data,
                    content_type=page.content_type,
                    label=page.filename,
                    sha256=hashlib.sha256(page.data).hexdigest(),
                    extracted_text=extracted_text,
                    text_format=text_format,
                    text_error=text_error,
                )
                return CapturedDocument(
                    source_type=self.source_type,
                    canonical_url=canonicalize_source_url(page.url),
                    requested_url=url,
                    title=page.filename,
                    account=str(urlsplit(page.url).hostname or ""),
                    publish_time="",
                    text=extracted_text or page.filename,
                    raw_html=b"",
                    rendered_html=b"",
                    attachments=[attachment],
                    metadata={
                        "http_status": 200,
                        "final_url": page.url,
                        "article_text": extracted_text,
                        "direct_attachment": True,
                        "image_urls": [],
                        "image_download_errors": [],
                        "image_download_warnings": [],
                        "images_truncated": 0,
                        "attachment_urls": [page.url],
                        "attachment_download_errors": [],
                        "attachment_download_warnings": [],
                        "attachment_text_errors": [text_error] if text_error else [],
                        "attachment_text_warnings": [],
                        "attachments_truncated": 0,
                        "discovered_links": 0,
                    },
                )
            if not is_html:
                raise SourceDocumentError(
                    f"来源不是 HTML 页面: {page.content_type}"
                )
            parsed = parse_official_html(
                page.data,
                url=page.url,
                content_type=page.content_type,
            )
            images, image_errors, image_warnings = await self._download_images(
                session, parsed["image_urls"]
            )
            (
                attachments,
                attachment_errors,
                attachment_warnings,
            ) = await self._download_attachments(
                session, parsed["attachment_links"]
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
                "image_download_warnings": image_warnings,
                "images_truncated": max(0, len(parsed["image_urls"]) - _MAX_IMAGES),
                "attachment_urls": [
                    link["url"] for link in parsed["attachment_links"]
                ],
                "attachment_download_errors": attachment_errors,
                "attachment_download_warnings": attachment_warnings,
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
    ) -> tuple[list[CapturedImage], list[str], list[str]]:
        async def _download(index: int, image_url: str):
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
            except aiohttp.ClientResponseError as exc:
                level = "warning" if exc.status in {404, 410} else "error"
                return (
                    level,
                    f"{image_url}: HTTP {exc.status} {exc.message}"[:800],
                )
            except Exception as exc:  # noqa: BLE001
                return ("error", f"{image_url}: {exc}"[:800])

        images: list[CapturedImage] = []
        errors: list[str] = []
        warnings: list[str] = []
        remaining = _MAX_TOTAL_IMAGE_BYTES
        indexed_urls = list(enumerate(urls[:_MAX_IMAGES]))
        for offset in range(0, len(indexed_urls), _IMAGE_DOWNLOAD_CONCURRENCY):
            if remaining <= 0:
                errors.append(
                    f"单篇文档原图累计超过 "
                    f"{_MAX_TOTAL_IMAGE_BYTES // 1024 // 1024} MiB 安全上限"
                )
                break
            chunk = indexed_urls[offset : offset + _IMAGE_DOWNLOAD_CONCURRENCY]
            downloaded = await asyncio.gather(
                *(_download(index, image_url) for index, image_url in chunk)
            )
            for item in downloaded:
                if isinstance(item, tuple):
                    (warnings if item[0] == "warning" else errors).append(item[1])
                    continue
                if not isinstance(item, CapturedImage):
                    continue
                if len(item.data) > remaining:
                    errors.append(
                        f"{item.source_url}: 单篇文档原图累计超过 "
                        f"{_MAX_TOTAL_IMAGE_BYTES // 1024 // 1024} MiB 安全上限"
                    )
                    continue
                remaining -= len(item.data)
                images.append(item)
        return images, errors, warnings

    @staticmethod
    async def _download_attachments(
        session: aiohttp.ClientSession,
        links: list[dict[str, str]],
    ) -> tuple[list[CapturedAttachment], list[str], list[str]]:
        attachments: list[CapturedAttachment] = []
        errors: list[str] = []
        warnings: list[str] = []
        extracted_by_hash: dict[str, tuple[str, str, str]] = {}
        remaining = _MAX_TOTAL_ATTACHMENT_BYTES
        indexed_links = list(enumerate(links[:_MAX_ATTACHMENTS]))

        async def _download(index: int, link: dict[str, str]):
            try:
                fetched = await fetch_resource_with_retry(
                    session,
                    link["url"],
                    max_bytes=_MAX_ATTACHMENT_BYTES,
                )
                return index, link, fetched, "", ""
            except aiohttp.ClientResponseError as exc:
                message = f"{link['url']}: HTTP {exc.status} {exc.message}"[:1000]
                level = "warning" if exc.status in {404, 410} else "error"
                return index, link, None, level, message
            except Exception as exc:  # noqa: BLE001
                return index, link, None, "error", f"{link['url']}: {exc}"[:1000]

        for offset in range(0, len(indexed_links), _ATTACHMENT_DOWNLOAD_CONCURRENCY):
            if remaining <= 0:
                errors.append(
                    f"单篇文档附件累计超过 "
                    f"{_MAX_TOTAL_ATTACHMENT_BYTES // 1024 // 1024} MiB 安全上限"
                )
                break
            chunk = indexed_links[
                offset : offset + _ATTACHMENT_DOWNLOAD_CONCURRENCY
            ]
            downloaded = await asyncio.gather(
                *(_download(index, link) for index, link in chunk)
            )
            accepted = []
            for index, link, fetched, level, message in downloaded:
                if fetched is None:
                    (warnings if level == "warning" else errors).append(message)
                    continue
                if len(fetched.data) > remaining:
                    errors.append(
                        f"{link['url']}: 单篇文档附件累计超过 "
                        f"{_MAX_TOTAL_ATTACHMENT_BYTES // 1024 // 1024} MiB 安全上限"
                    )
                    continue
                remaining -= len(fetched.data)
                accepted.append(
                    (
                        index,
                        link,
                        fetched,
                        hashlib.sha256(fetched.data).hexdigest(),
                    )
                )

            pending_extracts: dict[str, tuple[bytes, str, str]] = {}
            for _index, _link, fetched, digest in accepted:
                if digest not in extracted_by_hash and digest not in pending_extracts:
                    pending_extracts[digest] = (
                        fetched.data,
                        fetched.filename,
                        fetched.content_type,
                    )
            if pending_extracts:
                digests = list(pending_extracts)
                extracted_values = await asyncio.gather(
                    *(
                        asyncio.to_thread(
                            extract_attachment_text,
                            pending_extracts[digest][0],
                            filename=pending_extracts[digest][1],
                            content_type=pending_extracts[digest][2],
                        )
                        for digest in digests
                    ),
                    return_exceptions=True,
                )
                for digest, extracted in zip(digests, extracted_values, strict=True):
                    if isinstance(extracted, Exception):
                        extracted_by_hash[digest] = (
                            "",
                            str(extracted)[:1000],
                            "",
                        )
                    else:
                        extracted_by_hash[digest] = extracted

            for index, link, fetched, digest in accepted:
                text, text_error, text_format = extracted_by_hash[digest]
                attachments.append(
                    CapturedAttachment(
                        index=index,
                        source_url=fetched.url,
                        filename=fetched.filename,
                        data=fetched.data,
                        content_type=fetched.content_type,
                        label=str(link.get("label") or "")[:300],
                        sha256=digest,
                        extracted_text=text,
                        text_format=text_format,
                        text_error=text_error,
                    )
                )
        return attachments, errors, warnings
