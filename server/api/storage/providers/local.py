"""本地存储适配器，仅用于测试与迁移期兼容。"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

from api.storage.types import ObjectHead, PutResult, ReadAccess


class LocalStorageProvider:
    name = "local"
    bucket = "local"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("对象路径越界") from exc
        return path

    async def put_bytes(self, key: str, data: bytes, **_: object) -> PutResult:
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)
        return PutResult(etag=hashlib.md5(data, usedforsecurity=False).hexdigest())

    async def put_file(self, key: str, path: Path, **_: object) -> PutResult:
        target = self._path(key)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, path, target)
        data = await asyncio.to_thread(target.read_bytes)
        return PutResult(etag=hashlib.md5(data, usedforsecurity=False).hexdigest())

    async def head(self, key: str) -> ObjectHead:
        path = self._path(key)
        stat = await asyncio.to_thread(path.stat)
        return ObjectHead(
            size=stat.st_size,
            content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        )

    async def get_bytes(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def iter_bytes(
        self,
        key: str,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> AsyncIterator[bytes]:
        path = self._path(key)
        stream = await asyncio.to_thread(path.open, "rb")
        try:
            while chunk := await asyncio.to_thread(stream.read, max(64 * 1024, chunk_size)):
                yield chunk
        finally:
            await asyncio.to_thread(stream.close)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)

    async def read_access(self, key: str, **_: object) -> ReadAccess:
        return ReadAccess(mode="local", path=self._path(key))

    async def healthcheck(self) -> dict[str, object]:
        probe = "_healthcheck/local-probe.txt"
        await self.put_bytes(probe, b"ok")
        head = await self.head(probe)
        await self.delete(probe)
        return {"ok": head.size == 2, "provider": self.name, "root": quote(str(self.root))}
