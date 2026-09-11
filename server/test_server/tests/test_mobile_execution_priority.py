"""Queued collection priority is shared by every definition using a device."""
import asyncio
from contextlib import asynccontextmanager

import pytest


def test_queue_and_progress_metadata_do_not_invalidate_collection_checkpoints():
    from api.dao.mobile_collect import definition_fingerprint

    definition = {"task_def_id": "def-1", "device_id": "phone", "keywords": ["示例公司"]}
    assert definition_fingerprint(definition) == definition_fingerprint({
        **definition, "queue_priority": "high", "parent_task_id": "run-2",
        "progress_source": "mobile_collect", "progress_label": "手机采集",
        "latest_run": {"status": "running"},
    })


@pytest.mark.asyncio
async def test_device_priority_skips_cancelled_waiter_and_keeps_fifo(monkeypatch):
    from api.services import mobile_device_leases as service

    service._DEVICE_QUEUES.clear()
    monkeypatch.setattr(service, "resolve_device_key", lambda value: "same-phone")

    class Pool:
        def ensure_owner(self, *args):
            pass

    monkeypatch.setattr(service.DevicePool, "get_instance", lambda: Pool())

    @asynccontextmanager
    async def lease(*args, **kwargs):
        yield None

    monkeypatch.setattr(service, "mobile_execution_lease", lease)
    started, release = asyncio.Event(), asyncio.Event()
    order = []

    async def run(name, priority):
        async with service.background_device_lease(
            object(), device_id=name, run_task_id=name, queue_priority=priority,
        ):
            if name == "active":
                started.set()
                await release.wait()
            order.append(name)

    active = asyncio.create_task(run("active", "normal"))
    await started.wait()
    jobs = [asyncio.create_task(run(name, priority)) for name, priority in [
        ("low", "low"), ("normal", "normal"), ("cancelled", "high"),
        ("first-high", "high"), ("second-high", "high"),
    ]]
    await asyncio.sleep(0.03)
    jobs[2].cancel()
    await asyncio.gather(jobs[2], return_exceptions=True)
    release.set()
    await asyncio.wait_for(asyncio.gather(active, *(job for job in jobs if not job.cancelled())), 1)
    assert order == ["active", "first-high", "second-high", "normal", "low"]


@pytest.mark.asyncio
async def test_dispatch_publishes_waiting_and_connects_standalone_progress(monkeypatch):
    from api.services import mobile_collect_pipeline as service, task_progress

    updates = []
    monkeypatch.setattr(service, "get_db", lambda: object())

    async def update(*args, **kwargs):
        updates.append(kwargs)

    async def execute(*args, **kwargs):
        assert kwargs["queue_priority"] == "high"
        assert kwargs["runtime_overrides"]["parent_task_id"] == "run-1"
        await kwargs["on_waiting"]("waiting_mobile", "等待设备上线")
        await kwargs["on_started"]()
        return {"total": 1}

    monkeypatch.setattr(task_progress, "update_task_stage", update)
    monkeypatch.setattr(task_progress, "update_source_progress", update)
    monkeypatch.setattr(service, "run_mobile_collect_definition", execute)
    assert await service._dispatch_mobile_collect("run-1", "p1", {"task_def_id": "def1", "queue_priority": "high"}) == {"total": 1}
    assert updates[0]["stage"] == "waiting_mobile"
    assert updates[1]["status"] == "waiting"
    assert updates[2]["stage"] == "mobile_collect"
    assert updates[3]["status"] == "running"
