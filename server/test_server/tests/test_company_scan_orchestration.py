from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from api.services.company_scan_pipeline import CompanyScanPipeline


class _TargetCollection:
    def __init__(self) -> None:
        self.existing = {
            "target_id": "tgt_brand",
            "target_type": "company",
            "canonical_name": "B站",
            "normalized_name": "b站",
            "aliases_normalized": ["b站"],
        }
        self.update_filter: dict[str, Any] = {}
        self.update: dict[str, Any] = {}

    async def find_one(self, query: dict[str, Any], *_args: Any) -> dict[str, Any]:
        if "$or" in query:
            return dict(self.existing)
        return {
            **self.existing,
            "canonical_name": "上海宽娱数码科技有限公司",
            "root_domain": "bilibili.com",
        }

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        **_kwargs: Any,
    ) -> None:
        self.update_filter = query
        self.update = update


class _TargetDb:
    def __init__(self, collection: _TargetCollection) -> None:
        self.collection = collection

    def __getitem__(self, _name: str) -> _TargetCollection:
        return self.collection


def test_xhs_keywords_keep_brand_aliases_and_are_deterministic() -> None:
    pipeline = CompanyScanPipeline(object(), object())
    router = SimpleNamespace(
        success=True,
        all_keywords={"xhs": ["B站 实习", "B站 实习", "bilibili 内推"]},
    )

    keywords = pipeline._get_xhs_keywords(
        ["B站", "上海宽娱数码科技有限公司", "bilibili", "哔哩哔哩"],
        router,
    )

    assert keywords[:2] == ["B站 实习", "bilibili 内推"]
    assert "哔哩哔哩 招聘" in keywords
    assert len(keywords) == len(set(keywords))


@pytest.mark.asyncio
async def test_legal_company_reuses_existing_brand_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao

    async def no_direct_match(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(targets_dao, "find_target", no_direct_match)
    collection = _TargetCollection()
    target = await targets_dao.upsert_target(
        _TargetDb(collection),
        name="上海宽娱数码科技有限公司",
        root_domain="bilibili.com",
        aliases=["B站", "bilibili", "哔哩哔哩"],
        source="company_normalize",
    )

    assert collection.update_filter == {"target_id": "tgt_brand"}
    assert collection.update["$set"]["canonical_name"] == "上海宽娱数码科技有限公司"
    assert target["target_id"] == "tgt_brand"


@pytest.mark.asyncio
async def test_low_authority_alias_does_not_downgrade_canonical_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao

    async def no_direct_match(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(targets_dao, "find_target", no_direct_match)
    collection = _TargetCollection()
    collection.existing.update(
        canonical_name="上海宽娱数码科技有限公司",
        normalized_name="上海宽娱数码科技有限公司",
        root_domain="bilibili.com",
    )
    await targets_dao.upsert_target(
        _TargetDb(collection),
        name="B站",
        aliases=["哔哩哔哩"],
        source="mobile_collect_task",
    )

    assert collection.update["$set"]["canonical_name"] == "上海宽娱数码科技有限公司"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("incremental_scan", "expected_urls", "expected_known_alive"),
    [
        (
            False,
            [
                "https://manual.example.com",
                "https://new.example.com",
                "https://stable.example.com",
            ],
            [
                "https://new.example.com",
                "https://manual.example.com",
                "https://stable.example.com",
            ],
        ),
        (
            True,
            ["https://manual.example.com", "https://new.example.com"],
            ["https://new.example.com"],
        ),
    ],
)
async def test_asset_and_manual_urls_share_one_deep_scan(
    monkeypatch: pytest.MonkeyPatch,
    incremental_scan: bool,
    expected_urls: list[str],
    expected_known_alive: list[str],
) -> None:
    from api.services.asset_intelligence import AssetIntelligenceService

    async def discover(_self: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "enabled": True,
            "discovered": 3,
            "alive": 3,
            "inserted": 1,
            "updated": 1,
            "unchanged": 1,
            "providers": {},
            "alive_urls": [
                "https://new.example.com",
                "https://manual.example.com",
                "https://stable.example.com",
            ],
            "scan_urls": ["https://new.example.com"],
        }

    monkeypatch.setattr(AssetIntelligenceService, "discover", discover)
    pipeline = CompanyScanPipeline(object(), object())
    calls: list[dict[str, Any]] = []

    async def run_url_scan(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        return {"findings_count": 3, "copywritings_count": 1, "status": "completed"}

    monkeypatch.setattr(pipeline, "_run_url_scan", run_url_scan)
    result = await pipeline._run_asset_and_url_scan(
        task_id="task-1",
        project_id="project-1",
        identity={
            "input_name": "B站",
            "normalized_name": "上海宽娱数码科技有限公司",
            "root_domain": "bilibili.com",
            "target_id": "tgt-1",
            "aliases": ["B站", "bilibili"],
        },
        url_text="https://text.example.com",
        urls=["https://manual.example.com"],
        enable_asset_discovery=True,
        enable_url_scan=True,
        enable_copywriting=True,
        min_attention_score=40,
        fofa_size=100,
        hunter_size=100,
        probe_concurrency=48,
        incremental_scan=incremental_scan,
    )

    assert result["kind"] == "asset_url"
    assert result["url_scan"]["findings_count"] == 3
    assert len(calls) == 1
    assert calls[0]["args"][3] == expected_urls
    assert calls[0]["kwargs"]["known_alive_urls"] == expected_known_alive
    assert calls[0]["kwargs"]["target_id"] == "tgt-1"
    assert result["assets"]["scan_mode"] == ("incremental" if incremental_scan else "full")
    assert result["assets"]["scan_candidates"] == len(expected_known_alive)
