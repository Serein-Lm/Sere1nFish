"""Android input-method recovery runtime.

The HTTP and chat layers only express "restore manual input". ADB command
details and fallback selection stay in this adapter.
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import asdict, dataclass
from typing import Any

from AutoGLM_GUI.platform_utils import build_adb_command


ADB_KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"
_NON_TYPING_IME_MARKERS = ("voice", "handwriting")


class KeyboardRestoreError(RuntimeError):
    """Raised when no usable manual input method can be restored."""


@dataclass(frozen=True)
class KeyboardRestoreResult:
    current_ime: str
    restored_ime: str
    changed: bool
    available_imes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_ime(value: Any) -> str:
    normalized = str(value or "").strip()
    return "" if normalized.casefold() in {"null", "none"} else normalized


def choose_manual_ime(
    enabled_imes: list[str],
    *,
    preferred_ime: str = "",
    remembered_ime: str = "",
) -> str:
    """Choose a typing IME without binding the runtime to phone vendors."""
    candidates = list(
        dict.fromkeys(
            ime
            for value in enabled_imes
            if (ime := _clean_ime(value)) and ADB_KEYBOARD_IME not in ime
        )
    )
    if not candidates:
        return ""
    for preferred in (_clean_ime(preferred_ime), _clean_ime(remembered_ime)):
        if preferred in candidates:
            return preferred
    typing_candidates = [
        ime
        for ime in candidates
        if not any(marker in ime.casefold() for marker in _NON_TYPING_IME_MARKERS)
    ]
    return (typing_candidates or candidates)[0]


class MobileKeyboardService:
    """State-aware facade over Android IME shell commands."""

    def __init__(self, *, timeout_seconds: int = 10) -> None:
        self.timeout_seconds = max(1, int(timeout_seconds))
        self._last_manual_ime: dict[str, str] = {}
        self._lock = threading.Lock()

    def _run(self, device_id: str, args: list[str]) -> str:
        result = subprocess.run(
            [*build_adb_command(device_id), "shell", *args],
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            raise KeyboardRestoreError(
                output.strip() or f"adb 命令失败，退出码 {result.returncode}"
            )
        return output.strip()

    def remember_manual_ime(self, device_key: str, ime: str) -> None:
        normalized = _clean_ime(ime)
        if not normalized or ADB_KEYBOARD_IME in normalized:
            return
        with self._lock:
            self._last_manual_ime[str(device_key)] = normalized

    def restore_system_keyboard(
        self,
        adb_device_id: str,
        *,
        device_key: str = "",
        preferred_ime: str = "",
    ) -> KeyboardRestoreResult:
        current = _clean_ime(
            self._run(
                adb_device_id,
                ["settings", "get", "secure", "default_input_method"],
            )
        )
        enabled = [
            _clean_ime(line)
            for line in self._run(adb_device_id, ["ime", "list", "-s"]).splitlines()
            if _clean_ime(line)
        ]
        cache_key = str(device_key or adb_device_id)
        if current and ADB_KEYBOARD_IME not in current:
            self.remember_manual_ime(cache_key, current)
            return KeyboardRestoreResult(
                current_ime=current,
                restored_ime=current,
                changed=False,
                available_imes=enabled,
            )

        with self._lock:
            remembered = self._last_manual_ime.get(cache_key, "")
        target = choose_manual_ime(
            enabled,
            preferred_ime=preferred_ime,
            remembered_ime=remembered,
        )
        if not target:
            raise KeyboardRestoreError(
                "设备没有已启用的系统输入法，请先在手机设置中启用一个输入法"
            )

        self._run(adb_device_id, ["ime", "set", target])
        restored = _clean_ime(
            self._run(
                adb_device_id,
                ["settings", "get", "secure", "default_input_method"],
            )
        )
        if restored != target:
            raise KeyboardRestoreError(
                f"系统输入法切换未生效，当前仍为 {restored or '未知'}"
            )
        self.remember_manual_ime(cache_key, target)
        return KeyboardRestoreResult(
            current_ime=current,
            restored_ime=target,
            changed=True,
            available_imes=enabled,
        )


_KEYBOARD_SERVICE = MobileKeyboardService()


def get_mobile_keyboard_service() -> MobileKeyboardService:
    return _KEYBOARD_SERVICE
