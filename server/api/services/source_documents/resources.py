"""Shared remote document and attachment resource helpers.

The downloader is intentionally provider-agnostic. Callers supply an aiohttp
session and remain responsible for domain policy; every redirect is still
validated against the public-URL boundary before bytes are read.
"""
from __future__ import annotations

import asyncio
import io
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit

import aiohttp
from lxml import html as lxml_html

from api.services.url_security import assert_public_http_url


DEFAULT_MAX_ATTACHMENT_TEXT_CHARS = 60_000
ATTACHMENT_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".zip",
}
ATTACHMENT_LABEL_MARKERS = (
    "附件",
    "下载",
    "采购文件",
    "招标文件",
    "投标文件",
    "报名表",
    "申请表",
)
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class FetchedResource:
    url: str
    data: bytes
    content_type: str
    filename: str


def html_text_and_links(
    content: str | bytes,
    base_url: str,
) -> tuple[str, list[dict[str, str]]]:
    if not content:
        return "", []
    try:
        root = lxml_html.fromstring(content, base_url=base_url)
    except (TypeError, ValueError):
        try:
            root = lxml_html.fragment_fromstring(content, create_parent=True)
        except (TypeError, ValueError):
            text = _WHITESPACE_RE.sub(" ", str(content)).strip()
            return text, []
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in root.xpath("//a[@href]"):
        href = str(anchor.get("href") or "").strip()
        if not href or href.lower().startswith(
            ("javascript:", "data:", "mailto:", "tel:")
        ):
            continue
        absolute = urljoin(base_url, href)
        try:
            parsed = urlsplit(absolute)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append(
            {
                "url": absolute,
                "label": _WHITESPACE_RE.sub(" ", anchor.text_content()).strip()[:300],
            }
        )
    for node in root.xpath("//script|//style|//noscript|//template"):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    return _WHITESPACE_RE.sub(" ", root.text_content()).strip(), links


def is_attachment_link(link: dict[str, str]) -> bool:
    url = str(link.get("url") or "")
    label = str(link.get("label") or "")
    url_suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    label_suffix = PurePosixPath(label).suffix.lower()
    return (
        url_suffix in ATTACHMENT_EXTENSIONS
        or label_suffix in ATTACHMENT_EXTENSIONS
        or any(marker in label for marker in ATTACHMENT_LABEL_MARKERS)
    )


def safe_remote_filename(value: str) -> str:
    raw = str(value or "").encode("utf-8", errors="surrogateescape")
    decoded = ""
    for encoding in ("utf-8", "gb18030"):
        try:
            decoded = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not decoded:
        decoded = raw.decode("utf-8", errors="replace")
    decoded = PurePosixPath(decoded.replace("\\", "/")).name
    decoded = re.sub(r"[\x00-\x1f\x7f]+", "_", decoded).strip(" .")
    return decoded[-180:]


def filename_from_response(
    url: str,
    headers: aiohttp.typedefs.LooseHeaders,
    content_type: str,
) -> str:
    disposition = str(headers.get("Content-Disposition") or "")
    match = re.search(
        r"filename\*?=(?:UTF-8''|\")?([^\";]+)",
        disposition,
        re.I,
    )
    if match:
        name = unquote(match.group(1).strip().strip('"'))
    else:
        name = unquote(PurePosixPath(urlsplit(url).path).name)
    name = safe_remote_filename(name)
    if not name:
        extension = mimetypes.guess_extension(
            content_type.split(";", 1)[0]
        ) or ".bin"
        name = "attachment" + extension
    return name


async def fetch_resource(
    session: aiohttp.ClientSession,
    url: str,
    *,
    max_bytes: int,
    redirects: int = 5,
) -> FetchedResource:
    current = str(url or "").strip()
    for _ in range(max(0, redirects) + 1):
        await assert_public_http_url(current)
        async with session.get(current, allow_redirects=False) as response:
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise RuntimeError(f"HTTP {response.status} 缺少跳转地址")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            declared_size = int(response.headers.get("Content-Length") or 0)
            if declared_size > max_bytes:
                raise ValueError(
                    f"远程文件超过 {max_bytes // 1024 // 1024} MiB 限制"
                )
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.content.iter_chunked(128 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(
                        f"远程文件超过 {max_bytes // 1024 // 1024} MiB 限制"
                    )
                chunks.append(chunk)
            content_type = str(
                response.headers.get("Content-Type")
                or "application/octet-stream"
            )
            final_url = str(response.url)
            return FetchedResource(
                url=final_url,
                data=b"".join(chunks),
                content_type=content_type,
                filename=filename_from_response(
                    final_url,
                    response.headers,
                    content_type,
                ),
            )
    raise RuntimeError("远程地址跳转次数过多")


async def fetch_resource_with_retry(
    session: aiohttp.ClientSession,
    url: str,
    *,
    max_bytes: int,
    attempts: int = 3,
) -> FetchedResource:
    """Retry transient transport errors while returning deterministic 4xx."""
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return await fetch_resource(session, url, max_bytes=max_bytes)
        except aiohttp.ClientResponseError as exc:
            last_error = exc
            if exc.status not in {408, 425, 429} and exc.status < 500:
                raise
        except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
            last_error = exc
        if attempt + 1 < attempts:
            await asyncio.sleep(0.5 * (2**attempt))
    if last_error is not None:
        raise last_error
    raise RuntimeError("远程资源读取失败")


def _extract_pdf_text(data: bytes, limit: int) -> tuple[str, str]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=False)
        pages: list[str] = []
        size = 0
        for page in reader.pages[:80]:
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(text)
                size += len(text)
            if size >= limit:
                break
        text = _WHITESPACE_RE.sub(" ", "\n".join(pages)).strip()[:limit]
        return text, "" if text else "PDF 未提取到可读文本，可能为扫描件"
    except Exception as exc:  # noqa: BLE001
        return "", str(exc)


def _extract_docx_text(data: bytes, limit: int) -> tuple[str, str]:
    try:
        from docx import Document

        document = Document(io.BytesIO(data))
        values = [paragraph.text.strip() for paragraph in document.paragraphs]
        size = sum(len(item) for item in values)
        for table in document.tables:
            for row in table.rows:
                line = " | ".join(cell.text.strip() for cell in row.cells)
                values.append(line)
                size += len(line)
                if size >= limit:
                    break
            if size >= limit:
                break
        return (
            _WHITESPACE_RE.sub(" ", "\n".join(item for item in values if item))
            .strip()[:limit],
            "",
        )
    except Exception as exc:  # noqa: BLE001
        return "", str(exc)


def _extract_xlsx_text(data: bytes, limit: int) -> tuple[str, str]:
    try:
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        values: list[str] = []
        size = 0
        for worksheet in workbook.worksheets[:30]:
            values.append(f"工作表: {worksheet.title}")
            for row_index, row in enumerate(
                worksheet.iter_rows(values_only=True),
                start=1,
            ):
                if row_index > 3_000:
                    break
                line = " | ".join(
                    str(cell).strip() for cell in row[:100] if cell is not None
                )
                if line:
                    values.append(line)
                    size += len(line)
                if size >= limit:
                    break
            if size >= limit:
                break
        workbook.close()
        return _WHITESPACE_RE.sub(" ", "\n".join(values)).strip()[:limit], ""
    except Exception as exc:  # noqa: BLE001
        return "", str(exc)


def _decode_command_output(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_legacy_doc_text(data: bytes, limit: int) -> tuple[str, str]:
    executable = shutil.which("antiword")
    if not executable:
        return "", "运行环境缺少 antiword，旧版 DOC 已归档但未解析"
    path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as handle:
            handle.write(data)
            path = handle.name
        result = subprocess.run(
            [executable, path],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        text = _WHITESPACE_RE.sub(
            " ",
            _decode_command_output(result.stdout),
        ).strip()[:limit]
        error = _decode_command_output(result.stderr).strip()
        if result.returncode and not text:
            return "", error or f"antiword 退出码 {result.returncode}"
        return text, "" if text else (error or "旧版 DOC 未提取到可读文本")
    except (OSError, subprocess.SubprocessError) as exc:
        return "", str(exc)
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _extract_legacy_xls_text(data: bytes, limit: int) -> tuple[str, str]:
    try:
        import xlrd

        workbook = xlrd.open_workbook(file_contents=data, on_demand=True)
        values: list[str] = []
        size = 0
        for sheet in workbook.sheets()[:30]:
            values.append(f"工作表: {sheet.name}")
            for row_index in range(min(sheet.nrows, 3_000)):
                line = " | ".join(
                    str(sheet.cell_value(row_index, column)).strip()
                    for column in range(min(sheet.ncols, 100))
                    if str(sheet.cell_value(row_index, column)).strip()
                )
                if line:
                    values.append(line)
                    size += len(line)
                if size >= limit:
                    break
            if size >= limit:
                break
        workbook.release_resources()
        text = _WHITESPACE_RE.sub(" ", "\n".join(values)).strip()[:limit]
        return text, "" if text else "旧版 XLS 未提取到可读文本"
    except Exception as exc:  # noqa: BLE001
        return "", str(exc)


def _extract_zip_text(data: bytes, limit: int) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = [item for item in archive.infolist() if not item.is_dir()]
            if len(entries) > 1_000:
                return "", "ZIP 附件文件数量超过 1000 个安全上限"
            if sum(item.file_size for item in entries) > 200 * 1024 * 1024:
                return "", "ZIP 附件解压后超过 200 MiB 安全上限"
            values: list[str] = []
            errors: list[str] = []
            remaining = limit
            for item in entries[:100]:
                suffix = PurePosixPath(item.filename).suffix.lower()
                if suffix not in ATTACHMENT_EXTENSIONS - {".zip"}:
                    continue
                nested = archive.read(item)
                text, error, _format = extract_attachment_text(
                    nested,
                    filename=item.filename,
                    content_type=mimetypes.guess_type(item.filename)[0] or "",
                    max_chars=remaining,
                )
                if text:
                    block = f"【压缩包文件 {safe_remote_filename(item.filename)}】\n{text}"
                    values.append(block)
                    remaining -= len(block)
                if error:
                    errors.append(f"{safe_remote_filename(item.filename)}: {error}")
                if remaining <= 0:
                    break
            joined = "\n\n".join(values)[:limit]
            if not joined and not errors:
                errors.append("ZIP 中没有可解析的文档附件")
            return joined, "; ".join(errors)[:2_000]
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        return "", str(exc)


def _openxml_kind(data: bytes) -> str:
    if not data.startswith(b"PK"):
        return ""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 5_000:
                raise ValueError("Office 附件压缩条目过多")
            if sum(item.file_size for item in entries) > 100 * 1024 * 1024:
                raise ValueError("Office 附件解压后超过 100 MiB 安全上限")
            names = {item.filename for item in entries}
    except (OSError, zipfile.BadZipFile):
        return ""
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return ""


def extract_attachment_text(
    data: bytes,
    *,
    filename: str,
    content_type: str,
    max_chars: int = DEFAULT_MAX_ATTACHMENT_TEXT_CHARS,
) -> tuple[str, str, str]:
    limit = max(1_000, min(int(max_chars), 300_000))
    suffix = PurePosixPath(str(filename or "")).suffix.lower()
    normalized_type = str(content_type or "").lower()
    if data.startswith(b"%PDF") or "pdf" in normalized_type or suffix == ".pdf":
        text, error = _extract_pdf_text(data, limit)
        return text, error, "pdf"
    try:
        openxml_kind = _openxml_kind(data)
    except ValueError as exc:
        return "", str(exc), suffix.removeprefix(".") or "openxml"
    if (
        openxml_kind == "docx"
        or suffix == ".docx"
        or "wordprocessingml" in normalized_type
    ):
        text, error = _extract_docx_text(data, limit)
        return text, error, "docx"
    if (
        openxml_kind == "xlsx"
        or suffix == ".xlsx"
        or "spreadsheetml" in normalized_type
    ):
        text, error = _extract_xlsx_text(data, limit)
        return text, error, "xlsx"
    if suffix == ".doc" or "msword" in normalized_type:
        text, error = _extract_legacy_doc_text(data, limit)
        return text, error, "doc"
    if suffix == ".xls" or "ms-excel" in normalized_type:
        text, error = _extract_legacy_xls_text(data, limit)
        return text, error, "xls"
    if suffix == ".zip" or normalized_type in {
        "application/zip",
        "application/x-zip-compressed",
    }:
        text, error = _extract_zip_text(data, limit)
        return text, error, "zip"
    return (
        "",
        "当前格式仅归档，未提取正文",
        suffix.removeprefix(".") or "binary",
    )
