"""Durable recovery and coverage writes for company scan stages."""
from __future__ import annotations

from typing import Any

from core.logger import get_logger
from api.services.company_scan.contracts import CompanyScanContext, CompanyScanRecovery
from api.services.company_scan.policies import should_checkpoint_module


logger = get_logger("company_scan.checkpoints")


class CompanyScanCheckpointRepository:
    """Keep stage recovery decisions and checkpoint writes out of stage logic."""

    async def prepare(self, ctx: CompanyScanContext) -> CompanyScanRecovery:
        from api.services.company_scan_recovery import (
            load_recovery_state,
        )

        plan = ctx.plan
        raw = await load_recovery_state(ctx.db, task_id=plan.task_id)
        previous_result = dict(raw.get("result") or {})
        raw_results = self._completed_results(raw)
        checkpoints = {
            module: result
            for module, result in raw_results.items()
            if should_checkpoint_module(module, result)
        }
        retryable = await self._retryable_modules(
            ctx,
            raw_results=raw_results,
            checkpoints=checkpoints,
            previous_result=previous_result,
        )
        for module in retryable:
            checkpoints.pop(module, None)

        resume = dict(raw.get("resume") or {})
        restore_core, restored_identity = await self._restore_core_identity(
            ctx,
            resume,
        )
        recovery = CompanyScanRecovery(
            checkpoint_results=checkpoints,
            retryable_core_modules=retryable,
            previous_result=previous_result,
            restored_identity=restored_identity,
            restore_core_context=restore_core,
            resume_core_completed=bool(restore_core and not retryable),
            resume_mobile_completed=self._mobile_completed(
                plan.enable_wechat,
                resume,
                checkpoints,
                raw_results,
                previous_result,
            ),
        )
        if retryable:
            logger.warning(
                "检查点需要重跑 | task=%s modules=%s",
                plan.task_id,
                ", ".join(sorted(retryable)),
            )
        ctx.recovery = recovery
        return recovery

    @staticmethod
    def _completed_results(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            str(module): dict(entry.get("result") or {})
            for module, entry in dict(raw.get("modules") or {}).items()
            if isinstance(entry, dict)
            and entry.get("status") == "completed"
            and isinstance(entry.get("result"), dict)
        }

    async def _retryable_modules(
        self,
        ctx: CompanyScanContext,
        *,
        raw_results: dict[str, dict[str, Any]],
        checkpoints: dict[str, dict[str, Any]],
        previous_result: dict[str, Any],
    ) -> set[str]:
        from api.services.company_scan_recovery import (
            find_incompatible_core_modules,
            find_retryable_core_modules,
            recovery_modules_for_incomplete_sources,
        )

        enabled = ctx.plan.enabled_core_modules
        retryable = await find_retryable_core_modules(
            ctx.db,
            task_id=ctx.plan.task_id,
        )
        retryable.update(
            module
            for module in set(raw_results).difference(checkpoints)
            if enabled.get(module)
        )
        retryable.update(
            module
            for module in recovery_modules_for_incomplete_sources(previous_result)
            if module != "wechat" and enabled.get(module)
        )
        retryable.update(find_incompatible_core_modules(checkpoints))
        return retryable

    async def _restore_core_identity(
        self,
        ctx: CompanyScanContext,
        resume: dict[str, Any],
    ) -> tuple[bool, dict[str, Any] | None]:
        from api.services.company_scan_recovery import restore_identity

        restore_core = bool(resume.get("core_completed"))
        restored_identity = (
            await restore_identity(
                ctx.db,
                project_id=ctx.plan.project_id,
                company_name=ctx.plan.company_name,
            )
            if restore_core
            else None
        )
        if restore_core and not restored_identity:
            logger.warning(
                "核心恢复标记缺少公司身份，回退完整执行 | task=%s",
                ctx.plan.task_id,
            )
            restore_core = False
        return restore_core, restored_identity

    @staticmethod
    def _mobile_completed(
        enabled: bool,
        resume: dict[str, Any],
        checkpoints: dict[str, dict[str, Any]],
        raw_results: dict[str, dict[str, Any]],
        previous_result: dict[str, Any],
    ) -> bool:
        from api.services.company_scan_recovery import (
            recovery_modules_for_incomplete_sources,
        )

        invalid = set(raw_results).difference(checkpoints)
        incomplete = recovery_modules_for_incomplete_sources(previous_result)
        return bool(
            resume.get("mobile_completed")
            and "wechat" not in invalid
            and "wechat" not in incomplete
            and (
                not enabled
                or "wechat" in checkpoints
                or should_checkpoint_module(
                    "wechat", dict(previous_result.get("wechat") or {})
                )
            )
        )

    async def record(self, ctx: CompanyScanContext, kind: str, outcome: Any) -> None:
        if isinstance(outcome, dict):
            await self._record_coverage(ctx, kind, outcome)
        if kind in ctx.recovery.restored_primary_modules:
            return
        if not should_checkpoint_module(kind, outcome):
            return
        from api.services.task_progress import save_module_checkpoint

        await save_module_checkpoint(
            ctx.db,
            task_id=ctx.plan.task_id,
            module=kind,
            result=outcome,
        )

    async def _record_coverage(
        self,
        ctx: CompanyScanContext,
        kind: str,
        outcome: dict[str, Any],
    ) -> None:
        channels = {
            "control_structure": "control",
            "asset_url": "website",
            "xhs": "xhs",
            "bidding": "bidding",
            "wechat": "wechat",
            "scholar": "scholar",
        }
        channel = channels.get(kind, "")
        if not channel:
            return
        from api.services.target_scan_profile import (
            coverage_status_from_result,
            record_target_scan_coverage,
        )

        coverage_outcome = (
            dict(outcome.get("result") or {})
            if kind == "control_structure"
            else outcome
        )
        summary = self._coverage_summary(kind, coverage_outcome)
        status = coverage_status_from_result(channel, coverage_outcome)
        await record_target_scan_coverage(
            ctx.db,
            project_id=ctx.plan.project_id,
            target_id=ctx.target_id,
            channel=channel,
            status=status,
            task_id=ctx.plan.task_id,
            summary=summary,
            profile_fingerprint=str(ctx.scan_profile.get("fingerprint") or ""),
        )
        if kind == "wechat":
            await self._record_related_wechat_coverage(ctx, outcome, status)

    async def _record_related_wechat_coverage(
        self,
        ctx: CompanyScanContext,
        outcome: dict[str, Any],
        status: str,
    ) -> None:
        from api.services.target_scan_profile import record_target_scan_coverage

        for related_target_id in outcome.get("target_ids") or []:
            normalized_id = str(related_target_id or "")
            if not normalized_id or normalized_id == ctx.target_id:
                continue
            await record_target_scan_coverage(
                ctx.db,
                project_id=ctx.plan.project_id,
                target_id=normalized_id,
                channel="wechat",
                status=status,
                task_id=ctx.plan.task_id,
                summary={
                    "via_root_target_id": ctx.target_id,
                    "keywords_completed": int(outcome.get("keywords_completed") or 0),
                    "keyword_total": int(outcome.get("keyword_total") or 0),
                },
            )

    @staticmethod
    def _coverage_summary(kind: str, outcome: dict[str, Any]) -> dict[str, Any]:
        summary = {
            key: value
            for key, value in outcome.items()
            if isinstance(value, (str, int, float, bool)) and key != "error"
        }
        if kind != "asset_url":
            return summary
        url = dict(outcome.get("url_scan") or {})
        docs = dict(outcome.get("website_documents") or {})
        summary.update(
            {
                "url_status": str(url.get("status") or ""),
                "url_total": int(url.get("total_urls") or 0),
                "url_scanned": int(url.get("scanned_urls") or 0),
                "url_failed": int(url.get("failed_urls") or 0),
                "url_remaining": int(url.get("remaining_urls") or 0),
                "document_status": str(docs.get("status") or ""),
                "documents_discovered": int(docs.get("documents_scheduled") or 0),
                "documents_archived": int(docs.get("documents_archived") or 0),
                "document_pages_failed": int(docs.get("failed_pages") or 0),
                "documents_partial": int(docs.get("documents_partial") or 0),
                "attachments_archived": int(docs.get("attachments_archived") or 0),
                "document_truncated": bool(docs.get("truncated")),
            }
        )
        return summary
