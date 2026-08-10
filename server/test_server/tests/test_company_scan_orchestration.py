from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from api.services.company_scan_pipeline import (
    CompanyScanPipeline,
    related_entity_task_id,
    should_checkpoint_module,
)


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


def test_tianyancha_collection_is_disabled_by_default() -> None:
    parameters = inspect.signature(CompanyScanPipeline.run_pipeline).parameters

    assert parameters["enable_bidding"].default is False
    assert parameters["enable_control_structure"].default is False
    assert parameters["bidding_page_size"].default == 20
    assert parameters["bidding_max_records"].default == 20


def test_partial_followup_module_is_not_cached_as_complete() -> None:
    assert should_checkpoint_module(
        "wholly_owned_entities",
        {"status": "partial"},
    ) is False
    assert should_checkpoint_module(
        "scholar_entities",
        {"status": "completed"},
    ) is True
    assert should_checkpoint_module(
        "bidding",
        {"status": "partial"},
    ) is True


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


def test_scholar_collection_is_enabled_by_default_and_has_direction_parameters() -> None:
    parameters = inspect.signature(CompanyScanPipeline.run_pipeline).parameters

    assert parameters["enable_scholar"].default is True
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
        target_id="target-1",
        unit="安徽广播电视台",
        direction="融媒体技术",
        unit_en="Anhui Broadcasting",
        limit=12,
    )

    assert result["kind"] == "scholar"
    assert result["contacts_total"] == 1
    assert result["direction_source"] == "manual"
    assert captured["task_id"] == "task-1"
    assert captured["target_id"] == "target-1"
    assert captured["unit_en"] == "Anhui Broadcasting"
    assert captured["limit"] == 12
    assert captured["notify_completion"] is False


@pytest.mark.asyncio
async def test_standalone_scholar_dispatcher_returns_collection_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.routers import project_api
    from api.services import runtime_config, scholar_contact_pipeline

    summary = {
        "status": "completed",
        "articles_total": 4,
        "contacts_total": 2,
    }

    async def get_config() -> object:
        return object()

    async def collect(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["target_id"] == "target-1"
        assert kwargs["dry_run"] is True
        return summary

    monkeypatch.setattr(project_api, "get_db", lambda: object())
    monkeypatch.setattr(runtime_config, "get_runtime_app_config", get_config)
    monkeypatch.setattr(scholar_contact_pipeline, "run_scholar_contact_collect", collect)

    result = await project_api._dispatch_scholar_contact(
        "task-1",
        "project-1",
        {
            "target_id": "target-1",
            "unit": "示例单位",
            "direction": "epidemiology",
            "dry_run": True,
        },
    )

    assert result == summary


@pytest.mark.asyncio
async def test_related_entity_scholar_collection_is_serial_and_target_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import task_progress

    pipeline = CompanyScanPipeline(object(), object())
    active = 0
    peak = 0
    calls: list[dict[str, Any]] = []

    async def collect(**kwargs: Any) -> dict[str, Any]:
        nonlocal active, peak
        calls.append(kwargs)
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {
            "kind": "scholar",
            "status": "completed",
            "articles_total": 2,
            "verified_articles_total": 1,
            "unverified_articles_total": 1,
            "contacts_total": 1,
            "corresponding_count": 1,
        }

    async def progress(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(pipeline, "_run_scholar_collection", collect)
    monkeypatch.setattr(task_progress, "update_source_progress", progress)

    result = await pipeline._scan_scholar_entities(
        task_id="task-1",
        project_id="project-1",
        entities=[
            {
                "name": "目标子公司",
                "target_id": "target-child",
                "relation_depth": 1,
                "aliases": ["Child Company"],
            },
            {
                "name": "目标孙公司",
                "target_id": "target-grandchild",
                "parent_target_id": "target-child",
                "relation_depth": 2,
            },
        ],
        manual_direction="人工智能",
        limit=10,
        entity_concurrency=1,
    )

    assert peak == 1
    assert [call["target_id"] for call in calls] == [
        "target-child",
        "target-grandchild",
    ]
    assert calls[0]["unit_en"] == "Child Company"
    assert all(call["direction"] == "人工智能" for call in calls)
    assert result["status"] == "completed"
    assert result["summary"] == {
        "entities": 2,
        "completed": 2,
        "articles_total": 4,
        "verified_articles_total": 2,
        "unverified_articles_total": 2,
        "contacts_total": 2,
        "corresponding_count": 2,
    }
    assert [item["relation_depth"] for item in result["entities"]] == [1, 2]


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
    assert pipeline._derive_scholar_unit_en(
        ["教育部教育考试院", "NEEA"]
    ) == ""
    assert pipeline._derive_scholar_unit_en(
        ["教育部教育考试院", "NEEA"],
        explicit="National Education Examinations Authority",
    ) == "National Education Examinations Authority"


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

    async def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.updates.append(update)
        return {**query, **dict(update.get("$set") or {})}

    def find(
        self,
        _query: dict[str, Any],
        _projection: dict[str, Any] | None = None,
    ) -> "_PipelineCursor":
        return _PipelineCursor([])

    async def distinct(
        self,
        _field: str,
        _query: dict[str, Any],
    ) -> list[str]:
        return []


class _PipelineCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def __aiter__(self):
        self.iterator = iter(self.rows)
        return self

    def sort(self, *_args: Any, **_kwargs: Any):
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self.iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _PipelineDb:
    def __init__(self) -> None:
        self.collection = _PipelineCollection()

    def __getitem__(self, _name: str) -> _PipelineCollection:
        return self.collection


@pytest.mark.asyncio
async def test_company_scan_keeps_a_pinned_related_target_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import company_meta as company_meta_dao
    from api.dao import targets as targets_dao
    from api.services import company_normalize
    from api.services import targets as targets_service
    from Sere1nGraph.graph.company_router.router import CompanyRouterResult

    db = _PipelineDb()
    pipeline = CompanyScanPipeline(db, object())  # type: ignore[arg-type]
    attached: dict[str, Any] = {}
    stored_meta: dict[str, Any] = {}

    async def get_target(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "target_id": "target-child",
            "canonical_name": "目标子公司",
            "root_domain": "child.example",
            "root_domains": ["child.example"],
            "aliases": ["目标子公司", "污染母公司"],
            "identity_aliases": ["目标子公司"],
        }

    async def normalize(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "normalized_name": "错误母公司",
            "root_domain": "parent.example",
            "icp_domains": ["parent.example"],
            "aliases": ["错误母公司", "母公司平台"],
            "source": "test",
            "provenance": {},
        }

    async def route(*_args: Any, **_kwargs: Any) -> CompanyRouterResult:
        return CompanyRouterResult(success=False, error="test fallback")

    async def attach(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        attached.update(kwargs)
        return {
            "target_id": "target-child",
            "canonical_name": "目标子公司",
        }

    async def upsert_meta(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        stored_meta.update(kwargs)
        return dict(kwargs)

    async def noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(targets_dao, "get_target", get_target)
    monkeypatch.setattr(company_normalize, "normalize_company", normalize)
    monkeypatch.setattr(targets_service, "attach_normalized_company", attach)
    monkeypatch.setattr(company_meta_dao, "upsert_company_meta", upsert_meta)
    monkeypatch.setattr(targets_dao, "link_project_target", noop)
    monkeypatch.setattr(targets_dao, "touch_project_target_collection", noop)
    monkeypatch.setattr(pipeline, "_run_company_router", route)
    monkeypatch.setattr(pipeline, "_update_progress", noop)

    result = await pipeline.run_pipeline(
        task_id="task-pinned-target",
        project_id="project-1",
        company_name="目标子公司",
        target_id="target-child",
        enable_url_scan=False,
        enable_asset_discovery=False,
        enable_xhs=False,
        enable_bidding=False,
        enable_wechat=False,
        enable_scholar=False,
        enable_control_structure=False,
        enable_copywriting=False,
    )

    assert attached["preferred_target_id"] == "target-child"
    assert attached["normalized_name"] == "目标子公司"
    assert attached["root_domains"] == ["child.example"]
    assert "错误母公司" not in attached["aliases"]
    assert "污染母公司" not in attached["aliases"]
    assert stored_meta["target_id"] == "target-child"
    assert stored_meta["normalized_name"] == "目标子公司"
    assert result["identity"]["target_id"] == "target-child"
    assert result["identity"]["normalization_error"] is None


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
async def test_wechat_start_updates_source_without_overwriting_main_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import wechat_collection

    db = _PipelineDb()
    pipeline = CompanyScanPipeline(db, object())  # type: ignore[arg-type]
    started = asyncio.Event()

    async def run_wechat(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        await kwargs["on_started"]()
        return {"total": 0, "new": 0, "changed": 0}

    monkeypatch.setattr(
        wechat_collection,
        "run_company_wechat_collection",
        run_wechat,
    )

    result = await pipeline._run_wechat_collection(
        task_id="task-wechat-stage",
        project_id="project-1",
        target_id="target-1",
        target_name="目标公司",
        device_id="device-1",
        started_event=started,
    )

    assert started.is_set()
    assert result["total"] == 0
    fields = db.collection.updates[-1]["$set"]
    assert fields["progress.sources.wechat.status"] == "running"
    assert fields["progress.sources.wechat.message"] == "公众号任务已获得手机，正在采集"
    assert "progress.stage" not in fields


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
async def test_legal_company_does_not_reuse_an_untrusted_search_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao

    async def no_direct_match(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(targets_dao, "find_target", no_direct_match)
    collection = _TargetCollection()
    await targets_dao.upsert_target(
        _TargetDb(collection),
        name="上海宽娱数码科技有限公司",
        root_domain="bilibili.com",
        aliases=["B站", "bilibili", "哔哩哔哩"],
        source="company_normalize",
    )

    assert collection.update_filter == {
        "target_id": targets_dao.target_id_for_name("上海宽娱数码科技有限公司")
    }
    assert collection.update["$set"]["canonical_name"] == "上海宽娱数码科技有限公司"


def test_trusted_identity_aliases_keep_only_canonical_and_seed_name() -> None:
    from api.dao import targets as targets_dao

    target = {
        "target_id": targets_dao.target_id_for_name("B站"),
        "target_type": "company",
        "canonical_name": "上海宽娱数码科技有限公司",
        "aliases": [
            "B站",
            "哔哩哔哩",
            "上海宽娱数码科技有限公司",
            "无关子公司",
        ],
    }

    assert targets_dao.trusted_identity_aliases(target) == [
        "上海宽娱数码科技有限公司",
        "B站",
    ]


@pytest.mark.asyncio
async def test_normalized_company_promotes_only_the_exact_brand_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao
    from api.services import targets as targets_service

    upsert_kwargs: dict[str, Any] = {}

    async def find_exact(_db: Any, *, name: str, **_kwargs: Any):
        if name == "B站":
            return {
                "target_id": "tgt_brand",
                "canonical_name": "B站",
            }
        return None

    async def upsert(_db: Any, **kwargs: Any) -> dict[str, Any]:
        upsert_kwargs.update(kwargs)
        return {
            "target_id": kwargs.get("preferred_target_id") or "new-target",
            "canonical_name": kwargs["name"],
        }

    async def link(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(targets_dao, "find_target_exact_name", find_exact)
    monkeypatch.setattr(targets_dao, "upsert_target", upsert)
    monkeypatch.setattr(targets_dao, "link_project_target", link)

    target = await targets_service.attach_normalized_company(
        object(),
        project_id="project-1",
        input_name="B站",
        normalized_name="上海宽娱数码科技有限公司",
        root_domain="bilibili.com",
        aliases=["哔哩哔哩"],
    )

    assert target["target_id"] == "tgt_brand"
    assert upsert_kwargs["match_aliases"] is False
    assert upsert_kwargs["preferred_target_id"] == "tgt_brand"
    assert upsert_kwargs["identity_aliases"] == ["B站"]
    assert upsert_kwargs["preserve_canonical_name"] is False


@pytest.mark.asyncio
async def test_normalized_company_preserves_an_explicit_target_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao
    from api.services import targets as targets_service

    upsert_kwargs: dict[str, Any] = {}

    async def get_target(_db: Any, target_id: str) -> dict[str, Any]:
        assert target_id == "target-child"
        return {
            "target_id": "target-child",
            "canonical_name": "目标子公司",
        }

    async def upsert(_db: Any, **kwargs: Any) -> dict[str, Any]:
        upsert_kwargs.update(kwargs)
        return {
            "target_id": "target-child",
            "canonical_name": kwargs["name"],
        }

    async def link(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(targets_dao, "get_target", get_target)
    monkeypatch.setattr(targets_dao, "upsert_target", upsert)
    monkeypatch.setattr(targets_dao, "link_project_target", link)

    target = await targets_service.attach_normalized_company(
        object(),
        project_id="project-1",
        input_name="子公司平台",
        normalized_name="错误母公司",
        root_domain="child.example",
        preferred_target_id="target-child",
    )

    assert target["target_id"] == "target-child"
    assert upsert_kwargs["name"] == "目标子公司"
    assert upsert_kwargs["preferred_target_id"] == "target-child"
    assert upsert_kwargs["preserve_canonical_name"] is True
    assert upsert_kwargs["identity_aliases"] == []


@pytest.mark.asyncio
async def test_normalized_legal_entities_do_not_merge_by_shared_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao
    from api.services import targets as targets_service

    upserts: list[dict[str, Any]] = []

    async def no_exact(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def upsert(_db: Any, **kwargs: Any) -> dict[str, Any]:
        upserts.append(kwargs)
        return {
            "target_id": targets_dao.target_id_for_name(kwargs["name"]),
            "canonical_name": kwargs["name"],
        }

    async def link(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(targets_dao, "find_target_exact_name", no_exact)
    monkeypatch.setattr(targets_dao, "upsert_target", upsert)
    monkeypatch.setattr(targets_dao, "link_project_target", link)

    first = await targets_service.attach_normalized_company(
        object(),
        project_id="project-1",
        input_name="广州港集团有限公司",
        normalized_name="广州港集团有限公司",
        root_domain="gzport.com",
    )
    second = await targets_service.attach_normalized_company(
        object(),
        project_id="project-1",
        input_name="广州港物流有限公司",
        normalized_name="广州港物流有限公司",
        root_domain="gzport.com",
    )

    assert first["target_id"] != second["target_id"]
    assert all(item["match_aliases"] is False for item in upserts)
    assert all(not item["preferred_target_id"] for item in upserts)


@pytest.mark.asyncio
async def test_trusted_identity_alias_does_not_downgrade_canonical_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao

    collection = _TargetCollection()
    collection.existing.update(
        canonical_name="上海宽娱数码科技有限公司",
        normalized_name="上海宽娱数码科技有限公司",
        root_domain="bilibili.com",
    )

    async def trusted_match(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return dict(collection.existing)

    monkeypatch.setattr(targets_dao, "find_target", trusted_match)
    await targets_dao.upsert_target(
        _TargetDb(collection),
        name="B站",
        aliases=["哔哩哔哩"],
        source="mobile_collect_task",
    )

    assert collection.update["$set"]["canonical_name"] == "上海宽娱数码科技有限公司"


@pytest.mark.asyncio
async def test_control_entity_identity_does_not_reuse_a_shared_brand_alias() -> None:
    from api.dao import targets as targets_dao

    class _NoExactLegalTarget(_TargetCollection):
        async def find_one(
            self,
            query: dict[str, Any],
            *_args: Any,
        ) -> dict[str, Any] | None:
            if "$or" in query:
                return dict(self.existing)
            return None

    collection = _NoExactLegalTarget()
    target = await targets_dao.upsert_target(
        _TargetDb(collection),
        name="上海银清企业服务有限公司",
        aliases=["银清企业"],
        source="tianyancha_outbound_investment",
        match_aliases=False,
    )

    assert collection.update_filter == {
        "target_id": targets_dao.target_id_for_name(
            "上海银清企业服务有限公司",
            "company",
        )
    }
    assert target["target_id"] != "tgt_brand"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "incremental_scan",
        "recovery_required",
        "expected_urls",
        "expected_known_alive",
        "expected_scan_mode",
    ),
    [
        (
            False,
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
            "full",
        ),
        (
            True,
            False,
            [
                "https://bilibili.com",
                "https://manual.example.com",
                "https://new.example.com",
            ],
            ["https://new.example.com"],
            "incremental",
        ),
        (
            True,
            True,
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
            "full",
        ),
    ],
)
async def test_asset_and_manual_urls_share_one_deep_scan(
    monkeypatch: pytest.MonkeyPatch,
    incremental_scan: bool,
    recovery_required: bool,
    expected_urls: list[str],
    expected_known_alive: list[str],
    expected_scan_mode: str,
) -> None:
    from api.dao import url_scan as url_scan_dao
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

    async def no_recovery(*_args: Any, **_kwargs: Any) -> bool:
        return recovery_required

    monkeypatch.setattr(
        url_scan_dao,
        "task_requires_full_scan",
        no_recovery,
    )
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
    assert result["assets"]["scan_mode"] == expected_scan_mode
    assert result["assets"]["recovery_full_scan"] is recovery_required
    assert result["assets"]["scan_candidates"] == len(expected_known_alive)


@pytest.mark.asyncio
async def test_wholly_owned_entity_setup_failure_is_aggregated_without_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import notifications

    pipeline = CompanyScanPipeline(_PipelineDb(), object())  # type: ignore[arg-type]
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
    assert result["status"] == "error"
    assert result["errors"] == ["子公司: setup failed"]
    assert captured == []


@pytest.mark.asyncio
async def test_wholly_owned_url_scan_reports_progress_to_parent_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = CompanyScanPipeline(_PipelineDb(), object())  # type: ignore[arg-type]
    calls: list[dict[str, Any]] = []

    async def run_assets(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "kind": "asset_url",
            "assets": {"alive": 0},
            "url_scan": {"status": "completed"},
        }

    monkeypatch.setattr(pipeline, "_run_asset_and_url_scan", run_assets)

    result = await pipeline._scan_wholly_owned_entities(
        task_id="parent-task",
        project_id="project-1",
        entities=[{"name": "子公司", "target_id": "target-child"}],
        enable_asset_discovery=True,
        enable_url_scan=True,
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
        xhs_search_concurrency=1,
        entity_concurrency=1,
    )

    assert result["summary"]["completed"] == 1
    assert calls[0]["progress_task_id"] == "parent-task"
    assert calls[0]["task_id"] == related_entity_task_id(
        "parent-task",
        target_id="target-child",
        name="子公司",
    )
    assert calls[0]["progress_source"].startswith("entity_")
    assert calls[0]["progress_source"].endswith("_url_scan")


@pytest.mark.asyncio
async def test_wholly_owned_entity_runs_profile_copywriting_after_xhs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import notifications

    pipeline = CompanyScanPipeline(_PipelineDb(), object())  # type: ignore[arg-type]
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

    pipeline = CompanyScanPipeline(_PipelineDb(), object())  # type: ignore[arg-type]

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

    pipeline = CompanyScanPipeline(_PipelineDb(), object())  # type: ignore[arg-type]

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


@pytest.mark.asyncio
async def test_wholly_owned_bidding_collection_is_serial_and_target_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = CompanyScanPipeline(_PipelineDb(), object())  # type: ignore[arg-type]
    active = 0
    peak = 0
    calls: list[tuple[str, str]] = []

    async def run_bidding(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        calls.append((kwargs["company_name"], kwargs["target_id"]))
        await asyncio.sleep(0.01)
        active -= 1
        return {
            "kind": "bidding",
            "status": "completed",
            "records_fetched": 3,
            "attachments_archived": 1,
            "visual_analysis": {"findings_count": 2},
        }

    monkeypatch.setattr(pipeline, "_run_bidding_collection", run_bidding)

    result = await pipeline._scan_wholly_owned_entities(
        task_id="task-1",
        project_id="project-1",
        entities=[
            {"name": "子公司A", "target_id": "target-a"},
            {"name": "子公司B", "target_id": "target-b"},
        ],
        enable_asset_discovery=False,
        enable_url_scan=True,
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
        xhs_search_concurrency=1,
        entity_concurrency=2,
        enable_bidding=True,
    )

    assert peak == 1
    assert set(calls) == {("子公司A", "target-a"), ("子公司B", "target-b")}
    assert result["summary"]["bidding_records"] == 6
    assert result["summary"]["bidding_findings"] == 4
    assert result["summary"]["bidding_attachments"] == 2
