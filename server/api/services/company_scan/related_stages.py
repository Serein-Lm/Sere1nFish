"""Planning and registered stages for related company entities."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from api.services.company_scan.checkpoints import CompanyScanCheckpointRepository
from api.services.company_scan.contracts import CompanyScanContext


class RelatedEntityPlanningStage:
    name = "related_entity_plan"

    async def run(self, ctx: CompanyScanContext) -> None:
        from api.services.target_scan_profile import (
            load_project_descendant_scan_entities,
            select_subsidiary_scan_scope,
        )

        discovered = list(ctx.result["control_structure"].get("entities") or [])
        stored = await load_project_descendant_scan_entities(
            ctx.db,
            project_id=ctx.plan.project_id,
            root_target_id=ctx.target_id,
            max_depth=ctx.plan.control_max_depth,
        )
        ctx.wholly_owned_entities = self._merge_entities(discovered, stored)
        ctx.result["control_structure"].update(
            entities=ctx.wholly_owned_entities,
            stored_entities_loaded=len(stored),
        )
        channels = self._requested_channels(ctx)
        scope = {
            "selected": [],
            "skipped": [],
            "requested_channels": channels,
            "selected_count": 0,
            "skipped_count": len(ctx.wholly_owned_entities),
        }
        if ctx.wholly_owned_entities and channels:
            scope = await select_subsidiary_scan_scope(
                ctx.db,
                project_id=ctx.plan.project_id,
                entities=ctx.wholly_owned_entities,
                channels=channels,
                max_entities=ctx.plan.subsidiary_scan_limit,
                skip_completed=ctx.plan.skip_completed_subsidiaries,
            )
        ctx.subsidiary_scope = scope
        ctx.result["control_structure"]["scan_policy"].update(
            selected_count=int(scope.get("selected_count") or 0),
            skipped_count=int(scope.get("skipped_count") or 0),
            requested_channels=list(scope.get("requested_channels") or []),
        )

    @staticmethod
    def _merge_entities(
        discovered: list[dict[str, Any]],
        stored: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_target = {
            str(entity.get("target_id") or ""): entity
            for entity in stored
            if str(entity.get("target_id") or "")
        }
        for entity in discovered:
            target_id = str(entity.get("target_id") or "")
            if not target_id:
                continue
            prior = by_target.get(target_id, {})
            by_target[target_id] = {
                **prior,
                **entity,
                "aliases": list(entity.get("aliases") or prior.get("aliases") or []),
                "scan_profile": dict(
                    entity.get("scan_profile") or prior.get("scan_profile") or {}
                ),
            }
        return sorted(
            by_target.values(),
            key=lambda item: (
                int(item.get("relation_depth") or 1),
                str(item.get("name") or "").casefold(),
            ),
        )

    @staticmethod
    def _requested_channels(ctx: CompanyScanContext) -> list[str]:
        plan = ctx.plan
        return [
            *(["website"] if plan.enable_asset_discovery or plan.enable_url_scan else []),
            *(["xhs"] if plan.subsidiary_xhs_enabled else []),
            *(["bidding"] if plan.subsidiary_bidding_enabled else []),
            *(["scholar"] if plan.enable_scholar else []),
        ]


class RelatedXhsSelectionStage:
    """Select child XHS targets inside the core-resource boundary."""

    name = "related_xhs_selection"

    def enabled(self, ctx: CompanyScanContext) -> bool:
        checkpoints = ctx.recovery.checkpoint_results
        if "wholly_owned_entities" in checkpoints:
            return False
        if self._checkpoint_or_none(ctx) is not None:
            return True
        entities = list(ctx.subsidiary_scope.get("selected") or [])
        return bool(
            entities
            and ctx.plan.subsidiary_xhs_enabled
            and ctx.xhs_selector is not None
        )

    def requires_core_lease(self, ctx: CompanyScanContext) -> bool:
        return self._checkpoint_or_none(ctx) is None

    async def run(self, ctx: CompanyScanContext) -> dict[str, Any]:
        checkpoint = self._checkpoint_or_none(ctx)
        if checkpoint is not None:
            self.apply(ctx, checkpoint)
            return dict(checkpoint)

        entities = list(ctx.subsidiary_scope.get("selected") or [])
        from api.services.xhs_target_selection import (
            XhsTargetCandidate,
            merge_xhs_target_selection_results,
        )

        candidates = [
            XhsTargetCandidate(
                target_id=str(entity.get("target_id") or ""),
                target_name=str(entity.get("name") or "").strip(),
                aliases=ctx.owner._dedupe_text(
                    [
                        str(entity.get("name") or ""),
                        *[str(item) for item in entity.get("aliases") or []],
                    ]
                ),
                root_domain=str(entity.get("root_domain") or ""),
                context={
                    "registration_status": str(entity.get("registration_status") or ""),
                    "icp_domains": list(entity.get("icp_domains") or []),
                    "relation_type": str(
                        entity.get("relation_type")
                        or (entity.get("relation") or {}).get("relation_type")
                        or "wholly_owned_direct_investment"
                    ),
                    "relation_depth": int(entity.get("relation_depth") or 1),
                    "parent_target_name": str(
                        entity.get("parent_target_name") or ctx.normalized_name
                    ),
                },
            )
            for entity in entities
            if str(entity.get("target_id") or "")
            and str(entity.get("name") or "").strip()
        ]
        selection = await ctx.xhs_selector.select(
            candidates,
            project_id=ctx.plan.project_id,
            task_id=ctx.plan.task_id,
        )
        ctx.xhs_selection_result = (
            merge_xhs_target_selection_results(ctx.xhs_selection_result, selection)
            if ctx.xhs_selection_result is not None
            else selection
        )
        ctx.result["xhs"]["selection"] = ctx.xhs_selection_result.model_dump(
            mode="json"
        )
        ctx.child_xhs_decisions = {
            item.target_id: item.model_dump(mode="json")
            for item in selection.decisions
        }
        return {
            "kind": self.name,
            "status": "completed",
            "scope_target_ids": self._scope_target_ids(ctx),
            "selection": dict(ctx.result["xhs"]["selection"]),
            "decisions": dict(ctx.child_xhs_decisions),
        }

    def _checkpoint_or_none(
        self,
        ctx: CompanyScanContext,
    ) -> dict[str, Any] | None:
        checkpoint = ctx.recovery.checkpoint_results.get(self.name)
        if not isinstance(checkpoint, dict):
            return None
        checkpoint_targets = list(checkpoint.get("scope_target_ids") or [])
        if checkpoint_targets != self._scope_target_ids(ctx):
            return None
        return dict(checkpoint)

    @staticmethod
    def _scope_target_ids(ctx: CompanyScanContext) -> list[str]:
        return sorted(
            {
                str(entity.get("target_id") or "")
                for entity in ctx.subsidiary_scope.get("selected") or []
                if str(entity.get("target_id") or "")
            }
        )

    @staticmethod
    def apply(ctx: CompanyScanContext, outcome: dict[str, Any]) -> None:
        selection = outcome.get("selection")
        if isinstance(selection, dict):
            ctx.result["xhs"]["selection"] = dict(selection)
        decisions = outcome.get("decisions")
        if isinstance(decisions, dict):
            ctx.child_xhs_decisions = {
                str(target_id): dict(decision)
                for target_id, decision in decisions.items()
                if isinstance(decision, dict)
            }


class RelatedSourceStage(ABC):
    name: str = ""

    @abstractmethod
    def enabled(self, ctx: CompanyScanContext) -> bool:
        ...

    @abstractmethod
    async def run(self, ctx: CompanyScanContext) -> dict[str, Any]:
        ...

    def requires_core_lease(self, ctx: CompanyScanContext) -> bool:
        return self.name not in ctx.recovery.checkpoint_results


class WhollyOwnedCollectionStage(RelatedSourceStage):
    name = "wholly_owned_entities"

    def enabled(self, ctx: CompanyScanContext) -> bool:
        if self.name in ctx.recovery.checkpoint_results:
            return True
        selected = list(ctx.subsidiary_scope.get("selected") or [])
        selected_xhs = any(
            item.get("should_collect_xhs")
            for item in ctx.child_xhs_decisions.values()
        )
        plan = ctx.plan
        return bool(
            selected
            and (
                plan.enable_asset_discovery
                or plan.enable_url_scan
                or selected_xhs
                or plan.subsidiary_bidding_enabled
            )
        )

    async def run(self, ctx: CompanyScanContext) -> dict[str, Any]:
        checkpoint = ctx.recovery.checkpoint_results.get(self.name)
        if checkpoint is not None:
            return dict(checkpoint)
        plan = ctx.plan
        return await ctx.owner._scan_wholly_owned_entities(
            task_id=plan.task_id,
            project_id=plan.project_id,
            entities=list(ctx.subsidiary_scope.get("selected") or []),
            enable_asset_discovery=plan.enable_asset_discovery,
            enable_url_scan=plan.enable_url_scan,
            enable_copywriting=plan.enable_copywriting,
            enable_xhs=plan.subsidiary_xhs_enabled,
            xhs_max_notes=plan.xhs_max_notes,
            xhs_attention_threshold=plan.xhs_attention_threshold,
            min_attention_score=plan.min_attention_score,
            profile_copywriting_threshold=plan.profile_copywriting_threshold,
            fofa_size=plan.fofa_size,
            hunter_size=plan.hunter_size,
            asset_probe_concurrency=plan.asset_probe_concurrency,
            incremental_scan=plan.incremental_scan,
            url_probe_concurrency=plan.url_probe_concurrency,
            url_scan_concurrency=plan.url_scan_concurrency,
            copywriting_concurrency=plan.copywriting_concurrency,
            xhs_search_concurrency=plan.xhs_search_concurrency,
            entity_concurrency=plan.control_scan_concurrency,
            enable_bidding=plan.subsidiary_bidding_enabled,
            bidding_page_size=plan.bidding_page_size,
            bidding_max_records=plan.bidding_max_records,
            bidding_lookback_days=plan.bidding_lookback_days,
            xhs_decisions=ctx.child_xhs_decisions,
            website_collection_mode=plan.website_collection_mode,
        )


class RelatedScholarStage(RelatedSourceStage):
    name = "scholar_entities"

    def enabled(self, ctx: CompanyScanContext) -> bool:
        if self.name in ctx.recovery.checkpoint_results:
            return True
        return bool(
            ctx.plan.enable_scholar
            and any(
                "scholar" in (entity.get("scan_channels") or [])
                for entity in ctx.subsidiary_scope.get("selected") or []
            )
        )

    async def run(self, ctx: CompanyScanContext) -> dict[str, Any]:
        checkpoint = ctx.recovery.checkpoint_results.get(self.name)
        if checkpoint is not None:
            return dict(checkpoint)
        entities = [
            entity
            for entity in ctx.subsidiary_scope.get("selected") or []
            if "scholar" in (entity.get("scan_channels") or [])
        ]
        return await ctx.owner._scan_scholar_entities(
            task_id=ctx.plan.task_id,
            project_id=ctx.plan.project_id,
            entities=entities,
            manual_direction=ctx.plan.scholar_direction,
            limit=ctx.plan.scholar_limit,
            entity_concurrency=ctx.plan.control_scan_concurrency,
        )


class RelatedSourceRegistry:
    def __init__(self) -> None:
        self._stages: dict[str, RelatedSourceStage] = {}

    def register(self, stage: RelatedSourceStage) -> "RelatedSourceRegistry":
        if not stage.name or stage.name in self._stages:
            raise ValueError(f"关联单位 Stage 重复或为空: {stage.name}")
        self._stages[stage.name] = stage
        return self

    def active(self, ctx: CompanyScanContext) -> list[RelatedSourceStage]:
        return [stage for stage in self._stages.values() if stage.enabled(ctx)]

    @classmethod
    def default(cls) -> "RelatedSourceRegistry":
        return (
            cls()
            .register(WhollyOwnedCollectionStage())
            .register(RelatedScholarStage())
        )


class RelatedSourceRuntimeStage:
    name = "related_sources"

    def __init__(
        self,
        registry: RelatedSourceRegistry,
        checkpoints: CompanyScanCheckpointRepository,
        selection_stage: RelatedXhsSelectionStage | None = None,
    ) -> None:
        self.registry = registry
        self.checkpoints = checkpoints
        self.selection_stage = selection_stage or RelatedXhsSelectionStage()

    async def run(self, ctx: CompanyScanContext) -> None:
        selection_enabled = self.selection_stage.enabled(ctx)
        lease_required = bool(
            selection_enabled
            and self.selection_stage.requires_core_lease(ctx)
        )
        lease_acquired = False
        try:
            if lease_required:
                await self._acquire_core_lease(ctx)
                lease_acquired = True
            if selection_enabled:
                selection = await self.selection_stage.run(ctx)
                await self.checkpoints.record(ctx, self.selection_stage.name, selection)

            stages = self.registry.active(ctx)
            if not stages:
                return
            if any(stage.requires_core_lease(ctx) for stage in stages):
                if not lease_acquired:
                    await self._acquire_core_lease(ctx)
                    lease_acquired = True
            await ctx.owner._update_progress(
                ctx.plan.task_id,
                "followup_collection",
                "采集控股关联单位...",
            )
            jobs = [(stage.name, stage.run(ctx)) for stage in stages]
            outcomes = await ctx.owner._gather_named_jobs(
                jobs,
                on_completed=lambda kind, outcome: self.checkpoints.record(
                    ctx, kind, outcome
                ),
            )
            for (kind, _operation), outcome in zip(jobs, outcomes):
                if isinstance(outcome, BaseException):
                    ctx.result["sub_errors"].append(f"{kind}: {outcome}")
                    raise outcome
                self._apply(ctx, kind, outcome)
        finally:
            if lease_acquired and ctx.core_lease is not None:
                ctx.core_lease.release()

    @staticmethod
    async def _acquire_core_lease(ctx: CompanyScanContext) -> None:
        await ctx.owner._update_progress(
            ctx.plan.task_id,
            "waiting_core",
            "等待资源采集控股关联单位...",
        )
        if ctx.core_lease is not None:
            await ctx.core_lease.acquire()

    @staticmethod
    def _apply(ctx: CompanyScanContext, kind: str, outcome: dict[str, Any]) -> None:
        if kind == "wholly_owned_entities":
            _apply_wholly_owned_result(ctx, outcome)
        elif kind == "scholar_entities":
            _apply_related_scholar_result(ctx, outcome)


def _apply_wholly_owned_result(
    ctx: CompanyScanContext,
    outcome: dict[str, Any],
) -> None:
    scanned = {
        str(item.get("target_id") or ""): item
        for item in outcome.get("entities") or []
    }
    skipped = {
        str(item.get("target_id") or ""): item
        for item in ctx.subsidiary_scope.get("skipped") or []
    }
    merged: list[dict[str, Any]] = []
    for entity in ctx.wholly_owned_entities:
        target_id = str(entity.get("target_id") or "")
        if target_id in scanned:
            merged.append(scanned[target_id])
            continue
        skipped_entity = skipped.get(target_id) or {}
        merged.append(
            {
                **entity,
                "scan": {
                    "status": "skipped",
                    "reason": str(skipped_entity.get("skip_reason") or "not_selected"),
                    "coverage": dict(skipped_entity.get("scan_coverage") or {}),
                },
            }
        )
    ctx.result["control_structure"].update(
        entities=merged,
        scan_summary=outcome["summary"],
    )
    ctx.result["control_structure"]["errors"].extend(outcome["errors"])
    ctx.result["profile_copywritings"]["count"] = int(
        outcome["summary"].get("profile_copywritings") or 0
    )


def _apply_related_scholar_result(
    ctx: CompanyScanContext,
    outcome: dict[str, Any],
) -> None:
    summary = dict(outcome.get("summary") or {})
    scholar = ctx.result["scholar"]
    scholar.update(
        descendant_status=outcome.get("status") or "completed",
        descendant_entities_total=int(summary.get("entities") or 0),
        descendant_entities_completed=int(summary.get("completed") or 0),
        descendant_articles_total=int(summary.get("articles_total") or 0),
        descendant_verified_articles_total=int(
            summary.get("verified_articles_total") or 0
        ),
        descendant_contacts_total=int(summary.get("contacts_total") or 0),
        related_entities=list(outcome.get("entities") or []),
    )
    for field in (
        "articles_total",
        "verified_articles_total",
        "unverified_articles_total",
        "contacts_total",
        "corresponding_count",
    ):
        scholar[field] = int(scholar.get(field) or 0) + int(summary.get(field) or 0)
    ctx.result["sub_errors"].extend(
        str(error) for error in outcome.get("errors") or []
    )
