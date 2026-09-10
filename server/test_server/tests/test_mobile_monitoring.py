"""Contracts for persistent target and public-account mobile monitors."""
from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError


def _request(**overrides):
    from api.models.mobile_collect import MobileMonitorCreate

    values = {
        "project_id": "project-1",
        "target_id": "target-1",
        "device_id": "device-1",
        "scope": "target",
        "trigger": {"type": "interval", "interval_seconds": 21_600},
        "enabled": False,
    }
    values.update(overrides)
    return MobileMonitorCreate(**values)


def test_account_monitor_requires_and_normalizes_account_names() -> None:
    with pytest.raises(ValidationError, match="至少需要一个公众号名称"):
        _request(scope="official_account")

    request = _request(
        scope="official_account",
        official_accounts=[" 中国气象局 ", "中国气象局", "气象信息中心"],
    )

    assert request.official_accounts == ["中国气象局", "气象信息中心"]


def test_wechat_monitor_provider_uses_existing_handoff_pipeline() -> None:
    from api.services.mobile_monitoring.providers import (
        MobileMonitorProviderRegistry,
        MonitorTargetContext,
    )

    request = _request(
        scope="official_account",
        official_accounts=["中国气象局"],
        app_instance="clone",
    )
    task = MobileMonitorProviderRegistry.resolve(
        "wechat_official"
    ).build_task_definition(
        request,
        MonitorTargetContext("project-1", "target-1", "中国气象局"),
    )

    assert task["source_link_strategy"] == "wechat_copy_link"
    assert task["deep_collect"] is True
    assert task["skip_previously_collected"] is True
    assert task["prefer_recent_items"] is True
    assert task["app_instance"] == "clone"
    assert task["keywords"] == ["中国气象局"]
    assert task["use_target_keyword_library"] is False
    assert task["include_direct_children"] is False


def test_create_monitor_persists_managed_task_and_schedule(monkeypatch) -> None:
    from api.services.mobile_monitoring import service

    created_tasks: list[dict] = []
    created_schedules: list[dict] = []

    async def require_target(_db, **_kwargs):
        return {"target_id": "target-1", "target_name": "中国气象局"}

    async def find_existing(_db, _key):
        return None

    async def create_task(_db, payload):
        created_tasks.append(dict(payload))
        return {**payload, "task_def_id": "task-def-1", "status": "idle"}

    async def create_schedule(_db, **kwargs):
        created_schedules.append(dict(kwargs))
        return {
            "schedule_id": "schedule-1",
            "target_id": kwargs["target_id"],
            "name": kwargs["name"],
            "trigger": kwargs["trigger"],
            "enabled": kwargs["enabled"],
            "metadata": kwargs["metadata"],
        }

    monkeypatch.setattr(service, "require_project_target", require_target)
    monkeypatch.setattr(service, "_find_existing_by_key", find_existing)
    monkeypatch.setattr(service.collect_dao, "create_task_def", create_task)
    monkeypatch.setattr(service.scheduling, "create_schedule", create_schedule)

    result = asyncio.run(service.create_monitor(object(), _request()))

    assert result["monitor_id"].startswith("mon_")
    assert result["task_def_id"] == "task-def-1"
    assert created_tasks[0]["managed_by"] == service.MONITOR_KIND
    assert created_tasks[0]["target_id"] == "target-1"
    assert created_schedules[0]["metadata"]["scope_target_id"] == "target-1"
    assert created_schedules[0]["enabled"] is False


def test_schedule_registry_skips_overlapping_mobile_run(monkeypatch) -> None:
    from api.services import scheduling

    async def busy(*_args, **_kwargs):
        raise scheduling.MobileCollectTaskBusyError("该采集任务正在运行中")

    monkeypatch.setattr(scheduling, "start_mobile_collect_task", busy)
    result = asyncio.run(
        scheduling.trigger_schedule(
            object(),
            {
                "schedule_id": "schedule-1",
                "target_type": "mobile_collect",
                "target_id": "task-def-1",
            },
        )
    )

    assert result.status == "skipped_busy"
    assert "运行中" in result.message
