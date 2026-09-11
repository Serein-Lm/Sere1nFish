"""Queued task lifecycle uses persistent project task state before phone access."""
import asyncio

import pytest

from api.services import mobile_collect_tasks as service


@pytest.mark.asyncio
async def test_repeated_start_does_not_duplicate_a_waiting_task(monkeypatch):
    stored, dispatched = [], []

    async def get_definition(*args):
        return {"task_def_id": "def-1", "project_id": "p1", "status": "idle", "queue_priority": "high"}

    async def latest(*args, **kwargs):
        return stored[-1] if stored else None

    async def insert(db, document):
        await asyncio.sleep(0)
        stored.append(document)

    def spawn(coroutine, **kwargs):
        dispatched.append(kwargs["name"])
        coroutine.close()

    monkeypatch.setattr(service.collect_dao, "get_task_def", get_definition)
    monkeypatch.setattr(service.tasks_dao, "find_latest_matching_task", latest)
    monkeypatch.setattr(service.tasks_dao, "insert_task", insert)
    monkeypatch.setattr(service, "spawn_background", spawn)
    results = await asyncio.gather(
        *(service.start_mobile_collect_task(object(), task_def_id="def-1") for _ in range(2)),
        return_exceptions=True,
    )
    assert len(stored) == len(dispatched) == 1
    assert stored[0]["params"]["queue_priority"] == "high"
    assert sum(isinstance(result, service.MobileCollectTaskBusyError) for result in results) == 1


@pytest.mark.asyncio
async def test_stop_works_while_device_offline_before_definition_claim(monkeypatch):
    from api.services import project_task_control
    from core.mobile import collect

    async def get_definition(*args):
        return {"project_id": "p1", "status": "idle", "last_run_task_id": None}

    async def latest(*args, **kwargs):
        return {"task_id": "waiting-run", "status": "running"}

    async def pause(db, **kwargs):
        assert kwargs == {"project_id": "p1", "task_id": "waiting-run"}
        return {"status": "paused"}

    monkeypatch.setattr(service.collect_dao, "get_task_def", get_definition)
    monkeypatch.setattr(service.tasks_dao, "find_latest_matching_task", latest)
    monkeypatch.setattr(project_task_control, "pause_project_task", pause)
    monkeypatch.setattr(collect, "request_stop", lambda *args: pytest.fail("unstarted device runtime cannot process stop"))
    assert await service.stop_mobile_collect_task(object(), task_def_id="def-1") == {
        "ok": True, "run_task_id": "waiting-run", "status": "paused",
    }


@pytest.mark.asyncio
async def test_stop_rejects_unrelated_run(monkeypatch):
    async def get_definition(*args):
        return {"project_id": "p1", "last_run_task_id": "own-run"}

    async def latest(*args, **kwargs):
        return None

    monkeypatch.setattr(service.collect_dao, "get_task_def", get_definition)
    monkeypatch.setattr(service.tasks_dao, "find_latest_matching_task", latest)
    with pytest.raises(ValueError, match="不属于"):
        await service.stop_mobile_collect_task(object(), task_def_id="def-1", run_task_id="other-run")
