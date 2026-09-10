"""Stage runtime and lifecycle owner for company scans."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

from core.logger import get_logger
from core.observability import obs_log
from api.services.company_scan.checkpoints import CompanyScanCheckpointRepository
from api.services.company_scan.contracts import CompanyScanContext, CompanyScanPlan
from api.services.company_scan.identity import CompanyIdentityStage
from api.services.company_scan.policies import requires_initial_core_lease
from api.services.company_scan.related_stages import (
    RelatedEntityPlanningStage,
    RelatedSourceRegistry,
    RelatedSourceRuntimeStage,
)
from api.services.company_scan.result_projection import build_initial_result
from api.services.company_scan.source_stages import (
    CompanySourceStageRegistry,
    RootSourceStage,
)
from api.services.company_scan.terminal_stages import (
    CompanyScanFailureHandler,
    CompanyScanFinalizerStage,
    MobileJoinStage,
    ProfileCopywritingRuntimeStage,
    cleanup_company_scan_runtime,
)


logger = get_logger("company_scan.runtime")


class CompanyWorkflowStage(Protocol):
    name: str

    async def run(self, ctx: CompanyScanContext) -> None:
        ...


class CompanyStageRegistry:
    """Ordered workflow registry; source-level concurrency stays in source stages."""

    def __init__(self) -> None:
        self._stages: dict[str, CompanyWorkflowStage] = {}

    def register(self, stage: CompanyWorkflowStage) -> "CompanyStageRegistry":
        if not stage.name or stage.name in self._stages:
            raise ValueError(f"公司扫描 Stage 重复或为空: {stage.name}")
        self._stages[stage.name] = stage
        return self

    @property
    def stages(self) -> tuple[CompanyWorkflowStage, ...]:
        return tuple(self._stages.values())

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._stages)

    @classmethod
    def default(
        cls,
        checkpoints: CompanyScanCheckpointRepository,
    ) -> "CompanyStageRegistry":
        return (
            cls()
            .register(CompanyIdentityStage())
            .register(
                RootSourceStage(
                    CompanySourceStageRegistry.default(),
                    checkpoints,
                )
            )
            .register(RelatedEntityPlanningStage())
            .register(
                RelatedSourceRuntimeStage(
                    RelatedSourceRegistry.default(),
                    checkpoints,
                )
            )
            .register(ProfileCopywritingRuntimeStage())
            .register(MobileJoinStage())
            .register(CompanyScanFinalizerStage())
        )


class CompanyWorkflowRuntime:
    """Execute registered stages with uniform timing and domain context."""

    def __init__(self, registry: CompanyStageRegistry) -> None:
        self.registry = registry

    async def run(self, ctx: CompanyScanContext) -> None:
        for stage in self.registry.stages:
            await self._run_stage(stage, ctx)

    async def _run_stage(
        self,
        stage: CompanyWorkflowStage,
        ctx: CompanyScanContext,
    ) -> None:
        started = time.monotonic()
        obs_log(
            f"公司扫描阶段开始: {stage.name}",
            task_id=ctx.plan.task_id,
            project_id=ctx.plan.project_id,
            source="company_scan_runtime",
            level="info",
            event="stage_start",
            data={
                "stage": stage.name,
                "target_id": ctx.target_id,
                "plan_version": ctx.plan.version,
            },
        )
        try:
            await stage.run(ctx)
        except asyncio.CancelledError:
            self._observe_stage_end(ctx, stage.name, started, "cancelled")
            raise
        except Exception:
            self._observe_stage_end(ctx, stage.name, started, "error")
            raise
        self._observe_stage_end(ctx, stage.name, started, "completed")

    @staticmethod
    def _observe_stage_end(
        ctx: CompanyScanContext,
        stage_name: str,
        started: float,
        status: str,
    ) -> None:
        obs_log(
            f"公司扫描阶段{status}: {stage_name}",
            task_id=ctx.plan.task_id,
            project_id=ctx.plan.project_id,
            source="company_scan_runtime",
            level="error" if status == "error" else "info",
            event="stage_end",
            data={
                "stage": stage_name,
                "status": status,
                "target_id": ctx.target_id,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            },
        )


class CompanyScanRuntime:
    """Own plan preparation, leases, stage execution, failure and cleanup."""

    def __init__(
        self,
        owner: Any,
        plan: CompanyScanPlan,
        *,
        checkpoints: CompanyScanCheckpointRepository | None = None,
        registry: CompanyStageRegistry | None = None,
        failure_handler: CompanyScanFailureHandler | None = None,
    ) -> None:
        self.owner = owner
        self.plan = plan
        self.checkpoints = checkpoints or CompanyScanCheckpointRepository()
        self.registry = registry or CompanyStageRegistry.default(self.checkpoints)
        self.workflow = CompanyWorkflowRuntime(self.registry)
        self.failure_handler = failure_handler or CompanyScanFailureHandler()

    async def run(self) -> dict[str, Any]:
        ctx = await self._prepare()
        self._observe_start(ctx)
        try:
            await self._prepare_core_lease(ctx)
            await self.workflow.run(ctx)
            return ctx.result
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.failure_handler.handle(ctx, error)
            raise AssertionError("failure handler must raise")
        finally:
            await cleanup_company_scan_runtime(ctx)

    async def _prepare(self) -> CompanyScanContext:
        self.plan.validate()
        from api.services.company_scan_runtime import get_company_scan_resource_pool
        from api.services.wechat_collection import normalize_wechat_app_instance
        from api.services.xhs_target_selection import parse_manual_targets

        manual_targets = parse_manual_targets(self.plan.xhs_manual_targets)
        app_instance = normalize_wechat_app_instance(
            self.plan.wechat_app_instance
        )
        ctx = CompanyScanContext(
            owner=self.owner,
            plan=self.plan,
            result=build_initial_result(
                self.plan,
                manual_xhs_targets=manual_targets,
                wechat_app_instance=app_instance,
            ),
        )
        await self.checkpoints.prepare(ctx)
        ctx.core_lease = get_company_scan_resource_pool(
            self.plan.company_core_concurrency
        ).lease(task_id=self.plan.task_id)
        return ctx

    async def _prepare_core_lease(self, ctx: CompanyScanContext) -> None:
        recovery = ctx.recovery
        if recovery.resume_core_completed:
            await self.owner._update_progress(
                self.plan.task_id,
                "restoring_core",
                "复用已完成的网站、API 与学者扫描结果...",
            )
            return
        if not requires_initial_core_lease(
            enabled_core_modules=self.plan.enabled_core_modules,
            target_id=self.plan.target_id,
            refresh_target_identity=self.plan.refresh_target_identity,
            enable_wechat=self.plan.enable_wechat,
            wechat_target_selection_mode=self.plan.wechat_target_selection_mode,
        ):
            return
        await self.owner._update_progress(
            self.plan.task_id,
            "waiting_core",
            "等待公司扫描网络与 AI 并发资源...",
        )
        await ctx.core_lease.acquire()

    def _observe_start(self, ctx: CompanyScanContext) -> None:
        obs_log(
            "综合公司扫描流水线开始",
            task_id=self.plan.task_id,
            project_id=self.plan.project_id,
            source="company_scan_pipeline",
            level="notice",
            event="pipeline_start",
            data={
                "company_name": self.plan.company_name,
                "plan_version": self.plan.version,
                "stages": self.registry.names,
            },
        )
