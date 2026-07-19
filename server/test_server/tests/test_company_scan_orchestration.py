from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from api.services.company_scan_pipeline import CompanyScanPipeline


def test_subsidiary_xhs_is_disabled_by_default() -> None:
    parameter = inspect.signature(CompanyScanPipeline.run_pipeline).parameters[
        "enable_subsidiary_xhs"
    ]

    assert parameter.default is False


def test_xhs_target_selection_is_automatic_by_default() -> None:
    parameter = inspect.signature(CompanyScanPipeline.run_pipeline).parameters[
        "xhs_target_selection_mode"
    ]

    assert parameter.default == "auto"


def test_xhs_collection_is_disabled_by_default() -> None:
    parameter = inspect.signature(CompanyScanPipeline.run_pipeline).parameters[
        "enable_xhs"
    ]

    assert parameter.default is False


def test_bidding_collection_is_enabled_by_default() -> None:
    parameters = inspect.signature(CompanyScanPipeline.run_pipeline).parameters

    assert parameters["enable_bidding"].default is True
    assert parameters["bidding_page_size"].default == 20


def test_wechat_target_selection_is_automatic_by_default() -> None:
    parameter = inspect.signature(CompanyScanPipeline.run_pipeline).parameters[
        "wechat_target_selection_mode"
    ]

    assert parameter.default == "auto"


def test_primary_source_jobs_are_gathered_concurrently() -> None:
    active = 0
    peak = 0

    async def operation() -> str:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "done"

    result = asyncio.run(
        CompanyScanPipeline._gather_named_jobs(
            [("website", operation()), ("wechat", operation()), ("scholar", operation())]
        )
    )

    assert result == ["done", "done", "done"]
    assert peak == 3


def test_scholar_collection_is_opt_in_and_has_direction_parameters() -> None:
    parameters = inspect.signature(CompanyScanPipeline.run_pipeline).parameters

    assert parameters["enable_scholar"].default is False
    assert parameters["scholar_direction"].default == ""


@pytest.mark.asyncio
async def test_scholar_collection_uses_shared_pipeline_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import scholar_contact_pipeline

    captured: dict[str, Any] = {}

    async def collect(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "status": "completed",
            "unit": kwargs["unit"],
            "direction": kwargs["direction"],
            "articles_total": 2,
            "contacts_total": 1,
            "corresponding_count": 1,
        }

    monkeypatch.setattr(scholar_contact_pipeline, "run_scholar_contact_collect", collect)
    result = await CompanyScanPipeline(object(), object())._run_scholar_collection(
        task_id="task-1",
        project_id="project-1",
        unit="安徽广播电视台",
        direction="融媒体技术",
        unit_en="Anhui Broadcasting",
        limit=12,
    )

    assert result["kind"] == "scholar"
    assert result["contacts_total"] == 1
    assert result["direction_source"] == "manual"
    assert captured["task_id"] == "task-1"
    assert captured["unit_en"] == "Anhui Broadcasting"
    assert captured["limit"] == 12
    assert captured["notify_completion"] is False


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

    assert keywords[:3] == [
        "B站 实习",
        "上海宽娱数码科技有限公司 实习",
        "bilibili 实习",
    ]
    assert "bilibili 内推" in keywords
    assert "哔哩哔哩 实习" in keywords
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
                "https://bilibili.com",
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
            [
                "https://bilibili.com",
                "https://manual.example.com",
                "https://new.example.com",
            ],
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


@pytest.mark.asyncio
async def test_wholly_owned_entity_setup_failure_is_aggregated_without_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import notifications

    pipeline = CompanyScanPipeline(object(), object())
    captured: list[dict[str, Any]] = []

    def fail_before_subtasks(_values: list[str]) -> list[str]:
        raise RuntimeError("setup failed")

    def capture_notification(**kwargs: Any) -> bool:
        captured.append(kwargs)
        return True

    monkeypatch.setattr(pipeline, "_dedupe_text", fail_before_subtasks)
    monkeypatch.setattr(
        notifications,
        "notify_target_collection_completed",
        capture_notification,
    )

    result = await pipeline._scan_wholly_owned_entities(
        task_id="task-1",
        project_id="project-1",
        entities=[{"name": "子公司", "target_id": "target-child"}],
        enable_asset_discovery=False,
        enable_url_scan=False,
        enable_copywriting=False,
        enable_xhs=False,
        xhs_max_notes=20,
        xhs_attention_threshold=60,
        min_attention_score=40,
        profile_copywriting_threshold=60,
        fofa_size=200,
        hunter_size=200,
        asset_probe_concurrency=48,
        incremental_scan=False,
        url_probe_concurrency=64,
        url_scan_concurrency=10,
        copywriting_concurrency=6,
        xhs_search_concurrency=3,
        entity_concurrency=4,
    )

    assert result["summary"]["completed"] == 0
    assert result["errors"] == ["子公司: setup failed"]
    assert captured == []


@pytest.mark.asyncio
async def test_wholly_owned_entity_runs_profile_copywriting_after_xhs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import notifications

    pipeline = CompanyScanPipeline(object(), object())
    captured: list[dict[str, Any]] = []

    async def run_xhs(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"notes_count": 4, "profiles_count": 2}

    async def run_profile(*_args: Any, **kwargs: Any) -> int:
        assert kwargs["target_id"] == "target-child"
        return 2

    monkeypatch.setattr(pipeline, "_run_xhs_search", run_xhs)
    monkeypatch.setattr(pipeline, "_run_profile_copywriting", run_profile)
    monkeypatch.setattr(
        notifications,
        "notify_target_collection_completed",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    result = await pipeline._scan_wholly_owned_entities(
        task_id="task-1",
        project_id="project-1",
        entities=[{"name": "子公司", "target_id": "target-child"}],
        enable_asset_discovery=False,
        enable_url_scan=False,
        enable_copywriting=True,
        enable_xhs=True,
        xhs_max_notes=20,
        xhs_attention_threshold=60,
        min_attention_score=40,
        profile_copywriting_threshold=60,
        fofa_size=200,
        hunter_size=200,
        asset_probe_concurrency=48,
        incremental_scan=False,
        url_probe_concurrency=64,
        url_scan_concurrency=10,
        copywriting_concurrency=6,
        xhs_search_concurrency=3,
        entity_concurrency=4,
    )

    assert result["summary"]["completed"] == 1
    assert result["summary"]["profile_copywritings"] == 2
    assert captured == []


@pytest.mark.asyncio
async def test_wholly_owned_entity_skips_xhs_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import notifications

    pipeline = CompanyScanPipeline(object(), object())

    async def unexpected_xhs(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("子公司 XHS 默认关闭时不应执行搜索")

    monkeypatch.setattr(pipeline, "_run_xhs_search", unexpected_xhs)
    monkeypatch.setattr(
        notifications,
        "notify_target_collection_completed",
        lambda **_kwargs: True,
    )

    result = await pipeline._scan_wholly_owned_entities(
        task_id="task-1",
        project_id="project-1",
        entities=[{"name": "子公司", "target_id": "target-child"}],
        enable_asset_discovery=False,
        enable_url_scan=False,
        enable_copywriting=True,
        enable_xhs=False,
        xhs_max_notes=20,
        xhs_attention_threshold=60,
        min_attention_score=40,
        profile_copywriting_threshold=60,
        fofa_size=200,
        hunter_size=200,
        asset_probe_concurrency=48,
        incremental_scan=False,
        url_probe_concurrency=64,
        url_scan_concurrency=10,
        copywriting_concurrency=6,
        xhs_search_concurrency=1,
        entity_concurrency=1,
    )

    assert result["summary"]["completed"] == 1
    assert result["summary"]["xhs_notes"] == 0
    assert result["entities"][0]["scan"]["xhs"] == {
        "enabled": False,
        "keywords_used": [],
    }


@pytest.mark.asyncio
async def test_wholly_owned_entity_respects_target_selection_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import notifications

    pipeline = CompanyScanPipeline(object(), object())

    async def unexpected_xhs(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("被目标选择层跳过后不应执行 XHS 搜索")

    monkeypatch.setattr(pipeline, "_run_xhs_search", unexpected_xhs)
    monkeypatch.setattr(
        notifications,
        "notify_target_collection_completed",
        lambda **_kwargs: True,
    )
    decision = {
        "target_id": "target-public",
        "target_name": "某事业单位",
        "target_category": "public_institution",
        "should_collect_xhs": False,
        "reason": "事业单位默认不采集",
        "confidence": 0.98,
        "source": "ai",
    }

    result = await pipeline._scan_wholly_owned_entities(
        task_id="task-1",
        project_id="project-1",
        entities=[
            {"name": "某事业单位", "target_id": "target-public"},
            {"name": "未取得判定的子公司", "target_id": "target-missing"},
        ],
        enable_asset_discovery=False,
        enable_url_scan=False,
        enable_copywriting=True,
        enable_xhs=True,
        xhs_max_notes=20,
        xhs_attention_threshold=60,
        min_attention_score=40,
        profile_copywriting_threshold=60,
        fofa_size=200,
        hunter_size=200,
        asset_probe_concurrency=48,
        incremental_scan=False,
        url_probe_concurrency=64,
        url_scan_concurrency=10,
        copywriting_concurrency=6,
        xhs_search_concurrency=1,
        entity_concurrency=1,
        xhs_decisions={"target-public": decision},
    )

    assert result["summary"]["xhs_notes"] == 0
    assert result["entities"][0]["scan"]["xhs"] == {
        "enabled": False,
        "keywords_used": [],
        "selection": decision,
    }
    assert result["entities"][1]["scan"]["xhs"] == {
        "enabled": False,
        "keywords_used": [],
    }
    assert result["errors"] == []
