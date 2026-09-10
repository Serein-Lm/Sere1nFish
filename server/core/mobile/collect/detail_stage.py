"""Synchronous detail-review stage for a single mobile candidate."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from api.dao import findings as findings_dao
from api.dao import mobile_collect as collect_dao
from core.mobile.collect.candidate_policy import CandidatePolicyRegistry, candidate_tap_point
from core.mobile.collect.detail_capture import DetailCaptureContext, DetailCaptureRegistry
from core.mobile.collect.detail_observation import DetailStageObserver


@dataclass(slots=True)
class DetailRunState:
    ctx: Any
    keyword: str
    candidate: dict[str, Any]
    target: dict[str, Any] | None
    tap_x: int
    tap_y: int
    policy: Any
    source_link_strategy: str
    shots_b64: list[str] = field(default_factory=list)
    shot_ids: list[str] = field(default_factory=list)
    shot_urls: list[str] = field(default_factory=list)
    source_url: str | None = None
    restored_to_results: bool = False

    @property
    def shared(self) -> dict[str, Any]:
        return self.ctx.state

    @property
    def stopped(self) -> bool:
        return bool(self.shared["stop_event"].is_set())


@dataclass(frozen=True, slots=True)
class _IngestDecision:
    terminal: bool
    accepted: bool


class MobileDetailStageRunner:
    """Review, archive and persist one opened detail while the device is stable."""

    def __init__(
        self,
        *,
        capture_save: Callable[..., Awaitable[tuple[str, str, str]]],
        capture_verification: Callable[[Any], Awaitable[str]],
        tap_action: Callable[[str, int, int], None],
        back_action: Callable[[str], None],
        swipe_action: Callable[[str], None],
        scroll_top_action: Callable[[str], None],
        image_signature: Callable[[str], list[int] | None],
        images_similar: Callable[..., bool],
        source_link_extractor: Callable[..., Any],
        source_ingestor: Callable[..., Awaitable[dict[str, Any]]],
        detail_verifier: Callable[..., Awaitable[dict[str, Any]]],
        detail_analyzer: Callable[..., Awaitable[dict[str, Any] | None]],
        observer: Callable[..., Any],
        ingest_timeout_seconds: int,
    ) -> None:
        self.capture_save = capture_save
        self.capture_verification = capture_verification
        self.tap_action = tap_action
        self.back_action = back_action
        self.swipe_action = swipe_action
        self.scroll_top_action = scroll_top_action
        self.image_signature = image_signature
        self.images_similar = images_similar
        self.source_link_extractor = source_link_extractor
        self.source_ingestor = source_ingestor
        self.detail_verifier = detail_verifier
        self.detail_analyzer = detail_analyzer
        self.observer = observer
        self.events = DetailStageObserver(observer)
        self.ingest_timeout_seconds = ingest_timeout_seconds

    async def run(
        self,
        ctx: Any,
        keyword: str,
        candidate: dict[str, Any],
        target: dict[str, Any] | None,
    ) -> bool:
        run = self._build_state(ctx, keyword, candidate, target)
        if run is None:
            return False
        self.events.enter(run)
        try:
            await asyncio.to_thread(
                self.tap_action,
                run.shared["device_id"],
                run.tap_x,
                run.tap_y,
            )
            await asyncio.sleep(1.5)
            if run.stopped or not await self._verify_opened_detail(run):
                return False
            special = await self._capture_registered_detail(run)
            if special is not None:
                return special
            await self._capture_initial_frame(run)
            await self._extract_source_link(run)
            if run.source_link_strategy != "none" and not run.source_url and not run.policy.allow_mobile_detail_fallback:
                return False
            ingest_decision = await self._try_source_ingest(run)
            if ingest_decision.terminal:
                return ingest_decision.accepted
            await self._scroll_mobile_detail(run)
            return await self._analyze_and_emit_mobile_detail(run)
        except Exception as exc:  # noqa: BLE001
            self.events.error(run, exc)
            return False
        finally:
            await self._restore_results_page(run)

    @staticmethod
    def _build_state(
        ctx: Any,
        keyword: str,
        candidate: dict[str, Any],
        target: dict[str, Any] | None,
    ) -> DetailRunState | None:
        tap = candidate_tap_point(candidate)
        if tap is None:
            return None
        shared = ctx.state
        strategy = str(shared.get("source_link_strategy") or "none")
        policy = CandidatePolicyRegistry.resolve(
            str(shared.get("candidate_policy") or strategy or "default")
        )
        return DetailRunState(
            ctx=ctx,
            keyword=keyword,
            candidate=candidate,
            target=target or shared.get("target"),
            tap_x=tap[0],
            tap_y=tap[1],
            policy=policy,
            source_link_strategy=strategy,
        )

    async def _verify_opened_detail(self, run: DetailRunState) -> bool:
        if not run.policy.requires_detail_verification:
            return True
        target_name = str(
            (run.target or {}).get("canonical_name")
            or run.shared.get("collection_subject")
            or run.keyword
        )
        aliases = list((run.target or {}).get("aliases") or [])
        attempts: list[dict[str, Any]] = []
        verification = await self._verify_frame(run, target_name, aliases, attempts)
        if (
            run.policy.retry_detail_verification_at_top
            and verification.get("page_kind") == "article"
            and not str(verification.get("visible_title") or "").strip()
        ):
            await asyncio.to_thread(self.scroll_top_action, run.shared["device_id"])
            await asyncio.sleep(0.8)
            verification = await self._verify_frame(run, target_name, aliases, attempts)
        decision = run.policy.review_opened_detail(
            verification,
            candidate=run.candidate,
            target_name=target_name,
            aliases=aliases,
            min_subject_match=int(run.shared.get("min_subject_match", 70)),
        )
        review = {
            **verification,
            "keyword": run.keyword,
            "accepted": decision.accepted,
            "reason": decision.reason,
            "model_reason": str(verification.get("reason") or ""),
            "verification_attempts": len(attempts),
            "tap": [run.tap_x, run.tap_y],
            "candidate_fields": run.candidate.get("fields") or {},
        }
        reviews = run.shared.setdefault("detail_entry_reviews", [])
        if len(reviews) < int(run.shared.get("detail_entry_review_limit") or 100):
            reviews.append(review)
        self.observer(
            "点击后详情一致性校验完成",
            project_id=run.shared.get("project_id") or "",
            task_id=run.shared["run_task_id"],
            source="mobile_collect",
            level="info" if decision.accepted else "warning",
            event="collect_detail_entry_review",
            data=review,
        )
        return bool(decision.accepted)

    async def _verify_frame(
        self,
        run: DetailRunState,
        target_name: str,
        aliases: list[str],
        attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        frame = await self.capture_verification(run.ctx)
        result = await self.detail_verifier(
            frame,
            app_name=run.shared["app_name"],
            keyword=run.keyword,
            candidate_fields=dict(run.candidate.get("fields") or {}),
            target_name=target_name,
            target_aliases=aliases,
            policy_instructions=run.policy.detail_verification_instructions(),
            project_id=run.shared["project_id"],
            task_id=run.shared["run_task_id"],
        )
        attempts.append(result)
        return result

    async def _capture_registered_detail(self, run: DetailRunState) -> bool | None:
        capture = DetailCaptureRegistry.resolve(
            str(run.shared.get("detail_capture_strategy") or "default")
        )
        if capture is None:
            return None
        result = await capture.capture(self._detail_capture_context(run))
        run.restored_to_results = result.restored_to_results
        self.observer(
            result.reason or "详情证据采集完成",
            project_id=run.shared.get("project_id") or "",
            task_id=run.shared["run_task_id"],
            source="mobile_collect",
            level="notice" if result.accepted else "info",
            event="collect_detail_capture",
            data={
                "keyword": run.keyword,
                "strategy": capture.name,
                "accepted": result.accepted,
                "frames": len(result.frames),
                "restored_to_results": run.restored_to_results,
            },
        )
        if not result.accepted:
            return False
        await run.ctx.emit(
            "persist",
            {
                "fields": {**result.fields, "platform": str(run.shared.get("platform") or "")},
                "score": run.candidate.get("score"),
                "subject_match": run.candidate.get("subject_match"),
                "score_reason": run.candidate.get("score_reason") or "",
                "source_type": str(run.shared.get("record_source_type") or "social_place_media"),
                "target_id": str((run.target or {}).get("target_id") or ""),
                "target_name": str(
                    (run.target or {}).get("canonical_name")
                    or run.shared.get("collection_subject")
                    or ""
                ),
                "keyword": run.keyword,
                "screenshot_id": "",
                "screenshot_url": "",
                "screenshot_ids": [],
                "screenshot_urls": [],
                "candidate_fields": run.candidate.get("fields") or {},
                "media_frames": result.frames,
                "detail": True,
            },
        )
        return True

    def _detail_capture_context(self, run: DetailRunState) -> DetailCaptureContext:
        return DetailCaptureContext(
            device_id=run.shared["device_id"],
            app_name=run.shared["app_name"],
            keyword=run.keyword,
            place_name=str(
                run.shared.get("collection_subject")
                or (run.target or {}).get("canonical_name")
                or run.keyword
            ),
            collection_goal=str(run.shared.get("collection_goal") or ""),
            navigation_hint=str(run.shared.get("media_navigation_hint") or ""),
            candidate_fields=dict(run.candidate.get("fields") or {}),
            project_id=str(run.shared.get("project_id") or ""),
            task_id=run.shared["run_task_id"],
            owner=str(run.shared.get("owner") or ""),
            stop_event=run.shared["stop_event"],
            max_items=max(1, int(run.shared.get("media_max_items") or 1)),
            swipe_interval=float(run.shared.get("swipe_interval") or 1.0),
        )

    async def _capture_initial_frame(self, run: DetailRunState) -> None:
        image, screenshot_id, screenshot_url = await self.capture_save(
            run.ctx,
            run.keyword,
            note=f"detail kw={run.keyword} score={run.candidate.get('score')}",
        )
        run.shots_b64.append(image)
        run.shot_ids.append(screenshot_id)
        run.shot_urls.append(screenshot_url)

    async def _extract_source_link(self, run: DetailRunState) -> None:
        if run.source_link_strategy == "none" or run.stopped:
            return
        result = await asyncio.to_thread(
            self.source_link_extractor,
            run.shared["device_id"],
            run.source_link_strategy,
        )
        if result.ok:
            run.source_url = result.url
            self.events.link(run, True, result)
            return
        run.ctx.logger.warning(
            "[collect] 原文链接提取失败 strategy=%s: %s",
            run.source_link_strategy,
            result.error,
        )
        self.events.link(run, False, result)

    async def _try_source_ingest(self, run: DetailRunState) -> _IngestDecision:
        if not run.source_url or run.stopped:
            return _IngestDecision(False, False)
        try:
            result = await asyncio.wait_for(
                self.source_ingestor(
                    run.shared["db"],
                    url=run.source_url,
                    project_id=run.shared["project_id"] or "",
                    target=run.target,
                    task_def_id=run.shared["task_def_id"],
                    run_task_id=run.shared["run_task_id"],
                    keyword=run.keyword,
                    extract_fields=run.shared["extract_fields"],
                    discovery_score=run.candidate.get("score"),
                    discovery_subject_match=run.candidate.get("subject_match"),
                    discovery_context={
                        "candidate_fields": run.candidate.get("fields") or {},
                        "tap": [run.tap_x, run.tap_y],
                    },
                    persist=not bool(run.shared.get("dry_run")),
                    min_subject_match=int(run.shared.get("min_subject_match", 70)),
                ),
                timeout=self.ingest_timeout_seconds,
            )
            return await self._handle_ingest_result(run, result)
        except Exception as exc:  # noqa: BLE001
            self.events.ingest_fallback(run, exc)
            if run.policy.allow_mobile_detail_fallback:
                return _IngestDecision(False, False)
            await self._emit_pending_handoff(run, str(exc))
            return _IngestDecision(True, False)

    async def _handle_ingest_result(
        self,
        run: DetailRunState,
        result: dict[str, Any],
    ) -> _IngestDecision:
        if result.get("ok"):
            await self._emit_source_result(run, result)
            run.shared["counters"]["documents"] = int(
                run.shared["counters"].get("documents") or 0
            ) + 1
            self.events.source_ready(run, result)
            return _IngestDecision(True, True)
        if run.policy.allow_mobile_detail_fallback:
            return _IngestDecision(False, False)
        if result.get("rejected"):
            await self._archive_rejected_source(run, result)
        else:
            await self._emit_pending_handoff(
                run,
                str(result.get("reason") or "浏览器归档暂未完成"),
            )
        return _IngestDecision(True, False)

    async def _emit_source_result(
        self,
        run: DetailRunState,
        result: dict[str, Any],
    ) -> None:
        browser_ids = list(result.get("browser_screenshot_ids") or [])
        browser_urls = list(result.get("browser_screenshot_urls") or [])
        await run.ctx.emit(
            "persist",
            {
                "fields": {
                    **dict(run.candidate.get("fields") or {}),
                    **dict(result.get("fields") or {}),
                },
                "score": result.get("score"),
                "subject_match": result.get("subject_match"),
                "score_reason": result.get("score_reason") or "",
                "source_url": result.get("source_url") or run.source_url,
                "source_type": result.get("source_type") or "wechat_article",
                "source_document_id": result.get("document_id") or "",
                "source_document_version_id": result.get("version_id") or "",
                "target_id": result.get("target_id") or str((run.target or {}).get("target_id") or ""),
                "target_name": result.get("target_name") or str((run.target or {}).get("canonical_name") or ""),
                "contacts": result.get("contacts") or [],
                "keyword": run.keyword,
                "screenshot_id": run.shot_ids[0] if run.shot_ids else "",
                "screenshot_url": run.shot_urls[0] if run.shot_urls else "",
                "screenshot_ids": [*run.shot_ids, *browser_ids],
                "screenshot_urls": [*run.shot_urls, *browser_urls],
                "browser_screenshot_ids": browser_ids,
                "browser_screenshot_urls": browser_urls,
                "discovery_screenshot_ids": run.shot_ids,
                "discovery_screenshot_urls": run.shot_urls,
                "discovery_fields": run.candidate.get("fields") or {},
                "detail": True,
            },
        )

    async def _archive_rejected_source(
        self,
        run: DetailRunState,
        result: dict[str, Any],
    ) -> None:
        record_ids: list[str] = []
        if not run.shared.get("dry_run") and run.shared.get("project_id"):
            record_ids = await collect_dao.archive_rejected_source_records(
                run.shared["db"],
                task_def_id=str(run.shared.get("task_def_id") or ""),
                project_id=str(run.shared.get("project_id") or ""),
                source_document_id=str(result.get("document_id") or ""),
                target_id=str(
                    result.get("target_id")
                    or (run.target or {}).get("target_id")
                    or ""
                ),
            )
            for record_id in record_ids:
                await findings_dao.reconcile_contact_findings_for_record(
                    run.shared["db"],
                    project_id=str(run.shared.get("project_id") or ""),
                    record_id=record_id,
                    keep_finding_ids=[],
                )
        self.events.rejected(run, result, record_ids)

    async def _emit_pending_handoff(self, run: DetailRunState, reason: str) -> None:
        await run.ctx.emit(
            "persist",
            {
                "fields": run.candidate.get("fields") or {},
                "score": run.candidate.get("score"),
                "subject_match": run.candidate.get("subject_match"),
                "score_reason": run.candidate.get("score_reason") or "",
                "source_url": run.source_url,
                "source_type": "wechat_article",
                "source_archive_status": "pending",
                "source_archive_error": reason,
                "target_id": str((run.target or {}).get("target_id") or ""),
                "target_name": str((run.target or {}).get("canonical_name") or ""),
                "keyword": run.keyword,
                "screenshot_id": run.shot_ids[0] if run.shot_ids else "",
                "screenshot_url": run.shot_urls[0] if run.shot_urls else "",
                "screenshot_ids": run.shot_ids,
                "screenshot_urls": run.shot_urls,
                "discovery_screenshot_ids": run.shot_ids,
                "discovery_screenshot_urls": run.shot_urls,
                "discovery_fields": run.candidate.get("fields") or {},
                "detail": True,
            },
        )

    async def _scroll_mobile_detail(self, run: DetailRunState) -> None:
        previous = self.image_signature(run.shots_b64[-1])
        static_streak = 0
        swipes = 0
        reached_bottom = False
        for index in range(int(run.shared.get("detail_max_swipes", 8))):
            if run.stopped:
                break
            try:
                await asyncio.to_thread(self.swipe_action, run.shared["device_id"])
                await asyncio.sleep(float(run.shared["swipe_interval"]))
                image, screenshot_id, screenshot_url = await self.capture_save(
                    run.ctx,
                    run.keyword,
                    note=f"detail{index + 2} kw={run.keyword}",
                )
                swipes += 1
                run.shots_b64.append(image)
                run.shot_ids.append(screenshot_id)
                run.shot_urls.append(screenshot_url)
                signature = self.image_signature(image)
                static_streak = static_streak + 1 if self.images_similar(previous, signature, threshold=2.5) else 0
                previous = signature
                if static_streak >= 2:
                    reached_bottom = True
                    break
            except Exception:  # noqa: BLE001
                break
        self.events.scroll(run, swipes, reached_bottom)

    async def _analyze_and_emit_mobile_detail(self, run: DetailRunState) -> bool:
        record = await self.detail_analyzer(
            run.shots_b64,
            fields=run.shared["extract_fields"],
            app_name=run.shared["app_name"],
            keyword=run.keyword,
            project_id=run.shared["project_id"],
            task_id=run.shared["run_task_id"],
        )
        if not record:
            return False
        await run.ctx.emit(
            "persist",
            {
                "fields": record["fields"],
                "score": record.get("score"),
                "subject_match": record.get("subject_match") or run.candidate.get("subject_match"),
                "score_reason": record.get("score_reason", ""),
                "source_url": run.source_url or record.get("source_url"),
                "target_id": str((run.target or {}).get("target_id") or ""),
                "target_name": str((run.target or {}).get("canonical_name") or ""),
                "keyword": run.keyword,
                "screenshot_id": run.shot_ids[0] if run.shot_ids else "",
                "screenshot_url": run.shot_urls[0] if run.shot_urls else "",
                "screenshot_ids": run.shot_ids,
                "screenshot_urls": run.shot_urls,
                "discovery_fields": run.candidate.get("fields") or {},
                "detail": True,
            },
        )
        return True

    async def _restore_results_page(self, run: DetailRunState) -> None:
        if run.restored_to_results:
            return
        try:
            await asyncio.to_thread(self.back_action, run.shared["device_id"])
            await asyncio.sleep(0.8)
        except Exception:  # noqa: BLE001
            pass
