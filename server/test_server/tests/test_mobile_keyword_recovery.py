"""A pause preserves each keyword whose complete output has been persisted."""
import asyncio

import pytest

from test_server.tests.test_mobile_collect_pipeline import _FakeDB, _patch_pipeline, _task_def


@pytest.mark.asyncio
async def test_checkpoint_waits_for_persistence_and_survives_mid_next_keyword_cancel(monkeypatch):
    pl, _ = _patch_pipeline(monkeypatch, analyze_returns=[{"title": "A", "author": "x"}])
    stored, navigated = {}, []
    persist_started, persist_release, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    block_second = True
    original_persist = pl._PersistStage.handle

    async def persist(self, item, ctx):
        if item.payload["keyword"] == "kw1":
            persist_started.set()
            await persist_release.wait()
        await original_persist(self, item, ctx)

    async def navigate(self, ctx, keyword, **kwargs):
        navigated.append(keyword)
        if keyword == "kw2" and block_second:
            second_started.set()
            await asyncio.Event().wait()
        return True

    async def mark(*args, **kwargs):
        if kwargs["status"] == "completed":
            stored[kwargs["checkpoint_key"]] = kwargs["keyword"]

    async def completed(*args, **kwargs):
        return set(stored)

    monkeypatch.setattr(pl._PersistStage, "handle", persist)
    monkeypatch.setattr(pl._CollectStage, "_navigate_to_search_results", navigate)
    monkeypatch.setattr(pl.collect_dao, "mark_keyword_checkpoint", mark)
    monkeypatch.setattr(pl.collect_dao, "list_completed_checkpoint_keys", completed)
    db = _FakeDB()
    definition = _task_def(keywords=["kw1", "kw2"], notify_on="none")
    task = asyncio.create_task(pl.run_collect_task(db, run_task_id="resume-run", project_id="p1", task_def=definition))
    try:
        await asyncio.wait_for(persist_started.wait(), 1)
        assert not stored
        assert not second_started.is_set()
        persist_release.set()
        await asyncio.wait_for(second_started.wait(), 1)
        assert list(stored.values()) == ["kw1"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert pl.is_running("resume-run") is False
    block_second = False
    result = await pl.run_collect_task(db, run_task_id="resume-run", project_id="p1", task_def=definition)
    assert navigated == ["kw1", "kw2", "kw2"]
    assert result["keywords_completed"] == 2
    assert sorted(stored.values()) == ["kw1", "kw2"]


@pytest.mark.asyncio
async def test_persist_failure_only_prevents_its_own_keyword_checkpoint(monkeypatch):
    pl, _ = _patch_pipeline(monkeypatch, analyze_returns=[{"title": "A", "author": "x"}])
    completed = []
    original_persist = pl._PersistStage.handle

    async def persist(self, item, ctx):
        if item.payload["keyword"] == "kw1":
            raise RuntimeError("storage unavailable for first keyword")
        await original_persist(self, item, ctx)

    async def mark(*args, **kwargs):
        if kwargs["status"] == "completed":
            completed.append(kwargs["keyword"])

    monkeypatch.setattr(pl._PersistStage, "handle", persist)
    monkeypatch.setattr(pl.collect_dao, "mark_keyword_checkpoint", mark)
    result = await pl.run_collect_task(
        _FakeDB(), run_task_id="partial-run", project_id="p1",
        task_def=_task_def(keywords=["kw1", "kw2"], notify_on="none"),
    )
    assert result["persist_failed"] == 1
    assert result["keywords_completed"] == 1
    assert completed == ["kw2"]
