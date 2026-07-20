from __future__ import annotations

import asyncio
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _mobile_device_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.services import mobile_collect_pipeline

    async def ready(device_id: str, **_kwargs: Any) -> str:
        return device_id or "device-a"

    monkeypatch.setattr(
        mobile_collect_pipeline,
        "wait_for_mobile_device_ready",
        ready,
    )


def test_company_wechat_definition_enforces_phone_work_limits() -> None:
    from api.services.wechat_collection import (
        WECHAT_AUTO_TASK_NAME,
        _wechat_definition_patch,
    )

    patch = _wechat_definition_patch(
        {
            "name": WECHAT_AUTO_TASK_NAME,
            "extract_fields": [{"name": "title"}],
            "dedup_key_fields": ["title"],
            "search_hint": "微信公众号搜索",
            "source_link_strategy": "wechat_copy_link",
            "deep_collect": True,
            "include_direct_children": True,
            "max_resolved_keywords": 60,
            "detail_max_items": 5,
        }
    )

    assert patch["include_direct_children"] is False
    assert patch["max_resolved_keywords"] == 6
    assert patch["detail_max_items"] == 2
    assert patch["swipe_times"] == 3
    assert patch["max_runtime_seconds"] == 1800
    assert patch["notify_on"] == "none"


@pytest.mark.asyncio
async def test_ensure_wechat_configuration_creates_unbound_project_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import mobile_collect as collect_dao
    from api.services.wechat_collection import (
        WECHAT_SOURCE_LINK_STRATEGY,
        ensure_wechat_task_definition,
    )

    captured: dict[str, Any] = {}

    async def list_defs(_db: Any, *, project_id: str, limit: int = 200):
        assert project_id == "project-1"
        return []

    async def create_def(_db: Any, payload: dict[str, Any]):
        captured.update(payload)
        return {"task_def_id": "wechat-auto", **payload, "status": "idle"}

    monkeypatch.setattr(collect_dao, "list_task_defs", list_defs)
    monkeypatch.setattr(collect_dao, "create_task_def", create_def)

    task_def = await ensure_wechat_task_definition(
        object(),
        project_id="project-1",
        device_id="device-a",
    )

    assert task_def["task_def_id"] == "wechat-auto"
    assert captured["project_id"] == "project-1"
    assert captured["device_id"] == "device-a"
    assert captured["app_name"] == "微信"
    assert captured["target_id"] is None
    assert captured["source_link_strategy"] == WECHAT_SOURCE_LINK_STRATEGY
    assert captured["use_target_keyword_library"] is True
    assert captured["include_direct_children"] is False
    assert captured["max_resolved_keywords"] == 6
    assert captured["extract_fields"]
    assert captured["dedup_key_fields"] == ["title", "account"]
    assert captured["notify_on"] == "none"


@pytest.mark.asyncio
async def test_ensure_wechat_configuration_reuses_unbound_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import mobile_collect as collect_dao
    from api.services.wechat_collection import ensure_wechat_task_definition

    async def list_defs(_db: Any, **_kwargs: Any):
        return [
            {
                "task_def_id": "bound-other-company",
                "device_id": "device-a",
                "app_name": "微信",
                "target_id": "target-other",
                "source_link_strategy": "wechat_copy_link",
            },
            {
                "task_def_id": "shared-company-scan",
                "name": "综合扫描公众号采集",
                "device_id": "device-a",
                "app_name": "微信",
                "target_id": "",
                "source_link_strategy": "wechat_copy_link",
            },
        ]

    async def create_def(*_args: Any, **_kwargs: Any):
        raise AssertionError("已有未绑定公司的定义时不应重复创建")

    async def update_def(_db: Any, task_def_id: str, patch: dict[str, Any]):
        assert task_def_id == "shared-company-scan"
        return {
            "task_def_id": task_def_id,
            "device_id": "device-a",
            "app_name": "微信",
            "target_id": "",
            "source_link_strategy": "wechat_copy_link",
            **patch,
        }

    monkeypatch.setattr(collect_dao, "list_task_defs", list_defs)
    monkeypatch.setattr(collect_dao, "create_task_def", create_def)
    monkeypatch.setattr(collect_dao, "update_task_def", update_def)

    task_def = await ensure_wechat_task_definition(
        object(),
        project_id="project-1",
        device_id="device-a",
    )

    assert task_def["task_def_id"] == "shared-company-scan"
    assert task_def["extract_fields"]


@pytest.mark.asyncio
async def test_wechat_definition_concurrent_repair_updates_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import mobile_collect as collect_dao
    from api.services import wechat_collection

    wechat_collection._DEFINITION_LOCKS.clear()
    state: dict[str, Any] = {
        "task_def_id": "shared-company-scan",
        "name": "综合扫描公众号采集",
        "project_id": "project-1",
        "device_id": "device-a",
        "app_name": "微信",
        "target_id": "",
        "source_link_strategy": "wechat_copy_link",
        "status": "idle",
    }
    updates = 0

    async def list_defs(_db: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [dict(state)]

    async def update_def(
        _db: Any,
        _task_def_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        nonlocal updates
        updates += 1
        await asyncio.sleep(0.01)
        state.update(patch)
        return dict(state)

    monkeypatch.setattr(collect_dao, "list_task_defs", list_defs)
    monkeypatch.setattr(collect_dao, "update_task_def", update_def)

    results = await asyncio.gather(
        *(
            wechat_collection.resolve_wechat_task_definition(
                object(),
                project_id="project-1",
                device_id="device-a",
                allow_running=True,
            )
            for _ in range(8)
        )
    )

    assert updates == 1
    assert all(item["extract_fields"] for item in results)


@pytest.mark.asyncio
async def test_wechat_configuration_is_resolved_by_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import mobile_collect as collect_dao
    from api.services.wechat_collection import resolve_wechat_task_definition

    async def list_defs(_db: Any, *, project_id: str, limit: int = 200):
        assert project_id == "project-1"
        return [
            {
                "task_def_id": "other",
                "project_id": project_id,
                "device_id": "device-b",
                "app_name": "微信",
                "status": "idle",
            },
            {
                "task_def_id": "wechat-a",
                "project_id": project_id,
                "device_id": "device-a",
                "app_name": "微信",
                "source_link_strategy": "wechat_copy_link",
                "target_id": "target-1",
                "status": "idle",
            },
        ]

    monkeypatch.setattr(collect_dao, "list_task_defs", list_defs)

    async def update_def(_db: Any, task_def_id: str, patch: dict[str, Any]):
        return {
            "task_def_id": task_def_id,
            "project_id": "project-1",
            "device_id": "device-a",
            "app_name": "微信",
            "source_link_strategy": "wechat_copy_link",
            "target_id": "target-1",
            "status": "idle",
            **patch,
        }

    monkeypatch.setattr(collect_dao, "update_task_def", update_def)
    task_def = await resolve_wechat_task_definition(
        object(),
        project_id="project-1",
        device_id="device-a",
        expected_target_id="target-1",
    )

    assert task_def["task_def_id"] == "wechat-a"


@pytest.mark.asyncio
async def test_wechat_configuration_prefers_unbound_definition_over_other_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import mobile_collect as collect_dao
    from api.services.wechat_collection import resolve_wechat_task_definition

    async def list_defs(_db: Any, **_kwargs: Any):
        return [
            {
                "task_def_id": "bound-other-company",
                "device_id": "device-a",
                "app_name": "微信",
                "target_id": "target-other",
                "source_link_strategy": "wechat_copy_link",
                "status": "idle",
            },
            {
                "task_def_id": "shared-company-scan",
                "name": "综合扫描公众号采集",
                "device_id": "device-a",
                "app_name": "微信",
                "target_id": "",
                "source_link_strategy": "none",
                "status": "idle",
            },
        ]

    monkeypatch.setattr(collect_dao, "list_task_defs", list_defs)

    async def update_def(_db: Any, task_def_id: str, patch: dict[str, Any]):
        return {
            "task_def_id": task_def_id,
            "device_id": "device-a",
            "app_name": "微信",
            "target_id": "",
            "status": "idle",
            **patch,
        }

    monkeypatch.setattr(collect_dao, "update_task_def", update_def)
    task_def = await resolve_wechat_task_definition(
        object(),
        project_id="project-1",
        device_id="device-a",
        expected_target_id="target-current",
    )

    assert task_def["task_def_id"] == "shared-company-scan"


@pytest.mark.asyncio
async def test_wechat_configuration_rejects_unconfigured_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import mobile_collect as collect_dao
    from api.services.wechat_collection import resolve_wechat_task_definition

    async def list_defs(_db: Any, **_kwargs: Any):
        return []

    monkeypatch.setattr(collect_dao, "list_task_defs", list_defs)
    with pytest.raises(ValueError, match="没有当前项目的微信采集配置"):
        await resolve_wechat_task_definition(
            object(),
            project_id="project-1",
            device_id="device-a",
        )


@pytest.mark.asyncio
async def test_company_wechat_collection_injects_internal_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import wechat_collection

    async def resolve(*_args: Any, **_kwargs: Any):
        return {
            "task_def_id": "wechat-a",
            "device_id": "device-a",
        }

    captured: dict[str, Any] = {}

    async def run_definition(_db: Any, **kwargs: Any):
        captured.update(kwargs)
        return {
            "total": 3,
            "new": 2,
            "changed": 1,
            "contacts": 4,
            "documents": 2,
            "keywords_used": ["目标公司 招标"],
            "stopped": False,
        }

    monkeypatch.setattr(wechat_collection, "resolve_wechat_task_definition", resolve)
    monkeypatch.setattr(wechat_collection, "run_mobile_collect_definition", run_definition)

    result = await wechat_collection.run_company_wechat_collection(
        object(),
        task_id="scan-1",
        project_id="project-1",
        target_id="target-1",
        target_name="目标公司",
        device_id="device-a",
        collection_priority="low",
    )

    overrides = captured["runtime_overrides"]
    assert overrides["app_name"] == "微信"
    assert overrides["direct_launch_app"] is True
    assert overrides["source_link_strategy"] == "wechat_copy_link"
    assert overrides["extract_fields"]
    assert overrides["dedup_key_fields"] == ["title", "account"]
    assert overrides["include_direct_children"] is False
    assert overrides["max_resolved_keywords"] == 6
    assert overrides["detail_max_items"] == 2
    assert overrides["max_runtime_seconds"] == 1800
    assert overrides["parent_task_id"] == "scan-1"
    assert overrides["target_id"] == "target-1"
    assert captured["queue_priority"] == "low"
    assert result["documents"] == 2
    assert result["contacts"] == 4


@pytest.mark.asyncio
async def test_company_wechat_collection_marks_stopped_run_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import wechat_collection

    async def resolve(*_args: Any, **_kwargs: Any):
        return {"task_def_id": "wechat-a", "device_id": "device-a"}

    async def run_definition(*_args: Any, **_kwargs: Any):
        return {
            "total": 0,
            "new": 0,
            "changed": 0,
            "stopped": True,
            "timed_out": False,
            "keywords_used": ["目标公司 招标"],
        }

    monkeypatch.setattr(wechat_collection, "resolve_wechat_task_definition", resolve)
    monkeypatch.setattr(wechat_collection, "run_mobile_collect_definition", run_definition)

    result = await wechat_collection.run_company_wechat_collection(
        object(),
        task_id="scan-1",
        project_id="project-1",
        target_id="target-1",
        target_name="目标公司",
        device_id="device-a",
    )

    assert result["status"] == "partial"
    assert result["stopped"] is True


@pytest.mark.asyncio
async def test_mobile_collect_rejects_link_deep_collect_without_extract_fields() -> None:
    from core.mobile.collect.pipeline import run_collect_task

    with pytest.raises(ValueError, match="extract_fields 为空"):
        await run_collect_task(
            object(),
            run_task_id="scan-1-wechat",
            project_id="project-1",
            task_def={
                "task_def_id": "wechat-a",
                "device_id": "device-a",
                "deep_collect": True,
                "source_link_strategy": "wechat_copy_link",
                "extract_fields": [],
            },
        )


@pytest.mark.asyncio
async def test_mobile_collect_definition_claims_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import mobile_collect_pipeline
    from api.services import mobile_device_leases
    from contextlib import asynccontextmanager

    events: list[tuple[str, str]] = []

    async def get_task(_db: Any, task_def_id: str):
        return {"task_def_id": task_def_id, "project_id": "project-1"}

    async def claim(_db: Any, task_def_id: str, *, run_task_id: str):
        events.append(("claim", run_task_id))
        return {"task_def_id": task_def_id, "project_id": "project-1"}

    async def set_status(_db: Any, _task_def_id: str, status: str, **_kwargs: Any):
        events.append(("status", status))

    async def run_collect(_db: Any, **kwargs: Any):
        assert kwargs["task_def"]["target_id"] == "target-1"
        return {"total": 0, "new": 0, "changed": 0}

    @asynccontextmanager
    async def lease(_db: Any, **kwargs: Any):
        assert kwargs["requested_by"] == "admin"
        yield "collect:run-1"

    monkeypatch.setattr(mobile_collect_pipeline.collect_dao, "get_task_def", get_task)
    monkeypatch.setattr(mobile_collect_pipeline.collect_dao, "claim_task_run", claim)
    monkeypatch.setattr(mobile_collect_pipeline.collect_dao, "set_task_status", set_status)
    monkeypatch.setattr(mobile_collect_pipeline, "run_collect_task", run_collect)
    monkeypatch.setattr(mobile_device_leases, "background_device_lease", lease)

    await mobile_collect_pipeline.run_mobile_collect_definition(
        object(),
        run_task_id="run-1",
        project_id="project-1",
        task_def_id="wechat-a",
        runtime_overrides={"target_id": "target-1"},
        requested_by="admin",
    )

    assert events == [("claim", "run-1"), ("status", "idle")]


@pytest.mark.asyncio
async def test_mobile_collect_does_not_claim_or_start_before_device_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager
    from api.services import mobile_collect_pipeline
    from api.services import mobile_device_leases

    ready_waiting = asyncio.Event()
    release_ready = asyncio.Event()
    events: list[str] = []

    async def get_task(_db: Any, task_def_id: str) -> dict[str, Any]:
        return {
            "task_def_id": task_def_id,
            "project_id": "project-1",
            "device_id": "stable-device-id",
        }

    async def wait_ready(device_id: str, **_kwargs: Any) -> str:
        assert device_id == "stable-device-id"
        events.append("waiting_device")
        ready_waiting.set()
        await release_ready.wait()
        events.append("device_ready")
        return "10.144.144.3:5555"

    async def claim(
        _db: Any,
        task_def_id: str,
        *,
        run_task_id: str,
    ) -> dict[str, Any]:
        assert run_task_id == "run-ready"
        events.append("claim")
        return await get_task(_db, task_def_id)

    async def set_status(*_args: Any, **_kwargs: Any) -> None:
        events.append("idle")

    async def on_started() -> None:
        events.append("started")

    @asynccontextmanager
    async def lease(*_args: Any, **_kwargs: Any):
        events.append("lease")
        yield "owner"

    async def run_collect(*_args: Any, **_kwargs: Any) -> dict[str, int]:
        events.append("collect")
        return {"total": 0, "new": 0, "changed": 0}

    monkeypatch.setattr(mobile_collect_pipeline.collect_dao, "get_task_def", get_task)
    monkeypatch.setattr(mobile_collect_pipeline.collect_dao, "claim_task_run", claim)
    monkeypatch.setattr(mobile_collect_pipeline.collect_dao, "set_task_status", set_status)
    monkeypatch.setattr(mobile_collect_pipeline, "wait_for_mobile_device_ready", wait_ready)
    monkeypatch.setattr(mobile_collect_pipeline, "run_collect_task", run_collect)
    monkeypatch.setattr(mobile_device_leases, "background_device_lease", lease)

    task = asyncio.create_task(
        mobile_collect_pipeline.run_mobile_collect_definition(
            object(),
            run_task_id="run-ready",
            project_id="project-1",
            task_def_id="wechat-a",
            on_started=on_started,
        )
    )
    await asyncio.wait_for(ready_waiting.wait(), timeout=0.2)
    assert events == ["waiting_device"]
    release_ready.set()
    await asyncio.wait_for(task, timeout=0.5)

    assert events == [
        "waiting_device",
        "device_ready",
        "claim",
        "started",
        "lease",
        "collect",
        "idle",
    ]


@pytest.mark.asyncio
async def test_mobile_collect_definition_waits_for_same_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import mobile_collect_pipeline
    from api.services import mobile_device_leases
    from contextlib import asynccontextmanager

    active = 0
    peak = 0
    completed: list[str] = []
    mobile_collect_pipeline._TASK_DEFINITION_QUEUE_LOCKS.clear()

    async def get_task(_db: Any, task_def_id: str):
        return {"task_def_id": task_def_id, "project_id": "project-1"}

    async def claim(_db: Any, task_def_id: str, *, run_task_id: str):
        return {
            "task_def_id": task_def_id,
            "project_id": "project-1",
            "run_task_id": run_task_id,
        }

    async def set_status(*_args: Any, **_kwargs: Any):
        return None

    async def run_collect(_db: Any, **kwargs: Any):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        completed.append(kwargs["run_task_id"])
        active -= 1
        return {"total": 0, "new": 0, "changed": 0}

    @asynccontextmanager
    async def lease(*_args: Any, **_kwargs: Any):
        yield "owner"

    monkeypatch.setattr(mobile_collect_pipeline.collect_dao, "get_task_def", get_task)
    monkeypatch.setattr(mobile_collect_pipeline.collect_dao, "claim_task_run", claim)
    monkeypatch.setattr(mobile_collect_pipeline.collect_dao, "set_task_status", set_status)
    monkeypatch.setattr(mobile_collect_pipeline, "run_collect_task", run_collect)
    monkeypatch.setattr(mobile_device_leases, "background_device_lease", lease)

    await asyncio.gather(
        mobile_collect_pipeline.run_mobile_collect_definition(
            object(),
            run_task_id="run-1",
            project_id="project-1",
            task_def_id="wechat-a",
        ),
        mobile_collect_pipeline.run_mobile_collect_definition(
            object(),
            run_task_id="run-2",
            project_id="project-1",
            task_def_id="wechat-a",
        ),
    )

    assert peak == 1
    assert completed == ["run-1", "run-2"]


@pytest.mark.asyncio
async def test_mobile_collect_definition_prioritizes_queued_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import mobile_collect_pipeline
    from api.services import mobile_device_leases
    from contextlib import asynccontextmanager

    started = asyncio.Event()
    release_running = asyncio.Event()
    completed: list[str] = []
    mobile_collect_pipeline._TASK_DEFINITION_QUEUE_LOCKS.clear()

    async def get_task(_db: Any, task_def_id: str):
        return {"task_def_id": task_def_id, "project_id": "project-1"}

    async def claim(_db: Any, task_def_id: str, *, run_task_id: str):
        return {
            "task_def_id": task_def_id,
            "project_id": "project-1",
            "run_task_id": run_task_id,
        }

    async def set_status(*_args: Any, **_kwargs: Any):
        return None

    async def run_collect(_db: Any, **kwargs: Any):
        run_task_id = kwargs["run_task_id"]
        if run_task_id == "run-low-active":
            started.set()
            await release_running.wait()
        completed.append(run_task_id)
        return {"total": 0, "new": 0, "changed": 0}

    @asynccontextmanager
    async def lease(*_args: Any, **_kwargs: Any):
        yield "owner"

    monkeypatch.setattr(mobile_collect_pipeline.collect_dao, "get_task_def", get_task)
    monkeypatch.setattr(mobile_collect_pipeline.collect_dao, "claim_task_run", claim)
    monkeypatch.setattr(mobile_collect_pipeline.collect_dao, "set_task_status", set_status)
    monkeypatch.setattr(mobile_collect_pipeline, "run_collect_task", run_collect)
    monkeypatch.setattr(mobile_device_leases, "background_device_lease", lease)

    async def run(run_task_id: str, priority: str):
        return await mobile_collect_pipeline.run_mobile_collect_definition(
            object(),
            run_task_id=run_task_id,
            project_id="project-1",
            task_def_id="wechat-a",
            queue_priority=priority,
        )

    active = asyncio.create_task(run("run-low-active", "low"))
    await started.wait()
    normal = asyncio.create_task(run("run-normal", "normal"))
    low = asyncio.create_task(run("run-low", "low"))
    high = asyncio.create_task(run("run-high", "high"))
    await asyncio.sleep(0.01)
    release_running.set()
    await asyncio.gather(active, normal, low, high)

    assert completed == [
        "run-low-active",
        "run-high",
        "run-normal",
        "run-low",
    ]


@pytest.mark.asyncio
async def test_background_device_lease_queues_same_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import mobile_device_leases

    active = 0
    peak = 0
    mobile_device_leases._DEVICE_QUEUE_LOCKS.clear()

    class _Reservation:
        device_key = "device-key"
        owner = "owner"
        note = "mobile_collect"
        since = None

    class _Pool:
        def acquire_for_task(self, *_args: Any, **_kwargs: Any):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            return _Reservation()

        def release(self, *_args: Any, **_kwargs: Any):
            nonlocal active
            active -= 1

    pool = _Pool()

    async def upsert(*_args: Any, **_kwargs: Any):
        return None

    async def delete(*_args: Any, **_kwargs: Any):
        return None

    monkeypatch.setattr(mobile_device_leases, "resolve_device_key", lambda _device_id: "device-key")
    monkeypatch.setattr(mobile_device_leases.DevicePool, "get_instance", lambda: pool)
    monkeypatch.setattr(mobile_device_leases.reservations_dao, "upsert_reservation", upsert)
    monkeypatch.setattr(mobile_device_leases.reservations_dao, "delete_reservation", delete)

    async def use_device(run_task_id: str) -> None:
        async with mobile_device_leases.background_device_lease(
            object(),
            device_id="device-a",
            run_task_id=run_task_id,
        ):
            await asyncio.sleep(0.01)

    await asyncio.gather(use_device("run-1"), use_device("run-2"))

    assert peak == 1
