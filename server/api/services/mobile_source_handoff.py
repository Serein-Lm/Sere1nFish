"""Durable recovery for phone-discovered source URLs.

The phone is responsible only for discovering a real source URL. This worker
retries browser capture and source analysis independently so a model or browser
outage never forces the phone to open and scroll the same article again.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import findings as findings_dao
from api.dao import mobile_collect as collect_dao
from api.db.collections import MOBILE_COLLECT_TASKS_COLLECTION, TARGETS_COLLECTION
from api.models.mobile_collect import ExtractField
from api.services.source_documents import ingest_source_url
from core.background import spawn_background
from core.logger import get_logger
from core.mobile.collect.contacts import build_contact_findings


logger = get_logger("mobile_source_handoff")
_POLL_SECONDS = 30
_LEASE_SECONDS = 900


def _retry_delay(record: dict[str, Any], error: BaseException) -> int:
    message = str(error).casefold()
    try:
        attempts = max(1, int(record.get("source_archive_attempts") or 1))
    except (TypeError, ValueError):
        attempts = 1
    if any(
        marker in message
        for marker in ("insufficient_quota", "额度", "quota", "限流", "429")
    ):
        return min(1800, max(120, 120 * attempts))
    return min(900, max(30, 30 * (2 ** min(attempts - 1, 5))))


def _project_id(record: dict[str, Any]) -> str:
    direct = str(record.get("project_id") or "")
    if direct:
        return direct
    return next(
        (
            str(value)
            for value in record.get("project_ids") or []
            if str(value).strip()
        ),
        "",
    )


def _extract_fields(task_def: dict[str, Any]) -> list[ExtractField]:
    fields: list[ExtractField] = []
    for value in task_def.get("extract_fields") or []:
        try:
            fields.append(ExtractField.model_validate(value))
        except (TypeError, ValueError):
            continue
    return fields


async def process_mobile_source_handoff(
    db: AsyncIOMotorDatabase,
    record: dict[str, Any],
    *,
    worker_id: str,
) -> bool:
    """Archive one claimed record and project its verified contacts."""
    record_id = str(record.get("record_id") or "")
    task_def_id = str(record.get("task_def_id") or "")
    project_id = _project_id(record)
    source_url = str(record.get("source_url") or "")
    target_id = str(record.get("target_id") or "")
    run_task_id = str(record.get("latest_run_task_id") or "")
    task_def = await db[MOBILE_COLLECT_TASKS_COLLECTION].find_one(
        {"task_def_id": task_def_id},
        {"_id": 0},
    )
    target = None
    if target_id:
        target = await db[TARGETS_COLLECTION].find_one(
            {"target_id": target_id},
            {"_id": 0},
        )
    if target is None and record.get("target_name"):
        target = {
            "target_id": target_id,
            "canonical_name": str(record.get("target_name") or ""),
            "aliases": [],
        }
    if not task_def or not target or not project_id or not source_url:
        missing = ",".join(
            name
            for name, value in (
                ("task_def", task_def),
                ("target", target),
                ("project_id", project_id),
                ("source_url", source_url),
            )
            if not value
        )
        await collect_dao.defer_source_handoff(
            db,
            record_id=record_id,
            worker_id=worker_id,
            error=f"待归档记录缺少恢复上下文: {missing}",
            retry_after_seconds=900,
        )
        return False

    try:
        result = await ingest_source_url(
            db,
            url=source_url,
            project_id=project_id,
            target=target,
            task_def_id=task_def_id,
            run_task_id=run_task_id or f"source_handoff_{record_id}",
            keyword=str(record.get("keyword") or ""),
            extract_fields=_extract_fields(task_def),
            discovery_score=record.get("score"),
            discovery_subject_match=record.get("subject_match"),
            discovery_context={
                "source": "mobile_source_handoff_recovery",
                "record_id": record_id,
                "candidate_fields": dict(
                    record.get("discovery_fields") or record.get("fields") or {}
                ),
            },
            persist=True,
            min_subject_match=int(task_def.get("min_subject_match") or 70),
        )
        if not result.get("ok"):
            if result.get("rejected"):
                reason = str(
                    result.get("score_reason")
                    or result.get("reason")
                    or "独立相关性审核拒绝"
                )
                await collect_dao.reject_source_handoff(
                    db,
                    record_id=record_id,
                    worker_id=worker_id,
                    reason=reason,
                    source_document_id=str(result.get("document_id") or ""),
                )
                if project_id:
                    await findings_dao.reconcile_contact_findings_for_record(
                        db,
                        project_id=project_id,
                        record_id=record_id,
                        keep_finding_ids=[],
                    )
                logger.info(
                    "手机来源补录被相关性审核拒绝 | project=%s target=%s record=%s",
                    project_id,
                    target_id,
                    record_id,
                )
                return False
            raise RuntimeError(
                str(result.get("reason") or "来源文档归档未完成")
            )

        discovery_fields = dict(
            record.get("discovery_fields") or record.get("fields") or {}
        )
        fields = {**discovery_fields, **dict(result.get("fields") or {})}
        contacts = list(result.get("contacts") or [])
        stored = await collect_dao.upsert_record(
            db,
            task_def_id=task_def_id,
            project_id=project_id,
            fields=fields,
            dedup_key_fields=list(task_def.get("dedup_key_fields") or []),
            screenshot_ids=list(record.get("screenshot_ids") or []),
            screenshot_urls=list(record.get("screenshot_urls") or []),
            keyword=str(record.get("keyword") or ""),
            run_task_id=run_task_id,
            score=result.get("score"),
            subject_match=result.get("subject_match"),
            source_url=str(result.get("source_url") or source_url),
            source_document_id=str(result.get("document_id") or ""),
            source_document_version_id=str(result.get("version_id") or ""),
            target_id=str(result.get("target_id") or target_id),
            target_name=str(
                result.get("target_name") or target.get("canonical_name") or ""
            ),
            browser_screenshot_ids=list(
                result.get("browser_screenshot_ids") or []
            ),
            browser_screenshot_urls=list(
                result.get("browser_screenshot_urls") or []
            ),
            discovery_screenshot_ids=list(
                record.get("discovery_screenshot_ids")
                or record.get("screenshot_ids")
                or []
            ),
            discovery_screenshot_urls=list(
                record.get("discovery_screenshot_urls")
                or record.get("screenshot_urls")
                or []
            ),
            discovery_fields=discovery_fields,
            contact_count=len(contacts),
            source_archive_status="ready",
        )
        canonical_record_id = str(stored.get("record_id") or record_id)
        finding_ids: list[str] = []
        if bool(task_def.get("extract_contact_findings", True)):
            finding_record = {
                "fields": fields,
                "score": result.get("score"),
                "source_url": result.get("source_url") or source_url,
                "record_id": canonical_record_id,
                "keyword": record.get("keyword") or "",
                "screenshot_url": next(
                    iter(record.get("screenshot_urls") or []), ""
                ),
                "target_id": result.get("target_id") or target_id,
                "target_name": result.get("target_name")
                or target.get("canonical_name")
                or "",
                "source_type": result.get("source_type") or "wechat_article",
                "source_document_id": result.get("document_id") or "",
                "source_document_version_id": result.get("version_id") or "",
            }
            findings = build_contact_findings(
                project_id=project_id,
                task_id=run_task_id or f"source_handoff_{record_id}",
                record=finding_record,
                contacts=contacts,
            )
            for finding in findings:
                await findings_dao.upsert_contact_finding(db, finding)
            finding_ids = [str(finding["finding_id"]) for finding in findings]
            await findings_dao.reconcile_contact_findings_for_record(
                db,
                project_id=project_id,
                record_id=canonical_record_id,
                keep_finding_ids=finding_ids,
            )
            if finding_ids:
                from api.services.finding_context import schedule_finding_contexts

                schedule_finding_contexts(db, finding_ids)

        logger.notice(
            "手机来源补录完成 | project=%s target=%s record=%s document=%s findings=%s",
            project_id,
            target_id,
            canonical_record_id,
            result.get("document_id"),
            len(finding_ids),
        )
        return True
    except asyncio.CancelledError:
        await collect_dao.defer_source_handoff(
            db,
            record_id=record_id,
            worker_id=worker_id,
            error="服务重载中断，等待恢复",
            retry_after_seconds=15,
        )
        raise
    except Exception as exc:  # noqa: BLE001
        delay = _retry_delay(record, exc)
        await collect_dao.defer_source_handoff(
            db,
            record_id=record_id,
            worker_id=worker_id,
            error=str(exc),
            retry_after_seconds=delay,
        )
        logger.warning(
            "手机来源补录失败，已退避 | project=%s target=%s record=%s retry=%ss error=%s",
            project_id,
            target_id,
            record_id,
            delay,
            str(exc)[:500],
        )
        return False


class MobileSourceHandoffWorker:
    """Singleton worker for durable phone-to-browser handoff recovery."""

    _instance: "MobileSourceHandoffWorker | None" = None

    def __init__(self) -> None:
        self._task: asyncio.Task[Any] | None = None
        self._wakeup = asyncio.Event()
        self._db: AsyncIOMotorDatabase | None = None
        self._worker_id = f"mobile-source-{uuid.uuid4().hex[:12]}"

    @classmethod
    def get_instance(cls) -> "MobileSourceHandoffWorker":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start(self, db: AsyncIOMotorDatabase) -> None:
        self._db = db
        self._wakeup.set()
        if self._task is None or self._task.done():
            self._task = spawn_background(
                self._run(),
                name="mobile-source-handoff-worker",
            )

    def wake(self) -> None:
        self._wakeup.set()

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._db = None

    async def _wait(self) -> None:
        self._wakeup.clear()
        try:
            await asyncio.wait_for(self._wakeup.wait(), timeout=_POLL_SECONDS)
        except asyncio.TimeoutError:
            pass

    async def _run(self) -> None:
        assert self._db is not None
        while True:
            try:
                record = await collect_dao.claim_pending_source_handoff(
                    self._db,
                    worker_id=self._worker_id,
                    lease_seconds=_LEASE_SECONDS,
                )
                if record is None:
                    await self._wait()
                    continue
                await process_mobile_source_handoff(
                    self._db,
                    record,
                    worker_id=self._worker_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("手机来源补录 worker 循环异常: %s", exc)
                await self._wait()


def wake_mobile_source_handoff_worker() -> None:
    MobileSourceHandoffWorker.get_instance().wake()
