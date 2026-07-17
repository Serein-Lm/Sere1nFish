"""Verified Android application launch through the shared ADB runtime."""

from __future__ import annotations

from dataclasses import dataclass
import re
import subprocess
import time
from typing import Callable, Literal
from xml.etree import ElementTree

from AutoGLM_GUI.adb.apps import get_package_name


AppInstance = Literal["primary", "clone"]
CommandRunner = Callable[[list[str], int], subprocess.CompletedProcess[str]]

_ACTIVITY_PACKAGE_RE = re.compile(
    r"(?:topResumedActivity|mResumedActivity|ResumedActivity|mFocusedApp)"
    r".*?\bu\d+\s+([A-Za-z0-9._]+)/(?:[A-Za-z0-9._$]+)"
)
_WINDOW_PACKAGE_RE = re.compile(
    r"(?:mCurrentFocus|mFocusedApp).*?\s([A-Za-z0-9._]+)/(?:[A-Za-z0-9._$]+)"
)
_COMPONENT_RE = re.compile(
    r"^([A-Za-z0-9._]+)/(\.?[A-Za-z0-9._$]+)$", re.MULTILINE
)
_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_RESOLVER_PACKAGES = {
    "android",
    "com.android.intentresolver",
    "com.android.internal.app",
}
_CLONE_MARKERS = ("分身", "双开", "克隆", "clone")


def _default_command_runner(
    args: list[str], timeout: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["adb", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@dataclass(frozen=True)
class AppLaunchResult:
    ok: bool
    app_name: str
    package_name: str | None
    foreground_package: str | None = None
    selected_instance: AppInstance = "primary"
    chooser_handled: bool = False
    error: str | None = None


class AdbAppLauncher:
    """Launch an app and resolve OEM dual-app choosers deterministically."""

    def __init__(
        self,
        *,
        runner: CommandRunner = _default_command_runner,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._runner = runner
        self._sleep = sleep

    def _run(self, args: list[str], *, timeout: int = 10) -> str:
        try:
            result = self._runner(args, timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"ADB 命令超时: {' '.join(args[-4:])}") from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "ADB 命令失败").strip()
            raise RuntimeError(message[:300])
        return result.stdout or ""

    def _shell(
        self, adb_device_id: str, args: list[str], *, timeout: int = 10
    ) -> str:
        return self._run(["-s", adb_device_id, "shell", *args], timeout=timeout)

    def current_package(self, adb_device_id: str) -> str | None:
        output = self._shell(
            adb_device_id, ["dumpsys", "activity", "activities"], timeout=8
        )
        match = _ACTIVITY_PACKAGE_RE.search(output)
        if match:
            return match.group(1)

        output = self._shell(adb_device_id, ["dumpsys", "window"], timeout=8)
        match = _WINDOW_PACKAGE_RE.search(output)
        return match.group(1) if match else None

    def _wait_for_package(
        self,
        adb_device_id: str,
        package_name: str,
        *,
        attempts: int,
    ) -> tuple[str | None, bool]:
        foreground: str | None = None
        for attempt in range(attempts):
            foreground = self.current_package(adb_device_id)
            if foreground == package_name:
                return foreground, True
            if foreground in _RESOLVER_PACKAGES:
                return foreground, False
            if attempt + 1 < attempts:
                self._sleep(0.25)
        return foreground, False

    def _resolve_launcher_component(
        self, adb_device_id: str, package_name: str
    ) -> str:
        output = self._shell(
            adb_device_id,
            [
                "cmd",
                "package",
                "resolve-activity",
                "--brief",
                "-c",
                "android.intent.category.LAUNCHER",
                package_name,
            ],
            timeout=10,
        )
        matches = _COMPONENT_RE.findall(output)
        for package, activity in reversed(matches):
            if package == package_name:
                return f"{package}/{activity}"
        raise RuntimeError(f"未找到应用启动 Activity: {package_name}")

    def _collapse_system_panels(self, adb_device_id: str) -> None:
        """避免通知栏等系统浮层遮住 OEM 应用选择器。"""
        try:
            self._shell(
                adb_device_id,
                ["cmd", "statusbar", "collapse"],
                timeout=5,
            )
        except Exception:
            # 部分 Android 版本不提供 statusbar service，应用启动仍可继续。
            return

    def _dump_ui(self, adb_device_id: str) -> ElementTree.Element:
        output = self._run(
            [
                "-s",
                adb_device_id,
                "exec-out",
                "uiautomator",
                "dump",
                "--compressed",
                "/dev/tty",
            ],
            timeout=12,
        )
        start = output.find("<?xml")
        end = output.rfind("</hierarchy>")
        if start < 0 or end < 0:
            raise RuntimeError("双开选择器未返回有效界面树")
        try:
            return ElementTree.fromstring(
                output[start : end + len("</hierarchy>")]
            )
        except ElementTree.ParseError as exc:
            raise RuntimeError("双开选择器界面树解析失败") from exc

    @staticmethod
    def _matches_instance(text: str, app_name: str, instance: AppInstance) -> bool:
        normalized = text.strip()
        if not normalized:
            return False
        has_clone_marker = any(
            marker.lower() in normalized.lower() for marker in _CLONE_MARKERS
        )
        if instance == "clone":
            return app_name in normalized and has_clone_marker
        return normalized == app_name or (
            app_name in normalized and not has_clone_marker
        )

    @classmethod
    def _choice_bounds(
        cls,
        root: ElementTree.Element,
        app_name: str,
        instance: AppInstance,
    ) -> tuple[int, int, int, int] | None:
        fallback: tuple[int, int, int, int] | None = None
        for node in root.iter("node"):
            texts = [str(node.attrib.get("text") or "")]
            texts.extend(
                str(child.attrib.get("text") or "") for child in node.iter("node")
            )
            if not any(
                cls._matches_instance(text, app_name, instance) for text in texts
            ):
                continue
            match = _BOUNDS_RE.fullmatch(str(node.attrib.get("bounds") or ""))
            if not match:
                continue
            bounds = tuple(int(value) for value in match.groups())
            if node.attrib.get("clickable") == "true":
                return bounds
            fallback = fallback or bounds
        return fallback

    def _select_app_instance(
        self,
        adb_device_id: str,
        app_name: str,
        instance: AppInstance,
    ) -> None:
        root = self._dump_ui(adb_device_id)
        bounds = self._choice_bounds(root, app_name, instance)
        if not bounds:
            label = app_name if instance == "primary" else f"{app_name}(分身)"
            raise RuntimeError(f"双开选择器中未找到目标应用: {label}")
        left, top, right, bottom = bounds
        self._shell(
            adb_device_id,
            ["input", "tap", str((left + right) // 2), str((top + bottom) // 2)],
        )

    def launch(
        self,
        adb_device_id: str,
        app_name: str,
        *,
        instance: AppInstance = "primary",
    ) -> AppLaunchResult:
        package_name = get_package_name(app_name)
        if not package_name:
            return AppLaunchResult(
                ok=False,
                app_name=app_name,
                package_name=None,
                selected_instance=instance,
                error=f"不支持的应用: {app_name}",
            )

        try:
            component = self._resolve_launcher_component(adb_device_id, package_name)
            self._collapse_system_panels(adb_device_id)
            self._shell(
                adb_device_id,
                ["am", "start", "-n", component],
                timeout=15,
            )

            foreground, ready = self._wait_for_package(
                adb_device_id, package_name, attempts=8
            )
            chooser_handled = False
            if not ready and foreground in _RESOLVER_PACKAGES:
                self._select_app_instance(adb_device_id, app_name, instance)
                chooser_handled = True
                foreground, ready = self._wait_for_package(
                    adb_device_id, package_name, attempts=16
                )
            if not ready:
                raise RuntimeError(
                    f"应用未进入前台，当前包名: {foreground or 'unknown'}"
                )
            return AppLaunchResult(
                ok=True,
                app_name=app_name,
                package_name=package_name,
                foreground_package=foreground,
                selected_instance=instance,
                chooser_handled=chooser_handled,
            )
        except Exception as exc:  # noqa: BLE001
            try:
                foreground = self.current_package(adb_device_id)
            except Exception:  # noqa: BLE001
                foreground = None
            return AppLaunchResult(
                ok=False,
                app_name=app_name,
                package_name=package_name,
                foreground_package=foreground,
                selected_instance=instance,
                error=str(exc),
            )


__all__ = ["AdbAppLauncher", "AppInstance", "AppLaunchResult"]
