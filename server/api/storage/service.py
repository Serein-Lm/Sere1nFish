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
from api.storage.errors import (
    is_storage_configuration_error,
    storage_error_details,
)
from api.storage.factory import create_storage_provider
from api.storage.keys import build_object_key
from api.storage.types import ObjectHead, PutResult, ReadAccess, StorageProvider
from core.logger import get_logger


logger = get_logger("object_storage")

StorageUploader = Callable[
    [StorageProvider, str, str, str, dict[str, str]],
    Awaitable[PutResult],
]

# A durable provider configuration failure should not add one failed OSS request
# to every captured image. The fingerprint changes when credentials or endpoint
# configuration changes, so replacing the key automatically closes the circuit.
_PROVIDER_CONFIGURATION_FAILURES: dict[str, str] = {}
_MAX_CONFIGURATION_FAILURES = 64


def _provider_config_fingerprint(config: dict[str, Any]) -> str:
    values = (
        config.get("provider"),
        config.get("bucket"),
        config.get("region"),
        config.get("endpoint"),
        config.get("access_key_id"),
        config.get("access_key_secret"),
        config.get("security_token"),
    )
    encoded = "\0".join(str(value or "") for value in values).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ObjectStorageService:
    def __init__(
        self,
        config: dict[str, Any],
        provider: StorageProvider,
        *,
        fallback_provider: StorageProvider | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.fallback_provider = (
            fallback_provider
            if fallback_provider
            and (fallback_provider.name, fallback_provider.bucket)
            != (provider.name, provider.bucket)
            else None
        )
        self.prefix = str(config.get("prefix") or "sere1nfish/prod")
        self.presign_ttl = max(30, min(int(config.get("presign_ttl") or 300), 3600))
        self._provider_config_fingerprint = _provider_config_fingerprint(config)

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
            uploader=lambda provider, key, media_type, upload_name, metadata: provider.put_bytes(
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
            uploader=lambda provider, key, media_type, upload_name, metadata: provider.put_file(
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
        uploader: StorageUploader,
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
        cached_fallback_reason = _PROVIDER_CONFIGURATION_FAILURES.get(
            self._provider_config_fingerprint,
            "",
        )
        write_provider = (
            self.fallback_provider
            if self.fallback_provider and cached_fallback_reason
            else self.provider
        )
        if existing and existing.get("sha256") and existing.get("sha256") != sha256:
            raise ValueError(f"object_id={object_id} 已存在且内容哈希不同")

        ready_providers = [write_provider]
        if self.fallback_provider and self.fallback_provider is not write_provider:
            ready_providers.append(self.fallback_provider)
        for ready_provider in ready_providers:
            if not self._object_uses_provider(existing, ready_provider):
                continue
            if existing.get("sha256") != sha256 or int(existing.get("size") or 0) != size:
                raise ValueError(f"object_id={object_id} 已存在且内容哈希不同")
            return existing

        relocating = bool(existing and (
            existing.get("provider") != write_provider.name
            or existing.get("bucket") != write_provider.bucket
            or existing.get("object_key") != key
        ))
        previous = dict(existing) if relocating and existing else None
        if relocating and existing and write_provider is self.provider:
            await storage_dao.prepare_relocation(
                db,
                object_id,
                provider=write_provider.name,
                bucket=write_provider.bucket,
                object_key=key,
            )

        storage_meta = {
            **(meta or {}),
            **({"relative_path": relative_path} if relative_path else {}),
        }
        await storage_dao.create_pending(
            db,
            object_id=object_id,
            provider=write_provider.name,
            bucket=write_provider.bucket,
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
            meta=storage_meta,
        )
        if write_provider is self.fallback_provider:
            await storage_dao.activate_fallback(
                db,
                object_id,
                provider=write_provider.name,
                bucket=write_provider.bucket,
                object_key=key,
                preferred_provider=self.provider.name,
                preferred_bucket=self.provider.bucket,
                reason=cached_fallback_reason,
            )

        try:
            result = await self._put_verified(
                write_provider,
                uploader,
                key,
                size=size,
                sha256=sha256,
                content_type=content_type,
                filename=filename,
                metadata={"sha256": sha256, "object-id": object_id, "kind": kind},
            )
            ready = await storage_dao.mark_ready(
                db,
                object_id,
                etag=result.etag,
                version_id=result.version_id,
                crc64=result.crc64,
            )
            return ready or await storage_dao.get_object(db, object_id) or {}
        except Exception as exc:
            if (
                write_provider is self.provider
                and self.fallback_provider
                and is_storage_configuration_error(exc)
            ):
                return await self._store_with_fallback(
                    db=db,
                    object_id=object_id,
                    key=key,
                    size=size,
                    sha256=sha256,
                    uploader=uploader,
                    content_type=content_type,
                    filename=filename,
                    metadata={"sha256": sha256, "object-id": object_id, "kind": kind},
                    previous=previous,
                    primary_error=exc,
                )
            if previous:
                await storage_dao.restore_relocation(db, object_id, previous, error=str(exc))
            else:
                error = (
                    storage_error_details(exc).safe_message()
                    if is_storage_configuration_error(exc)
                    else str(exc)
                )
                await storage_dao.mark_error(db, object_id, error)
            raise

    @staticmethod
    def _object_uses_provider(
        doc: dict[str, Any] | None,
        provider: StorageProvider,
    ) -> bool:
        return bool(
            doc
            and doc.get("status") == "ready"
            and doc.get("provider") == provider.name
            and doc.get("bucket") == provider.bucket
        )

    @staticmethod
    def _head_matches(
        provider: StorageProvider,
        head: ObjectHead,
        *,
        size: int,
        sha256: str,
    ) -> bool:
        if head.size != size:
            return False
        if provider.name == "local":
            return True
        remote_sha = head.metadata.get("sha256") or head.metadata.get("x-oss-meta-sha256")
        return remote_sha == sha256

    async def _put_verified(
        self,
        provider: StorageProvider,
        uploader: StorageUploader,
        key: str,
        *,
        size: int,
        sha256: str,
        content_type: str,
        filename: str,
        metadata: dict[str, str],
    ) -> PutResult:
        try:
            remote = await provider.head(key)
            if self._head_matches(provider, remote, size=size, sha256=sha256):
                return PutResult(etag=remote.etag)
        except Exception as exc:
            if is_storage_configuration_error(exc):
                raise

        try:
            result = await uploader(
                provider,
                key,
                content_type,
                filename,
                metadata,
            )
        except Exception as exc:
            if is_storage_configuration_error(exc):
                raise
            try:
                remote = await provider.head(key)
                if self._head_matches(provider, remote, size=size, sha256=sha256):
                    return PutResult(etag=remote.etag)
            except Exception:
                pass
            raise

        head = await provider.head(key)
        if head.size != size:
            raise RuntimeError(f"上传后大小校验失败: local={size}, remote={head.size}")
        return PutResult(
            etag=result.etag or head.etag,
            version_id=result.version_id,
            crc64=result.crc64,
        )

    async def _store_with_fallback(
        self,
        *,
        db: Any,
        object_id: str,
        key: str,
        size: int,
        sha256: str,
        uploader: StorageUploader,
        content_type: str,
        filename: str,
        metadata: dict[str, str],
        previous: dict[str, Any] | None,
        primary_error: BaseException,
    ) -> dict[str, Any]:
        fallback = self.fallback_provider
        if fallback is None:  # pragma: no cover - guarded by caller
            raise primary_error

        details = storage_error_details(primary_error)
        safe_error = details.safe_message()
        if len(_PROVIDER_CONFIGURATION_FAILURES) >= _MAX_CONFIGURATION_FAILURES:
            _PROVIDER_CONFIGURATION_FAILURES.pop(next(iter(_PROVIDER_CONFIGURATION_FAILURES)))
        _PROVIDER_CONFIGURATION_FAILURES[self._provider_config_fingerprint] = safe_error
        logger.warning(
            "对象存储主 Provider 不可用，写入已降级到本地: provider=%s bucket=%s %s",
            self.provider.name,
            self.provider.bucket,
            safe_error,
        )

        await storage_dao.activate_fallback(
            db,
            object_id,
            provider=fallback.name,
            bucket=fallback.bucket,
            object_key=key,
            preferred_provider=self.provider.name,
            preferred_bucket=self.provider.bucket,
            reason=safe_error,
        )
        try:
            result = await self._put_verified(
                fallback,
                uploader,
                key,
                size=size,
                sha256=sha256,
                content_type=content_type,
                filename=filename,
                metadata=metadata,
            )
            ready = await storage_dao.mark_ready(
                db,
                object_id,
                etag=result.etag,
                version_id=result.version_id,
                crc64=result.crc64,
            )
            return ready or await storage_dao.get_object(db, object_id) or {}
        except Exception as fallback_error:
            combined_error = f"primary={safe_error}; fallback={fallback_error}"
            if previous:
                await storage_dao.restore_relocation(
                    db,
                    object_id,
                    previous,
                    error=combined_error,
                )
            else:
                await storage_dao.mark_error(db, object_id, combined_error)
            raise fallback_error from primary_error

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
        inline: bool = False,
    ) -> ReadAccess:
        doc, provider = await self._ready_provider(object_id)
        return await provider.read_access(
            str(doc["object_key"]),
            expires_seconds=expires_seconds or self.presign_ttl,
            filename=filename or str(doc.get("filename") or ""),
            content_type=content_type or str(doc.get("content_type") or ""),
            inline=inline,
        )

    async def delete(self, object_id: str) -> None:
        doc, provider = await self._ready_provider(object_id)
        await provider.delete(str(doc["object_key"]))
        await storage_dao.mark_deleted(get_db(), object_id)

    async def healthcheck(self) -> dict[str, Any]:
        result = await self.provider.healthcheck()
        if result.get("ok"):
            _PROVIDER_CONFIGURATION_FAILURES.pop(
                self._provider_config_fingerprint,
                None,
            )
        return result

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
    fallback_provider = None
    if not force_configured_provider and provider.name != "local":
        fallback_provider = create_storage_provider(
            {**config, "provider": "local", "enabled": False}
        )
    return ObjectStorageService(
        config,
        provider,
        fallback_provider=fallback_provider,
    )
