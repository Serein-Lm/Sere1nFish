from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services import mobile_incremental_notifications as service


NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


def example():
    payload = {"record_id": "r1", "target_id": "t1", "target_name": "示例机构", "kind": "new",
               "fields": {"title": "公开公告", "publish_time": "2026年9月14日 10:00", "summary": "公告摘要"},
               "source_url": "https://example.org/article", "source_archive_status": "ready",
               "source_document_id": "d1", "source_document_version_id": "v1"}
    state = {"project_id": "p1", "run_task_id": "run1", "task_def_id": "def1", "task_name": "示例监控",
             "incremental_window": {"until": NOW, "overlap_hours": 24,
                                    "targets": {"t1": {"since": NOW - timedelta(days=2)}}}}
    return payload, state


@pytest.mark.parametrize("change", ["pending", "rejected", "old", "unknown", "future", "foreign_target"])
def test_unverified_or_out_of_window_never_becomes_a_bulletin(change):
    payload, state = example()
    if change in {"pending", "rejected"}:
        payload["source_archive_status"] = change
    elif change == "foreign_target":
        payload["target_id"] = "different"
    else:
        payload["fields"]["publish_time"] = {"old": "2020-01-01", "unknown": "", "future": "2030-01-01"}[change]
    assert service.build_event(payload=payload, state=state, now=NOW) is None


def test_overlap_and_analysis_revisions_reuse_identity_but_new_content_does_not():
    payload, state = example()
    first = service.build_event(payload=payload, state=state, now=NOW)
    payload["kind"] = "changed"
    state["run_task_id"] = "another_run"
    payload["fields"]["summary"] = "同一来源版本的重新分析"
    repeated = service.build_event(payload=payload, state=state, now=NOW)
    assert first["event_id"] == repeated["event_id"]
    payload["source_document_version_id"] = "v2"
    assert service.build_event(payload=payload, state=state)["event_id"] != first["event_id"]
    state["project_id"] = "p2"
    assert service.build_event(payload=payload, state=state)["event_id"] != repeated["event_id"]


def test_notification_highlights_counts_identity_dates_and_source():
    payload, state = example()
    event = service.build_event(payload=payload, state=state, now=NOW)
    message = service.notification_content(event, {"new": 3, "changed": 2})
    assert "【增量通报 · 新增】示例机构" in message["title"]
    assert "新增 **3** 条 · 变化 **2** 条" in message["content"]
    assert "2026-09-14 20:00:00" in message["content"]
    assert "2026年9月14日 10:00" in message["content"]
    assert "[打开新增资料](https://example.org/article)" in message["content"]


def test_legacy_non_project_event_still_has_a_valid_public_contract():
    from api.models.mobile_incremental_events import IncrementalEvent
    payload, state = example()
    state.update(project_id=None, incremental_window=None)
    event = service.build_event(payload=payload, state=state, now=NOW)
    assert IncrementalEvent.model_validate(event).project_id == ""


@pytest.mark.asyncio
async def test_notification_failure_keeps_durable_bulletin(monkeypatch):
    payload, state = example()
    event = service.build_event(payload=payload, state=state, now=NOW)
    insert = AsyncMock(); finish = AsyncMock()
    monkeypatch.setattr(service.dao, "insert_event", insert)
    monkeypatch.setattr(service.dao, "claim_delivery", AsyncMock(return_value=event))
    monkeypatch.setattr(service.dao, "counts", AsyncMock(return_value={"new": 1, "changed": 0}))
    monkeypatch.setattr(service.dao, "finish_delivery", finish)
    monkeypatch.setattr(service, "notify_event", AsyncMock(return_value=SimpleNamespace(ok=False, skipped=False)))
    await service.publish_increment(None, payload=payload, state=state)
    insert.assert_awaited_once()
    assert finish.call_args.kwargs["status"] == "failed"


@pytest.mark.asyncio
async def test_replay_does_not_send_again(monkeypatch):
    payload, state = example()
    monkeypatch.setattr(service.dao, "insert_event", AsyncMock(return_value=False))
    monkeypatch.setattr(service.dao, "claim_delivery", AsyncMock(return_value=None))
    notify = AsyncMock(); monkeypatch.setattr(service, "notify_event", notify)
    await service.publish_increment(None, payload=payload, state=state)
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_handoff_uses_original_window_and_announces_first_archive_as_new(monkeypatch):
    from api.dao import mobile_incremental
    payload, state = example()
    monkeypatch.setattr(mobile_incremental, "get_run_window", AsyncMock(return_value=state["incremental_window"]))
    publish = AsyncMock(); monkeypatch.setattr(service, "publish_increment", publish)
    previous = {**payload, "source_document_version_id": "", "latest_run_task_id": "run1"}
    await service.publish_recovered_increment(None, previous=previous,
        result={"document_id": "d1", "version_id": "v1", "fields": payload["fields"]},
        stored={"record_id": "r1", "is_changed": True},
        task_def={"task_def_id": "def1", "project_id": "p1", "notify_on": "new", "incremental_by_time": True})
    assert publish.call_args.kwargs["payload"]["kind"] == "new"
    assert publish.call_args.kwargs["state"]["incremental_window"] == state["incremental_window"]


@pytest.mark.asyncio
async def test_archived_time_increment_is_reported_even_without_high_contact_score(monkeypatch):
    from core.mobile.collect.persistence_stage import PersistStage
    payload, state = example()
    state.update(db=None, notify_on="new")
    prepared = SimpleNamespace(payload=payload, is_high_score=False, notification_score=39)
    record = AsyncMock(return_value={"event_id": "e1"})
    monkeypatch.setattr(service, "record_increment", record)
    ctx = SimpleNamespace(emit=AsyncMock())
    await PersistStage._emit_notification(ctx, state, prepared, {"record_id": "r1", "is_new": True, "is_changed": False})
    record.assert_awaited_once()
    ctx.emit.assert_awaited_once()
