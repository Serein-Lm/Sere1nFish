"""Classify fetched bytes before dispatching HTML or attachment extraction."""
from dataclasses import replace
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit

from .contracts import FetchedResource

# Extension and link semantics are supplied lazily to keep the transport layer acyclic.
ATTACHMENT_ENDPOINT_RE = re.compile(
    r"(?:^|[/_?&=-])(?:attachment|download|file)(?:$|[/_?&=-])|"
    r"(?:file|attach)(?:id|name|path)=",
    re.I,
)
ATTACHMENT_CONTENT_TYPES = {
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


def looks_like_attachment_url(value: str) -> bool:
    from .resources import ATTACHMENT_EXTENSIONS

    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return bool(
        PurePosixPath(parsed.path).suffix.lower() in ATTACHMENT_EXTENSIONS
        or ATTACHMENT_ENDPOINT_RE.search(f"{parsed.path}?{parsed.query}")
    )


def looks_like_attachment_response(
    *,
    requested_url: str,
    final_url: str,
    filename: str,
    content_type: str,
) -> bool:
    from .resources import is_attachment_link

    media_type = str(content_type or "").split(";", 1)[0].strip().lower()
    return bool(
        looks_like_attachment_url(requested_url)
        or looks_like_attachment_url(final_url)
        or is_attachment_link({"url": final_url, "label": filename})
        or media_type in ATTACHMENT_CONTENT_TYPES
    )


def signature_content_type(data: bytes) -> str:
    prefix = data[:1024].lstrip(b"\xef\xbb\xbf \t\r\n")
    if prefix.startswith(b"%PDF"):
        return "application/pdf"
    if prefix.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "application/zip"
    if prefix.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        # OLE directory stream names identify legacy Office containers; preserve
        # unknown containers as binary evidence instead of trying HTML/Chrome.
        streams = (("Workbook", "application/vnd.ms-excel"), ("Book", "application/vnd.ms-excel"),
                   ("WordDocument", "application/msword"), ("PowerPoint Document", "application/vnd.ms-powerpoint"))
        for name, media_type in streams:
            if name.encode("utf-16-le") + b"\0\0" in data:
                return media_type
        return "application/octet-stream"
    if prefix.lower().startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        return "text/html"
    return ""


def normalize_resource(resource: FetchedResource) -> FetchedResource:
    detected = signature_content_type(resource.data)
    # Do not discard a more specific Office media type for its ZIP/OLE envelope.
    reported = resource.content_type.split(";", 1)[0].strip().lower()
    if detected == "application/zip" and "openxmlformats-officedocument" in reported:
        detected = reported
    if detected == "application/octet-stream" and reported in {
        "application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
    }:
        detected = reported
    if detected and detected != reported:
        return replace(resource, content_type=detected,
                       reported_content_type=resource.reported_content_type or resource.content_type)
    return resource


def is_html_resource(resource: FetchedResource) -> bool:
    detected = signature_content_type(resource.data)
    if detected:
        return detected == "text/html"
    return "html" in resource.content_type.lower()


def is_attachment_resource(resource: FetchedResource) -> bool:
    return not is_html_resource(resource) and (
        bool(signature_content_type(resource.data)) or looks_like_attachment_response(
            requested_url=resource.requested_url or resource.url, final_url=resource.url,
            filename=resource.filename, content_type=resource.content_type,
        )
    )


def http_error_status(error: BaseException) -> int:
    import aiohttp

    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, aiohttp.ClientResponseError):
            return int(error.status or 0)
        error = error.__cause__ or error.__context__
    return 0


def is_permanently_missing(error: BaseException) -> bool:
    return http_error_status(error) in {404, 410}
