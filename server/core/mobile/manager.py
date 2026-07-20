"""Device manager facade that reuses the existing AutoGLM device manager."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from AutoGLM_GUI.device_manager import DeviceManager, ManagedDevice
from AutoGLM_GUI.devices.adb_device import ADBDevice
from AutoGLM_GUI.device_protocol import Screenshot

from core.mobile.device import DeviceAdapter, DeviceHealth, DeviceCapabilityError


@dataclass
class MobileDeviceInfo:
    device_id: str
    status: str
    model: str | None = None
    connection_type: str | None = None


class MobileDeviceManager:
    """Thin wrapper around the existing device manager for reuse."""

    def __init__(self, adb_path: str | None = None) -> None:
        self._device_manager = DeviceManager.get_instance(adb_path=adb_path or "adb")

    def _find_managed_device(
        self, device_id: str, *, refresh_if_missing: bool = True
    ) -> ManagedDevice | None:
        managed = self._device_manager.get_device_by_device_id(device_id)
        if managed or not refresh_if_missing:
            return managed
        self.refresh()
        return self._device_manager.get_device_by_device_id(device_id)

    def start_polling(self) -> None:
        """启动后台设备轮询(幂等)。"""
        try:
            if not self._device_manager.is_polling_active():
                self._device_manager.start_polling()
        except Exception:
            pass

    def refresh(self) -> None:
        """同步强制刷新设备列表。"""
        try:
            self._device_manager.force_refresh()
        except Exception:
            pass

    def list_devices(self) -> list[MobileDeviceInfo]:
        devices = self._device_manager.get_devices()
        return [
            MobileDeviceInfo(
                device_id=d.serial,
                status=getattr(d, "status", "unknown"),
                model=getattr(d, "model", None),
                connection_type=getattr(
                    getattr(d, "connection_type", None),
                    "value",
                    getattr(d, "connection_type", None),
                ),
            )
            for d in devices
        ]

    def resolve_adb_device_id(self, device_id: str) -> str:
        """Resolve a stable device serial to the active ADB transport endpoint."""
        managed = self._find_managed_device(device_id)
        if not managed:
            return device_id
        try:
            return managed.primary_device_id
        except Exception:
            return device_id

    def resolve_ready_adb_device_id(self, device_id: str) -> str | None:
        """Return an online ADB endpoint, refreshing the runtime cache once."""
        managed = self._find_managed_device(
            device_id,
            refresh_if_missing=False,
        )
        if not managed or not managed.online:
            self.refresh()
            managed = self._find_managed_device(
                device_id,
                refresh_if_missing=False,
            )
        if not managed or not managed.online:
            return None
        try:
            return managed.primary_device_id
        except Exception:
            return None

    def get_device(self, device_id: str) -> DeviceAdapter:
        managed = self._find_managed_device(device_id)
        if managed:
            return DeviceAdapter(self._device_manager.get_device_protocol(device_id))
        return DeviceAdapter(ADBDevice(device_id))

    def health(self, device_id: str) -> DeviceHealth:
        try:
            device = self.get_device(device_id)
        except Exception as exc:
            return DeviceHealth(device_id=device_id, online=False, error=str(exc))

        screenshot_ready = False
        capture_failed = False
        screenshot_error: str | None = None
        try:
            screenshot = device.get_screenshot(timeout=5)
            capture_failed = bool(getattr(screenshot, "capture_failed", False))
            screenshot_ready = bool(screenshot.base64_data) and not capture_failed
            if capture_failed:
                screenshot_error = "设备未返回有效截图"
        except Exception as exc:
            capture_failed = True
            screenshot_error = str(exc)

        current_app_ready = False
        try:
            current_app_ready = bool(device.get_current_app())
        except Exception:
            pass

        online = screenshot_ready or current_app_ready
        return DeviceHealth(
            device_id=device_id,
            online=online,
            screenshot_ready=screenshot_ready,
            input_ready=True,
            current_app_ready=current_app_ready,
            capture_failed=capture_failed,
            error=screenshot_error,
        )

    def capture(self, device_id: str, timeout: int = 10) -> Screenshot:
        return self.get_device(device_id).get_screenshot(timeout=timeout)
