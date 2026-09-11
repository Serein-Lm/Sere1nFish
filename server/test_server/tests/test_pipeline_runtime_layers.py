from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from api.services.company_scan.checkpoints import CompanyScanCheckpointRepository
from api.services.company_scan.contracts import (
    CompanyScanContext,
    CompanyScanPlan,
    CompanyScanRecovery,
)
from api.services.company_scan.identity import CompanyIdentityStage
from api.services.company_scan.related_stages import (
    RelatedSourceRegistry,
    RelatedSourceRuntimeStage,
)
from api.services.company_scan.result_projection import build_initial_result
from api.services.company_scan.runtime import (
    CompanyScanRuntime,
    CompanyStageRegistry,
    CompanyWorkflowRuntime,
)
from api.services.company_scan.source_stages import (
    CompanySourceStage,
    CompanySourceStageRegistry,
    RootSourceStage,
    XhsSourceStage,
)
from api.services.company_scan.terminal_stages import ProfileCopywritingRuntimeStage
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


class _RecordingLease:
    def __init__(self, *, fail_on_acquire: bool = False) -> None:
        self.fail_on_acquire = fail_on_acquire
        self.acquire_calls = 0
        self.release_calls = 0
        self.acquired = False

    async def acquire(self) -> None:
        self.acquire_calls += 1
        if self.fail_on_acquire:
            raise RuntimeError("lease unavailable")
        self.acquired = True

    def release(self) -> None:
        if not self.acquired:
            return
        self.acquired = False
        self.release_calls += 1


class _RecordingCheckpoints:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []

    async def record(
        self,
        _ctx: CompanyScanContext,
        kind: str,
        outcome: dict[str, Any],
    ) -> None:
        self.records.append((kind, dict(outcome)))


class _RuntimeOwner:
    def __init__(self, lease: _RecordingLease) -> None:
        self.db = object()
        self.app_config = object()
        self.lease = lease
        self.progress: list[str] = []

    async def _update_progress(
        self,
        _task_id: str,
        stage: str,
        _message: str,
    ) -> None:
        self.progress.append(stage)

    async def _gather_named_jobs(
        self,
        jobs: list[tuple[str, Any]],
        *,
        on_completed: Any,
        on_checkpoint_error: Any = None,
    ) -> list[Any]:
        outcomes: list[Any] = []
        for kind, operation in jobs:
            try:
                outcome = await operation
            except BaseException as error:
                outcome = error
            else:
                try:
                    await on_completed(kind, outcome)
                except Exception as error:
                    if on_checkpoint_error is not None:
                        on_checkpoint_error(kind, error)
            outcomes.append(outcome)
        return outcomes

    @staticmethod
    def _jobs_completed_successfully(
        jobs: list[tuple[str, Any]],
        outcomes: list[Any],
    ) -> bool:
        return len(jobs) == len(outcomes) and all(
            not isinstance(outcome, BaseException) for outcome in outcomes
        )

    @staticmethod
    def _merge_primary_job_results(
        _result: dict[str, Any],
        _jobs: list[tuple[str, Any]],
        _outcomes: list[Any],
    ) -> tuple[set[str], bool]:
        return set(), False

    @staticmethod
    def _dedupe_text(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))


def _runtime_context(
    owner: Any,
    *,
    recovery: CompanyScanRecovery | None = None,
    **plan_overrides: Any,
) -> CompanyScanContext:
    plan = _company_plan(**plan_overrides)
    return CompanyScanContext(
        owner=owner,
        plan=plan,
        result=build_initial_result(
            plan,
            manual_xhs_targets=[],
            wechat_app_instance="primary",
        ),
        recovery=recovery or CompanyScanRecovery(),
        core_lease=owner.lease,
    )


@pytest.mark.asyncio
async def test_xhs_checkpoint_wins_without_global_core_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import xhs_target_selection

    class Selector:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def select(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("检查点存在时不应重新运行 XHS 目标选择")

    monkeypatch.setattr(xhs_target_selection, "XhsTargetSelectionService", Selector)
    owner = _RuntimeOwner(_RecordingLease())
    ctx = _runtime_context(
        owner,
        recovery=CompanyScanRecovery(
            checkpoint_results={"xhs": {"kind": "xhs", "status": "completed"}},
        ),
        enable_xhs=True,
    )

    await CompanyIdentityStage()._select_xhs(ctx)

    assert ctx.root_xhs_enabled is True
    assert ctx.result["xhs"]["selection"]["status"] == "restored"
    assert XhsSourceStage().enabled(ctx) is True


@pytest.mark.asyncio
async def test_root_source_restoration_does_not_reacquire_core_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = 0

    class RestoreStage(CompanySourceStage):
        name = "restore_only"
        restores_from_storage = True

        def enabled(self, _ctx: CompanyScanContext) -> bool:
            return True

        async def run(self, _ctx: CompanyScanContext) -> dict[str, Any]:
            nonlocal executed
            executed += 1
            return {"kind": self.name, "status": "completed"}

    async def mark_phases(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(RootSourceStage, "_mark_phases", mark_phases)
    lease = _RecordingLease()
    owner = _RuntimeOwner(lease)
    ctx = _runtime_context(
        owner,
        recovery=CompanyScanRecovery(
            restore_core_context=True,
            resume_core_completed=True,
        ),
    )
    registry = CompanySourceStageRegistry().register(RestoreStage())

    await RootSourceStage(registry, _RecordingCheckpoints()).run(ctx)

    assert executed == 1
    assert lease.acquire_calls == 0


@pytest.mark.asyncio
async def test_checkpoint_only_runtime_skips_initial_core_lease() -> None:
    lease = _RecordingLease(fail_on_acquire=True)
    owner = _RuntimeOwner(lease)
    ctx = _runtime_context(
        owner,
        recovery=CompanyScanRecovery(
            checkpoint_results={
                "asset_url": {"kind": "asset_url", "status": "completed"},
                "xhs": {"kind": "xhs", "status": "completed"},
            },
        ),
        target_id="target-1",
        enable_scholar=False,
        enable_xhs=True,
    )

    await CompanyScanRuntime(owner, ctx.plan)._prepare_core_lease(ctx)

    assert lease.acquire_calls == 0


@pytest.mark.asyncio
async def test_root_source_creates_coroutines_only_after_core_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = 0

    class ExternalStage(CompanySourceStage):
        name = "external"

        def enabled(self, _ctx: CompanyScanContext) -> bool:
            return True

        async def run(self, _ctx: CompanyScanContext) -> dict[str, Any]:
            nonlocal executed
            executed += 1
            return {"kind": self.name, "status": "completed"}

    async def mark_phases(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(RootSourceStage, "_mark_phases", mark_phases)
    lease = _RecordingLease(fail_on_acquire=True)
    owner = _RuntimeOwner(lease)
    ctx = _runtime_context(owner)
    registry = CompanySourceStageRegistry().register(ExternalStage())

    with pytest.raises(RuntimeError, match="lease unavailable"):
        await RootSourceStage(registry, _RecordingCheckpoints()).run(ctx)

    assert executed == 0


@pytest.mark.asyncio
async def test_root_source_does_not_mark_phase_after_checkpoint_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marks = 0

    class ExternalStage(CompanySourceStage):
        name = "external"

        def enabled(self, _ctx: CompanyScanContext) -> bool:
            return True

        async def run(self, _ctx: CompanyScanContext) -> dict[str, Any]:
            return {"kind": self.name, "status": "completed"}

    class FailingCheckpoints(_RecordingCheckpoints):
        async def record(
            self,
            _ctx: CompanyScanContext,
            _kind: str,
            _outcome: dict[str, Any],
        ) -> None:
            raise RuntimeError("checkpoint unavailable")

    async def mark_phases(*_args: Any, **_kwargs: Any) -> None:
        nonlocal marks
        marks += 1

    monkeypatch.setattr(RootSourceStage, "_mark_phases", mark_phases)
    lease = _RecordingLease()
    owner = _RuntimeOwner(lease)
    ctx = _runtime_context(owner)
    registry = CompanySourceStageRegistry().register(ExternalStage())

    await RootSourceStage(registry, FailingCheckpoints()).run(ctx)

    assert marks == 0
    assert lease.release_calls == 1


@pytest.mark.asyncio
async def test_related_xhs_selection_runs_inside_core_lease() -> None:
    lease = _RecordingLease()
    owner = _RuntimeOwner(lease)
    checkpoints = _RecordingCheckpoints()
    ctx = _runtime_context(
        owner,
        enable_url_scan=False,
        enable_asset_discovery=False,
        enable_scholar=False,
        enable_xhs=True,
        enable_subsidiary_xhs=True,
    )
    ctx.target_id = "target-root"
    ctx.normalized_name = "根单位"
    entity = {
        "target_id": "target-child",
        "name": "子单位",
        "aliases": ["子单位简称"],
        "scan_channels": ["xhs"],
    }
    ctx.wholly_owned_entities = [entity]
    ctx.subsidiary_scope = {"selected": [entity], "skipped": []}

    class Decision:
        target_id = "target-child"

        @staticmethod
        def model_dump(*, mode: str) -> dict[str, Any]:
            assert mode == "json"
            return {"target_id": "target-child", "should_collect_xhs": True}

    class Selection:
        decisions = [Decision()]

        @staticmethod
        def model_dump(*, mode: str) -> dict[str, Any]:
            assert mode == "json"
            return {"status": "completed", "decisions": [Decision.model_dump(mode=mode)]}

    class Selector:
        async def select(self, *_args: Any, **_kwargs: Any) -> Selection:
            assert lease.acquired is True
            return Selection()

    async def scan_related(**_kwargs: Any) -> dict[str, Any]:
        assert lease.acquired is True
        return {
            "status": "completed",
            "entities": [],
            "summary": {"profile_copywritings": 0},
            "errors": [],
        }

    ctx.xhs_selector = Selector()
    owner._scan_wholly_owned_entities = scan_related

    await RelatedSourceRuntimeStage(
        RelatedSourceRegistry.default(),
        checkpoints,
    ).run(ctx)

    assert lease.acquire_calls == 1
    assert lease.release_calls == 1
    assert ctx.child_xhs_decisions["target-child"]["should_collect_xhs"] is True
    assert checkpoints.records[0][0] == "related_xhs_selection"


@pytest.mark.asyncio
async def test_related_checkpoint_restoration_does_not_acquire_core_lease() -> None:
    outcome = {
        "kind": "wholly_owned_entities",
        "status": "completed",
        "entities": [],
        "summary": {"profile_copywritings": 0},
        "errors": [],
    }
    lease = _RecordingLease(fail_on_acquire=True)
    owner = _RuntimeOwner(lease)
    ctx = _runtime_context(
        owner,
        recovery=CompanyScanRecovery(
            checkpoint_results={"wholly_owned_entities": outcome},
        ),
        enable_url_scan=False,
        enable_asset_discovery=False,
        enable_scholar=False,
    )
    ctx.subsidiary_scope = {"selected": [], "skipped": []}

    await RelatedSourceRuntimeStage(
        RelatedSourceRegistry.default(),
        _RecordingCheckpoints(),
    ).run(ctx)

    assert lease.acquire_calls == 0


@pytest.mark.asyncio
async def test_profile_copywriting_recovers_after_core_phase_completion() -> None:
    lease = _RecordingLease()
    owner = _RuntimeOwner(lease)
    checkpoints = _RecordingCheckpoints()
    ctx = _runtime_context(
        owner,
        recovery=CompanyScanRecovery(restore_core_context=True),
        enable_xhs=True,
        enable_copywriting=True,
    )
    ctx.root_xhs_enabled = True
    ctx.xhs_succeeded = True
    ctx.target_id = "target-root"
    ctx.normalized_name = "根单位"
    ctx.router_output = object()

    async def generate(*_args: Any, **_kwargs: Any) -> int:
        assert lease.acquired is True
        return 2

    owner._run_profile_copywriting = generate

    await ProfileCopywritingRuntimeStage(checkpoints).run(ctx)

    assert ctx.result["profile_copywritings"]["count"] == 2
    assert checkpoints.records == [
        (
            "profile_copywriting",
            {"kind": "profile_copywriting", "status": "completed", "count": 2},
        )
    ]
    assert lease.release_calls == 1


@pytest.mark.asyncio
async def test_profile_copywriting_checkpoint_restores_count_without_lease() -> None:
    checkpoint = {
        "kind": "profile_copywriting",
        "status": "completed",
        "count": 3,
    }
    lease = _RecordingLease(fail_on_acquire=True)
    owner = _RuntimeOwner(lease)
    ctx = _runtime_context(
        owner,
        recovery=CompanyScanRecovery(
            checkpoint_results={"profile_copywriting": checkpoint},
        ),
        enable_xhs=True,
    )
    ctx.result["profile_copywritings"]["count"] = 4

    await ProfileCopywritingRuntimeStage(_RecordingCheckpoints()).run(ctx)

    assert ctx.result["profile_copywritings"]["count"] == 7
    assert lease.acquire_calls == 0
