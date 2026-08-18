"""Target-aware relevance gate for procurement provider results."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import bidding as bidding_dao
from api.dao import targets as targets_dao
from api.db.collections import (
    BIDDING_RECORD_LINKS_COLLECTION,
    BIDDING_RECORDS_COLLECTION,
    TARGETS_COLLECTION,
)


_GENERIC_ALIASES = frozenset(
    {
        "公司",
        "集团",
        "有限公司",
        "股份有限公司",
        "机场",
        "中心",
        "管理中心",
        "集团公司",
    }
)
_TEXT_FIELDS = (
    "title",
    "procurement_title",
    "purchaser",
    "agency",
    "winner",
    "summary",
    "introduction",
    "content_html",
    "content_preview",
    "detail_text_preview",
)
_URL_FIELDS = ("detail_url", "resolved_detail_url", "provider_url")
_ALIAS_FIELDS = (
    "canonical_name",
    "display_name",
    "normalized_name",
    "aliases",
    "identity_aliases",
    "scan_aliases",
    "short_names",
)
_ASCII_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class BiddingRelevanceDecision:
    relevant: bool
    reason: str
    matched_aliases: tuple[str, ...] = ()
    matched_domains: tuple[str, ...] = ()


def _record_value(record: Any, field: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(field)
    return getattr(record, field, None)


def _flatten_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [str(item) for item in value.values() if item]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [str(item) for item in value if item]
    return [str(value)]


def _trusted_aliases(
    *,
    company_name: str,
    target: Mapping[str, Any] | None,
) -> list[str]:
    values = [company_name]
    target_doc = target or {}
    for field in _ALIAS_FIELDS:
        values.extend(_flatten_text(target_doc.get(field)))
    profile = target_doc.get("scan_profile")
    if isinstance(profile, Mapping):
        for field in ("canonical_name", "display_name", "short_names", "search_aliases"):
            values.extend(_flatten_text(profile.get(field)))

    aliases: list[str] = []
    seen: set[str] = set()
    for value in values:
        display = str(value or "").strip()
        normalized = targets_dao.normalize_target_name(display)
        if (
            not normalized
            or normalized in _GENERIC_ALIASES
            or len(normalized) < 3
            or normalized in seen
        ):
            continue
        seen.add(normalized)
        aliases.append(display)
    return sorted(
        aliases,
        key=lambda item: len(targets_dao.normalize_target_name(item)),
        reverse=True,
    )


def _alias_matches_text(alias: str, *, raw_text: str, normalized_text: str) -> bool:
    normalized = targets_dao.normalize_target_name(alias)
    if not normalized:
        return False
    if not _ASCII_IDENTIFIER_RE.fullmatch(normalized):
        return normalized in normalized_text

    tokens = _ASCII_TOKEN_RE.findall(str(alias or "").casefold())
    if not tokens:
        return False
    pattern = r"[\s._-]*".join(re.escape(token) for token in tokens)
    return bool(
        re.search(
            rf"(?<![a-z0-9]){pattern}(?![a-z0-9])",
            raw_text,
            re.IGNORECASE,
        )
    )


def _target_domains(target: Mapping[str, Any] | None) -> list[str]:
    target_doc = target or {}
    values = [target_doc.get("root_domain")]
    values.extend(_flatten_text(target_doc.get("root_domains")))
    domains: list[str] = []
    for value in values:
        raw = str(value or "").strip().casefold()
        if not raw:
            continue
        candidate = raw if "://" in raw else f"https://{raw}"
        try:
            hostname = (urlsplit(candidate).hostname or "").strip(".")
        except ValueError:
            continue
        if hostname.startswith("www."):
            hostname = hostname[4:]
        if hostname and hostname not in domains:
            domains.append(hostname)
    return domains


def assess_bidding_relevance(
    record: Any,
    *,
    company_name: str,
    target: Mapping[str, Any] | None = None,
) -> BiddingRelevanceDecision:
    """Require evidence inside the announcement, not merely a provider hit."""
    text_values = [str(_record_value(record, field) or "") for field in _TEXT_FIELDS]
    raw_text = "\n".join(text_values).casefold()
    normalized_text = targets_dao.normalize_target_name(raw_text)

    matched_aliases: list[str] = []
    for alias in _trusted_aliases(company_name=company_name, target=target):
        if _alias_matches_text(
            alias,
            raw_text=raw_text,
            normalized_text=normalized_text,
        ):
            matched_aliases.append(alias)

    target_domains = _target_domains(target)
    matched_domains: list[str] = []
    for field in _URL_FIELDS:
        raw_url = str(_record_value(record, field) or "").strip()
        if not raw_url:
            continue
        try:
            hostname = (urlsplit(raw_url).hostname or "").strip(".").casefold()
        except ValueError:
            continue
        for domain in target_domains:
            if hostname == domain or hostname.endswith(f".{domain}"):
                matched_domains.append(domain)

    if matched_aliases:
        return BiddingRelevanceDecision(
            relevant=True,
            reason="target_alias_match",
            matched_aliases=tuple(dict.fromkeys(matched_aliases)),
        )
    if matched_domains:
        return BiddingRelevanceDecision(
            relevant=True,
            reason="target_domain_match",
            matched_domains=tuple(dict.fromkeys(matched_domains)),
        )
    return BiddingRelevanceDecision(
        relevant=False,
        reason="no_target_evidence_in_announcement",
    )


def filter_bidding_records(
    records: Sequence[Any],
    *,
    company_name: str,
    target: Mapping[str, Any] | None = None,
) -> tuple[list[Any], list[tuple[Any, BiddingRelevanceDecision]]]:
    accepted: list[Any] = []
    rejected: list[tuple[Any, BiddingRelevanceDecision]] = []
    for record in records:
        decision = assess_bidding_relevance(
            record,
            company_name=company_name,
            target=target,
        )
        if decision.relevant:
            accepted.append(record)
        else:
            rejected.append((record, decision))
    return accepted, rejected


async def reconcile_project_bidding_links(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    apply: bool = False,
) -> dict[str, Any]:
    """Preview or remove project links that fail the stable relevance gate."""
    links = await db[BIDDING_RECORD_LINKS_COLLECTION].find(
        {"project_id": project_id},
        {"_id": 0},
    ).to_list(length=None)
    record_ids = list(
        dict.fromkeys(
            str(item.get("record_id") or "")
            for item in links
            if item.get("record_id")
        )
    )
    target_ids = list(
        dict.fromkeys(
            str(item.get("target_id") or "")
            for item in links
            if item.get("target_id")
        )
    )
    records = {
        str(item.get("record_id") or ""): item
        for item in await db[BIDDING_RECORDS_COLLECTION].find(
            {"record_id": {"$in": record_ids}},
            {"_id": 0},
        ).to_list(length=None)
    }
    targets = {
        str(item.get("target_id") or ""): item
        for item in await db[TARGETS_COLLECTION].find(
            {"target_id": {"$in": target_ids}},
            {"_id": 0},
        ).to_list(length=None)
    }

    rejected_link_ids: list[str] = []
    rejected_by_target: dict[str, int] = {}
    for link in links:
        record = records.get(str(link.get("record_id") or ""))
        if not record:
            continue
        target_id = str(link.get("target_id") or "")
        company_name = str(
            (targets.get(target_id) or {}).get("canonical_name")
            or link.get("latest_query_name")
            or ""
        )
        decision = assess_bidding_relevance(
            record,
            company_name=company_name,
            target=targets.get(target_id),
        )
        if decision.relevant:
            continue
        rejected_link_ids.append(str(link.get("link_id") or ""))
        rejected_by_target[target_id] = rejected_by_target.get(target_id, 0) + 1

    rejected_link_ids = [value for value in rejected_link_ids if value]
    deleted = 0
    if apply:
        deleted = await bidding_dao.remove_record_links(db, rejected_link_ids)
    return {
        "project_id": project_id,
        "applied": apply,
        "reviewed": len(links),
        "retained": len(links) - len(rejected_link_ids),
        "rejected": len(rejected_link_ids),
        "deleted": deleted,
        "rejected_by_target": rejected_by_target,
    }
