"""Time windows must survive resume and must not skip gaps or notify old articles."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services import mobile_incremental as service
from core.mobile.collect.candidate_time import candidate_window_status
from core.mobile.collect.persistence_stage import PersistStage
from core.stream import Item


NOW = datetime(2026, 9, 14, 4, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=3)


def window():
    return {"until": NOW, "overlap_hours": 24, "targets": {"target": {"since": SINCE, "scope_key": "scope"}}}


@pytest.mark.parametrize("published,status", [
    ("2026-09-10T04:00:00Z", "inside"),
    ("2026-09-10T03:59:59Z", "outside"),
    ("2026-09-14T04:00:00Z", "inside"),
    ("2026-09-14T04:00:01Z", "outside"),
    ("2026年9月12日", "inside"),
    ("2026年8月12日", "outside"),
    ("", "unknown"),
    ("来源：北京", "unknown"),
])
def test_publication_window_is_primary(published, status):
    assert candidate_window_status({"fields": {"publish_time": published}}, window(), "target") == status


def test_wrong_target_cannot_borrow_another_targets_window():
    assert candidate_window_status({"fields": {"publish_time": NOW}}, window(), "other") == "unknown"


def plan_and_seeds():
    return SimpleNamespace(
        project_id="project", task_def_id="definition", run_task_id="run", db=object(), dry_run=False,
        task_def={"incremental_by_time": True, "incremental_since": SINCE, "source_link_strategy": "wechat_copy_link"},
    ), SimpleNamespace(definition_fingerprint="fingerprint", seed_specs=[{
        "target_id": "target", "keyword": "单位", "checkpoint_key": "keyword-checkpoint",
    }])


@pytest.mark.asyncio
async def test_window_frozen_on_resume_and_uses_imported_baseline(monkeypatch):
    plan, seeds = plan_and_seeds()
    saved = []
    async def save(_db, value):
        saved.append(value)
        return value
    monkeypatch.setattr(service.dao, "get_window", AsyncMock(return_value=None))
    monkeypatch.setattr(service.dao, "get_target_state", AsyncMock(return_value={
        "mobile_incremental_baseline": {"since": SINCE - timedelta(days=20), "reason": "historical_mobile"},
    }))
    monkeypatch.setattr(service.dao, "save_window", save)
    first = await service.prepare_window(plan, seeds, now=NOW)
    assert first["targets"]["target"]["since"] == SINCE - timedelta(days=20)
    monkeypatch.setattr(service.dao, "get_window", AsyncMock(return_value=first))
    resumed = await service.prepare_window(plan, seeds, now=NOW + timedelta(days=1))
    assert resumed["until"] == NOW
    assert len(saved) == 1


@pytest.mark.asyncio
async def test_next_round_uses_success_cursor_and_dry_run_never_saves(monkeypatch):
    plan, seeds = plan_and_seeds()
    plan.dry_run = True
    scope = service.target_scopes(seeds.seed_specs, "wechat_copy_link")["target"]
    monkeypatch.setattr(service.dao, "get_target_state", AsyncMock(return_value={
        "mobile_incremental_cursors": {scope: {"through_at": SINCE + timedelta(days=1)}},
    }))
    save = AsyncMock()
    monkeypatch.setattr(service.dao, "save_window", save)
    result = await service.prepare_window(plan, seeds, now=NOW)
    assert result["targets"]["target"]["since"] == SINCE + timedelta(days=1)
    save.assert_not_called()


@pytest.mark.parametrize("gap", ["failed", "persist_failed", "screen_errors", "time_unverified", "time_coverage_incomplete", "paused", "timeout", "partial", "dry_run"])
@pytest.mark.asyncio
async def test_incomplete_run_never_moves_cursor(monkeypatch, gap):
    plan, seeds = plan_and_seeds()
    state = {"incremental_window": window(), "stop_event": asyncio.Event(), "keyword_total": 1, "keywords_completed": 1, "counters": {}}
    execution = SimpleNamespace(plan=plan, seeds=seeds, state=state, timed_out=gap == "timeout")
    plan.dry_run = gap == "dry_run"
    if gap == "paused":
        state["stop_event"].set()
    elif gap == "partial":
        state["keywords_completed"] = 0
    else:
        state["counters"][gap] = 1
    advance = AsyncMock()
    monkeypatch.setattr(service.dao, "advance_cursor", advance)
    assert not await service.complete_window(execution)
    advance.assert_not_called()


@pytest.mark.asyncio
async def test_resumed_historical_gap_blocks_advance_then_complete_advances(monkeypatch):
    plan, seeds = plan_and_seeds()
    execution = SimpleNamespace(plan=plan, seeds=seeds, timed_out=False, state={
        "incremental_window": window(), "stop_event": asyncio.Event(), "keyword_total": 1, "keywords_completed": 1,
    })
    advance = AsyncMock()
    monkeypatch.setattr(service.dao, "advance_cursor", advance)
    monkeypatch.setattr(service.dao, "get_window", AsyncMock(return_value={"status": "completed", "stats": {"screen_errors": 1}}))
    assert not await service.complete_window(execution)
    advance.assert_not_called()
    monkeypatch.setattr(service.dao, "get_window", AsyncMock(return_value={"status": "completed", "stats": {}}))
    assert await service.complete_window(execution)
    assert advance.call_args.kwargs["through_at"] == NOW


@pytest.mark.parametrize("published", ["2024-01-01", "", "unparseable"])
@pytest.mark.asyncio
async def test_final_payload_cannot_bypass_time_gate(monkeypatch, published):
    stage = PersistStage()
    upsert = AsyncMock()
    monkeypatch.setattr(stage, "_upsert_record", upsert)
    ctx = SimpleNamespace(state={"incremental_window": window(), "counters": {}}, emit=AsyncMock())
    await stage.handle(Item(payload={"target_id": "target", "fields": {"publish_time": published}}), ctx)
    upsert.assert_not_called()
    ctx.emit.assert_not_called()


def test_different_keyword_scope_does_not_reuse_an_unrelated_cursor():
    _, seeds = plan_and_seeds()
    first = service.target_scopes(seeds.seed_specs, "wechat_copy_link")
    seeds.seed_specs[0]["keyword"] = "另一个公众号"
    assert service.target_scopes(seeds.seed_specs, "wechat_copy_link") != first


@pytest.mark.asyncio
async def test_unknown_date_pending_url_is_saved_without_notification(monkeypatch):
    stage = PersistStage()
    payload = {"fields": {}, "target_id": "target", "source_url": "https://example.test/article", "source_archive_status": "pending"}
    prepared = SimpleNamespace(payload=payload, is_high_score=True)
    monkeypatch.setattr(stage, "_prepare", lambda *_: prepared)
    upsert = AsyncMock(return_value={"is_new": True, "is_changed": False, "record_id": "record"})
    monkeypatch.setattr(stage, "_upsert_record", upsert)
    monkeypatch.setattr(stage, "_update_record_counters", lambda *_: None)
    for method in ("_wake_pending_handoff", "_archive_media", "_persist_findings", "_reconcile_findings"):
        monkeypatch.setattr(stage, method, AsyncMock())
    ctx = SimpleNamespace(state={"incremental_window": window(), "notify_on": "new", "counters": {}}, emit=AsyncMock())
    await stage.handle(Item(payload=payload), ctx)
    upsert.assert_awaited_once()
    ctx.emit.assert_not_called()
    assert ctx.state["counters"]["time_unverified"] == 1
