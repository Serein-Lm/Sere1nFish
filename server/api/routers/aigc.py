"""AIGC media APIs backed by Aliyun Bailian."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import User, get_current_active_user
from api.services.bailian_aigc import BailianAPIError, get_bailian_client

router = APIRouter(dependencies=[Depends(get_current_active_user)])


class QwenImageEditReq(BaseModel):
    images: list[str] = Field(..., min_length=1, max_length=3, description="Image URL or data URL list")
    prompt: str = Field(..., min_length=1, max_length=4000)
    model: str | None = None
    parameters: dict[str, Any] | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "images": ["https://example.com/input.png"],
                    "prompt": "把背景改成清晨的办公室，保留主体人物。",
                    "parameters": {"n": 1, "watermark": False, "prompt_extend": True},
                }
            ]
        }
    }


class WanxImageEditReq(BaseModel):
    base_image_url: str = Field(..., min_length=1, description="Base image URL or data URL")
    prompt: str = Field(..., min_length=1, max_length=800)
    function: str = Field("description_edit", description="Wanx image edit function")
    mask_image_url: str | None = None
    model: str | None = None
    parameters: dict[str, Any] | None = None
    extra_input: dict[str, Any] | None = None


class TextToVideoReq(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    negative_prompt: str | None = Field(None, max_length=1000)
    audio_url: str | None = None
    model: str | None = None
    parameters: dict[str, Any] | None = None


class ImageToVideoReq(BaseModel):
    img_url: str | None = Field(None, description="First-frame image URL or data URL")
    image_url: str | None = Field(None, description="Alias for img_url")
    last_frame_url: str | None = Field(None, description="Optional last-frame image URL for Wan 2.7")
    first_clip_url: str | None = Field(None, description="Optional first video clip URL for Wan 2.7 continuation")
    media: list[dict[str, str]] | None = Field(
        None,
        description="Raw Wan 2.7 media items, e.g. [{'type':'first_frame','url':'https://...'}]",
    )
    prompt: str | None = Field(None, max_length=1500)
    negative_prompt: str | None = Field(None, max_length=500)
    audio_url: str | None = None
    template: str | None = None
    model: str | None = None
    parameters: dict[str, Any] | None = None
    protocol: Literal["workspace", "legacy", "auto"] = "auto"


def _raise_bailian_error(exc: BailianAPIError) -> None:
    detail: dict[str, Any] = {"message": str(exc)}
    if exc.payload is not None:
        detail["payload"] = exc.payload
    raise HTTPException(status_code=exc.status_code, detail=detail)


@router.get("/config", summary="Bailian AIGC configuration status")
async def aigc_config(_: User = Depends(get_current_active_user)):
    try:
        client = await get_bailian_client()
    except BailianAPIError as exc:
        _raise_bailian_error(exc)
    cfg = client.config
    return {
        "provider": "aliyun_bailian",
        "configured": True,
        "region": cfg.region,
        "has_api_key": bool(cfg.api_key),
        "has_workspace_id": bool(cfg.workspace_id),
        "qwen_image_edit_model": cfg.qwen_image_edit_model,
        "wanx_image_edit_model": cfg.wanx_image_edit_model,
        "text_to_video_model": cfg.text_to_video_model,
        "image_to_video_model": cfg.image_to_video_model,
    }


@router.post("/images/qwen-edit", summary="Qwen Image Edit")
async def qwen_image_edit(body: QwenImageEditReq):
    try:
        client = await get_bailian_client()
        return await client.qwen_image_edit(
            images=body.images,
            prompt=body.prompt,
            model=body.model,
            parameters=body.parameters,
        )
    except BailianAPIError as exc:
        _raise_bailian_error(exc)


@router.post("/images/wanx-edit", summary="Wanx async image edit")
async def wanx_image_edit(body: WanxImageEditReq):
    try:
        client = await get_bailian_client()
        return await client.wanx_image_edit(
            base_image_url=body.base_image_url,
            prompt=body.prompt,
            function=body.function,
            mask_image_url=body.mask_image_url,
            model=body.model,
            parameters=body.parameters,
            extra_input=body.extra_input,
        )
    except BailianAPIError as exc:
        _raise_bailian_error(exc)


@router.post("/videos/text-to-video", summary="Wanx 2.7 async text-to-video")
async def text_to_video(body: TextToVideoReq):
    try:
        client = await get_bailian_client()
        return await client.text_to_video(
            prompt=body.prompt,
            negative_prompt=body.negative_prompt,
            audio_url=body.audio_url,
            model=body.model,
            parameters=body.parameters,
        )
    except BailianAPIError as exc:
        _raise_bailian_error(exc)


@router.post("/videos/image-to-video", summary="Wanx async image-to-video")
async def image_to_video(body: ImageToVideoReq):
    img_url = body.img_url or body.image_url
    if not (img_url or body.first_clip_url or body.media):
        raise HTTPException(status_code=422, detail="img_url, image_url, first_clip_url, or media is required")
    try:
        client = await get_bailian_client()
        return await client.image_to_video(
            img_url=img_url,
            prompt=body.prompt,
            negative_prompt=body.negative_prompt,
            audio_url=body.audio_url,
            last_frame_url=body.last_frame_url,
            first_clip_url=body.first_clip_url,
            media=body.media,
            template=body.template,
            model=body.model,
            parameters=body.parameters,
            protocol=body.protocol,
        )
    except BailianAPIError as exc:
        _raise_bailian_error(exc)


@router.get("/tasks/{task_id}", summary="Query Bailian async task")
async def query_task(
    task_id: str,
    protocol: Literal["workspace", "legacy", "auto"] = Query(
        "workspace",
        description="workspace for Qwen/Wanx new APIs, legacy for Beijing 2.1-2.6 image-to-video",
    ),
):
    try:
        client = await get_bailian_client()
        return await client.query_task(task_id, protocol=protocol)
    except BailianAPIError as exc:
        _raise_bailian_error(exc)
