"""Reliable mobile screen capture helpers."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from PIL import Image, ImageStat, UnidentifiedImageError

from AutoGLM_GUI.device_protocol import Screenshot
from core.mobile.manager import MobileDeviceManager


@dataclass
class CaptureResult:
    screenshot: Screenshot
    attempts: int
    blank_frames: int = 0
    wake: dict[str, Any] = field(default_factory=dict)

    def metadata(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "blank_frames": self.blank_frames,
            "wake_ok": bool((self.wake or {}).get("ok")),
        }


class ScreenCaptureUnavailableError(RuntimeError):
    """Raised when the device never returns a usable frame within the retry budget."""


def _decode_image(shot: Screenshot) -> Image.Image | None:
    if not shot.base64_data:
        return None
    try:
        raw = base64.b64decode(shot.base64_data, validate=True)
        return Image.open(BytesIO(raw))
    except (ValueError, UnidentifiedImageError, OSError):
        return None


def is_probably_blank_screen(shot: Screenshot) -> bool:
    """Detect failed/placeholder black frames without rejecting normal dark UIs."""
    if not shot.base64_data:
        return True
    if bool(getattr(shot, "capture_failed", False)):
        return True

    img = _decode_image(shot)
    if img is None:
        return True

    gray = img.convert("L")
    gray.thumbnail((96, 96))
    stat = ImageStat.Stat(gray)
    mean = float(stat.mean[0])
    extrema = gray.getextrema()
    max_luma = float(extrema[1] if extrema else 0)

    # A genuine dark-mode UI still has text/icons and therefore some bright pixels.
    return max_luma <= 18 and mean <= 6


async def capture_ready_screen(
    device_id: str,
    *,
    manager: MobileDeviceManager | None = None,
    timeout: int = 6,
    attempts: int = 2,
    delay: float = 0.45,
    wake: bool = True,
) -> CaptureResult:
    """Capture a non-blank screen, retrying common first-frame black captures."""
    mgr = manager or MobileDeviceManager()
    wake_result: dict[str, Any] = {}
    if wake:
        wake_result = await wake_device(device_id, stay_on=True)
        await asyncio.sleep(min(delay, 0.5))

    last_error: Exception | None = None
    blank_frames = 0
    total = max(1, attempts)
    for index in range(total):
        try:
            shot = await asyncio.to_thread(mgr.capture, device_id, timeout)
            if not is_probably_blank_screen(shot):
                return CaptureResult(
                    screenshot=shot,
                    attempts=index + 1,
                    blank_frames=blank_frames,
                    wake=wake_result,
                )
            blank_frames += 1
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        if index < total - 1:
            await asyncio.sleep(delay)

    detail = f": {last_error}" if last_error else ""
    raise ScreenCaptureUnavailableError(
        f"设备 {device_id} 在 {total} 次尝试内未返回有效截图{detail}"
    ) from last_error


async def wake_device(device_id: str, *, stay_on: bool = True) -> dict[str, Any]:
    """Best-effort code-level wake; planners should not emit wake subtasks."""
    try:
        from core.mobile.pool import DevicePool

        return await asyncio.to_thread(
            DevicePool.get_instance().wake, device_id, stay_on=stay_on
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
