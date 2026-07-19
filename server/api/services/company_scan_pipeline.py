"""
综合公司扫描流水线（Company Scan Pipeline）

整合五条链路：
1. URL 扫描 → findings → 话术生成
2. 小红书搜索（多关键词）→ 打标 → 画像
3. 画像 → 话术生成（每个高分画像生成多套话术）
4. 第一层全资子公司发现 → ICP 补全 → 资产与社媒采集
5. 微信公众号手机发现 → 原文链接 → Chrome 全文与图片归档

前端只需传 company_name + 勾选项，后端自动编排。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.logger import get_logger
from core.stream import Stage, Item, RetryPolicy
from api.services.info_collection.tuning import (
    DEFAULT_ASSET_PROBE_CONCURRENCY,
    DEFAULT_COPYWRITING_CONCURRENCY,
    DEFAULT_COMPANY_SCAN_CONCURRENCY,
    DEFAULT_URL_PROBE_CONCURRENCY,
    DEFAULT_URL_SCAN_CONCURRENCY,
    DEFAULT_XHS_SEARCH_CONCURRENCY,
)
from api.services.info_collection.xhs_stages import (
    XhsDetailStage as _XhsDetailStage,
    XhsSearchStage as _XhsSearchStage,
    XhsTaggingStage as _XhsTaggingStage,
)
from api.services.company_url import normalize_url

logger = get_logger("company_scan")

# 集合名
COMPANY_SCAN_COLLECTION = "company_scan_results"
PROFILE_COPYWRITINGS_COLLECTION = "profile_copywritings"
COMPANY_NORMALIZE_TIMEOUT_SECONDS = 300
COMPANY_ROUTER_TIMEOUT_SECONDS = 120


class _ProfileCopywritingStage(Stage):
    """Generate copywriting for one high-score profile through a copywriting tool."""

    name = "profile_copywriting"
    retry = RetryPolicy(max_attempts=2, base_delay=2.0, jitter=True)

    def __init__(
        self,
        *,
        concurrency: int,
        project_id: str,
        task_id: str,
        company_name: str,
        router_output: Any,
        db: Any,
        pipeline_owner: Any,
        target_id: str = "",
    ) -> None:
        self.project_id = project_id
        self.task_id = task_id
        self.company_name = company_name
        self.router_output = router_output
        self.db = db
        self.pipeline_owner = pipeline_owner
        self.target_id = target_id
        super().__init__(concurrency=concurrency)

    async def on_setup(self, state: dict[str, Any]) -> None:
        import json
        from Sere1nGraph.graph.skills.schemas import FindingCopywriting

        state.setdefault("profile_copywriting_count", 0)
        state.setdefault(
            "_profile_copywriting_schema_json",
            json.dumps(FindingCopywriting.model_json_schema(), ensure_ascii=False, indent=2),
        )

    async def handle(self, item: Item, ctx) -> None:
        from api.dao import findings as findings_dao
        from api.services.info_collection import CopywritingRequest
        from Sere1nGraph.graph.skills.schemas import FindingCopywriting

        profile = item.payload
        user_id = profile.get("user_id", "")
        copywriting_tool = ctx.state.get("profile_copywriting_tool")
        if not copywriting_tool:
            raise RuntimeError("profile_copywriting_tool 未初始化")

        url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
        context = self.pipeline_owner._build_profile_copywriting_context(
            profile,
            self.company_name,
            self.router_output,
        )
        context += (
            f"\n\n# 输出 JSON Schema\n\n```json\n"
            f"{ctx.state['_profile_copywriting_schema_json']}\n"
            f"```\n\nfinding_id 前缀: profile_{user_id or 'unknown'}\n"
            f"url: {url}"
        )

        result = await copywriting_tool.generate(
            CopywritingRequest(
                source="xhs_profile",
                project_id=self.project_id,
                task_id=self.task_id,
                target_id=user_id,
                target=profile,
                context=context,
                options={"url": url, "response_model": FindingCopywriting},
            )
        )
        if not result.ok:
            ctx.logger.warning(
                f"[profile-cw-w{ctx.worker_id}] 话术生成无结果 user={user_id} "
                f"error={result.meta.get('error', '')}"
            )
            return

        for generated in result.copywritings:
            cw = dict(generated)
            cw.setdefault("finding_id", f"profile_{user_id or 'unknown'}")
            cw.setdefault("url", url)
            cw["task_id"] = self.task_id
            cw["project_id"] = self.project_id
            cw["source"] = "xhs_profile"
            cw["user_id"] = user_id
            if self.target_id:
                cw["target_id"] = self.target_id
            cw["status"] = "completed"
            await self.db[PROFILE_COPYWRITINGS_COLLECTION].insert_one(cw)

            try:
                finding_query = {"project_id": self.project_id, "xhs_user_id": user_id}
                if self.target_id:
                    finding_query["target_id"] = self.target_id
                finding = await self.db["findings"].find_one(
                    finding_query,
                    {"finding_id": 1},
                )
                if finding:
                    await findings_dao.insert_copywriting(
                        self.db,
                        {**cw, "finding_id": finding["finding_id"]},
                    )
            except Exception as store_err:
                ctx.logger.warning(
                    f"[profile-cw-w{ctx.worker_id}] 统一话术落库失败 user={user_id}: {store_err}"
                )

            ctx.state["profile_copywriting_count"] = ctx.state.get("profile_copywriting_count", 0) + 1

        ctx.logger.info(
            f"[profile-cw-w{ctx.worker_id}] 完成 user={user_id} "
            f"count={result.count} total={ctx.state.get('profile_copywriting_count', 0)}"
        )


class CompanyScanPipeline:
    """综合公司扫描流水线"""

    def __init__(self, db: AsyncIOMotorDatabase, app_config: Any):
        self.db = db
        self.app_config = app_config

    # ══════════════════════════════════════
    # 主入口
    # ══════════════════════════════════════

    async def run_pipeline(
        self,
        task_id: str,
        project_id: str,
        company_name: str,
        batch_id: str = "",
        url_text: str = "",
        urls: list[str] | None = None,
        enable_url_scan: bool = True,
        enable_asset_discovery: bool = True,
        enable_xhs: bool = False,
        enable_subsidiary_xhs: bool = False,
        xhs_target_selection_mode: str = "auto",
        xhs_manual_targets: list[str] | str | None = None,
        enable_bidding: bool = True,
        bidding_page_size: int = 20,
        enable_wechat: bool = False,
        wechat_device_id: str = "",
        wechat_target_selection_mode: str = "auto",
        enable_scholar: bool = False,
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
        enable_control_structure: bool = True,
        control_max_entities: int = 100,
        control_lookup_concurrency: int = 4,
        control_icp_concurrency: int = 6,
        control_scan_concurrency: int = 1,
        company_core_concurrency: int = DEFAULT_COMPANY_SCAN_CONCURRENCY,
        requested_by: str = "",
    ) -> dict[str, Any]:
        """
        运行综合扫描流水线

        阶段:
        1. CompanyRouter 分析公司 → 生成搜索策略
        2. 并行执行: URL扫描 + 招投标 + XHS搜索 + 公众号 + 学者联系 + 全资子公司查询
        3. 逐个采集全资子公司（子公司 XHS 默认关闭）
        4. 根 Target 的 XHS画像 → 话术生成
        """
        from core.observability import obs_log
        from api.services.company_scan_runtime import get_company_scan_resource_pool
        from api.services.xhs_target_selection import parse_manual_targets

        subsidiary_xhs_enabled = bool(enable_xhs and enable_subsidiary_xhs)
        manual_xhs_targets = parse_manual_targets(xhs_manual_targets)
        result = {
            "task_id": task_id,
            "company_name": company_name,
            "status": "running",
            "identity": {},
            "router_result": None,
            "control_structure": {
                "enabled": enable_control_structure,
                "status": "pending" if enable_control_structure else "disabled",
                "relation_type": "wholly_owned_direct_investment",
                "relation_depth": 1,
                "ownership_percent": 100.0,
                "entities": [],
                "errors": [],
            },
            "assets": {
                "enabled": enable_asset_discovery,
                "discovered": 0,
                "alive": 0,
                "inserted": 0,
                "updated": 0,
                "scan_mode": "incremental" if incremental_scan else "full",
                "scan_candidates": 0,
                "providers": {},
            },
            "url_scan": {"enabled": enable_url_scan, "findings_count": 0, "copywritings_count": 0},
            "xhs": {
                "enabled": enable_xhs,
                "subsidiaries_enabled": subsidiary_xhs_enabled,
                "keywords_used": [],
                "notes_count": 0,
                "profiles_count": 0,
                "root_selected": False,
                "selection": {
                    "mode": str(xhs_target_selection_mode or "auto"),
                    "status": "pending" if enable_xhs else "disabled",
                    "prompt_slug": None,
                    "manual_targets": manual_xhs_targets,
                    "matched_manual_targets": [],
                    "unmatched_manual_targets": manual_xhs_targets,
                    "decisions": [],
                    "selected_count": 0,
                    "skipped_count": 0,
                    "error": None,
                },
            },
            "bidding": {
                "enabled": enable_bidding,
                "status": "pending" if enable_bidding else "disabled",
                "query_name": "",
                "records_fetched": 0,
                "total_reported": 0,
                "attachments_archived": 0,
                "findings_count": 0,
                "copywritings_count": 0,
            },
            "wechat": {
                "enabled": enable_wechat,
                "status": "pending" if enable_wechat else "disabled",
                "selected": False,
                "priority": None,
                "device_id": wechat_device_id,
                "task_def_id": "",
                "total": 0,
                "new": 0,
                "changed": 0,
                "contacts": 0,
                "documents": 0,
                "high_score_records": 0,
                "high_score_documents": 0,
                "max_score": 0,
                "keywords_used": [],
                "selection": {
                    "mode": str(wechat_target_selection_mode or "auto"),
                    "status": "pending" if enable_wechat else "disabled",
                    "prompt_slug": None,
                    "decisions": [],
                    "selected_count": 0,
                    "skipped_count": 0,
                    "error": None,
                },
            },
            "scholar": {
                "enabled": enable_scholar,
                "status": "pending" if enable_scholar else "disabled",
                "unit": "",
                "direction": str(scholar_direction or "").strip(),
                "direction_source": "manual" if str(scholar_direction or "").strip() else "pending",
                "direction_terms": [],
                "articles_total": 0,
                "verified_articles_total": 0,
                "unverified_articles_total": 0,
                "contacts_total": 0,
                "corresponding_count": 0,
            },
            "profile_copywritings": {"count": 0},
            "sub_errors": [],
            "error": None,
        }
        obs_log(
            "综合公司扫描流水线开始", task_id=task_id, project_id=project_id,
            source="company_scan_pipeline", level="notice", event="pipeline_start",
            data={"company_name": company_name},
        )

        core_lease = get_company_scan_resource_pool(
            company_core_concurrency
        ).lease(task_id=task_id)
        mobile_task: asyncio.Task[list[Any]] | None = None
        mobile_started = asyncio.Event()
        try:
            await self._update_progress(
                task_id,
                "waiting_core",
                "等待公司扫描网络与 AI 并发资源...",
            )
            await core_lease.acquire()
            # ── 阶段 1: 公司标准化与场景路由并发执行 ──
            logger.info(f"[company_scan] task={task_id} 阶段1: 识别公司 '{company_name}'")
            await self._update_progress(task_id, "routing", "识别法定主体、根域名和搜索别名...")
            from api.dao import company_meta as company_meta_dao
            from api.services.company_normalize import normalize_company
            from api.services.targets import attach_normalized_company
            from Sere1nGraph.graph.company_router.router import CompanyRouterResult

            normalized_result, router_result = await asyncio.gather(
                asyncio.wait_for(
                    normalize_company(
                        self.db,
                        self.app_config,
                        project_id=project_id,
                        input_name=company_name,
                        task_id=task_id,
                    ),
                    timeout=COMPANY_NORMALIZE_TIMEOUT_SECONDS,
                ),
                asyncio.wait_for(
                    self._run_company_router(
                        company_name,
                        project_id=project_id,
                        task_id=task_id,
                    ),
                    timeout=COMPANY_ROUTER_TIMEOUT_SECONDS,
                ),
                return_exceptions=True,
            )
            normalization_error = ""
            if isinstance(normalized_result, Exception):
                normalization_error = str(normalized_result) or "公司规范化执行超时"
                logger.warning("[company_scan] 公司规范化失败，降级使用路由结果: %s", normalized_result)
                company_meta: dict[str, Any] = {
                    "normalized_name": company_name,
                    "root_domain": "",
                    "aliases": [company_name],
                    "source": "fallback",
                    "confidence": None,
                }
            else:
                company_meta = normalized_result
            if isinstance(router_result, Exception):
                router_output = CompanyRouterResult(success=False, error=str(router_result))
            else:
                router_output = router_result

            router_profile = router_output.company_profile if router_output.success else None
            router_legal_name = str(getattr(router_profile, "icp_name", "") or "").strip()
            normalized_name = str(company_meta.get("normalized_name") or company_name).strip()
            if normalized_name == company_name and router_legal_name:
                normalized_name = router_legal_name
            aliases = self._dedupe_text(
                [
                    company_name,
                    normalized_name,
                    *[str(item) for item in company_meta.get("aliases") or []],
                    *list(getattr(router_profile, "colloquial_names", []) or []),
                    router_legal_name,
                ]
            )[:20]
            root_domain = str(company_meta.get("root_domain") or "").strip()
            root_domains = self._dedupe_text(
                [root_domain, *list(company_meta.get("icp_domains") or [])]
            )[:6]
            provenance = dict(company_meta.get("provenance") or {})
            normalization_error = normalization_error or str(
                provenance.get("browser_error") or ""
            )
            target = await attach_normalized_company(
                self.db,
                project_id=project_id,
                input_name=company_name,
                normalized_name=normalized_name,
                root_domain=root_domain,
                root_domains=root_domains,
                aliases=aliases,
                task_id=task_id,
                normalization_version=int(
                    provenance.get("normalization_version") or 0
                )
                or None,
            )
            target_id = str(target.get("target_id") or "")
            company_meta = await company_meta_dao.upsert_company_meta(
                self.db,
                project_id=project_id,
                input_name=company_name,
                normalized_name=normalized_name,
                root_domain=root_domain,
                aliases=aliases,
                confidence=company_meta.get("confidence"),
                source=str(company_meta.get("source") or "company_scan"),
                task_id=task_id,
                target_id=target_id,
                icp_domains=root_domains,
                provenance=provenance or None,
            )
            result["identity"] = {
                "input_name": company_name,
                "normalized_name": normalized_name,
                "root_domain": root_domain,
                "root_domains": root_domains,
                "aliases": aliases,
                "target_id": target_id,
                "normalization_error": normalization_error or None,
            }
            result["router_result"] = {
                "success": router_output.success,
                "enabled_nodes": router_output.enabled_nodes,
                "keywords": router_output.all_keywords,
            }

            def _profile_value(field: str, default: Any = None) -> Any:
                value = (
                    getattr(router_profile, field, default)
                    if router_profile
                    else default
                )
                return getattr(value, "value", value)

            selection_context = {
                "industry": _profile_value("industry", "other"),
                "sub_industries": _profile_value("sub_industries", []),
                "business_nature": _profile_value("business_nature", "mixed"),
                "main_business": _profile_value("main_business", []),
                "tags": _profile_value("tags", []),
                "scale": _profile_value("scale", "unknown"),
                "is_listed": bool(_profile_value("is_listed", False)),
            }

            xhs_selector: Any = None
            xhs_selection_result: Any = None
            root_xhs_enabled = False
            if enable_xhs:
                from api.services.xhs_target_selection import (
                    XhsTargetCandidate,
                    XhsTargetSelectionService,
                )

                xhs_selector = XhsTargetSelectionService(
                    self.app_config,
                    mode=xhs_target_selection_mode,
                    manual_targets=manual_xhs_targets,
                )
                xhs_selection_result = await xhs_selector.select(
                    [
                        XhsTargetCandidate(
                            target_id=target_id,
                            target_name=normalized_name,
                            aliases=aliases,
                            root_domain=root_domain,
                            context=selection_context,
                        )
                    ],
                    project_id=project_id,
                    task_id=task_id,
                )
                result["xhs"]["selection"] = xhs_selection_result.model_dump(mode="json")
                root_decision = next(iter(xhs_selection_result.decisions), None)
                root_xhs_enabled = bool(
                    root_decision and root_decision.should_collect_xhs
                )
                result["xhs"]["root_selected"] = root_xhs_enabled
                logger.info(
                    "[company_scan] task=%s XHS 根目标选择 mode=%s selected=%s reason=%s",
                    task_id,
                    xhs_selector.mode,
                    root_xhs_enabled,
                    root_decision.reason if root_decision else "无判定结果",
                )

            root_wechat_enabled = False
            if enable_wechat:
                from api.services.wechat_target_selection import (
                    WechatTargetCandidate,
                    WechatTargetSelectionService,
                )

                wechat_selection = await WechatTargetSelectionService(
                    self.app_config,
                    mode=wechat_target_selection_mode,
                ).select(
                    [
                        WechatTargetCandidate(
                            target_id=target_id,
                            target_name=normalized_name,
                            aliases=aliases,
                            root_domain=root_domain,
                            context=selection_context,
                        )
                    ],
                    project_id=project_id,
                    task_id=task_id,
                )
                result["wechat"]["selection"] = wechat_selection.model_dump(
                    mode="json"
                )
                wechat_decision = next(iter(wechat_selection.decisions), None)
                root_wechat_enabled = bool(
                    wechat_decision
                    and wechat_decision.should_collect_wechat
                )
                result["wechat"]["selected"] = root_wechat_enabled
                result["wechat"]["priority"] = (
                    wechat_decision.collection_priority
                    if wechat_decision
                    else "skip"
                )
                if not root_wechat_enabled:
                    result["wechat"]["status"] = "skipped"
                logger.info(
                    "[company_scan] task=%s 公众号根目标选择 mode=%s selected=%s priority=%s reason=%s",
                    task_id,
                    wechat_selection.mode,
                    root_wechat_enabled,
                    result["wechat"]["priority"],
                    wechat_decision.reason if wechat_decision else "无判定结果",
                )

            scholar_resolution: Any = None
            if enable_scholar:
                from api.services.scholar_direction import (
                    resolve_scholar_direction,
                )

                scholar_resolution = resolve_scholar_direction(
                    scholar_direction,
                    router_output,
                    names=aliases,
                )
                result["scholar"].update(
                    direction=scholar_resolution.direction,
                    direction_source=scholar_resolution.source,
                    direction_terms=scholar_resolution.terms,
                )

            from api.dao import targets as targets_dao
            from api.services.search_terms import build_target_channel_terms

            channel_terms = build_target_channel_terms(
                names=aliases,
                routed_terms_by_channel=router_output.all_keywords if router_output.success else {},
            )
            await targets_dao.link_project_target(
                self.db,
                project_id=project_id,
                target=target,
                search_terms=aliases,
                search_terms_by_channel=channel_terms,
                task_def_id=task_id,
            )

            if not router_output.success:
                logger.warning(f"[company_scan] 公司路由失败: {router_output.error}，使用默认策略")

            # ── 阶段 2: 各根 Target 数据源并发执行 ──
            primary_jobs: list[tuple[str, Any]] = []
            xhs_succeeded = False
            if enable_control_structure:
                primary_jobs.append((
                    "control_structure",
                    self._run_wholly_owned_investments(
                        task_id=task_id,
                        project_id=project_id,
                        parent_target=target,
                        company_name=normalized_name,
                        max_entities=control_max_entities,
                        page_concurrency=control_lookup_concurrency,
                        icp_concurrency=control_icp_concurrency,
                    ),
                ))
            if enable_asset_discovery or (enable_url_scan and (url_text or urls)):
                primary_jobs.append((
                    "asset_url",
                    self._run_asset_and_url_scan(
                        task_id=task_id,
                        project_id=project_id,
                        identity=result["identity"],
                        url_text=url_text,
                        urls=urls or [],
                        enable_asset_discovery=enable_asset_discovery,
                        enable_url_scan=enable_url_scan,
                        enable_copywriting=enable_copywriting,
                        min_attention_score=min_attention_score,
                        fofa_size=fofa_size,
                        hunter_size=hunter_size,
                        probe_concurrency=asset_probe_concurrency,
                        incremental_scan=incremental_scan,
                        url_probe_concurrency=url_probe_concurrency,
                        url_scan_concurrency=url_scan_concurrency,
                        copywriting_concurrency=copywriting_concurrency,
                    ),
                ))

            if root_xhs_enabled:
                xhs_keywords = self._get_xhs_keywords(aliases, router_output)
                result["xhs"]["keywords_used"] = xhs_keywords
                primary_jobs.append((
                    "xhs",
                    self._run_xhs_search(
                        task_id,
                        project_id,
                        xhs_keywords,
                        xhs_max_notes,
                        xhs_attention_threshold,
                        target_id=target_id,
                        target_name=normalized_name,
                        search_concurrency=xhs_search_concurrency,
                    ),
                ))

            if enable_bidding:
                result["bidding"]["query_name"] = normalized_name
                primary_jobs.append((
                    "bidding",
                    self._run_bidding_collection(
                        task_id=task_id,
                        project_id=project_id,
                        company_name=normalized_name,
                        target_id=target_id,
                        page_size=bidding_page_size,
                        enable_visual_analysis=enable_url_scan,
                        enable_copywriting=enable_copywriting,
                        min_attention_score=min_attention_score,
                        scan_concurrency=url_scan_concurrency,
                        copywriting_concurrency=copywriting_concurrency,
                    ),
                ))

            if root_wechat_enabled:
                primary_jobs.append((
                    "wechat",
                    self._run_wechat_collection(
                        task_id=task_id,
                        project_id=project_id,
                        target_id=target_id,
                        target_name=normalized_name,
                        device_id=wechat_device_id,
                        collection_priority=str(
                            result["wechat"].get("priority") or "normal"
                        ),
                        requested_by=requested_by,
                        started_event=mobile_started,
                    ),
                ))

            if enable_scholar:
                primary_jobs.append((
                    "scholar",
                    self._run_scholar_collection(
                        task_id=task_id,
                        project_id=project_id,
                        unit=normalized_name,
                        direction=scholar_resolution.direction,
                        direction_source=scholar_resolution.source,
                        unit_en=scholar_unit_en,
                        limit=scholar_limit,
                    ),
                ))

            if primary_jobs:
                logger.info(
                    "[company_scan] task=%s 阶段2: 并行执行 %s",
                    task_id,
                    ", ".join(kind for kind, _operation in primary_jobs),
                )
                await self._update_progress(
                    task_id,
                    "scanning",
                    f"并行执行 {len(primary_jobs)} 条根 Target 数据源流水线...",
                )
                mobile_jobs = [job for job in primary_jobs if job[0] == "wechat"]
                core_jobs = [job for job in primary_jobs if job[0] != "wechat"]
                mobile_task = (
                    asyncio.create_task(
                        self._gather_named_jobs(mobile_jobs),
                        name=f"company-wechat:{task_id}",
                    )
                    if mobile_jobs
                    else None
                )
                core_results = await self._gather_named_jobs(core_jobs)
                mobile_results: list[Any] = []
                if mobile_task is not None:
                    core_lease.release()
                    if not mobile_task.done():
                        await self._update_progress(
                            task_id,
                            "mobile_scanning" if mobile_started.is_set() else "waiting_mobile",
                            (
                                "公众号任务正在采集..."
                                if mobile_started.is_set()
                                else "网络与 API 扫描已让出并发资源，公众号任务进入手机队列..."
                            ),
                        )
                    mobile_results = await mobile_task
                    await self._update_progress(
                        task_id,
                        "waiting_core",
                        "公众号采集结束，等待资源汇总扫描结果...",
                    )
                    await core_lease.acquire()
                primary_jobs = [*core_jobs, *mobile_jobs]
                sub_results = [*core_results, *mobile_results]

                failed_primary_jobs: set[str] = set()
                for (kind, _operation), sub in zip(primary_jobs, sub_results):
                    if isinstance(sub, Exception):
                        failed_primary_jobs.add(kind)
                        logger.error("[company_scan] %s 子流水线失败: %s", kind, sub)
                        result["sub_errors"].append(f"{kind}: {sub}")
                        if kind == "wechat":
                            result["wechat"].update(status="error", error=str(sub))
                        elif kind == "scholar":
                            result["scholar"].update(status="error", error=str(sub))
                    elif isinstance(sub, dict):
                        if kind == "asset_url":
                            result["assets"].update(sub.get("assets") or {})
                            result["url_scan"].update(sub.get("url_scan") or {})
                        elif kind == "control_structure":
                            result["control_structure"].update(sub.get("result") or {})
                        elif kind == "bidding":
                            visual = sub.get("visual_analysis") or {}
                            bidding_failed = sub.get("status") == "error"
                            bidding_partial = bool(
                                visual.get("status") == "error"
                                or sub.get("archive_error_count")
                            )
                            result["bidding"].update(
                                sub,
                                status=(
                                    "error"
                                    if bidding_failed
                                    else "partial"
                                    if bidding_partial
                                    else "completed"
                                ),
                                findings_count=visual.get("findings_count", 0),
                                copywritings_count=visual.get("copywritings_count", 0),
                            )
                            if bidding_failed and sub.get("error"):
                                failed_primary_jobs.add(kind)
                                result["sub_errors"].append(str(sub["error"]))
                        elif kind == "xhs":
                            result["xhs"].update(sub)
                            xhs_succeeded = True
                        elif kind == "wechat":
                            result["wechat"].update(sub)
                            if sub.get("status") == "error":
                                failed_primary_jobs.add(kind)
                        elif kind == "scholar":
                            result["scholar"].update(sub)
                            if sub.get("status") == "error":
                                failed_primary_jobs.add(kind)
                if result["wechat"].get("status") == "error":
                    raise RuntimeError(
                        "公众号采集失败: "
                        + str(result["wechat"].get("error") or "未知错误")
                    )
                if len(failed_primary_jobs) == len(primary_jobs):
                    raise RuntimeError("所有公司扫描子流水线均失败: " + "; ".join(result["sub_errors"]))

            wholly_owned_entities = list(result["control_structure"].get("entities") or [])
            child_xhs_decisions: dict[str, dict[str, Any]] = {}
            selected_child_xhs = False
            if wholly_owned_entities and subsidiary_xhs_enabled and xhs_selector:
                from api.services.xhs_target_selection import (
                    XhsTargetCandidate,
                    merge_xhs_target_selection_results,
                )

                child_selection = await xhs_selector.select(
                    [
                        XhsTargetCandidate(
                            target_id=str(entity.get("target_id") or ""),
                            target_name=str(entity.get("name") or "").strip(),
                            aliases=self._dedupe_text(
                                [
                                    str(entity.get("name") or ""),
                                    *[str(item) for item in entity.get("aliases") or []],
                                ]
                            ),
                            root_domain=str(entity.get("root_domain") or ""),
                            context={
                                "registration_status": str(
                                    entity.get("registration_status") or ""
                                ),
                                "icp_domains": list(entity.get("icp_domains") or []),
                                "relation_type": "wholly_owned_direct_investment",
                                "parent_target_name": normalized_name,
                            },
                        )
                        for entity in wholly_owned_entities
                        if str(entity.get("target_id") or "")
                        and str(entity.get("name") or "").strip()
                    ],
                    project_id=project_id,
                    task_id=task_id,
                )
                xhs_selection_result = merge_xhs_target_selection_results(
                    xhs_selection_result,
                    child_selection,
                )
                result["xhs"]["selection"] = xhs_selection_result.model_dump(mode="json")
                child_xhs_decisions = {
                    item.target_id: item.model_dump(mode="json")
                    for item in child_selection.decisions
                }
                selected_child_xhs = any(
                    item.should_collect_xhs for item in child_selection.decisions
                )

            followups: list[tuple[str, Any]] = []
            if wholly_owned_entities and (enable_asset_discovery or selected_child_xhs):
                followups.append((
                    "wholly_owned_entities",
                    self._scan_wholly_owned_entities(
                        task_id=task_id,
                        project_id=project_id,
                        entities=wholly_owned_entities,
                        enable_asset_discovery=enable_asset_discovery,
                        enable_url_scan=enable_url_scan,
                        enable_copywriting=enable_copywriting,
                        enable_xhs=subsidiary_xhs_enabled,
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
                        entity_concurrency=control_scan_concurrency,
                        xhs_decisions=child_xhs_decisions,
                    ),
                ))
            if followups:
                await self._update_progress(
                    task_id,
                    "followup_collection",
                    "采集全资子公司...",
                )
                followup_results = await self._gather_named_jobs(followups)
                for (kind, _operation), outcome in zip(followups, followup_results):
                    if isinstance(outcome, Exception):
                        error_message = f"{kind}: {outcome}"
                        result["sub_errors"].append(error_message)
                        raise outcome
                    if kind == "wholly_owned_entities":
                        result["control_structure"]["entities"] = outcome["entities"]
                        result["control_structure"]["scan_summary"] = outcome["summary"]
                        result["control_structure"]["errors"].extend(outcome["errors"])
                        result["profile_copywritings"]["count"] = int(
                            outcome["summary"].get("profile_copywritings") or 0
                        )

            # ── 阶段 3: 画像→话术 ──
            if root_xhs_enabled and enable_copywriting and xhs_succeeded:
                logger.info(f"[company_scan] task={task_id} 阶段3: 画像话术生成")
                await self._update_progress(task_id, "profile_copywriting", "为高分画像生成话术...")

                cw_count = await self._run_profile_copywriting(
                    task_id, project_id, normalized_name,
                    router_output, profile_copywriting_threshold,
                    target_id=target_id,
                )
                result["profile_copywritings"]["count"] += cw_count

            # ── 保存综合结果 ──
            result["status"] = "completed"
            await self.db[COMPANY_SCAN_COLLECTION].update_one(
                {"task_id": task_id},
                {"$set": {
                    "task_id": task_id,
                    "project_id": project_id,
                    "company_name": company_name,
                    "result": result,
                    "updated_at": datetime.now(timezone.utc),
                }},
                upsert=True,
            )
            if target_id:
                await targets_dao.touch_project_target_collection(
                    self.db,
                    project_id=project_id,
                    target_id=target_id,
                    run_task_id=task_id,
                )
            if not batch_id:
                from api.services.notifications import notify_target_collection_completed

                enabled_modules = []
                if enable_asset_discovery or enable_url_scan:
                    enabled_modules.append("网站")
                if enable_bidding:
                    enabled_modules.append("招投标")
                if enable_wechat:
                    enabled_modules.append("公众号")
                if enable_scholar:
                    enabled_modules.append("学者")
                if enable_xhs:
                    enabled_modules.append("小红书")
                notify_target_collection_completed(
                    project_id=project_id,
                    task_id=task_id,
                    target_id=target_id,
                    target_name=normalized_name,
                    source="company_scan_pipeline",
                    summary={
                        "enabled_modules": enabled_modules,
                        "assets_alive": result["assets"].get("alive", 0),
                        "url_findings": result["url_scan"].get("findings_count", 0),
                        "xhs_notes": result["xhs"].get("notes_count", 0),
                        "xhs_profiles": result["xhs"].get("profiles_count", 0),
                        "wechat_documents": result["wechat"].get("documents", 0),
                        "wechat_high_score_records": result["wechat"].get(
                            "high_score_records", 0
                        ),
                        "wechat_max_score": result["wechat"].get("max_score", 0),
                        "wechat_contacts": result["wechat"].get("contacts", 0),
                        "bidding_records": result["bidding"].get("records_fetched", 0),
                        "bidding_findings": result["bidding"].get("findings_count", 0),
                        "scholar_articles": result["scholar"].get("articles_total", 0),
                        "scholar_verified_articles": result["scholar"].get(
                            "verified_articles_total", 0
                        ),
                        "scholar_contacts": result["scholar"].get("contacts_total", 0),
                    },
                )
            await self._update_progress(task_id, "completed", "综合公司扫描完成")
            obs_log(
                "综合公司扫描流水线完成", task_id=task_id, project_id=project_id,
                source="company_scan_pipeline", level="notice", event="pipeline_done",
                data={
                    "url_findings": result["url_scan"].get("findings_count", 0),
                    "assets": result["assets"].get("discovered", 0),
                    "alive_assets": result["assets"].get("alive", 0),
                    "xhs_notes": result["xhs"].get("notes_count", 0),
                    "xhs_profiles": result["xhs"].get("profiles_count", 0),
                    "wechat_records": result["wechat"].get("total", 0),
                    "wechat_documents": result["wechat"].get("documents", 0),
                    "bidding_records": result["bidding"].get("records_fetched", 0),
                    "bidding_findings": result["bidding"].get("findings_count", 0),
                    "scholar_articles": result["scholar"].get("articles_total", 0),
                    "scholar_contacts": result["scholar"].get("contacts_total", 0),
                    "profile_copywritings": result["profile_copywritings"].get("count", 0),
                    "xhs_targets_selected": result["xhs"]["selection"].get(
                        "selected_count", 0
                    ),
                    "xhs_targets_skipped": result["xhs"]["selection"].get(
                        "skipped_count", 0
                    ),
                },
            )

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            logger.error(f"[company_scan] task={task_id} 流水线异常: {e}")
            await self.db[COMPANY_SCAN_COLLECTION].update_one(
                {"task_id": task_id},
                {
                    "$set": {
                        "task_id": task_id,
                        "project_id": project_id,
                        "company_name": company_name,
                        "result": result,
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
                upsert=True,
            )
            await self._update_progress(task_id, "error", f"综合公司扫描失败: {e}")
            obs_log(
                f"综合公司扫描流水线失败: {e}", task_id=task_id, project_id=project_id,
                source="company_scan_pipeline", level="error", event="pipeline_error",
                data={"error": str(e)},
            )
            if not batch_id:
                from api.services.notifications import notify_target_collection_completed

                failed_identity = result.get("identity") or {}
                notify_target_collection_completed(
                    project_id=project_id,
                    task_id=task_id,
                    target_id=str(failed_identity.get("target_id") or ""),
                    target_name=str(
                        failed_identity.get("normalized_name") or company_name
                    ),
                    source="company_scan_pipeline",
                    summary={"error": str(e)},
                    status="failed",
                )
            raise
        finally:
            core_lease.release()
            if mobile_task is not None:
                if not mobile_task.done():
                    mobile_task.cancel()
                await asyncio.gather(mobile_task, return_exceptions=True)

        return result

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
        collection_priority: str = "normal",
        requested_by: str = "",
        started_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        from api.services.wechat_collection import run_company_wechat_collection

        async def _on_started() -> None:
            if started_event is not None:
                started_event.set()
            await self._update_progress(
                task_id,
                "mobile_scanning",
                "公众号任务已获得手机，正在采集...",
            )

        return await run_company_wechat_collection(
            self.db,
            task_id=task_id,
            project_id=project_id,
            target_id=target_id,
            target_name=target_name,
            device_id=device_id,
            collection_priority=collection_priority,
            requested_by=requested_by,
            on_started=_on_started,
        )

    async def _run_scholar_collection(
        self,
        *,
        task_id: str,
        project_id: str,
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
                page_size=max(1, min(int(page_size), 20)),
                enable_visual_analysis=enable_visual_analysis,
                enable_copywriting=enable_copywriting,
                min_attention_score=min_attention_score,
                scan_concurrency=max(1, min(int(scan_concurrency), 3)),
                copywriting_concurrency=max(1, min(int(copywriting_concurrency), 2)),
            )
        except Exception as exc:  # noqa: BLE001
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
        xhs_decisions: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """按全资子公司限流并发，单位内部继续并行资产与社媒流水线。"""
        from api.services.search_terms import build_channel_terms

        semaphore = asyncio.Semaphore(max(1, entity_concurrency))

        async def _scan(index: int, entity: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                name = str(entity.get("name") or "").strip()
                target_id = str(entity.get("target_id") or "")
                aliases = self._dedupe_text(
                    [name, *list(entity.get("aliases") or [])]
                )[:20]
                identity = {
                    "input_name": name,
                    "normalized_name": name,
                    "root_domain": str(entity.get("root_domain") or ""),
                    "root_domains": list(entity.get("icp_domains") or []),
                    "aliases": aliases,
                    "target_id": target_id,
                }
                xhs_decision = (xhs_decisions or {}).get(target_id)
                entity_xhs_enabled = bool(
                    enable_xhs
                    and (
                        xhs_decisions is None
                        or (
                            xhs_decision
                            and xhs_decision.get("should_collect_xhs")
                        )
                    )
                )
                subtasks: list[Any] = []
                if enable_asset_discovery:
                    subtasks.append(
                        self._run_asset_and_url_scan(
                            task_id=f"{task_id}_wholly_owned_{index}",
                            project_id=project_id,
                            identity=identity,
                            url_text="",
                            urls=[],
                            enable_asset_discovery=True,
                            enable_url_scan=enable_url_scan,
                            enable_copywriting=enable_copywriting,
                            min_attention_score=min_attention_score,
                            fofa_size=fofa_size,
                            hunter_size=hunter_size,
                            probe_concurrency=asset_probe_concurrency,
                            incremental_scan=incremental_scan,
                            url_probe_concurrency=url_probe_concurrency,
                            url_scan_concurrency=url_scan_concurrency,
                            copywriting_concurrency=copywriting_concurrency,
                        )
                    )
                xhs_keywords: list[str] = []
                if entity_xhs_enabled:
                    xhs_keywords = build_channel_terms(
                        channel="xhs",
                        names=aliases,
                        limit=20,
                    )
                    subtasks.append(
                        self._run_xhs_search(
                            f"{task_id}_wholly_owned_{index}",
                            project_id,
                            xhs_keywords,
                            xhs_max_notes,
                            xhs_attention_threshold,
                            target_id=target_id,
                            target_name=name,
                            search_concurrency=xhs_search_concurrency,
                        )
                    )
                scan_result: dict[str, Any] = {
                    "assets": {},
                    "url_scan": {},
                    "xhs": {"enabled": entity_xhs_enabled, "keywords_used": xhs_keywords},
                    "profile_copywritings": {"count": 0},
                    "errors": [],
                }
                if xhs_decision:
                    scan_result["xhs"]["selection"] = xhs_decision
                xhs_succeeded = False
                for sub in await asyncio.gather(*subtasks, return_exceptions=True):
                    if isinstance(sub, Exception):
                        scan_result["errors"].append(str(sub))
                    elif sub.get("kind") == "asset_url":
                        scan_result["assets"] = sub.get("assets") or {}
                        scan_result["url_scan"] = sub.get("url_scan") or {}
                    elif "notes_count" in sub:
                        scan_result["xhs"].update(sub)
                        xhs_succeeded = True
                if entity_xhs_enabled and enable_copywriting and xhs_succeeded:
                    from Sere1nGraph.graph.company_router.router import CompanyRouterResult

                    try:
                        scan_result["profile_copywritings"]["count"] = (
                            await self._run_profile_copywriting(
                                f"{task_id}_wholly_owned_{index}",
                                project_id,
                                name,
                                CompanyRouterResult(success=False),
                                profile_copywriting_threshold,
                                target_id=target_id,
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        scan_result["errors"].append(f"画像话术生成失败: {exc}")
                return {**entity, "scan": scan_result}

        scanned = await asyncio.gather(
            *[_scan(index, entity) for index, entity in enumerate(entities)],
            return_exceptions=True,
        )
        output_entities: list[dict[str, Any]] = []
        errors: list[str] = []
        summary = {
            "entities": len(entities),
            "completed": 0,
            "assets_discovered": 0,
            "assets_alive": 0,
            "url_findings": 0,
            "xhs_notes": 0,
            "xhs_profiles": 0,
            "profile_copywritings": 0,
        }
        for index, item in enumerate(scanned):
            if isinstance(item, Exception):
                entity = entities[index]
                entity_name = str(entity.get("name") or index)
                error_message = str(item)
                errors.append(f"{entity_name}: {error_message}")
                output_entities.append({**entity, "scan": {"errors": [error_message]}})
                continue
            output_entities.append(item)
            scan = item.get("scan") or {}
            assets = scan.get("assets") or {}
            url_scan = scan.get("url_scan") or {}
            xhs = scan.get("xhs") or {}
            profile_copywritings = scan.get("profile_copywritings") or {}
            summary["completed"] += int(not scan.get("errors"))
            summary["assets_discovered"] += int(assets.get("discovered") or 0)
            summary["assets_alive"] += int(assets.get("alive") or 0)
            summary["url_findings"] += int(url_scan.get("findings_count") or 0)
            summary["xhs_notes"] += int(xhs.get("notes_count") or 0)
            summary["xhs_profiles"] += int(xhs.get("profiles_count") or 0)
            summary["profile_copywritings"] += int(profile_copywritings.get("count") or 0)
            errors.extend(
                f"{item.get('name')}: {message}" for message in scan.get("errors") or []
            )
        return {"entities": output_entities, "summary": summary, "errors": errors}

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
    ) -> dict[str, Any]:
        from api.services.asset_intelligence import AssetIdentity, AssetIntelligenceService

        asset_result: dict[str, Any] = {
            "enabled": enable_asset_discovery,
            "discovered": 0,
            "alive": 0,
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "scan_mode": "incremental" if incremental_scan else "full",
            "scan_candidates": 0,
            "providers": {},
        }
        discovered_urls: list[str] = []
        if enable_asset_discovery:
            asset_result = await AssetIntelligenceService(
                self.db,
                app_config=self.app_config,
            ).discover(
                identity=AssetIdentity(
                    input_name=str(identity.get("input_name") or ""),
                    normalized_name=str(identity.get("normalized_name") or ""),
                    root_domain=str(identity.get("root_domain") or ""),
                    target_id=str(identity.get("target_id") or ""),
                    aliases=list(identity.get("aliases") or []),
                    root_domains=list(identity.get("root_domains") or []),
                ),
                project_id=project_id,
                task_id=task_id,
                provider_sizes={"fofa": fofa_size, "hunter": hunter_size},
                probe_concurrency=probe_concurrency,
            )
            candidate_key = "scan_urls" if incremental_scan else "alive_urls"
            discovered_urls = [
                str(value)
                for value in asset_result.get(candidate_key) or []
                if str(value).strip()
            ]
            asset_result["scan_mode"] = "incremental" if incremental_scan else "full"
            asset_result["scan_candidates"] = len(discovered_urls)

        url_result: dict[str, Any] = {
            "enabled": enable_url_scan,
            "findings_count": 0,
            "copywritings_count": 0,
        }
        if enable_url_scan:
            root_domains = self._dedupe_text(
                [
                    str(identity.get("root_domain") or ""),
                    *list(identity.get("root_domains") or []),
                ]
            )[:6]
            root_urls = [normalize_url(domain) for domain in root_domains if domain]
            merged_urls = self._dedupe_text([*root_urls, *urls, *discovered_urls])
            if merged_urls or url_text.strip():
                url_result.update(
                    await self._run_url_scan(
                        task_id,
                        project_id,
                        url_text,
                        merged_urls,
                        min_attention_score,
                        enable_copywriting,
                        target_id=str(identity.get("target_id") or ""),
                        known_alive_urls=discovered_urls,
                        probe_concurrency=url_probe_concurrency,
                        scan_concurrency=url_scan_concurrency,
                        copywriting_concurrency=copywriting_concurrency,
                    )
                )
        return {"kind": "asset_url", "assets": asset_result, "url_scan": url_result}

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
        probe_concurrency: int = DEFAULT_URL_PROBE_CONCURRENCY,
        scan_concurrency: int = DEFAULT_URL_SCAN_CONCURRENCY,
        copywriting_concurrency: int = DEFAULT_COPYWRITING_CONCURRENCY,
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
            probe_concurrency=probe_concurrency,
            scan_concurrency=scan_concurrency,
            copywriting_concurrency=copywriting_concurrency,
        )
        if scan_result.get("status") == "error":
            raise RuntimeError(str(scan_result.get("error") or "URL 深度扫描失败"))

        return {
            "findings_count": scan_result.get("total_findings", 0),
            "copywritings_count": scan_result.get("total_copywritings", 0),
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
        """
        流式队列架构的 XHS 搜索流水线

        搜索（受账号池容量约束）→ 笔记入库 → 打标队列 → 详情队列 → 画像
        """
        import time as _time
        from api.services.info_collection import ProfileRequest
        from api.services.info_collection.factory import InfoCollectionToolFactory
        from api.services.info_collection.streaming import make_stream_items, run_stream_pipeline, stream_stage
        from api.services.xhs_pipeline import XhsPipeline

        t0 = _time.time()
        per_keyword = max(1, min(40, (max_notes + max(1, len(keywords)) - 1) // max(1, len(keywords))))
        pipeline = XhsPipeline(self.db, self.app_config)
        toolset = await InfoCollectionToolFactory(
            db=self.db,
            app_config=self.app_config,
        ).create_xhs_toolset(pipeline)

        # ── 三阶段流式管道 (search → tagging → detail) ──
        logger.info(f"[xhs-stream] ▶ 流式流水线启动 | keywords={len(keywords)} per_keyword={per_keyword}")

        from api.services.xhs_runtime import resolve_xhs_search_concurrency

        effective_search_concurrency = await resolve_xhs_search_concurrency(
            self.db,
            requested=search_concurrency,
            workload_size=len(keywords),
        )
        search_stage = _XhsSearchStage(
            concurrency=effective_search_concurrency, project_id=project_id, task_id=task_id,
            per_keyword=per_keyword, db=self.db, pipeline_owner=pipeline,
            target_id=target_id, target_name=target_name,
        )
        tagging_stage = _XhsTaggingStage(
            concurrency=8, attention_threshold=attention_threshold,
            db=self.db, pipeline_owner=pipeline,
        )
        detail_stage = _XhsDetailStage(
            concurrency=1, project_id=project_id, db=self.db, pipeline_owner=pipeline,
        )

        try:
            pipe = await run_stream_pipeline(
                stages=[
                    stream_stage(search_stage, downstream=["tagging"]),
                    stream_stage(tagging_stage, downstream=["detail"]),
                    stream_stage(detail_stage),
                ],
                seeds=make_stream_items(keywords, indexed=True),
                entry="search",
                state={
                    "db": self.db,
                    **toolset.state(),
                },
            )
        except Exception:
            await toolset.close()
            raise

        all_notes_count = pipe.state.get("all_notes_count", 0)
        all_suspicious_count = pipe.state.get("all_suspicious_count", 0)
        all_profiles_count = 0
        logger.info(
            f"[xhs-stream] 流式三阶段完成 | notes={all_notes_count} suspicious={all_suspicious_count}"
        )

        # 画像阶段按目标聚合执行一次。此前按关键词并发会重复查询同一批 finding，
        # 造成重复截图、重复 Agent 调用和无意义的 Token 消耗。
        logger.info("[xhs-stream] 阶段D: 目标画像生成（单批次）")
        try:
            profile_result = await toolset.profile_tool.generate_profile(
                ProfileRequest(
                    source="xhs",
                    project_id=project_id,
                    task_id=f"{task_id}_xhs_profile",
                    keyword=" / ".join(keywords[:4]),
                    options={
                        "target_id": target_id,
                        "screenshot_concurrency": 1,
                        "profile_concurrency": 2,
                    },
                )
            )
            all_profiles_count = profile_result.count
            logger.info("[xhs-stream] 画像完成 profiles=%s", all_profiles_count)
        except Exception as exc:
            logger.error("[xhs-stream] 画像失败: %s", exc)
        finally:
            await toolset.close()

        elapsed = _time.time() - t0
        logger.info(
            f"[xhs-stream] ■ 流水线完成 | notes={all_notes_count} "
            f"suspicious={all_suspicious_count} | {elapsed:.1f}s"
        )

        return {
            "notes_count": all_notes_count,
            "profiles_count": all_profiles_count,
        }

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
    async def _gather_named_jobs(
        jobs: list[tuple[str, Any]],
    ) -> list[Any]:
        """Run independent source pipelines concurrently with isolated failures."""
        if not jobs:
            return []
        return list(
            await asyncio.gather(
                *(operation for _kind, operation in jobs),
                return_exceptions=True,
            )
        )

    @staticmethod
    def _dedupe_text(values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                value.strip() for value in values if isinstance(value, str) and value.strip()
            )
        )

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
        await self.db["tasks"].update_one(
            {"task_id": task_id},
            {"$set": {
                "progress.stage": stage,
                "progress.message": message,
                "updated_at": datetime.now(timezone.utc),
            }},
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
