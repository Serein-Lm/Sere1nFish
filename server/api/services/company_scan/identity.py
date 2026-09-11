"""Identity and channel-selection stage for company scans."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from core.logger import get_logger
from api.services.company_scan.contracts import CompanyScanContext


logger = get_logger("company_scan.identity")
COMPANY_NORMALIZE_TIMEOUT_SECONDS = 300
COMPANY_ROUTER_TIMEOUT_SECONDS = 120


@dataclass(slots=True)
class _IdentityInputs:
    pinned_target: dict[str, Any] | None
    company_meta: dict[str, Any]
    router_output: Any
    normalization_error: str = ""


class CompanyIdentityStage:
    name = "identity"

    async def run(self, ctx: CompanyScanContext) -> None:
        plan = ctx.plan
        logger.info("阶段 identity 开始 | task=%s company=%s", plan.task_id, plan.company_name)
        await ctx.owner._update_progress(
            plan.task_id,
            "routing",
            "识别法定主体、根域名和搜索别名...",
        )
        inputs = await self._resolve_inputs(ctx)
        merged = self._merge_identity(ctx, inputs)
        identity_reused = await self._persist_identity(ctx, inputs, merged)
        await self._resolve_channel_selection(ctx)
        await self._link_project_target(ctx)
        self._log_router_fallback(ctx, identity_reused)

    async def _resolve_inputs(self, ctx: CompanyScanContext) -> _IdentityInputs:
        from api.dao import company_meta as company_meta_dao
        from api.dao import targets as targets_dao
        from Sere1nGraph.graph.company_router.router import CompanyRouterResult

        plan = ctx.plan
        requested_target_id = str(plan.target_id or "").strip()
        pinned = (
            await targets_dao.get_target(ctx.db, requested_target_id)
            if requested_target_id
            else None
        )
        if requested_target_id and pinned is None:
            raise ValueError(f"指定的 Target 不存在: {requested_target_id}")
        if ctx.recovery.restore_core_context:
            meta = await company_meta_dao.get_company_meta(
                ctx.db, plan.project_id, plan.company_name
            ) or dict(ctx.recovery.restored_identity or {})
            return _IdentityInputs(
                pinned,
                meta,
                CompanyRouterResult(
                    success=False,
                    error="核心阶段从持久化结果恢复，未重复执行公司路由",
                ),
            )
        if pinned is not None and not plan.refresh_target_identity:
            return _IdentityInputs(
                pinned,
                self._pinned_company_meta(ctx, pinned),
                CompanyRouterResult(
                    success=False,
                    error="复用项目内已确认的 Target 身份，未重复执行公司路由",
                ),
            )
        return await self._run_identity_providers(ctx, pinned)

    async def _run_identity_providers(
        self,
        ctx: CompanyScanContext,
        pinned: dict[str, Any] | None,
    ) -> _IdentityInputs:
        from api.services.company_normalize import normalize_company
        from Sere1nGraph.graph.company_router.router import CompanyRouterResult

        plan = ctx.plan
        normalized, routed = await asyncio.gather(
            asyncio.wait_for(
                normalize_company(
                    ctx.db,
                    ctx.app_config,
                    project_id=plan.project_id,
                    input_name=plan.company_name,
                    task_id=plan.task_id,
                ),
                timeout=COMPANY_NORMALIZE_TIMEOUT_SECONDS,
            ),
            asyncio.wait_for(
                ctx.owner._run_company_router(
                    plan.company_name,
                    project_id=plan.project_id,
                    task_id=plan.task_id,
                ),
                timeout=COMPANY_ROUTER_TIMEOUT_SECONDS,
            ),
            return_exceptions=True,
        )
        error = ""
        if isinstance(normalized, BaseException):
            error = str(normalized) or "公司规范化执行超时"
            logger.warning("公司规范化失败，降级使用路由结果: %s", normalized)
            company_meta = {
                "normalized_name": plan.company_name,
                "root_domain": "",
                "aliases": [plan.company_name],
                "source": "fallback",
                "confidence": None,
            }
        else:
            company_meta = dict(normalized or {})
        router_output = (
            CompanyRouterResult(success=False, error=str(routed))
            if isinstance(routed, BaseException)
            else routed
        )
        return _IdentityInputs(pinned, company_meta, router_output, error)

    @staticmethod
    def _pinned_company_meta(
        ctx: CompanyScanContext,
        pinned: dict[str, Any],
    ) -> dict[str, Any]:
        profile = dict(pinned.get("scan_profile") or {})
        roots = ctx.owner._dedupe_text(
            [
                str(pinned.get("root_domain") or ""),
                *list(pinned.get("root_domains") or []),
                *list(pinned.get("official_root_domains") or []),
            ]
        )[:6]
        return {
            "normalized_name": str(
                pinned.get("canonical_name") or ctx.plan.company_name
            ).strip(),
            "root_domain": roots[0] if roots else "",
            "icp_domains": roots,
            "aliases": ctx.owner._dedupe_text(
                [
                    *list(pinned.get("identity_aliases") or []),
                    *list(profile.get("search_aliases") or []),
                ]
            )[:20],
            "source": "project_target_identity",
            "confidence": 1.0,
            "provenance": {
                "pinned_target_id": str(ctx.plan.target_id or "").strip(),
                "identity_reused": True,
            },
        }

    def _merge_identity(
        self,
        ctx: CompanyScanContext,
        inputs: _IdentityInputs,
    ) -> dict[str, Any]:
        plan = ctx.plan
        meta = inputs.company_meta
        router_profile = (
            inputs.router_output.company_profile
            if inputs.router_output.success
            else None
        )
        ctx.router_profile = router_profile
        router_legal_name = str(
            getattr(router_profile, "icp_name", "") or ""
        ).strip()
        candidate_name = str(meta.get("normalized_name") or plan.company_name).strip()
        if candidate_name == plan.company_name and router_legal_name:
            candidate_name = router_legal_name
        roots = ctx.owner._dedupe_text(
            [
                str(meta.get("root_domain") or "").strip(),
                *list(meta.get("icp_domains") or []),
            ]
        )[:6]
        if inputs.pinned_target is None:
            return {
                "normalized_name": candidate_name,
                "root_domains": roots,
                "aliases": ctx.owner._dedupe_text(
                    [
                        plan.company_name,
                        candidate_name,
                        *[str(item) for item in meta.get("aliases") or []],
                        *list(getattr(router_profile, "colloquial_names", []) or []),
                        router_legal_name,
                    ]
                )[:20],
                "normalization_matches": True,
                "router_matches": True,
                "provenance": dict(meta.get("provenance") or {}),
            }
        return self._merge_pinned_identity(
            ctx,
            inputs.pinned_target,
            candidate_name,
            roots,
            [str(item) for item in meta.get("aliases") or []],
            router_legal_name,
            router_profile,
            dict(meta.get("provenance") or {}),
        )

    def _merge_pinned_identity(
        self,
        ctx: CompanyScanContext,
        pinned: dict[str, Any],
        candidate_name: str,
        candidate_roots: list[str],
        normalized_aliases: list[str],
        router_legal_name: str,
        router_profile: Any,
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        from api.dao import targets as targets_dao

        plan = ctx.plan
        normalized_name = str(pinned.get("canonical_name") or plan.company_name).strip()
        pinned_key = targets_dao.normalize_target_name(normalized_name)
        normalization_matches = bool(
            targets_dao.normalize_target_name(candidate_name) == pinned_key
        )
        router_matches = bool(
            targets_dao.normalize_target_name(router_legal_name) == pinned_key
        )
        input_matches = bool(
            normalization_matches
            or targets_dao.normalize_target_name(plan.company_name) == pinned_key
        )
        roots = ctx.owner._dedupe_text(
            [
                str(pinned.get("root_domain") or ""),
                *list(pinned.get("root_domains") or []),
                *(candidate_roots if normalization_matches else []),
            ]
        )[:6]
        aliases = ctx.owner._dedupe_text(
            [
                *([plan.company_name] if input_matches else []),
                normalized_name,
                *list(pinned.get("identity_aliases") or []),
                *(
                    normalized_aliases
                    if normalization_matches
                    else []
                ),
                *(
                    list(getattr(router_profile, "colloquial_names", []) or [])
                    if router_matches
                    else []
                ),
            ]
        )[:20]
        provenance.update(
            {
                "pinned_target_id": str(plan.target_id or "").strip(),
                "normalization_candidate_name": candidate_name,
                "normalization_identity_mismatch": not normalization_matches,
                "input_identity_mismatch": not input_matches,
            }
        )
        if not input_matches:
            logger.warning(
                "忽略与固定 Target 不一致的输入身份 | task=%s target=%s input=%s",
                plan.task_id,
                normalized_name,
                plan.company_name,
            )
        return {
            "normalized_name": normalized_name,
            "root_domains": roots,
            "aliases": aliases,
            "normalization_matches": normalization_matches,
            "router_matches": router_matches,
            "provenance": provenance,
        }

    async def _persist_identity(
        self,
        ctx: CompanyScanContext,
        inputs: _IdentityInputs,
        merged: dict[str, Any],
    ) -> bool:
        from api.dao import company_meta as company_meta_dao
        from api.services.target_scan_profile import persist_target_scan_profile
        from api.services.targets import attach_normalized_company

        plan = ctx.plan
        normalized_name = merged["normalized_name"]
        roots = merged["root_domains"]
        aliases = merged["aliases"]
        provenance = merged["provenance"]
        target = await attach_normalized_company(
            ctx.db,
            project_id=plan.project_id,
            input_name=plan.company_name,
            normalized_name=normalized_name,
            root_domain=roots[0] if roots else "",
            root_domains=roots,
            aliases=aliases,
            task_id=plan.task_id,
            normalization_version=int(provenance.get("normalization_version") or 0) or None,
            preferred_target_id=str(plan.target_id or "").strip(),
            batch_tags=plan.target_batch_tags,
        )
        identity_reused = bool(provenance.get("identity_reused"))
        profile = self._build_scan_profile(ctx, inputs, merged, target, identity_reused)
        target = await persist_target_scan_profile(
            ctx.db,
            project_id=plan.project_id,
            target=target,
            profile=profile,
            routed_terms_by_channel=(
                inputs.router_output.all_keywords if inputs.router_output.success else {}
            ),
        )
        aliases = list(profile.get("search_aliases") or [normalized_name])
        target_id = str(target.get("target_id") or "")
        await company_meta_dao.upsert_company_meta(
            ctx.db,
            project_id=plan.project_id,
            input_name=plan.company_name,
            normalized_name=normalized_name,
            root_domain=roots[0] if roots else "",
            aliases=aliases,
            confidence=inputs.company_meta.get("confidence"),
            source=str(inputs.company_meta.get("source") or "company_scan"),
            task_id=plan.task_id,
            target_id=target_id,
            icp_domains=roots,
            provenance=provenance or None,
        )
        self._set_context_identity(ctx, inputs, target, profile, aliases, merged)
        return identity_reused

    def _build_scan_profile(
        self,
        ctx: CompanyScanContext,
        inputs: _IdentityInputs,
        merged: dict[str, Any],
        target: dict[str, Any],
        identity_reused: bool,
    ) -> dict[str, Any]:
        from api.services.target_scan_profile import build_target_scan_profile

        router_profile = ctx.router_profile
        return build_target_scan_profile(
            canonical_name=merged["normalized_name"],
            identity_aliases=list(target.get("identity_aliases") or []),
            verified_aliases=(
                [str(item) for item in inputs.company_meta.get("aliases") or [] if str(item).strip()]
                if inputs.pinned_target is None or merged["normalization_matches"]
                else []
            ),
            ai_aliases=(
                list(getattr(router_profile, "colloquial_names", []) or [])
                if router_profile
                and (inputs.pinned_target is None or merged["router_matches"])
                else []
            ),
            fallback_aliases=merged["aliases"],
            existing_profile=dict(target.get("scan_profile") or {}),
            ai_identity_verified=bool(
                router_profile
                and (inputs.pinned_target is None or merged["router_matches"])
            ),
            source="project_target_identity" if identity_reused else "company_scan_router",
        )

    def _set_context_identity(
        self,
        ctx: CompanyScanContext,
        inputs: _IdentityInputs,
        target: dict[str, Any],
        profile: dict[str, Any],
        aliases: list[str],
        merged: dict[str, Any],
    ) -> None:
        roots = merged["root_domains"]
        ctx.target = target
        ctx.target_id = str(target.get("target_id") or "")
        ctx.normalized_name = merged["normalized_name"]
        ctx.aliases = aliases
        ctx.router_output = inputs.router_output
        ctx.scan_profile = profile
        ctx.resolved_scholar_unit_en = ctx.owner._derive_scholar_unit_en(
            aliases,
            explicit=ctx.plan.scholar_unit_en,
        )
        provenance = merged["provenance"]
        error = inputs.normalization_error or str(provenance.get("browser_error") or "")
        ctx.result["identity"] = {
            "input_name": ctx.plan.company_name,
            "normalized_name": ctx.normalized_name,
            "root_domain": roots[0] if roots else "",
            "root_domains": roots,
            "official_root_domains": list(target.get("official_root_domains") or []),
            "asset_root_domains": list(target.get("asset_root_domains") or []),
            "aliases": aliases,
            "display_name": profile.get("display_name") or ctx.normalized_name,
            "short_names": list(profile.get("short_names") or []),
            "scan_profile_version": profile.get("version"),
            "scan_profile_fingerprint": profile.get("fingerprint"),
            "target_id": ctx.target_id,
            "normalization_error": error or None,
        }
        ctx.result["router_result"] = {
            "success": inputs.router_output.success,
            "identity_reused": bool(provenance.get("identity_reused")),
            "enabled_nodes": inputs.router_output.enabled_nodes,
            "keywords": inputs.router_output.all_keywords,
        }

    async def _resolve_channel_selection(self, ctx: CompanyScanContext) -> None:
        ctx.selection_context = self._selection_context(ctx.router_profile)
        await self._select_xhs(ctx)
        await self._select_wechat(ctx)
        if ctx.plan.enable_scholar:
            from api.services.scholar_direction import resolve_scholar_direction

            ctx.scholar_resolution = resolve_scholar_direction(
                ctx.plan.scholar_direction,
                ctx.router_output,
                names=ctx.aliases,
            )
            ctx.result["scholar"].update(
                direction=ctx.scholar_resolution.direction,
                direction_source=ctx.scholar_resolution.source,
                direction_terms=ctx.scholar_resolution.terms,
            )

    async def _select_xhs(self, ctx: CompanyScanContext) -> None:
        if not ctx.plan.enable_xhs:
            return
        from api.services.xhs_target_selection import (
            XhsTargetCandidate,
            XhsTargetSelectionService,
        )

        ctx.xhs_selector = XhsTargetSelectionService(
            ctx.app_config,
            mode=ctx.plan.xhs_target_selection_mode,
            manual_targets=ctx.result["xhs"]["selection"]["manual_targets"],
        )
        recovery = ctx.recovery
        if "xhs" in recovery.checkpoint_results:
            ctx.root_xhs_enabled = True
            ctx.result["xhs"]["root_selected"] = True
            ctx.result["xhs"]["status"] = "pending"
            ctx.result["xhs"]["selection"].update(status="restored", error=None)
            return
        if recovery.restore_core_context and "xhs" not in recovery.retryable_core_modules:
            ctx.root_xhs_enabled = False
            ctx.result["xhs"]["root_selected"] = False
            ctx.result["xhs"]["status"] = "skipped"
            ctx.result["xhs"]["selection"].update(status="restored", error=None)
            return
        selection = await ctx.xhs_selector.select(
            [self._xhs_candidate(ctx, XhsTargetCandidate)],
            project_id=ctx.plan.project_id,
            task_id=ctx.plan.task_id,
        )
        ctx.xhs_selection_result = selection
        ctx.result["xhs"]["selection"] = selection.model_dump(mode="json")
        decision = next(iter(selection.decisions), None)
        ctx.root_xhs_enabled = bool(decision and decision.should_collect_xhs)
        ctx.result["xhs"]["root_selected"] = ctx.root_xhs_enabled
        if not ctx.root_xhs_enabled:
            ctx.result["xhs"]["status"] = "skipped"

    async def _select_wechat(self, ctx: CompanyScanContext) -> None:
        if not ctx.plan.enable_wechat:
            return
        if ctx.recovery.restore_core_context:
            ctx.root_wechat_enabled = not ctx.recovery.resume_mobile_completed
            ctx.result["wechat"].update(selected=True, priority="normal")
            ctx.result["wechat"]["selection"].update(
                status="restored", selected_count=1, skipped_count=0, error=None
            )
            return
        from api.services.wechat_target_selection import (
            WechatTargetCandidate,
            WechatTargetSelectionService,
        )

        selection = await WechatTargetSelectionService(
            ctx.app_config,
            mode=ctx.plan.wechat_target_selection_mode,
        ).select(
            [
                WechatTargetCandidate(
                    target_id=ctx.target_id,
                    target_name=ctx.normalized_name,
                    aliases=ctx.aliases,
                    root_domain=str(ctx.result["identity"].get("root_domain") or ""),
                    context=ctx.selection_context,
                )
            ],
            project_id=ctx.plan.project_id,
            task_id=ctx.plan.task_id,
        )
        ctx.result["wechat"]["selection"] = selection.model_dump(mode="json")
        decision = next(iter(selection.decisions), None)
        ctx.root_wechat_enabled = bool(decision and decision.should_collect_wechat)
        ctx.result["wechat"].update(
            selected=ctx.root_wechat_enabled,
            priority=decision.collection_priority if decision else "skip",
        )
        if not ctx.root_wechat_enabled:
            ctx.result["wechat"]["status"] = "skipped"

    @staticmethod
    def _xhs_candidate(ctx: CompanyScanContext, candidate_type: Any) -> Any:
        return candidate_type(
            target_id=ctx.target_id,
            target_name=ctx.normalized_name,
            aliases=ctx.aliases,
            root_domain=str(ctx.result["identity"].get("root_domain") or ""),
            context=ctx.selection_context,
        )

    async def _link_project_target(self, ctx: CompanyScanContext) -> None:
        from api.dao import targets as targets_dao
        from api.services.search_terms import build_target_channel_terms

        channel_terms = build_target_channel_terms(
            names=ctx.aliases,
            routed_terms_by_channel=(
                ctx.router_output.all_keywords if ctx.router_output.success else {}
            ),
        )
        await targets_dao.link_project_target(
            ctx.db,
            project_id=ctx.plan.project_id,
            target=ctx.target,
            search_terms=ctx.aliases,
            search_terms_by_channel=channel_terms,
            task_def_id=ctx.plan.task_id,
            batch_tags=ctx.plan.target_batch_tags,
        )

    @staticmethod
    def _selection_context(profile: Any) -> dict[str, Any]:
        def value(field: str, default: Any) -> Any:
            raw = getattr(profile, field, default) if profile else default
            return getattr(raw, "value", raw)

        return {
            "industry": value("industry", "other"),
            "sub_industries": value("sub_industries", []),
            "business_nature": value("business_nature", "mixed"),
            "main_business": value("main_business", []),
            "tags": value("tags", []),
            "scale": value("scale", "unknown"),
            "is_listed": bool(value("is_listed", False)),
        }

    @staticmethod
    def _log_router_fallback(ctx: CompanyScanContext, identity_reused: bool) -> None:
        if ctx.router_output.success:
            return
        if ctx.recovery.restore_core_context:
            logger.info("核心阶段已恢复，跳过重复公司路由 | task=%s", ctx.plan.task_id)
        elif identity_reused:
            logger.info("已复用 Target 身份，跳过重复公司路由 | task=%s", ctx.plan.task_id)
        else:
            logger.warning("公司路由失败，使用默认策略: %s", ctx.router_output.error or "未返回错误详情")
