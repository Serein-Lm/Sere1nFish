"""统一对象存储元数据、读取和健康检查 API。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, Response

from api.auth import User, get_current_active_user, require_admin
from api.dao import storage_migrations as migration_dao
from api.dao import storage_objects as storage_dao
from api.db.mongodb import get_db
from api.storage import get_object_storage


router = APIRouter(dependencies=[Depends(get_current_active_user)])


def _public_meta(doc: dict) -> dict:
    return {
        key: value
        for key, value in doc.items()
        if key not in {"object_key", "legacy_path", "bucket", "etag", "version_id", "crc64"}
    }


@router.get("/objects")
async def list_storage_objects(
    kind: str = "",
    project_id: str = "",
    limit: int = Query(100, ge=1, le=1000),
    _: User = Depends(require_admin),
):
    items = await storage_dao.list_objects(
        get_db(),
        kind=kind,
        project_id=project_id,
        limit=limit,
    )
    return {"items": [_public_meta(item) for item in items], "total": len(items)}


@router.get("/objects/{object_id}")
async def get_storage_object(object_id: str, _: User = Depends(require_admin)):
    doc = await storage_dao.get_object(get_db(), object_id)
    if not doc:
        raise HTTPException(status_code=404, detail="存储对象不存在")
    return _public_meta(doc)


@router.get("/objects/{object_id}/content")
async def read_storage_object(
    object_id: str,
    proxy: bool = Query(default=False),
):
    doc = await storage_dao.get_object(get_db(), object_id)
    if not doc or doc.get("status") != "ready":
        raise HTTPException(status_code=404, detail="存储对象不存在")
    try:
        service = await get_object_storage()
        access = await service.read_access(object_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="存储对象不存在") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"存储对象暂时不可读取: {exc}") from exc
    if access.mode == "redirect":
        content_type = str(doc.get("content_type") or "application/octet-stream")
        if proxy or content_type.startswith("image/"):
            try:
                response_type = (
                    "text/plain; charset=utf-8"
                    if content_type.split(";", 1)[0].lower()
                    in {"text/html", "application/xhtml+xml"}
                    else content_type
                )
                return Response(
                    content=await service.get_bytes(object_id),
                    media_type=response_type,
                    headers={
                        "Cache-Control": "private, max-age=60",
                        "X-Content-Type-Options": "nosniff",
                    },
                )
            except Exception as exc:
                raise HTTPException(status_code=503, detail="存储对象暂时不可读取") from exc
        return RedirectResponse(access.url, status_code=307, headers={"Cache-Control": "private, no-store"})
    if access.path and access.path.is_file():
        return FileResponse(access.path)
    raise HTTPException(status_code=404, detail="存储对象不存在")


@router.get("/objects/{object_id}/access")
async def get_storage_object_access(object_id: str):
    """Issue short-lived private read access without proxying image bytes."""
    doc = await storage_dao.get_object(get_db(), object_id)
    if not doc or doc.get("status") != "ready":
        raise HTTPException(status_code=404, detail="存储对象不存在")
    content_type = str(doc.get("content_type") or "application/octet-stream")
    try:
        service = await get_object_storage()
        access = await service.read_access(
            object_id,
            content_type=content_type,
            inline=content_type.startswith("image/"),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="存储对象不存在") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="存储对象暂时不可读取") from exc

    expires_in = service.presign_ttl if access.mode == "redirect" else 300
    url = access.url if access.mode == "redirect" else (
        f"/api/v1/storage/objects/{object_id}/content"
    )
    return {
        "object_id": object_id,
        "mode": access.mode,
        "url": url,
        "content_type": content_type,
        "expires_in": expires_in,
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    }


@router.get("/status")
async def storage_status(_: User = Depends(require_admin)):
    db = get_db()
    service = await get_object_storage()
    return {
        "provider": service.config.get("provider") or service.provider.name,
        "active_provider": service.provider.name,
        "bucket": service.config.get("bucket") or service.provider.bucket,
        "enabled": bool(service.config.get("enabled", False)),
        "region": service.config.get("region") or "",
        "migration_state": service.config.get("migration_state") or "not_started",
        "stats": await storage_dao.get_stats(db),
        "latest_migration": await migration_dao.latest(db),
    }


@router.post("/test")
async def test_storage(_: User = Depends(require_admin)):
    service = await get_object_storage(force_configured_provider=True)
    result = await service.healthcheck()
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result)
    return result
