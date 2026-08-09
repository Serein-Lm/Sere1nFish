"""社交地点图片采集 API：计划、排队、状态和媒体证据。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from api.auth import User, get_current_active_user
from api.dao import social_collection as social_dao
from api.db.mongodb import get_db
from api.models.social_collection import (
    SocialCollectionPreviewRequest,
    SocialCollectionRequest,
)
from api.services.social_collection import (
    compile_social_collection_plan,
    create_social_collection_job,
)
from api.services.social_collection.platforms import SocialPlatformRegistry


router = APIRouter(dependencies=[Depends(get_current_active_user)])


@router.get("/platforms")
async def list_platforms() -> dict:
    return {"items": SocialPlatformRegistry.catalog()}


@router.post("/jobs/preview")
async def preview_job(request: SocialCollectionPreviewRequest) -> dict:
    """Compile the complete plan without touching Mongo task state or a phone."""
    return compile_social_collection_plan(
        SocialCollectionRequest(**request.model_dump())
    )


@router.post("/jobs")
async def create_job(
    request: SocialCollectionRequest,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    try:
        return await create_social_collection_job(
            get_db(),
            request,
            requested_by=current_user.username,
            start=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/jobs")
async def list_jobs(
    project_id: str = "",
    status: str = "",
    platform: str = "",
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    items, total = await social_dao.list_jobs(
        get_db(),
        project_id=project_id,
        status=status,
        platform=platform,
        skip=skip,
        limit=limit,
    )
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = await social_dao.get_job(get_db(), job_id)
    if not job:
        raise HTTPException(status_code=404, detail="社交采集 Job 不存在")
    return job


@router.get("/jobs/{job_id}/media")
async def list_job_media(
    job_id: str,
    platform: str = "",
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    if not await social_dao.get_job(get_db(), job_id):
        raise HTTPException(status_code=404, detail="社交采集 Job 不存在")
    items, total = await social_dao.list_media_evidence(
        get_db(),
        job_id=job_id,
        platform=platform,
        skip=skip,
        limit=limit,
    )
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/media")
async def list_media(
    project_id: str = "",
    platform: str = "",
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    items, total = await social_dao.list_media_evidence(
        get_db(),
        project_id=project_id,
        platform=platform,
        skip=skip,
        limit=limit,
    )
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/media/{evidence_id}")
async def get_media(evidence_id: str) -> dict:
    item = await social_dao.get_media_evidence(get_db(), evidence_id)
    if not item:
        raise HTTPException(status_code=404, detail="媒体证据不存在")
    return item


@router.get("/media/{evidence_id}/image")
async def read_media_image(evidence_id: str) -> Response:
    item = await social_dao.get_media_evidence(get_db(), evidence_id)
    if not item:
        raise HTTPException(status_code=404, detail="媒体证据不存在")
    object_id = str(item.get("storage_object_id") or "")
    if not object_id:
        raise HTTPException(status_code=404, detail="媒体对象尚未归档")
    try:
        from api.storage import get_object_storage

        content = await (await get_object_storage()).get_bytes(object_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="媒体对象不存在") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="媒体对象暂时不可读取") from exc
    return Response(
        content=content,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=60",
            "X-Content-Type-Options": "nosniff",
        },
    )
