"""Target 领域服务：统一解析公司/机构实体并建立项目关联。"""
from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlsplit

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import projects as projects_dao
from api.dao import findings as findings_dao
from api.dao import mobile_collect as mobile_collect_dao
from api.dao import scholar_contact as scholar_dao
from api.dao import target_relationships as target_relationships_dao
from api.dao import targets as targets_dao
from api.dao.project_scope import project_scope_query
from api.db.collections import (
    COPYWRITINGS_COLLECTION,
    FINDINGS_COLLECTION,
    FOFA_ASSETS_COLLECTION,
    MOBILE_COLLECT_RECORDS_COLLECTION,
    PROFILES_COLLECTION,
    SOURCE_DOCUMENT_LINKS_COLLECTION,
    TARGETS_COLLECTION,
    TASKS_COLLECTION,
    URL_SCAN_RESULTS_COLLECTION,
    WEB_TAGS_COLLECTION,
    XHS_NOTES_COLLECTION,
)
from api.utils.url_identity import endpoint_identity, prefer_https_url


_HIGH_SCORE_SOURCE_KEYS = (
    "website",
    "xiaohongshu",
    "wechat",
    "bidding",
    "scholars",
    "other",
)

_FINDING_SOURCE_MODULES = {
    "web_tagging": "website",
    "website": "website",
    "xhs": "xiaohongshu",
    "xhs_profile": "xiaohongshu",
    "wechat": "wechat",
    "wechat_article": "wechat",
    "bidding": "bidding",
    "bidding_url_scan": "bidding",
    "scholar": "scholars",
    "scholar_contact": "scholars",
}

_BATCH_PRIORITY_PATTERN = re.compile(
    r"^第(?P<level>\d+|[一二三四五六七八九十]+)等级$"
)
_CHINESE_PRIORITY_DIGITS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_UNRANKED_BATCH_PRIORITY = 1_000_000

_TARGET_MODULE_LABELS = {
    "website": "网站",
    "xiaohongshu": "小红书",
    "wechat": "公众号",
    "bidding": "招投标",
    "scholars": "学者联系",
    "other": "其他",
}

_ORGANIZATION_HIERARCHY_RELATION_TYPES = {
    "parent_organization",
    "controlled_subsidiary",
}


def _empty_high_score_breakdown() -> dict[str, int]:
    return {key: 0 for key in _HIGH_SCORE_SOURCE_KEYS}


def _task_collection_status(task: dict[str, Any]) -> str:
    """Expose terminal tasks with incomplete collection as partial to dashboards."""
    task_status = str(task.get("status") or "")
    result = task.get("result")
    result_status = str(
        task.get("result_status")
        or (result.get("status") if isinstance(result, dict) else "")
        or ""
    )
    if task_status == "completed" and result_status == "partial":
        return "partial"
    return task_status


def _parse_priority_level(value: str) -> int | None:
    if value.isdigit():
        level = int(value)
        return level if level > 0 else None
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = _CHINESE_PRIORITY_DIGITS.get(left, 1) if left else 1
        units = _CHINESE_PRIORITY_DIGITS.get(right, 0) if right else 0
        return tens * 10 + units if tens and units >= 0 else None
    return _CHINESE_PRIORITY_DIGITS.get(value)


def _target_batch_priority(
    batch_tags: list[str] | tuple[str, ...] | str | None,
) -> dict[str, Any]:
    """Resolve business priority from level tags without coupling callers to labels."""
    tags = targets_dao.normalize_batch_tags(batch_tags)
    ranked: list[tuple[int, str]] = []
    for tag in tags:
        match = _BATCH_PRIORITY_PATTERN.fullmatch(tag)
        if not match:
            continue
        level = _parse_priority_level(match.group("level"))
        if level is not None:
            ranked.append((level, tag))
    rank, label = min(ranked, default=(_UNRANKED_BATCH_PRIORITY, ""))
    return {
        "batch_priority_rank": rank if label else None,
        "batch_priority_label": label,
        "is_expanded_target": "拓展目标" in tags,
    }


def _target_batch_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    priority = _target_batch_priority(item.get("batch_tags"))
    rank = priority["batch_priority_rank"]
    return (
        int(rank) if rank is not None else _UNRANKED_BATCH_PRIORITY,
        int(bool(priority["is_expanded_target"])),
        str(priority["batch_priority_label"]),
    )


def _summarize_finding_counts(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Fold Target/source aggregation rows into the stable Target summary shape."""
    summaries: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = row.get("_id")
        if isinstance(identity, dict):
            target_id = str(identity.get("target_id") or "")
            source = str(identity.get("source") or "").strip().lower()
        else:
            target_id = str(identity or "")
            source = ""
        if not target_id:
            continue

        summary = summaries.setdefault(
            target_id,
            {
                "finding_count": 0,
                "high_score_finding_count": 0,
                "high_score_by_source": _empty_high_score_breakdown(),
            },
        )
        finding_count = int(row.get("finding_count") or 0)
        high_score_count = int(row.get("high_score_count") or 0)
        summary["finding_count"] += finding_count
        summary["high_score_finding_count"] += high_score_count
        source_key = _FINDING_SOURCE_MODULES.get(source, "other")
        summary["high_score_by_source"][source_key] += high_score_count
    return summaries


def _finding_module(source: Any) -> str:
    return _FINDING_SOURCE_MODULES.get(
        str(source or "").strip().lower(),
        "other",
    )


def _finding_source_url(item: dict[str, Any]) -> str:
    latest_evidence = item.get("latest_evidence_ref") or {}
    grouped_source_urls = item.get("source_urls") or []
    if isinstance(grouped_source_urls, str):
        grouped_source_urls = [grouped_source_urls]
    for value in (
        item.get("source_url"),
        item.get("url"),
        latest_evidence.get("source_url")
        if isinstance(latest_evidence, dict)
        else "",
        *grouped_source_urls,
    ):
        url = str(value or "").strip()
        if url.lower().startswith(("http://", "https://")):
            return url
    return ""


def _finding_source_urls(item: dict[str, Any]) -> list[str]:
    values = item.get("source_urls") or []
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, (list, tuple, set)):
        values = []
    urls_by_identity: dict[str, str] = {}
    for value in values:
        url = str(value or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        identity = endpoint_identity(url, include_query=True)
        if not identity:
            continue
        urls_by_identity[identity] = prefer_https_url(
            urls_by_identity.get(identity, ""),
            url,
        )
    return list(urls_by_identity.values())


def _dashboard_finding(item: dict[str, Any]) -> dict[str, Any]:
    module = _finding_module(item.get("source"))
    source_urls = _finding_source_urls(item)
    return {
        "finding_id": str(item.get("finding_id") or ""),
        "source": str(item.get("source") or ""),
        "module": module,
        "module_label": _TARGET_MODULE_LABELS[module],
        "type": str(item.get("type") or ""),
        "channel": str(item.get("channel") or ""),
        "label": str(item.get("label") or ""),
        "value": str(item.get("value") or ""),
        "context": str(item.get("context") or item.get("summary") or "")[:600],
        "attention_score": int(item.get("attention_score") or 0),
        "party_name": str(item.get("party_name") or item.get("entity_name") or ""),
        "source_url": _finding_source_url(item),
        "source_urls": source_urls,
        "source_count": max(1, len(source_urls)),
        "duplicate_count": max(1, int(item.get("duplicate_count") or 1)),
        "evidence_count": max(1, int(item.get("evidence_count") or 1)),
        "finding_ids": list(item.get("finding_ids") or []),
        "sources": list(item.get("sources") or []),
        "finding_types": list(item.get("finding_types") or []),
        "channels": list(item.get("channels") or []),
        "source_document_id": str(item.get("source_document_id") or ""),
        "screenshot_url": str(item.get("screenshot_url") or ""),
        "updated_at": item.get("updated_at") or item.get("created_at"),
    }


def _dashboard_contact_from_finding(item: dict[str, Any]) -> dict[str, Any] | None:
    finding_type = str(item.get("type") or "").strip().lower()
    channel = str(item.get("channel") or "").strip().lower()
    subtype = str(item.get("subtype") or "").strip().lower()
    scope = str(item.get("scope") or "").strip().lower()
    value = str(item.get("value") or "").strip()
    digits = re.sub(r"\D", "", value)
    if len(digits) == 13 and digits.startswith("86"):
        digits = digits[2:]
    is_mainland_mobile = bool(re.fullmatch(r"1[3-9]\d{9}", digits))
    if channel in {"phone", "telephone"} and (
        finding_type == "personal_mobile"
        or scope == "personal"
        or subtype == "mobile_personal"
        or is_mainland_mobile
    ):
        kind = "personal_phone"
        channel = "phone"
    elif channel == "email" and (
        finding_type == "personal_email"
        or scope == "personal"
        or subtype == "email_personal"
    ):
        kind = "personal_email"
    else:
        return None
    if not value:
        return None
    module = _finding_module(item.get("source"))
    evidence_refs = item.get("evidence_refs") or []
    source_urls = _finding_source_urls(item)
    return {
        "contact_id": str(item.get("finding_id") or value),
        "finding_id": str(item.get("finding_id") or ""),
        "kind": kind,
        "channel": channel,
        "value": value,
        "contact_name": "",
        "label": str(item.get("label") or ""),
        "role": str(item.get("role") or ""),
        "party_name": str(item.get("party_name") or item.get("entity_name") or ""),
        "context": str(item.get("context") or item.get("summary") or "")[:600],
        "attention_score": int(item.get("attention_score") or 0),
        "source": str(item.get("source") or ""),
        "module": module,
        "module_label": _TARGET_MODULE_LABELS[module],
        "source_url": _finding_source_url(item),
        "source_urls": source_urls,
        "source_count": max(1, len(source_urls)),
        "duplicate_count": max(1, int(item.get("duplicate_count") or 1)),
        "source_document_id": str(item.get("source_document_id") or ""),
        "evidence_count": max(
            1,
            int(item.get("evidence_count") or 0),
            len(evidence_refs) if isinstance(evidence_refs, list) else 0,
        ),
        "verified": str(item.get("target_relation") or "") != "not_target",
        "updated_at": item.get("updated_at") or item.get("created_at"),
    }


def _dashboard_contact_from_scholar(item: dict[str, Any]) -> dict[str, Any] | None:
    value = str(item.get("email") or "").strip().lower()
    if not value or str(item.get("email_kind") or "").lower() != "personal":
        return None
    return {
        "contact_id": str(item.get("doc_id") or value),
        "finding_id": "",
        "kind": "personal_email",
        "channel": "email",
        "value": value,
        "contact_name": str(item.get("author_name") or ""),
        "label": "通讯作者" if item.get("is_corresponding") else "学术联系",
        "role": "scholar",
        "party_name": str(item.get("unit") or ""),
        "context": str(
            item.get("article_title")
            or item.get("direction")
            or item.get("evidence")
            or ""
        )[:600],
        "attention_score": 82 if item.get("is_corresponding") else 72,
        "source": "scholar_contact",
        "module": "scholars",
        "module_label": _TARGET_MODULE_LABELS["scholars"],
        "source_url": str(item.get("article_url") or ""),
        "source_document_id": "",
        "evidence_count": 1,
        "verified": bool(item.get("unit_verified")),
        "updated_at": item.get("updated_at") or item.get("created_at"),
    }


def _contact_identity(item: dict[str, Any]) -> tuple[str, str]:
    channel = str(item.get("channel") or "").lower()
    value = str(item.get("value") or "").strip().casefold()
    if channel == "phone":
        digits = re.sub(r"\D", "", value)
        if len(digits) == 13 and digits.startswith("86"):
            digits = digits[2:]
        value = digits or value
    return channel, value


def _merge_target_dashboard_contacts(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate one public contact while retaining its richest evidence."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in items:
        item = dict(raw)
        identity = _contact_identity(item)
        if not identity[1]:
            continue
        current = merged.get(identity)
        if current is None:
            merged[identity] = item
            continue
        current_quality = (
            int(current.get("attention_score") or 0),
            int(bool(current.get("source_url"))),
            int(bool(current.get("contact_name"))),
        )
        item_quality = (
            int(item.get("attention_score") or 0),
            int(bool(item.get("source_url"))),
            int(bool(item.get("contact_name"))),
        )
        preferred, supplement = (
            (item, current) if item_quality > current_quality else (current, item)
        )
        for field in ("contact_name", "label", "role", "party_name", "context", "source_url"):
            if not preferred.get(field) and supplement.get(field):
                preferred[field] = supplement[field]
        preferred["source_urls"] = list(
            dict.fromkeys(
                [
                    *(current.get("source_urls") or []),
                    *(item.get("source_urls") or []),
                ]
            )
        )
        preferred["source_count"] = max(1, len(preferred["source_urls"]))
        preferred["duplicate_count"] = int(
            current.get("duplicate_count") or 1
        ) + int(item.get("duplicate_count") or 1)
        preferred["evidence_count"] = int(current.get("evidence_count") or 0) + int(
            item.get("evidence_count") or 0
        )
        preferred["verified"] = bool(current.get("verified") or item.get("verified"))
        merged[identity] = preferred
    return sorted(
        merged.values(),
        key=lambda item: (
            -int(item.get("attention_score") or 0),
            str(item.get("contact_name") or item.get("label") or "").casefold(),
            str(item.get("value") or "").casefold(),
        ),
    )


def _target_summary_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    """业务等级优先，同等级按高分、完成度和数据量稳定排序。"""
    module_total = sum(
        int(item.get(field) or 0)
        for field in (
            "website_count",
            "xhs_count",
            "wechat_count",
            "bidding_count",
            "scholar_contact_count",
        )
    )
    return (
        *_target_batch_sort_key(item),
        -int(item.get("high_score_finding_count") or 0),
        -int(bool(item.get("collection_complete"))),
        -int(item.get("finding_count") or 0),
        -module_total,
        str(item.get("target_name") or "").casefold(),
        str(item.get("target_id") or ""),
    )


def _target_scan_coverage_summary(
    relation: dict[str, Any],
) -> dict[str, Any]:
    """按当前扫描画像汇总核心渠道，替代仅看 last_collected_at 的旧语义。"""
    from api.services.target_scan_profile import is_scan_coverage_current

    required_channels = ("website", "wechat", "scholar", "bidding")
    completed_channels = [
        channel
        for channel in required_channels
        if is_scan_coverage_current(relation, channel)
    ]
    return {
        "collection_complete": len(completed_channels) == len(required_channels),
        "coverage_completed_count": len(completed_channels),
        "coverage_required_count": len(required_channels),
        "coverage_completed_channels": completed_channels,
        "coverage_missing_channels": [
            channel
            for channel in required_channels
            if channel not in completed_channels
        ],
    }


def _normalized_search_values(values: list[Any]) -> list[str]:
    return list(
        dict.fromkeys(
            normalized
            for value in values
            if (normalized := targets_dao.normalize_target_name(str(value or "")))
        )
    )


def _target_search_rank(
    relation: dict[str, Any],
    target: dict[str, Any],
    query: str,
) -> int | None:
    """Rank a Target match while requiring every query term to be present."""
    normalized_query = targets_dao.normalize_target_name(query)
    if not normalized_query:
        return 0
    query_terms = _normalized_search_values(str(query or "").split()) or [normalized_query]

    channel_terms = [
        term
        for terms in (relation.get("search_terms_by_channel") or {}).values()
        if isinstance(terms, list)
        for term in terms
    ]
    names = _normalized_search_values([
        relation.get("target_name"),
        relation.get("display_name"),
        target.get("canonical_name"),
        target.get("display_name"),
    ])
    authoritative_aliases = [
        *(relation.get("short_names") or []),
        *(relation.get("scan_aliases") or []),
        *(target.get("short_names") or []),
        *(target.get("scan_aliases") or []),
    ]
    aliases = _normalized_search_values(
        authoritative_aliases or list(target.get("identity_aliases") or [])
    )
    domains = _normalized_search_values([
        relation.get("root_domain"),
        *(relation.get("root_domains") or []),
        target.get("root_domain"),
        *(target.get("root_domains") or []),
    ])
    identifiers = _normalized_search_values([
        relation.get("target_id"),
        relation.get("project_target_id"),
    ])
    context = _normalized_search_values([
        *(relation.get("search_terms") or []),
        *channel_terms,
    ])
    all_values = [*names, *aliases, *domains, *identifiers, *context]
    if not all(any(term in value for value in all_values) for term in query_terms):
        return None

    score = 400 + min(len(query_terms), 10)
    weighted_values = (
        (names, 1000, 850, 700),
        (aliases, 960, 820, 650),
        (domains, 920, 800, 620),
        (identifiers, 900, 780, 600),
        (context, 720, 620, 500),
    )
    for values, exact_score, prefix_score, contains_score in weighted_values:
        if normalized_query in values:
            score = max(score, exact_score)
        elif any(value.startswith(normalized_query) for value in values):
            score = max(score, prefix_score)
        elif any(normalized_query in value for value in values):
            score = max(score, contains_score)
    return score


def _hierarchy_parent_target_id(relation: dict[str, Any]) -> str:
    return str(
        relation.get("hierarchy_parent_target_id")
        or relation.get("parent_target_id")
        or ""
    )


def _hierarchy_depth(relation: dict[str, Any]) -> int:
    value = relation.get("hierarchy_depth")
    if value is None:
        value = relation.get("relation_depth")
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _relationship_confidence(relationship: dict[str, Any]) -> float:
    try:
        return float(relationship.get("confidence") or 0)
    except (TypeError, ValueError):
        return 0.0


def apply_project_target_hierarchy(
    relations: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a tree read model without persisting organization edges as control."""
    projected = [dict(relation) for relation in relations]
    by_target = {
        str(relation.get("target_id") or ""): relation
        for relation in projected
        if str(relation.get("target_id") or "")
    }
    parent_by_target: dict[str, str] = {}
    for target_id, relation in by_target.items():
        parent_id = _hierarchy_parent_target_id(relation)
        if parent_id and parent_id != target_id:
            relation_details = (
                relation.get("relation")
                if isinstance(relation.get("relation"), dict)
                else {}
            )
            parent_by_target[target_id] = parent_id
            relation.setdefault("hierarchy_parent_target_id", parent_id)
            relation.setdefault(
                "hierarchy_parent_target_name",
                str(relation.get("parent_target_name") or ""),
            )
            relation.setdefault(
                "hierarchy_relation_type",
                str(relation.get("relation_type") or ""),
            )
            relation.setdefault(
                "hierarchy_relation_source",
                str(relation.get("relation_source") or ""),
            )
            relation.setdefault(
                "hierarchy_source_urls",
                list(
                    relation.get("source_urls")
                    or relation_details.get("source_urls")
                    or []
                ),
            )
            relation.setdefault(
                "hierarchy_ownership_percent",
                relation.get("ownership_percent"),
            )
            relation.setdefault(
                "hierarchy_effective_ownership_percent",
                relation.get("ownership_percent"),
            )

    def would_cycle(child_id: str, parent_id: str) -> bool:
        current_id = parent_id
        visited: set[str] = set()
        while current_id and current_id not in visited:
            if current_id == child_id:
                return True
            visited.add(current_id)
            current_id = parent_by_target.get(current_id, "")
        return False

    candidates = sorted(
        relationships,
        key=lambda item: (
            -_relationship_confidence(item),
            str(item.get("relationship_id") or ""),
        ),
    )
    for edge in candidates:
        if edge.get("active") is False:
            continue
        if str(edge.get("direction") or "") != "upstream":
            continue
        relation_type = str(edge.get("relation_type") or "")
        if relation_type not in _ORGANIZATION_HIERARCHY_RELATION_TYPES:
            continue
        child_id = str(edge.get("subject_target_id") or "")
        parent_id = str(edge.get("related_target_id") or "")
        if (
            not child_id
            or not parent_id
            or child_id not in by_target
            or parent_id not in by_target
            or child_id in parent_by_target
            or would_cycle(child_id, parent_id)
        ):
            continue
        child = by_target[child_id]
        parent_by_target[child_id] = parent_id
        child.update(
            {
                "hierarchy_parent_target_id": parent_id,
                "hierarchy_parent_target_name": str(
                    edge.get("related_target_name")
                    or by_target[parent_id].get("target_name")
                    or ""
                ),
                "hierarchy_relation_type": relation_type,
                "hierarchy_relation_source": str(edge.get("source") or ""),
                "hierarchy_relation_summary": str(edge.get("summary") or ""),
                "hierarchy_source_urls": list(edge.get("source_urls") or []),
                "hierarchy_ownership_percent": edge.get("ownership_percent"),
                "hierarchy_indirect_ownership_percent": edge.get(
                    "indirect_ownership_percent"
                ),
                "hierarchy_effective_ownership_percent": edge.get(
                    "effective_ownership_percent"
                ),
            }
        )

    for target_id, relation in by_target.items():
        lineage_ids = [target_id]
        visited = {target_id}
        current_id = target_id
        while parent_id := parent_by_target.get(current_id, ""):
            if parent_id in visited:
                break
            visited.add(parent_id)
            lineage_ids.append(parent_id)
            current_id = parent_id
            if parent_id not in by_target:
                break
        lineage_ids.reverse()
        lineage_names = [
            str(by_target.get(lineage_id, {}).get("target_name") or "")
            for lineage_id in lineage_ids
        ]
        relation.update(
            {
                "hierarchy_root_target_id": lineage_ids[0],
                "hierarchy_root_target_name": lineage_names[0],
                "hierarchy_depth": max(0, len(lineage_ids) - 1),
                "hierarchy_lineage_target_ids": lineage_ids,
                "hierarchy_lineage_target_names": lineage_names,
            }
        )
    return projected


async def _load_project_target_hierarchy(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    relations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    target_ids = [
        str(relation.get("target_id") or "")
        for relation in relations
        if str(relation.get("target_id") or "")
    ]
    relationships = await target_relationships_dao.list_for_targets(
        db,
        project_id=project_id,
        target_ids=target_ids,
    )
    return apply_project_target_hierarchy(relations, relationships), relationships


def _relation_root_target_id(
    relation: dict[str, Any],
    relations_by_target: dict[str, dict[str, Any]],
) -> str:
    target_id = str(relation.get("target_id") or "")
    root_target_id = str(
        relation.get("hierarchy_root_target_id")
        or relation.get("root_target_id")
        or ""
    )
    if root_target_id and root_target_id in relations_by_target:
        return root_target_id

    current = relation
    visited = {target_id}
    while parent_id := _hierarchy_parent_target_id(current):
        if parent_id in visited or parent_id not in relations_by_target:
            break
        visited.add(parent_id)
        current = relations_by_target[parent_id]
        target_id = parent_id
    return target_id


def _target_hierarchy_counts(
    relations: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int]]:
    """Return direct-child and descendant counts without loading summary metrics."""
    relations_by_target = {
        str(item.get("target_id") or ""): item
        for item in relations
        if str(item.get("target_id") or "")
    }
    child_counts = {target_id: 0 for target_id in relations_by_target}
    descendant_counts: dict[str, int] = {}
    for target_id, relation in relations_by_target.items():
        parent_target_id = _hierarchy_parent_target_id(relation)
        if parent_target_id in child_counts and parent_target_id != target_id:
            child_counts[parent_target_id] += 1
        root_target_id = _relation_root_target_id(relation, relations_by_target)
        if target_id != root_target_id:
            descendant_counts[root_target_id] = (
                descendant_counts.get(root_target_id, 0) + 1
            )
    return child_counts, descendant_counts


def _select_target_relation_page(
    relations: list[dict[str, Any]],
    targets_by_id: dict[str, dict[str, Any]],
    *,
    query: str,
    batch_tag: str = "",
    page: int,
    page_size: int,
    root_stats: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """Select root Target groups and the minimum hierarchy needed for the page."""
    relations_by_target = {
        str(item.get("target_id") or ""): item
        for item in relations
        if str(item.get("target_id") or "")
    }
    root_by_target = {
        target_id: _relation_root_target_id(relation, relations_by_target)
        for target_id, relation in relations_by_target.items()
    }
    groups: dict[str, list[dict[str, Any]]] = {}
    for target_id, relation in relations_by_target.items():
        groups.setdefault(root_by_target[target_id], []).append(relation)

    normalized_batch_tag = str(batch_tag or "").strip()
    eligible_roots = [
        root_id
        for root_id, group in groups.items()
        if not normalized_batch_tag
        or any(
            normalized_batch_tag
            in targets_dao.normalize_batch_tags(relation.get("batch_tags") or [])
            for relation in group
        )
    ]
    eligible_target_ids = {
        str(relation.get("target_id") or "")
        for root_id in eligible_roots
        for relation in groups[root_id]
    }

    normalized_query = targets_dao.normalize_target_name(query)
    direct_scores: dict[str, int] = {}
    if normalized_query:
        for target_id, relation in relations_by_target.items():
            if target_id not in eligible_target_ids:
                continue
            score = _target_search_rank(
                relation,
                targets_by_id.get(target_id, {}),
                query,
            )
            if score is not None:
                direct_scores[target_id] = score
        candidate_roots = list(
            dict.fromkeys(root_by_target[target_id] for target_id in direct_scores)
        )
    else:
        candidate_roots = list(eligible_roots)

    group_scores = {
        root_id: max(
            (direct_scores.get(str(item.get("target_id") or ""), 0) for item in groups[root_id]),
            default=0,
        )
        for root_id in candidate_roots
    }

    def root_sort_key(root_id: str) -> tuple[Any, ...]:
        relation = relations_by_target.get(root_id, {})
        stats = root_stats.get(root_id, {})
        return (
            -group_scores.get(root_id, 0),
            *_target_batch_sort_key(relation),
            -int(stats.get("high_score_finding_count") or 0),
            -int(bool(relation.get("last_collected_at"))),
            -int(stats.get("finding_count") or 0),
            str(relation.get("target_name") or "").casefold(),
            root_id,
        )

    candidate_roots.sort(key=root_sort_key)
    root_total = len(candidate_roots)
    filtered_project_total = sum(len(groups[root_id]) for root_id in eligible_roots)
    safe_page_size = max(1, min(int(page_size or 10), 100))
    max_page = max(1, (root_total + safe_page_size - 1) // safe_page_size)
    safe_page = min(max(1, int(page or 1)), max_page)
    start = (safe_page - 1) * safe_page_size
    selected_roots = candidate_roots[start:start + safe_page_size]

    selected_target_ids: set[str] = set()
    expanded_project_target_ids: set[str] = set()
    if not normalized_query:
        selected_target_ids.update(selected_roots)
    else:
        direct_matches = set(direct_scores)

        def lineage(target_id: str) -> list[str]:
            values: list[str] = []
            current_id = target_id
            visited: set[str] = set()
            while current_id and current_id not in visited:
                visited.add(current_id)
                values.append(current_id)
                current = relations_by_target.get(current_id, {})
                current_id = _hierarchy_parent_target_id(current)
            return values

        for root_id in selected_roots:
            group_target_ids = {
                str(item.get("target_id") or "") for item in groups.get(root_id, [])
            }
            group_matches = direct_matches.intersection(group_target_ids)
            for match_id in group_matches:
                match_lineage = lineage(match_id)
                selected_target_ids.update(match_lineage)
                for ancestor_id in match_lineage[1:]:
                    project_target_id = str(
                        relations_by_target.get(ancestor_id, {}).get("project_target_id") or ""
                    )
                    if project_target_id:
                        expanded_project_target_ids.add(project_target_id)

    root_order = {root_id: index for index, root_id in enumerate(selected_roots)}
    selected_relations = [
        relation
        for relation in relations
        if str(relation.get("target_id") or "") in selected_target_ids
    ]
    selected_relations.sort(
        key=lambda relation: (
            root_order.get(
                root_by_target.get(str(relation.get("target_id") or ""), ""),
                len(root_order),
            ),
            _hierarchy_depth(relation),
            str(relation.get("target_name") or "").casefold(),
        )
    )
    child_counts, descendant_counts = _target_hierarchy_counts(relations)
    return {
        "relations": selected_relations,
        "page": safe_page,
        "page_size": safe_page_size,
        "root_total": root_total,
        "project_total": filtered_project_total,
        "all_root_total": len(groups),
        "all_project_total": len(relations),
        "matched_total": (
            len(direct_scores) if normalized_query else filtered_project_total
        ),
        "matched_target_ids": sorted(direct_scores),
        "expanded_project_target_ids": sorted(expanded_project_target_ids),
        "child_counts": child_counts,
        "descendant_counts": descendant_counts,
        "search_scores": {
            **direct_scores,
            **{
                root_id: max(group_scores.get(root_id, 0), direct_scores.get(root_id, 0))
                for root_id in selected_roots
            },
        },
    }


async def resolve_target(
    db: AsyncIOMotorDatabase,
    *,
    target_id: str = "",
    target_name: str = "",
    target_type: str = "company",
    root_domain: str = "",
    aliases: list[str] | None = None,
    source: str = "",
) -> dict[str, Any] | None:
    """解析已有 Target，或由明确名称创建一个全局 Target。"""
    if target_id:
        existing = await targets_dao.get_target(db, target_id)
        if existing:
            return existing
    if not str(target_name or "").strip():
        return None
    return await targets_dao.upsert_target(
        db,
        name=target_name,
        target_type=target_type,
        root_domain=root_domain,
        aliases=aliases,
        source=source,
    )


def _value_matches_root_domains(value: Any, root_domains: set[str]) -> bool:
    text = str(value or "").strip().casefold()
    if not text:
        return False
    try:
        host = str(
            urlsplit(text if "://" in text else f"//{text}").hostname or ""
        ).casefold().strip(".")
    except ValueError:
        return False
    return bool(
        host
        and any(host == root or host.endswith("." + root) for root in root_domains)
    )


def _asset_record_matches_root_domains(
    record: dict[str, Any],
    root_domains: set[str],
) -> bool:
    return any(
        _value_matches_root_domains(record.get(field), root_domains)
        for field in (
            "canonical_url",
            "link",
            "host",
            "domain",
            "cert_domain",
            "root_domain",
        )
    )


async def reconcile_target_asset_scope(
    db: AsyncIOMotorDatabase,
    *,
    target_id: str,
    root_domains: list[str],
) -> dict[str, int]:
    """Detach derived Target data outside a newly verified asset boundary."""
    selected_target_id = str(target_id or "").strip()
    roots = {
        str(value or "").casefold().strip(".").removeprefix("www.")
        for value in root_domains
        if str(value or "").strip()
    }
    if not selected_target_id or not roots:
        return {
            "assets_excluded": 0,
            "website_records_excluded": 0,
            "findings_removed": 0,
        }

    asset_rows, scan_rows, legacy_rows, finding_rows = await asyncio.gather(
        db[FOFA_ASSETS_COLLECTION]
        .find(
            {
                "$or": [
                    {"target_id": selected_target_id},
                    {"target_ids": selected_target_id},
                ]
            },
            {
                "_id": 1,
                "canonical_url": 1,
                "link": 1,
                "host": 1,
                "domain": 1,
                "cert_domain": 1,
                "root_domain": 1,
            },
        )
        .to_list(None),
        db[URL_SCAN_RESULTS_COLLECTION]
        .find(
            {"target_id": selected_target_id, "source": "web_tagging"},
            {"_id": 1, "url": 1, "exclusion_reason": 1},
        )
        .to_list(None),
        db[WEB_TAGS_COLLECTION]
        .find(
            {
                "target_id": selected_target_id,
                "$or": [
                    {"source": "web_tagging"},
                    {"source": {"$exists": False}},
                    {"source": None},
                ],
            },
            {"_id": 1, "url": 1, "exclusion_reason": 1},
        )
        .to_list(None),
        db[FINDINGS_COLLECTION]
        .find(
            {"target_id": selected_target_id, "source": "web_tagging"},
            {"_id": 1, "finding_id": 1, "url": 1, "source_url": 1},
        )
        .to_list(None),
    )

    asset_outside = [
        row["_id"]
        for row in asset_rows
        if not _asset_record_matches_root_domains(row, roots)
    ]
    asset_inside = [
        row["_id"]
        for row in asset_rows
        if _asset_record_matches_root_domains(row, roots)
    ]
    if asset_outside:
        await db[FOFA_ASSETS_COLLECTION].update_many(
            {"_id": {"$in": asset_outside}},
            {"$addToSet": {"excluded_target_ids": selected_target_id}},
        )
    if asset_inside:
        await db[FOFA_ASSETS_COLLECTION].update_many(
            {"_id": {"$in": asset_inside}},
            {"$pull": {"excluded_target_ids": selected_target_id}},
        )

    async def _reconcile_website_collection(
        collection_name: str,
        rows: list[dict[str, Any]],
    ) -> int:
        outside = [
            row["_id"]
            for row in rows
            if not _value_matches_root_domains(row.get("url"), roots)
        ]
        restored = [
            row["_id"]
            for row in rows
            if _value_matches_root_domains(row.get("url"), roots)
            and row.get("exclusion_reason") == "outside_verified_asset_scope"
        ]
        if outside:
            await db[collection_name].update_many(
                {"_id": {"$in": outside}},
                {
                    "$set": {
                        "excluded": True,
                        "exclusion_reason": "outside_verified_asset_scope",
                    }
                },
            )
        if restored:
            await db[collection_name].update_many(
                {
                    "_id": {"$in": restored},
                    "exclusion_reason": "outside_verified_asset_scope",
                },
                {"$unset": {"excluded": "", "exclusion_reason": ""}},
            )
        return len(outside)

    scan_excluded, legacy_excluded = await asyncio.gather(
        _reconcile_website_collection(URL_SCAN_RESULTS_COLLECTION, scan_rows),
        _reconcile_website_collection(WEB_TAGS_COLLECTION, legacy_rows),
    )

    invalid_findings = [
        row
        for row in finding_rows
        if (row.get("source_url") or row.get("url"))
        and not _value_matches_root_domains(
            row.get("source_url") or row.get("url"),
            roots,
        )
    ]
    finding_object_ids = [row["_id"] for row in invalid_findings]
    finding_ids = [
        str(row.get("finding_id") or "")
        for row in invalid_findings
        if str(row.get("finding_id") or "")
    ]
    if finding_object_ids:
        await db[FINDINGS_COLLECTION].delete_many(
            {"_id": {"$in": finding_object_ids}}
        )
    if finding_ids:
        derivative_query = {
            "$or": [
                {"finding_id": {"$in": finding_ids}},
                {"finding_ids": {"$in": finding_ids}},
            ]
        }
        await asyncio.gather(
            db[COPYWRITINGS_COLLECTION].delete_many(derivative_query),
            db[PROFILES_COLLECTION].delete_many(derivative_query),
        )
    return {
        "assets_excluded": len(asset_outside),
        "website_records_excluded": scan_excluded + legacy_excluded,
        "findings_removed": len(finding_object_ids),
    }


async def set_target_official_website_roots(
    db: AsyncIOMotorDatabase,
    *,
    target_id: str,
    root_domains: list[str] | str,
    asset_root_domains: list[str] | str | None = None,
) -> dict[str, Any]:
    """Apply verified website and asset scopes while retaining domain history."""
    from api.services.website_documents import normalize_website_root_domains

    normalized = normalize_website_root_domains(root_domains)
    if not normalized:
        raise ValueError("已核验官网根域名不能为空")
    normalized_assets = normalize_website_root_domains(
        asset_root_domains if asset_root_domains else normalized
    )
    target = await targets_dao.set_target_official_root_domains(
        db,
        target_id=str(target_id or "").strip(),
        root_domains=normalized,
        asset_root_domains=normalized_assets,
    )
    if not target:
        raise ValueError("Target 不存在")
    reconciliation = await reconcile_target_asset_scope(
        db,
        target_id=str(target_id or "").strip(),
        root_domains=normalized_assets,
    )
    return {**target, "scope_reconciliation": reconciliation}


async def require_project_target(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str,
) -> dict[str, str]:
    """校验 Target 属于项目，并返回稳定 ID 与正式名称。"""
    normalized_target_id = str(target_id or "").strip()
    if not normalized_target_id:
        raise ValueError("目标公司 ID 不能为空")
    relation = await targets_dao.get_project_target(
        db,
        project_id=project_id,
        target_id=normalized_target_id,
    )
    if not relation:
        raise ValueError("目标公司不属于当前项目")
    target = await targets_dao.get_target(db, normalized_target_id)
    if not target:
        raise ValueError("目标公司不存在")
    target_name = str(
        relation.get("target_name")
        or target.get("canonical_name")
        or target.get("name")
        or ""
    ).strip()
    return {"target_id": normalized_target_id, "target_name": target_name}


async def resolve_collection_target(
    db: AsyncIOMotorDatabase,
    *,
    task_def: dict[str, Any],
    project_id: str = "",
) -> dict[str, Any] | None:
    """从采集定义解析 Target。

    只接受任务显式 target_name/target_id 或项目历史 target 文本，不把任意搜索词
    自动当成公司，避免把“公司名 + 招标”等查询意图错误聚类为新实体。
    """
    target_id = str(task_def.get("target_id") or "").strip()
    target_name = str(task_def.get("target_name") or "").strip()
    if not target_name and not target_id and project_id:
        project = await projects_dao.get_project(db, project_id)
        if project:
            target_name = str(project.get("target") or "").strip()
    target = await resolve_target(
        db,
        target_id="" if target_name else target_id,
        target_name=target_name,
        target_type=str(task_def.get("target_type") or "company"),
        source="mobile_collect_task",
    )
    if target and project_id:
        keywords = [str(item).strip() for item in task_def.get("keywords") or []]
        await targets_dao.link_project_target(
            db,
            project_id=project_id,
            target=target,
            search_terms=keywords,
            objectives=[str(task_def.get("search_hint") or "")],
            task_def_id=str(task_def.get("task_def_id") or ""),
        )
        target_id = str(target.get("target_id") or "")
        target_name = str(target.get("canonical_name") or "")
        await mobile_collect_dao.backfill_task_target(
            db,
            task_def_id=str(task_def.get("task_def_id") or ""),
            target_id=target_id,
            target_name=target_name,
        )
        normalized_target = targets_dao.normalize_target_name(target_name)
        explicit_target_terms = [
            term
            for term in keywords
            if normalized_target
            and normalized_target in targets_dao.normalize_target_name(term)
        ]
        await mobile_collect_dao.backfill_project_target_by_keywords(
            db,
            project_id=project_id,
            keywords=explicit_target_terms,
            target_id=target_id,
            target_name=target_name,
        )
    return target


async def attach_normalized_company(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    input_name: str,
    normalized_name: str,
    root_domain: str = "",
    root_domains: list[str] | None = None,
    aliases: list[str] | None = None,
    task_id: str = "",
    normalization_version: int | None = None,
    preferred_target_id: str = "",
    batch_tags: list[str] | tuple[str, ...] | str | None = None,
) -> dict[str, Any]:
    """把 company_meta 的项目级规范化结果挂到全局 Target 聚类。"""
    canonical_name = str(normalized_name or input_name).strip()
    input_key = targets_dao.normalize_target_name(input_name)
    canonical_key = targets_dao.normalize_target_name(canonical_name)
    existing = await targets_dao.get_target(db, preferred_target_id)
    if preferred_target_id and existing is None:
        raise ValueError(f"指定的 Target 不存在: {preferred_target_id}")
    if existing is not None:
        canonical_name = str(existing.get("canonical_name") or canonical_name).strip()
        canonical_key = targets_dao.normalize_target_name(canonical_name)
    else:
        existing = await targets_dao.find_target_exact_name(
            db,
            name=canonical_name,
            target_type="company",
        )
    promoted_brand_target = False
    if existing is None and input_key and input_key != canonical_key:
        # The only cross-name identity promotion allowed here is a Target that
        # was originally created under the exact user-supplied brand name.
        brand_target = await targets_dao.find_target_exact_name(
            db,
            name=input_name,
            target_type="company",
        )
        if (
            brand_target
            and targets_dao.normalize_target_name(
                str(brand_target.get("canonical_name") or "")
            ) == input_key
        ):
            existing = brand_target
            promoted_brand_target = True
    trusted_identity_aliases = (
        [input_name]
        if input_key == canonical_key or promoted_brand_target
        else []
    )
    target = await targets_dao.upsert_target(
        db,
        name=canonical_name,
        target_type="company",
        root_domain=root_domain,
        root_domains=root_domains,
        aliases=[input_name, *(aliases or [])],
        source="company_normalize",
        normalization_version=normalization_version,
        match_aliases=False,
        preferred_target_id=str((existing or {}).get("target_id") or ""),
        identity_aliases=trusted_identity_aliases,
        preserve_canonical_name=bool(preferred_target_id),
    )
    if project_id:
        await targets_dao.link_project_target(
            db,
            project_id=project_id,
            target=target,
            search_terms=[input_name],
            task_def_id=task_id,
            batch_tags=batch_tags,
        )
    return target


async def list_project_target_summaries(
    db: AsyncIOMotorDatabase,
    project_id: str,
    *,
    compact: bool = False,
    relations: list[dict[str, Any]] | None = None,
    target_relationships: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if relations is None:
        relations = await targets_dao.list_project_targets(
            db,
            project_id,
            summary_only=compact,
        )
    target_ids = [str(item.get("target_id") or "") for item in relations]
    if not target_ids:
        return []
    document_counts_job = db[SOURCE_DOCUMENT_LINKS_COLLECTION].aggregate(
        [
            {"$match": {"target_id": {"$in": target_ids}}},
            {
                "$group": {
                    "_id": "$target_id",
                    "document_ids": {"$addToSet": "$document_id"},
                    "project_ids": {"$addToSet": "$project_id"},
                    "last_document_at": {"$max": "$last_seen_at"},
                }
            },
            {
                "$project": {
                    "document_count": {"$size": "$document_ids"},
                    "linked_project_count": {"$size": "$project_ids"},
                    "last_document_at": 1,
                }
            },
        ]
    ).to_list(len(target_ids))
    project_document_counts_job = db[SOURCE_DOCUMENT_LINKS_COLLECTION].aggregate(
        [
            {
                "$match": {
                    "project_id": project_id,
                    "target_id": {"$in": target_ids},
                }
            },
            {
                "$group": {
                    "_id": "$target_id",
                    "document_ids": {"$addToSet": "$document_id"},
                }
            },
            {"$project": {"document_count": {"$size": "$document_ids"}}},
        ]
    ).to_list(len(target_ids))
    record_counts_job = db[MOBILE_COLLECT_RECORDS_COLLECTION].aggregate(
        [
            {
                "$match": project_scope_query(
                    project_id,
                    {
                        "target_id": {"$in": target_ids},
                        "superseded_by_record_id": {"$exists": False},
                    },
                ),
            },
            {"$group": {"_id": "$target_id", "record_count": {"$sum": 1}}},
        ]
    ).to_list(len(target_ids))
    wechat_counts_job = db[MOBILE_COLLECT_RECORDS_COLLECTION].aggregate(
        [
            {
                "$match": project_scope_query(
                    project_id,
                    {
                        "target_id": {"$in": target_ids},
                        "superseded_by_record_id": {"$exists": False},
                        "source_document_id": {
                            "$exists": True,
                            "$nin": ["", None],
                        },
                    },
                ),
            },
            {"$group": {"_id": "$target_id", "wechat_count": {"$sum": 1}}},
        ]
    ).to_list(len(target_ids))
    asset_counts_job = db[FOFA_ASSETS_COLLECTION].aggregate(
        [
            {
                "$match": {
                    "project_id": project_id,
                    "$or": [
                        {"target_ids": {"$in": target_ids}},
                        {"target_id": {"$in": target_ids}},
                    ],
                }
            },
            {
                "$set": {
                    "_resolved_target_ids": {
                        "$setUnion": [
                            {
                                "$cond": [
                                    {"$isArray": "$target_ids"},
                                    "$target_ids",
                                    [],
                                ]
                            },
                            {
                                "$cond": [
                                    {
                                        "$ne": [
                                            {"$ifNull": ["$target_id", ""]},
                                            "",
                                        ]
                                    },
                                    ["$target_id"],
                                    [],
                                ]
                            },
                        ]
                    }
                }
            },
            {"$unwind": "$_resolved_target_ids"},
            {
                "$match": {
                    "_resolved_target_ids": {"$in": target_ids},
                    "$expr": {
                        "$not": [
                            {
                                "$in": [
                                    "$_resolved_target_ids",
                                    {"$ifNull": ["$excluded_target_ids", []]},
                                ]
                            }
                        ]
                    },
                }
            },
            {
                "$group": {
                    "_id": "$_resolved_target_ids",
                    "asset_count": {"$sum": 1},
                    "alive_asset_count": {
                        "$sum": {"$cond": [{"$eq": ["$is_alive", True]}, 1, 0]}
                    },
                }
            },
        ]
    ).to_list(len(target_ids))
    finding_counts_job = findings_dao.aggregate_target_finding_counts(
        db,
        project_id=project_id,
        target_ids=target_ids,
    )
    from api.services.website_records import count_project_website_records_by_target

    website_counts_job = count_project_website_records_by_target(
        db,
        project_id=project_id,
        target_ids=target_ids,
    )
    xhs_counts_job = db[XHS_NOTES_COLLECTION].aggregate(
        [
            {
                "$match": {
                    "project_id": project_id,
                    "target_id": {"$in": target_ids},
                }
            },
            {"$group": {"_id": "$target_id", "xhs_count": {"$sum": 1}}},
        ]
    ).to_list(len(target_ids))
    from api.services.bidding_records import count_project_bidding_records_by_target

    bidding_counts_job = count_project_bidding_records_by_target(
        db,
        project_id=project_id,
        target_ids=target_ids,
    )
    from api.dao import scholar_contact as scholar_dao

    scholar_counts_job = scholar_dao.count_contacts_by_target(
        db,
        project_id=project_id,
        target_ids=target_ids,
    )
    target_relationships_job = (
        asyncio.sleep(0, result=target_relationships)
        if target_relationships is not None
        else target_relationships_dao.list_for_targets(
            db,
            project_id=project_id,
            target_ids=target_ids,
        )
    )
    task_ids = list(
        {
            str(task_id)
            for relation in relations
            for task_id in [
                *(relation.get("run_task_ids") or []),
                *(relation.get("task_def_ids") or []),
            ]
            if str(task_id or "").strip()
        }
    )
    task_docs_job = db[TASKS_COLLECTION].find(
        {"project_id": project_id, "task_id": {"$in": task_ids}},
        {
            "_id": 0,
            "task_id": 1,
            "status": 1,
            "result_status": 1,
            "result.status": 1,
            "updated_at": 1,
            "created_at": 1,
        },
    ).to_list(max(1, len(task_ids)))
    (
        counts,
        project_document_counts,
        record_counts,
        wechat_counts,
        asset_counts,
        finding_counts,
        website_counts,
        xhs_counts,
        bidding_counts,
        scholar_counts,
        target_relationships,
        task_docs,
    ) = await asyncio.gather(
        document_counts_job,
        project_document_counts_job,
        record_counts_job,
        wechat_counts_job,
        asset_counts_job,
        finding_counts_job,
        website_counts_job,
        xhs_counts_job,
        bidding_counts_job,
        scholar_counts_job,
        target_relationships_job,
        task_docs_job,
    )

    def _by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {str(item.get("_id") or ""): item for item in items}

    by_target = _by_id(counts)
    assets_by_target = _by_id(asset_counts)
    findings_by_target = _summarize_finding_counts(finding_counts)
    xhs_by_target = _by_id(xhs_counts)
    project_docs_by_target = {
        str(item.get("_id") or ""): int(item.get("document_count") or 0)
        for item in project_document_counts
    }
    records_by_target = {
        str(item.get("_id") or ""): int(item.get("record_count") or 0)
        for item in record_counts
    }
    wechat_by_target = {
        str(item.get("_id") or ""): int(item.get("wechat_count") or 0)
        for item in wechat_counts
    }
    tasks_by_id = {str(item.get("task_id") or ""): item for item in task_docs}
    relations = apply_project_target_hierarchy(relations, target_relationships)
    relationship_views = target_relationships_dao.build_target_relationship_views(
        target_relationships
    )

    def _relation_payload(relation: dict[str, Any]) -> dict[str, Any]:
        if not compact:
            return relation
        return {
            key: relation.get(key)
            for key in (
                "project_target_id",
                "project_id",
                "target_id",
                "target_type",
                "target_name",
                "root_domain",
                "root_domains",
                "search_terms",
                "search_terms_by_channel",
                "root_target_id",
                "root_target_name",
                "parent_target_id",
                "parent_target_name",
                "relation_type",
                "relation_depth",
                "ownership_percent",
                "relation_source",
                "lineage_target_ids",
                "lineage_target_names",
                "hierarchy_root_target_id",
                "hierarchy_root_target_name",
                "hierarchy_parent_target_id",
                "hierarchy_parent_target_name",
                "hierarchy_relation_type",
                "hierarchy_relation_source",
                "hierarchy_relation_summary",
                "hierarchy_source_urls",
                "hierarchy_ownership_percent",
                "hierarchy_indirect_ownership_percent",
                "hierarchy_effective_ownership_percent",
                "hierarchy_depth",
                "hierarchy_lineage_target_ids",
                "hierarchy_lineage_target_names",
                "batch_tags",
                "display_name",
                "short_names",
                "scan_aliases",
                "scan_profile_version",
                "scan_profile_fingerprint",
                "scan_profile_updated_at",
                "scan_coverage",
            )
            if key in relation
        }

    summaries = [
        {
            **_relation_payload(relation),
            **_target_batch_priority(relation.get("batch_tags")),
            **relationship_views.get(
                str(relation.get("target_id") or ""),
                {
                    "supervising_units": [],
                    "supervised_units": [],
                    "related_units": [],
                },
            ),
            "document_count": int(
                by_target.get(str(relation.get("target_id") or ""), {}).get(
                    "document_count", 0
                )
            ),
            "linked_project_count": int(
                by_target.get(str(relation.get("target_id") or ""), {}).get(
                    "linked_project_count", 0
                )
            ),
            "last_document_at": by_target.get(
                str(relation.get("target_id") or ""), {}
            ).get("last_document_at"),
            "project_document_count": project_docs_by_target.get(
                str(relation.get("target_id") or ""), 0
            ),
            "record_count": records_by_target.get(
                str(relation.get("target_id") or ""), 0
            ),
            "asset_count": int(
                assets_by_target.get(str(relation.get("target_id") or ""), {}).get(
                    "asset_count", 0
                )
            ),
            "alive_asset_count": int(
                assets_by_target.get(str(relation.get("target_id") or ""), {}).get(
                    "alive_asset_count", 0
                )
            ),
            "finding_count": int(
                findings_by_target.get(str(relation.get("target_id") or ""), {}).get(
                    "finding_count", 0
                )
            ),
            "high_score_finding_count": int(
                findings_by_target.get(str(relation.get("target_id") or ""), {}).get(
                    "high_score_finding_count", 0
                )
            ),
            "high_score_by_source": dict(
                findings_by_target.get(str(relation.get("target_id") or ""), {}).get(
                    "high_score_by_source", _empty_high_score_breakdown()
                )
            ),
            "website_count": int(
                website_counts.get(str(relation.get("target_id") or ""), 0)
            ),
            "xhs_count": int(
                xhs_by_target.get(str(relation.get("target_id") or ""), {}).get(
                    "xhs_count", 0
                )
            ),
            "wechat_count": wechat_by_target.get(
                str(relation.get("target_id") or ""), 0
            ),
            "bidding_count": int(
                bidding_counts.get(str(relation.get("target_id") or ""), 0)
            ),
            "scholar_contact_count": int(
                scholar_counts.get(str(relation.get("target_id") or ""), 0)
            ),
            "latest_task_status": next(
                (
                    _task_collection_status(tasks_by_id[task_id])
                    for task_id in reversed(
                        [str(value) for value in relation.get("run_task_ids") or []]
                    )
                    if task_id in tasks_by_id
                ),
                "",
            ),
            **_target_scan_coverage_summary(relation),
        }
        for relation in relations
    ]
    summaries.sort(key=_target_summary_sort_key)
    return summaries


async def list_project_target_options(
    db: AsyncIOMotorDatabase,
    project_id: str,
) -> list[dict[str, Any]]:
    """Return the lightweight complete Target index used by project filters."""
    relations = await targets_dao.list_project_targets(
        db,
        project_id,
        summary_only=True,
    )
    relations, _relationships = await _load_project_target_hierarchy(
        db,
        project_id=project_id,
        relations=relations,
    )
    fields = (
        "project_target_id",
        "target_id",
        "target_name",
        "root_domain",
        "root_target_id",
        "root_target_name",
        "parent_target_id",
        "parent_target_name",
        "relation_depth",
        "hierarchy_root_target_id",
        "hierarchy_root_target_name",
        "hierarchy_parent_target_id",
        "hierarchy_parent_target_name",
        "hierarchy_relation_type",
        "hierarchy_depth",
        "batch_tags",
        "display_name",
        "short_names",
        "scan_aliases",
        "scan_profile_version",
        "scan_profile_fingerprint",
        "scan_profile_updated_at",
        "scan_coverage",
    )
    items = [
        {key: relation.get(key) for key in fields if key in relation}
        for relation in relations
    ]
    items.sort(
        key=lambda item: (
            str(
                item.get("hierarchy_root_target_name")
                or item.get("root_target_name")
                or item.get("target_name")
                or ""
            ).casefold(),
            _hierarchy_depth(item),
            str(item.get("target_name") or "").casefold(),
            str(item.get("target_id") or ""),
        )
    )
    return items


async def list_project_target_batches(
    db: AsyncIOMotorDatabase,
    project_id: str,
) -> list[dict[str, Any]]:
    """Return batch labels derived from the ProjectTarget source of truth."""
    relations = await targets_dao.list_project_targets(
        db,
        project_id,
        summary_only=True,
    )
    relations, _relationships = await _load_project_target_hierarchy(
        db,
        project_id=project_id,
        relations=relations,
    )
    relations_by_target = {
        str(item.get("target_id") or ""): item
        for item in relations
        if str(item.get("target_id") or "")
    }
    counts: dict[str, dict[str, set[str]]] = {}
    for target_id, relation in relations_by_target.items():
        root_target_id = _relation_root_target_id(relation, relations_by_target)
        for batch_tag in targets_dao.normalize_batch_tags(
            relation.get("batch_tags") or []
        ):
            values = counts.setdefault(
                batch_tag,
                {"target_ids": set(), "root_target_ids": set()},
            )
            values["target_ids"].add(target_id)
            values["root_target_ids"].add(root_target_id)
    items = [
        {
            "batch_tag": batch_tag,
            "target_count": len(values["target_ids"]),
            "root_count": len(values["root_target_ids"]),
        }
        for batch_tag, values in counts.items()
    ]
    items.sort(
        key=lambda item: (
            *_target_batch_sort_key({"batch_tags": [item["batch_tag"]]}),
            str(item["batch_tag"]).casefold(),
        )
    )
    return items


async def assign_project_target_batches(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_ids: list[str],
    batch_tags: list[str],
    operation: str = "add",
    include_descendants: bool = True,
) -> dict[str, Any]:
    """Assign business batch labels and optionally propagate them down a branch."""
    requested_ids = list(
        dict.fromkeys(
            str(value or "").strip()
            for value in target_ids
            if str(value or "").strip()
        )
    )
    if not requested_ids:
        raise ValueError("至少需要选择一个 Target")
    normalized_tags = targets_dao.normalize_batch_tags(batch_tags)
    relations = await targets_dao.list_project_targets(
        db,
        project_id,
        summary_only=True,
    )
    relations, _relationships = await _load_project_target_hierarchy(
        db,
        project_id=project_id,
        relations=relations,
    )
    relations_by_target = {
        str(item.get("target_id") or ""): item
        for item in relations
        if str(item.get("target_id") or "")
    }
    missing_ids = [
        target_id
        for target_id in requested_ids
        if target_id not in relations_by_target
    ]
    if missing_ids:
        raise ValueError(f"以下 Target 不属于当前项目: {', '.join(missing_ids)}")

    selected_ids = set(requested_ids)
    if include_descendants:
        seed_ids = set(requested_ids)
        for candidate_id, candidate in relations_by_target.items():
            stored_ancestors = {
                str(value or "").strip()
                for value in [
                    candidate.get("root_target_id"),
                    *(candidate.get("lineage_target_ids") or []),
                ]
                if str(value or "").strip()
            }
            if stored_ancestors.intersection(seed_ids):
                selected_ids.add(candidate_id)
                continue
            current_id = candidate_id
            visited: set[str] = set()
            while current_id and current_id not in visited:
                if current_id in selected_ids:
                    selected_ids.add(candidate_id)
                    break
                visited.add(current_id)
                current_id = _hierarchy_parent_target_id(
                    relations_by_target.get(current_id, {})
                )

    result = await targets_dao.update_project_target_batch_tags(
        db,
        project_id=project_id,
        target_ids=sorted(selected_ids),
        batch_tags=normalized_tags,
        operation=operation,
    )
    return {
        **result,
        "project_id": project_id,
        "target_ids": sorted(selected_ids),
        "target_count": len(selected_ids),
        "batch_tags": normalized_tags,
        "operation": operation,
        "include_descendants": include_descendants,
    }


async def get_project_target_summary(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str,
) -> dict[str, Any] | None:
    """Recompute one Target summary from persisted module collections."""
    relation = await targets_dao.get_project_target(
        db,
        project_id=project_id,
        target_id=target_id,
    )
    if not relation or relation.get("active") is False:
        return None
    summaries = await list_project_target_summaries(
        db,
        project_id,
        compact=True,
        relations=[relation],
    )
    return summaries[0] if summaries else None


async def get_project_target_dashboard(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str,
) -> dict[str, Any] | None:
    """Build the actionable Target overview consumed by web and AI surfaces."""
    summary_job = get_project_target_summary(
        db,
        project_id=project_id,
        target_id=target_id,
    )
    findings_job = findings_dao.query_target_dashboard_findings(
        db,
        project_id=project_id,
        target_id=target_id,
    )
    scholar_job = scholar_dao.query_contacts(
        db,
        project_id,
        target_id=target_id,
        only_verified=True,
        email_kind="personal",
        limit=500,
    )
    summary, (top_findings, contact_findings), (scholar_contacts, _) = (
        await asyncio.gather(summary_job, findings_job, scholar_job)
    )
    if summary is None:
        return None

    contact_candidates = [
        contact
        for item in contact_findings
        if (contact := _dashboard_contact_from_finding(item)) is not None
    ]
    contact_candidates.extend(
        contact
        for item in scholar_contacts
        if (contact := _dashboard_contact_from_scholar(item)) is not None
    )
    contacts = _merge_target_dashboard_contacts(contact_candidates)
    personal_phones = [
        item for item in contacts if item.get("kind") == "personal_phone"
    ]
    personal_emails = [
        item for item in contacts if item.get("kind") == "personal_email"
    ]
    return {
        "target": summary,
        "contact_counts": {
            "personal_phone": len(personal_phones),
            "personal_email": len(personal_emails),
        },
        "personal_phones": personal_phones[:200],
        "personal_emails": personal_emails[:200],
        "top_findings": [_dashboard_finding(item) for item in top_findings],
    }


async def list_project_target_branch(
    db: AsyncIOMotorDatabase,
    project_id: str,
    target_id: str,
) -> dict[str, Any]:
    """Load one root hierarchy on demand for an expanded dashboard row."""
    relations = await targets_dao.list_project_targets(
        db,
        project_id,
        summary_only=True,
    )
    relations, target_relationships = await _load_project_target_hierarchy(
        db,
        project_id=project_id,
        relations=relations,
    )
    relations_by_target = {
        str(item.get("target_id") or ""): item
        for item in relations
        if str(item.get("target_id") or "")
    }
    selected = relations_by_target.get(str(target_id or ""))
    if selected is None:
        return {"items": [], "total": 0, "root_target_id": ""}
    root_target_id = _relation_root_target_id(selected, relations_by_target)
    branch_relations = [
        relation
        for candidate_id, relation in relations_by_target.items()
        if _relation_root_target_id(relation, relations_by_target) == root_target_id
        and candidate_id
    ]
    summaries = await list_project_target_summaries(
        db,
        project_id,
        compact=True,
        relations=branch_relations,
        target_relationships=target_relationships,
    )
    child_counts, descendant_counts = _target_hierarchy_counts(relations)
    for summary in summaries:
        summary_target_id = str(summary.get("target_id") or "")
        summary["child_count"] = int(child_counts.get(summary_target_id, 0))
        summary["descendant_count"] = int(
            descendant_counts.get(summary_target_id, 0)
        )
    return {
        "items": summaries,
        "total": len(summaries),
        "root_target_id": root_target_id,
    }


async def list_project_target_summary_page(
    db: AsyncIOMotorDatabase,
    project_id: str,
    *,
    page: int = 1,
    page_size: int = 10,
    query: str = "",
    batch_tag: str = "",
) -> dict[str, Any]:
    """Return one root-level page with child context and page-local statistics."""
    relations = await targets_dao.list_project_targets(
        db,
        project_id,
        summary_only=True,
    )
    if not relations:
        return {
            "items": [],
            "total": 0,
            "root_total": 0,
            "project_total": 0,
            "all_root_total": 0,
            "all_project_total": 0,
            "matched_total": 0,
            "page": 1,
            "page_size": max(1, min(int(page_size or 10), 100)),
            "matched_target_ids": [],
            "expanded_project_target_ids": [],
        }

    relations, target_relationships = await _load_project_target_hierarchy(
        db,
        project_id=project_id,
        relations=relations,
    )

    relations_by_target = {
        str(item.get("target_id") or ""): item
        for item in relations
        if str(item.get("target_id") or "")
    }
    target_ids = list(relations_by_target)
    root_ids = list(
        dict.fromkeys(
            _relation_root_target_id(relation, relations_by_target)
            for relation in relations
        )
    )
    target_docs_job = db[TARGETS_COLLECTION].find(
        {"target_id": {"$in": target_ids}},
        {
            "_id": 0,
            "target_id": 1,
            "canonical_name": 1,
            "identity_aliases": 1,
            "aliases": 1,
            "display_name": 1,
            "short_names": 1,
            "scan_aliases": 1,
            "root_domain": 1,
            "root_domains": 1,
        },
    ).to_list(len(target_ids))
    root_stats_job = findings_dao.aggregate_target_finding_counts(
        db,
        project_id=project_id,
        target_ids=root_ids,
    )
    target_docs, root_stat_rows = await asyncio.gather(target_docs_job, root_stats_job)
    targets_by_id = {
        str(item.get("target_id") or ""): item for item in target_docs
    }
    root_stats = _summarize_finding_counts(root_stat_rows)
    selection = _select_target_relation_page(
        relations,
        targets_by_id,
        query=query,
        batch_tag=batch_tag,
        page=page,
        page_size=page_size,
        root_stats=root_stats,
    )
    summaries = await list_project_target_summaries(
        db,
        project_id,
        compact=True,
        relations=selection["relations"],
        target_relationships=target_relationships,
    )
    matched_target_ids = set(selection["matched_target_ids"])
    search_scores = selection["search_scores"]
    for summary in summaries:
        target_id = str(summary.get("target_id") or "")
        summary["search_match"] = target_id in matched_target_ids
        summary["search_score"] = int(search_scores.get(target_id, 0))
        summary["child_count"] = int(selection["child_counts"].get(target_id, 0))
        summary["descendant_count"] = int(
            selection["descendant_counts"].get(target_id, 0)
        )
    return {
        "items": summaries,
        "total": selection["root_total"],
        "root_total": selection["root_total"],
        "project_total": selection["project_total"],
        "all_root_total": selection["all_root_total"],
        "all_project_total": selection["all_project_total"],
        "matched_total": selection["matched_total"],
        "page": selection["page"],
        "page_size": selection["page_size"],
        "matched_target_ids": selection["matched_target_ids"],
        "expanded_project_target_ids": selection["expanded_project_target_ids"],
    }
