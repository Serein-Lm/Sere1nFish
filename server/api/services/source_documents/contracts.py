"""来源文档 Provider 协议与传输对象。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class CapturedImage:
    index: int
    source_url: str
    data: bytes
    content_type: str
    width: int = 0
    height: int = 0
    sha256: str = ""


@dataclass(slots=True)
class CapturedScreenshot:
    index: int
    data: bytes
    content_type: str = "image/jpeg"
    width: int = 1280
    height: int = 900


@dataclass(slots=True)
class CapturedDocument:
    source_type: str
    canonical_url: str
    requested_url: str
    title: str
    account: str
    publish_time: str
    text: str
    raw_html: bytes
    rendered_html: bytes
    images: list[CapturedImage] = field(default_factory=list)
    screenshots: list[CapturedScreenshot] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class SourceDocumentProvider(Protocol):
    source_type: str

    def supports(self, url: str) -> bool: ...

    async def capture(self, url: str, *, task_id: str = "") -> CapturedDocument: ...


class SourceDocumentError(RuntimeError):
    """来源文档读取失败。"""


class SourceDocumentBlocked(SourceDocumentError):
    """目标站点要求人工验证，调用侧可回退到原采集方式。"""
