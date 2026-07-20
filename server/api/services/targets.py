"""Target 领域服务：统一解析公司/机构实体并建立项目关联。"""
from __future__ import annotations

import asyncio
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import projects as projects_dao
from api.dao import mobile_collect as mobile_collect_dao
from api.dao import targets as targets_dao
from api.db.collections import (
    FOFA_ASSETS_COLLECTION,
    FINDINGS_COLLECTION,
    MOBILE_COLLECT_RECORDS_COLLECTION,
    SCHOLAR_CONTACTS_COLLECTION,
    SOURCE_DOCUMENT_LINKS_COLLECTION,
    TASKS_COLLECTION,
    XHS_NOTES_COLLECTION,
)


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
) -> dict[str, Any]:
    """把 company_meta 的项目级规范化结果挂到全局 Target 聚类。"""
    target = await targets_dao.upsert_target(
        db,
        name=normalized_name or input_name,
        target_type="company",
        root_domain=root_domain,
        root_domains=root_domains,
        aliases=[input_name, *(aliases or [])],
        source="company_normalize",
        normalization_version=normalization_version,
    )
    if project_id:
        await targets_dao.link_project_target(
            db,
            project_id=project_id,
            target=target,
            search_terms=[input_name],
            task_def_id=task_id,
        )
    return target


async def list_project_target_summaries(
    db: AsyncIOMotorDatabase,
    project_id: str,
    *,
    compact: bool = False,
) -> list[dict[str, Any]]:
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
                "$match": {
                    "project_id": project_id,
                    "target_id": {"$in": target_ids},
                }
            },
            {"$group": {"_id": "$target_id", "record_count": {"$sum": 1}}},
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
    scholar_counts_job = db[SCHOLAR_CONTACTS_COLLECTION].aggregate(
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
                                    {"$ne": [{"$ifNull": ["$target_id", ""]}, ""]},
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
                    "scholar_contact_count": {"$sum": 1},
                }
            },
        ]
    ).to_list(len(target_ids))
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
    findings_by_target = _by_id(finding_counts)
    xhs_by_target = _by_id(xhs_counts)
    scholars_by_target = _by_id(scholar_counts)
    project_docs_by_target = {
        str(item.get("_id") or ""): int(item.get("document_count") or 0)
        for item in project_document_counts
    }
    records_by_target = {
        str(item.get("_id") or ""): int(item.get("record_count") or 0)
        for item in record_counts
    }
    tasks_by_id = {str(item.get("task_id") or ""): item for item in task_docs}

    def _relation_payload(relation: dict[str, Any]) -> dict[str, Any]:
        if not compact:
            return relation
        return {
            key: relation.get(key)
            for key in (
                "project_target_id",
                "target_id",
                "target_type",
                "target_name",
                "root_domain",
                "parent_target_id",
                "relation_type",
                "relation_depth",
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
            "website_count": int(
                website_counts.get(str(relation.get("target_id") or ""), 0)
            ),
            "xhs_count": int(
                xhs_by_target.get(str(relation.get("target_id") or ""), {}).get(
                    "xhs_count", 0
                )
            ),
            "wechat_count": project_docs_by_target.get(
                str(relation.get("target_id") or ""), 0
            ),
            "bidding_count": int(
                bidding_counts.get(str(relation.get("target_id") or ""), 0)
            ),
            "scholar_contact_count": int(
                scholars_by_target.get(str(relation.get("target_id") or ""), {}).get(
                    "scholar_contact_count", 0
                )
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
            "collection_complete": bool(relation.get("last_collected_at")),
        }
        for relation in relations
    ]
    summaries.sort(
        key=lambda item: (
            bool(item.get("collection_complete")),
            int(item.get("high_score_finding_count") or 0),
            sum(
                int(item.get(field) or 0)
                for field in (
                    "website_count",
                    "xhs_count",
                    "wechat_count",
                    "bidding_count",
                    "scholar_contact_count",
                )
            ),
            str(item.get("target_name") or ""),
        ),
        reverse=True,
    )
    return summaries
