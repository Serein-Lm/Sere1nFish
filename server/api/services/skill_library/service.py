"""Application service for importing Skill packages through source adapters."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import skill_resources as resources_dao
from api.dao import skills as skills_dao
from api.db.collections import SKILLS_COLLECTION

from .contracts import SkillSourceAdapter
from .filesystem import FilesystemSkillSourceAdapter


_DEFAULT_EXTERNAL_ROOT = Path("/opt/sere1nfish-skills")


def configured_skill_sources() -> list[SkillSourceAdapter]:
    external_root = Path(
        os.getenv("EXTERNAL_SKILLS_DIR", str(_DEFAULT_EXTERNAL_ROOT))
    ).expanduser()
    if not external_root.exists() and Path("/root/skills").exists():
        external_root = Path("/root/skills")
    return [
        FilesystemSkillSourceAdapter(
            root=external_root,
            source_key="external-documents",
            display_name="external-skill-library",
            default_category="document-output",
            default_phases=("finalize",),
        )
    ]


async def sync_external_skill_sources(
    db: AsyncIOMotorDatabase,
    *,
    overwrite: bool = False,
    prune_stale: bool = False,
    dry_run: bool = False,
    sources: list[SkillSourceAdapter] | None = None,
) -> dict[str, Any]:
    """Converge configured external sources without overwriting DB edits by default."""
    await resources_dao.ensure_indexes(db)
    adapters = sources if sources is not None else configured_skill_sources()
    summary: dict[str, Any] = {
        "sources": [],
        "discovered": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "resources": 0,
        "deleted": 0,
        "dry_run": dry_run,
    }
    seen_by_source: dict[str, set[str]] = {}
    all_tags: set[str] = set()

    for adapter in adapters:
        available = adapter.available()
        packages = adapter.discover() if available else []
        source_item = {
            "key": adapter.source_key,
            "name": adapter.display_name,
            "available": available,
            "skills": len(packages),
        }
        summary["sources"].append(source_item)
        summary["discovered"] += len(packages)
        seen_by_source[adapter.source_key] = {package.slug for package in packages}

        for package in packages:
            all_tags.update(package.tags)
            existing = await skills_dao.get_skill_by_slug(db, package.slug)
            if existing and not overwrite:
                summary["skipped"] += 1
                resource_count = await resources_dao.count_skill_resources(
                    db, str(existing["skill_id"])
                )
                existing_meta = (
                    existing.get("meta") if isinstance(existing.get("meta"), dict) else {}
                )
                source_owned = existing_meta.get("source_key") == adapter.source_key
                manifest_changed = (
                    source_owned
                    and existing_meta.get("manifest_hash") != package.meta.get("manifest_hash")
                )
                if not resource_count or manifest_changed:
                    if dry_run:
                        summary["resources"] += len(package.resources)
                        continue
                    summary["resources"] += await resources_dao.replace_skill_resources(
                        db,
                        skill_id=str(existing["skill_id"]),
                        resources=[item.as_document() for item in package.resources],
                    )
                    if manifest_changed:
                        source_meta_keys = {
                            "resource_count",
                            "resource_bytes",
                            "manifest_hash",
                            "source_path",
                            "source_version",
                        }
                        await skills_dao.update_skill(
                            db,
                            str(existing["skill_id"]),
                            meta={
                                **existing_meta,
                                **{
                                    key: value
                                    for key, value in package.meta.items()
                                    if key in source_meta_keys
                                },
                            },
                        )
                continue

            if dry_run:
                summary["updated" if existing else "created"] += 1
                summary["resources"] += len(package.resources)
                continue

            await skills_dao.upsert_category_by_slug(
                db,
                package.category,
                {
                    "name": "文档产物" if package.category == "document-output" else package.category,
                    "description": "可渐进加载的文档读取、编辑与生成能力",
                    "sort_order": 90,
                },
            )
            doc = await skills_dao.upsert_skill_by_slug(
                db,
                package.slug,
                {
                    "name": package.name,
                    "category": package.category,
                    "description": package.description,
                    "content_raw": package.content_raw,
                    "tags": package.tags,
                    "triggers": package.triggers,
                    "anti_triggers": package.anti_triggers,
                    "aliases": package.aliases,
                    "requires": package.requires,
                    "related": package.related,
                    "file_signals": package.file_signals,
                    "risk_signals": package.risk_signals,
                    "priority": package.priority,
                    "meta": package.meta,
                    "status": "approved",
                    "created_by": "system",
                },
            )
            summary["updated" if existing else "created"] += 1
            summary["resources"] += await resources_dao.replace_skill_resources(
                db,
                skill_id=str(doc["skill_id"]),
                resources=[item.as_document() for item in package.resources],
            )

    if all_tags and not dry_run:
        await skills_dao.bulk_upsert_tags(db, sorted(all_tags))

    if prune_stale and not dry_run:
        for source_key, slugs in seen_by_source.items():
            cursor = db[SKILLS_COLLECTION].find(
                {"meta.source_key": source_key, "slug": {"$nin": sorted(slugs)}},
                {"_id": 0, "skill_id": 1},
            )
            stale_ids = [str(doc["skill_id"]) async for doc in cursor]
            for skill_id in stale_ids:
                await resources_dao.delete_skill_resources(db, skill_id)
                if await skills_dao.delete_skill(db, skill_id):
                    summary["deleted"] += 1
    return summary


async def sync_embedded_reference_resources(
    db: AsyncIOMotorDatabase,
) -> dict[str, int]:
    """Migrate legacy embedded references into the shared resource tree."""
    await resources_dao.ensure_indexes(db)
    cursor = db[SKILLS_COLLECTION].find(
        {"meta.reference_contents": {"$exists": True, "$ne": {}}},
        {"_id": 0, "skill_id": 1, "meta.reference_contents": 1},
    )
    skill_count = 0
    resource_count = 0
    async for skill in cursor:
        skill_id = str(skill["skill_id"])
        if await resources_dao.count_skill_resources(db, skill_id):
            continue
        raw_references = (skill.get("meta") or {}).get("reference_contents")
        if not isinstance(raw_references, dict) or not raw_references:
            continue
        documents: list[dict[str, Any]] = [
            {
                "path": "references",
                "parent_path": "",
                "name": "references",
                "kind": "directory",
                "role": "reference",
                "content_type": "inode/directory",
                "size": 0,
                "content_hash": "",
            }
        ]
        for name, value in sorted(raw_references.items()):
            relative = PurePosixPath(str(name or "").strip().lstrip("/"))
            if not str(relative) or str(relative) == "." or ".." in relative.parts:
                continue
            content = str(value or "")
            encoded = content.encode("utf-8")
            documents.append(
                {
                    "path": f"references/{relative.as_posix()}",
                    "parent_path": "references",
                    "name": relative.name,
                    "kind": "file",
                    "role": "reference",
                    "content_type": "text/markdown",
                    "size": len(encoded),
                    "content_hash": hashlib.sha256(encoded).hexdigest(),
                    "content": content,
                }
            )
        if len(documents) == 1:
            continue
        resource_count += await resources_dao.replace_skill_resources(
            db,
            skill_id=skill_id,
            resources=documents,
        )
        skill_count += 1
    return {"skills": skill_count, "resources": resource_count}
