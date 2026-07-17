from __future__ import annotations

from typing import Any

import pytest


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
    task_def = await resolve_wechat_task_definition(
        object(),
        project_id="project-1",
        device_id="device-a",
        expected_target_id="target-1",
    )

    assert task_def["task_def_id"] == "wechat-a"


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
    )

    overrides = captured["runtime_overrides"]
    assert overrides["app_name"] == "微信"
    assert overrides["direct_launch_app"] is True
    assert overrides["source_link_strategy"] == "wechat_copy_link"
    assert overrides["target_id"] == "target-1"
    assert result["documents"] == 2
    assert result["contacts"] == 4


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
