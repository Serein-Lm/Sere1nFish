"""The task result and source progress must agree on collection completeness."""
import asyncio
from types import SimpleNamespace

import pytest

from core.mobile.collect.contracts import MobileCollectExecution, MobileSeedPlan
from core.mobile.collect.runtime import _update_parent_terminal_progress
from core.mobile.collect.state import project_terminal_result


def execution(*, completed=2, total=2, timed_out=False, stopped=False, **state):
    stop_event = asyncio.Event()
    if stopped:
        stop_event.set()
    return MobileCollectExecution(
        plan=SimpleNamespace(),
        seeds=MobileSeedPlan(None, {"keywords": ["first", "second"]}, "fingerprint"),
        state={"stop_event": stop_event, "keywords_completed": completed,
               "keyword_total": total, "parent_task_id": "parent", "counters": {}, **state},
        timed_out=timed_out,
    )


@pytest.mark.parametrize("kwargs, expected", [
    ({}, "completed"),
    ({"completed": 23, "total": 24, "timed_out": True}, "partial"),
    ({"timed_out": True}, "partial"),
    ({"stopped": True}, "partial"),
    ({"completed": 1}, "partial"),
    ({"counters": {"failed": 1}}, "partial"),
    ({"counters": {"persist_failed": 1}}, "partial"),
    ({"counters": {"persist_failed": 1}, "require_persist_success": True}, "error"),
    ({"all_failed": True, "completed": 0}, "error"),
])
@pytest.mark.asyncio
async def test_result_and_parent_progress_share_terminal_outcome(monkeypatch, kwargs, expected):
    from api.services import task_progress

    updates = []

    async def update(_db, **fields):
        updates.append(fields)

    monkeypatch.setattr(task_progress, "update_source_progress", update)
    run = execution(**kwargs)
    result = project_terminal_result(run)
    await _update_parent_terminal_progress(
        object(), run.state, timed_out=run.timed_out,
        all_failed=bool(run.state.get("all_failed")),
        failed_keywords=run.state["counters"].get("failed", 0),
    )
    assert result["status"] == updates[0]["status"] == expected
    assert result["keywords_completed"] == run.state["keywords_completed"]


@pytest.mark.asyncio
async def test_partial_result_persists_partial_without_overriding_pause():
    from api.dao.tasks import complete_task

    writes = []

    class Collection:
        async def update_one(self, query, update):
            writes.append((query, update))
            # The task was paused by its owner while the run was finishing.
            return SimpleNamespace(modified_count=0)

    result = project_terminal_result(execution(completed=23, total=24, timed_out=True))
    assert await complete_task(
        {"tasks": Collection()}, task_id="parent", runtime_id="owner",
        elapsed_ms=100, result=result,
    ) is False
    query, update = writes[0]
    assert query == {"task_id": "parent", "status": "running", "runtime_id": "owner"}
    assert update["$set"]["result_status"] == "partial"
    assert update["$set"]["progress.stage"] == "partial"
