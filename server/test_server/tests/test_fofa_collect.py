from typing import Any

import pytest

from api.services.fofa_collect import run_fofa_collect


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("incremental_scan", "expected_urls"),
    [
        (False, ["https://new.example.com", "https://stable.example.com"]),
        (True, ["https://new.example.com"]),
    ],
)
async def test_asset_collect_uses_explicit_full_or_incremental_scan_mode(
    monkeypatch: pytest.MonkeyPatch,
    incremental_scan: bool,
    expected_urls: list[str],
) -> None:
    from api.dao import targets as targets_dao
    from api.services import company_normalize
    from api.services import notifications
    from api.services.asset_intelligence import AssetIntelligenceService
    from api.services.url_scan_pipeline import UrlScanPipeline

    async def normalize(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "normalized_name": "示例公司",
            "root_domain": "example.com",
            "target_id": "target-1",
            "aliases": ["示例"],
        }

    async def discover(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "discovered": 2,
            "alive": 2,
            "inserted": 1,
            "updated": 0,
            "unchanged": 1,
            "alive_urls": ["https://new.example.com", "https://stable.example.com"],
            "scan_urls": ["https://new.example.com"],
            "providers": {},
        }

    scan_calls: list[dict[str, Any]] = []

    async def scan(_self: Any, **kwargs: Any) -> dict[str, Any]:
        scan_calls.append(kwargs)
        return {"status": "completed"}

    async def touch(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(company_normalize, "normalize_company", normalize)
    monkeypatch.setattr(AssetIntelligenceService, "discover", discover)
    monkeypatch.setattr(UrlScanPipeline, "run_pipeline", scan)
    monkeypatch.setattr(targets_dao, "touch_project_target_collection", touch)
    monkeypatch.setattr(
        notifications,
        "notify_target_collection_completed",
        lambda **_kwargs: True,
    )

    result = await run_fofa_collect(
        object(),
        object(),
        task_id="task-1",
        project_id="project-1",
        company_name="示例",
        incremental_scan=incremental_scan,
    )

    assert result["status"] == "completed"
    assert result["scan_mode"] == ("incremental" if incremental_scan else "full")
    assert result["scan_candidates"] == len(expected_urls)
    assert len(scan_calls) == 1
    assert scan_calls[0]["url_content"].splitlines() == expected_urls
    assert scan_calls[0]["known_alive_urls"] == expected_urls
