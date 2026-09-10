"""Scholar-contact collection adapter for related ProjectTarget entities."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from core.logger import get_logger


logger = get_logger("company_scan.scholar_entities")


@dataclass(frozen=True, slots=True)
class ScholarEntityCollectionRequest:
    task_id: str
    project_id: str
    entities: tuple[dict[str, Any], ...]
    manual_direction: str
    limit: int
    entity_concurrency: int


class ScholarEntityCollectionAdapter:
    """Resolve and collect scholars per related entity with durable coverage."""

    def __init__(self, owner: Any, request: ScholarEntityCollectionRequest) -> None:
        self.owner = owner
        self.request = request
        concurrency = max(1, min(int(request.entity_concurrency), 3))
        self.semaphore = asyncio.Semaphore(concurrency)
        self.progress_lock = asyncio.Lock()
        self.processed = 0

    @property
    def db(self) -> Any:
        return self.owner.db

    async def run(self) -> dict[str, Any]:
        total = len(self.request.entities)
        await self._update_progress(
            0,
            "running",
            f"开始采集 {total} 家子、孙单位的学者联系",
        )
        outcomes = await asyncio.gather(
            *(self._collect_with_progress(entity) for entity in self.request.entities),
            return_exceptions=True,
        )
        collected, errors, summary = self._summarize(outcomes)
        status = self._terminal_status(summary, total)
        await self._update_progress(
            total,
            status,
            (
                "子、孙单位学者联系采集结束 "
                f"完成 {summary['completed']}、部分完成 {summary['partial']}"
                f"、失败 {summary['failed']}"
            ),
            succeeded=summary["completed"],
            failed=summary["failed"],
        )
        return {
            "kind": "scholar_entities",
            "status": status,
            "entities": collected,
            "summary": summary,
            "errors": errors,
        }

    async def _collect_with_progress(
        self,
        entity: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return await self._collect_entity(entity)
        finally:
            async with self.progress_lock:
                self.processed += 1
                total = len(self.request.entities)
                await self._update_progress(
                    self.processed,
                    "completed" if self.processed >= total else "running",
                    f"子、孙单位学者联系已处理 {self.processed}/{total}",
                )

    async def _collect_entity(self, entity: dict[str, Any]) -> dict[str, Any]:
        name, target_id, aliases = self._entity_identity(entity)
        from Sere1nGraph.graph.company_router.router import CompanyRouterResult
        from api.services.scholar_direction import resolve_scholar_direction

        resolution = resolve_scholar_direction(
            self.request.manual_direction,
            CompanyRouterResult(success=False),
            names=aliases,
        )
        try:
            scholar = await self._run_scholar(
                name,
                target_id,
                aliases,
                resolution,
            )
            await self._record_coverage(
                entity,
                target_id,
                self._coverage_status(scholar),
                self._scalar_summary(scholar),
            )
            return {
                "target_id": target_id,
                "name": name,
                "relation_depth": int(entity.get("relation_depth") or 1),
                "parent_target_id": str(entity.get("parent_target_id") or ""),
                "scholar": scholar,
            }
        except Exception as error:
            await self._record_coverage(
                entity,
                target_id,
                "error",
                {"error": str(error)},
            )
            raise

    def _entity_identity(
        self,
        entity: dict[str, Any],
    ) -> tuple[str, str, list[str]]:
        name = str(entity.get("name") or "").strip()
        target_id = str(entity.get("target_id") or "").strip()
        aliases = self.owner._dedupe_text(
            [name, *[str(item) for item in entity.get("aliases") or []]]
        )[:20]
        if not name or not target_id:
            raise ValueError("关联单位缺少名称或 target_id")
        return name, target_id, aliases

    async def _run_scholar(
        self,
        name: str,
        target_id: str,
        aliases: list[str],
        resolution: Any,
    ) -> dict[str, Any]:
        async with self.semaphore:
            return await self.owner._run_scholar_collection(
                task_id=f"{self.request.task_id}_scholar_{target_id}",
                project_id=self.request.project_id,
                target_id=target_id,
                unit=name,
                direction=resolution.direction,
                direction_source=resolution.source,
                unit_en=self.owner._derive_scholar_unit_en(aliases),
                limit=self.request.limit,
            )

    async def _record_coverage(
        self,
        entity: dict[str, Any],
        target_id: str,
        status: str,
        summary: dict[str, Any],
    ) -> None:
        from api.services.target_scan_profile import record_target_scan_coverage

        try:
            await record_target_scan_coverage(
                self.db,
                project_id=self.request.project_id,
                target_id=target_id,
                channel="scholar",
                status=status,
                task_id=self.request.task_id,
                summary=summary,
                profile_fingerprint=str(
                    (entity.get("scan_profile") or {}).get("fingerprint") or ""
                ),
            )
        except Exception as error:
            logger.warning(
                "子单位学者覆盖状态写入失败 | target=%s error=%s",
                target_id,
                error,
            )

    def _summarize(
        self,
        outcomes: list[Any],
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, int]]:
        summary = self._empty_summary(len(self.request.entities))
        collected: list[dict[str, Any]] = []
        errors: list[str] = []
        for index, outcome in enumerate(outcomes):
            if isinstance(outcome, BaseException):
                name = str(self.request.entities[index].get("name") or index)
                errors.append(f"{name}: {outcome}")
                summary["failed"] += 1
                continue
            collected.append(outcome)
            scholar = dict(outcome.get("scholar") or {})
            status = str(scholar.get("status") or "completed").lower()
            if status == "completed":
                summary["completed"] += 1
            elif status == "partial":
                summary["partial"] += 1
            else:
                summary["failed"] += 1
            for field in self._metric_fields():
                summary[field] += int(scholar.get(field) or 0)
        return collected, errors, summary

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

        values: dict[str, Any] = {
            "task_id": self.request.task_id,
            "source": "scholar_entities",
            "total": len(self.request.entities),
            "processed": processed,
            "status": status,
            "message": message,
        }
        if succeeded is not None:
            values["succeeded"] = succeeded
        if failed is not None:
            values["failed"] = failed
        await update_source_progress(self.db, **values)

    @staticmethod
    def _coverage_status(scholar: dict[str, Any]) -> str:
        status = str(scholar.get("status") or "completed").lower()
        if status == "error":
            return "error"
        if status in {"partial", "timed_out", "stopped"}:
            return "partial"
        return "completed"

    @staticmethod
    def _metric_fields() -> tuple[str, ...]:
        return (
            "articles_total",
            "verified_articles_total",
            "unverified_articles_total",
            "contacts_total",
            "corresponding_count",
        )

    @classmethod
    def _empty_summary(cls, total: int) -> dict[str, int]:
        return {
            "entities": total,
            "completed": 0,
            "partial": 0,
            "failed": 0,
            **{field: 0 for field in cls._metric_fields()},
        }

    @staticmethod
    def _terminal_status(summary: dict[str, int], total: int) -> str:
        if summary["completed"] == total:
            return "completed"
        if summary["failed"] == total:
            return "error"
        return "partial"

    @staticmethod
    def _scalar_summary(values: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in values.items()
            if isinstance(value, (str, int, float, bool)) and key != "error"
        }
