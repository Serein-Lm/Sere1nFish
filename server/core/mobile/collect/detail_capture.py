"""Registry-backed detail evidence capture strategies for mobile collection."""

from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass, field
from typing import Any, Protocol

from PIL import Image

from core.mobile.coordinates import resolve_swipe
from core.mobile.manager import MobileDeviceManager
from core.mobile.planner import run_planned_task
from core.mobile.screen_capture import capture_ready_screen
from core.mobile.collect.analysis import analyze_social_media_frame


@dataclass(slots=True)
class DetailCaptureContext:
    device_id: str
    app_name: str
    keyword: str
    place_name: str
    collection_goal: str
    navigation_hint: str
    candidate_fields: dict[str, Any]
    project_id: str
    task_id: str
    owner: str
    stop_event: asyncio.Event
    max_items: int
    swipe_interval: float


@dataclass(slots=True)
class DetailCaptureResult:
    accepted: bool
    fields: dict[str, Any] = field(default_factory=dict)
    frames: list[dict[str, Any]] = field(default_factory=list)
    restored_to_results: bool = False
    reason: str = ""


class DetailCaptureStrategy(Protocol):
    name: str

    async def capture(self, context: DetailCaptureContext) -> DetailCaptureResult: ...


def _terminal_event(event: dict[str, Any]) -> tuple[str, str]:
    stage = str(event.get("stage") or "")
    if stage not in {"done", "aborted", "error", "cancelled"}:
        return "", ""
    data = event.get("data")
    message = ""
    if isinstance(data, dict):
        message = str(data.get("message") or data.get("reason") or "")
    return stage, message


async def _run_goal(context: DetailCaptureContext, goal: str, suffix: str) -> bool:
    terminal = ""
    async for event in run_planned_task(
        context.device_id,
        goal,
        project_id=context.project_id or None,
        owner=context.owner,
        plan_id=f"{context.task_id}-{suffix}",
        max_replans=1,
        preplanned_subtasks=[goal],
    ):
        stage, _message = _terminal_event(event)
        if stage:
            terminal = stage
        if context.stop_event.is_set():
            return False
    return terminal == "done"


def _crop_signature(image_base64: str, bounds: list[int]) -> bytes | None:
    try:
        raw = image_base64.split(",", 1)[-1]
        image = Image.open(io.BytesIO(base64.b64decode(raw))).convert("L")
        left, top, right, bottom = bounds
        box = (
            round(image.width * left / 1000),
            round(image.height * top / 1000),
            round(image.width * right / 1000),
            round(image.height * bottom / 1000),
        )
        cropped = image.crop(box).resize((24, 24))
        return bytes(cropped.getdata())
    except Exception:  # noqa: BLE001
        return None


def _near_duplicate(left: bytes | None, right: bytes | None) -> bool:
    if not left or not right or len(left) != len(right):
        return False
    average = sum(abs(a - b) for a, b in zip(left, right)) / len(left)
    return average < 3.0


def _swipe_gallery(device_id: str) -> None:
    manager = MobileDeviceManager()
    device = manager.get_device(device_id)
    adb_id = manager.resolve_adb_device_id(device_id)
    sx, sy, ex, ey = resolve_swipe(
        820,
        520,
        180,
        520,
        device_id=adb_id,
        coord_space="normalized_1000",
    )
    device.swipe(sx, sy, ex, ey, 320, delay=0.1)


@dataclass(frozen=True, slots=True)
class SocialPlaceGalleryCaptureStrategy:
    name: str = "social_place_gallery"

    async def capture(self, context: DetailCaptureContext) -> DetailCaptureResult:
        goal = (
            f"当前已打开{context.app_name}中与地点“{context.place_name}”相关的公开详情。"
            f"{context.navigation_hint}。找到并打开第一张符合要求的公开用户图片后立即完成，"
            "停留在图片查看器。不要点赞、评论、收藏、关注、私信、下单、发布或修改任何内容。"
        )
        opened = await _run_goal(context, goal, "media-open")
        if not opened or context.stop_event.is_set():
            return DetailCaptureResult(False, reason="未进入公开图片查看器")

        frames: list[dict[str, Any]] = []
        signatures: list[bytes] = []
        duplicate_streak = 0
        restored = False
        try:
            for index in range(max(1, context.max_items)):
                if context.stop_event.is_set():
                    break
                captured = await capture_ready_screen(
                    context.device_id,
                    manager=MobileDeviceManager(),
                )
                shot = captured.screenshot
                analysis = await analyze_social_media_frame(
                    shot.base64_data,
                    app_name=context.app_name,
                    place_name=context.place_name,
                    keyword=context.keyword,
                    candidate_fields=context.candidate_fields,
                    collection_goal=context.collection_goal,
                    project_id=context.project_id or None,
                    task_id=context.task_id,
                )
                bounds = list(analysis.get("image_bounds") or [])
                signature = (
                    _crop_signature(shot.base64_data, bounds) if bounds else None
                )
                duplicate = any(
                    _near_duplicate(signature, prior) for prior in signatures
                )
                if analysis.get("accepted") and not duplicate:
                    frames.append(
                        {
                            "image_base64": shot.base64_data,
                            "width": int(shot.width or 0),
                            "height": int(shot.height or 0),
                            "analysis": analysis,
                            "gallery_index": index,
                        }
                    )
                    if signature:
                        signatures.append(signature)
                    duplicate_streak = 0
                elif duplicate:
                    duplicate_streak += 1
                elif not analysis.get("is_media_viewer"):
                    break

                if index + 1 >= context.max_items or duplicate_streak >= 2:
                    break
                await asyncio.to_thread(_swipe_gallery, context.device_id)
                await asyncio.sleep(max(0.4, context.swipe_interval))
        finally:
            if not context.stop_event.is_set():
                try:
                    restored = await _run_goal(
                        context,
                        (
                            f"返回{context.app_name}中“{context.keyword}”的搜索结果列表并停留。"
                            "不要进行任何互动或修改。"
                        ),
                        "media-restore",
                    )
                except Exception:  # noqa: BLE001
                    restored = False
        if not frames:
            return DetailCaptureResult(
                False,
                restored_to_results=restored,
                reason="图片查看器中未发现通过地点和价值审核的图片",
            )

        descriptions = [
            str((frame.get("analysis") or {}).get("photo_description") or "")
            for frame in frames
        ]
        contexts = [
            str((frame.get("analysis") or {}).get("visible_context") or "")
            for frame in frames
        ]
        return DetailCaptureResult(
            True,
            fields={
                **context.candidate_fields,
                "place_name": context.place_name,
                "photo_count": len(frames),
                "photo_descriptions": [item for item in descriptions if item],
                "review_contexts": [item for item in contexts if item],
            },
            frames=frames,
            restored_to_results=restored,
            reason=f"已采集 {len(frames)} 张公开图片",
        )


class DetailCaptureRegistry:
    _strategies: dict[str, DetailCaptureStrategy] = {
        "social_place_gallery": SocialPlaceGalleryCaptureStrategy(),
    }

    @classmethod
    def register(cls, strategy: DetailCaptureStrategy) -> None:
        name = str(strategy.name or "").strip()
        if not name:
            raise ValueError("详情采集策略名不能为空")
        cls._strategies[name] = strategy

    @classmethod
    def resolve(cls, name: str) -> DetailCaptureStrategy | None:
        normalized = str(name or "default").strip()
        if normalized in {"", "default", "none"}:
            return None
        return cls._strategies.get(normalized)
