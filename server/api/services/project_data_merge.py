"""Idempotent merge of project-scoped facts into partitioned projects.

Project-specific facts receive destination-scoped stable IDs. Immutable or
audit-like records remain single-copy and gain ``project_ids`` associations.
The service is deliberately independent of HTTP so partitioning, maintenance
jobs, and future project consolidation all use the same merge semantics.
"""
from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import UpdateOne

from api.dao import company_meta as company_meta_dao
from api.dao import bidding as bidding_dao
from api.dao import findings as findings_dao
from api.dao import fofa_assets as fofa_assets_dao
from api.dao import scholar_contact as scholar_contact_dao
from api.dao import source_documents as source_documents_dao
from api.dao import targets as targets_dao
from api.db.collections import (
    BIDDING_RECORD_LINKS_COLLECTION,
    COMPANY_META_COLLECTION,
    COMPANY_SCAN_COLLECTION,
    COPYWRITINGS_COLLECTION,
    FINDINGS_COLLECTION,
    FOFA_ASSETS_COLLECTION,
    MOBILE_COLLECT_RECORDS_COLLECTION,
    MOBILE_COLLECT_TASKS_COLLECTION,
    MOBILE_SCREENSHOTS_COLLECTION,
    PROFILES_COLLECTION,
    PROJECTS_COLLECTION,
    PROJECT_TARGETS_COLLECTION,
    SCHOLAR_ARTICLES_COLLECTION,
    SCHOLAR_CONTACTS_COLLECTION,
    SOURCE_DOCUMENT_LINKS_COLLECTION,
    STORAGE_OBJECTS_COLLECTION,
    TASKS_COLLECTION,
    TARGET_RESEARCH_COLLECTION,
    TOKEN_USAGE_RECORDS_COLLECTION,
    URL_SCAN_RESULTS_COLLECTION,
    URL_SCAN_TASKS_COLLECTION,
)


_BATCH_SIZE = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if item not in (None, "")]
    return [value] if value != "" else []


def _unique_strings(values: list[Any]) -> list[str]:
    return list(
        dict.fromkeys(
            normalized
            for value in values
            if (normalized := str(value or "").strip())
        )
    )


@dataclass(frozen=True)
class MergeDestination:
    project_id: str
    target_ids: frozenset[str]
    batch_tag: str = ""


@dataclass
class MergeStats:
    name: str
    source_records: int = 0
    planned_relations: int = 0
    existing_relations: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    unmapped_records: int = 0
    invalid_records: int = 0


TargetResolver = Callable[[dict[str, Any]], list[str]]
IdentityFactory = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class CloneAdapter:
    name: str
    collection: str
    identity_field: str
    identity_factory: IdentityFactory
    source_identity_field: str
    target_resolver: TargetResolver
    array_fields: tuple[str, ...] = ()
    max_fields: tuple[str, ...] = ("updated_at",)
    min_fields: tuple[str, ...] = ("created_at",)


@dataclass(frozen=True)
class SharedAssociationAdapter:
    name: str
    collection: str
    target_resolver: TargetResolver


def _primary_target_ids(document: dict[str, Any]) -> list[str]:
    target_id = str(document.get("target_id") or "").strip()
    return [target_id] if target_id else []


def _asset_target_ids(document: dict[str, Any]) -> list[str]:
    return _unique_strings(
        [document.get("target_id"), *_values(document.get("target_ids"))]
    )


def _scholar_target_ids(document: dict[str, Any]) -> list[str]:
    primary = _primary_target_ids(document)
    verified = _unique_strings(_values(document.get("verified_target_ids")))
    verification = document.get("target_verification") or {}
    if isinstance(verification, dict):
        verified.extend(
            str(target_id)
            for target_id, context in verification.items()
            if isinstance(context, dict) and context.get("verified") is True
        )
    return _unique_strings([*primary, *verified])


def _company_scan_target_ids(document: dict[str, Any]) -> list[str]:
    result = document.get("result") or {}
    identity = result.get("identity") or {} if isinstance(result, dict) else {}
    target_id = str(identity.get("target_id") or "").strip()
    return [target_id] if target_id else _primary_target_ids(document)


def route_destinations(
    target_ids: list[str],
    destinations: list[MergeDestination],
) -> list[MergeDestination]:
    """Resolve all destination projects once, including intentional overlaps."""
    selected = set(_unique_strings(target_ids))
    if not selected:
        return []
    return [
        destination
        for destination in destinations
        if selected.intersection(destination.target_ids)
    ]


def _finding_identity(document: dict[str, Any]) -> str:
    if document.get("source") == "mobile" and document.get("contact_id"):
        return findings_dao.mobile_profile_finding_id(
            str(document.get("project_id") or ""),
            str(document.get("contact_id") or ""),
        )
    return findings_dao.stable_finding_id(document)


_CLONE_ADAPTERS: tuple[CloneAdapter, ...] = (
    CloneAdapter(
        name="findings",
        collection=FINDINGS_COLLECTION,
        identity_field="finding_id",
        identity_factory=_finding_identity,
        source_identity_field="finding_id",
        target_resolver=_primary_target_ids,
        array_fields=(
            "task_ids",
            "target_ids",
            "evidence_history",
            "evidence_refs",
            "source_document_ids",
        ),
        max_fields=("updated_at", "attention_score"),
    ),
    CloneAdapter(
        name="assets",
        collection=FOFA_ASSETS_COLLECTION,
        identity_field="asset_id",
        identity_factory=lambda document: fofa_assets_dao.fofa_asset_id(
            str(document.get("project_id") or ""),
            str(document.get("host") or ""),
            str(document.get("ip") or ""),
            str(document.get("port") or ""),
        ),
        source_identity_field="asset_id",
        target_resolver=_asset_target_ids,
        array_fields=(
            "sources",
            "source_queries",
            "task_ids",
            "target_ids",
            "fingerprints",
        ),
        max_fields=("updated_at", "last_seen_at"),
    ),
    CloneAdapter(
        name="company_meta",
        collection=COMPANY_META_COLLECTION,
        identity_field="meta_id",
        identity_factory=lambda document: company_meta_dao.company_meta_id(
            str(document.get("project_id") or ""),
            str(document.get("input_name") or ""),
        ),
        source_identity_field="meta_id",
        target_resolver=_primary_target_ids,
        array_fields=("aliases", "icp_domains", "task_ids", "target_ids"),
        max_fields=("updated_at", "confidence"),
    ),
    CloneAdapter(
        name="scholar_articles",
        collection=SCHOLAR_ARTICLES_COLLECTION,
        identity_field="doc_id",
        identity_factory=lambda document: scholar_contact_dao.scholar_article_id(
            str(document.get("project_id") or ""),
            str(document.get("article_id") or ""),
        ),
        source_identity_field="doc_id",
        target_resolver=_scholar_target_ids,
        array_fields=("source_keys", "task_ids", "target_ids"),
        max_fields=("updated_at", "unit_verified"),
    ),
    CloneAdapter(
        name="scholar_contacts",
        collection=SCHOLAR_CONTACTS_COLLECTION,
        identity_field="doc_id",
        identity_factory=lambda document: scholar_contact_dao.scholar_contact_id(
            str(document.get("project_id") or ""),
            str(document.get("email") or ""),
            str(document.get("article_id") or ""),
        ),
        source_identity_field="doc_id",
        target_resolver=_scholar_target_ids,
        array_fields=(
            "task_ids",
            "target_ids",
            "verified_target_ids",
        ),
        max_fields=(
            "updated_at",
            "unit_verified",
            "is_corresponding",
            "verification_authoritative",
        ),
    ),
)


_SHARED_ASSOCIATION_ADAPTERS: tuple[SharedAssociationAdapter, ...] = (
    SharedAssociationAdapter(
        "mobile_collect_records",
        MOBILE_COLLECT_RECORDS_COLLECTION,
        _primary_target_ids,
    ),
    SharedAssociationAdapter(
        "target_research",
        TARGET_RESEARCH_COLLECTION,
        _primary_target_ids,
    ),
    SharedAssociationAdapter(
        "url_scan_results",
        URL_SCAN_RESULTS_COLLECTION,
        _primary_target_ids,
    ),
    SharedAssociationAdapter(
        "url_scan_tasks",
        URL_SCAN_TASKS_COLLECTION,
        _primary_target_ids,
    ),
    SharedAssociationAdapter(
        "company_scan_results",
        COMPANY_SCAN_COLLECTION,
        _company_scan_target_ids,
    ),
)


def _filtered_target_ids(
    document: dict[str, Any],
    destination: MergeDestination,
    routed_target_ids: list[str],
) -> list[str]:
    candidates = _unique_strings(
        [
            document.get("target_id"),
            *_values(document.get("target_ids")),
            *routed_target_ids,
        ]
    )
    return [value for value in candidates if value in destination.target_ids]


def prepare_project_clone(
    adapter: CloneAdapter,
    source: dict[str, Any],
    destination: MergeDestination,
) -> tuple[dict[str, Any], str]:
    """Build one destination fact and regenerate its project-scoped identity."""
    clone = copy.deepcopy(source)
    clone.pop("_id", None)
    clone.pop("project_ids", None)
    routed_target_ids = adapter.target_resolver(source)
    relevant_target_ids = _filtered_target_ids(
        source,
        destination,
        routed_target_ids,
    )
    clone["project_id"] = destination.project_id
    current_target_id = str(source.get("target_id") or "").strip()
    if current_target_id in destination.target_ids:
        clone["target_id"] = current_target_id
    elif relevant_target_ids:
        clone["target_id"] = relevant_target_ids[0]
    if relevant_target_ids or "target_ids" in clone:
        clone["target_ids"] = relevant_target_ids
    verification = clone.get("target_verification")
    if isinstance(verification, dict):
        clone["target_verification"] = {
            target_id: value
            for target_id, value in verification.items()
            if target_id in destination.target_ids
        }
    identity = str(adapter.identity_factory(clone) or "").strip()
    clone[adapter.identity_field] = identity
    return clone, identity


def _upsert_for_clone(
    adapter: CloneAdapter,
    source: dict[str, Any],
    clone: dict[str, Any],
    identity: str,
    source_project_id: str,
) -> UpdateOne:
    source_identity = str(source.get(adapter.source_identity_field) or "").strip()
    mutable = copy.deepcopy(clone)
    set_fields = {
        adapter.identity_field: identity,
        "project_id": str(clone.get("project_id") or ""),
    }
    add_to_set: dict[str, Any] = {
        "merged_from_project_ids": source_project_id,
    }
    if source_identity:
        add_to_set["merged_source_ids"] = source_identity
    for field in adapter.array_fields:
        values = _values(mutable.pop(field, None))
        if values:
            add_to_set[field] = {"$each": values}
    max_fields: dict[str, Any] = {}
    for field in adapter.max_fields:
        value = mutable.pop(field, None)
        if value is not None:
            max_fields[field] = value
    min_fields: dict[str, Any] = {}
    for field in adapter.min_fields:
        value = mutable.pop(field, None)
        if value is not None:
            min_fields[field] = value
    mutable.pop(adapter.identity_field, None)
    mutable.pop("project_id", None)
    mutable.pop("merged_from_project_ids", None)
    mutable.pop("merged_source_ids", None)
    update: dict[str, Any] = {
        "$set": set_fields,
        "$setOnInsert": mutable,
        "$addToSet": add_to_set,
    }
    if max_fields:
        update["$max"] = max_fields
    if min_fields:
        update["$min"] = min_fields
    return UpdateOne(
        {adapter.identity_field: identity},
        update,
        upsert=True,
    )


async def _existing_identities(
    db: AsyncIOMotorDatabase,
    *,
    collection: str,
    identity_field: str,
    identities: list[str],
) -> set[str]:
    if not identities:
        return set()
    cursor = db[collection].find(
        {identity_field: {"$in": identities}},
        {"_id": 0, identity_field: 1},
    )
    return {
        str(document.get(identity_field) or "")
        async for document in cursor
    }


async def _merge_clone_adapter(
    db: AsyncIOMotorDatabase,
    *,
    adapter: CloneAdapter,
    source_project_id: str,
    destinations: list[MergeDestination],
    dry_run: bool,
    track_routes: bool = False,
) -> tuple[MergeStats, dict[str, list[tuple[str, str]]]]:
    stats = MergeStats(name=adapter.name)
    source_routes: dict[str, list[tuple[str, str]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    pending: list[tuple[dict[str, Any], dict[str, Any], str]] = []

    async def flush() -> None:
        nonlocal pending
        if not pending:
            return
        identities = [identity for _source, _clone, identity in pending]
        existing = await _existing_identities(
            db,
            collection=adapter.collection,
            identity_field=adapter.identity_field,
            identities=identities,
        )
        stats.existing_relations += len(existing)
        if dry_run:
            stats.inserted += len(identities) - len(existing)
            stats.unchanged += len(existing)
            pending = []
            return
        operations = [
            _upsert_for_clone(
                adapter,
                source,
                clone,
                identity,
                source_project_id,
            )
            for source, clone, identity in pending
        ]
        result = await db[adapter.collection].bulk_write(operations, ordered=False)
        stats.inserted += int(result.upserted_count or 0)
        stats.updated += int(result.modified_count or 0)
        stats.unchanged += max(
            0,
            len(operations)
            - int(result.upserted_count or 0)
            - int(result.modified_count or 0),
        )
        pending = []

    cursor = db[adapter.collection].find(
        {"project_id": source_project_id},
        {"_id": 0},
    )
    async for source in cursor:
        stats.source_records += 1
        target_ids = adapter.target_resolver(source)
        selected = route_destinations(target_ids, destinations)
        if not selected:
            stats.unmapped_records += 1
            continue
        source_identity = str(source.get(adapter.source_identity_field) or "")
        valid = False
        for destination in selected:
            clone, identity = prepare_project_clone(adapter, source, destination)
            if not identity:
                continue
            operation_key = (destination.project_id, identity)
            if operation_key in seen:
                continue
            seen.add(operation_key)
            valid = True
            stats.planned_relations += 1
            pending.append((source, clone, identity))
            if track_routes and source_identity:
                source_routes[source_identity].append(
                    (destination.project_id, identity)
                )
            if len(pending) >= _BATCH_SIZE:
                await flush()
        if not valid and selected:
            stats.invalid_records += 1
    await flush()
    return stats, dict(source_routes)


async def _merge_finding_dependents(
    db: AsyncIOMotorDatabase,
    *,
    source_project_id: str,
    finding_routes: dict[str, list[tuple[str, str]]],
    dry_run: bool,
) -> list[MergeStats]:
    results: list[MergeStats] = []
    for name, collection in (
        ("copywritings", COPYWRITINGS_COLLECTION),
        ("profiles", PROFILES_COLLECTION),
    ):
        stats = MergeStats(name=name)
        seen: set[str] = set()
        pending: list[tuple[str, dict[str, Any], str]] = []
        query = (
            {"project_id": source_project_id}
            if name == "copywritings"
            else {"finding_id": {"$in": list(finding_routes)}}
        )
        cursor = db[collection].find(query, {"_id": 0}).sort("created_at", -1)
        async for source in cursor:
            stats.source_records += 1
            source_finding_id = str(source.get("finding_id") or "")
            routes = finding_routes.get(source_finding_id, [])
            if not routes:
                stats.unmapped_records += 1
                continue
            for destination_project_id, finding_id in routes:
                if finding_id in seen:
                    continue
                seen.add(finding_id)
                clone = copy.deepcopy(source)
                clone["project_id"] = destination_project_id
                clone["finding_id"] = finding_id
                clone["merged_from_project_id"] = source_project_id
                stats.planned_relations += 1
                pending.append((finding_id, clone, source_finding_id))
        for offset in range(0, len(pending), _BATCH_SIZE):
            batch = pending[offset : offset + _BATCH_SIZE]
            identities = [finding_id for finding_id, _clone, _source_id in batch]
            existing = await _existing_identities(
                db,
                collection=collection,
                identity_field="finding_id",
                identities=identities,
            )
            stats.existing_relations += len(existing)
            if dry_run:
                stats.inserted += len(batch) - len(existing)
                stats.unchanged += len(existing)
                continue
            operations: list[UpdateOne] = []
            for finding_id, clone, source_finding_id in batch:
                created_at = clone.pop("created_at", None)
                clone.pop("_id", None)
                update: dict[str, Any] = {
                    "$setOnInsert": clone,
                    "$addToSet": {
                        "merged_from_project_ids": source_project_id,
                        "merged_source_finding_ids": source_finding_id,
                    },
                }
                if created_at is not None:
                    update["$min"] = {"created_at": created_at}
                operations.append(
                    UpdateOne({"finding_id": finding_id}, update, upsert=True)
                )
            result = await db[collection].bulk_write(operations, ordered=False)
            stats.inserted += int(result.upserted_count or 0)
            stats.updated += int(result.modified_count or 0)
            stats.unchanged += max(
                0,
                len(operations)
                - int(result.upserted_count or 0)
                - int(result.modified_count or 0),
            )
        results.append(stats)
    return results


async def _merge_project_target_metadata(
    db: AsyncIOMotorDatabase,
    *,
    source_project_id: str,
    destinations: list[MergeDestination],
    dry_run: bool,
) -> MergeStats:
    stats = MergeStats(name="project_targets")
    cursor = db[PROJECT_TARGETS_COLLECTION].find(
        {"project_id": source_project_id, "active": {"$ne": False}},
        {"_id": 0},
    )
    async for source in cursor:
        stats.source_records += 1
        selected = route_destinations(_primary_target_ids(source), destinations)
        if not selected:
            stats.unmapped_records += 1
            continue
        for destination in selected:
            target_id = str(source.get("target_id") or "")
            query = {
                "project_id": destination.project_id,
                "target_id": target_id,
            }
            existing = await db[PROJECT_TARGETS_COLLECTION].find_one(
                query,
                {"_id": 0, "project_target_id": 1},
            )
            stats.planned_relations += 1
            if existing:
                stats.existing_relations += 1
            if dry_run:
                if existing:
                    stats.unchanged += 1
                else:
                    stats.invalid_records += 1
                continue
            if not existing:
                stats.invalid_records += 1
                continue
            additions: dict[str, Any] = {
                "merged_from_project_ids": source_project_id,
            }
            for field in (
                "search_terms",
                "objectives",
                "task_def_ids",
                "run_task_ids",
                "batch_tags",
                "short_names",
                "scan_aliases",
                "root_domains",
                "scan_history",
            ):
                values = _values(source.get(field))
                if values:
                    additions[field] = {"$each": values}
            channel_terms = source.get("search_terms_by_channel") or {}
            if isinstance(channel_terms, dict):
                for channel, values in channel_terms.items():
                    normalized = "".join(
                        char
                        for char in str(channel).strip().lower()
                        if char.isalnum() or char == "_"
                    )
                    items = _values(values)
                    if normalized and items:
                        additions[f"search_terms_by_channel.{normalized}"] = {
                            "$each": items
                        }
            update: dict[str, Any] = {"$addToSet": additions}
            max_fields = {
                field: source[field]
                for field in (
                    "last_seen_at",
                    "last_collected_at",
                )
                if source.get(field) is not None
            }
            min_fields = {
                field: source[field]
                for field in ("created_at", "first_seen_at")
                if source.get(field) is not None
            }
            if max_fields:
                update["$max"] = max_fields
            if min_fields:
                update["$min"] = min_fields
            result = await db[PROJECT_TARGETS_COLLECTION].update_one(query, update)
            stats.updated += int(result.modified_count or 0)
            if not result.modified_count:
                stats.unchanged += 1
            for channel, coverage in (source.get("scan_coverage") or {}).items():
                if not isinstance(coverage, dict):
                    continue
                normalized_channel = "".join(
                    char
                    for char in str(channel).strip().lower()
                    if char.isalnum() or char == "_"
                )
                if not normalized_channel:
                    continue
                path = f"scan_coverage.{normalized_channel}"
                await db[PROJECT_TARGETS_COLLECTION].update_one(
                    {**query, path: {"$exists": False}},
                    {"$set": {path: coverage}},
                )
            if source.get("scan_profile"):
                await db[PROJECT_TARGETS_COLLECTION].update_one(
                    {**query, "scan_profile": {"$exists": False}},
                    {
                        "$set": {
                            "scan_profile": source["scan_profile"],
                            "scan_profile_version": source.get(
                                "scan_profile_version", 0
                            ),
                            "scan_profile_fingerprint": source.get(
                                "scan_profile_fingerprint", ""
                            ),
                        }
                    },
                )
    return stats


async def _associate_shared_records(
    db: AsyncIOMotorDatabase,
    *,
    adapter: SharedAssociationAdapter,
    source_project_id: str,
    destinations: list[MergeDestination],
    dry_run: bool,
) -> MergeStats:
    stats = MergeStats(name=adapter.name)
    pending: list[UpdateOne] = []
    cursor = db[adapter.collection].find(
        {"project_id": source_project_id},
        {"_id": 1, "project_id": 1, "project_ids": 1, "target_id": 1, "result.identity.target_id": 1},
    )
    async for source in cursor:
        stats.source_records += 1
        selected = route_destinations(adapter.target_resolver(source), destinations)
        if not selected:
            stats.unmapped_records += 1
            continue
        destination_ids = _unique_strings(
            [destination.project_id for destination in selected]
        )
        current_ids = set(
            _unique_strings(
                [source.get("project_id"), *_values(source.get("project_ids"))]
            )
        )
        existing = sum(value in current_ids for value in destination_ids)
        new_relations = len(destination_ids) - existing
        stats.planned_relations += len(destination_ids)
        stats.existing_relations += existing
        stats.inserted += new_relations
        if dry_run:
            stats.unchanged += existing
            continue
        pending.append(
            UpdateOne(
                {"_id": source["_id"]},
                {
                    "$addToSet": {
                        "project_ids": {"$each": destination_ids},
                        "merged_from_project_ids": source_project_id,
                    }
                },
            )
        )
        if len(pending) >= _BATCH_SIZE:
            result = await db[adapter.collection].bulk_write(
                pending,
                ordered=False,
            )
            stats.updated += int(result.modified_count or 0)
            stats.unchanged += len(pending) - int(result.modified_count or 0)
            pending = []
    if pending:
        result = await db[adapter.collection].bulk_write(pending, ordered=False)
        stats.updated += int(result.modified_count or 0)
        stats.unchanged += len(pending) - int(result.modified_count or 0)
    return stats


async def merge_project_data(
    db: AsyncIOMotorDatabase,
    *,
    source_project_id: str,
    destinations: list[MergeDestination],
    dry_run: bool = True,
) -> dict[str, Any]:
    """Merge all registered project facts and evidence associations."""
    normalized_destinations = [
        destination
        for destination in destinations
        if destination.project_id
        and destination.project_id != source_project_id
        and destination.target_ids
    ]
    if not normalized_destinations:
        raise ValueError("没有可用的目标项目数据映射")
    destination_target_ids = set().union(
        *(destination.target_ids for destination in normalized_destinations)
    )
    source_target_ids = set(
        await db[PROJECT_TARGETS_COLLECTION].distinct(
            "target_id",
            {"project_id": source_project_id, "active": {"$ne": False}},
        )
    )
    unmapped_target_ids = sorted(source_target_ids - destination_target_ids)
    stats: list[MergeStats] = [
        await _merge_project_target_metadata(
            db,
            source_project_id=source_project_id,
            destinations=normalized_destinations,
            dry_run=dry_run,
        )
    ]
    finding_routes: dict[str, list[tuple[str, str]]] = {}
    for adapter in _CLONE_ADAPTERS:
        adapter_stats, routes = await _merge_clone_adapter(
            db,
            adapter=adapter,
            source_project_id=source_project_id,
            destinations=normalized_destinations,
            dry_run=dry_run,
            track_routes=adapter.name == "findings",
        )
        stats.append(adapter_stats)
        if routes:
            finding_routes = routes
    stats.extend(
        await _merge_finding_dependents(
            db,
            source_project_id=source_project_id,
            finding_routes=finding_routes,
            dry_run=dry_run,
        )
    )
    for adapter in _SHARED_ASSOCIATION_ADAPTERS:
        stats.append(
            await _associate_shared_records(
                db,
                adapter=adapter,
                source_project_id=source_project_id,
                destinations=normalized_destinations,
                dry_run=dry_run,
            )
        )
    reports = [asdict(item) for item in stats]
    invalid_total = sum(item.invalid_records for item in stats)
    unmapped_record_total = sum(item.unmapped_records for item in stats)
    return {
        "dry_run": dry_run,
        "source_project_id": source_project_id,
        "destination_project_ids": [
            destination.project_id for destination in normalized_destinations
        ],
        "source_target_count": len(source_target_ids),
        "mapped_target_count": len(source_target_ids & destination_target_ids),
        "unmapped_target_ids": unmapped_target_ids,
        "unmapped_record_total": unmapped_record_total,
        "invalid_record_total": invalid_total,
        "complete": not unmapped_target_ids
        and unmapped_record_total == 0
        and invalid_total == 0,
        "collections": reports,
        "planned_relations_total": sum(item.planned_relations for item in stats),
        "existing_relations_total": sum(item.existing_relations for item in stats),
        "inserted_total": sum(item.inserted for item in stats),
        "updated_total": sum(item.updated for item in stats),
        "generated_at": _now(),
    }


def _destination_projects_by_target(
    destinations: list[MergeDestination],
) -> dict[str, list[str]]:
    projects: dict[str, list[str]] = defaultdict(list)
    for destination in destinations:
        for target_id in destination.target_ids:
            if destination.project_id not in projects[target_id]:
                projects[target_id].append(destination.project_id)
    return dict(projects)


def _merge_project_ids(
    document: dict[str, Any],
    destination_project_ids: list[str],
    source_project_id: str,
) -> list[str]:
    return _unique_strings(
        [
            *(
                value
                for value in _values(document.get("project_ids"))
                if str(value or "").strip() != source_project_id
            ),
            *destination_project_ids,
        ]
    )


async def _verify_partition_links(
    db: AsyncIOMotorDatabase,
    *,
    source_project_id: str,
    destinations: list[MergeDestination],
) -> dict[str, int]:
    target_projects = _destination_projects_by_target(destinations)
    expected_source_links: set[str] = set()
    source_links = db[SOURCE_DOCUMENT_LINKS_COLLECTION].find(
        {"project_id": source_project_id},
        {"_id": 0, "target_id": 1, "document_id": 1},
    )
    async for document in source_links:
        target_id = str(document.get("target_id") or "")
        document_id = str(document.get("document_id") or "")
        for project_id in target_projects.get(target_id, []):
            expected_source_links.add(
                source_documents_dao.document_link_id(
                    project_id,
                    target_id,
                    document_id,
                )
            )
    expected_bidding_links: set[str] = set()
    bidding_links = db[BIDDING_RECORD_LINKS_COLLECTION].find(
        {"project_id": source_project_id},
        {"_id": 0, "target_id": 1, "record_id": 1},
    )
    async for document in bidding_links:
        target_id = str(document.get("target_id") or "")
        record_id = str(document.get("record_id") or "")
        for project_id in target_projects.get(target_id, []):
            expected_bidding_links.add(
                bidding_dao.bidding_record_link_id(
                    project_id,
                    target_id,
                    record_id,
                )
            )
    source_link_count = await db[SOURCE_DOCUMENT_LINKS_COLLECTION].count_documents(
        {"link_id": {"$in": list(expected_source_links)}}
    ) if expected_source_links else 0
    bidding_link_count = await db[BIDDING_RECORD_LINKS_COLLECTION].count_documents(
        {"link_id": {"$in": list(expected_bidding_links)}}
    ) if expected_bidding_links else 0
    if source_link_count != len(expected_source_links):
        raise ValueError("来源文档关联尚未全部迁移，拒绝删除源项目")
    if bidding_link_count != len(expected_bidding_links):
        raise ValueError("招投标关联尚未全部迁移，拒绝删除源项目")
    return {
        "source_document_links": source_link_count,
        "bidding_record_links": bidding_link_count,
    }


async def _build_task_project_map(
    db: AsyncIOMotorDatabase,
    *,
    source_project_id: str,
    destinations: list[MergeDestination],
) -> tuple[
    dict[str, list[str]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    target_projects = _destination_projects_by_target(destinations)
    relations = await db[PROJECT_TARGETS_COLLECTION].find(
        {"project_id": source_project_id},
        {"_id": 0, "target_id": 1, "target_name": 1, "search_terms": 1},
    ).to_list(None)
    target_by_name: dict[str, str] = {}
    for relation in relations:
        target_id = str(relation.get("target_id") or "")
        for value in [
            relation.get("target_name"),
            *_values(relation.get("search_terms")),
        ]:
            normalized = targets_dao.normalize_target_name(str(value or ""))
            if normalized and target_id:
                target_by_name.setdefault(normalized, target_id)

    documents = await db[TASKS_COLLECTION].find(
        {"project_id": source_project_id},
        {"_id": 1, "task_id": 1, "status": 1, "params": 1, "result.identity.target_id": 1, "project_ids": 1},
    ).to_list(None)
    task_projects: dict[str, list[str]] = {}
    discardable: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for document in documents:
        params = document.get("params") or {}
        result = document.get("result") or {}
        identity = result.get("identity") or {} if isinstance(result, dict) else {}
        target_id = str(
            params.get("target_id") or identity.get("target_id") or ""
        ).strip()
        if not target_id:
            target_id = target_by_name.get(
                targets_dao.normalize_target_name(
                    str(params.get("company_name") or "")
                ),
                "",
            )
        project_ids = target_projects.get(target_id, [])
        task_id = str(document.get("task_id") or "")
        if project_ids and task_id:
            task_projects[task_id] = project_ids
        elif str(document.get("status") or "") in {
            "paused",
            "cancelled",
            "error",
            "failed",
        }:
            discardable.append(document)
        else:
            unresolved.append(document)
    return task_projects, discardable, unresolved


def _projects_for_task_reference(
    task_id: Any,
    task_projects: dict[str, list[str]],
) -> list[str]:
    normalized = str(task_id or "").strip()
    if not normalized:
        return []
    return task_projects.get(normalized) or task_projects.get(
        normalized.split("_", 1)[0],
        [],
    )


def _rehome_update(
    document: dict[str, Any],
    *,
    project_ids: list[str],
    source_project_id: str,
    now: datetime,
) -> dict[str, Any]:
    merged = _merge_project_ids(document, project_ids, source_project_id)
    if not merged:
        raise ValueError("共享记录缺少目标项目归属")
    return {
        "$set": {
            "project_id": merged[0],
            "project_ids": merged,
            "origin_project_id": source_project_id,
            "project_rehomed_at": now,
        }
    }


async def _bulk_rehome(
    db: AsyncIOMotorDatabase,
    *,
    collection: str,
    documents: list[tuple[dict[str, Any], list[str]]],
    source_project_id: str,
    now: datetime,
) -> int:
    updated = 0
    operations: list[UpdateOne] = []
    for document, project_ids in documents:
        operations.append(
            UpdateOne(
                {"_id": document["_id"], "project_id": source_project_id},
                _rehome_update(
                    document,
                    project_ids=project_ids,
                    source_project_id=source_project_id,
                    now=now,
                ),
            )
        )
        if len(operations) >= _BATCH_SIZE:
            result = await db[collection].bulk_write(operations, ordered=False)
            updated += int(result.modified_count or 0)
            operations = []
    if operations:
        result = await db[collection].bulk_write(operations, ordered=False)
        updated += int(result.modified_count or 0)
    return updated


async def purge_merged_source_project(
    db: AsyncIOMotorDatabase,
    *,
    source_project_id: str,
    destinations: list[MergeDestination],
) -> dict[str, Any]:
    """Hard-delete a merged source project without deleting shared evidence.

    The operation first proves that a repeated merge would insert nothing. It
    then rehomes immutable evidence and audit rows, removes source-only copies,
    and deletes the project document last. A failed intermediate run is safe to
    resume because every write is deterministic and idempotent.
    """
    try:
        source_project_oid = ObjectId(source_project_id)
    except Exception as exc:
        raise ValueError("源项目 ID 无效") from exc
    project = await db[PROJECTS_COLLECTION].find_one(
        {"_id": source_project_oid}
    )
    if not project:
        raise ValueError("源项目不存在")
    verification = await merge_project_data(
        db,
        source_project_id=source_project_id,
        destinations=destinations,
        dry_run=True,
    )
    if not verification.get("complete") or int(
        verification.get("inserted_total") or 0
    ):
        raise ValueError("历史数据尚未全部合并，拒绝删除源项目")
    active_tasks = await db[TASKS_COLLECTION].count_documents(
        {
            "project_id": source_project_id,
            "status": {"$in": ["queued", "pending", "running", "pausing"]},
        }
    )
    if active_tasks:
        raise ValueError("源项目仍有运行中的任务，拒绝删除")
    link_verification = await _verify_partition_links(
        db,
        source_project_id=source_project_id,
        destinations=destinations,
    )
    task_projects, discardable_tasks, unresolved_tasks = (
        await _build_task_project_map(
            db,
            source_project_id=source_project_id,
            destinations=destinations,
        )
    )
    if unresolved_tasks:
        raise ValueError("存在无法归属的新鲜任务记录，拒绝删除源项目")
    discardable_task_ids = {
        str(document.get("task_id") or "")
        for document in discardable_tasks
        if str(document.get("task_id") or "")
    }
    now = _now()
    target_projects = _destination_projects_by_target(destinations)
    rehomed: dict[str, int] = {}

    shared_plans: dict[str, list[tuple[dict[str, Any], list[str]]]] = {}
    for adapter in _SHARED_ASSOCIATION_ADAPTERS:
        documents = await db[adapter.collection].find(
            {"project_id": source_project_id},
            {"_id": 1, "project_id": 1, "project_ids": 1, "target_id": 1, "result.identity.target_id": 1},
        ).to_list(None)
        plan: list[tuple[dict[str, Any], list[str]]] = []
        for document in documents:
            selected = route_destinations(adapter.target_resolver(document), destinations)
            project_ids = [item.project_id for item in selected]
            if not project_ids:
                raise ValueError(f"{adapter.name} 存在无法归属的共享记录")
            plan.append((document, project_ids))
        shared_plans[adapter.collection] = plan

    task_documents = await db[TASKS_COLLECTION].find(
        {"project_id": source_project_id, "task_id": {"$in": list(task_projects)}},
        {"_id": 1, "project_id": 1, "project_ids": 1, "task_id": 1},
    ).to_list(None)
    task_plan = [
        (document, task_projects[str(document.get("task_id") or "")])
        for document in task_documents
    ]

    token_documents = await db[TOKEN_USAGE_RECORDS_COLLECTION].find(
        {"project_id": source_project_id},
        {"_id": 1, "project_id": 1, "project_ids": 1, "task_id": 1},
    ).to_list(None)
    token_plan: list[tuple[dict[str, Any], list[str]]] = []
    discardable_token_ids: list[Any] = []
    for document in token_documents:
        project_ids = _projects_for_task_reference(
            document.get("task_id"),
            task_projects,
        )
        if project_ids:
            token_plan.append((document, project_ids))
            continue
        base_task_id = str(document.get("task_id") or "").split("_", 1)[0]
        if base_task_id in discardable_task_ids:
            discardable_token_ids.append(document["_id"])
            continue
        raise ValueError("存在无法归属的 Token 观测记录，拒绝删除源项目")

    screenshot_documents = await db[MOBILE_SCREENSHOTS_COLLECTION].find(
        {"project_id": source_project_id},
        {"_id": 1, "project_id": 1, "project_ids": 1, "task_id": 1},
    ).to_list(None)
    screenshot_plan: list[tuple[dict[str, Any], list[str]]] = []
    for document in screenshot_documents:
        project_ids = _projects_for_task_reference(
            document.get("task_id"),
            task_projects,
        )
        if not project_ids:
            raise ValueError("存在无法归属的手机截图，拒绝删除源项目")
        screenshot_plan.append((document, project_ids))

    storage_documents = await db[STORAGE_OBJECTS_COLLECTION].find(
        {"project_id": source_project_id},
        {
            "_id": 1,
            "project_id": 1,
            "project_ids": 1,
            "subject_id": 1,
            "source_id": 1,
            "meta.target_id": 1,
            "meta.task_id": 1,
        },
    ).to_list(None)
    storage_plan: list[tuple[dict[str, Any], list[str]]] = []
    for document in storage_documents:
        meta = document.get("meta") or {}
        target_id = str(meta.get("target_id") or "")
        subject_id = str(document.get("subject_id") or "")
        if not target_id and subject_id.startswith("tgt_"):
            target_id = subject_id
        project_ids = (
            target_projects.get(target_id, [])
            or _projects_for_task_reference(meta.get("task_id"), task_projects)
            or _projects_for_task_reference(document.get("source_id"), task_projects)
        )
        if not project_ids:
            raise ValueError("存在无法归属的 OSS 对象，拒绝删除源项目")
        storage_plan.append((document, project_ids))

    for collection, plan in shared_plans.items():
        rehomed[collection] = await _bulk_rehome(
            db,
            collection=collection,
            documents=plan,
            source_project_id=source_project_id,
            now=now,
        )
    rehomed[TASKS_COLLECTION] = await _bulk_rehome(
        db,
        collection=TASKS_COLLECTION,
        documents=task_plan,
        source_project_id=source_project_id,
        now=now,
    )
    rehomed[TOKEN_USAGE_RECORDS_COLLECTION] = await _bulk_rehome(
        db,
        collection=TOKEN_USAGE_RECORDS_COLLECTION,
        documents=token_plan,
        source_project_id=source_project_id,
        now=now,
    )
    rehomed[MOBILE_SCREENSHOTS_COLLECTION] = await _bulk_rehome(
        db,
        collection=MOBILE_SCREENSHOTS_COLLECTION,
        documents=screenshot_plan,
        source_project_id=source_project_id,
        now=now,
    )
    rehomed[STORAGE_OBJECTS_COLLECTION] = await _bulk_rehome(
        db,
        collection=STORAGE_OBJECTS_COLLECTION,
        documents=storage_plan,
        source_project_id=source_project_id,
        now=now,
    )

    discarded_tokens = 0
    if discardable_token_ids:
        result = await db[TOKEN_USAGE_RECORDS_COLLECTION].delete_many(
            {"_id": {"$in": discardable_token_ids}}
        )
        discarded_tokens = int(result.deleted_count or 0)
    discarded_tasks = 0
    if discardable_task_ids:
        result = await db[TASKS_COLLECTION].delete_many(
            {"project_id": source_project_id, "task_id": {"$in": list(discardable_task_ids)}}
        )
        discarded_tasks = int(result.deleted_count or 0)

    source_finding_ids = await db[FINDINGS_COLLECTION].distinct(
        "finding_id",
        {"project_id": source_project_id},
    )
    deleted: dict[str, int] = {}
    if source_finding_ids:
        result = await db[PROFILES_COLLECTION].delete_many(
            {"finding_id": {"$in": source_finding_ids}}
        )
        deleted[PROFILES_COLLECTION] = int(result.deleted_count or 0)
    source_copy_collections = (
        FINDINGS_COLLECTION,
        COPYWRITINGS_COLLECTION,
        FOFA_ASSETS_COLLECTION,
        COMPANY_META_COLLECTION,
        SCHOLAR_ARTICLES_COLLECTION,
        SCHOLAR_CONTACTS_COLLECTION,
    )
    for collection in source_copy_collections:
        result = await db[collection].delete_many({"project_id": source_project_id})
        deleted[collection] = int(result.deleted_count or 0)
    result = await db[SOURCE_DOCUMENT_LINKS_COLLECTION].delete_many(
        {"project_id": source_project_id}
    )
    deleted[SOURCE_DOCUMENT_LINKS_COLLECTION] = int(result.deleted_count or 0)
    source_bidding_link_count = await db[
        BIDDING_RECORD_LINKS_COLLECTION
    ].count_documents({"project_id": source_project_id})
    detached_bidding_records = await bidding_dao.detach_project(
        db,
        source_project_id,
    )
    deleted[BIDDING_RECORD_LINKS_COLLECTION] = source_bidding_link_count
    result = await db[MOBILE_COLLECT_TASKS_COLLECTION].delete_many(
        {"project_id": source_project_id, "status": {"$ne": "running"}}
    )
    deleted[MOBILE_COLLECT_TASKS_COLLECTION] = int(result.deleted_count or 0)

    remaining = []
    for collection in (
        *source_copy_collections,
        SOURCE_DOCUMENT_LINKS_COLLECTION,
        BIDDING_RECORD_LINKS_COLLECTION,
        MOBILE_COLLECT_RECORDS_COLLECTION,
        TARGET_RESEARCH_COLLECTION,
        URL_SCAN_RESULTS_COLLECTION,
        URL_SCAN_TASKS_COLLECTION,
        COMPANY_SCAN_COLLECTION,
        TASKS_COLLECTION,
        TOKEN_USAGE_RECORDS_COLLECTION,
        MOBILE_SCREENSHOTS_COLLECTION,
        STORAGE_OBJECTS_COLLECTION,
        MOBILE_COLLECT_TASKS_COLLECTION,
    ):
        count = await db[collection].count_documents(
            {"project_id": source_project_id}
        )
        if count:
            remaining.append({"collection": collection, "count": count})
    if remaining:
        raise ValueError(f"源项目仍有未迁移记录: {remaining}")
    result = await db[PROJECT_TARGETS_COLLECTION].delete_many(
        {"project_id": source_project_id}
    )
    deleted[PROJECT_TARGETS_COLLECTION] = int(result.deleted_count or 0)
    project_result = await db[PROJECTS_COLLECTION].delete_one({"_id": project["_id"]})
    if not project_result.deleted_count:
        raise ValueError("源项目删除失败")
    return {
        "ok": True,
        "source_project_id": source_project_id,
        "source_project_deleted": True,
        "verification": {
            "merged_relations": verification.get("planned_relations_total", 0),
            "would_insert": verification.get("inserted_total", 0),
            **link_verification,
        },
        "rehomed": rehomed,
        "deleted": deleted,
        "discarded_incomplete_tasks": discarded_tasks,
        "discarded_incomplete_token_records": discarded_tokens,
        "detached_bidding_records": detached_bidding_records,
        "completed_at": now,
    }
