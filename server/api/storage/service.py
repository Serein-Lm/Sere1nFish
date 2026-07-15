"""统一对象存储领域服务。"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Awaitable, Callable

from api.dao import storage_objects as storage_dao
from api.db.mongodb import get_db
from api.services.runtime_config import get_runtime_config_section
from api.storage.factory import create_storage_provider
from api.storage.keys import build_object_key
from api.storage.types import ObjectHead, PutResult, ReadAccess, StorageProvider


class ObjectStorageService:
    def __init__(self, config: dict[str, Any], provider: StorageProvider) -> None:
        self.config = config
        self.provider = provider
        self.prefix = str(config.get("prefix") or "sere1nfish/prod")
        self.presign_ttl = max(30, min(int(config.get("presign_ttl") or 300), 3600))

    async def store_bytes(
        self,
        data: bytes,
        *,
        kind: str,
        filename: str,
        object_id: str = "",
        content_type: str = "",
        owner: str = "",
        project_id: str = "",
        conversation_id: str = "",
        subject_id: str = "",
        source: str = "",
        source_id: str = "",
        relative_path: str = "",
        legacy_path: str = "",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sha256 = hashlib.sha256(data).hexdigest()
        return await self._store(
            size=len(data),
            sha256=sha256,
            uploader=lambda key, media_type, upload_name, metadata: self.provider.put_bytes(
                key,
                data,
                content_type=media_type,
                filename=upload_name,
                metadata=metadata,
            ),
            kind=kind,
            filename=filename,
            object_id=object_id,
            content_type=content_type,
            owner=owner,
            project_id=project_id,
            conversation_id=conversation_id,
            subject_id=subject_id,
            source=source,
            source_id=source_id,
            relative_path=relative_path,
            legacy_path=legacy_path,
            meta=meta,
        )

    async def store_file(self, path: Path, **kwargs: Any) -> dict[str, Any]:
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        size, sha256 = await asyncio.to_thread(self._file_digest, path)
        filename = str(kwargs.pop("filename", "") or path.name)
        content_type = str(
            kwargs.pop("content_type", "")
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )
        kwargs.setdefault("legacy_path", str(path))
        return await self._store(
            size=size,
            sha256=sha256,
            uploader=lambda key, media_type, upload_name, metadata: self.provider.put_file(
                key,
                path,
                content_type=media_type,
                filename=upload_name,
                metadata=metadata,
            ),
            filename=filename,
            content_type=content_type,
            **kwargs,
        )

    @staticmethod
    def _file_digest(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()

    async def _store(
        self,
        *,
        size: int,
        sha256: str,
        uploader: Callable[[str, str, str, dict[str, str]], Awaitable[PutResult]],
        kind: str,
        filename: str,
        object_id: str = "",
        content_type: str = "",
        owner: str = "",
        project_id: str = "",
        conversation_id: str = "",
        subject_id: str = "",
        source: str = "",
        source_id: str = "",
        relative_path: str = "",
        legacy_path: str = "",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        object_id = object_id or "obj_" + uuid.uuid4().hex
        extension = Path(filename).suffix.lstrip(".") or "bin"
        content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        key = build_object_key(
            prefix=self.prefix,
            kind=kind,
            object_id=object_id,
            extension=extension,
            project_id=project_id,
            owner=owner,
            conversation_id=conversation_id,
            subject_id=subject_id,
            relative_path=relative_path,
        )
        db = get_db()
        existing = await storage_dao.get_object(db, object_id)
        if (
            existing
            and existing.get("status") == "ready"
            and existing.get("provider") == self.provider.name
            and existing.get("bucket") == self.provider.bucket
        ):
            if existing.get("sha256") != sha256 or int(existing.get("size") or 0) != size:
                raise ValueError(f"object_id={object_id} 已存在且内容哈希不同")
            return existing
        relocating = bool(existing and (
            existing.get("provider") != self.provider.name
            or existing.get("bucket") != self.provider.bucket
            or existing.get("object_key") != key
        ))
        previous = dict(existing) if relocating and existing else None
        if relocating and existing:
            if existing.get("sha256") and existing.get("sha256") != sha256:
                raise ValueError(f"object_id={object_id} 迁移内容哈希不同")
            await storage_dao.prepare_relocation(
                db,
                object_id,
                provider=self.provider.name,
                bucket=self.provider.bucket,
                object_key=key,
            )

        await storage_dao.create_pending(
            db,
            object_id=object_id,
            provider=self.provider.name,
            bucket=self.provider.bucket,
            object_key=key,
            kind=kind,
            filename=filename,
            content_type=content_type,
            size=size,
            sha256=sha256,
            owner=owner,
            project_id=project_id,
            conversation_id=conversation_id,
            subject_id=subject_id,
            source=source,
            source_id=source_id,
            legacy_path=legacy_path,
            meta={**(meta or {}), **({"relative_path": relative_path} if relative_path else {})},
        )
        try:
            remote = await self.provider.head(key)
            remote_sha = remote.metadata.get("sha256") or remote.metadata.get("x-oss-meta-sha256")
            if remote.size == size and remote_sha == sha256:
                ready = await storage_dao.mark_ready(db, object_id, etag=remote.etag)
                return ready or {}
        except Exception:
            pass
        try:
            result = await uploader(
                key,
                content_type,
                filename,
                {"sha256": sha256, "object-id": object_id, "kind": kind},
            )
            head = await self.provider.head(key)
            if head.size != size:
                raise RuntimeError(f"上传后大小校验失败: local={size}, remote={head.size}")
            ready = await storage_dao.mark_ready(
                db,
                object_id,
                etag=result.etag or head.etag,
                version_id=result.version_id,
                crc64=result.crc64,
            )
            return ready or await storage_dao.get_object(db, object_id) or {}
        except Exception as exc:
            try:
                remote = await self.provider.head(key)
                remote_sha = remote.metadata.get("sha256") or remote.metadata.get("x-oss-meta-sha256")
                if remote.size == size and remote_sha == sha256:
                    ready = await storage_dao.mark_ready(db, object_id, etag=remote.etag)
                    return ready or {}
            except Exception:
                pass
            if previous:
                await storage_dao.restore_relocation(db, object_id, previous, error=str(exc))
            else:
                await storage_dao.mark_error(db, object_id, str(exc))
            raise

    async def get(self, object_id: str) -> dict[str, Any] | None:
        return await storage_dao.get_object(get_db(), object_id)

    async def head(self, object_id: str) -> ObjectHead:
        doc, provider = await self._ready_provider(object_id)
        return await provider.head(str(doc["object_key"]))

    async def get_bytes(self, object_id: str) -> bytes:
        doc, provider = await self._ready_provider(object_id)
        return await provider.get_bytes(str(doc["object_key"]))

    async def iter_bytes(
        self,
        object_id: str,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> AsyncIterator[bytes]:
        doc, provider = await self._ready_provider(object_id)
        async for chunk in provider.iter_bytes(
            str(doc["object_key"]),
            chunk_size=chunk_size,
        ):
            yield chunk

    async def read_access(
        self,
        object_id: str,
        *,
        filename: str = "",
        content_type: str = "",
        expires_seconds: int | None = None,
    ) -> ReadAccess:
        doc, provider = await self._ready_provider(object_id)
        return await provider.read_access(
            str(doc["object_key"]),
            expires_seconds=expires_seconds or self.presign_ttl,
            filename=filename or str(doc.get("filename") or ""),
            content_type=content_type or str(doc.get("content_type") or ""),
        )

    async def delete(self, object_id: str) -> None:
        doc, provider = await self._ready_provider(object_id)
        await provider.delete(str(doc["object_key"]))
        await storage_dao.mark_deleted(get_db(), object_id)

    async def healthcheck(self) -> dict[str, Any]:
        return await self.provider.healthcheck()

    async def _ready_provider(self, object_id: str) -> tuple[dict[str, Any], StorageProvider]:
        doc = await storage_dao.get_object(get_db(), object_id)
        if not doc or doc.get("status") != "ready":
            raise FileNotFoundError(f"对象不存在或尚未就绪: {object_id}")
        provider_name = str(doc.get("provider") or "")
        bucket = str(doc.get("bucket") or "")
        if provider_name == self.provider.name and bucket == self.provider.bucket:
            return doc, self.provider
        provider = create_storage_provider({**self.config, "provider": provider_name, "enabled": True})
        if provider.name != provider_name or provider.bucket != bucket:
            raise RuntimeError("对象 Provider 元数据与当前可用配置不一致")
        return doc, provider


async def get_object_storage(*, force_configured_provider: bool = False) -> ObjectStorageService:
    config = await get_runtime_config_section("object_storage")
    if force_configured_provider and config.get("provider") == "aliyun_oss":
        config = {**config, "enabled": True}
    provider = create_storage_provider(config)
    return ObjectStorageService(config, provider)
