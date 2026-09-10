"""Concurrent collection adapter for wholly owned ProjectTarget entities."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Awaitable

from core.logger import get_logger


logger = get_logger("company_scan.related_entities")


def related_entity_task_id(
    parent_task_id: str,
    *,
    target_id: str,
    name: str,
) -> str:
    identity = str(target_id or name).strip()
    suffix = uuid.uuid5(uuid.NAMESPACE_URL, identity).hex[:12]
    return f"{parent_task_id}_entity_{suffix}"


@dataclass(frozen=True, slots=True)
class RelatedEntityCollectionRequest:
    task_id: str
    project_id: str
    entities: tuple[dict[str, Any], ...]
    enable_asset_discovery: bool
    enable_url_scan: bool
    enable_copywriting: bool
    enable_xhs: bool
    xhs_max_notes: int
    xhs_attention_threshold: int
    min_attention_score: int
    profile_copywriting_threshold: int
    fofa_size: int
    hunter_size: int
    asset_probe_concurrency: int
    incremental_scan: bool
    url_probe_concurrency: int
    url_scan_concurrency: int
    copywriting_concurrency: int
    xhs_search_concurrency: int
    entity_concurrency: int
    enable_bidding: bool
    bidding_page_size: int
    bidding_max_records: int
    bidding_lookback_days: int
    xhs_decisions: dict[str, dict[str, Any]] | None
    website_collection_mode: str


@dataclass(slots=True)
class EntityScanSpec:
    entity: dict[str, Any]
    name: str
    target_id: str
    task_id: str
    progress_key: str
    aliases: list[str]
    channels: set[str]
    identity: dict[str, Any]
    xhs_decision: dict[str, Any] | None
    xhs_enabled: bool
    bidding_enabled: bool
    xhs_keywords: list[str] = field(default_factory=list)


class RelatedEntityCollectionAdapter:
    """Run selected source adapters per entity with bounded concurrency."""

    def __init__(self, owner: Any, request: RelatedEntityCollectionRequest) -> None:
        self.owner = owner
        self.request = request
        self.entity_semaphore = asyncio.Semaphore(max(1, request.entity_concurrency))
        self.bidding_semaphore = asyncio.Semaphore(1)
        self.progress_lock = asyncio.Lock()
        self.processed = 0

    @property
    def db(self) -> Any:
        return self.owner.db

    async def run(self) -> dict[str, Any]:
        total = len(self.request.entities)
        await self._update_progress(0, "running", f"开始采集 {total} 家全资关联单位")
        outcomes = await asyncio.gather(
            *(self._scan_with_progress(entity) for entity in self.request.entities),
            return_exceptions=True,
        )
        output, errors, summary = self._summarize(outcomes)
        status = self._terminal_status(summary, total)
        await self._update_progress(
            total,
            status,
            (
                "全资关联单位采集结束 "
                f"完成 {summary['completed']}、部分完成 {summary['partial']}"
                f"、失败 {summary['failed']}"
            ),
            succeeded=summary["completed"],
            failed=summary["failed"],
        )
        return {
            "kind": "wholly_owned_entities",
            "status": status,
            "entities": output,
            "summary": summary,
            "errors": errors,
        }

    async def _scan_with_progress(self, entity: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self.entity_semaphore:
                return await self._scan_entity(entity)
        finally:
            async with self.progress_lock:
                self.processed += 1
                total = len(self.request.entities)
                await self._update_progress(
                    self.processed,
                    "completed" if self.processed >= total else "running",
                    f"全资关联单位已处理 {self.processed}/{total}",
                )

    async def _scan_entity(self, entity: dict[str, Any]) -> dict[str, Any]:
        spec = self._prepare_entity(entity)
        definitions = self._build_subtasks(spec)
        result = self._initial_scan_result(spec)
        subtasks = [(channel, factory()) for channel, factory in definitions]
        if subtasks:
            await asyncio.gather(
                *(
                    self._record_coverage(spec.entity, channel, "running")
                    for channel, _operation in subtasks
                )
            )
        outcomes = await asyncio.gather(
            *(operation for _channel, operation in subtasks),
            return_exceptions=True,
        )
        xhs_succeeded = False
        for (channel, _operation), outcome in zip(subtasks, outcomes):
            xhs_succeeded = (
                await self._merge_channel_result(result, spec, channel, outcome)
                or xhs_succeeded
            )
        await self._maybe_generate_profile(result, spec, xhs_succeeded)
        result["status"] = self._entity_status(result["channel_statuses"], subtasks)
        return {**entity, "scan": result}

    def _prepare_entity(self, entity: dict[str, Any]) -> EntityScanSpec:
        request = self.request
        name = str(entity.get("name") or "").strip()
        target_id = str(entity.get("target_id") or "")
        task_id = related_entity_task_id(
            request.task_id,
            target_id=target_id,
            name=name,
        )
        scan_profile = dict(entity.get("scan_profile") or {})
        aliases = self.owner._dedupe_text(
            [
                name,
                *list(scan_profile.get("search_aliases") or []),
                *list(entity.get("aliases") or []),
            ]
        )[:20]
        channels = self._scan_channels(entity)
        decision = (request.xhs_decisions or {}).get(target_id)
        xhs_enabled = self._xhs_enabled(target_id, channels, decision)
        return EntityScanSpec(
            entity=entity,
            name=name,
            target_id=target_id,
            task_id=task_id,
            progress_key=task_id.rsplit("_", 1)[-1],
            aliases=aliases,
            channels=channels,
            identity={
                "input_name": name,
                "normalized_name": name,
                "root_domain": str(entity.get("root_domain") or ""),
                "root_domains": list(entity.get("icp_domains") or []),
                "aliases": aliases,
                "target_id": target_id,
            },
            xhs_decision=decision,
            xhs_enabled=xhs_enabled,
            bidding_enabled=bool(
                request.enable_bidding and "bidding" in channels and target_id
            ),
        )

    def _scan_channels(self, entity: dict[str, Any]) -> set[str]:
        request = self.request
        defaults = {
            *(
                ["website"]
                if request.enable_asset_discovery or request.enable_url_scan
                else []
            ),
            *(["bidding"] if request.enable_bidding else []),
            *(["xhs"] if request.enable_xhs else []),
        }
        values = entity.get("scan_channels") if "scan_channels" in entity else defaults
        return {
            str(channel or "").strip().lower()
            for channel in values
            if str(channel or "").strip()
        }

    def _xhs_enabled(
        self,
        target_id: str,
        channels: set[str],
        decision: dict[str, Any] | None,
    ) -> bool:
        request = self.request
        return bool(
            request.enable_xhs
            and "xhs" in channels
            and (
                request.xhs_decisions is None
                or (decision and decision.get("should_collect_xhs"))
            )
        )

    def _build_subtasks(
        self,
        spec: EntityScanSpec,
    ) -> list[tuple[str, Callable[[], Awaitable[dict[str, Any]]]]]:
        request = self.request
        tasks: list[tuple[str, Callable[[], Awaitable[dict[str, Any]]]]] = []
        if "website" in spec.channels and (
            request.enable_asset_discovery or request.enable_url_scan
        ):
            tasks.append(("website", lambda: self._asset_operation(spec)))
        if spec.bidding_enabled:
            tasks.append(("bidding", lambda: self._bidding_operation(spec)))
        if spec.xhs_enabled:
            from api.services.search_terms import build_channel_terms

            spec.xhs_keywords = build_channel_terms(
                channel="xhs",
                names=spec.aliases,
                limit=20,
            )
            tasks.append(("xhs", lambda: self._xhs_operation(spec)))
        return tasks

    def _asset_operation(self, spec: EntityScanSpec) -> Awaitable[dict[str, Any]]:
        request = self.request
        return self.owner._run_asset_and_url_scan(
            task_id=spec.task_id,
            project_id=request.project_id,
            identity=spec.identity,
            url_text="",
            urls=[],
            enable_asset_discovery=request.enable_asset_discovery,
            enable_url_scan=request.enable_url_scan,
            enable_copywriting=request.enable_copywriting,
            min_attention_score=request.min_attention_score,
            fofa_size=request.fofa_size,
            hunter_size=request.hunter_size,
            probe_concurrency=request.asset_probe_concurrency,
            incremental_scan=request.incremental_scan,
            url_probe_concurrency=request.url_probe_concurrency,
            url_scan_concurrency=request.url_scan_concurrency,
            copywriting_concurrency=request.copywriting_concurrency,
            progress_task_id=request.task_id,
            progress_source=f"entity_{spec.progress_key}_url_scan",
            website_collection_mode=request.website_collection_mode,
        )

    async def _bidding_operation(self, spec: EntityScanSpec) -> dict[str, Any]:
        request = self.request
        async with self.bidding_semaphore:
            return await self.owner._run_bidding_collection(
                task_id=spec.task_id,
                project_id=request.project_id,
                company_name=spec.name,
                target_id=spec.target_id,
                page_size=request.bidding_page_size,
                max_records=request.bidding_max_records,
                lookback_days=request.bidding_lookback_days,
                enable_visual_analysis=request.enable_url_scan,
                enable_copywriting=request.enable_copywriting,
                min_attention_score=request.min_attention_score,
                scan_concurrency=request.url_scan_concurrency,
                copywriting_concurrency=request.copywriting_concurrency,
            )

    def _xhs_operation(self, spec: EntityScanSpec) -> Awaitable[dict[str, Any]]:
        request = self.request
        return self.owner._run_xhs_search(
            spec.task_id,
            request.project_id,
            spec.xhs_keywords,
            request.xhs_max_notes,
            request.xhs_attention_threshold,
            target_id=spec.target_id,
            target_name=spec.name,
            search_concurrency=request.xhs_search_concurrency,
        )

    @staticmethod
    def _initial_scan_result(spec: EntityScanSpec) -> dict[str, Any]:
        result = {
            "requested_channels": sorted(spec.channels),
            "channel_statuses": {},
            "assets": {},
            "url_scan": {},
            "xhs": {
                "enabled": spec.xhs_enabled,
                "keywords_used": spec.xhs_keywords,
            },
            "bidding": {
                "enabled": spec.bidding_enabled,
                "status": "pending" if spec.bidding_enabled else "disabled",
            },
            "profile_copywritings": {"count": 0},
            "errors": [],
        }
        if spec.xhs_decision:
            result["xhs"]["selection"] = spec.xhs_decision
        return result

    async def _merge_channel_result(
        self,
        result: dict[str, Any],
        spec: EntityScanSpec,
        channel: str,
        outcome: Any,
    ) -> bool:
        if isinstance(outcome, BaseException):
            result["errors"].append(str(outcome))
            result["channel_statuses"][channel] = "error"
            await self._record_coverage(
                spec.entity,
                channel,
                "error",
                {"error": str(outcome)},
            )
            return False
        from api.services.target_scan_profile import coverage_status_from_result

        status = coverage_status_from_result(channel, outcome)
        result["channel_statuses"][channel] = status
        await self._record_coverage(
            spec.entity,
            channel,
            status,
            self._scalar_summary(outcome),
        )
        self._merge_success(result, outcome)
        return bool(channel == "xhs" and status == "completed")

    @staticmethod
    def _merge_success(result: dict[str, Any], outcome: dict[str, Any]) -> None:
        if outcome.get("kind") == "asset_url":
            result["assets"] = outcome.get("assets") or {}
            result["url_scan"] = outcome.get("url_scan") or {}
        elif outcome.get("kind") == "bidding":
            result["bidding"].update(outcome)
            if outcome.get("status") == "error":
                result["errors"].append(
                    f"招投标采集失败: {outcome.get('error') or '未知错误'}"
                )
        elif "notes_count" in outcome:
            result["xhs"].update(outcome)

    async def _maybe_generate_profile(
        self,
        result: dict[str, Any],
        spec: EntityScanSpec,
        xhs_succeeded: bool,
    ) -> None:
        request = self.request
        if not (spec.xhs_enabled and request.enable_copywriting and xhs_succeeded):
            return
        from Sere1nGraph.graph.company_router.router import CompanyRouterResult

        try:
            result["profile_copywritings"]["count"] = (
                await self.owner._run_profile_copywriting(
                    spec.task_id,
                    request.project_id,
                    spec.name,
                    CompanyRouterResult(success=False),
                    request.profile_copywriting_threshold,
                    target_id=spec.target_id,
                )
            )
        except Exception as error:
            result["errors"].append(f"画像话术生成失败: {error}")

    async def _record_coverage(
        self,
        entity: dict[str, Any],
        channel: str,
        status: str,
        summary: dict[str, Any] | None = None,
    ) -> None:
        target_id = str(entity.get("target_id") or "").strip()
        if not target_id:
            return
        from api.services.target_scan_profile import record_target_scan_coverage

        try:
            await record_target_scan_coverage(
                self.db,
                project_id=self.request.project_id,
                target_id=target_id,
                channel=channel,
                status=status,
                task_id=self.request.task_id,
                summary=summary,
                profile_fingerprint=str(
                    (entity.get("scan_profile") or {}).get("fingerprint") or ""
                ),
            )
        except Exception as error:
            logger.warning(
                "子单位覆盖状态写入失败 | target=%s channel=%s error=%s",
                target_id,
                channel,
                error,
            )

    def _summarize(
        self,
        outcomes: list[Any],
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, int]]:
        output: list[dict[str, Any]] = []
        errors: list[str] = []
        summary = self._empty_summary()
        summary["entities"] = len(self.request.entities)
        for index, outcome in enumerate(outcomes):
            if isinstance(outcome, BaseException):
                entity = self.request.entities[index]
                name = str(entity.get("name") or index)
                errors.append(f"{name}: {outcome}")
                output.append({**entity, "scan": {"errors": [str(outcome)]}})
                summary["failed"] += 1
                continue
            output.append(outcome)
            self._accumulate_summary(summary, outcome)
            errors.extend(
                f"{outcome.get('name')}: {message}"
                for message in (outcome.get("scan") or {}).get("errors") or []
            )
        return output, errors, summary

    @staticmethod
    def _empty_summary() -> dict[str, int]:
        return {
            "entities": 0,
            "completed": 0,
            "partial": 0,
            "failed": 0,
            "assets_discovered": 0,
            "assets_alive": 0,
            "url_findings": 0,
            "xhs_notes": 0,
            "xhs_profiles": 0,
            "profile_copywritings": 0,
            "bidding_records": 0,
            "bidding_findings": 0,
            "bidding_attachments": 0,
        }

    def _accumulate_summary(
        self,
        summary: dict[str, int],
        outcome: dict[str, Any],
    ) -> None:
        scan = dict(outcome.get("scan") or {})
        status = str(scan.get("status") or "error").lower()
        summary[
            "completed" if status in {"completed", "skipped"} else status
            if status == "partial"
            else "failed"
        ] += 1
        assets = dict(scan.get("assets") or {})
        url = dict(scan.get("url_scan") or {})
        xhs = dict(scan.get("xhs") or {})
        copywritings = dict(scan.get("profile_copywritings") or {})
        bidding = dict(scan.get("bidding") or {})
        summary["assets_discovered"] += int(assets.get("discovered") or 0)
        summary["assets_alive"] += int(assets.get("alive") or 0)
        summary["url_findings"] += int(url.get("findings_count") or 0)
        summary["xhs_notes"] += int(xhs.get("notes_count") or 0)
        summary["xhs_profiles"] += int(xhs.get("profiles_count") or 0)
        summary["profile_copywritings"] += int(copywritings.get("count") or 0)
        summary["bidding_records"] += int(bidding.get("records_fetched") or 0)
        summary["bidding_findings"] += int(
            (bidding.get("visual_analysis") or {}).get("findings_count") or 0
        )
        summary["bidding_attachments"] += int(
            bidding.get("attachments_archived") or 0
        )

    async def _update_progress(
        self,
        processed: int,
        status: str,
        message: str,
        *,
        succeeded: int | None = None,
        failed: int | None = None,
    ) -> None:
        from api.services.task_progress import update_source_progress

        kwargs: dict[str, Any] = {
            "task_id": self.request.task_id,
            "source": "wholly_owned_entities",
            "total": len(self.request.entities),
            "processed": processed,
            "status": status,
            "message": message,
        }
        if succeeded is not None:
            kwargs["succeeded"] = succeeded
        if failed is not None:
            kwargs["failed"] = failed
        await update_source_progress(self.db, **kwargs)

    @staticmethod
    def _entity_status(
        statuses: dict[str, str],
        subtasks: list[tuple[str, Any]],
    ) -> str:
        values = set(statuses.values())
        if not subtasks:
            return "skipped"
        if values.issubset({"completed", "skipped"}):
            return "completed"
        if values.intersection({"completed", "partial", "skipped"}):
            return "partial"
        return "error"

    @staticmethod
    def _terminal_status(summary: dict[str, int], total: int) -> str:
        if summary["completed"] == total:
            return "completed"
        if summary["failed"] == total:
            return "error"
        return "partial"

    @staticmethod
    def _scalar_summary(outcome: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in outcome.items()
            if isinstance(value, (str, int, float, bool)) and key != "error"
        }
