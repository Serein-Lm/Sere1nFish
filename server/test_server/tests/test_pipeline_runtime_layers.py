from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from api.services.company_scan.checkpoints import CompanyScanCheckpointRepository
from api.services.company_scan.contracts import CompanyScanContext, CompanyScanPlan
from api.services.company_scan.runtime import (
    CompanyStageRegistry,
    CompanyWorkflowRuntime,
)
from api.services.company_scan.source_stages import CompanySourceStageRegistry
from core.mobile.collect.contracts import (
    MobileCollectExecution,
    MobileCollectPlan,
    MobileSeedPlan,
)
from core.mobile.collect.runtime import MobileCollectRuntime, MobileRunRegistry
from core.mobile.collect.stage_registry import (
    MobileStageDefinition,
    MobileStageRegistry,
)
from core.stream import Item, Stage


def _company_plan(**overrides: Any) -> CompanyScanPlan:
    values = {
        "task_id": "task-1",
        "project_id": "project-1",
        "company_name": "测试单位",
    }
    values.update(overrides)
    return CompanyScanPlan.from_call(**values)


def test_company_scan_plan_normalizes_sequences_and_validates_version() -> None:
    plan = _company_plan(
        urls=["https://example.com"],
        website_root_domains=["example.com"],
        website_required_path_segments=["notice"],
        enable_bidding=True,
        enable_url_scan=False,
    )

    assert plan.urls == ("https://example.com",)
    assert plan.website_root_domains == ("example.com",)
    assert plan.website_required_path_segments == ("notice",)
    assert plan.bidding_visual_analysis_enabled is False
    plan.validate()

    with pytest.raises(ValueError, match="计划版本"):
        _company_plan(version=999).validate()


def test_company_scan_registries_have_stable_order_and_reject_duplicates() -> None:
    checkpoints = CompanyScanCheckpointRepository()
    workflow = CompanyStageRegistry.default(checkpoints)
    sources = CompanySourceStageRegistry.default()

    assert workflow.names == (
        "identity",
        "root_sources",
        "related_entity_plan",
        "related_sources",
        "profile_copywriting",
        "mobile_join",
        "finalize",
    )
    assert sources.names == (
        "control_structure",
        "asset_url",
        "xhs",
        "bidding",
        "wechat",
        "scholar",
    )

    with pytest.raises(ValueError, match="重复"):
        workflow.register(workflow.stages[0])


@pytest.mark.asyncio
async def test_company_workflow_runtime_is_ordered_and_fail_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services.company_scan import runtime as runtime_module

    events: list[str] = []

    class RecordingStage:
        def __init__(self, name: str, *, fail: bool = False) -> None:
            self.name = name
            self.fail = fail

        async def run(self, _ctx: CompanyScanContext) -> None:
            events.append(self.name)
            if self.fail:
                raise RuntimeError("stage failed")

    registry = (
        CompanyStageRegistry()
        .register(RecordingStage("first"))
        .register(RecordingStage("second", fail=True))
        .register(RecordingStage("never"))
    )
    context = CompanyScanContext(
        owner=SimpleNamespace(db=object(), app_config=object()),
        plan=_company_plan(),
        result={},
    )
    observations: list[dict[str, Any]] = []
    monkeypatch.setattr(
        runtime_module,
        "obs_log",
        lambda *_args, **kwargs: observations.append(kwargs),
    )

    with pytest.raises(RuntimeError, match="stage failed"):
        await CompanyWorkflowRuntime(registry).run(context)

    assert events == ["first", "second"]
    assert [item["event"] for item in observations] == [
        "stage_start",
        "stage_end",
        "stage_start",
        "stage_end",
    ]
    assert observations[-1]["data"]["status"] == "error"


class _CollectStage(Stage):
    name = "collect"

    async def handle(self, item: Item, ctx: Any) -> None:
        await ctx.emit("persist", item)


class _PersistStage(Stage):
    name = "persist"

    async def handle(self, item: Item, ctx: Any) -> None:
        await ctx.emit("notify", item)


class _NotifyStage(Stage):
    name = "notify"

    async def handle(self, item: Item, ctx: Any) -> None:
        ctx.state.setdefault("seen", []).append(item.payload)


@pytest.mark.asyncio
async def test_mobile_stage_registry_builds_declared_stream_graph() -> None:
    registry = MobileStageRegistry.default(
        collect=_CollectStage,
        persist=_PersistStage,
        notify=_NotifyStage,
    )
    state: dict[str, Any] = {"seen": []}

    metrics = await registry.build(
        state=state,
        pipeline_id="runtime-test",
        entry="collect",
    ).run(seeds=[Item(payload="record")], entry="collect")

    assert registry.names == ("collect", "persist", "notify")
    assert state["seen"] == ["record"]
    assert metrics["notify"].succeeded == 1


def test_mobile_stage_registry_rejects_invalid_graphs() -> None:
    duplicate = MobileStageRegistry().register(
        MobileStageDefinition("collect", _CollectStage)
    )
    with pytest.raises(ValueError, match="重复"):
        duplicate.register(MobileStageDefinition("collect", _CollectStage))

    missing = MobileStageRegistry().register(
        MobileStageDefinition("collect", _CollectStage, ("missing",))
    )
    with pytest.raises(ValueError, match="下游未注册"):
        missing.build(state={}, pipeline_id="missing", entry="collect")


def test_mobile_run_registry_owns_idempotent_stop_signal() -> None:
    registry = MobileRunRegistry()
    event = asyncio.Event()

    registry.register("run-1", event)
    assert registry.is_running("run-1") is True
    assert registry.request_stop("run-1") is True
    assert event.is_set() is True

    registry.unregister("run-1", asyncio.Event())
    assert registry.is_running("run-1") is True
    registry.unregister("run-1", event)
    assert registry.is_running("run-1") is False
    assert registry.request_stop("run-1") is False


@pytest.mark.asyncio
async def test_mobile_runtime_releases_run_marker_when_observer_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = MobileCollectPlan(
        db=object(),  # type: ignore[arg-type]
        run_task_id="run-observer-failure",
        project_id="project-1",
        task_def={"task_def_id": "definition-1", "device_id": "device-1"},
    )
    registry = MobileStageRegistry.default(
        collect=_CollectStage,
        persist=_PersistStage,
        notify=_NotifyStage,
    )
    runs = MobileRunRegistry()

    def fail_observation(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("observer failed")

    runtime = MobileCollectRuntime(
        plan,
        stages=registry,
        observer=fail_observation,
        run_registry=runs,
    )
    execution = MobileCollectExecution(
        plan=plan,
        seeds=MobileSeedPlan(
            target=None,
            keyword_resolution={"keywords": []},
            definition_fingerprint="fingerprint",
        ),
        state={"stop_event": asyncio.Event()},
    )

    async def prepared() -> MobileCollectExecution:
        return execution

    monkeypatch.setattr(runtime, "_prepare", prepared)

    with pytest.raises(RuntimeError, match="observer failed"):
        await runtime.run()

    assert runs.is_running(plan.run_task_id) is False


def test_mobile_collect_plan_validates_deep_collection_contract() -> None:
    plan = MobileCollectPlan(
        db=object(),  # type: ignore[arg-type]
        run_task_id="run-1",
        project_id="project-1",
        task_def={
            "task_def_id": "definition-1",
            "device_id": "device-1",
            "deep_collect": True,
            "source_link_strategy": "wechat",
            "extract_fields": [],
        },
    )

    with pytest.raises(ValueError, match="extract_fields"):
        plan.validate()
