"""Project bidding read model with actionable contact aggregation."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import bidding as bidding_dao
from api.db.collections import FINDINGS_COLLECTION
from api.utils.url_identity import endpoint_identity


_SPACE_RE = re.compile(r"\s+")
_EXCLUDED_ROLES = {"customer_service", "support"}
_EXCLUDED_TYPES = {"customer_service"}
_EXCLUDED_PARTY_ROLES = {"publisher"}
_ACTIONABLE_CHANNELS = {"email", "phone", "wechat"}
_PUBLIC_RECORD_FIELDS = (
    "record_id",
    "provider",
    "title",
    "announcement_type",
    "stage",
    "published_on",
    "province",
    "purchaser",
    "agency",
    "amount",
    "winner",
    "enterprise_identity",
    "provider_url",
    "content_length",
    "provider_payload_url",
    "raw_content_url",
    "detail_html_url",
    "query_names",
    "target_ids",
    "updated_at",
)
_PUBLIC_CONTACT_FIELDS = (
    "finding_id",
    "channel",
    "value",
    "label",
    "party_name",
    "party_role",
    "role",
    "context",
    "evidence",
    "attention_score",
)
_PUBLIC_ATTACHMENT_FIELDS = (
    "index",
    "status",
    "filename",
    "label",
    "url",
    "content_type",
    "size",
)


def is_actionable_bidding_contact(finding: dict[str, Any]) -> bool:
    """Keep participant contacts while dropping platform support information."""
    value = str(finding.get("value") or "").strip()
    if not value:
        return False
    if str(finding.get("channel") or "") not in _ACTIONABLE_CHANNELS:
        return False
    if str(finding.get("role") or "") in _EXCLUDED_ROLES:
        return False
    if str(finding.get("type") or "") in _EXCLUDED_TYPES:
        return False
    if str(finding.get("party_role") or "") in _EXCLUDED_PARTY_ROLES:
        return False
    return True


def _record_urls(record: dict[str, Any]) -> set[str]:
    return {
        identity
        for value in (
            record.get("resolved_detail_url"),
            record.get("detail_url"),
            record.get("provider_url"),
        )
        if (
            identity := endpoint_identity(
                str(value or ""),
                include_query=True,
            )
        )
    }


def _overview(record: dict[str, Any]) -> str:
    for field in (
        "introduction",
        "summary",
        "content_preview",
        "detail_text_preview",
    ):
        value = _SPACE_RE.sub(" ", str(record.get(field) or "")).strip()
        if value:
            if len(value) <= 320:
                return value
            boundary = max(value.rfind(mark, 120, 320) for mark in ("。", "；", ";"))
            return value[: boundary + 1 if boundary >= 120 else 320].rstrip() + "..."
    return ""


def _compact_text(value: Any, *, limit: int) -> str:
    text = _SPACE_RE.sub(" ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _public_bidding_record(
    record: dict[str, Any],
    *,
    contacts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the project read model without leaking archived evidence payloads."""
    public = {
        field: record[field]
        for field in _PUBLIC_RECORD_FIELDS
        if field in record
    }
    public["attachments"] = [
        {
            field: attachment[field]
            for field in _PUBLIC_ATTACHMENT_FIELDS
            if field in attachment
        }
        for attachment in record.get("attachments") or []
        if isinstance(attachment, dict)
    ]
    public["contacts"] = [
        {
            field: contact[field]
            for field in _PUBLIC_CONTACT_FIELDS
            if field in contact
        }
        for contact in contacts
    ]
    public["contact_count"] = len(contacts)
    public["overview"] = _overview(record)
    public["original_url"] = str(
        record.get("resolved_detail_url")
        or record.get("detail_url")
        or record.get("provider_url")
        or ""
    )
    public["max_contact_score"] = max(
        int(item.get("attention_score") or 0)
        for item in contacts
    )
    preview = record.get("content_preview") or record.get("detail_text_preview")
    if preview:
        public["content_preview"] = _compact_text(preview, limit=2_000)
    return public


async def list_project_bidding_records(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str = "",
    limit: int = 20,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return only announcements that have usable participant contacts."""
    records, _ = await bidding_dao.query_records(
        db,
        project_id=project_id,
        target_id=target_id,
        limit=5_000,
        skip=0,
    )
    record_ids = [str(record.get("record_id") or "") for record in records]
    finding_query: dict[str, Any] = {
        "project_id": project_id,
        "source": "bidding",
        "$or": [
            {"bidding_record_id": {"$in": record_ids}},
            {"bidding_record_id": {"$exists": False}},
            {"bidding_record_id": ""},
        ],
    }
    if target_id:
        finding_query["target_id"] = target_id
    findings = await db[FINDINGS_COLLECTION].find(
        finding_query,
        {"_id": 0},
    ).to_list(None)
    by_record_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_endpoint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        if not is_actionable_bidding_contact(finding):
            continue
        record_id = str(finding.get("bidding_record_id") or "")
        if record_id:
            by_record_id[record_id].append(finding)
        if key := endpoint_identity(
            str(finding.get("source_url") or finding.get("url") or ""),
            include_query=True,
        ):
            by_endpoint[key].append(finding)

    output: list[dict[str, Any]] = []
    for record in records:
        record_id = str(record.get("record_id") or "")
        contacts = list(by_record_id.get(record_id, []))
        if not contacts:
            for key in _record_urls(record):
                contacts.extend(by_endpoint.get(key, []))
        deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for contact in contacts:
            key = (
                str(contact.get("channel") or ""),
                str(contact.get("value") or "").casefold(),
                str(contact.get("party_name") or "").casefold(),
            )
            previous = deduped.get(key)
            if previous is None or int(contact.get("attention_score") or 0) > int(
                previous.get("attention_score") or 0
            ):
                deduped[key] = contact
        ordered_contacts = sorted(
            deduped.values(),
            key=lambda item: int(item.get("attention_score") or 0),
            reverse=True,
        )
        if not ordered_contacts:
            continue
        output.append(_public_bidding_record(record, contacts=ordered_contacts))
    output.sort(
        key=lambda item: (
            int(item.get("max_contact_score") or 0),
            str(item.get("published_on") or ""),
            str(item.get("updated_at") or ""),
        ),
        reverse=True,
    )
    bounded_skip = max(0, int(skip or 0))
    # HTTP API caps page_size at 100; the wider internal bound lets project
    # summaries reuse this exact read model instead of maintaining a second
    # definition of an actionable bidding record.
    bounded_limit = max(1, min(int(limit or 20), 5_000))
    return output[bounded_skip : bounded_skip + bounded_limit], len(output)


async def count_project_bidding_records_by_target(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_ids: list[str],
) -> dict[str, int]:
    """Count contact-bearing announcements per Target using the list read model."""
    selected = {str(target_id or "").strip() for target_id in target_ids}
    selected.discard("")
    if not selected:
        return {}
    records, _ = await list_project_bidding_records(
        db,
        project_id=project_id,
        limit=5_000,
    )
    counts = {target_id: 0 for target_id in selected}
    for record in records:
        for target_id in set(str(value or "") for value in record.get("target_ids") or []):
            if target_id in counts:
                counts[target_id] += 1
    return counts
