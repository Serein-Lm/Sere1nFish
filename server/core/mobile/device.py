"""Shared mobile device abstraction built on top of existing AutoGLM-GUI code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from AutoGLM_GUI.device_protocol import DeviceProtocol, Screenshot


class DeviceCapabilityError(RuntimeError):
    """Raised when a device capability is unavailable or unhealthy."""


@dataclass(frozen=True)
class DeviceHealth:
    device_id: str
    online: bool
    screenshot_ready: bool = False
    input_ready: bool = False
    current_app_ready: bool = False
    capture_failed: bool = False
    error: str | None = None


@runtime_checkable
class MobileDevice(Protocol):
    """Protocol for the shared mobile device contract."""

    @property
    def device_id(self) -> str:
        ...

    def get_screenshot(self, timeout: int = 10) -> Screenshot:
        ...

    def tap(self, x: int, y: int, delay: float | None = None) -> None:
        ...

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int | None = None,
        delay: float | None = None,
    ) -> None:
        ...

    def type_text(self, text: str) -> None:
        ...

    def clear_text(self) -> None:
        ...

    def back(self, delay: float | None = None) -> None:
        ...

    def home(self, delay: float | None = None) -> None:
        ...

    def press_key(self, key: str, delay: float | None = None) -> bool:
        ...

    def launch_app(self, app_name: str, delay: float | None = None) -> bool:
        ...

    def get_current_app(self) -> str:
        ...

    def detect_and_set_adb_keyboard(self) -> str:
        ...

    def restore_keyboard(self, ime: str) -> None:
        ...


class DeviceAdapter:
    """Adapter that wraps the existing AutoGLM device implementation."""

    def __init__(self, device: DeviceProtocol):
        self._device = device

    @property
    def device_id(self) -> str:
        return self._device.device_id

    def get_screenshot(self, timeout: int = 10) -> Screenshot:
        return self._device.get_screenshot(timeout=timeout)

    def tap(self, x: int, y: int, delay: float | None = None) -> None:
        self._device.tap(x, y, delay=delay)

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int | None = None,
        delay: float | None = None,
    ) -> None:
        self._device.swipe(start_x, start_y, end_x, end_y, duration_ms, delay)

    def type_text(self, text: str) -> None:
        self._device.type_text(text)

    def clear_text(self) -> None:
        self._device.clear_text()

    def back(self, delay: float | None = None) -> None:
        self._device.back(delay=delay)

    def home(self, delay: float | None = None) -> None:
        self._device.home(delay=delay)

    def press_key(self, key: str, delay: float | None = None) -> bool:
        return self._device.press_key(key, delay=delay)

    def launch_app(self, app_name: str, delay: float | None = None) -> bool:
        return self._device.launch_app(app_name, delay=delay)

    def get_current_app(self) -> str:
        return self._device.get_current_app()

    def detect_and_set_adb_keyboard(self) -> str:
        return self._device.detect_and_set_adb_keyboard()

    def restore_keyboard(self, ime: str) -> None:
        self._device.restore_keyboard(ime)
