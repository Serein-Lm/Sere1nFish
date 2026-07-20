import base64
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from core.mobile.screen_capture import (
    ScreenCaptureUnavailableError,
    capture_ready_screen,
)
from AutoGLM_GUI.device_protocol import Screenshot


def _image(color: str, *, capture_failed: bool = False) -> Screenshot:
    image = Image.new("RGB", (32, 64), color=color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return Screenshot(
        base64_data=base64.b64encode(buffer.getvalue()).decode("ascii"),
        width=32,
        height=64,
        capture_failed=capture_failed,
    )


class _CaptureManager:
    def __init__(self, shots: list[Screenshot]) -> None:
        self.shots = list(shots)
        self.calls: list[tuple[str, int]] = []

    def capture(self, device_id: str, timeout: int) -> Screenshot:
        self.calls.append((device_id, timeout))
        return self.shots.pop(0)


@pytest.mark.asyncio
async def test_capture_ready_screen_retries_blank_then_returns_valid_frame() -> None:
    manager = _CaptureManager([_image("black"), _image("white")])

    result = await capture_ready_screen(
        "device-a",
        manager=manager,
        wake=False,
        delay=0,
    )

    assert result.attempts == 2
    assert result.blank_frames == 1
    assert manager.calls == [("device-a", 6), ("device-a", 6)]


@pytest.mark.asyncio
async def test_capture_ready_screen_never_returns_failed_placeholder() -> None:
    manager = _CaptureManager([
        _image("white", capture_failed=True),
        _image("black"),
    ])

    with pytest.raises(ScreenCaptureUnavailableError, match="2 次尝试"):
        await capture_ready_screen(
            "device-a",
            manager=manager,
            wake=False,
            delay=0,
        )

    assert len(manager.calls) == 2


def test_adb_device_preserves_capture_failure_marker(monkeypatch) -> None:
    from AutoGLM_GUI.devices import adb_device

    monkeypatch.setattr(
        adb_device.adb,
        "get_screenshot",
        lambda *_args, **_kwargs: _image("black", capture_failed=True),
    )

    result = adb_device.ADBDevice("device-a").get_screenshot()

    assert result.capture_failed is True


def test_mobile_health_rejects_capture_failure_placeholder(monkeypatch) -> None:
    from core.mobile.manager import MobileDeviceManager

    manager = object.__new__(MobileDeviceManager)
    device = SimpleNamespace(
        get_screenshot=lambda **_kwargs: _image("black", capture_failed=True),
        get_current_app=lambda: "com.tencent.mm",
    )
    monkeypatch.setattr(manager, "get_device", lambda _device_id: device)

    health = manager.health("device-a")

    assert health.online is True
    assert health.current_app_ready is True
    assert health.screenshot_ready is False
    assert health.capture_failed is True
    assert health.error == "设备未返回有效截图"
