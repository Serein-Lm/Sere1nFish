"""ADB device adapter that reconnects by stable device identity on transport loss."""
from __future__ import annotations

from typing import Any

from AutoGLM_GUI.devices.adb_device import ADBDevice
from AutoGLM_GUI.device_protocol import Screenshot

from core.mobile.manager import MobileDeviceManager
from core.mobile.pool import DevicePool


class RecoveringADBDevice:
    """Resolve the active endpoint per action and retry once after reconnect."""

    def __init__(self, device_id: str) -> None:
        self._stable_device_id = device_id

    @property
    def device_id(self) -> str:
        return self._stable_device_id

    def _endpoint(self) -> str:
        return MobileDeviceManager().resolve_adb_device_id(self._stable_device_id)

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        endpoint = self._endpoint()
        try:
            return getattr(ADBDevice(endpoint), method)(*args, **kwargs)
        except Exception:
            recovered = DevicePool.get_instance().ensure_connected(
                self._stable_device_id
            )
            if not recovered:
                raise
            return getattr(ADBDevice(recovered), method)(*args, **kwargs)

    def get_screenshot(self, timeout: int = 10) -> Screenshot:
        return self._call("get_screenshot", timeout=timeout)

    def tap(self, x: int, y: int, delay: float | None = None) -> None:
        self._call("tap", x, y, delay=delay)

    def double_tap(self, x: int, y: int, delay: float | None = None) -> None:
        self._call("double_tap", x, y, delay=delay)

    def long_press(
        self,
        x: int,
        y: int,
        duration_ms: int = 3000,
        delay: float | None = None,
    ) -> None:
        self._call("long_press", x, y, duration_ms=duration_ms, delay=delay)

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int | None = None,
        delay: float | None = None,
    ) -> None:
        self._call(
            "swipe",
            start_x,
            start_y,
            end_x,
            end_y,
            duration_ms=duration_ms,
            delay=delay,
        )

    def type_text(self, text: str) -> None:
        self._call("type_text", text)

    def clear_text(self) -> None:
        self._call("clear_text")

    def back(self, delay: float | None = None) -> None:
        self._call("back", delay=delay)

    def home(self, delay: float | None = None) -> None:
        self._call("home", delay=delay)

    def press_key(self, key: str, delay: float | None = None) -> bool:
        return bool(self._call("press_key", key, delay=delay))

    def launch_app(self, app_name: str, delay: float | None = None) -> bool:
        return bool(self._call("launch_app", app_name, delay=delay))

    def get_current_app(self) -> str:
        return str(self._call("get_current_app"))

    def detect_and_set_adb_keyboard(self) -> str:
        return str(self._call("detect_and_set_adb_keyboard"))

    def restore_keyboard(self, ime: str) -> None:
        self._call("restore_keyboard", ime)
