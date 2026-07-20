"""Registry-driven deterministic search navigation for mobile collectors."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from core.mobile.app_launcher import AdbAppLauncher, AppLaunchResult
from core.mobile.coordinates import resolve_tap
from core.mobile.manager import MobileDeviceManager


CommandRunner = Callable[[list[str], int], subprocess.CompletedProcess[str]]
NavigatorFactory = Callable[[], "SearchNavigator"]


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


@dataclass(frozen=True, slots=True)
class SearchNavigationResult:
    strategy: str
    ok: bool
    mode: str = "deterministic"
    error: str | None = None
    elapsed_ms: int = 0
    metadata: dict[str, str] = field(default_factory=dict)


class SearchNavigator(Protocol):
    strategy: str

    def navigate(
        self,
        device_id: str,
        *,
        app_name: str,
        app_instance: str,
        keyword: str,
    ) -> SearchNavigationResult: ...


class SearchNavigationRegistry:
    """Resolve app-specific navigation without exposing it to pipeline callers."""

    _factories: dict[str, NavigatorFactory] = {}

    @classmethod
    def register(cls, strategy: str, factory: NavigatorFactory) -> None:
        normalized = str(strategy or "").strip()
        if not normalized:
            raise ValueError("搜索导航策略名不能为空")
        cls._factories[normalized] = factory

    @classmethod
    def create(cls, strategy: str) -> SearchNavigator | None:
        factory = cls._factories.get(str(strategy or "").strip())
        return factory() if factory else None


class WechatArticleSearchNavigator:
    """Enter WeChat article search through verified activities and ADB input."""

    strategy = "wechat_copy_link"
    _PACKAGE = "com.tencent.mm"
    _SEARCH_ACTIVITY = "com.tencent.mm.plugin.fts.ui.FTSMainUI"
    _RESULT_ACTIVITY = (
        "com.tencent.mm.plugin.webview.ui.tools.fts.MMFTSSOSHomeWebViewUI"
    )
    _CURRENT_COMPONENT_RE = re.compile(
        r"(?:mCurrentFocus|mFocusedApp)=.*?\s+com\.tencent\.mm/"
        r"(com\.tencent\.mm\.[A-Za-z0-9_.$]+|\.[A-Za-z0-9_.$]+)",
        flags=re.MULTILINE,
    )
    _LAUNCHER_ACTIVITY = "com.tencent.mm.ui.LauncherUI"
    _LAUNCHER_SEARCH_ENTRY = (830, 67)
    _EXACT_QUERY_SUGGESTION = (400, 229)

    def __init__(
        self,
        *,
        manager_factory: Callable[[], MobileDeviceManager] = MobileDeviceManager,
        launcher_factory: Callable[[], AdbAppLauncher] = AdbAppLauncher,
        runner: CommandRunner = _default_command_runner,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._manager_factory = manager_factory
        self._launcher_factory = launcher_factory
        self._runner = runner
        self._sleep = sleep

    def _shell(self, adb_device_id: str, args: list[str], *, timeout: int = 5) -> str:
        try:
            result = self._runner(
                ["-s", adb_device_id, "shell", *args],
                timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"ADB 命令超时: {' '.join(args[:3])}") from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "ADB 命令失败").strip()
            raise RuntimeError(message[:300])
        return result.stdout or ""

    def _current_activity(self, adb_device_id: str) -> str:
        output = self._shell(
            adb_device_id,
            ["dumpsys", "window"],
            timeout=5,
        )
        match = self._CURRENT_COMPONENT_RE.search(output)
        if not match:
            return ""
        activity = match.group(1)
        if activity.startswith("."):
            return f"{self._PACKAGE}{activity}"
        return activity

    def _wait_for_activity(
        self,
        adb_device_id: str,
        expected: str,
        *,
        attempts: int = 5,
        pending: str | None = None,
    ) -> str:
        current = ""
        last_error: Exception | None = None
        for attempt in range(max(1, attempts)):
            try:
                current = self._current_activity(adb_device_id)
                if current == expected:
                    return current
            except Exception as exc:  # noqa: BLE001
                last_error = exc
            if attempt + 1 < attempts:
                self._sleep(0.25)
        if pending and current == pending:
            return current
        if last_error and not current:
            raise RuntimeError(f"微信页面校验失败: {last_error}") from last_error
        raise RuntimeError(
            f"微信页面校验失败，期望 {expected.rsplit('.', 1)[-1]}，"
            f"实际 {current.rsplit('.', 1)[-1] if current else 'unknown'}"
        )

    def _enter_search_activity(self, device, adb_device_id: str) -> str:
        """Normalize Launcher/result states to WeChat's focused search editor."""
        activity = self._current_activity(adb_device_id)
        if activity == self._SEARCH_ACTIVITY:
            return activity
        if activity == self._RESULT_ACTIVITY:
            for _attempt in range(3):
                device.back(delay=0.1)
                self._sleep(0.3)
                activity = self._current_activity(adb_device_id)
                if activity == self._SEARCH_ACTIVITY:
                    return activity
                if activity != self._RESULT_ACTIVITY:
                    break
        elif activity == self._LAUNCHER_ACTIVITY:
            self._tap_normalized(
                device,
                adb_device_id,
                self._LAUNCHER_SEARCH_ENTRY,
            )
            return self._wait_for_activity(adb_device_id, self._SEARCH_ACTIVITY)
        raise RuntimeError(
            "微信当前页面不支持确定性搜索导航: "
            f"{activity.rsplit('.', 1)[-1] if activity else 'unknown'}"
        )

    def _submit_search(self, device, adb_device_id: str) -> tuple[str, str]:
        """提交搜索并校验结果页；建议项未响应时用键盘确定性重试。"""
        self._tap_normalized(
            device,
            adb_device_id,
            self._EXACT_QUERY_SUGGESTION,
        )
        submission = "suggestion_tap"
        for attempt in range(4):
            activity = self._wait_for_activity(
                adb_device_id,
                self._RESULT_ACTIVITY,
                attempts=4,
                pending=self._SEARCH_ACTIVITY,
            )
            if activity == self._RESULT_ACTIVITY:
                return activity, submission
            if attempt >= 3:
                break
            if not device.press_key("enter", delay=0.1):
                raise RuntimeError("微信搜索提交失败: Enter 按键未执行")
            submission = f"enter_retry_{attempt + 1}"
            self._sleep(0.25)
        raise RuntimeError("微信搜索提交失败: 多次提交后仍停留在 FTSMainUI")

    @staticmethod
    def _tap_normalized(device, adb_device_id: str, point: tuple[int, int]) -> None:
        x, y = resolve_tap(
            point[0],
            point[1],
            device_id=adb_device_id,
            coord_space="normalized_1000",
        )
        device.tap(x, y, delay=0.1)

    def navigate(
        self,
        device_id: str,
        *,
        app_name: str,
        app_instance: str,
        keyword: str,
    ) -> SearchNavigationResult:
        started = time.monotonic()
        normalized_keyword = str(keyword or "").strip()
        if not normalized_keyword:
            return SearchNavigationResult(
                strategy=self.strategy,
                ok=False,
                error="搜索关键词为空",
            )

        activity = ""
        try:
            manager = self._manager_factory()
            adb_device_id = manager.resolve_adb_device_id(device_id)
            launch: AppLaunchResult = self._launcher_factory().launch(
                adb_device_id,
                app_name,
                instance="clone" if app_instance == "clone" else "primary",
            )
            if not launch.ok:
                raise RuntimeError(launch.error or f"{app_name}启动失败")

            device = manager.get_device(device_id)
            self._sleep(0.35)
            activity = self._enter_search_activity(device, adb_device_id)

            previous_ime = device.detect_and_set_adb_keyboard()
            restore_error = ""
            try:
                device.clear_text()
                device.type_text(normalized_keyword)
                self._sleep(0.35)
                activity, submission = self._submit_search(
                    device,
                    adb_device_id,
                )
            finally:
                if previous_ime:
                    try:
                        device.restore_keyboard(previous_ime)
                    except Exception as exc:  # noqa: BLE001
                        restore_error = str(exc)[:200]

            metadata = {
                "activity": activity,
                "keyword": normalized_keyword,
                "app_instance": "clone" if app_instance == "clone" else "primary",
                "submission": submission,
            }
            if restore_error:
                metadata["keyboard_restore_error"] = restore_error
            return SearchNavigationResult(
                strategy=self.strategy,
                ok=True,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001
            return SearchNavigationResult(
                strategy=self.strategy,
                ok=False,
                error=str(exc),
                elapsed_ms=int((time.monotonic() - started) * 1000),
                metadata={"activity": activity, "keyword": normalized_keyword},
            )


SearchNavigationRegistry.register(
    WechatArticleSearchNavigator.strategy,
    WechatArticleSearchNavigator,
)


__all__ = [
    "SearchNavigationRegistry",
    "SearchNavigationResult",
    "SearchNavigator",
    "WechatArticleSearchNavigator",
]
