from __future__ import annotations

import asyncio
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import bidding as bidding_dao
from api.dao import project_groups as project_groups_dao
from api.dao import projects as projects_dao
from api.dao import source_documents as source_documents_dao
from api.dao import targets as targets_dao
from api.db.collections import PROJECT_TARGETS_COLLECTION
from api.models.projects import ProjectPartitionRequest


_RELATION_FIELDS = (
    "root_target_id",
    "root_target_name",
    "parent_target_id",
    "parent_target_name",
    "relation_type",
    "relation_depth",
    "ownership_percent",
    "effective_ownership_percent",
    "relation_source",
    "lineage_target_ids",
    "lineage_target_names",
)


def select_batch_relations(
    relations: list[dict[str, Any]],
    batch_tag: str,
) -> list[dict[str, Any]]:
    """Select tagged roots and every stored descendant in stable hierarchy order."""
    roots = {
        str(item.get("target_id") or "")
        for item in relations
        if batch_tag in list(item.get("batch_tags") or [])
        and int(item.get("relation_depth") or 0) == 0
        and str(item.get("target_id") or "")
    }
    selected = [
        item
        for item in relations
        if str(item.get("target_id") or "") in roots
        or str(item.get("root_target_id") or "") in roots
        or str(item.get("parent_target_id") or "") in roots
    ]
    return sorted(
        selected,
        key=lambda item: (
            int(item.get("relation_depth") or 0),
            str(item.get("target_name") or ""),
        ),
    )


async def _ensure_group(
    db: AsyncIOMotorDatabase,
    *,
    name: str,
    description: str,
) -> dict[str, Any]:
    group = await project_groups_dao.get_group_by_name(db, name)
    if group:
        return group
    return await project_groups_dao.create_group(
        db,
        name=name,
        description=description,
    )


async def _link_relations(
    db: AsyncIOMotorDatabase,
    *,
    destination_project_id: str,
    relations: list[dict[str, Any]],
) -> int:
    semaphore = asyncio.Semaphore(24)

    async def _link_one(source: dict[str, Any]) -> bool:
        async with semaphore:
            target_id = str(source.get("target_id") or "")
            target = await targets_dao.get_target(db, target_id)
            if not target:
                return False
            relation = {
                field: source[field]
                for field in _RELATION_FIELDS
                if field in source and source[field] is not None
            }
            await targets_dao.link_project_target(
                db,
                project_id=destination_project_id,
                target=target,
                search_terms=list(source.get("search_terms") or []),
                search_terms_by_channel=dict(
                    source.get("search_terms_by_channel") or {}
                ),
                objectives=list(source.get("objectives") or []),
                relation=relation or None,
                batch_tags=list(source.get("batch_tags") or []),
                replace_search_terms=True,
            )
            return True

    results = await asyncio.gather(*(_link_one(source) for source in relations))
    return sum(results)


async def partition_project_by_batch_tags(
    db: AsyncIOMotorDatabase,
    *,
    source_project_id: str,
    request: ProjectPartitionRequest,
) -> dict[str, Any]:
    """Create idempotent batch projects while preserving the source as archive."""
    source_project = await projects_dao.get_project(db, source_project_id)
    if not source_project:
        raise ValueError("源项目不存在")
    relations = [
        doc
        async for doc in db[PROJECT_TARGETS_COLLECTION].find(
            {"project_id": source_project_id, "active": {"$ne": False}},
            {"_id": 0},
        )
    ]
    plans: list[dict[str, Any]] = []
    selected_by_tag: dict[str, list[dict[str, Any]]] = {}
    for spec in request.batches:
        selected = select_batch_relations(relations, spec.batch_tag)
        selected_by_tag[spec.batch_tag] = selected
        target_ids = [str(item.get("target_id") or "") for item in selected]
        plans.append(
            {
                "batch_tag": spec.batch_tag,
                "project_name": spec.project_name,
                "root_target_count": sum(
                    1 for item in selected if int(item.get("relation_depth") or 0) == 0
                ),
                "target_relation_count": len(selected),
                "target_ids": target_ids,
            }
        )
    result: dict[str, Any] = {
        "dry_run": request.dry_run,
        "source_project_id": source_project_id,
        "source_project_name": str(source_project.get("name") or ""),
        "group_name": request.group_name,
        "batches": plans,
    }
    if request.dry_run:
        return result

    group = await _ensure_group(
        db,
        name=request.group_name.strip(),
        description=request.group_description.strip(),
    )
    group_id = str(group.get("group_id") or "")
    if request.keep_source_project_in_group:
        await projects_dao.update_project(
            db,
            source_project_id,
            {"group_id": group_id},
        )

    applied: list[dict[str, Any]] = []
    for spec in request.batches:
        project = await projects_dao.upsert_get_project_by_name(
            db,
            spec.project_name.strip(),
            spec.description.strip() or None,
        )
        destination_project_id = str(project.get("_id") or "")
        await projects_dao.update_project(
            db,
            destination_project_id,
            {
                "group_id": group_id,
                "description": spec.description.strip() or project.get("description"),
                "partition_source_project_id": source_project_id,
                "partition_batch_tag": spec.batch_tag,
            },
        )
        selected = selected_by_tag[spec.batch_tag]
        target_ids = [str(item.get("target_id") or "") for item in selected]
        linked = await _link_relations(
            db,
            destination_project_id=destination_project_id,
            relations=selected,
        )
        source_links = (
            await source_documents_dao.clone_project_links(
                db,
                source_project_id=source_project_id,
                destination_project_id=destination_project_id,
                target_ids=target_ids,
            )
            if request.copy_source_links
            else 0
        )
        bidding_links = (
            await bidding_dao.clone_project_links(
                db,
                source_project_id=source_project_id,
                destination_project_id=destination_project_id,
                target_ids=target_ids,
            )
            if request.copy_bidding_links
            else 0
        )
        applied.append(
            {
                "batch_tag": spec.batch_tag,
                "project_id": destination_project_id,
                "project_name": spec.project_name,
                "target_relations_linked": linked,
                "source_links_copied": source_links,
                "bidding_links_copied": bidding_links,
            }
        )
    result.update({"group_id": group_id, "applied": applied})
    return result
