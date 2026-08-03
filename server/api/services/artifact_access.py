"""Channel-safe access helpers for AI generated artifacts."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from motor.motor_asyncio import AsyncIOMotorDatabase


def _public_http_url(value: Any) -> str:
    url = str(value or "").strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else ""


async def attach_temporary_download_urls(
    db: AsyncIOMotorDatabase,
    artifacts: list[dict[str, Any]],
    *,
    owner: str,
    expires_seconds: int = 3600,
) -> list[dict[str, Any]]:
    """Attach short-lived private-storage URLs after verifying artifact ownership."""
    if not artifacts or not owner:
        return [dict(item) for item in artifacts]

    from api.dao import artifacts as artifacts_dao
    from api.storage import get_object_storage

    ttl = max(30, min(int(expires_seconds or 3600), 3600))
    try:
        storage = await get_object_storage()
    except Exception:
        return [dict(item) for item in artifacts]

    result: list[dict[str, Any]] = []
    for artifact in artifacts:
        enriched = dict(artifact)
        artifact_id = str(artifact.get("artifact_id") or "").strip()
        if not artifact_id:
            result.append(enriched)
            continue
        try:
            document = await artifacts_dao.get_artifact(db, artifact_id)
            document_owner = str((document or {}).get("owner") or "")
            object_id = str((document or {}).get("storage_object_id") or "")
            if not document or document_owner != owner or not object_id:
                result.append(enriched)
                continue
            access = await storage.read_access(
                object_id,
                filename=str(document.get("filename") or artifact_id),
                content_type=str(
                    document.get("content_type") or "application/octet-stream"
                ),
                expires_seconds=ttl,
            )
            temporary_url = (
                _public_http_url(access.url)
                if access.mode == "redirect"
                else ""
            )
            if temporary_url:
                enriched["temporary_download_url"] = temporary_url
                enriched["temporary_download_expires_seconds"] = ttl
        except Exception:
            # A temporary link is an optional channel convenience; the stable
            # authenticated artifact link remains available as fallback.
            pass
        result.append(enriched)
    return result
