"""手机详情页原文链接提取能力测试。"""
from __future__ import annotations

import asyncio


def test_find_settings_search_editor_and_decode_text():
    from core.mobile.collect.source_links import _find_search_editor

    xml = """<?xml version='1.0' encoding='UTF-8'?>
    <hierarchy>
      <node class="android.widget.AutoCompleteTextView"
            package="com.android.settings"
            resource-id="com.android.settings:id/search_src_text"
            text="https://mp.weixin.qq.com/s/demo?a=1&amp;b=2"
            content-desc="" clickable="true" focusable="true" focused="true"
            bounds="[168,178][768,268]" />
    </hierarchy>"""

    editor = _find_search_editor(xml)

    assert editor is not None
    assert editor.focused is True
    assert editor.center == (468, 223)
    assert editor.text == "https://mp.weixin.qq.com/s/demo?a=1&b=2"


def test_extract_http_url_rejects_non_wechat_hosts():
    from core.mobile.collect.source_links import _extract_http_url

    allowed = {"mp.weixin.qq.com"}
    assert (
        _extract_http_url(
            "原文 https://mp.weixin.qq.com/s/demo?a=1&b=2", allowed_hosts=allowed
        )
        == "https://mp.weixin.qq.com/s/demo?a=1&b=2"
    )
    assert _extract_http_url("https://example.com/s/demo", allowed_hosts=allowed) is None
    assert _extract_http_url("javascript:alert(1)", allowed_hosts=allowed) is None


class _FakeDevice:
    def __init__(self) -> None:
        self.taps: list[tuple[int, int]] = []

    def tap(self, x: int, y: int, delay=None) -> None:
        self.taps.append((x, y))


class _FakeManager:
    def __init__(self, device: _FakeDevice) -> None:
        self.device = device

    def resolve_adb_device_id(self, device_id: str) -> str:
        assert device_id == "stable-device"
        return "10.0.0.2:5555"

    def get_device(self, device_id: str) -> _FakeDevice:
        assert device_id == "stable-device"
        return self.device


class _FakeClipboard:
    def __init__(self, value: str | None) -> None:
        self.value = value
        self.marker = ""

    def current_package(self, adb_device_id: str) -> str:
        return "com.tencent.mm"

    def prime(self, adb_device_id: str, marker: str, *, return_package: str) -> bool:
        self.marker = marker
        return True

    def read(self, adb_device_id: str, *, return_package: str) -> str | None:
        return self.marker if self.value == "__marker__" else self.value


def test_wechat_copy_link_extractor_uses_adapter_and_validates_url(monkeypatch):
    from core.mobile.collect import source_links as links

    device = _FakeDevice()
    manager = _FakeManager(device)
    clipboard = _FakeClipboard("https://mp.weixin.qq.com/s/real-link")
    monkeypatch.setattr(links, "resolve_tap", lambda x, y, **kwargs: (x, y))

    extractor = links.WechatCopyLinkExtractor(
        manager_factory=lambda: manager,
        clipboard=clipboard,
        sleep=lambda _: None,
    )
    result = extractor.extract("stable-device")

    assert result.ok is True
    assert result.url == "https://mp.weixin.qq.com/s/real-link"
    assert device.taps == [
        links.WechatCopyLinkExtractor._MENU_TAP,
        links.WechatCopyLinkExtractor._COPY_LINK_TAP,
    ]


def test_wechat_copy_link_extractor_rejects_stale_clipboard(monkeypatch):
    from core.mobile.collect import source_links as links

    device = _FakeDevice()
    manager = _FakeManager(device)
    clipboard = _FakeClipboard("__marker__")
    monkeypatch.setattr(links, "resolve_tap", lambda x, y, **kwargs: (x, y))

    result = links.WechatCopyLinkExtractor(
        manager_factory=lambda: manager,
        clipboard=clipboard,
        sleep=lambda _: None,
    ).extract("stable-device")

    assert result.ok is False
    assert result.url is None
    assert "未更新剪贴板" in (result.error or "")


def test_source_link_strategy_registry_exposes_wechat_adapter():
    from core.mobile.collect.source_links import list_source_link_strategies

    strategies = {item.strategy: item for item in list_source_link_strategies()}
    assert "none" in strategies
    assert strategies["wechat_copy_link"].label == "微信文章复制链接"


def test_wechat_preset_and_api_expose_registered_strategy():
    from api.models.mobile_collect import CollectTaskDef
    from api.routers.mobile_collect import source_link_strategies
    from core.mobile.collect.presets import PRESETS

    preset = next(item for item in PRESETS if item["preset_id"] == "wechat_official")
    assert preset["task"]["source_link_strategy"] == "wechat_copy_link"

    payload = {
        **preset["task"],
        "device_id": "stable-device",
    }
    assert CollectTaskDef(**payload).source_link_strategy == "wechat_copy_link"

    result = asyncio.run(source_link_strategies())
    assert any(
        item["strategy"] == "wechat_copy_link" for item in result["items"]
    )
