"""阿里云 OSS Python SDK V2 适配器。"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from api.storage.types import ObjectHead, PutResult, ReadAccess


def _content_disposition(filename: str) -> str:
    if not filename:
        return ""
    fallback = "download" + Path(filename).suffix
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


class AliyunOSSProvider:
    name = "aliyun_oss"

    def __init__(self, config: dict[str, object]) -> None:
        try:
            import alibabacloud_oss_v2 as oss
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("未安装 alibabacloud-oss-v2") from exc

        self._oss = oss
        self.bucket = str(config.get("bucket") or "").strip()
        self.region = str(config.get("region") or "").strip()
        self.endpoint = str(config.get("endpoint") or "").strip()
        self.public_endpoint = str(
            config.get("public_endpoint") or f"https://oss-{self.region}.aliyuncs.com"
        ).strip()
        self.access_key_id = str(config.get("access_key_id") or "").strip()
        self.access_key_secret = str(config.get("access_key_secret") or "").strip()
        self.security_token = str(config.get("security_token") or "").strip() or None
        self.server_side_encryption = str(config.get("server_side_encryption") or "AES256").strip()
        self.connect_timeout = max(1, min(int(config.get("connect_timeout") or 5), 60))
        self.readwrite_timeout = max(5, min(int(config.get("readwrite_timeout") or 60), 600))
        self.retry_max_attempts = max(0, min(int(config.get("retry_max_attempts") or 3), 10))
        if not all((self.bucket, self.region, self.endpoint, self.access_key_id, self.access_key_secret)):
            raise ValueError("OSS 配置缺少 bucket/region/endpoint/access_key_id/access_key_secret")
        self._client = self._new_client(self.endpoint)
        self._public_client = self._new_client(self.public_endpoint)

    def _new_client(self, endpoint: str):
        oss = self._oss
        cfg = oss.config.load_default()
        cfg.credentials_provider = oss.credentials.StaticCredentialsProvider(
            self.access_key_id,
            self.access_key_secret,
            self.security_token,
        )
        cfg.region = self.region
        cfg.endpoint = endpoint
        cfg.connect_timeout = self.connect_timeout
        cfg.readwrite_timeout = self.readwrite_timeout
        cfg.retry_max_attempts = self.retry_max_attempts
        return oss.Client(cfg)

    def _safe_error(self, exc: Exception) -> str:
        return str(exc).replace(self.access_key_id, "***")[:1000]

    def _request_metadata(self, metadata: dict[str, str] | None) -> dict[str, str]:
        return {str(key): str(value)[:512] for key, value in (metadata or {}).items()}

    def _put_request(
        self,
        key: str,
        *,
        body=None,
        content_type: str,
        filename: str,
        metadata: dict[str, str] | None,
    ):
        return self._oss.PutObjectRequest(
            bucket=self.bucket,
            key=key,
            body=body,
            content_type=content_type,
            content_disposition=_content_disposition(filename) or None,
            cache_control="private, max-age=300",
            metadata=self._request_metadata(metadata),
            server_side_encryption=self.server_side_encryption or None,
            forbid_overwrite=True,
        )

    async def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        filename: str = "",
        metadata: dict[str, str] | None = None,
    ) -> PutResult:
        request = self._put_request(
            key,
            body=data,
            content_type=content_type,
            filename=filename,
            metadata=metadata,
        )
        result = await asyncio.to_thread(self._client.put_object, request)
        return PutResult(
            etag=str(result.etag or "").strip('"'),
            version_id=str(result.version_id or ""),
            crc64=str(result.hash_crc64 or ""),
        )

    async def put_file(
        self,
        key: str,
        path: Path,
        *,
        content_type: str,
        filename: str = "",
        metadata: dict[str, str] | None = None,
    ) -> PutResult:
        request = self._put_request(
            key,
            content_type=content_type,
            filename=filename,
            metadata=metadata,
        )
        result = await asyncio.to_thread(self._client.put_object_from_file, request, str(path))
        return PutResult(
            etag=str(result.etag or "").strip('"'),
            version_id=str(result.version_id or ""),
            crc64=str(result.hash_crc64 or ""),
        )

    async def head(self, key: str) -> ObjectHead:
        result = await asyncio.to_thread(
            self._client.head_object,
            self._oss.HeadObjectRequest(bucket=self.bucket, key=key),
        )
        return ObjectHead(
            size=int(result.content_length or 0),
            content_type=str(result.content_type or "application/octet-stream"),
            etag=str(result.etag or "").strip('"'),
            metadata={str(k): str(v) for k, v in (result.metadata or {}).items()},
        )

    async def get_bytes(self, key: str) -> bytes:
        result = await asyncio.to_thread(
            self._client.get_object,
            self._oss.GetObjectRequest(bucket=self.bucket, key=key),
        )
        if result.body is None:
            return b""
        try:
            return await asyncio.to_thread(result.body.read)
        finally:
            await asyncio.to_thread(result.body.close)

    async def iter_bytes(
        self,
        key: str,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> AsyncIterator[bytes]:
        result = await asyncio.to_thread(
            self._client.get_object,
            self._oss.GetObjectRequest(bucket=self.bucket, key=key),
        )
        if result.body is None:
            return
        iterator = result.body.iter_bytes(chunk_size=max(64 * 1024, chunk_size))
        try:
            while chunk := await asyncio.to_thread(next, iterator, None):
                yield chunk
        finally:
            await asyncio.to_thread(result.body.close)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(
            self._client.delete_object,
            self._oss.DeleteObjectRequest(bucket=self.bucket, key=key),
        )

    async def read_access(
        self,
        key: str,
        *,
        expires_seconds: int,
        filename: str = "",
        content_type: str = "",
    ) -> ReadAccess:
        request = self._oss.GetObjectRequest(
            bucket=self.bucket,
            key=key,
            response_content_disposition=_content_disposition(filename) or None,
        )
        result = await asyncio.to_thread(
            self._public_client.presign,
            request,
            expires=timedelta(seconds=max(30, min(expires_seconds, 3600))),
        )
        return ReadAccess(mode="redirect", url=str(result.url))

    async def healthcheck(self) -> dict[str, object]:
        probe = f"sere1nfish/prod/_healthcheck/storage-probe-{uuid.uuid4().hex}.txt"
        uploaded = False
        try:
            await self.put_bytes(
                probe,
                b"ok",
                content_type="text/plain",
                metadata={"probe": "true"},
            )
            uploaded = True
            head = await self.head(probe)
            body = await self.get_bytes(probe)
            return {
                "ok": head.size == 2 and body == b"ok",
                "provider": self.name,
                "bucket": self.bucket,
                "region": self.region,
            }
        except Exception as exc:
            return {
                "ok": False,
                "provider": self.name,
                "bucket": self.bucket,
                "region": self.region,
                "error": self._safe_error(exc),
            }
        finally:
            if uploaded:
                try:
                    await self.delete(probe)
                except Exception:
                    pass
