"""Lifecycle runtime for mobile collection plans."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from api.dao import mobile_collect as collect_dao
from core.logger import get_logger
from core.mobile.collect.contracts import MobileCollectExecution, MobileCollectPlan
from core.mobile.collect.planning import resolve_collection_target, resolve_keyword_plan
from core.mobile.collect.stage_registry import MobileStageRegistry
from core.mobile.collect.state import build_stream_state, project_terminal_result
from core.stream import Item


logger = get_logger("mobile_collect.runtime")
_OBS_SOURCE = "mobile_collect"


class MobileRunRegistry:
    """Own cooperative stop signals for active in-process collection runs."""

    def __init__(self) -> None:
        self.events: dict[str, asyncio.Event] = {}

    def register(self, run_task_id: str, event: asyncio.Event) -> None:
        if run_task_id in self.events:
            raise RuntimeError(f"手机采集运行实例重复: {run_task_id}")
        self.events[run_task_id] = event

    def unregister(self, run_task_id: str, event: asyncio.Event) -> None:
        if self.events.get(run_task_id) is event:
            self.events.pop(run_task_id, None)

    def request_stop(self, run_task_id: str) -> bool:
        event = self.events.get(run_task_id)
        if event is None:
            return False
        event.set()
        return True

    def is_running(self, run_task_id: str) -> bool:
        return run_task_id in self.events


MOBILE_RUN_REGISTRY = MobileRunRegistry()


class MobileCollectRuntime:
    """Resolve a plan, execute its registered stream stages, then project output."""

    def __init__(
        self,
        plan: MobileCollectPlan,
        *,
        stages: MobileStageRegistry,
        observer: Callable[..., Any],
        run_registry: MobileRunRegistry = MOBILE_RUN_REGISTRY,
    ) -> None:
        self.plan = plan
        self.stages = stages
        self.observer = observer
        self.run_registry = run_registry

    async def run(self) -> dict[str, Any]:
        execution = await self._prepare()
        stop_event = execution.state["stop_event"]
        self.run_registry.register(self.plan.run_task_id, stop_event)
        try:
            self._observe_start(execution)
            await self._execute(execution)
        finally:
            self.run_registry.unregister(self.plan.run_task_id, stop_event)
        self._observe_done(execution)
        return project_terminal_result(execution)

    async def _prepare(self) -> MobileCollectExecution:
        self.plan.validate()
        target = await resolve_collection_target(self.plan)
        seeds = await resolve_keyword_plan(self.plan, target)
        stop_event = asyncio.Event()
        state = build_stream_state(self.plan, seeds, stop_event=stop_event)
        return MobileCollectExecution(plan=self.plan, seeds=seeds, state=state)

    async def _execute(self, execution: MobileCollectExecution) -> None:
        pipeline = self.stages.build(
            state=execution.state,
            pipeline_id=self.plan.run_task_id[:8],
            entry="collect",
        )
        seeds = [Item(payload=spec) for spec in execution.seeds.pending_seed_specs]
        execution.metrics = await self._run_stream(pipeline, seeds, execution)
        failure_state = await self._finalize_metrics_and_checkpoints(execution)
        await _update_parent_terminal_progress(
            self.plan.db,
            execution.state,
            timed_out=execution.timed_out,
            all_failed=failure_state["all_failed"],
            failed_keywords=failure_state["collect_failed"],
        )
        self._raise_terminal_failure(execution, failure_state)

    async def _run_stream(
        self,
        pipeline: Any,
        seeds: list[Item],
        execution: MobileCollectExecution,
    ) -> dict[str, Any]:
        if not seeds:
            return {}
        try:
            if self.plan.runtime_limit:
                return await asyncio.wait_for(
                    pipeline.run(seeds=seeds, entry="collect"),
                    timeout=self.plan.runtime_limit,
                )
            return await pipeline.run(seeds=seeds, entry="collect")
        except asyncio.TimeoutError:
            execution.timed_out = True
            execution.state["stop_event"].set()
            logger.warning(
                "运行达到总时限，保留部分结果 | run=%s timeout=%ss",
                self.plan.run_task_id,
                self.plan.runtime_limit,
            )
            await self._publish_timeout_progress(execution)
            return {}

    async def _publish_timeout_progress(
        self,
        execution: MobileCollectExecution,
    ) -> None:
        state = execution.state
        parent_task_id = str(state.get("parent_task_id") or "")
        if not parent_task_id:
            return
        from api.services.task_progress import update_source_progress

        await update_source_progress(
            self.plan.db,
            task_id=parent_task_id,
            source=str(state.get("progress_source") or "wechat"),
            total=len(execution.seeds.keywords),
            processed=int(state.get("keywords_completed") or 0),
            status="partial",
            message=(
                f"{str(state.get('progress_label') or '公众号')}达到 "
                f"{self.plan.runtime_limit} 秒时限，已保留部分结果"
            ),
        )

    async def _finalize_metrics_and_checkpoints(
        self,
        execution: MobileCollectExecution,
    ) -> dict[str, int | bool]:
        state = execution.state
        collect = execution.metrics.get("collect")
        persist = execution.metrics.get("persist")
        collect_failed = int(getattr(collect, "failed", 0) or 0)
        collect_received = int(getattr(collect, "received", 0) or 0)
        collect_succeeded = int(getattr(collect, "succeeded", 0) or 0)
        persist_failed = int(getattr(persist, "failed", 0) or 0)
        state["counters"]["failed"] = collect_failed
        state["counters"]["persist_failed"] = persist_failed
        if self.plan.dry_run:
            state["keywords_completed"] = int(state.get("keywords_processed") or 0)
        elif not execution.timed_out and persist_failed == 0:
            await self._commit_keyword_checkpoints(execution)
        return {
            "collect_failed": collect_failed,
            "persist_failed": persist_failed,
            "all_failed": bool(collect_received and collect_succeeded == 0),
        }

    async def _commit_keyword_checkpoints(
        self,
        execution: MobileCollectExecution,
    ) -> None:
        state = execution.state
        for candidate in state.get("checkpoint_candidates", {}).values():
            await collect_dao.mark_keyword_checkpoint(
                self.plan.db,
                run_task_id=self.plan.run_task_id,
                task_def_id=self.plan.task_def_id,
                definition_fingerprint=execution.seeds.definition_fingerprint,
                checkpoint_key=str(candidate.get("checkpoint_key") or ""),
                keyword=str(candidate.get("keyword") or ""),
                target_id=str(candidate.get("target_id") or ""),
                status="completed",
                stats=dict(candidate.get("stats") or {}),
            )
            state["keywords_completed"] = int(state.get("keywords_completed") or 0) + 1

    @staticmethod
    def _raise_terminal_failure(
        execution: MobileCollectExecution,
        failure_state: dict[str, int | bool],
    ) -> None:
        if failure_state["all_failed"]:
            failed = int(failure_state["collect_failed"])
            received = int(
                getattr(execution.metrics.get("collect"), "received", 0) or 0
            )
            raise RuntimeError(
                f"手机采集全部失败: {failed}/{received} 个关键词进入失败队列"
            )
        if execution.state.get("require_persist_success") and failure_state[
            "persist_failed"
        ]:
            raise RuntimeError(
                f"手机采集持久化失败: {failure_state['persist_failed']} 条结果未能完整归档"
            )

    def _observe_start(self, execution: MobileCollectExecution) -> None:
        self.observer(
            "采集任务开始" + ("(试跑)" if self.plan.dry_run else ""),
            project_id=self.plan.project_id or "",
            task_id=self.plan.run_task_id,
            source=_OBS_SOURCE,
            level="notice",
            event="collect_start",
            data={
                "task_def_id": self.plan.task_def_id,
                "device_id": self.plan.device_id,
                "keywords": execution.seeds.keywords,
                "resumed_keywords": execution.seeds.completed_count,
                "dry_run": self.plan.dry_run,
                "plan_version": self.plan.version,
                "stages": self.stages.names,
            },
        )

    def _observe_done(self, execution: MobileCollectExecution) -> None:
        state = execution.state
        self.observer(
            "采集任务结束" + ("(试跑)" if self.plan.dry_run else ""),
            project_id=self.plan.project_id or "",
            task_id=self.plan.run_task_id,
            source=_OBS_SOURCE,
            level="notice",
            event="collect_done",
            data={
                "task_def_id": self.plan.task_def_id,
                "stopped": bool(state["stop_event"].is_set()),
                "dry_run": self.plan.dry_run,
                "preview_count": len(state.get("preview") or []),
                "candidate_review_count": len(state.get("candidate_reviews") or []),
                "detail_entry_review_count": len(
                    state.get("detail_entry_reviews") or []
                ),
                "timed_out": execution.timed_out,
                "keywords_completed": int(state.get("keywords_completed") or 0),
                "keyword_total": int(state.get("keyword_total") or 0),
                **dict(state.get("counters") or {}),
            },
        )


async def _update_parent_terminal_progress(
    db: Any,
    state: dict[str, Any],
    *,
    timed_out: bool,
    all_failed: bool,
    failed_keywords: int,
) -> None:
    parent_task_id = str(state.get("parent_task_id") or "")
    if not parent_task_id:
        return
    from api.services.task_progress import update_source_progress

    completed = int(state.get("keywords_completed") or 0)
    total = int(state.get("keyword_total") or 0)
    stopped = bool(state["stop_event"].is_set())
    partial = timed_out or stopped or completed < total
    status = "error" if all_failed else ("partial" if partial else "completed")
    label = str(state.get("progress_label") or "公众号")
    if all_failed:
        message = f"{label}采集失败，已完成 {completed}/{total} 个关键词"
    elif timed_out:
        message = f"{label}达到运行时限，已完成 {completed}/{total} 个关键词"
    elif stopped:
        message = f"{label}已停止，保留 {completed}/{total} 个关键词结果"
    else:
        message = f"{label}关键词已完成 {completed}/{total}"
    await update_source_progress(
        db,
        task_id=parent_task_id,
        source=str(state.get("progress_source") or "wechat"),
        total=total,
        processed=completed,
        succeeded=completed,
        failed=max(0, int(failed_keywords or 0)),
        status=status,
        message=message,
    )
