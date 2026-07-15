"""统一对象存储元数据、读取和健康检查 API。"""

from __future__ import annotations

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
async def read_storage_object(object_id: str):
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
        if content_type.startswith("image/"):
            try:
                return Response(
                    content=await service.get_bytes(object_id),
                    media_type=content_type,
                    headers={"Cache-Control": "private, max-age=60"},
                )
            except Exception as exc:
                raise HTTPException(status_code=503, detail="存储对象暂时不可读取") from exc
        return RedirectResponse(access.url, status_code=307, headers={"Cache-Control": "private, no-store"})
    if access.path and access.path.is_file():
        return FileResponse(access.path)
    raise HTTPException(status_code=404, detail="存储对象不存在")


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
