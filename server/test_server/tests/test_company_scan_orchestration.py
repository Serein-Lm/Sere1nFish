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


def test_scholar_unit_en_reuses_longest_ascii_company_alias() -> None:
    pipeline = CompanyScanPipeline(object(), object())

    assert pipeline._derive_scholar_unit_en(
        ["安徽广播电视台", "AHTV", "Anhui Radio and Television Station"]
    ) == "Anhui Radio and Television Station"
    assert pipeline._derive_scholar_unit_en(
        ["安徽广播电视台", "AHTV"],
        explicit="Anhui Broadcasting Corporation",
    ) == "Anhui Broadcasting Corporation"


def test_named_jobs_write_checkpoint_after_each_success() -> None:
    completed: list[tuple[str, dict[str, Any]]] = []

    async def operation(value: int) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"value": value}

    async def checkpoint(kind: str, result: dict[str, Any]) -> None:
        completed.append((kind, result))

    result = asyncio.run(
        CompanyScanPipeline._gather_named_jobs(
            [("asset_url", operation(1)), ("scholar", operation(2))],
            on_completed=checkpoint,
        )
    )

    assert result == [{"value": 1}, {"value": 2}]
    assert sorted(completed) == [
        ("asset_url", {"value": 1}),
        ("scholar", {"value": 2}),
    ]


class _PipelineUpdateResult:
    matched_count = 1


class _PipelineCollection:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    async def find_one(
        self,
        _query: dict[str, Any],
        _projection: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> None:
        return None

    async def update_one(
        self,
        _query: dict[str, Any],
        update: dict[str, Any],
        **_kwargs: Any,
    ) -> _PipelineUpdateResult:
        self.updates.append(update)
        return _PipelineUpdateResult()

    async def distinct(
        self,
        _field: str,
        _query: dict[str, Any],
    ) -> list[str]:
        return []


class _PipelineDb:
    def __init__(self) -> None:
        self.collection = _PipelineCollection()

    def __getitem__(self, _name: str) -> _PipelineCollection:
        return self.collection


@pytest.mark.asyncio
async def test_mobile_wait_does_not_block_wholly_owned_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import company_meta as company_meta_dao
    from api.dao import targets as targets_dao
    from api.services import company_normalize
    from api.services import targets as targets_service
    from Sere1nGraph.graph.company_router.router import CompanyRouterResult

    db = _PipelineDb()
    pipeline = CompanyScanPipeline(db, object())  # type: ignore[arg-type]
    mobile_release = asyncio.Event()
    followup_started = asyncio.Event()

    async def normalize(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "normalized_name": "目标公司",
            "root_domain": "target.example",
            "aliases": ["目标公司"],
            "source": "test",
            "provenance": {},
        }

    async def route(*_args: Any, **_kwargs: Any) -> CompanyRouterResult:
        return CompanyRouterResult(success=False, error="test fallback")

    async def attach(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"target_id": "target-root", "canonical_name": "目标公司"}

    async def upsert_meta(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)

    async def noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def run_control(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "kind": "control_structure",
            "result": {
                "status": "completed",
                "entities": [
                    {"name": "目标子公司", "target_id": "target-child"}
                ],
                "errors": [],
            },
        }

    async def run_assets(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "kind": "asset_url",
            "assets": {"enabled": True, "alive": 1},
            "url_scan": {"enabled": False},
        }

    async def run_mobile(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs["started_event"].set()
        await mobile_release.wait()
        return {
            "kind": "wechat",
            "status": "completed",
            "total": 1,
            "documents": 1,
        }

    async def run_followup(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        followup_started.set()
        return {
            "kind": "wholly_owned_entities",
            "entities": list(kwargs["entities"]),
            "summary": {"completed": 1, "profile_copywritings": 0},
            "errors": [],
        }

    monkeypatch.setattr(company_normalize, "normalize_company", normalize)
    monkeypatch.setattr(targets_service, "attach_normalized_company", attach)
    monkeypatch.setattr(company_meta_dao, "upsert_company_meta", upsert_meta)
    monkeypatch.setattr(targets_dao, "link_project_target", noop)
    monkeypatch.setattr(targets_dao, "touch_project_target_collection", noop)
    monkeypatch.setattr(pipeline, "_run_company_router", route)
    monkeypatch.setattr(pipeline, "_run_wholly_owned_investments", run_control)
    monkeypatch.setattr(pipeline, "_run_asset_and_url_scan", run_assets)
    monkeypatch.setattr(pipeline, "_run_wechat_collection", run_mobile)
    monkeypatch.setattr(pipeline, "_scan_wholly_owned_entities", run_followup)
    monkeypatch.setattr(pipeline, "_update_progress", noop)

    pipeline_task = asyncio.create_task(
        pipeline.run_pipeline(
            task_id="task-parallel",
            project_id="project-1",
            company_name="目标公司",
            batch_id="batch-1",
            enable_url_scan=False,
            enable_asset_discovery=True,
            enable_xhs=False,
            enable_bidding=False,
            enable_wechat=True,
            wechat_target_selection_mode="all",
            enable_scholar=False,
            enable_control_structure=True,
            enable_copywriting=False,
            company_core_concurrency=1,
        )
    )
    try:
        await asyncio.wait_for(followup_started.wait(), timeout=5)
        assert not mobile_release.is_set()
        assert not pipeline_task.done()
    finally:
        mobile_release.set()
        await asyncio.wait_for(asyncio.shield(pipeline_task), timeout=5)

    result = pipeline_task.result()
    assert result["status"] == "completed"
    assert result["wechat"]["documents"] == 1
    assert result["control_structure"]["scan_summary"]["completed"] == 1
    update_fields = [
        update.get("$set", {}) for update in db.collection.updates
    ]
    assert any(fields.get("resume.core_completed") is True for fields in update_fields)
    assert any(fields.get("resume.mobile_completed") is True for fields in update_fields)


@pytest.mark.asyncio
async def test_wechat_checkpoint_wins_over_incomplete_mobile_resume_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import company_meta as company_meta_dao
    from api.dao import targets as targets_dao
    from api.services import company_scan_recovery
    from api.services import targets as targets_service

    db = _PipelineDb()
    pipeline = CompanyScanPipeline(db, object())  # type: ignore[arg-type]
    checkpoint_result = {
        "kind": "wechat",
        "status": "completed",
        "total": 3,
        "documents": 2,
        "from_checkpoint": True,
    }

    async def load_state(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "resume": {"core_completed": True, "mobile_completed": False},
            "modules": {
                "wechat": {
                    "status": "completed",
                    "result": checkpoint_result,
                }
            },
        }

    async def restore_identity(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "input_name": "目标公司",
            "normalized_name": "目标公司",
            "root_domain": "target.example",
            "root_domains": ["target.example"],
            "aliases": ["目标公司"],
            "target_id": "target-root",
        }

    async def get_meta(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "normalized_name": "目标公司",
            "root_domain": "target.example",
            "icp_domains": ["target.example"],
            "aliases": ["目标公司"],
            "target_id": "target-root",
            "source": "test",
            "provenance": {},
        }

    async def upsert_meta(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)

    async def attach(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"target_id": "target-root", "canonical_name": "目标公司"}

    async def noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def unexpected_mobile(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("已有公众号模块检查点时不应重新启动手机采集")

    monkeypatch.setattr(company_scan_recovery, "load_recovery_state", load_state)
    monkeypatch.setattr(company_scan_recovery, "restore_identity", restore_identity)
    monkeypatch.setattr(company_meta_dao, "get_company_meta", get_meta)
    monkeypatch.setattr(company_meta_dao, "upsert_company_meta", upsert_meta)
    monkeypatch.setattr(targets_service, "attach_normalized_company", attach)
    monkeypatch.setattr(targets_dao, "link_project_target", noop)
    monkeypatch.setattr(targets_dao, "touch_project_target_collection", noop)
    monkeypatch.setattr(pipeline, "_run_wechat_collection", unexpected_mobile)
    monkeypatch.setattr(pipeline, "_update_progress", noop)

    result = await pipeline.run_pipeline(
        task_id="task-wechat-checkpoint",
        project_id="project-1",
        company_name="目标公司",
        batch_id="batch-1",
        enable_url_scan=False,
        enable_asset_discovery=False,
        enable_xhs=False,
        enable_bidding=False,
        enable_wechat=True,
        enable_scholar=False,
        enable_control_structure=False,
        enable_copywriting=False,
    )

    assert result["status"] == "completed"
    assert result["wechat"]["from_checkpoint"] is True
    update_fields = [
        update.get("$set", {}) for update in db.collection.updates
    ]
    assert any(fields.get("resume.mobile_completed") is True for fields in update_fields)


@pytest.mark.asyncio
async def test_retryable_url_child_reopens_only_asset_module_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import company_meta as company_meta_dao
    from api.dao import targets as targets_dao
    from api.services import company_normalize
    from api.services import company_scan_recovery
    from api.services import targets as targets_service

    db = _PipelineDb()
    pipeline = CompanyScanPipeline(db, object())  # type: ignore[arg-type]
    asset_runs = 0

    async def load_state(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "resume": {"core_completed": True, "mobile_completed": True},
            "modules": {
                "asset_url": {
                    "status": "completed",
                    "result": {
                        "kind": "asset_url",
                        "assets": {"alive": 99},
                        "url_scan": {"status": "completed"},
                    },
                }
            },
        }

    async def retryable_modules(*_args: Any, **_kwargs: Any) -> set[str]:
        return {"asset_url"}

    async def restore_identity(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "input_name": "目标公司",
            "normalized_name": "目标公司",
            "root_domain": "target.example",
            "root_domains": ["target.example"],
            "aliases": ["目标公司"],
            "target_id": "target-root",
        }

    async def get_meta(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "normalized_name": "目标公司",
            "root_domain": "target.example",
            "icp_domains": ["target.example"],
            "aliases": ["目标公司"],
            "target_id": "target-root",
            "source": "test",
            "provenance": {},
        }

    async def upsert_meta(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)

    async def attach(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"target_id": "target-root", "canonical_name": "目标公司"}

    async def noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def unexpected(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("恢复 URL 子任务时不应重复公司规范化或路由")

    async def run_assets(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal asset_runs
        asset_runs += 1
        return {
            "kind": "asset_url",
            "assets": {"enabled": True, "alive": 2},
            "url_scan": {
                "enabled": True,
                "status": "completed",
                "scanned_urls": 2,
                "failed_urls": 0,
            },
        }

    monkeypatch.setattr(company_scan_recovery, "load_recovery_state", load_state)
    monkeypatch.setattr(
        company_scan_recovery,
        "find_retryable_core_modules",
        retryable_modules,
    )
    monkeypatch.setattr(company_scan_recovery, "restore_identity", restore_identity)
    monkeypatch.setattr(company_meta_dao, "get_company_meta", get_meta)
    monkeypatch.setattr(company_meta_dao, "upsert_company_meta", upsert_meta)
    monkeypatch.setattr(company_normalize, "normalize_company", unexpected)
    monkeypatch.setattr(targets_service, "attach_normalized_company", attach)
    monkeypatch.setattr(targets_dao, "link_project_target", noop)
    monkeypatch.setattr(targets_dao, "touch_project_target_collection", noop)
    monkeypatch.setattr(pipeline, "_run_company_router", unexpected)
    monkeypatch.setattr(pipeline, "_run_asset_and_url_scan", run_assets)
    monkeypatch.setattr(pipeline, "_update_progress", noop)

    result = await pipeline.run_pipeline(
        task_id="task-retryable-asset",
        project_id="project-1",
        company_name="目标公司",
        batch_id="batch-1",
        enable_url_scan=True,
        enable_asset_discovery=True,
        enable_xhs=False,
        enable_bidding=False,
        enable_wechat=False,
        enable_scholar=False,
        enable_control_structure=False,
        enable_copywriting=False,
    )

    assert asset_runs == 1
    assert result["status"] == "completed"
    assert result["assets"]["alive"] == 2
    assert result["url_scan"]["scanned_urls"] == 2


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
