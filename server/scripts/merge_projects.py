"""Merge project references into one retained project.

The command is dry-run by default. Use ``--execute`` only after taking a MongoDB
backup. References are discovered recursively so nested finding/profile links
are migrated together with top-level ``project_id`` fields.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo import MongoClient, UpdateOne
from pymongo.database import Database


OBJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)


def _project_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "project_id" and isinstance(item, (str, ObjectId)):
                project_id = str(item)
                if OBJECT_ID_PATTERN.fullmatch(project_id):
                    found.add(project_id)
            elif key == "project_ids" and isinstance(item, list):
                found.update(
                    str(entry)
                    for entry in item
                    if isinstance(entry, (str, ObjectId))
                    and OBJECT_ID_PATTERN.fullmatch(str(entry))
                )
            found.update(_project_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_project_ids(item))
    return found


def _deduplicate(values: Iterable[Any]) -> list[Any]:
    output: list[Any] = []
    for value in values:
        if value not in output:
            output.append(value)
    return output


def replace_project_references(
    value: Any,
    source_ids: set[str],
    target_id: str,
    *,
    parent_key: str = "",
) -> Any:
    if isinstance(value, (str, ObjectId)):
        if parent_key not in {"project_id", "project_ids"} or str(value) not in source_ids:
            return value
        return ObjectId(target_id) if isinstance(value, ObjectId) else target_id
    if isinstance(value, dict):
        return {
            key: replace_project_references(item, source_ids, target_id, parent_key=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        updated = [
            replace_project_references(item, source_ids, target_id, parent_key=parent_key)
            for item in value
        ]
        return _deduplicate(updated) if parent_key == "project_ids" else updated
    return value


def discover_reference_ids(db: Database[Any]) -> set[str]:
    found: set[str] = set()
    for collection_name in db.list_collection_names():
        if collection_name == "projects" or collection_name.startswith("system."):
            continue
        for document in db[collection_name].find({}):
            found.update(_project_ids(document))
    return found


def _oldest_project(projects: list[dict[str, Any]]) -> dict[str, Any]:
    return min(
        projects,
        key=lambda item: (
            item.get("created_at") or datetime.max.replace(tzinfo=timezone.utc),
            str(item["_id"]),
        ),
    )


def merge_projects(
    db: Database[Any],
    *,
    target_name: str,
    target_id: str = "",
    include_orphans: bool = False,
    execute: bool = False,
) -> dict[str, Any]:
    projects = list(db.projects.find({}))
    if not projects:
        raise RuntimeError("没有可合并的项目")

    by_id = {str(item["_id"]): item for item in projects}
    if target_id:
        target = by_id.get(target_id)
        if target is None:
            raise RuntimeError(f"目标项目不存在: {target_id}")
    else:
        target = _oldest_project(projects)
        target_id = str(target["_id"])

    existing_source_ids = set(by_id) - {target_id}
    reference_ids = discover_reference_ids(db)
    orphan_ids = reference_ids - set(by_id)
    source_ids = set(existing_source_ids)
    if include_orphans:
        source_ids.update(orphan_ids)

    changed_by_collection: dict[str, int] = {}
    top_level_backfills: dict[str, int] = {}
    if execute:
        if source_ids:
            for collection_name in db.list_collection_names():
                if collection_name == "projects" or collection_name.startswith("system."):
                    continue
                collection = db[collection_name]
                operations: list[UpdateOne] = []
                changed = 0
                for document in collection.find({}):
                    updated = replace_project_references(document, source_ids, target_id)
                    if updated == document:
                        continue
                    operations.append(
                        UpdateOne(
                            {"_id": document["_id"]},
                            {"$set": {key: value for key, value in updated.items() if key != "_id"}},
                        )
                    )
                    changed += 1
                    if len(operations) >= 500:
                        collection.bulk_write(operations, ordered=False)
                        operations.clear()
                if operations:
                    collection.bulk_write(operations, ordered=False)
                if changed:
                    changed_by_collection[collection_name] = changed

        # Legacy Web Tagging documents only carried project_id inside findings.
        # A single-project merge must backfill the top-level field used by list
        # and dashboard queries, otherwise the records remain invisible.
        if "web_tagging_results" in db.list_collection_names():
            web_project_id = ObjectId(target_id)
            backfill = db.web_tagging_results.update_many(
                {"project_id": {"$ne": web_project_id}},
                {"$set": {"project_id": web_project_id}},
            )
            if backfill.modified_count:
                top_level_backfills["web_tagging_results"] = backfill.modified_count

        contents = _deduplicate(
            content
            for project in projects
            for content in (project.get("contents") or [])
            if content
        )
        merged_projects = list(target.get("merged_projects") or [])
        merged_projects.extend(
            {
                "project_id": project_id,
                "name": project.get("name"),
                "description": project.get("description"),
            }
            for project_id, project in by_id.items()
            if project_id != target_id
        )
        merged_projects.extend(
            {"project_id": project_id, "name": None, "description": "历史孤儿数据"}
            for project_id in sorted(orphan_ids)
            if project_id in source_ids
        )
        merged_projects = list(
            {
                str(item.get("project_id")): item
                for item in merged_projects
                if item.get("project_id")
            }.values()
        )
        db.projects.update_one(
            {"_id": target["_id"]},
            {
                "$set": {
                    "name": target_name,
                    "description": "用于统一展示原有项目的任务、发现、画像与采集数据。",
                    "contents": contents,
                    "merged_projects": merged_projects,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        db.projects.delete_many({"_id": {"$in": [by_id[item]["_id"] for item in existing_source_ids]}})

    return {
        "execute": execute,
        "target_id": target_id,
        "target_name": target_name,
        "existing_source_ids": sorted(existing_source_ids),
        "orphan_ids": sorted(orphan_ids),
        "included_source_ids": sorted(source_ids),
        "changed_by_collection": changed_by_collection,
        "top_level_backfills": top_level_backfills,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="将项目及其关联数据归并到一个项目")
    parser.add_argument("--target-id", default="", help="保留的项目 ID；默认保留最早项目")
    parser.add_argument("--name", default="展示项目", help="合并后的项目名称")
    parser.add_argument("--include-orphans", action="store_true", help="同时合并没有项目卡的历史引用")
    parser.add_argument("--execute", action="store_true", help="执行写入；省略时仅预览")
    args = parser.parse_args()

    client = MongoClient(os.getenv("MONGODB_URI", "mongodb://mongo:27017"))
    database_name = os.getenv("MONGODB_DATABASE", "Sere1nG0Fish")
    try:
        result = merge_projects(
            client[database_name],
            target_name=args.name,
            target_id=args.target_id,
            include_orphans=args.include_orphans,
            execute=args.execute,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
