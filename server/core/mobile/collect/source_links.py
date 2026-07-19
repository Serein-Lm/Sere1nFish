"""详情页原文链接提取能力。

采集流水线只依赖策略名和统一结果；应用菜单、ADB、剪贴板限制与页面恢复均由
具体适配器处理。Android 10+ 不允许 shell 直接读取剪贴板，因此默认桥接器把
剪贴板粘贴到系统设置的可访问搜索框，再从 UIAutomator 树读取真实文本。
"""
from __future__ import annotations

import re
import shlex
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, Protocol
from urllib.parse import urlsplit

from core.mobile.coordinates import resolve_swipe, resolve_tap
from core.mobile.manager import MobileDeviceManager


class SourceLinkExtractionError(RuntimeError):
    """原文链接提取失败。"""


@dataclass(frozen=True)
class SourceLinkResult:
    strategy: str
    ok: bool
    url: str | None = None
    error: str | None = None
    elapsed_ms: int = 0
    metadata: dict[str, str] = field(default_factory=dict)


class SourceLinkExtractor(Protocol):
    strategy: str

    def extract(self, device_id: str) -> SourceLinkResult:
        ...


class ClipboardBridge(Protocol):
    def current_package(self, adb_device_id: str) -> str | None:
        ...

    def prime(
        self, adb_device_id: str, marker: str, *, return_package: str
    ) -> bool:
        ...

    def read(self, adb_device_id: str, *, return_package: str) -> str | None:
        ...


@dataclass(frozen=True)
class SourceLinkStrategyInfo:
    strategy: str
    label: str
    description: str


@dataclass(frozen=True)
class _UiNode:
    text: str
    bounds: tuple[int, int, int, int]
    focused: bool
    focusable: bool
    clickable: bool
    score: int

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return ((left + right) // 2, (top + bottom) // 2)


_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_COMPONENT_RE = re.compile(
    r"(?:mCurrentFocus|mFocusedApp)=.*?\s([A-Za-z0-9_.]+)/(?:[A-Za-z0-9_.$]+)"
)
_URL_RE = re.compile(r"https?://[^\s<>\"']+", flags=re.IGNORECASE)


def _parse_bounds(raw: str) -> tuple[int, int, int, int] | None:
    match = _BOUNDS_RE.fullmatch(raw or "")
    if not match:
        return None
    bounds = tuple(int(value) for value in match.groups())
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        return None
    return bounds  # type: ignore[return-value]


def _find_search_editor(xml_text: str) -> _UiNode | None:
    """从不同 Android 厂商的 Settings UI 树中定位搜索输入框。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    candidates: list[_UiNode] = []
    for node in root.iter("node"):
        attrs = node.attrib
        class_name = attrs.get("class", "")
        resource_id = attrs.get("resource-id", "").lower()
        text = attrs.get("text", "")
        content_desc = attrs.get("content-desc", "")
        searchable = "search" in resource_id or "搜索" in text or "搜索" in content_desc
        editable = class_name.endswith("EditText") or class_name.endswith(
            "AutoCompleteTextView"
        )
        if not (editable and searchable):
            continue
        bounds = _parse_bounds(attrs.get("bounds", ""))
        if not bounds:
            continue
        focused = attrs.get("focused") == "true"
        focusable = attrs.get("focusable") == "true"
        clickable = attrs.get("clickable") == "true"
        score = (
            (100 if focused else 0)
            + (30 if focusable else 0)
            + (20 if clickable else 0)
            + (20 if "search_src_text" in resource_id else 0)
            + (10 if attrs.get("package") == "com.android.settings" else 0)
        )
        candidates.append(
            _UiNode(text, bounds, focused, focusable, clickable, score)
        )
    return max(candidates, key=lambda item: item.score, default=None)


def _find_action_node(xml_text: str, labels: set[str]) -> _UiNode | None:
    """Locate a visible menu action by text/content description and bounds."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    normalized_labels = {label.casefold().strip() for label in labels if label.strip()}
    candidates: list[_UiNode] = []
    for node in root.iter("node"):
        attrs = node.attrib
        text = str(attrs.get("text") or "").strip()
        content_desc = str(attrs.get("content-desc") or "").strip()
        combined = " ".join(value for value in (text, content_desc) if value)
        normalized = combined.casefold()
        exact = normalized in normalized_labels
        contains = any(label in normalized for label in normalized_labels)
        if not (exact or contains):
            continue
        bounds = _parse_bounds(attrs.get("bounds", ""))
        if not bounds:
            continue
        focused = attrs.get("focused") == "true"
        focusable = attrs.get("focusable") == "true"
        clickable = attrs.get("clickable") == "true"
        score = (100 if exact else 60) + (30 if clickable else 0)
        candidates.append(
            _UiNode(combined, bounds, focused, focusable, clickable, score)
        )
    return max(candidates, key=lambda item: item.score, default=None)


def _extract_http_url(raw: str | None, *, allowed_hosts: set[str]) -> str | None:
    if not raw or len(raw) > 8192:
        return None
    match = _URL_RE.search(raw.strip())
    if not match:
        return None
    url = match.group(0).rstrip("),.;，。；）]")
    if len(url) > 4096:
        return None
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in allowed_hosts:
        return None
    return url


CommandRunner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


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


class SettingsSearchClipboardBridge:
    """借助系统设置搜索框安全读取/写入 Android 剪贴板。"""

    def __init__(
        self,
        *,
        runner: CommandRunner = _default_command_runner,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._runner = runner
        self._sleep = sleep
        self._editor_centers: dict[str, tuple[int, int]] = {}

    def _adb(self, args: list[str], *, timeout: int = 10) -> str:
        try:
            result = self._runner(args, timeout)
        except subprocess.TimeoutExpired as exc:
            raise SourceLinkExtractionError(f"ADB 命令超时: {' '.join(args[-3:])}") from exc
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "ADB 命令失败").strip()
            raise SourceLinkExtractionError(error[:300])
        return result.stdout or ""

    def _shell(
        self, adb_device_id: str, args: list[str], *, timeout: int = 10
    ) -> str:
        return self._adb(["-s", adb_device_id, "shell", *args], timeout=timeout)

    def current_package(self, adb_device_id: str) -> str | None:
        output = self._shell(adb_device_id, ["dumpsys", "window"], timeout=8)
        match = _COMPONENT_RE.search(output)
        return match.group(1) if match else None

    def _dump_ui(self, adb_device_id: str) -> str:
        output = self._adb(
            [
                "-s",
                adb_device_id,
                "exec-out",
                "uiautomator",
                "dump",
                "--compressed",
                "/dev/tty",
            ],
            timeout=10,
        )
        start = output.find("<?xml")
        end = output.rfind("</hierarchy>")
        if start < 0 or end < 0:
            raise SourceLinkExtractionError("UIAutomator 未返回有效界面树")
        return output[start : end + len("</hierarchy>")]

    def _restore_package(self, adb_device_id: str, target_package: str) -> bool:
        try:
            self._shell(
                adb_device_id,
                ["am", "force-stop", "com.android.settings"],
                timeout=8,
            )
            self._sleep(0.25)
        except Exception:
            pass
        for _ in range(6):
            try:
                if self.current_package(adb_device_id) == target_package:
                    return True
                self._shell(adb_device_id, ["input", "keyevent", "4"])
                self._sleep(0.35)
            except Exception:
                break
        try:
            return self.current_package(adb_device_id) == target_package
        except Exception:
            return False

    def _scroll_settings_to_top(self, adb_device_id: str) -> None:
        sx, sy, ex, ey = resolve_swipe(
            500,
            200,
            500,
            875,
            device_id=adb_device_id,
            coord_space="normalized_1000",
        )
        command = (
            "for i in 1 2 3; do "
            f"input swipe {sx} {sy} {ex} {ey} 250; "
            "done"
        )
        self._shell(adb_device_id, ["sh", "-c", shlex.quote(command)])

    def _focus_search_editor(self, adb_device_id: str) -> None:
        self._shell(
            adb_device_id,
            ["am", "start", "-a", "android.settings.SETTINGS"],
            timeout=12,
        )
        self._sleep(0.6)
        self._scroll_settings_to_top(adb_device_id)
        self._sleep(0.35)

        center = self._editor_centers.get(adb_device_id)
        if center:
            self._shell(
                adb_device_id,
                ["input", "tap", str(center[0]), str(center[1])],
            )
            self._sleep(0.35)
            return

        editor = _find_search_editor(self._dump_ui(adb_device_id))
        if not editor:
            raise SourceLinkExtractionError("系统设置中未找到可访问的搜索输入框")
        if editor.focused:
            return
        self._editor_centers[adb_device_id] = editor.center
        x, y = editor.center
        self._shell(adb_device_id, ["input", "tap", str(x), str(y)])
        self._sleep(0.35)

    def _select_all(self, adb_device_id: str) -> None:
        self._shell(
            adb_device_id,
            ["input", "keycombination", "113", "29"],
        )

    def _clear_editor(self, adb_device_id: str) -> None:
        self._select_all(adb_device_id)
        self._shell(adb_device_id, ["input", "keyevent", "67"])

    def _read_editor(self, adb_device_id: str) -> str:
        editor = _find_search_editor(self._dump_ui(adb_device_id))
        return editor.text.strip() if editor else ""

    def _write_and_verify_marker(self, adb_device_id: str, marker: str) -> bool:
        safe_marker = shlex.quote(marker)
        command = (
            "input keycombination 113 29; "
            "input keyevent 67; "
            f"input text {safe_marker}; "
            "input keycombination 113 29; "
            "input keyevent 278; "
            "input keycombination 113 29; "
            "input keyevent 67; "
            "input keyevent 279"
        )
        self._shell(adb_device_id, ["sh", "-c", shlex.quote(command)])
        self._sleep(0.2)
        return self._read_editor(adb_device_id) == marker

    def prime(
        self, adb_device_id: str, marker: str, *, return_package: str
    ) -> bool:
        """用随机标记覆盖剪贴板并回读，避免把历史链接误判为本次结果。"""
        try:
            self._focus_search_editor(adb_device_id)
            verified = self._write_and_verify_marker(adb_device_id, marker)
            self._clear_editor(adb_device_id)
            return verified
        finally:
            self._restore_package(adb_device_id, return_package)

    def read(self, adb_device_id: str, *, return_package: str) -> str | None:
        try:
            self._focus_search_editor(adb_device_id)
            self._shell(
                adb_device_id,
                [
                    "sh",
                    "-c",
                    shlex.quote(
                        "input keycombination 113 29; "
                        "input keyevent 67; input keyevent 279"
                    ),
                ],
            )
            self._sleep(0.3)
            value = self._read_editor(adb_device_id)
            self._clear_editor(adb_device_id)
            return value or None
        finally:
            self._restore_package(adb_device_id, return_package)


class AdbUiHierarchyReader:
    """Small UIAutomator adapter used by app-specific interaction strategies."""

    def __init__(self, runner: CommandRunner = _default_command_runner) -> None:
        self._runner = runner

    def dump(self, adb_device_id: str) -> str:
        try:
            result = self._runner(
                [
                    "-s",
                    adb_device_id,
                    "exec-out",
                    "uiautomator",
                    "dump",
                    "--compressed",
                    "/dev/tty",
                ],
                10,
            )
        except subprocess.TimeoutExpired as exc:
            raise SourceLinkExtractionError("UIAutomator 读取超时") from exc
        if result.returncode != 0:
            raise SourceLinkExtractionError(
                (result.stderr or result.stdout or "UIAutomator 读取失败").strip()[:300]
            )
        output = result.stdout or ""
        start = output.find("<?xml")
        end = output.rfind("</hierarchy>")
        if start < 0 or end < 0:
            raise SourceLinkExtractionError("UIAutomator 未返回有效界面树")
        return output[start : end + len("</hierarchy>")]


class WechatCopyLinkExtractor:
    """通过微信文章分享菜单的“复制链接”提取原文 URL。"""

    strategy = "wechat_copy_link"
    _WECHAT_PACKAGE = "com.tencent.mm"
    _ALLOWED_HOSTS = {"mp.weixin.qq.com"}
    _MENU_TAP = (926, 71)
    _COPY_LINK_TAP = (926, 788)

    def __init__(
        self,
        *,
        manager_factory: Callable[[], MobileDeviceManager] = MobileDeviceManager,
        clipboard: ClipboardBridge | None = None,
        ui_reader: AdbUiHierarchyReader | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._manager_factory = manager_factory
        self._clipboard = clipboard or SettingsSearchClipboardBridge(sleep=sleep)
        self._ui_reader = ui_reader or AdbUiHierarchyReader()
        self._sleep = sleep

    def _locate_action(
        self,
        adb_device_id: str,
        labels: set[str],
    ) -> tuple[int, int] | None:
        try:
            node = _find_action_node(self._ui_reader.dump(adb_device_id), labels)
            return node.center if node else None
        except Exception:
            return None

    def extract(self, device_id: str) -> SourceLinkResult:
        started = time.monotonic()
        adb_device_id = device_id
        try:
            manager = self._manager_factory()
            adb_device_id = manager.resolve_adb_device_id(device_id)
            current_package = self._clipboard.current_package(adb_device_id)
            if current_package != self._WECHAT_PACKAGE:
                raise SourceLinkExtractionError("当前详情页不在微信前台")

            marker = f"SERE1NFISH_CLIP_{uuid.uuid4().hex[:12]}"
            if not self._clipboard.prime(
                adb_device_id, marker, return_package=self._WECHAT_PACKAGE
            ):
                raise SourceLinkExtractionError("剪贴板标记回读失败")

            device = manager.get_device(device_id)
            menu_position = self._locate_action(
                adb_device_id,
                {"更多", "更多功能", "more", "more options"},
            )
            menu_locator = "uiautomator"
            if menu_position is None:
                menu_position = resolve_tap(
                    *self._MENU_TAP,
                    device_id=adb_device_id,
                    coord_space="normalized_1000",
                )
                menu_locator = "coordinate_fallback"
            menu_x, menu_y = menu_position
            device.tap(menu_x, menu_y, delay=0.1)
            self._sleep(0.8)
            copy_position = self._locate_action(
                adb_device_id,
                {"复制链接", "复制原文链接", "copy link"},
            )
            copy_locator = "uiautomator"
            if copy_position is None:
                copy_position = resolve_tap(
                    *self._COPY_LINK_TAP,
                    device_id=adb_device_id,
                    coord_space="normalized_1000",
                )
                copy_locator = "coordinate_fallback"
            copy_x, copy_y = copy_position
            device.tap(copy_x, copy_y, delay=0.1)
            self._sleep(0.6)

            clipboard_text = self._clipboard.read(
                adb_device_id, return_package=self._WECHAT_PACKAGE
            )
            if clipboard_text == marker:
                raise SourceLinkExtractionError("微信未更新剪贴板")
            url = _extract_http_url(
                clipboard_text, allowed_hosts=self._ALLOWED_HOSTS
            )
            if not url:
                raise SourceLinkExtractionError("剪贴板中没有有效的微信公众号链接")
            return SourceLinkResult(
                strategy=self.strategy,
                ok=True,
                url=url,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                metadata={
                    "adb_device_id": adb_device_id,
                    "host": urlsplit(url).hostname or "",
                    "menu_locator": menu_locator,
                    "copy_locator": copy_locator,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return SourceLinkResult(
                strategy=self.strategy,
                ok=False,
                error=str(exc),
                elapsed_ms=int((time.monotonic() - started) * 1000),
                metadata={"adb_device_id": adb_device_id},
            )


ExtractorFactory = Callable[[], SourceLinkExtractor]
_EXTRACTORS: dict[str, tuple[SourceLinkStrategyInfo, ExtractorFactory]] = {}


def register_source_link_extractor(
    strategy: str,
    *,
    label: str,
    description: str,
    factory: ExtractorFactory,
) -> None:
    if not strategy or strategy == "none":
        raise ValueError("策略名不能为空或 none")
    _EXTRACTORS[strategy] = (
        SourceLinkStrategyInfo(strategy, label, description),
        factory,
    )


def list_source_link_strategies() -> list[SourceLinkStrategyInfo]:
    disabled = SourceLinkStrategyInfo("none", "不提取", "仅使用视觉模型可见的链接")
    return [disabled, *[item[0] for item in _EXTRACTORS.values()]]


def extract_source_link(device_id: str, strategy: str) -> SourceLinkResult:
    if not strategy or strategy == "none":
        return SourceLinkResult(strategy="none", ok=False, error="未启用原文链接提取")
    registered = _EXTRACTORS.get(strategy)
    if not registered:
        return SourceLinkResult(
            strategy=strategy, ok=False, error="未知的原文链接提取策略"
        )
    return registered[1]().extract(device_id)


register_source_link_extractor(
    WechatCopyLinkExtractor.strategy,
    label="微信文章复制链接",
    description="从微信文章右上角菜单复制 mp.weixin.qq.com 原始链接",
    factory=WechatCopyLinkExtractor,
)
