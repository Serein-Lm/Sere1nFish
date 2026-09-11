"""Per-keyword device stage runtime for mobile collection."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from api.dao import mobile_collect as collect_dao
from core.mobile.collect.candidate_history import CandidateHistory
from core.mobile.collect.candidate_policy import (
    CandidatePolicyRegistry,
    candidate_tap_point,
)
from core.mobile.collect.candidate_time import (
    candidate_age_rejection,
    candidate_publish_time,
)


@dataclass(slots=True)
class KeywordStageState:
    ctx: Any
    item: Any
    keyword: str
    target: dict[str, Any] | None
    checkpoint_key: str
    checkpoint_target_id: str
    candidate_policy: Any
    candidate_history: CandidateHistory
    shots: int = 0
    successful_screens: int = 0
    screen_errors: int = 0
    emitted: int = 0
    details_attempted: int = 0
    details_accepted: int = 0
    persist_failures_before: int = 0
    no_new_streak: int = 0
    seen_keys: set[str] = field(default_factory=set)
    candidate_rejections: dict[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def shared(self) -> dict[str, Any]:
        return self.ctx.state

    @property
    def stopped(self) -> bool:
        return bool(self.shared["stop_event"].is_set())


class MobileKeywordStageRunner:
    """Keep one device keyword sequential while exposing testable sub-stages."""

    def __init__(
        self,
        *,
        collect_stage: Any,
        swipe_action: Callable[[str], None],
        observer: Callable[..., Any],
    ) -> None:
        self.collect_stage = collect_stage
        self.swipe_action = swipe_action
        self.observer = observer

    async def run(self, item: Any, ctx: Any) -> None:
        run = await self._build_state(item, ctx)
        if run.stopped:
            return
        if run.checkpoint_key and not run.shared.get("dry_run"):
            metrics = await run.ctx.drain("persist")
            run.persist_failures_before = int(metrics.get("failed") or 0)
        await self._mark_checkpoint(run, "running")
        if not await self._navigate(run):
            await self._fail_navigation(run)
        await self._scan_screens(run)
        await self._finalize(run)

    async def _build_state(self, item: Any, ctx: Any) -> KeywordStageState:
        shared = ctx.state
        seed = item.payload if isinstance(item.payload, dict) else {"keyword": item.payload}
        keyword = str(seed.get("keyword") or "")
        target = seed.get("target") or shared.get("target")
        policy = CandidatePolicyRegistry.resolve(
            str(
                shared.get("candidate_policy")
                or shared.get("source_link_strategy")
                or "default"
            )
        )
        return KeywordStageState(
            ctx=ctx,
            item=item,
            keyword=keyword,
            target=target,
            checkpoint_key=str(seed.get("checkpoint_key") or ""),
            checkpoint_target_id=str(
                (target or {}).get("target_id") or seed.get("target_id") or ""
            ),
            candidate_policy=policy,
            candidate_history=await self.collect_stage._candidate_history(ctx, target),
        )

    async def _navigate(self, run: KeywordStageState) -> bool:
        return await self.collect_stage._navigate_to_search_results(
            run.ctx,
            keyword=run.keyword,
            item_id=run.item.item_id,
            candidate_policy=run.candidate_policy,
        )

    async def _fail_navigation(self, run: KeywordStageState) -> None:
        shared = run.shared
        error = f"手机搜索导航未完成: {shared['app_name']} {run.keyword}".strip()
        await self._mark_checkpoint(run, "failed", error=error)
        raise RuntimeError(error)

    async def _scan_screens(self, run: KeywordStageState) -> None:
        shared = run.shared
        swipe_times = int(shared["swipe_times"])
        for index in range(swipe_times + 1):
            if run.stopped:
                break
            try:
                new_visible = await self._process_screen(run, index)
                if self._reached_bottom(run, new_visible):
                    self._observe_bottom(run, index)
                    break
                await self._publish_screen_progress(run, index)
            except Exception as exc:  # noqa: BLE001
                run.screen_errors += 1
                run.ctx.logger.warning(
                    "[collect] 处理失败 kw=%r idx=%s: %s",
                    run.keyword,
                    index,
                    exc,
                )
                self._observe_screen_error(run, index, exc)
            if index < swipe_times and not run.stopped:
                await self._swipe(run)

    async def _process_screen(self, run: KeywordStageState, index: int) -> int:
        image, screenshot_id, screenshot_url = await self.collect_stage._capture_save(
            run.ctx,
            run.keyword,
            note=f"collect kw={run.keyword} idx={index}",
        )
        run.shots += 1
        records = await self.collect_stage._analyze_list(
            run.ctx,
            run.keyword,
            image,
        )
        run.successful_screens += 1
        new_count, visible_count = self._classify_candidates(run, records)
        run.emitted += await self._emit_list_candidates(
            run,
            records,
            screenshot_id,
            screenshot_url,
        )
        self._observe_triage(run, index, records, new_count, visible_count)
        if self._should_review_details(run):
            await self._review_and_collect_details(run, records, index)
        return visible_count

    def _classify_candidates(
        self,
        run: KeywordStageState,
        records: list[dict[str, Any]],
    ) -> tuple[int, int]:
        shared = run.shared
        new_count = 0
        visible_count = 0
        for record in records:
            key = self._candidate_key(run, record)
            is_new_visible = key not in run.seen_keys
            visible_count += int(is_new_visible)
            history_reason = run.candidate_history.match(
                dict(record.get("fields") or {}),
                source_url=record.get("source_url"),
            )
            age_reason = candidate_age_rejection(
                record,
                max_age_days=int(shared.get("max_item_age_days") or 0),
            )
            rejection = (
                ("history", history_reason)
                if history_reason
                else ("stale", age_reason)
                if age_reason
                else None
            )
            if rejection:
                run.candidate_rejections[key] = rejection
                self._count_rejection(shared, key, rejection[0])
            elif is_new_visible:
                new_count += 1
            run.seen_keys.add(key)
        return new_count, visible_count

    @staticmethod
    def _count_rejection(state: dict[str, Any], key: str, kind: str) -> None:
        skipped = state.setdefault(f"{kind}_candidate_keys", set())
        if key in skipped:
            return
        skipped.add(key)
        counter = "duplicates_skipped" if kind == "history" else "stale_skipped"
        counters = state.setdefault("counters", {})
        counters[counter] = int(counters.get(counter) or 0) + 1

    async def _emit_list_candidates(
        self,
        run: KeywordStageState,
        records: list[dict[str, Any]],
        screenshot_id: str,
        screenshot_url: str,
    ) -> int:
        if not run.candidate_policy.persist_list_candidates:
            return 0
        for record in records:
            await run.ctx.emit(
                "persist",
                {
                    "fields": record["fields"],
                    "score": record.get("score"),
                    "subject_match": record.get("subject_match"),
                    "score_reason": record.get("score_reason", ""),
                    "source_url": record.get("source_url"),
                    "target_id": str((run.target or {}).get("target_id") or ""),
                    "target_name": str(
                        (run.target or {}).get("canonical_name") or ""
                    ),
                    "keyword": run.keyword,
                    "screenshot_id": screenshot_id,
                    "screenshot_url": screenshot_url,
                    "detail": False,
                },
            )
        return len(records)

    def _should_review_details(self, run: KeywordStageState) -> bool:
        return bool(
            run.shared.get("deep_collect")
            and int(run.shared.get("detail_max_items", 5)) > 0
            and not run.stopped
        )

    async def _review_and_collect_details(
        self,
        run: KeywordStageState,
        records: list[dict[str, Any]],
        screen_index: int,
    ) -> None:
        candidates, reviews = self._review_candidates(run, records, screen_index)
        self._record_reviews(run, reviews, screen_index)
        candidates.sort(key=lambda item: self._rank_candidate(run, item[1]), reverse=True)
        details_left, reviews_left = self._remaining_detail_budget(run)
        per_screen = 0
        detailed = run.shared.setdefault("detailed_record_keys", set())
        for candidate_key, candidate in candidates:
            if candidate_key in detailed:
                continue
            if run.candidate_policy.max_details_per_screen > 0 and per_screen >= run.candidate_policy.max_details_per_screen:
                break
            if details_left <= 0 or reviews_left <= 0 or run.stopped:
                break
            detailed.add(candidate_key)
            run.details_attempted += 1
            per_screen += 1
            run.shared["details_attempted"] = int(run.shared.get("details_attempted") or 0) + 1
            reviews_left -= 1
            accepted = await self.collect_stage._deep_dive(
                run.ctx,
                run.keyword,
                candidate,
                run.target,
            )
            if accepted:
                run.candidate_history.add(
                    dict(candidate.get("fields") or {}),
                    source_url=candidate.get("source_url"),
                )
                run.details_accepted += 1
                run.shared["details_accepted"] = int(run.shared.get("details_accepted") or 0) + 1
                details_left -= 1

    def _review_candidates(
        self,
        run: KeywordStageState,
        records: list[dict[str, Any]],
        screen_index: int,
    ) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, Any]]]:
        candidates: list[tuple[str, dict[str, Any]]] = []
        reviews: list[dict[str, Any]] = []
        shared = run.shared
        for candidate in records:
            key = self._candidate_key(run, candidate)
            precheck = run.candidate_rejections.get(key)
            decision = None if precheck else run.candidate_policy.review_detail(
                candidate,
                min_score=int(shared.get("min_score_to_detail", 60)),
                min_subject_match=int(shared.get("min_subject_match", 70)),
                target_name=str(
                    (run.target or {}).get("canonical_name")
                    or shared.get("collection_subject")
                    or run.keyword
                ),
                aliases=list((run.target or {}).get("aliases") or []),
            )
            accepted = bool(decision.accepted) if decision else False
            reason = (
                decision.reason
                if decision
                else str(precheck[1] if precheck else "候选审核未返回决策")
            )
            reviews.append(self._candidate_review(run, candidate, key, screen_index, accepted, reason, precheck))
            if accepted:
                candidates.append((key, candidate))
        return candidates, reviews

    @staticmethod
    def _candidate_review(
        run: KeywordStageState,
        candidate: dict[str, Any],
        key: str,
        screen_index: int,
        accepted: bool,
        reason: str,
        precheck: tuple[str, str] | None,
    ) -> dict[str, Any]:
        return {
            "keyword": run.keyword,
            "screen": screen_index,
            "candidate_key": key,
            "accepted": accepted,
            "reason": reason,
            "precheck": precheck[0] if precheck else "",
            "content_kind": candidate.get("content_kind"),
            "is_article_result": candidate.get("is_article_result"),
            "subject_match": candidate.get("subject_match"),
            "score": candidate.get("score"),
            "target_evidence": candidate.get("target_evidence") or "",
            "tap": list(candidate_tap_point(candidate) or ()),
            "tap_bounds": candidate.get("tap_bounds"),
            "fields": candidate.get("fields") or {},
        }

    def _record_reviews(
        self,
        run: KeywordStageState,
        reviews: list[dict[str, Any]],
        screen_index: int,
    ) -> None:
        if not reviews:
            return
        self.observer(
            "列表候选点击前审核完成",
            project_id=run.shared.get("project_id") or "",
            task_id=run.shared["run_task_id"],
            source="mobile_collect",
            level="info",
            event="collect_candidate_review",
            data={"keyword": run.keyword, "screen": screen_index, "items": reviews},
        )
        if not run.shared.get("dry_run"):
            return
        audit = run.shared["candidate_reviews"]
        remaining = max(
            0,
            int(run.shared.get("candidate_review_limit") or 0) - len(audit),
        )
        audit.extend(reviews[:remaining])

    @staticmethod
    def _rank_candidate(run: KeywordStageState, candidate: dict[str, Any]) -> tuple[float, int, int]:
        published_at = candidate_publish_time(candidate)
        recency = (
            published_at.timestamp()
            if run.shared.get("prefer_recent_items") and published_at is not None
            else 0.0
        )
        return (
            recency,
            int(candidate.get("subject_match") or 0),
            int(candidate.get("score") or 0),
        )

    @staticmethod
    def _remaining_detail_budget(run: KeywordStageState) -> tuple[int, int]:
        shared = run.shared
        details = max(0, int(shared.get("detail_max_items", 5)) - run.details_accepted)
        total_details = int(shared.get("detail_max_total_items", 0))
        if total_details > 0:
            details = min(details, max(0, total_details - int(shared.get("details_accepted") or 0)))
        review_limit = int(shared.get("detail_review_max_items") or shared.get("detail_max_items", 5))
        reviews = max(0, review_limit - run.details_attempted)
        total_reviews = int(shared.get("detail_review_max_total_items") or shared.get("detail_max_total_items", 0))
        if total_reviews > 0:
            reviews = min(reviews, max(0, total_reviews - int(shared.get("details_attempted") or 0)))
        return details, reviews

    async def _swipe(self, run: KeywordStageState) -> None:
        try:
            await asyncio.to_thread(self.swipe_action, run.shared["device_id"])
        except Exception as exc:  # noqa: BLE001
            run.ctx.logger.warning("[collect] 滑动失败: %s", exc)
        await asyncio.sleep(float(run.shared["swipe_interval"]))

    @staticmethod
    def _reached_bottom(run: KeywordStageState, visible_count: int) -> bool:
        run.no_new_streak = run.no_new_streak + 1 if visible_count == 0 else 0
        return run.no_new_streak >= int(run.shared.get("no_new_stop_threshold", 2))

    async def _finalize(self, run: KeywordStageState) -> None:
        if run.stopped:
            raise RuntimeError(f"采集已停止，关键词未完成: {run.keyword or '-'}")
        if run.successful_screens == 0:
            error = (
                f"关键词 {run.keyword or '-'} 未产生有效采集屏幕"
                f"（截图 {run.shots}，失败 {run.screen_errors}）"
            )
            await self._mark_checkpoint(
                run,
                "failed",
                error=error,
                stats={"shots": run.shots, "screen_errors": run.screen_errors},
            )
            raise RuntimeError(error)
        run.ctx.logger.info(
            "[collect] kw=%s 截屏 %s 张, 产出 %s 条候选",
            run.keyword or "-",
            run.shots,
            run.emitted,
        )
        self._observe_complete(run)
        stats = self._checkpoint_stats(run)
        await self._mark_checkpoint(run, "captured", stats=stats)
        if run.checkpoint_key and not run.shared.get("dry_run"):
            metrics = await run.ctx.drain("persist")
            if int(metrics.get("failed") or 0) == run.persist_failures_before:
                await self._mark_checkpoint(run, "completed", stats=stats)
                run.shared["keywords_completed"] = int(run.shared.get("keywords_completed") or 0) + 1
        run.shared["keywords_processed"] = int(run.shared.get("keywords_processed") or 0) + 1
        await self._publish_keyword_progress(run)

    async def _mark_checkpoint(
        self,
        run: KeywordStageState,
        status: str,
        *,
        error: str = "",
        stats: dict[str, Any] | None = None,
    ) -> None:
        if not run.checkpoint_key or run.shared.get("dry_run"):
            return
        await collect_dao.mark_keyword_checkpoint(
            run.shared["db"],
            run_task_id=run.shared["run_task_id"],
            task_def_id=run.shared["task_def_id"],
            definition_fingerprint=run.shared["definition_fingerprint"],
            checkpoint_key=run.checkpoint_key,
            keyword=run.keyword,
            target_id=run.checkpoint_target_id,
            status=status,
            error=error,
            stats=stats,
        )

    async def _publish_screen_progress(self, run: KeywordStageState, index: int) -> None:
        parent = str(run.shared.get("parent_task_id") or "")
        if not parent:
            return
        from api.services.task_progress import update_source_progress

        await update_source_progress(
            run.shared["db"],
            task_id=parent,
            source=str(run.shared.get("progress_source") or "wechat"),
            total=int(run.shared.get("keyword_total") or 0),
            processed=int(run.shared.get("keywords_processed") or 0),
            status="running",
            message=(
                f"{str(run.shared.get('progress_label') or '公众号')}正在处理关键词 "
                f"{run.keyword or '-'}，第 {index + 1} 屏"
            ),
            extra={
                "current_keyword": run.keyword,
                "screen": index + 1,
                "details_attempted": int(run.shared.get("details_attempted") or 0),
                "details_accepted": int(run.shared.get("details_accepted") or 0),
            },
        )

    async def _publish_keyword_progress(self, run: KeywordStageState) -> None:
        parent = str(run.shared.get("parent_task_id") or "")
        if not parent:
            return
        from api.services.task_progress import update_source_progress

        processed = int(run.shared["keywords_processed"])
        total = int(run.shared.get("keyword_total") or 0)
        await update_source_progress(
            run.shared["db"],
            task_id=parent,
            source=str(run.shared.get("progress_source") or "wechat"),
            total=total,
            processed=processed,
            succeeded=int(run.shared.get("keywords_completed") or 0),
            status="running",
            message=(
                f"{str(run.shared.get('progress_label') or '公众号')}关键词已采集 "
                f"{processed}/{total}，正在归档"
            ),
            extra={
                "current_keyword": run.keyword,
                "details_attempted": int(run.shared.get("details_attempted") or 0),
                "details_accepted": int(run.shared.get("details_accepted") or 0),
            },
        )

    def _observe_triage(
        self,
        run: KeywordStageState,
        index: int,
        records: list[dict[str, Any]],
        new_count: int,
        visible_count: int,
    ) -> None:
        self.observer(
            f"列表分诊 kw={run.keyword or '-'} idx={index} 候选 {len(records)} 条",
            project_id=run.shared.get("project_id") or "",
            task_id=run.shared["run_task_id"],
            source="mobile_collect",
            level="info",
            event="collect_triage",
            data={
                "keyword": run.keyword,
                "index": index,
                "candidates": len(records),
                "new": new_count,
                "new_visible": visible_count,
                "max_score": max((item.get("score") or 0 for item in records), default=0),
            },
        )

    def _observe_bottom(self, run: KeywordStageState, index: int) -> None:
        self.observer(
            f"已滑到底 kw={run.keyword or '-'} 第{index}屏 共见 {len(run.seen_keys)} 条",
            project_id=run.shared.get("project_id") or "",
            task_id=run.shared["run_task_id"],
            source="mobile_collect",
            level="info",
            event="collect_reached_bottom",
            data={
                "keyword": run.keyword,
                "index": index,
                "seen_keys": len(run.seen_keys),
                "streak": run.no_new_streak,
            },
        )

    def _observe_screen_error(self, run: KeywordStageState, index: int, error: Exception) -> None:
        self.observer(
            f"采集处理失败: {error}",
            project_id=run.shared.get("project_id") or "",
            task_id=run.shared["run_task_id"],
            source="mobile_collect",
            level="warning",
            event="collect_shot_error",
            data={"keyword": run.keyword, "index": index, "error": str(error)},
        )

    def _observe_complete(self, run: KeywordStageState) -> None:
        self.observer(
            f"采集完成 kw={run.keyword or '-'} 截屏 {run.shots} 张 候选 {run.emitted} 条",
            project_id=run.shared.get("project_id") or "",
            task_id=run.shared["run_task_id"],
            source="mobile_collect",
            level="info",
            event="collect_captured",
            data={"keyword": run.keyword, "shots": run.shots, "emitted": run.emitted},
        )

    def _candidate_key(self, run: KeywordStageState, candidate: dict[str, Any]) -> str:
        return collect_dao.stable_record_id(
            run.shared["task_def_id"],
            candidate["fields"],
            run.shared["dedup_key_fields"],
        )

    @staticmethod
    def _checkpoint_stats(run: KeywordStageState) -> dict[str, int]:
        counters = run.shared.get("counters", {})
        return {
            "shots": run.shots,
            "successful_screens": run.successful_screens,
            "screen_errors": run.screen_errors,
            "emitted": run.emitted,
            "duplicates_skipped": int(counters.get("duplicates_skipped") or 0),
            "stale_skipped": int(counters.get("stale_skipped") or 0),
        }
