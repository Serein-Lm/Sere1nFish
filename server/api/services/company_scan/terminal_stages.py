"""Profile, detached-mobile join, persistence and failure finalization stages."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from api.db.collections import COMPANY_SCAN_COLLECTION
from api.services.company_scan.checkpoints import CompanyScanCheckpointRepository
from api.services.company_scan.contracts import CompanyScanContext
from api.services.company_scan.policies import incomplete_collection_sources
from api.services.company_scan.result_projection import completion_observation
from core.logger import get_logger
from core.observability import obs_log


logger = get_logger("company_scan.terminal")


class ProfileCopywritingRuntimeStage:
    name = "profile_copywriting"

    def __init__(self, checkpoints: CompanyScanCheckpointRepository) -> None:
        self.checkpoints = checkpoints

    async def run(self, ctx: CompanyScanContext) -> None:
        if not self._enabled(ctx):
            return
        checkpoint = ctx.recovery.checkpoint_results.get(self.name)
        if isinstance(checkpoint, dict):
            ctx.result["profile_copywritings"]["count"] += int(
                checkpoint.get("count") or 0
            )
            return
        await ctx.owner._update_progress(
            ctx.plan.task_id,
            "waiting_core",
            "等待资源生成画像话术...",
        )
        await ctx.core_lease.acquire()
        try:
            await ctx.owner._update_progress(
                ctx.plan.task_id,
                "profile_copywriting",
                "为高分画像生成话术...",
            )
            count = await ctx.owner._run_profile_copywriting(
                ctx.plan.task_id,
                ctx.plan.project_id,
                ctx.normalized_name,
                ctx.router_output,
                ctx.plan.profile_copywriting_threshold,
                target_id=ctx.target_id,
            )
            ctx.result["profile_copywritings"]["count"] += count
            await self.checkpoints.record(
                ctx,
                self.name,
                {
                    "kind": self.name,
                    "status": "completed",
                    "count": count,
                },
            )
        finally:
            ctx.core_lease.release()

    @staticmethod
    def _enabled(ctx: CompanyScanContext) -> bool:
        if ProfileCopywritingRuntimeStage.name in ctx.recovery.checkpoint_results:
            return True
        return bool(
            ctx.root_xhs_enabled
            and ctx.plan.enable_copywriting
            and ctx.xhs_succeeded
        )


class MobileJoinStage:
    name = "mobile_join"

    async def run(self, ctx: CompanyScanContext) -> None:
        if ctx.mobile_task is not None:
            await self._join(ctx)
        if ctx.result["wechat"].get("status") == "error":
            raise RuntimeError(
                "公众号采集失败: "
                + str(ctx.result["wechat"].get("error") or "未知错误")
            )
        if (
            ctx.primary_job_count
            and len(ctx.failed_primary_jobs) == ctx.primary_job_count
        ):
            raise RuntimeError(
                "所有公司扫描子流水线均失败: "
                + "; ".join(ctx.result["sub_errors"])
            )

    async def _join(self, ctx: CompanyScanContext) -> None:
        assert ctx.mobile_task is not None
        if not ctx.mobile_task.done():
            await ctx.owner._update_progress(
                ctx.plan.task_id,
                "mobile_scanning" if ctx.mobile_started.is_set() else "waiting_mobile",
                (
                    "网站与 API 后续已完成，正在等待公众号采集..."
                    if ctx.mobile_started.is_set()
                    else "网站与 API 后续已完成，公众号任务仍在手机队列..."
                ),
            )
        outcomes = await ctx.mobile_task
        ctx.mobile_task = None
        failures, xhs_succeeded = ctx.owner._merge_primary_job_results(
            ctx.result,
            ctx.mobile_jobs,
            outcomes,
        )
        ctx.failed_primary_jobs.update(failures)
        ctx.xhs_succeeded = ctx.xhs_succeeded or xhs_succeeded
        await ctx.owner._update_progress(
            ctx.plan.task_id,
            "finalizing",
            "公众号采集结束，正在汇总已持久化结果...",
        )


class CompanyScanFinalizerStage:
    name = "finalize"

    async def run(self, ctx: CompanyScanContext) -> None:
        incomplete = incomplete_collection_sources(ctx.result)
        ctx.result["incomplete_sources"] = incomplete
        ctx.result["status"] = "partial" if incomplete else "completed"
        await persist_company_scan_result(ctx)
        if ctx.target_id:
            from api.dao import targets as targets_dao

            await targets_dao.touch_project_target_collection(
                ctx.db,
                project_id=ctx.plan.project_id,
                target_id=ctx.target_id,
                run_task_id=ctx.plan.task_id,
            )
        if not ctx.plan.batch_id:
            self._notify_completion(ctx)
        message = (
            "综合公司扫描完成"
            if ctx.result["status"] == "completed"
            else "综合公司扫描部分完成，未完整渠道：" + "、".join(incomplete)
        )
        await ctx.owner._update_progress(
            ctx.plan.task_id,
            ctx.result["status"],
            message,
        )
        obs_log(
            message,
            task_id=ctx.plan.task_id,
            project_id=ctx.plan.project_id,
            source="company_scan_pipeline",
            level="notice" if ctx.result["status"] == "completed" else "warning",
            event="pipeline_done",
            data=completion_observation(ctx.result),
        )

    @staticmethod
    def _notify_completion(ctx: CompanyScanContext) -> None:
        from api.services.notifications import notify_target_collection_completed

        notify_target_collection_completed(
            project_id=ctx.plan.project_id,
            task_id=ctx.plan.task_id,
            target_id=ctx.target_id,
            target_name=ctx.normalized_name,
            source="company_scan_pipeline",
            status=ctx.result["status"],
            summary=_notification_summary(ctx),
        )


class CompanyScanFailureHandler:
    """Persist terminal errors while preserving detached mobile work semantics."""

    async def handle(self, ctx: CompanyScanContext, error: Exception) -> None:
        from core.llm_capacity import LLMCapacityUnavailableError

        if isinstance(error, LLMCapacityUnavailableError):
            await self._handle_capacity_wait(ctx)
            raise error
        ctx.result["status"] = "error"
        ctx.result["error"] = str(error)
        logger.error("公司扫描流水线异常 | task=%s error=%s", ctx.plan.task_id, error)
        await persist_company_scan_result(ctx)
        await ctx.owner._update_progress(
            ctx.plan.task_id,
            "error",
            f"综合公司扫描失败: {error}",
        )
        obs_log(
            f"综合公司扫描流水线失败: {error}",
            task_id=ctx.plan.task_id,
            project_id=ctx.plan.project_id,
            source="company_scan_pipeline",
            level="error",
            event="pipeline_error",
            data={"error": str(error)},
        )
        if not ctx.plan.batch_id:
            self._notify_failure(ctx, error)
        raise error

    async def _handle_capacity_wait(self, ctx: CompanyScanContext) -> None:
        if ctx.mobile_task is not None:
            if not ctx.mobile_task.done():
                await ctx.owner._update_progress(
                    ctx.plan.task_id,
                    "waiting_model",
                    "模型额度暂不可用，公众号采集继续运行...",
                )
            await ctx.mobile_task
            ctx.mobile_task = None
        ctx.result.update(status="waiting_model", error=None)
        await persist_company_scan_result(ctx)
        await ctx.owner._update_progress(
            ctx.plan.task_id,
            "waiting_model",
            "模型额度暂不可用，已保留各模块检查点并等待自动恢复",
        )

    @staticmethod
    def _notify_failure(ctx: CompanyScanContext, error: Exception) -> None:
        from api.services.notifications import notify_target_collection_completed

        identity = ctx.result.get("identity") or {}
        notify_target_collection_completed(
            project_id=ctx.plan.project_id,
            task_id=ctx.plan.task_id,
            target_id=str(identity.get("target_id") or ""),
            target_name=str(
                identity.get("normalized_name") or ctx.plan.company_name
            ),
            source="company_scan_pipeline",
            summary={"error": str(error)},
            status="failed",
        )


async def persist_company_scan_result(ctx: CompanyScanContext) -> None:
    await ctx.db[COMPANY_SCAN_COLLECTION].update_one(
        {"task_id": ctx.plan.task_id},
        {
            "$set": {
                "task_id": ctx.plan.task_id,
                "project_id": ctx.plan.project_id,
                "company_name": ctx.plan.company_name,
                "result": ctx.result,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )


async def cleanup_company_scan_runtime(ctx: CompanyScanContext) -> None:
    if ctx.core_lease is not None:
        ctx.core_lease.release()
    if ctx.mobile_task is None:
        return
    if not ctx.mobile_task.done():
        ctx.mobile_task.cancel()
    await asyncio.gather(ctx.mobile_task, return_exceptions=True)
    ctx.mobile_task = None


def _notification_summary(ctx: CompanyScanContext) -> dict[str, Any]:
    plan = ctx.plan
    enabled_modules = [
        *(["网站"] if plan.enable_asset_discovery or plan.enable_url_scan else []),
        *(["招投标"] if plan.enable_bidding else []),
        *(["公众号"] if plan.enable_wechat else []),
        *(["学者"] if plan.enable_scholar else []),
        *(["小红书"] if plan.enable_xhs else []),
    ]
    result = ctx.result
    return {
        "enabled_modules": enabled_modules,
        "assets_alive": result["assets"].get("alive", 0),
        "url_findings": result["url_scan"].get("findings_count", 0),
        "xhs_notes": result["xhs"].get("notes_count", 0),
        "xhs_profiles": result["xhs"].get("profiles_count", 0),
        "wechat_documents": result["wechat"].get("documents", 0),
        "wechat_high_score_records": result["wechat"].get("high_score_records", 0),
        "wechat_max_score": result["wechat"].get("max_score", 0),
        "wechat_contacts": result["wechat"].get("contacts", 0),
        "bidding_records": result["bidding"].get("records_fetched", 0),
        "bidding_findings": result["bidding"].get("findings_count", 0),
        "scholar_articles": result["scholar"].get("articles_total", 0),
        "scholar_verified_articles": result["scholar"].get(
            "verified_articles_total", 0
        ),
        "scholar_contacts": result["scholar"].get("contacts_total", 0),
    }
