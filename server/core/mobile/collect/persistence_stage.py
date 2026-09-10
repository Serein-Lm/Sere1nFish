"""Persistence and notification stages for mobile collection."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from api.dao import findings as findings_dao
from api.dao import mobile_collect as collect_dao
from core.mobile.collect.contacts import (
    build_contact_findings,
    extract_contacts,
    record_text_blob,
)
from core.mobile.collect.score_policy import ScorePolicyRegistry
from core.observability import obs_log
from core.stream import Item, Stage


_OBS_SOURCE = "mobile_collect"


@dataclass(slots=True)
class _PreparedRecord:
    payload: dict[str, Any]
    contacts: list[dict[str, Any]]
    score: int | None
    notification_score: int
    is_high_score: bool


def resolve_payload_contacts(
    payload: dict[str, Any],
    source_url: str | None,
) -> list[dict[str, Any]]:
    """Respect an explicit empty contact list from an authoritative upstream."""
    if "contacts" in payload:
        return [dict(item) for item in payload.get("contacts") or []]
    return extract_contacts(record_text_blob(payload["fields"], source_url))


class PersistStage(Stage):
    name = "persist"
    concurrency = 2

    async def handle(self, item: Item, ctx: Any) -> None:
        state = ctx.state
        prepared = self._prepare(item.payload, state)
        if prepared is None:
            return
        if state.get("dry_run"):
            self._append_preview(state, prepared)
            return
        result = await self._upsert_record(state, prepared)
        await self._wake_pending_handoff(prepared.payload)
        self._update_record_counters(state, prepared, result)
        await self._archive_media(state, prepared.payload, result)
        finding_ids = await self._persist_findings(state, prepared, result)
        await self._reconcile_findings(state, prepared, result, finding_ids)
        await self._emit_notification(ctx, state, prepared, result)

    @staticmethod
    def _prepare(
        payload: dict[str, Any],
        state: dict[str, Any],
    ) -> _PreparedRecord | None:
        source_url = payload.get("source_url")
        contacts = (
            resolve_payload_contacts(payload, source_url)
            if bool(state.get("extract_contact_findings", True))
            else []
        )
        score = ScorePolicyRegistry.resolve(
            str(state.get("score_policy") or "contact_weighted")
        ).score(payload.get("score"), has_contacts=bool(contacts))
        min_persist = int(state.get("min_score_to_persist", 0) or 0)
        if (
            min_persist > 0
            and not payload.get("source_document_id")
            and payload.get("source_archive_status") != "pending"
            and (score or 0) < min_persist
        ):
            return None
        try:
            notification_score = max(int(payload.get("score") or 0), int(score or 0))
        except (TypeError, ValueError):
            notification_score = int(score or 0)
        return _PreparedRecord(
            payload=payload,
            contacts=contacts,
            score=score,
            notification_score=notification_score,
            is_high_score=notification_score
            >= int(state.get("notification_min_score", 60) or 60),
        )

    @staticmethod
    def _append_preview(
        state: dict[str, Any],
        prepared: _PreparedRecord,
    ) -> None:
        payload = prepared.payload
        state["counters"]["total"] += 1
        preview = state["preview"]
        if len(preview) >= state.get("preview_limit", 50):
            return
        preview.append(
            {
                "fields": payload["fields"],
                "score": prepared.score,
                "subject_match": payload.get("subject_match"),
                "score_reason": payload.get("score_reason", ""),
                "source_url": payload.get("source_url"),
                "contacts_count": len(prepared.contacts),
                "detail": bool(payload.get("detail")),
                "keyword": payload["keyword"],
                "screenshot_id": payload["screenshot_id"],
                "screenshot_url": payload["screenshot_url"],
                "source_document_id": payload.get("source_document_id") or "",
                "source_document_version_id": payload.get("source_document_version_id") or "",
                "target_id": payload.get("target_id") or "",
                "target_name": payload.get("target_name") or "",
                "browser_screenshot_urls": payload.get("browser_screenshot_urls") or [],
                "media_count": len(payload.get("media_frames") or []),
                "media": [
                    dict(frame.get("analysis") or {})
                    for frame in payload.get("media_frames") or []
                ],
            }
        )

    @staticmethod
    async def _upsert_record(
        state: dict[str, Any],
        prepared: _PreparedRecord,
    ) -> dict[str, Any]:
        payload = prepared.payload
        shot_ids = [
            str(value)
            for value in payload.get("screenshot_ids") or [payload.get("screenshot_id")]
            if value
        ]
        shot_urls = [
            str(value)
            for value in payload.get("screenshot_urls") or [payload.get("screenshot_url")]
            if value
        ]
        return await collect_dao.upsert_record(
            state["db"],
            task_def_id=state["task_def_id"],
            project_id=state["project_id"],
            fields=payload["fields"],
            dedup_key_fields=state["dedup_key_fields"],
            screenshot_ids=shot_ids,
            screenshot_urls=shot_urls,
            keyword=payload["keyword"],
            run_task_id=state["run_task_id"],
            score=prepared.score,
            subject_match=payload.get("subject_match"),
            source_url=payload.get("source_url"),
            source_document_id=str(payload.get("source_document_id") or ""),
            source_document_version_id=str(
                payload.get("source_document_version_id") or ""
            ),
            target_id=str(payload.get("target_id") or ""),
            target_name=str(payload.get("target_name") or ""),
            browser_screenshot_ids=list(payload.get("browser_screenshot_ids") or []),
            browser_screenshot_urls=list(payload.get("browser_screenshot_urls") or []),
            discovery_screenshot_ids=list(payload.get("discovery_screenshot_ids") or []),
            discovery_screenshot_urls=list(payload.get("discovery_screenshot_urls") or []),
            discovery_fields=payload.get("discovery_fields") or None,
            contact_count=len(prepared.contacts),
            source_archive_status=str(payload.get("source_archive_status") or ""),
            source_archive_error=str(payload.get("source_archive_error") or ""),
        )

    @staticmethod
    async def _wake_pending_handoff(payload: dict[str, Any]) -> None:
        if payload.get("source_archive_status") != "pending":
            return
        from api.services.mobile_source_handoff import (
            wake_mobile_source_handoff_worker,
        )

        wake_mobile_source_handoff_worker()

    @staticmethod
    def _update_record_counters(
        state: dict[str, Any],
        prepared: _PreparedRecord,
        result: dict[str, Any],
    ) -> None:
        counters = state["counters"]
        counters["total"] += 1
        if result["is_new"]:
            counters["new"] += 1
        elif result["is_changed"]:
            counters["changed"] += 1
        if not prepared.is_high_score or not (
            result["is_new"] or result["is_changed"]
        ):
            return
        counters["high_score_records"] += 1
        counters["max_score"] = max(
            counters["max_score"], prepared.notification_score
        )
        if prepared.payload.get("source_document_id"):
            counters["high_score_documents"] += 1

    @staticmethod
    async def _archive_media(
        state: dict[str, Any],
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        frames = list(payload.get("media_frames") or [])
        if not frames:
            return
        from api.services.social_collection.media import archive_social_media_frames

        archived = await archive_social_media_frames(
            state["db"],
            frames=frames,
            job_id=str(state.get("social_collection_job_id") or ""),
            project_id=str(state.get("project_id") or ""),
            target_id=str(payload.get("target_id") or ""),
            platform=str(state.get("platform") or ""),
            place_name=str(
                state.get("collection_subject")
                or payload.get("target_name")
                or payload.get("keyword")
                or ""
            ),
            keyword=str(payload.get("keyword") or ""),
            device_id=str(state.get("device_id") or ""),
            run_task_id=str(state.get("run_task_id") or ""),
            task_def_id=str(state.get("task_def_id") or ""),
            record_id=str(result.get("record_id") or ""),
            candidate_fields=dict(payload.get("candidate_fields") or {}),
        )
        await collect_dao.attach_media_evidence(
            state["db"],
            record_id=str(result.get("record_id") or ""),
            evidence_ids=[
                str(item.get("evidence_id") or "")
                for item in archived.items
                if item.get("evidence_id")
            ],
            storage_object_ids=[
                str(item.get("storage_object_id") or "")
                for item in archived.items
                if item.get("storage_object_id")
            ],
        )
        counters = state["counters"]
        counters["media"] = counters.get("media", 0) + len(archived.items)
        counters["media_failed"] = counters.get("media_failed", 0) + archived.failed_count

    async def _persist_findings(
        self,
        state: dict[str, Any],
        prepared: _PreparedRecord,
        result: dict[str, Any],
    ) -> list[str]:
        project_id = state["project_id"]
        if not project_id or not prepared.contacts:
            return []
        payload = prepared.payload
        record = {
            "fields": payload["fields"],
            "score": prepared.score,
            "source_url": payload.get("source_url"),
            "record_id": result["record_id"],
            "keyword": payload["keyword"],
            "screenshot_url": payload.get("screenshot_url") or "",
            "target_id": payload.get("target_id") or "",
            "target_name": payload.get("target_name") or "",
            "source_type": payload.get("source_type")
            or state.get("record_source_type")
            or "mobile",
            "source_document_id": payload.get("source_document_id") or "",
            "source_document_version_id": payload.get("source_document_version_id") or "",
        }
        findings = build_contact_findings(
            project_id=project_id,
            task_id=state["run_task_id"],
            record=record,
            contacts=prepared.contacts,
        )
        for finding in findings:
            await findings_dao.upsert_contact_finding(state["db"], finding)
        finding_ids = [str(finding["finding_id"]) for finding in findings]
        from api.services.finding_context import schedule_finding_contexts

        schedule_finding_contexts(state["db"], finding_ids)
        state["counters"]["contacts"] = (
            state["counters"].get("contacts", 0) + len(findings)
        )
        self._observe_contacts(state, prepared, result, findings)
        return finding_ids

    @staticmethod
    def _observe_contacts(
        state: dict[str, Any],
        prepared: _PreparedRecord,
        result: dict[str, Any],
        findings: list[dict[str, Any]],
    ) -> None:
        observer = state.get("observer") or obs_log
        observer(
            f"发现联系方式 {len(findings)} 条 kw={prepared.payload['keyword'] or '-'}",
            project_id=state["project_id"],
            task_id=state["run_task_id"],
            source=_OBS_SOURCE,
            level="notice",
            event="collect_contact_found",
            data={
                "keyword": prepared.payload["keyword"],
                "record_id": result["record_id"],
                "contacts": [item["label"] for item in prepared.contacts],
            },
        )

    @staticmethod
    async def _reconcile_findings(
        state: dict[str, Any],
        prepared: _PreparedRecord,
        result: dict[str, Any],
        finding_ids: list[str],
    ) -> None:
        if not state["project_id"] or not bool(
            state.get("extract_contact_findings", True)
        ):
            return
        await findings_dao.reconcile_contact_findings_for_record(
            state["db"],
            project_id=state["project_id"],
            record_id=str(result["record_id"]),
            keep_finding_ids=finding_ids,
        )

    @staticmethod
    async def _emit_notification(
        ctx: Any,
        state: dict[str, Any],
        prepared: _PreparedRecord,
        result: dict[str, Any],
    ) -> None:
        notify_on = state["notify_on"]
        should_notify = (
            result["is_new"] and notify_on in ("new", "both")
        ) or (
            result["is_changed"] and notify_on in ("changed", "both")
        )
        if not should_notify or not prepared.is_high_score:
            return
        await ctx.emit(
            "notify",
            {
                "record_id": result["record_id"],
                "fields": prepared.payload["fields"],
                "score": prepared.notification_score,
                "keyword": prepared.payload["keyword"],
                "kind": "new" if result["is_new"] else "changed",
            },
        )


class NotifyStage(Stage):
    name = "notify"
    concurrency = 2

    async def handle(self, item: Item, ctx: Any) -> None:
        from api.services.notifications import notify_event_background

        state = ctx.state
        payload = item.payload
        label = "新增" if payload["kind"] == "new" else "变更"
        summary = payload["fields"].get("summary") or ", ".join(
            f"{key}={value}"
            for key, value in list(payload["fields"].items())[:5]
        )
        notify_event_background(
            event="mobile_collect_incremental",
            title=f"[采集{label}] {state['task_name']}",
            content=f"关键词: {payload['keyword'] or '-'}\n{summary}",
            level="notice",
            source=_OBS_SOURCE,
            project_id=state["project_id"],
            task_id=state["run_task_id"],
            context={
                "task_def_id": state["task_def_id"],
                "record_id": payload["record_id"],
                "kind": payload["kind"],
            },
        )
