"""Authenticated Deepfake REST and realtime proxy API."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket
from fastapi.responses import Response
from starlette.websockets import WebSocketDisconnect

from api.auth import User, get_current_active_user
from api.services.deepfake import get_deepfake_service
from api.services.deepfake.adapters import DeepfakeProviderError
from api.services.deepfake.contracts import SourceImage
from api.services.deepfake.service import DeepfakeConfigurationError
from api.services.media_output import MediaOutputError, get_media_output_service
from api.services.websocket_auth import (
    PUBLIC_SUBPROTOCOL,
    authenticated_websocket_username,
)

router = APIRouter()


def _raise_service_error(exc: Exception) -> None:
    if isinstance(exc, DeepfakeProviderError):
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if isinstance(exc, DeepfakeConfigurationError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _read_sources(
    files: list[UploadFile],
    *,
    max_bytes: int,
    max_count: int,
) -> list[SourceImage]:
    if not 1 <= len(files) <= max_count:
        raise ValueError(f"source image count must be between 1 and {max_count}")
    return [
        SourceImage(
            content=await upload.read(max_bytes + 1),
            filename=upload.filename or f"source-{index}.jpg",
        )
        for index, upload in enumerate(files, start=1)
    ]


@router.get("/status")
async def deepfake_status(_: User = Depends(get_current_active_user)):
    try:
        return await (await get_deepfake_service()).status()
    except Exception as exc:
        _raise_service_error(exc)


@router.post("/swap/image")
async def swap_image(
    source: Annotated[list[UploadFile], File(...)],
    target: Annotated[UploadFile, File(...)],
    authorized_use: Annotated[bool, Form(...)],
    max_width: Annotated[int, Form()] = 1280,
    profile: Annotated[str, Form()] = "quality",
    _: User = Depends(get_current_active_user),
):
    if not authorized_use:
        raise HTTPException(status_code=403, detail="必须确认素材已获得授权")
    try:
        service = await get_deepfake_service()
        sources = await _read_sources(
            source,
            max_bytes=service.config.max_image_bytes,
            max_count=service.config.max_source_images,
        )
        target_data = await target.read(service.config.max_image_bytes + 1)
        result = await service.swap_image(
            sources=sources,
            target=target_data,
            target_name=target.filename or "target.jpg",
            max_width=max_width,
            profile=profile,
        )
        return Response(
            content=result.content,
            media_type=result.content_type,
            headers={
                "Cache-Control": "private, no-store",
                "X-Inference-Ms": f"{result.inference_ms:.2f}",
                "X-Quality-Profile": result.quality_profile,
                "X-Source-Count": str(result.source_count),
                "X-Source-Consistency": f"{result.source_consistency:.4f}",
                "X-Max-Width": str(result.effective_max_width or max_width),
                "X-Synthetic-Media": "true",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        _raise_service_error(exc)


@router.post("/sessions")
async def create_session(
    source: Annotated[list[UploadFile], File(...)],
    authorized_use: Annotated[bool, Form(...)],
    max_width: Annotated[int | None, Form()] = None,
    profile: Annotated[str, Form()] = "fast",
    user: User = Depends(get_current_active_user),
):
    if not authorized_use:
        raise HTTPException(status_code=403, detail="必须确认素材已获得授权")
    try:
        service = await get_deepfake_service()
        sources = await _read_sources(
            source,
            max_bytes=service.config.max_image_bytes,
            max_count=service.config.max_source_images,
        )
        return await service.create_session(
            username=user.username,
            sources=sources,
            max_width=max_width,
            profile=profile,
        )
    except HTTPException:
        raise
    except Exception as exc:
        _raise_service_error(exc)


@router.get("/sessions/{session_id}")
async def session_status(session_id: str, user: User = Depends(get_current_active_user)):
    try:
        return await (await get_deepfake_service()).session_status(session_id, user.username)
    except Exception as exc:
        _raise_service_error(exc)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user: User = Depends(get_current_active_user)):
    try:
        return await (await get_deepfake_service()).delete_session(session_id, user.username)
    except Exception as exc:
        _raise_service_error(exc)


@router.websocket("/sessions/{session_id}/stream")
async def stream_session(
    websocket: WebSocket,
    session_id: str,
    output_session_id: str = "",
) -> None:
    username = await authenticated_websocket_username(websocket)
    if not username:
        await websocket.close(code=4401)
        return
    try:
        service = await get_deepfake_service()
        stream_context = await service.open_stream(session_id, username)
        if output_session_id:
            await get_media_output_service().require_owner(
                output_session_id,
                username,
            )
    except Exception:
        await websocket.close(code=4404)
        return
    await websocket.accept(subprotocol=PUBLIC_SUBPROTOCOL)

    async def send_remote_result(result: bytes | str) -> None:
        nonlocal output_session_id
        if isinstance(result, bytes):
            if output_session_id:
                try:
                    await get_media_output_service().publish_video(
                        output_session_id,
                        username,
                        result,
                    )
                except MediaOutputError:
                    output_session_id = ""
            await websocket.send_bytes(result)
            return
        try:
            json.loads(result)
        except (TypeError, ValueError):
            result = json.dumps(
                {"type": "error", "message": "GPU returned invalid stream metadata"}
            )
        await websocket.send_text(result)

    try:
        async with stream_context as remote:
            ready = await remote.recv()
            await send_remote_result(ready)
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                payload: bytes | str | None = message.get("bytes")
                if payload is None:
                    payload = message.get("text")
                if payload is None:
                    continue
                await remote.send(payload)
                result = await remote.recv()
                await send_remote_result(result)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": "GPU stream disconnected"}))
        except Exception:
            pass
