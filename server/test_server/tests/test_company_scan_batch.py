from __future__ import annotations

from typing import Any

import pytest

from api.services.company_scan_batch import plan_company_scan_coverage


@pytest.mark.asyncio
async def test_inflight_company_scan_query_keeps_channel_flags() -> None:
    from api.dao import tasks as tasks_dao

    captured: dict[str, Any] = {}

    class Cursor:
        async def to_list(self, _length: int | None) -> list[dict[str, Any]]:
            return []

    class Collection:
        def find(
            self,
            query: dict[str, Any],
            projection: dict[str, int],
        ) -> Cursor:
            captured["query"] = query
            captured["projection"] = projection
            return Cursor()

    class Database:
        def __getitem__(self, _name: str) -> Collection:
            return Collection()

    await tasks_dao.list_inflight_company_scans(
        Database(),  # type: ignore[arg-type]
        project_id="project-1",
    )

    projection = captured["projection"]
    assert projection["params.enable_wechat"] == 1
    assert projection["params.enable_asset_discovery"] == 1
    assert projection["params.enable_url_scan"] == 1
    assert projection["params.enable_scholar"] == 1
    assert projection["params.enable_bidding"] == 1
    assert projection["params.enable_xhs"] == 1
    assert projection["params.enable_control_structure"] == 1


@pytest.mark.asyncio
async def test_coverage_plan_excludes_finance_and_schedules_only_missing_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao
    from api.dao import tasks as tasks_dao

    async def relations(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "target_id": "public-root",
                "target_name": "公共服务中心",
                "display_name": "公共服务中心",
                "relation_depth": 0,
                "batch_tags": ["第一批"],
                "scan_profile_version": 4,
                "scan_profile_fingerprint": "fp-public",
                "scan_coverage": {
                    "website": {
                        "status": "completed",
                        "profile_fingerprint": "fp-public",
                    }
                },
            },
            {
                "target_id": "finance-root",
                "target_name": "中证信息技术服务有限责任公司",
                "relation_depth": 0,
                "batch_tags": ["第一批"],
            },
            {
                "target_id": "child",
                "target_name": "公共服务子单位",
                "relation_depth": 1,
                "batch_tags": ["第一批"],
            },
        ]

    async def inflight(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(targets_dao, "list_project_targets", relations)
    monkeypatch.setattr(tasks_dao, "list_inflight_company_scans", inflight)

    plan = await plan_company_scan_coverage(
        object(),
        project_id="project-1",
        batch_tag="第一批",
        required_channels=["website", "wechat", "scholar", "bidding"],
        excluded_sectors=["financial"],
        wechat_device_id="device-1",
        subsidiary_scan_limit=8,
    )

    assert plan["planned_count"] == 1
    assert plan["excluded_count"] == 1
    item = plan["items"][0]
    assert item["target_id"] == "public-root"
    assert item["missing_channels"] == ["wechat", "scholar", "bidding"]
    assert item["params"]["enable_asset_discovery"] is False
    assert item["params"]["enable_url_scan"] is False
    assert item["params"]["enable_bidding_visual_analysis"] is True
    assert item["params"]["enable_wechat"] is True
    assert item["params"]["wechat_app_instance"] == "primary"
    assert item["params"]["wechat_target_selection_mode"] == "all"
    assert item["params"]["control_max_depth"] == 2
    assert item["params"]["subsidiary_scan_limit"] == 8


@pytest.mark.asyncio
async def test_coverage_plan_does_not_duplicate_inflight_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao
    from api.dao import tasks as tasks_dao

    async def relations(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "target_id": "target-1",
                "target_name": "目标单位",
                "relation_depth": 0,
                "batch_tags": ["第一批"],
            }
        ]

    async def inflight(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "task_id": "task-running",
                "status": "running",
                "params": {
                    "target_id": "target-1",
                    "company_name": "目标单位",
                },
            }
        ]

    monkeypatch.setattr(targets_dao, "list_project_targets", relations)
    monkeypatch.setattr(tasks_dao, "list_inflight_company_scans", inflight)

    plan = await plan_company_scan_coverage(
        object(),
        project_id="project-1",
        batch_tag="第一批",
        required_channels=["website"],
    )

    assert plan["planned_count"] == 0
    assert plan["inflight_count"] == 1
    assert plan["inflight"][0]["task_id"] == "task-running"


@pytest.mark.asyncio
async def test_coverage_plan_allows_missing_wechat_while_website_is_inflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao
    from api.dao import tasks as tasks_dao

    async def relations(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "target_id": "airport-1",
                "target_name": "示例国际机场",
                "relation_depth": 0,
                "batch_tags": ["机场航空"],
            }
        ]

    async def inflight(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "task_id": "website-running",
                "status": "running",
                "params": {
                    "target_id": "airport-1",
                    "company_name": "示例国际机场",
                    "enable_asset_discovery": True,
                    "enable_url_scan": True,
                    "enable_wechat": False,
                    "enable_scholar": False,
                },
            }
        ]

    monkeypatch.setattr(targets_dao, "list_project_targets", relations)
    monkeypatch.setattr(tasks_dao, "list_inflight_company_scans", inflight)

    plan = await plan_company_scan_coverage(
        object(),
        project_id="project-1",
        batch_tag="机场航空",
        required_channels=["wechat"],
        wechat_device_id="device-1",
        wechat_app_instance="clone",
    )

    assert plan["planned_count"] == 1
    assert plan["inflight_count"] == 0
    item = plan["items"][0]
    assert item["missing_channels"] == ["wechat"]
    assert item["params"]["enable_asset_discovery"] is False
    assert item["params"]["enable_url_scan"] is False
    assert item["params"]["enable_wechat"] is True
    assert item["params"]["wechat_app_instance"] == "clone"
