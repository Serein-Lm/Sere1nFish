import asyncio
import subprocess
from types import SimpleNamespace

from core.mobile.app_launcher import AppLaunchResult
from core.mobile.collect.search_navigation import (
    SearchNavigationRegistry,
    WechatArticleSearchNavigator,
)


class _FakeDevice:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def tap(self, x: int, y: int, delay=None) -> None:
        self.events.append(("tap", x, y))

    def detect_and_set_adb_keyboard(self) -> str:
        self.events.append(("keyboard", "set"))
        return "original/.Ime"

    def clear_text(self) -> None:
        self.events.append(("clear",))

    def type_text(self, text: str) -> None:
        self.events.append(("type", text))

    def restore_keyboard(self, ime: str) -> None:
        self.events.append(("keyboard", "restore", ime))

    def back(self, delay=None) -> None:
        self.events.append(("back",))

    def press_key(self, key: str, delay=None) -> bool:
        self.events.append(("press", key))
        return True


class _FakeManager:
    def __init__(self, device: _FakeDevice) -> None:
        self.device = device

    def resolve_adb_device_id(self, device_id: str) -> str:
        assert device_id == "device-a"
        return "10.0.0.2:5555"

    def get_device(self, device_id: str) -> _FakeDevice:
        assert device_id == "device-a"
        return self.device


class _FakeLauncher:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def launch(self, adb_device_id: str, app_name: str, *, instance: str):
        self.calls.append((adb_device_id, app_name, instance))
        return AppLaunchResult(
            ok=True,
            app_name=app_name,
            package_name="com.tencent.mm",
            foreground_package="com.tencent.mm",
            selected_instance=instance,
        )


def _activity_runner(activities: list[str]):
    pending = list(activities)

    def run(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        assert args[-2:] == ["dumpsys", "window"]
        activity = pending.pop(0)
        output = (
            "mCurrentFocus=Window{1 u0 "
            f"com.tencent.mm/{activity}" + "}"
        )
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    return run


def test_wechat_search_navigator_uses_verified_activity_path(monkeypatch) -> None:
    from core.mobile.collect import search_navigation

    device = _FakeDevice()
    launcher = _FakeLauncher()
    monkeypatch.setattr(
        search_navigation,
        "resolve_tap",
        lambda x, y, **_kwargs: (x, y),
    )
    navigator = WechatArticleSearchNavigator(
        manager_factory=lambda: _FakeManager(device),
        launcher_factory=lambda: launcher,
        runner=_activity_runner(
            [
                "com.tencent.mm.ui.LauncherUI",
                "com.tencent.mm.plugin.fts.ui.FTSMainUI",
                "com.tencent.mm.plugin.webview.ui.tools.fts.MMFTSSOSHomeWebViewUI",
            ]
        ),
        sleep=lambda _seconds: None,
    )

    result = navigator.navigate(
        "device-a",
        app_name="微信",
        app_instance="primary",
        keyword="安徽广播电视台 招标",
    )

    assert result.ok is True
    assert result.metadata["activity"].endswith("MMFTSSOSHomeWebViewUI")
    assert result.metadata["submission"] == "suggestion_tap"
    assert launcher.calls == [("10.0.0.2:5555", "微信", "primary")]
    assert device.events == [
        ("tap", 830, 67),
        ("keyboard", "set"),
        ("clear",),
        ("type", "安徽广播电视台 招标"),
        ("tap", 400, 229),
        ("keyboard", "restore", "original/.Ime"),
    ]


def test_wechat_search_navigator_reports_activity_mismatch(monkeypatch) -> None:
    from core.mobile.collect import search_navigation

    device = _FakeDevice()
    monkeypatch.setattr(
        search_navigation,
        "resolve_tap",
        lambda x, y, **_kwargs: (x, y),
    )
    navigator = WechatArticleSearchNavigator(
        manager_factory=lambda: _FakeManager(device),
        launcher_factory=_FakeLauncher,
        runner=_activity_runner(["com.tencent.mm.ui.LauncherUI"] * 6),
        sleep=lambda _seconds: None,
    )

    result = navigator.navigate(
        "device-a",
        app_name="微信",
        app_instance="primary",
        keyword="安徽广播电视台",
    )

    assert result.ok is False
    assert "微信页面校验失败" in str(result.error)
    assert not any(event[0] == "type" for event in device.events)


def test_wechat_search_navigator_reuses_result_search_input(monkeypatch) -> None:
    from core.mobile.collect import search_navigation

    device = _FakeDevice()
    monkeypatch.setattr(
        search_navigation,
        "resolve_tap",
        lambda x, y, **_kwargs: (x, y),
    )
    navigator = WechatArticleSearchNavigator(
        manager_factory=lambda: _FakeManager(device),
        launcher_factory=_FakeLauncher,
        runner=_activity_runner(
            [
                "com.tencent.mm.plugin.webview.ui.tools.fts.MMFTSSOSHomeWebViewUI",
                "com.tencent.mm.plugin.webview.ui.tools.fts.MMFTSSOSHomeWebViewUI",
                "com.tencent.mm.plugin.fts.ui.FTSMainUI",
                "com.tencent.mm.plugin.webview.ui.tools.fts.MMFTSSOSHomeWebViewUI",
            ]
        ),
        sleep=lambda _seconds: None,
    )

    result = navigator.navigate(
        "device-a",
        app_name="微信",
        app_instance="primary",
        keyword="AHTV 招标",
    )

    assert result.ok is True
    assert device.events[:2] == [("back",), ("back",)]
    assert ("type", "AHTV 招标") in device.events


def test_wechat_search_navigator_retries_enter_when_suggestion_does_not_submit(
    monkeypatch,
) -> None:
    from core.mobile.collect import search_navigation

    device = _FakeDevice()
    monkeypatch.setattr(
        search_navigation,
        "resolve_tap",
        lambda x, y, **_kwargs: (x, y),
    )
    search_activity = "com.tencent.mm.plugin.fts.ui.FTSMainUI"
    result_activity = (
        "com.tencent.mm.plugin.webview.ui.tools.fts.MMFTSSOSHomeWebViewUI"
    )
    navigator = WechatArticleSearchNavigator(
        manager_factory=lambda: _FakeManager(device),
        launcher_factory=_FakeLauncher,
        runner=_activity_runner(
            [search_activity, *([search_activity] * 4), result_activity]
        ),
        sleep=lambda _seconds: None,
    )

    result = navigator.navigate(
        "device-a",
        app_name="微信",
        app_instance="primary",
        keyword="安徽广播电视台 招标",
    )

    assert result.ok is True
    assert result.metadata["submission"] == "enter_retry_1"
    assert ("press", "enter") in device.events


def test_registered_navigation_runs_without_visual_agent(monkeypatch) -> None:
    from core.mobile.collect import pipeline
    from core.mobile.collect.search_navigation import SearchNavigationResult

    class _Navigator:
        def navigate(self, device_id: str, **kwargs):
            return SearchNavigationResult(
                strategy="wechat_copy_link",
                ok=True,
                metadata={"device_id": device_id, "keyword": kwargs["keyword"]},
            )

    monkeypatch.setattr(
        SearchNavigationRegistry,
        "create",
        lambda _strategy: _Navigator(),
    )

    result = asyncio.run(
        pipeline._run_registered_search_navigation(
            "device-a",
            strategy="wechat_copy_link",
            app_name="微信",
            app_instance="primary",
            keyword="安徽广播电视台 招标",
        )
    )

    assert result is not None
    assert result.ok is True
    assert result.metadata["keyword"] == "安徽广播电视台 招标"


def _collect_navigation_context() -> SimpleNamespace:
    return SimpleNamespace(
        state={
            "stop_event": asyncio.Event(),
            "device_id": "device-a",
            "app_name": "微信",
            "project_id": "project-a",
            "run_task_id": "run-a",
            "direct_launch_app": True,
            "direct_app_ready": False,
            "source_link_strategy": "wechat_copy_link",
            "app_instance": "primary",
            "search_hint": "选择文章结果",
            "owner": "tester",
        },
        logger=SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    )


def test_collect_stage_skips_visual_agent_after_registered_success(monkeypatch) -> None:
    from core.mobile.collect import pipeline
    from core.mobile.collect.candidate_policy import CandidatePolicyRegistry
    from core.mobile.collect.search_navigation import SearchNavigationResult

    async def deterministic(*_args, **_kwargs):
        return SearchNavigationResult(strategy="wechat_copy_link", ok=True)

    async def visual(*_args, **_kwargs):
        raise AssertionError("确定性导航成功后不应调用视觉 Agent")

    monkeypatch.setattr(pipeline, "_run_registered_search_navigation", deterministic)
    monkeypatch.setattr(pipeline, "_run_search_navigation", visual)
    monkeypatch.setattr(pipeline, "obs_log", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        pipeline._CollectStage()._navigate_to_search_results(
            _collect_navigation_context(),
            keyword="安徽广播电视台 招标",
            item_id="item-a",
            candidate_policy=CandidatePolicyRegistry.resolve("wechat_copy_link"),
        )
    )

    assert result is True


def test_collect_stage_fallback_prompt_keeps_article_result_scope(monkeypatch) -> None:
    from core.mobile.collect import pipeline
    from core.mobile.collect.candidate_policy import CandidatePolicyRegistry
    from core.mobile.collect.search_navigation import SearchNavigationResult

    captured = {}

    async def deterministic(*_args, **_kwargs):
        return SearchNavigationResult(
            strategy="wechat_copy_link",
            ok=False,
            error="activity changed",
        )

    async def visual(_device_id, goal, **_kwargs):
        captured["goal"] = goal
        return True

    monkeypatch.setattr(pipeline, "_run_registered_search_navigation", deterministic)
    monkeypatch.setattr(pipeline, "_run_search_navigation", visual)
    monkeypatch.setattr(
        pipeline,
        "_do_launch_app",
        lambda *_args, **_kwargs: AppLaunchResult(
            ok=True,
            app_name="微信",
            package_name="com.tencent.mm",
            foreground_package="com.tencent.mm",
        ),
    )
    monkeypatch.setattr(pipeline, "obs_log", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        pipeline._CollectStage()._navigate_to_search_results(
            _collect_navigation_context(),
            keyword="安徽广播电视台 招标",
            item_id="item-a",
            candidate_policy=CandidatePolicyRegistry.resolve("wechat_copy_link"),
        )
    )

    assert result is True
    assert "停留在“全部”结果页" in captured["goal"]
    assert "不得切换到账号、视频" in captured["goal"]
