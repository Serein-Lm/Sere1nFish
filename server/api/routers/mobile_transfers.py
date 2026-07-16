"""手机文件上传、下发和历史重试 API。"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from api.auth import User, get_current_active_user
from api.db.mongodb import get_db
from api.routers.mobile import ensure_device_access
from api.services.mobile_transfer import MobileTransferError, get_mobile_transfer_service

router = APIRouter(dependencies=[Depends(get_current_active_user)])


@router.post("/devices/{device_id}/transfers")
async def upload_mobile_transfer(
    device_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    file: Annotated[UploadFile, File(...)],
    category: Annotated[
        Literal["auto", "image", "audio", "attachment"], Form()
    ] = "auto",
) -> dict:
    await ensure_device_access(device_id, current_user)
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    try:
        return await get_mobile_transfer_service(get_db()).upload_and_push(
            device_id=device_id,
            owner=current_user.username,
            filename=file.filename,
            content_type=file.content_type or "",
            category=category,
            upload=file,
        )
    except MobileTransferError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    finally:
        await file.close()


@router.get("/devices/{device_id}/transfers")
async def list_mobile_transfers(
    device_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    await ensure_device_access(device_id, current_user)
    items = await get_mobile_transfer_service(get_db()).list_for_device(
        device_id=device_id,
        owner=current_user.username,
        is_admin=current_user.is_admin,
        limit=limit,
    )
    return {"items": items, "total": len(items)}


@router.post("/devices/{device_id}/transfers/{transfer_id}/retry")
async def retry_mobile_transfer(
    device_id: str,
    transfer_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    await ensure_device_access(device_id, current_user)
    try:
        return await get_mobile_transfer_service(get_db()).retry(
            transfer_id=transfer_id,
            device_id=device_id,
            owner=current_user.username,
            is_admin=current_user.is_admin,
        )
    except MobileTransferError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
