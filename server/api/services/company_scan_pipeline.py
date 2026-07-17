"""
综合公司扫描流水线（Company Scan Pipeline）

整合三条链路：
1. URL 扫描 → findings → 话术生成
2. 小红书搜索（多关键词）→ 打标 → 画像
3. 画像 → 话术生成（每个高分画像生成多套话术）

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
    DEFAULT_URL_PROBE_CONCURRENCY,
    DEFAULT_URL_SCAN_CONCURRENCY,
    DEFAULT_XHS_SEARCH_CONCURRENCY,
)
from api.services.info_collection.xhs_stages import (
    XhsDetailStage as _XhsDetailStage,
    XhsSearchStage as _XhsSearchStage,
    XhsTaggingStage as _XhsTaggingStage,
)

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
                options={"url": url},
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
        url_text: str = "",
        urls: list[str] | None = None,
        enable_url_scan: bool = True,
        enable_asset_discovery: bool = True,
        enable_xhs: bool = True,
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
    ) -> dict[str, Any]:
        """
        运行综合扫描流水线

        阶段:
        1. CompanyRouter 分析公司 → 生成搜索策略
        2. 并行执行: URL扫描 + XHS搜索
        3. XHS画像 → 话术生成
        """
        from core.observability import obs_log

        result = {
            "task_id": task_id,
            "company_name": company_name,
            "status": "running",
            "identity": {},
            "router_result": None,
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
            "xhs": {"enabled": enable_xhs, "keywords_used": [], "notes_count": 0, "profiles_count": 0},
            "profile_copywritings": {"count": 0},
            "sub_errors": [],
            "error": None,
        }
        obs_log(
            "综合公司扫描流水线开始", task_id=task_id, project_id=project_id,
            source="company_scan_pipeline", level="notice", event="pipeline_start",
            data={"company_name": company_name},
        )

        try:
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
                    self._run_company_router(company_name),
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
            )
            root_domain = str(company_meta.get("root_domain") or "").strip()
            target = await attach_normalized_company(
                self.db,
                project_id=project_id,
                input_name=company_name,
                normalized_name=normalized_name,
                root_domain=root_domain,
                aliases=aliases,
                task_id=task_id,
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
            )
            result["identity"] = {
                "input_name": company_name,
                "normalized_name": normalized_name,
                "root_domain": root_domain,
                "aliases": aliases,
                "target_id": target_id,
                "normalization_error": normalization_error or None,
            }
            result["router_result"] = {
                "success": router_output.success,
                "enabled_nodes": router_output.enabled_nodes,
                "keywords": router_output.all_keywords,
            }

            if not router_output.success:
                logger.warning(f"[company_scan] 公司路由失败: {router_output.error}，使用默认策略")

            # ── 阶段 2: 资产发现/URL 深扫与社媒采集并发执行 ──
            tasks: list[Any] = []
            if enable_asset_discovery or (enable_url_scan and (url_text or urls)):
                tasks.append(
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
                    )
                )

            if enable_xhs:
                xhs_keywords = self._get_xhs_keywords(aliases, router_output)
                result["xhs"]["keywords_used"] = xhs_keywords
                tasks.append(self._run_xhs_search(
                    task_id, project_id, xhs_keywords,
                    xhs_max_notes, xhs_attention_threshold,
                    target_id=target_id,
                    target_name=normalized_name,
                    search_concurrency=xhs_search_concurrency,
                ))

            if tasks:
                logger.info(f"[company_scan] task={task_id} 阶段2: 并行执行 {len(tasks)} 条子流水线")
                await self._update_progress(task_id, "scanning", f"并行执行 {len(tasks)} 条子流水线...")
                sub_results = await asyncio.gather(*tasks, return_exceptions=True)

                for i, sub in enumerate(sub_results):
                    if isinstance(sub, Exception):
                        logger.error(f"[company_scan] 子流水线 {i} 失败: {sub}")
                        result["sub_errors"].append(str(sub))
                    elif isinstance(sub, dict):
                        if sub.get("kind") == "asset_url":
                            result["assets"].update(sub.get("assets") or {})
                            result["url_scan"].update(sub.get("url_scan") or {})
                        elif "notes_count" in sub:
                            result["xhs"].update(sub)
                if result["sub_errors"] and len(result["sub_errors"]) == len(tasks):
                    raise RuntimeError("所有公司扫描子流水线均失败: " + "; ".join(result["sub_errors"]))

            # ── 阶段 3: 画像→话术 ──
            if enable_xhs and enable_copywriting:
                logger.info(f"[company_scan] task={task_id} 阶段3: 画像话术生成")
                await self._update_progress(task_id, "profile_copywriting", "为高分画像生成话术...")

                cw_count = await self._run_profile_copywriting(
                    task_id, project_id, normalized_name,
                    router_output, profile_copywriting_threshold,
                    target_id=target_id,
                )
                result["profile_copywritings"]["count"] = cw_count

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
                from api.dao import targets as targets_dao

                await targets_dao.touch_project_target_collection(
                    self.db,
                    project_id=project_id,
                    target_id=target_id,
                    run_task_id=task_id,
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
                    "profile_copywritings": result["profile_copywritings"].get("count", 0),
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
            raise

        return result

    # ══════════════════════════════════════
    # 阶段 1: 公司路由
    # ══════════════════════════════════════

    async def _run_company_router(self, company_name: str):
        from Sere1nGraph.graph.company_router.router import CompanyRouter
        router = CompanyRouter(self.app_config)
        return await router.route(company_name)

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
            asset_result = await AssetIntelligenceService(self.db).discover(
                identity=AssetIdentity(
                    input_name=str(identity.get("input_name") or ""),
                    normalized_name=str(identity.get("normalized_name") or ""),
                    root_domain=str(identity.get("root_domain") or ""),
                    target_id=str(identity.get("target_id") or ""),
                    aliases=list(identity.get("aliases") or []),
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
            merged_urls = self._dedupe_text([*urls, *discovered_urls])
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
            concurrency=4, project_id=project_id, db=self.db, pipeline_owner=pipeline,
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

        # ── 阶段 D: 画像生成（并发，所有关键词同时跑）──
        logger.info(f"[xhs-stream] 阶段D: 画像生成（并发）")

        async def _gen_profile(idx, keyword):
            sub_task_id = f"{task_id}_xhs_{idx}"
            try:
                profile_result = await toolset.profile_tool.generate_profile(
                    ProfileRequest(
                        source="xhs",
                        project_id=project_id,
                        task_id=sub_task_id,
                        keyword=keyword,
                        options={"target_id": target_id},
                    )
                )
                logger.info(f"[xhs-stream] 画像完成 keyword='{keyword}' profiles={profile_result.count}")
                return profile_result.count
            except Exception as e:
                logger.error(f"[xhs-stream] 画像失败 keyword='{keyword}': {e}")
                return 0

        try:
            profile_results = await asyncio.gather(
                *[_gen_profile(i, kw) for i, kw in enumerate(keywords)],
                return_exceptions=True,
            )
        finally:
            await toolset.close()
        for r in profile_results:
            if isinstance(r, int):
                all_profiles_count += r

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
        ).create_copywriting_tool(response_parser=self._parse_agent_response)
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
    def _dedupe_text(values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                value.strip() for value in values if isinstance(value, str) and value.strip()
            )
        )

    def _get_xhs_keywords(self, search_names: list[str], router_output: Any) -> list[str]:
        """组合真实品牌别名与场景词，法定名不再是唯一检索入口。"""
        routed = (
            list(router_output.all_keywords.get("xhs") or [])
            if router_output.success
            else []
        )
        scene_terms = ["实习", "内推", "招聘", "工作体验"]
        generated = [
            f"{name} {term}"
            for name in search_names[:4]
            for term in scene_terms
        ]
        return self._dedupe_text([*routed, *generated])[:20]

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
