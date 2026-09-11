"""Registered root-source stages for company scans."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from core.logger import get_logger
from api.services.company_scan.checkpoints import CompanyScanCheckpointRepository
from api.services.company_scan.contracts import CompanyScanContext


logger = get_logger("company_scan.sources")


class CompanySourceStage(ABC):
    name: str = ""
    resource_group: str = "core"
    restores_from_storage: bool = False

    @abstractmethod
    def enabled(self, ctx: CompanyScanContext) -> bool:
        ...

    @abstractmethod
    async def run(self, ctx: CompanyScanContext) -> dict[str, Any]:
        ...

    async def checkpoint_or_none(
        self,
        ctx: CompanyScanContext,
    ) -> dict[str, Any] | None:
        result = ctx.recovery.checkpoint_results.get(self.name)
        return dict(result) if isinstance(result, dict) else None

    def requires_core_lease(self, ctx: CompanyScanContext) -> bool:
        """Return whether this run performs external work instead of restoration."""
        return bool(
            self.resource_group != "mobile"
            and self.name not in ctx.recovery.checkpoint_results
            and not (
                self.restores_from_storage
                and ctx.recovery.resume_core_completed
            )
        )


class ControlStructureStage(CompanySourceStage):
    name = "control_structure"
    restores_from_storage = True

    def enabled(self, ctx: CompanyScanContext) -> bool:
        return ctx.plan.enable_control_structure

    async def run(self, ctx: CompanyScanContext) -> dict[str, Any]:
        checkpoint = await self.checkpoint_or_none(ctx)
        if checkpoint is not None:
            return checkpoint
        if ctx.recovery.resume_core_completed:
            from api.services.company_scan_recovery import restore_control_structure

            ctx.recovery.restored_primary_modules.add(self.name)
            return await restore_control_structure(
                ctx.db,
                project_id=ctx.plan.project_id,
                parent_target_id=ctx.target_id,
                max_depth=ctx.plan.control_max_depth,
                min_ownership_percent=ctx.plan.control_min_ownership_percent,
            )
        return await ctx.owner._run_wholly_owned_investments(
            task_id=ctx.plan.task_id,
            project_id=ctx.plan.project_id,
            parent_target=ctx.target,
            company_name=ctx.normalized_name,
            min_ownership_percent=ctx.plan.control_min_ownership_percent,
            max_depth=ctx.plan.control_max_depth,
            max_entities=ctx.plan.control_max_entities,
            page_concurrency=ctx.plan.control_lookup_concurrency,
            icp_concurrency=ctx.plan.control_icp_concurrency,
        )


class AssetUrlStage(CompanySourceStage):
    name = "asset_url"
    restores_from_storage = True

    def enabled(self, ctx: CompanyScanContext) -> bool:
        return ctx.plan.enable_asset_discovery or ctx.plan.enable_url_scan

    async def run(self, ctx: CompanyScanContext) -> dict[str, Any]:
        checkpoint = await self.checkpoint_or_none(ctx)
        if checkpoint is not None:
            return checkpoint
        plan = ctx.plan
        if ctx.recovery.resume_core_completed:
            from api.services.company_scan_recovery import restore_asset_url

            ctx.recovery.restored_primary_modules.add(self.name)
            return await restore_asset_url(
                ctx.db,
                task_id=plan.task_id,
                project_id=plan.project_id,
                target_id=ctx.target_id,
                incremental_scan=plan.incremental_scan,
                enable_asset_discovery=plan.enable_asset_discovery,
                enable_url_scan=plan.enable_url_scan,
            )
        return await ctx.owner._run_asset_and_url_scan(
            task_id=plan.task_id,
            project_id=plan.project_id,
            identity=ctx.result["identity"],
            url_text=plan.url_text,
            urls=list(plan.urls),
            enable_asset_discovery=plan.enable_asset_discovery,
            enable_url_scan=plan.enable_url_scan,
            enable_copywriting=plan.enable_copywriting,
            min_attention_score=plan.min_attention_score,
            fofa_size=plan.fofa_size,
            hunter_size=plan.hunter_size,
            probe_concurrency=plan.asset_probe_concurrency,
            incremental_scan=plan.incremental_scan,
            url_probe_concurrency=plan.url_probe_concurrency,
            url_scan_concurrency=plan.url_scan_concurrency,
            copywriting_concurrency=plan.copywriting_concurrency,
            website_collection_mode=plan.website_collection_mode,
            website_root_domains=(
                list(plan.website_root_domains)
                or ctx.result["identity"].get("official_root_domains")
            ),
            website_required_path_segments=list(
                plan.website_required_path_segments
            ),
        )


class XhsSourceStage(CompanySourceStage):
    name = "xhs"

    def enabled(self, ctx: CompanyScanContext) -> bool:
        return bool(
            self.name in ctx.recovery.checkpoint_results
            or ctx.root_xhs_enabled
        )

    async def run(self, ctx: CompanyScanContext) -> dict[str, Any]:
        checkpoint = await self.checkpoint_or_none(ctx)
        if checkpoint is not None:
            return checkpoint
        keywords = ctx.owner._get_xhs_keywords(ctx.aliases, ctx.router_output)
        ctx.result["xhs"]["keywords_used"] = keywords
        return await ctx.owner._run_xhs_search(
            ctx.plan.task_id,
            ctx.plan.project_id,
            keywords,
            ctx.plan.xhs_max_notes,
            ctx.plan.xhs_attention_threshold,
            target_id=ctx.target_id,
            target_name=ctx.normalized_name,
            search_concurrency=ctx.plan.xhs_search_concurrency,
        )


class BiddingSourceStage(CompanySourceStage):
    name = "bidding"
    restores_from_storage = True

    def enabled(self, ctx: CompanyScanContext) -> bool:
        return ctx.plan.enable_bidding

    async def run(self, ctx: CompanyScanContext) -> dict[str, Any]:
        checkpoint = await self.checkpoint_or_none(ctx)
        if checkpoint is not None:
            return checkpoint
        plan = ctx.plan
        ctx.result["bidding"]["query_name"] = ctx.normalized_name
        if ctx.recovery.resume_core_completed:
            from api.services.company_scan_recovery import restore_bidding

            ctx.recovery.restored_primary_modules.add(self.name)
            return await restore_bidding(
                ctx.db,
                task_id=plan.task_id,
                company_name=ctx.normalized_name,
            )
        return await ctx.owner._run_bidding_collection(
            task_id=plan.task_id,
            project_id=plan.project_id,
            company_name=ctx.normalized_name,
            target_id=ctx.target_id,
            page_size=plan.bidding_page_size,
            max_records=plan.bidding_max_records,
            lookback_days=plan.bidding_lookback_days,
            enable_visual_analysis=plan.bidding_visual_analysis_enabled,
            enable_copywriting=plan.enable_copywriting,
            min_attention_score=plan.min_attention_score,
            scan_concurrency=plan.url_scan_concurrency,
            copywriting_concurrency=plan.copywriting_concurrency,
        )


class WechatSourceStage(CompanySourceStage):
    name = "wechat"
    resource_group = "mobile"

    def enabled(self, ctx: CompanyScanContext) -> bool:
        return bool(
            ctx.plan.enable_wechat
            and (
                self.name in ctx.recovery.checkpoint_results
                or ctx.recovery.resume_mobile_completed
                or ctx.root_wechat_enabled
            )
        )

    async def run(self, ctx: CompanyScanContext) -> dict[str, Any]:
        checkpoint = await self.checkpoint_or_none(ctx)
        if checkpoint is not None:
            return checkpoint
        if ctx.recovery.resume_mobile_completed:
            from api.services.company_scan_recovery import restore_wechat

            ctx.recovery.restored_primary_modules.add(self.name)
            return await restore_wechat(ctx.db, task_id=ctx.plan.task_id)
        return await ctx.owner._run_wechat_collection(
            task_id=ctx.plan.task_id,
            project_id=ctx.plan.project_id,
            target_id=ctx.target_id,
            target_name=ctx.normalized_name,
            device_id=ctx.plan.wechat_device_id,
            app_instance=str(ctx.result["wechat"].get("app_instance") or "primary"),
            collection_priority=str(ctx.result["wechat"].get("priority") or "normal"),
            requested_by=ctx.plan.requested_by,
            started_event=ctx.mobile_started,
        )


class ScholarSourceStage(CompanySourceStage):
    name = "scholar"
    restores_from_storage = True

    def enabled(self, ctx: CompanyScanContext) -> bool:
        return ctx.plan.enable_scholar

    async def run(self, ctx: CompanyScanContext) -> dict[str, Any]:
        checkpoint = await self.checkpoint_or_none(ctx)
        if checkpoint is not None:
            return checkpoint
        if ctx.recovery.resume_core_completed:
            from api.services.company_scan_recovery import restore_scholar

            ctx.recovery.restored_primary_modules.add(self.name)
            return await restore_scholar(
                ctx.db,
                task_id=ctx.plan.task_id,
                unit=ctx.normalized_name,
            )
        resolution = ctx.scholar_resolution
        return await ctx.owner._run_scholar_collection(
            task_id=ctx.plan.task_id,
            project_id=ctx.plan.project_id,
            target_id=ctx.target_id,
            unit=ctx.normalized_name,
            direction=resolution.direction,
            direction_source=resolution.source,
            unit_en=ctx.resolved_scholar_unit_en,
            limit=ctx.plan.scholar_limit,
        )


class CompanySourceStageRegistry:
    def __init__(self) -> None:
        self._stages: dict[str, CompanySourceStage] = {}

    def register(self, stage: CompanySourceStage) -> "CompanySourceStageRegistry":
        if not stage.name or stage.name in self._stages:
            raise ValueError(f"公司来源 Stage 重复或为空: {stage.name}")
        self._stages[stage.name] = stage
        return self

    def active(self, ctx: CompanyScanContext) -> list[CompanySourceStage]:
        return [stage for stage in self._stages.values() if stage.enabled(ctx)]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._stages)

    @classmethod
    def default(cls) -> "CompanySourceStageRegistry":
        registry = cls()
        for stage in (
            ControlStructureStage(),
            AssetUrlStage(),
            XhsSourceStage(),
            BiddingSourceStage(),
            WechatSourceStage(),
            ScholarSourceStage(),
        ):
            registry.register(stage)
        return registry


class RootSourceStage:
    """Run registered sources, detaching mobile work from the core lease."""

    name = "root_sources"

    def __init__(
        self,
        registry: CompanySourceStageRegistry,
        checkpoints: CompanyScanCheckpointRepository,
    ) -> None:
        self.registry = registry
        self.checkpoints = checkpoints

    async def run(self, ctx: CompanyScanContext) -> None:
        stages = self.registry.active(ctx)
        if not stages:
            try:
                await self._mark_phases(ctx, mobile=True)
            finally:
                self._release_core_lease(ctx)
            return
        ctx.primary_job_count = len(stages)
        await ctx.owner._update_progress(
            ctx.plan.task_id,
            "scanning",
            f"并行执行 {len(stages)} 条根 Target 数据源流水线...",
        )
        mobile_stages = [
            stage for stage in stages if stage.resource_group == "mobile"
        ]
        core_stages = [
            stage for stage in stages if stage.resource_group != "mobile"
        ]
        mobile_jobs = self._job_factories(ctx, mobile_stages)
        core_jobs = self._job_factories(ctx, core_stages)
        if mobile_jobs:
            self._start_mobile(ctx, mobile_jobs)
        try:
            if any(stage.requires_core_lease(ctx) for stage in core_stages):
                await self._ensure_core_lease(ctx)
            materialized_core_jobs = self._materialize_jobs(core_jobs)
            checkpoint_errors: set[str] = set()
            outcomes = await ctx.owner._gather_named_jobs(
                materialized_core_jobs,
                on_completed=lambda kind, outcome: self.checkpoints.record(
                    ctx, kind, outcome
                ),
                on_checkpoint_error=lambda kind, _error: checkpoint_errors.add(
                    kind
                ),
            )
            if not checkpoint_errors and ctx.owner._jobs_completed_successfully(
                materialized_core_jobs,
                outcomes,
            ):
                await self._mark_phases(ctx, mobile=not mobile_jobs)
            failures, xhs_succeeded = ctx.owner._merge_primary_job_results(
                ctx.result,
                materialized_core_jobs,
                outcomes,
            )
            ctx.failed_primary_jobs.update(failures)
            ctx.xhs_succeeded = ctx.xhs_succeeded or xhs_succeeded
        finally:
            self._release_core_lease(ctx)

    def _start_mobile(
        self,
        ctx: CompanyScanContext,
        jobs: list[tuple[str, Callable[[], Awaitable[dict[str, Any]]]]],
    ) -> None:
        from core.background import spawn_background

        operation = self._run_mobile_jobs(ctx, jobs)
        try:
            ctx.mobile_task = spawn_background(
                operation,
                name=f"company-wechat:{ctx.plan.task_id}",
            )
        except Exception:
            operation.close()
            raise

    async def _run_mobile_jobs(
        self,
        ctx: CompanyScanContext,
        jobs: list[tuple[str, Callable[[], Awaitable[dict[str, Any]]]]],
    ) -> list[Any]:
        ctx.mobile_jobs = self._materialize_jobs(jobs)
        return await ctx.owner._run_mobile_jobs(
            ctx.mobile_jobs,
            task_id=ctx.plan.task_id,
            on_completed=lambda kind, outcome: self.checkpoints.record(
                ctx, kind, outcome
            ),
        )

    @staticmethod
    def _job_factories(
        ctx: CompanyScanContext,
        stages: list[CompanySourceStage],
    ) -> list[tuple[str, Callable[[], Awaitable[dict[str, Any]]]]]:
        return [
            (stage.name, lambda stage=stage: stage.run(ctx))
            for stage in stages
        ]

    @staticmethod
    def _materialize_jobs(
        jobs: list[tuple[str, Callable[[], Awaitable[dict[str, Any]]]]],
    ) -> list[tuple[str, Awaitable[dict[str, Any]]]]:
        return [(name, factory()) for name, factory in jobs]

    @staticmethod
    async def _ensure_core_lease(ctx: CompanyScanContext) -> None:
        if ctx.core_lease is not None:
            await ctx.core_lease.acquire()

    @staticmethod
    def _release_core_lease(ctx: CompanyScanContext) -> None:
        if ctx.core_lease is not None:
            ctx.core_lease.release()

    @staticmethod
    async def _mark_phases(ctx: CompanyScanContext, *, mobile: bool) -> None:
        from api.services.task_progress import mark_resume_phases

        phases = ["core_completed"]
        if mobile:
            phases.append("mobile_completed")
        await mark_resume_phases(ctx.db, task_id=ctx.plan.task_id, phases=phases)
