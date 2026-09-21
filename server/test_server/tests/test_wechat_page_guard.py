"""结果页守卫与「跳出微信」修复的行为测试。

覆盖 core.mobile.collect.wechat_page_guard 的安全规则：
- 已在结果页 → 不按返回
- 文章/菜单等中间页 → 有界按返回回到结果页
- 搜索输入页 / 微信主界面 → 绝不按返回（这是跳出微信的根源）
- 微信包外 → 绝不按返回
- detail/keyword 阶段集成：守卫失败时跳过点击并提前结束关键词
"""
from __future__ import annotations

import asyncio
import subprocess

from core.mobile.collect.wechat_page_guard import (
    LAUNCHER_ACTIVITY,
    RESULT_ACTIVITY,
    SEARCH_ACTIVITY,
    WECHAT_PACKAGE,
    ensure_wechat_results_page,
    read_wechat_component,
)


def _fake_runner(components):
    """components: list[(package, activity)] — 每次读取弹出下一个；耗尽后停在最后一个。"""
    state = {"index": 0}

    def _run(args, timeout):
        idx = min(state["index"], len(components) - 1)
        state["index"] += 1
        package, activity = components[idx]
        if package == "":
            focus = "  mCurrentFocus=Window{abc com.android.systemui}"
        else:
            focus = "  mCurrentFocus=Window{abc " + package + "/" + activity + "}"
        output = "WINDOW MANAGER WINDOWS (dumpsys window)\n" + focus + " fd=123\n"
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=output,
            stderr="",
        )

    return _run


class _NoSleep:
    def __call__(self, _seconds):
        return None


def test_read_wechat_component_parses_focus():
    runner = _fake_runner([(WECHAT_PACKAGE, RESULT_ACTIVITY)])
    assert read_wechat_component("dev", runner=runner) == (
        WECHAT_PACKAGE,
        RESULT_ACTIVITY,
    )


def test_guard_already_on_results_never_presses_back():
    backs: list[str] = []
    runner = _fake_runner([(WECHAT_PACKAGE, RESULT_ACTIVITY)])
    result = ensure_wechat_results_page(
        "dev",
        runner=runner,
        back_action=lambda device_id: backs.append(device_id),
        sleep=_NoSleep(),
    )
    assert result.restored is True
    assert result.action == "already_results"
    assert result.backs == 0
    assert backs == []


def test_guard_backs_through_article_to_results():
    backs: list[str] = []
    runner = _fake_runner(
        [
            (WECHAT_PACKAGE, "com.tencent.mm.plugin.webview.ui.tools.WebViewUI"),
            (WECHAT_PACKAGE, RESULT_ACTIVITY),
        ]
    )
    result = ensure_wechat_results_page(
        "dev",
        runner=runner,
        back_action=lambda device_id: backs.append(device_id),
        sleep=_NoSleep(),
    )
    assert result.restored is True
    assert result.action == "backed_to_results"
    assert result.backs == 1
    assert backs == ["dev"]


def test_guard_never_backs_at_search_input_page():
    """搜索输入页再按返回会退出搜索流程——守卫必须停手。"""
    backs: list[str] = []
    runner = _fake_runner([(WECHAT_PACKAGE, SEARCH_ACTIVITY)])
    result = ensure_wechat_results_page(
        "dev",
        runner=runner,
        back_action=lambda device_id: backs.append(device_id),
        sleep=_NoSleep(),
    )
    assert result.restored is False
    assert result.action == "stopped_at_search_input"
    assert result.backs == 0
    assert backs == []


def test_guard_never_backs_at_wechat_home():
    """微信主界面再按返回会退出微信——守卫必须停手。"""
    backs: list[str] = []
    runner = _fake_runner([(WECHAT_PACKAGE, LAUNCHER_ACTIVITY)])
    result = ensure_wechat_results_page(
        "dev",
        runner=runner,
        back_action=lambda device_id: backs.append(device_id),
        sleep=_NoSleep(),
    )
    assert result.restored is False
    assert result.action == "stopped_at_wechat_home"
    assert result.backs == 0
    assert backs == []


def test_guard_never_backs_outside_wechat():
    """已被带出微信（桌面/其他应用）时绝不在其它应用里按返回。"""
    backs: list[str] = []
    runner = _fake_runner([("com.android.chrome", "org.chromium.chrome.browser.ChromeTabbedActivity")])
    result = ensure_wechat_results_page(
        "dev",
        runner=runner,
        back_action=lambda device_id: backs.append(device_id),
        sleep=_NoSleep(),
    )
    assert result.restored is False
    assert result.action == "left_wechat"
    assert result.backs == 0
    assert backs == []


def test_guard_backs_are_bounded():
    backs: list[str] = []
    runner = _fake_runner([(WECHAT_PACKAGE, "com.tencent.mm.plugin.webview.ui.tools.WebViewUI")])
    result = ensure_wechat_results_page(
        "dev",
        runner=runner,
        back_action=lambda device_id: backs.append(device_id),
        sleep=_NoSleep(),
        max_backs=3,
    )
    assert result.restored is False
    assert result.backs == 3
    assert len(backs) == 3


def test_guard_uses_adb_keyevent_when_no_action_injected():
    """未注入 back_action 时走 adb shell input keyevent 4 分支（命令被记录）。"""
    commands: list[list[str]] = []

    def runner(args, timeout):
        commands.append(args)
        if "dumpsys" in args:
            backs_done = any("keyevent" in c for c in commands)
            activity = (
                RESULT_ACTIVITY
                if backs_done
                else "com.tencent.mm.plugin.webview.ui.tools.WebViewUI"
            )
            output = (
                "  mCurrentFocus=Window{ abc "
                f"{WECHAT_PACKAGE}/{activity}}}"
            )
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=output, stderr=""
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    result = ensure_wechat_results_page(
        "dev",
        runner=runner,
        sleep=_NoSleep(),
    )
    assert result.restored is True
    assert result.backs == 1
    assert any("keyevent" in c and "4" in c for c in commands)


# ── detail / keyword 阶段集成 ──────────────────────────────


class _DetailContext:
    def __init__(self, state=None):
        import logging

        self.logger = logging.getLogger("guard-test")
        self.state = state or {
            "db": object(),
            "stop_event": asyncio.Event(),
            "device_id": "dev",
            "app_name": "微信",
            "project_id": "project",
            "run_task_id": "run",
            "task_def_id": "task",
            "dry_run": True,
            "source_link_strategy": "none",
            "detail_max_swipes": 0,
            "swipe_interval": 0.01,
            "extract_fields": [],
            "results_page_lost": False,
        }

    async def emit(self, *_args, **_kwargs):
        return None


def _detail_runner(guard):
    from core.mobile.collect.detail_stage import MobileDetailStageRunner

    async def _capture_save(*_args, **_kwargs):
        return "QUJD", "shot", "/shot"

    async def _verifier(*_args, **_kwargs):
        return {"page_kind": "article", "visible_title": "标题"}

    async def _ingestor(*_args, **_kwargs):
        return {"ok": False}

    async def _detail_analyzer(*_args, **_kwargs):
        return None

    return MobileDetailStageRunner(
        capture_save=_capture_save,
        capture_verification=_verifier,
        tap_action=lambda *a, **k: taps.append(a),
        back_action=lambda *a, **k: backs.append(a),
        swipe_action=lambda *a, **k: None,
        scroll_top_action=lambda *a, **k: None,
        image_signature=lambda _img: None,
        images_similar=lambda *_a, **_k: False,
        source_link_extractor=lambda *a, **k: None,
        source_ingestor=_ingestor,
        detail_verifier=_verifier,
        detail_analyzer=_detail_analyzer,
        observer=lambda *a, **k: events.append((a, k.get("event"))),
        ingest_timeout_seconds=1,
        ensure_results_page=guard,
    )


taps: list = []
backs: list = []
events: list = []


def test_detail_stage_skips_tap_when_page_lost_and_marks_flag():
    taps.clear()
    backs.clear()
    events.clear()
    runner = _detail_runner(
        lambda device_id: {
            "restored": False,
            "action": "stopped_at_search_input",
            "backs": 0,
        }
    )
    context = _DetailContext()
    candidate = {
        "tap_x": 100,
        "tap_y": 200,
        "score": 90,
        "subject_match": 90,
        "fields": {"title": "标题", "account": "公众号"},
    }
    accepted = asyncio.new_event_loop().run_until_complete(
        runner.run(context, "关键词", candidate, None)
    )
    assert accepted is False
    assert taps == []  # 不在结果页，绝不点击候选
    assert backs == []  # 守卫生效时不再盲按返回
    assert context.state["results_page_lost"] is True
    assert any(e[1] == "collect_detail_skip_page_lost" for e in events)


def test_detail_stage_restores_with_guard_when_page_recoverable():
    taps.clear()
    backs.clear()
    events.clear()
    guard_results = iter(
        [
            {"restored": True, "action": "already_results", "backs": 0},
            {"restored": True, "action": "backed_to_results", "backs": 1},
        ]
    )
    runner = _detail_runner(lambda device_id: next(guard_results))
    context = _DetailContext()
    candidate = {
        "tap_x": 100,
        "tap_y": 200,
        "score": 90,
        "subject_match": 90,
        "fields": {"title": "标题", "account": "公众号"},
    }
    accepted = asyncio.new_event_loop().run_until_complete(
        runner.run(context, "关键词", candidate, None)
    )
    assert taps == [("dev", 100, 200)]  # 在结果页才点击
    assert backs == []
    assert context.state["results_page_lost"] is False
    assert any(e[1] == "collect_detail_restore" for e in events)


def test_keyword_stage_stops_scanning_after_page_lost():
    """结果页丢失后，关键词阶段立即结束屏幕循环（不浪费视觉模型调用）。"""
    from core.mobile.collect.keyword_stage import MobileKeywordStageRunner

    observed: list[str] = []
    processed: list[int] = []

    class _CollectStage:
        async def _candidate_history(self, *_args, **_kwargs):
            from core.mobile.collect.candidate_history import CandidateHistory

            return CandidateHistory()

        async def _navigate_to_search_results(self, *_args, **_kwargs):
            return True

        async def _capture_save(self, ctx, keyword, note):
            processed.append(int(note.split("idx=")[-1]))
            return "QUJD", "shot", "/shot"

        async def _analyze_list(self, *_args, **_kwargs):
            # 每屏都返回一条未见过的新候选，避免触发 no_new_streak 提前结束
            return [
                {
                    "fields": {"title": f"标题{len(processed)}", "account": "公众号"},
                    "score": 90,
                    "subject_match": 90,
                    "tap_x": 100,
                    "tap_y": 200,
                }
            ]

        async def _deep_dive(self, *_args, **_kwargs):
            # 第一次详情后把结果页弄丢
            context.state["results_page_lost"] = True
            return False

    class _Context:
        def __init__(self):
            import logging

            self.logger = logging.getLogger("kw-guard-test")
            self.state = {
                "db": object(),
                "stop_event": asyncio.Event(),
                "device_id": "dev",
                "app_name": "微信",
                "project_id": "project",
                "run_task_id": "run",
                "task_def_id": "task",
                "dry_run": True,
                "deep_collect": True,
                "detail_max_items": 5,
                "min_score_to_detail": 60,
                "min_subject_match": 70,
                "no_new_stop_threshold": 5,
                "swipe_times": 5,
                "swipe_interval": 0.01,
                "dedup_key_fields": ["title", "account"],
                "source_link_strategy": "none",
                "max_item_age_days": 0,
                "search_hint": "",
                "owner": "test",
                "parent_task_id": "",
                "keyword_total": 1,
                "keywords_completed": 0,
                "keywords_processed": 0,
                "counters": {},
                "candidate_reviews": [],
                "candidate_review_limit": 0,
                "results_page_lost": False,
                "detail_entry_reviews": [],
                "detail_entry_review_limit": 0,
            }

        async def emit(self, *_args, **_kwargs):
            return None

        async def drain(self, *_args, **_kwargs):
            return {"failed": 0}

    context = _Context()
    runner = MobileKeywordStageRunner(
        collect_stage=_CollectStage(),
        swipe_action=lambda *_a, **_k: None,
        observer=lambda *a, **k: observed.append(str(k.get("event") or "")),
    )
    item = type("Item", (), {"payload": {"keyword": "关键词"}, "item_id": "1"})()
    asyncio.new_event_loop().run_until_complete(runner.run(item, context))

    # 第一屏处理后详情弄丢结果页 → 立即终止，不进入第二屏
    assert processed == [0]
    assert "collect_results_page_lost" in observed


def test_state_injects_guard_only_for_wechat_tasks():
    from core.mobile.collect.state import _wechat_page_guard_state

    wechat = _wechat_page_guard_state({"app_name": "微信", "source_link_strategy": "wechat_copy_link"})
    assert callable(wechat.get("ensure_results_page"))

    other_app = _wechat_page_guard_state({"app_name": "抖音", "source_link_strategy": "none"})
    assert "ensure_results_page" not in other_app

    default_none = _wechat_page_guard_state({"app_name": "", "source_link_strategy": ""})
    assert "ensure_results_page" not in default_none
