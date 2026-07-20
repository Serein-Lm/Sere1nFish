"""对象存储稳定类型与 Provider 协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Protocol


@dataclass(frozen=True)
class PutResult:
    etag: str = ""
    version_id: str = ""
    crc64: str = ""


@dataclass(frozen=True)
class ObjectHead:
    size: int
    content_type: str = "application/octet-stream"
    etag: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReadAccess:
    mode: str
    url: str = ""
    path: Path | None = None


class StorageProvider(Protocol):
    name: str
    bucket: str

    async def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        filename: str = "",
        metadata: dict[str, str] | None = None,
    ) -> PutResult: ...

    async def put_file(
        self,
        key: str,
        path: Path,
        *,
        content_type: str,
        filename: str = "",
        metadata: dict[str, str] | None = None,
    ) -> PutResult: ...

    async def head(self, key: str) -> ObjectHead: ...

    async def get_bytes(self, key: str) -> bytes: ...

    def iter_bytes(self, key: str, *, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]: ...

    async def delete(self, key: str) -> None: ...

    async def read_access(
        self,
        key: str,
        *,
        expires_seconds: int,
        filename: str = "",
        content_type: str = "",
        inline: bool = False,
    ) -> ReadAccess: ...

    async def healthcheck(self) -> dict[str, Any]: ...
