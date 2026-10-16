import asyncio

import pytest

from core.task_dependencies import TaskDependencyBlocked
from test_server.tests.test_mobile_search_navigation import (
    _FakeDevice, _FakeLauncher, _FakeManager, _activity_runner, _collect_navigation_context,
)


@pytest.mark.parametrize("activity", [
    "com.tencent.mm.ui.account.LoginUI",
    "com.tencent.mm.ui.account.mobile.MobileInputUI",
    "com.tencent.mm.ui.account.RegByMobileVerifySmsUI",
])
def test_known_login_activity_blocks_without_back_or_input(activity):
    from core.mobile.collect.search_navigation import WechatArticleSearchNavigator
    device = _FakeDevice()
    navigator = WechatArticleSearchNavigator(
        manager_factory=lambda: _FakeManager(device), launcher_factory=_FakeLauncher,
        runner=_activity_runner([activity]), sleep=lambda _: None,
    )
    result = navigator.navigate("device-a", app_name="微信", app_instance="primary", keyword="news")
    assert result.blocked_reason == "needs_auth"
    assert result.metadata["activity"] == activity
    assert device.events == []


@pytest.mark.asyncio
async def test_dependency_block_never_launches_visual_fallback():
    from core.mobile.collect.navigation_runtime import MobileSearchNavigationRunner
    from core.mobile.collect.search_navigation import SearchNavigationResult
    async def registered(*args, **kwargs):
        return SearchNavigationResult(strategy="wechat_copy_link", ok=False,
                                      blocked_reason="needs_auth", error="manual login")
    async def visual(*args, **kwargs):
        pytest.fail("login must not invoke visual navigation")
    def launch(*args, **kwargs):
        pytest.fail("login must not trigger app relaunch")
    runner = MobileSearchNavigationRunner(registered_navigation=registered, launch_app=launch,
                                         visual_navigation=visual, observer=lambda *a, **kw: None, source="test")
    ctx = _collect_navigation_context()
    assert not await runner.run(ctx, keyword="news", item_id="i", candidate_policy=None)
    assert ctx.state["stop_event"].is_set()
    assert ctx.state["blocked_dependency"]["reason"] == "needs_auth"


@pytest.mark.asyncio
async def test_blocked_keyword_retains_completed_cursor_and_skips_remaining(monkeypatch):
    from test_server.tests.test_mobile_collect_pipeline import _FakeDB, _patch_pipeline, _task_def
    pl, _ = _patch_pipeline(monkeypatch, analyze_returns=[{"title": "A", "author": "x"}])
    checkpoints, navigated = {}, []
    async def navigate(self, ctx, keyword, **kwargs):
        navigated.append(keyword)
        if keyword == "kw2":
            ctx.state["blocked_dependency"] = {"reason": "needs_auth", "message": "manual login"}
            ctx.state["stop_event"].set()
            return False
        return True
    async def mark(*args, **kwargs):
        checkpoints[kwargs["keyword"]] = kwargs["status"]
    monkeypatch.setattr(pl._CollectStage, "_navigate_to_search_results", navigate)
    monkeypatch.setattr(pl.collect_dao, "mark_keyword_checkpoint", mark)
    with pytest.raises(TaskDependencyBlocked) as blocked:
        await pl.run_collect_task(_FakeDB(), run_task_id="blocked", project_id="p",
                                  task_def=_task_def(keywords=["kw1", "kw2", "kw3"], notify_on="none"))
    assert navigated == ["kw1", "kw2"]
    assert checkpoints == {"kw1": "completed", "kw2": "blocked"}
    assert blocked.value.result["keywords_completed"] == 1
    assert not blocked.value.result["incremental_cursor_advanced"]
    assert not pl.is_running("blocked")


@pytest.mark.asyncio
async def test_dependency_pause_is_fenced_and_manual_resume_preserves_checkpoint(isolated_mongo_db):
    from api.dao.task_dependencies import pause_for_dependency
    from api.dao import tasks
    db = isolated_mongo_db
    await db.tasks.insert_one({"task_id": "t", "project_id": "p", "status": "running",
                               "runtime_id": "new", "checkpoint": {"saved": True}})
    args = dict(task_id="t", dependency={"reason": "needs_auth", "message": "login"}, result={"total": 2})
    assert not await pause_for_dependency(db, runtime_id="old", **args)
    assert await pause_for_dependency(db, runtime_id="new", **args)
    assert not await tasks.complete_task(db, task_id="t", runtime_id="new", elapsed_ms=1, result={})
    paused = await db.tasks.find_one({"task_id": "t"})
    assert paused["status"] == "paused" and paused["checkpoint"] == {"saved": True}
    assert paused["result"] == {"total": 2}
    resumed = await tasks.resume_task(db, project_id="p", task_id="t")
    assert resumed["status"] == "pending" and resumed["checkpoint"] == {"saved": True}
    assert "blocked_dependency" not in resumed


def test_project_runtime_pauses_dependency_and_releases_context(monkeypatch):
    from test_server.tests.test_project_task_execution import _Db, _Tracker
    from api.services import project_task_runtime as runtime
    from Sere1nGraph.graph import observability
    db, tracker = _Db(), _Tracker()
    async def dispatch(*args):
        raise TaskDependencyBlocked("needs_auth", "manual login", result={"total": 2})
    monkeypatch.setattr(runtime, "get_db", lambda: db)
    monkeypatch.setattr(runtime, "_TASK_DISPATCHERS", {"mobile_collect": dispatch})
    monkeypatch.setattr(observability, "get_global_tracker", lambda: tracker)
    monkeypatch.setattr(runtime, "obs_log", lambda *a, **kw: None)
    asyncio.run(runtime.execute_project_task("t", "p", "mobile_collect", {}))
    assert db.tasks.doc["status"] == "paused"
    assert db.tasks.doc["blocked_dependency"]["reason"] == "needs_auth"
    assert tracker.depth == 0


def test_company_source_merge_preserves_dependency_identity():
    from api.services.company_scan_pipeline import CompanyScanPipeline
    result = {"sub_errors": [], "wechat": {}}
    error = TaskDependencyBlocked("needs_auth", "login", context={"device_id": "d"})
    CompanyScanPipeline._merge_primary_job_results(result, [("wechat", None)], [error])
    assert result["blocked_dependencies"] == [{"reason": "needs_auth", "message": "login", "device_id": "d"}]
