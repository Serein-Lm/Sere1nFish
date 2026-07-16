"""Target 领域服务：统一解析公司/机构实体并建立项目关联。"""
from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import projects as projects_dao
from api.dao import mobile_collect as mobile_collect_dao
from api.dao import targets as targets_dao
from api.db.collections import (
    MOBILE_COLLECT_RECORDS_COLLECTION,
    SOURCE_DOCUMENT_LINKS_COLLECTION,
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
    aliases: list[str] | None = None,
    task_id: str = "",
) -> dict[str, Any]:
    """把 company_meta 的项目级规范化结果挂到全局 Target 聚类。"""
    target = await targets_dao.upsert_target(
        db,
        name=normalized_name or input_name,
        target_type="company",
        root_domain=root_domain,
        aliases=[input_name, *(aliases or [])],
        source="company_normalize",
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
    db: AsyncIOMotorDatabase, project_id: str
) -> list[dict[str, Any]]:
    relations = await targets_dao.list_project_targets(db, project_id)
    target_ids = [str(item.get("target_id") or "") for item in relations]
    if not target_ids:
        return []
    counts = await db[SOURCE_DOCUMENT_LINKS_COLLECTION].aggregate(
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
    by_target = {str(item.get("_id") or ""): item for item in counts}
    project_document_counts = await db[SOURCE_DOCUMENT_LINKS_COLLECTION].aggregate(
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
    project_docs_by_target = {
        str(item.get("_id") or ""): int(item.get("document_count") or 0)
        for item in project_document_counts
    }
    record_counts = await db[MOBILE_COLLECT_RECORDS_COLLECTION].aggregate(
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
    records_by_target = {
        str(item.get("_id") or ""): int(item.get("record_count") or 0)
        for item in record_counts
    }
    return [
        {
            **relation,
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
        }
        for relation in relations
    ]
