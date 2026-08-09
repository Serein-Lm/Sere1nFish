"""Archive reviewed mobile media through the unified object storage service."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
from dataclasses import dataclass
from typing import Any

from PIL import Image

from api.dao import mobile_artifacts as mobile_artifacts_dao
from api.dao import social_collection as social_dao
from api.storage import get_object_storage
from core.logger import get_logger


logger = get_logger("social_collection_media")


@dataclass(frozen=True, slots=True)
class MediaArchiveBatchResult:
    items: list[dict[str, Any]]
    failed_count: int


def crop_screen_render(
    image_base64: str,
    bounds: list[int],
) -> tuple[bytes, int, int]:
    """Crop a normalized media rectangle and encode the pixels as lossless PNG."""
    if len(bounds) != 4:
        raise ValueError("图片边界必须包含 left/top/right/bottom")
    left, top, right, bottom = [int(value) for value in bounds]
    if not (0 <= left < right <= 1000 and 0 <= top < bottom <= 1000):
        raise ValueError("图片边界超出 0-1000 归一化坐标")
    raw = str(image_base64 or "").split(",", 1)[-1]
    image = Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGB")
    box = (
        max(0, round(image.width * left / 1000)),
        max(0, round(image.height * top / 1000)),
        min(image.width, round(image.width * right / 1000)),
        min(image.height, round(image.height * bottom / 1000)),
    )
    cropped = image.crop(box)
    if cropped.width < 32 or cropped.height < 32:
        raise ValueError("识别出的图片区域过小")
    output = io.BytesIO()
    cropped.save(output, format="PNG", optimize=True)
    return output.getvalue(), cropped.width, cropped.height


async def _archive_frame(
    db,
    *,
    frame: dict[str, Any],
    frame_index: int,
    job_id: str,
    project_id: str,
    target_id: str,
    platform: str,
    place_name: str,
    keyword: str,
    device_id: str,
    run_task_id: str,
    task_def_id: str,
    record_id: str,
    candidate_fields: dict[str, Any],
) -> dict[str, Any]:
    analysis = dict(frame.get("analysis") or {})
    bounds = list(analysis.get("image_bounds") or [])
    png_bytes, width, height = await asyncio.to_thread(
        crop_screen_render,
        str(frame.get("image_base64") or ""),
        bounds,
    )
    content_sha256 = hashlib.sha256(png_bytes).hexdigest()
    evidence_id = social_dao.stable_evidence_id(
        project_id=project_id,
        platform=platform,
        place_name=place_name,
        content_sha256=content_sha256,
    )
    context = await mobile_artifacts_dao.save_screenshot(
        db,
        image_base64=str(frame.get("image_base64") or ""),
        project_id=project_id,
        task_id=run_task_id,
        device_id=device_id,
        source="social_place_media",
        width=int(frame.get("width") or 0) or None,
        height=int(frame.get("height") or 0) or None,
        note=f"{platform} {place_name} gallery={frame_index}",
        meta={
            "job_id": job_id,
            "record_id": record_id,
            "platform": platform,
            "keyword": keyword,
            "capture_fidelity": "full_context_screen",
        },
    )
    storage = await get_object_storage()
    stored = await storage.store_bytes(
        png_bytes,
        kind="social_media_image",
        filename=f"{evidence_id}.png",
        object_id=evidence_id,
        content_type="image/png",
        project_id=project_id,
        subject_id=place_name,
        source="social_place_media",
        source_id=evidence_id,
        relative_path=platform,
        meta={
            "job_id": job_id,
            "run_task_id": run_task_id,
            "record_id": record_id,
            "platform": platform,
            "capture_fidelity": "screen_render_crop",
            "source_original_available": False,
            "content_sha256": content_sha256,
        },
    )
    document = {
        "project_id": project_id,
        "target_id": target_id,
        "platform": platform,
        "place_name": place_name,
        "keyword": keyword,
        "device_id": device_id,
        "task_def_id": task_def_id,
        "record_id": record_id,
        "candidate_fields": candidate_fields,
        "gallery_index": int(frame.get("gallery_index", frame_index) or 0),
        "analysis": analysis,
        "capture_fidelity": "screen_render_crop",
        "source_original_available": False,
        "storage_object_id": str(stored.get("object_id") or evidence_id),
        "image_url": f"/api/v1/social-collection/media/{evidence_id}/image",
        "content_sha256": content_sha256,
        "width": width,
        "height": height,
        "crop_bounds": bounds,
        "context_screenshot_id": str(context.get("screenshot_id") or ""),
        "context_storage_object_id": str(context.get("storage_object_id") or ""),
        "context_image_url": str(context.get("url") or ""),
    }
    return await social_dao.upsert_media_evidence(
        db,
        evidence_id=evidence_id,
        document=document,
        job_id=job_id,
        run_task_id=run_task_id,
    )


async def archive_social_media_frames(
    db,
    *,
    frames: list[dict[str, Any]],
    job_id: str,
    project_id: str,
    target_id: str,
    platform: str,
    place_name: str,
    keyword: str,
    device_id: str,
    run_task_id: str,
    task_def_id: str,
    record_id: str,
    candidate_fields: dict[str, Any],
) -> MediaArchiveBatchResult:
    """Archive accepted frames with bounded concurrency; one failure does not hide others."""
    semaphore = asyncio.Semaphore(4)

    async def _bounded(index: int, frame: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await _archive_frame(
                db,
                frame=frame,
                frame_index=index,
                job_id=job_id,
                project_id=project_id,
                target_id=target_id,
                platform=platform,
                place_name=place_name,
                keyword=keyword,
                device_id=device_id,
                run_task_id=run_task_id,
                task_def_id=task_def_id,
                record_id=record_id,
                candidate_fields=candidate_fields,
            )

    results = await asyncio.gather(
        *(_bounded(index, frame) for index, frame in enumerate(frames)),
        return_exceptions=True,
    )
    failures = [item for item in results if isinstance(item, BaseException)]
    archived = [item for item in results if isinstance(item, dict)]
    for failure in failures:
        logger.warning(
            "社交图片归档失败 | job=%s record=%s error=%s",
            job_id,
            record_id,
            failure,
        )
    if failures and not archived:
        raise RuntimeError(f"社交图片归档全部失败: {failures[0]}")
    return MediaArchiveBatchResult(
        items=archived,
        failed_count=len(failures),
    )
