"""
综合公司扫描流水线（Company Scan Pipeline）

整合五条链路：
1. URL 扫描 → findings → 话术生成
2. 小红书搜索（多关键词）→ 打标 → 画像
3. 画像 → 话术生成（每个高分画像生成多套话术）
4. 控股关联单位分层发现 → ICP 补全 → 资产与社媒采集
5. 微信公众号手机发现 → 原文链接 → Chrome 全文与图片归档

前端只需传 company_name + 勾选项，后端自动编排。
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import PROFILE_COPYWRITINGS_COLLECTION
from core.logger import get_logger
from api.services.info_collection.tuning import (
    DEFAULT_ASSET_PROBE_CONCURRENCY,
    DEFAULT_COPYWRITING_CONCURRENCY,
    DEFAULT_COMPANY_SCAN_CONCURRENCY,
    DEFAULT_URL_PROBE_CONCURRENCY,
    DEFAULT_URL_SCAN_CONCURRENCY,
    DEFAULT_XHS_SEARCH_CONCURRENCY,
)
from api.services.company_scan.asset_url_adapter import (
    resolve_official_website_roots,
)
from api.services.company_scan.related_entity_adapter import related_entity_task_id
from api.services.company_scan.profile_copywriting_stage import (
    ProfileCopywritingStage as _ProfileCopywritingStage,
)
from api.services.company_scan.policies import (
    incomplete_collection_sources,
    requires_initial_core_lease as _requires_initial_core_lease,
    should_checkpoint_module,
)

logger = get_logger("company_scan")


class CompanyScanPipeline:
    """综合公司扫描流水线"""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        app_config: Any,
        *,
        selected_skill_ids: list[str] | tuple[str, ...] | None = None,
    ):
        self.db = db
        self.app_config = app_config
        self.selected_skill_ids = tuple(selected_skill_ids or ())

    # ══════════════════════════════════════
    # 主入口
    # ══════════════════════════════════════

    async def run_pipeline(
        self,
        task_id: str,
        project_id: str,
        company_name: str,
        target_id: str = "",
        batch_id: str = "",
        target_batch_tags: list[str] | tuple[str, ...] | str | None = None,
        url_text: str = "",
        urls: list[str] | None = None,
        enable_url_scan: bool = True,
        enable_asset_discovery: bool = True,
        enable_xhs: bool = False,
        enable_subsidiary_xhs: bool = False,
        enable_subsidiary_bidding: bool = False,
        xhs_target_selection_mode: str = "auto",
        xhs_manual_targets: list[str] | str | None = None,
        enable_bidding: bool = False,
        enable_bidding_visual_analysis: bool | None = None,
        bidding_page_size: int = 20,
        bidding_max_records: int = 20,
        bidding_lookback_days: int = 30,
        enable_wechat: bool = False,
        wechat_device_id: str = "",
        wechat_app_instance: str = "primary",
        wechat_target_selection_mode: str = "auto",
        enable_scholar: bool = True,
        scholar_direction: str = "",
        scholar_unit_en: str = "",
        scholar_limit: int = 10,
        enable_copywriting: bool = True,
        xhs_max_notes: int = 100,
        xhs_attention_threshold: int = 60,
        min_attention_score: int = 40,
        profile_copywriting_threshold: int = 60,
        fofa_size: int = 200,
        hunter_size: int = 200,
        asset_probe_concurrency: int = DEFAULT_ASSET_PROBE_CONCURRENCY,
        incremental_scan: bool = False,
        url_probe_concurrency: int = DEFAULT_URL_PROBE_CONCURRENCY,
        url_scan_concurrency: int = DEFAULT_URL_SCAN_CONCURRENCY,
        copywriting_concurrency: int = DEFAULT_COPYWRITING_CONCURRENCY,
        xhs_search_concurrency: int = DEFAULT_XHS_SEARCH_CONCURRENCY,
        enable_control_structure: bool = False,
        control_min_ownership_percent: float = 100.0,
        control_max_depth: int = 1,
        control_max_entities: int = 100,
        control_lookup_concurrency: int = 4,
        control_icp_concurrency: int = 6,
        control_scan_concurrency: int = 1,
        subsidiary_scan_limit: int = 12,
        skip_completed_subsidiaries: bool = True,
        company_core_concurrency: int = DEFAULT_COMPANY_SCAN_CONCURRENCY,
        website_collection_mode: str = "deep",
        website_root_domains: list[str] | None = None,
        website_required_path_segments: list[str] | None = None,
        requested_by: str = "",
        refresh_target_identity: bool = False,
    ) -> dict[str, Any]:
        """Build a versioned plan and delegate lifecycle to CompanyScanRuntime."""
        arguments = {
            key: value
            for key, value in locals().items()
            if key != "self"
        }
        from api.services.company_scan import CompanyScanPlan, CompanyScanRuntime

        plan = CompanyScanPlan.from_call(**arguments)
        return await CompanyScanRuntime(self, plan).run()

    # ══════════════════════════════════════
    # 阶段 1: 公司路由
    # ══════════════════════════════════════

    async def _run_company_router(
        self,
        company_name: str,
        *,
        project_id: str = "",
        task_id: str = "",
    ):
        from core.observability import observation_context
        from Sere1nGraph.graph.company_router.router import CompanyRouter

        router = CompanyRouter(self.app_config)
        with observation_context(
            project_id=project_id or None,
            task_id=task_id or None,
            phase="company_router",
            agent="company_router",
            task_type="company_scan",
        ):
            return await router.route(company_name)

    async def _run_wholly_owned_investments(
        self,
        *,
        task_id: str,
        project_id: str,
        parent_target: dict[str, Any],
        company_name: str,
        min_ownership_percent: float,
        max_depth: int,
        max_entities: int,
        page_concurrency: int,
        icp_concurrency: int,
    ) -> dict[str, Any]:
        from api.services.company_control import CompanyControlService

        result = await CompanyControlService(self.db).discover_and_persist(
            project_id=project_id,
            task_id=task_id,
            parent_target=parent_target,
            company_name=company_name,
            min_ownership_percent=min_ownership_percent,
            max_depth=max_depth,
            max_entities=max_entities,
            page_concurrency=page_concurrency,
            icp_concurrency=icp_concurrency,
        )
        return {"kind": "control_structure", "result": result}

    async def _run_wechat_collection(
        self,
        *,
        task_id: str,
        project_id: str,
        target_id: str,
        target_name: str,
        device_id: str,
        app_instance: str = "primary",
        collection_priority: str = "normal",
        requested_by: str = "",
        started_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        from api.services.wechat_collection import run_company_wechat_collection

        async def _on_started() -> None:
            if started_event is not None:
                started_event.set()
            from api.services.task_progress import update_source_progress

            await update_source_progress(
                self.db,
                task_id=task_id,
                source="wechat",
                status="running",
                message="公众号任务已获得手机，正在采集",
            )

        return await run_company_wechat_collection(
            self.db,
            task_id=task_id,
            project_id=project_id,
            target_id=target_id,
            target_name=target_name,
            device_id=device_id,
            app_instance=app_instance,
            collection_priority=collection_priority,
            requested_by=requested_by,
            on_started=_on_started,
        )

    async def _run_scholar_collection(
        self,
        *,
        task_id: str,
        project_id: str,
        target_id: str,
        unit: str,
        direction: str,
        direction_source: str = "manual",
        unit_en: str = "",
        limit: int = 10,
    ) -> dict[str, Any]:
        from api.services.scholar_contact_pipeline import run_scholar_contact_collect

        summary = await run_scholar_contact_collect(
            self.db,
            self.app_config,
            task_id=task_id,
            project_id=project_id,
            target_id=target_id,
            unit=unit,
            direction=str(direction or "").strip(),
            unit_en=str(unit_en or "").strip(),
            limit=max(1, min(int(limit or 10), 50)),
            notify_completion=False,
        )
        return {
            "kind": "scholar",
            **summary,
            "direction_source": direction_source,
        }

    async def _run_bidding_collection(
        self,
        *,
        task_id: str,
        project_id: str,
        company_name: str,
        target_id: str,
        page_size: int,
        max_records: int,
        lookback_days: int,
        enable_visual_analysis: bool,
        enable_copywriting: bool,
        min_attention_score: int,
        scan_concurrency: int,
        copywriting_concurrency: int,
    ) -> dict[str, Any]:
        from api.services.bidding_pipeline import BiddingPipeline

        try:
            return await BiddingPipeline(self.db, self.app_config).run_pipeline(
                task_id=f"{task_id}_bidding",
                project_id=project_id,
                company_name=company_name,
                target_id=target_id,
                parent_task_id=task_id,
                page_size=max(1, min(int(page_size), 20)),
                max_records=max(1, min(int(max_records), 2000)),
                lookback_days=max(1, min(int(lookback_days), 30)),
                enable_visual_analysis=enable_visual_analysis,
                enable_copywriting=enable_copywriting,
                min_attention_score=min_attention_score,
                scan_concurrency=max(1, min(int(scan_concurrency), 3)),
                copywriting_concurrency=max(1, min(int(copywriting_concurrency), 2)),
                selected_skill_ids=list(self.selected_skill_ids),
            )
        except Exception as exc:  # noqa: BLE001
            from core.llm_capacity import LLMCapacityUnavailableError

            if isinstance(exc, LLMCapacityUnavailableError):
                raise
            logger.exception("[company_scan] 招投标子流水线失败: %s", exc)
            return {
                "kind": "bidding",
                "enabled": True,
                "status": "error",
                "query_name": company_name,
                "records_fetched": 0,
                "error": str(exc),
                "visual_analysis": {
                    "status": "error",
                    "findings_count": 0,
                    "copywritings_count": 0,
                },
            }

    async def _scan_wholly_owned_entities(
        self,
        *,
        task_id: str,
        project_id: str,
        entities: list[dict[str, Any]],
        enable_asset_discovery: bool,
        enable_url_scan: bool,
        enable_copywriting: bool,
        enable_xhs: bool,
        xhs_max_notes: int,
        xhs_attention_threshold: int,
        min_attention_score: int,
        profile_copywriting_threshold: int,
        fofa_size: int,
        hunter_size: int,
        asset_probe_concurrency: int,
        incremental_scan: bool,
        url_probe_concurrency: int,
        url_scan_concurrency: int,
        copywriting_concurrency: int,
        xhs_search_concurrency: int,
        entity_concurrency: int,
        enable_bidding: bool = False,
        bidding_page_size: int = 20,
        bidding_max_records: int = 20,
        bidding_lookback_days: int = 30,
        xhs_decisions: dict[str, dict[str, Any]] | None = None,
        website_collection_mode: str = "deep",
    ) -> dict[str, Any]:
        from api.services.company_scan.related_entity_adapter import (
            RelatedEntityCollectionAdapter,
            RelatedEntityCollectionRequest,
        )

        request = RelatedEntityCollectionRequest(
            task_id=task_id,
            project_id=project_id,
            entities=tuple(entities),
            enable_asset_discovery=enable_asset_discovery,
            enable_url_scan=enable_url_scan,
            enable_copywriting=enable_copywriting,
            enable_xhs=enable_xhs,
            xhs_max_notes=xhs_max_notes,
            xhs_attention_threshold=xhs_attention_threshold,
            min_attention_score=min_attention_score,
            profile_copywriting_threshold=profile_copywriting_threshold,
            fofa_size=fofa_size,
            hunter_size=hunter_size,
            asset_probe_concurrency=asset_probe_concurrency,
            incremental_scan=incremental_scan,
            url_probe_concurrency=url_probe_concurrency,
            url_scan_concurrency=url_scan_concurrency,
            copywriting_concurrency=copywriting_concurrency,
            xhs_search_concurrency=xhs_search_concurrency,
            entity_concurrency=entity_concurrency,
            enable_bidding=enable_bidding,
            bidding_page_size=bidding_page_size,
            bidding_max_records=bidding_max_records,
            bidding_lookback_days=bidding_lookback_days,
            xhs_decisions=xhs_decisions,
            website_collection_mode=website_collection_mode,
        )
        return await RelatedEntityCollectionAdapter(self, request).run()

    async def _scan_scholar_entities(
        self,
        *,
        task_id: str,
        project_id: str,
        entities: list[dict[str, Any]],
        manual_direction: str = "",
        limit: int = 10,
        entity_concurrency: int = 1,
    ) -> dict[str, Any]:
        from api.services.company_scan.scholar_entity_adapter import (
            ScholarEntityCollectionAdapter,
            ScholarEntityCollectionRequest,
        )

        request = ScholarEntityCollectionRequest(
            task_id=task_id,
            project_id=project_id,
            entities=tuple(entities),
            manual_direction=manual_direction,
            limit=limit,
            entity_concurrency=entity_concurrency,
        )
        return await ScholarEntityCollectionAdapter(self, request).run()
    # ══════════════════════════════════════
    # 阶段 2a: URL 扫描
    # ══════════════════════════════════════

    async def _run_asset_and_url_scan(
        self,
        *,
        task_id: str,
        project_id: str,
        identity: dict[str, Any],
        url_text: str,
        urls: list[str],
        enable_asset_discovery: bool,
        enable_url_scan: bool,
        enable_copywriting: bool,
        min_attention_score: int,
        fofa_size: int,
        hunter_size: int,
        probe_concurrency: int,
        incremental_scan: bool = False,
        url_probe_concurrency: int = DEFAULT_URL_PROBE_CONCURRENCY,
        url_scan_concurrency: int = DEFAULT_URL_SCAN_CONCURRENCY,
        copywriting_concurrency: int = DEFAULT_COPYWRITING_CONCURRENCY,
        progress_task_id: str = "",
        progress_source: str = "",
        website_collection_mode: str = "deep",
        website_root_domains: list[str] | None = None,
        website_required_path_segments: list[str] | None = None,
    ) -> dict[str, Any]:
        from api.services.company_scan.asset_url_adapter import (
            AssetUrlCollectionAdapter,
            AssetUrlCollectionRequest,
        )

        request = AssetUrlCollectionRequest(
            task_id=task_id,
            project_id=project_id,
            identity=identity,
            url_text=url_text,
            urls=tuple(urls),
            enable_asset_discovery=enable_asset_discovery,
            enable_url_scan=enable_url_scan,
            enable_copywriting=enable_copywriting,
            min_attention_score=min_attention_score,
            fofa_size=fofa_size,
            hunter_size=hunter_size,
            probe_concurrency=probe_concurrency,
            incremental_scan=incremental_scan,
            url_probe_concurrency=url_probe_concurrency,
            url_scan_concurrency=url_scan_concurrency,
            copywriting_concurrency=copywriting_concurrency,
            progress_task_id=progress_task_id,
            progress_source=progress_source,
            website_collection_mode=website_collection_mode,
            website_root_domains=tuple(website_root_domains or ()),
            website_required_path_segments=tuple(
                website_required_path_segments or ()
            ),
        )
        return await AssetUrlCollectionAdapter(self, request).run()

    async def _run_url_scan(
        self,
        task_id: str,
        project_id: str,
        url_text: str,
        urls: list[str],
        min_attention_score: int,
        enable_copywriting: bool,
        target_id: str = "",
        known_alive_urls: list[str] | None = None,
        known_alive_metadata: dict[str, dict[str, Any]] | None = None,
        probe_concurrency: int = DEFAULT_URL_PROBE_CONCURRENCY,
        scan_concurrency: int = DEFAULT_URL_SCAN_CONCURRENCY,
        copywriting_concurrency: int = DEFAULT_COPYWRITING_CONCURRENCY,
        progress_task_id: str = "",
        progress_source: str = "",
        target_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from api.services.url_scan_pipeline import UrlScanPipeline

        url_content = url_text
        if urls:
            url_content = "\n".join(urls) + ("\n" + url_text if url_text else "")

        pipeline = UrlScanPipeline(self.db, self.app_config)
        scan_result = await pipeline.run_pipeline(
            task_id=f"{task_id}_url",
            project_id=project_id,
            url_content=url_content,
            min_attention_score=min_attention_score,
            target_id=target_id,
            enable_copywriting=enable_copywriting,
            known_alive_urls=known_alive_urls,
            known_alive_metadata=known_alive_metadata,
            parent_task_id=progress_task_id or task_id,
            progress_source=progress_source,
            probe_concurrency=probe_concurrency,
            scan_concurrency=scan_concurrency,
            copywriting_concurrency=copywriting_concurrency,
            target_context=target_context,
            selected_skill_ids=list(self.selected_skill_ids),
        )
        if scan_result.get("status") == "error":
            raise RuntimeError(str(scan_result.get("error") or "URL 深度扫描失败"))

        return {
            "findings_count": scan_result.get("total_findings", 0),
            "copywritings_count": scan_result.get("total_copywritings", 0),
            "total_urls": scan_result.get("total_urls", 0),
            "eligible_urls": scan_result.get("eligible_urls", 0),
            "scanned_urls": scan_result.get("scanned_urls", 0),
            "failed_urls": scan_result.get("failed_urls", 0),
            "remaining_urls": scan_result.get("remaining_urls", 0),
            "status": scan_result.get("status"),
            "error": scan_result.get("error"),
        }

    # ══════════════════════════════════════
    # 阶段 2b: 小红书搜索（多关键词）
    # ══════════════════════════════════════

    async def _run_xhs_search(
        self,
        task_id: str,
        project_id: str,
        keywords: list[str],
        max_notes: int,
        attention_threshold: int,
        target_id: str = "",
        target_name: str = "",
        search_concurrency: int = DEFAULT_XHS_SEARCH_CONCURRENCY,
    ) -> dict[str, Any]:
        from api.services.company_scan.xhs_adapter import (
            XhsCollectionAdapter,
            XhsCollectionRequest,
        )

        request = XhsCollectionRequest(
            task_id=task_id,
            project_id=project_id,
            keywords=tuple(keywords),
            max_notes=max_notes,
            attention_threshold=attention_threshold,
            target_id=target_id,
            target_name=target_name,
            search_concurrency=search_concurrency,
        )
        return await XhsCollectionAdapter(self, request).run()

    # ══════════════════════════════════════
    # 阶段 3: 画像→话术
    # ══════════════════════════════════════

    async def _run_profile_copywriting(
        self,
        task_id: str,
        project_id: str,
        company_name: str,
        router_output: Any,
        threshold: int,
        target_id: str = "",
    ) -> int:
        """为高分画像生成话术"""
        from api.dao import xhs as xhs_dao
        from api.services.info_collection.factory import InfoCollectionToolFactory
        from api.services.info_collection.streaming import make_stream_items, run_stream_pipeline, stream_stage

        # 获取高分画像
        profiles, _ = await xhs_dao.list_profiles(
            self.db,
            project_id,
            target_id=target_id or None,
            limit=500,
        )
        high_profiles = [
            p for p in profiles
            if (p.get("attention_score") or p.get("tagging", {}).get("attention_score", 0)) >= threshold
        ]

        if not high_profiles:
            logger.info(f"[company_scan] 无高分画像（阈值={threshold}），跳过话术生成")
            return 0

        logger.info(f"[company_scan] 为 {len(high_profiles)} 个高分画像生成话术")

        copywriting_tool = InfoCollectionToolFactory(
            db=self.db,
            app_config=self.app_config,
        ).create_copywriting_tool()
        concurrency = min(6, max(1, len(high_profiles)))
        stage = _ProfileCopywritingStage(
            concurrency=concurrency,
            project_id=project_id,
            task_id=task_id,
            company_name=company_name,
            router_output=router_output,
            db=self.db,
            pipeline_owner=self,
            target_id=target_id,
            selected_skill_ids=self.selected_skill_ids,
        )
        pipe = await run_stream_pipeline(
            stages=[stream_stage(stage)],
            seeds=make_stream_items(high_profiles, indexed=True),
            entry="profile_copywriting",
            state={
                "db": self.db,
                "profile_copywriting_tool": copywriting_tool,
                "profile_copywriting_count": 0,
            },
        )

        return int(pipe.state.get("profile_copywriting_count", 0))

    # ══════════════════════════════════════
    # 辅助方法
    # ══════════════════════════════════════

    @staticmethod
    async def _completed_module_result(result: dict[str, Any]) -> dict[str, Any]:
        return dict(result)

    @staticmethod
    def _jobs_completed_successfully(
        jobs: list[tuple[str, Any]],
        results: list[Any],
    ) -> bool:
        return len(jobs) == len(results) and all(
            not isinstance(outcome, BaseException)
            and should_checkpoint_module(kind, outcome)
            for (kind, _operation), outcome in zip(jobs, results)
        )

    async def _run_mobile_jobs(
        self,
        jobs: list[tuple[str, Any]],
        *,
        task_id: str,
        on_completed: Any = None,
    ) -> list[Any]:
        """Run mobile sources and persist their resume boundary independently."""
        checkpoint_errors: set[str] = set()
        results = await self._gather_named_jobs(
            jobs,
            on_completed=on_completed,
            on_checkpoint_error=lambda kind, _error: checkpoint_errors.add(kind),
        )
        if not checkpoint_errors and self._jobs_completed_successfully(
            jobs,
            results,
        ):
            from api.services.task_progress import mark_resume_phase

            await mark_resume_phase(
                self.db,
                task_id=task_id,
                phase="mobile_completed",
            )
        return results

    @staticmethod
    def _merge_primary_job_results(
        result: dict[str, Any],
        jobs: list[tuple[str, Any]],
        outcomes: list[Any],
    ) -> tuple[set[str], bool]:
        """Merge independently completed sources without coupling their runtimes."""
        failed_jobs: set[str] = set()
        xhs_succeeded = False
        for (kind, _operation), outcome in zip(jobs, outcomes):
            if isinstance(outcome, BaseException):
                failed_jobs.add(kind)
                logger.error("[company_scan] %s 子流水线失败: %s", kind, outcome)
                result["sub_errors"].append(f"{kind}: {outcome}")
                if kind == "wechat":
                    result["wechat"].update(status="error", error=str(outcome))
                elif kind == "scholar":
                    result["scholar"].update(status="error", error=str(outcome))
                continue
            if not isinstance(outcome, dict):
                continue
            if kind == "asset_url":
                result["assets"].update(outcome.get("assets") or {})
                result["url_scan"].update(outcome.get("url_scan") or {})
                result["website_documents"].update(
                    outcome.get("website_documents") or {}
                )
                if outcome.get("status") == "error":
                    failed_jobs.add(kind)
                if outcome.get("status") in {"partial", "error"}:
                    messages = [
                        str(section.get("error") or "")
                        for section in (
                            outcome.get("url_scan") or {},
                            outcome.get("website_documents") or {},
                        )
                        if str(section.get("error") or "").strip()
                    ]
                    result["sub_errors"].extend(messages)
            elif kind == "control_structure":
                result["control_structure"].update(outcome.get("result") or {})
            elif kind == "bidding":
                visual = outcome.get("visual_analysis") or {}
                bidding_failed = outcome.get("status") == "error"
                bidding_disabled = outcome.get("status") == "disabled"
                bidding_partial = bool(
                    outcome.get("status") == "partial"
                    or visual.get("status") == "error"
                    or outcome.get("archive_error_count")
                )
                result["bidding"].update(
                    outcome,
                    status=(
                        "disabled"
                        if bidding_disabled
                        else "error"
                        if bidding_failed
                        else "partial"
                        if bidding_partial
                        else "completed"
                    ),
                    findings_count=visual.get("findings_count", 0),
                    copywritings_count=visual.get("copywritings_count", 0),
                )
                if bidding_failed and outcome.get("error"):
                    failed_jobs.add(kind)
                    result["sub_errors"].append(str(outcome["error"]))
            elif kind == "xhs":
                result["xhs"].update(outcome)
                xhs_succeeded = outcome.get("status") != "error"
                if not xhs_succeeded:
                    failed_jobs.add(kind)
            elif kind == "wechat":
                result["wechat"].update(outcome)
                if outcome.get("status") == "error":
                    failed_jobs.add(kind)
            elif kind == "scholar":
                result["scholar"].update(outcome)
                if outcome.get("status") == "error":
                    failed_jobs.add(kind)
        return failed_jobs, xhs_succeeded

    @staticmethod
    async def _gather_named_jobs(
        jobs: list[tuple[str, Any]],
        *,
        on_completed: Any = None,
        on_checkpoint_error: Any = None,
    ) -> list[Any]:
        """Run independent source pipelines concurrently with isolated failures."""
        if not jobs:
            return []

        async def _run(kind: str, operation: Any) -> Any:
            outcome = await operation
            if on_completed is not None:
                try:
                    await on_completed(kind, outcome)
                except Exception as checkpoint_error:  # noqa: BLE001
                    logger.warning(
                        "公司扫描模块检查点写入失败 | module=%s error=%s",
                        kind,
                        checkpoint_error,
                    )
                    if on_checkpoint_error is not None:
                        on_checkpoint_error(kind, checkpoint_error)
            return outcome

        results = list(
            await asyncio.gather(
                *(_run(kind, operation) for kind, operation in jobs),
                return_exceptions=True,
            )
        )
        from core.llm_capacity import LLMCapacityUnavailableError

        for result in results:
            if isinstance(result, LLMCapacityUnavailableError):
                raise result
        return results

    @staticmethod
    def _dedupe_text(values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                value.strip() for value in values if isinstance(value, str) and value.strip()
            )
        )

    @staticmethod
    def _derive_scholar_unit_en(
        aliases: list[str],
        *,
        explicit: str = "",
    ) -> str:
        """Prefer a descriptive English institution name, not an ambiguous acronym."""
        configured = str(explicit or "").strip()
        if configured:
            return configured
        candidates = [
            value.strip()
            for value in aliases
            if isinstance(value, str)
            and value.strip()
            and value.isascii()
            and any(character.isalpha() for character in value)
            and len(re.findall(r"[A-Za-z]{2,}", value)) >= 2
            and sum(
                len(token) for token in re.findall(r"[A-Za-z]{2,}", value)
            )
            >= 10
        ]
        return max(candidates, key=lambda value: (len(value), value), default="")

    def _get_xhs_keywords(self, search_names: list[str], router_output: Any) -> list[str]:
        """组合路由结果与数据库 XHS Skill，法定名不再是唯一检索入口。"""
        from api.services.search_terms import build_channel_terms

        routed = (
            list(router_output.all_keywords.get("xhs") or [])
            if router_output.success
            else []
        )
        return build_channel_terms(
            channel="xhs",
            names=search_names,
            routed_terms=routed,
            limit=20,
        )

    def _build_profile_copywriting_context(
        self,
        profile: dict[str, Any],
        company_name: str,
        router_output: Any,
    ) -> str:
        """构建画像→话术的上下文"""
        parts = []

        # 公司信息
        parts.append("# 目标公司信息")
        parts.append(f"- 公司名称: {company_name}")
        if router_output.success and router_output.company_profile:
            cp = router_output.company_profile
            parts.append(f"- 行业: {cp.industry}")
            parts.append(f"- 业务性质: {cp.business_nature}")
            parts.append(f"- 主营业务: {', '.join(cp.main_business)}")

        # 人物画像
        parts.append("")
        parts.append("# 目标人物画像（来自小红书分析）")
        parts.append(f"- 昵称: {profile.get('nickname', '未知')}")
        parts.append(f"- 用户ID: {profile.get('user_id', '')}")

        identity = profile.get("identity") or profile.get("tagging", {}).get("identity", {})
        if identity:
            parts.append(f"- 公司: {identity.get('company', '未知')}")
            parts.append(f"- 职位: {identity.get('position', '未知')}")
            parts.append(f"- 部门: {identity.get('department', '未知')}")

        personality = profile.get("personality_profile") or profile.get("tagging", {}).get("personality_profile", {})
        if personality:
            parts.append(f"- 性格特征: {personality}")

        summary = profile.get("profile_summary") or profile.get("tagging", {}).get("profile_summary", "")
        if summary:
            parts.append(f"- 画像摘要: {summary}")

        # 攻击面
        attack = profile.get("attack_surface") or profile.get("tagging", {}).get("attack_surface", {})
        if attack:
            parts.append("")
            parts.append("# 攻击面分析")
            parts.append(f"- 风险评分: {attack.get('risk_score', 0)}/100")

            exposed = attack.get("exposed_information", [])
            if exposed:
                parts.append("- 暴露信息:")
                for info in exposed[:10]:
                    parts.append(f"  - [{info.get('category', '')}] {info.get('type', '')}: {info.get('value', '')}")

            vectors = attack.get("attack_vectors", [])
            if vectors:
                parts.append("- 攻击向量:")
                for v in vectors[:5]:
                    parts.append(f"  - {v.get('vector', '')}: {v.get('description', '')} (可行性={v.get('feasibility', '')})")

        # 关注度
        score = profile.get("attention_score") or profile.get("tagging", {}).get("attention_score", 0)
        parts.append(f"\n- 关注度评分: {score}/100")

        actions = profile.get("recommended_actions") or profile.get("tagging", {}).get("recommended_actions", [])
        if actions:
            parts.append("- 建议动作:")
            for a in actions[:5]:
                parts.append(f"  - {a}")

        parts.append("")
        parts.append("# 任务要求")
        parts.append("请为该人物生成 2-3 套不同攻击向量的话术。每套话术对应一个不同的社工场景。")
        parts.append("话术必须利用画像中的具体信息，渠道必须与暴露的联系方式匹配。")

        return "\n".join(parts)

    async def _update_progress(self, task_id: str, stage: str, message: str):
        """更新任务进度"""
        from api.services.task_progress import update_task_stage

        await update_task_stage(
            self.db,
            task_id=task_id,
            stage=stage,
            message=message,
        )

    @staticmethod
    def _parse_agent_response(result: dict[str, Any]) -> Any:
        """解析 Agent 响应（支持数组和对象）"""
        from api.utils.json_extract import extract_json_object
        messages = result.get("messages", []) if isinstance(result, dict) else []
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            if isinstance(content, str) and content.strip():
                try:
                    return extract_json_object(content.strip())
                except Exception:
                    continue
        return None
