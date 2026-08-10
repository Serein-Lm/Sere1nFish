"""Target 领域服务：统一解析公司/机构实体并建立项目关联。"""
from __future__ import annotations

import asyncio
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import projects as projects_dao
from api.dao import mobile_collect as mobile_collect_dao
from api.dao import targets as targets_dao
from api.dao.project_scope import project_scope_query
from api.db.collections import (
    FOFA_ASSETS_COLLECTION,
    FINDINGS_COLLECTION,
    MOBILE_COLLECT_RECORDS_COLLECTION,
    SOURCE_DOCUMENT_LINKS_COLLECTION,
    TARGETS_COLLECTION,
    TASKS_COLLECTION,
    XHS_NOTES_COLLECTION,
)


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


def _empty_high_score_breakdown() -> dict[str, int]:
    return {key: 0 for key in _HIGH_SCORE_SOURCE_KEYS}


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


def _target_summary_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    """高分 Finding 优先，随后按完成度和数据量稳定排序。"""
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


def _relation_root_target_id(
    relation: dict[str, Any],
    relations_by_target: dict[str, dict[str, Any]],
) -> str:
    target_id = str(relation.get("target_id") or "")
    root_target_id = str(relation.get("root_target_id") or "")
    if root_target_id and root_target_id in relations_by_target:
        return root_target_id

    current = relation
    visited = {target_id}
    while parent_id := str(current.get("parent_target_id") or ""):
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
        parent_target_id = str(relation.get("parent_target_id") or "")
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
                current_id = str(current.get("parent_target_id") or "")
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
            int(relation.get("relation_depth") or 0),
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
            {"$match": {"_resolved_target_ids": {"$in": target_ids}}},
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
    finding_counts_job = db[FINDINGS_COLLECTION].aggregate(
        [
            {
                "$match": {
                    "project_id": project_id,
                    "target_id": {"$in": target_ids},
                }
            },
            {
                "$group": {
                    "_id": {
                        "target_id": "$target_id",
                        "source": {"$ifNull": ["$source", ""]},
                    },
                    "finding_count": {"$sum": 1},
                    "high_score_count": {
                        "$sum": {
                            "$cond": [
                                {"$gte": [{"$ifNull": ["$attention_score", 0]}, 70]},
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
        ]
    ).to_list(len(target_ids))
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
        {"_id": 0, "task_id": 1, "status": 1, "updated_at": 1, "created_at": 1},
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
                    str(tasks_by_id[task_id].get("status") or "")
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
            str(item.get("root_target_name") or item.get("target_name") or "").casefold(),
            int(item.get("relation_depth") or 0),
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
    return [
        {
            "batch_tag": batch_tag,
            "target_count": len(values["target_ids"]),
            "root_count": len(values["root_target_ids"]),
        }
        for batch_tag, values in sorted(counts.items())
    ]


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
                current_id = str(
                    relations_by_target.get(current_id, {}).get("parent_target_id")
                    or ""
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
    root_stats_job = db[FINDINGS_COLLECTION].aggregate(
        [
            {
                "$match": {
                    "project_id": project_id,
                    "target_id": {"$in": root_ids},
                }
            },
            {
                "$group": {
                    "_id": "$target_id",
                    "finding_count": {"$sum": 1},
                    "high_score_finding_count": {
                        "$sum": {
                            "$cond": [
                                {"$gte": [{"$ifNull": ["$attention_score", 0]}, 70]},
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
        ]
    ).to_list(len(root_ids))
    target_docs, root_stat_rows = await asyncio.gather(target_docs_job, root_stats_job)
    targets_by_id = {
        str(item.get("target_id") or ""): item for item in target_docs
    }
    root_stats = {
        str(item.get("_id") or ""): item for item in root_stat_rows
    }
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
