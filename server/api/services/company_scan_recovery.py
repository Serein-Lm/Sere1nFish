"""Rebuild company scan summaries from durable source collections."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import (
    BIDDING_RECORDS_COLLECTION,
    COMPANY_SCAN_COLLECTION,
    COMPANY_META_COLLECTION,
    COPYWRITINGS_COLLECTION,
    FINDINGS_COLLECTION,
    FOFA_ASSETS_COLLECTION,
    MOBILE_COLLECT_RECORDS_COLLECTION,
    SCHOLAR_ARTICLES_COLLECTION,
    SCHOLAR_CONTACTS_COLLECTION,
    TASKS_COLLECTION,
)


_CURRENT_BIDDING_LOOKBACK_DAYS = 30
_CURRENT_BIDDING_TYPES = {"1", "2", "4"}


_INCOMPLETE_SOURCE_MODULES = {
    "control_structure": "control_structure",
    "url_scan": "asset_url",
    "website_documents": "asset_url",
    "asset_intelligence": "asset_url",
    "xhs": "xhs",
    "bidding": "bidding",
    "wechat": "wechat",
    "scholar": "scholar",
}


def recovery_modules_for_incomplete_sources(
    result: dict[str, Any] | None,
) -> set[str]:
    """Map a prior terminal result back to modules that must be reopened."""
    sources = (result or {}).get("incomplete_sources") or []
    return {
        module
        for source in sources
        if (module := _INCOMPLETE_SOURCE_MODULES.get(str(source)))
    }


def _website_coverage_projection(outcome: dict[str, Any]) -> dict[str, Any]:
    url_summary = dict(outcome.get("url_scan") or {})
    document_summary = dict(outcome.get("website_documents") or {})
    return {
        "kind": "asset_url",
        "url_status": str(url_summary.get("status") or ""),
        "url_total": int(url_summary.get("total_urls") or 0),
        "url_scanned": int(url_summary.get("scanned_urls") or 0),
        "url_failed": int(url_summary.get("failed_urls") or 0),
        "url_remaining": int(url_summary.get("remaining_urls") or 0),
        "document_status": str(document_summary.get("status") or ""),
        "documents_discovered": int(
            document_summary.get("documents_scheduled") or 0
        ),
        "documents_archived": int(
            document_summary.get("documents_archived") or 0
        ),
        "document_pages_failed": int(
            document_summary.get("failed_pages") or 0
        ),
        "documents_partial": int(
            document_summary.get("documents_partial") or 0
        ),
        "attachments_archived": int(
            document_summary.get("attachments_archived") or 0
        ),
        "document_truncated": bool(document_summary.get("truncated")),
    }


async def reconcile_terminal_company_website_result(
    db: AsyncIOMotorDatabase,
    *,
    parent_task_id: str,
    website_summary: dict[str, Any],
) -> bool:
    """Synchronize repaired website evidence into terminal company read models."""
    task = await db[TASKS_COLLECTION].find_one(
        {
            "task_id": parent_task_id,
            "task_type": "company_scan",
            "status": "completed",
        },
        {"_id": 0, "project_id": 1, "result": 1},
    )
    result = dict((task or {}).get("result") or {})
    if not result:
        return False

    from api.services.company_scan_pipeline import incomplete_collection_sources
    from api.services.target_scan_profile import (
        coverage_status_from_result,
        record_target_scan_coverage,
    )

    result["website_documents"] = dict(website_summary)
    incomplete_sources = incomplete_collection_sources(result)
    result["incomplete_sources"] = incomplete_sources
    result["status"] = "partial" if incomplete_sources else "completed"
    now = datetime.now(timezone.utc)
    partial = result["status"] == "partial"
    document_failed = int(website_summary.get("failed_pages") or 0) + int(
        website_summary.get("documents_partial") or 0
    )
    document_progress = {
        "source": "website_documents",
        "status": str(website_summary.get("status") or "partial"),
        "processed": int(website_summary.get("processed_pages") or 0),
        "total": int(website_summary.get("total_pages") or 0),
        "succeeded": int(website_summary.get("documents_archived") or 0),
        "failed": document_failed,
        "message": (
            "官网文档归档完成 "
            f"{int(website_summary.get('documents_archived') or 0)} 篇，"
            f"附件 {int(website_summary.get('attachments_archived') or 0)} 个"
        ),
        "updated_at": now,
    }
    updated = await db[TASKS_COLLECTION].update_one(
        {
            "task_id": parent_task_id,
            "task_type": "company_scan",
            "status": "completed",
        },
        {
            "$set": {
                "result": result,
                "result_status": result["status"],
                "progress.stage": "partial" if partial else "completed",
                "progress.message": (
                    "任务已结束，但部分数据源未完整采集"
                    if partial
                    else "任务已完成"
                ),
                "progress.sources.website_documents": document_progress,
                "progress.last_activity_at": now,
                "updated_at": now,
            }
        },
    )
    if not updated.modified_count:
        return False

    await db[COMPANY_SCAN_COLLECTION].update_one(
        {"task_id": parent_task_id},
        {"$set": {"result": result, "updated_at": now}},
        upsert=True,
    )

    identity = dict(result.get("identity") or {})
    target_id = str(identity.get("target_id") or "")
    project_id = str((task or {}).get("project_id") or "")
    if target_id and project_id:
        outcome = {
            "status": result["status"],
            "assets": dict(result.get("assets") or {}),
            "url_scan": dict(result.get("url_scan") or {}),
            "website_documents": dict(website_summary),
        }
        await record_target_scan_coverage(
            db,
            project_id=project_id,
            target_id=target_id,
            channel="website",
            status=coverage_status_from_result("website", outcome),
            task_id=parent_task_id,
            summary=_website_coverage_projection(outcome),
            profile_fingerprint=str(
                identity.get("scan_profile_fingerprint") or ""
            ),
        )
    return True


def find_incompatible_core_modules(
    checkpoint_results: dict[str, dict[str, Any]],
) -> set[str]:
    """Return completed checkpoints whose persisted contract is now obsolete."""
    incompatible: set[str] = set()
    bidding = checkpoint_results.get("bidding")
    if isinstance(bidding, dict):
        try:
            lookback_days = int(bidding.get("lookback_days") or 0)
        except (TypeError, ValueError):
            lookback_days = 0
        bid_types = {
            str(item).strip()
            for item in bidding.get("bid_types") or []
            if str(item).strip()
        }
        # A wider successful window already covers the current contract. Old
        # summaries rebuilt from durable records may not carry query metadata;
        # keep those checkpoints instead of forcing an unbounded retry loop.
        window_incomplete = bool(lookback_days) and (
            lookback_days < _CURRENT_BIDDING_LOOKBACK_DAYS
        )
        types_incomplete = bool(bid_types) and (
            not _CURRENT_BIDDING_TYPES.issubset(bid_types)
        )
        if window_incomplete or types_incomplete:
            incompatible.add("bidding")
    asset_url = checkpoint_results.get("asset_url")
    if isinstance(asset_url, dict):
        url_scan = dict(asset_url.get("url_scan") or {})
        website_documents = dict(asset_url.get("website_documents") or {})
        if (
            url_scan.get("enabled") is not False
            and (
                not website_documents
                or str(website_documents.get("status") or "").lower()
                not in {"completed", "skipped", "disabled"}
            )
        ):
            incompatible.add("asset_url")
    return incompatible


async def load_recovery_state(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
) -> dict[str, Any]:
    task = await db[TASKS_COLLECTION].find_one(
        {"task_id": task_id},
        {"_id": 0, "resume": 1, "checkpoint.modules": 1, "result": 1},
    )
    task = task or {}
    return {
        "resume": dict(task.get("resume") or {}),
        "modules": dict((task.get("checkpoint") or {}).get("modules") or {}),
        "result": dict(task.get("result") or {}),
    }


async def find_retryable_core_modules(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
) -> set[str]:
    """Map unfinished URL child tasks back to company module checkpoints."""
    from api.dao import url_scan as url_scan_dao
    from api.dao import website_crawl as website_crawl_dao

    child_tasks = {
        "asset_url": f"{task_id}_url",
        "bidding": f"{task_id}_bidding_visual",
    }
    retryable = await url_scan_dao.retryable_task_ids(
        db,
        task_ids=set(child_tasks.values()),
    )
    modules = {
        module
        for module, child_task_id in child_tasks.items()
        if child_task_id in retryable
    }
    url_task = await url_scan_dao.get_task(db, task_id=f"{task_id}_url")
    if url_task and (
        int(url_task.get("remaining_urls") or 0) > 0
        or str(url_task.get("status") or "").lower()
        in {"pending", "running", "probing", "scanning", "waiting_model", "error"}
    ):
        modules.add("asset_url")
    if await website_crawl_dao.task_requires_retry(
        db,
        crawl_task_id=f"{task_id}_webdocs",
    ):
        modules.add("asset_url")
    return modules


async def restore_identity(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    company_name: str,
) -> dict[str, Any] | None:
    from api.dao import company_meta as company_meta_dao

    meta = await company_meta_dao.get_company_meta(db, project_id, company_name)
    if not meta:
        return None
    root_domain = str(meta.get("root_domain") or "")
    root_domains = list(
        dict.fromkeys(
            value
            for value in [root_domain, *list(meta.get("icp_domains") or [])]
            if value
        )
    )[:6]
    return {
        "input_name": company_name,
        "normalized_name": str(meta.get("normalized_name") or company_name),
        "root_domain": root_domain,
        "root_domains": root_domains,
        "aliases": list(meta.get("aliases") or [company_name]),
        "target_id": str(meta.get("target_id") or ""),
        "normalization_error": str(
            (meta.get("provenance") or {}).get("browser_error") or ""
        )
        or None,
    }


async def restore_asset_url(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    project_id: str,
    target_id: str,
    incremental_scan: bool,
    enable_asset_discovery: bool = True,
    enable_url_scan: bool = True,
) -> dict[str, Any]:
    from api.dao import url_scan as url_scan_dao
    from api.dao import website_crawl as website_crawl_dao

    asset_query: dict[str, Any] = {"project_id": project_id, "task_ids": task_id}
    if target_id:
        asset_query["$or"] = [{"target_ids": target_id}, {"target_id": target_id}]
    assets = db[FOFA_ASSETS_COLLECTION]
    discovered = await assets.count_documents(asset_query)
    alive = await assets.count_documents({**asset_query, "is_alive": True})
    url_task_id = f"{task_id}_url"
    url_summary = await url_scan_dao.summarize_task(db, task_id=url_task_id)
    durable_url_task = await url_scan_dao.get_task(db, task_id=url_task_id) or {}
    findings = await db[FINDINGS_COLLECTION].count_documents(
        {"task_id": url_task_id, "source": "web_tagging"}
    )
    copywritings = await db[COPYWRITINGS_COLLECTION].count_documents(
        {"task_id": url_task_id}
    )
    website_task_id = f"{task_id}_webdocs"
    website_task = (
        await website_crawl_dao.get_task(db, website_task_id)
        if enable_url_scan
        else None
    )
    if website_task:
        website_documents = await website_crawl_dao.summarize_task(
            db,
            crawl_task_id=website_task_id,
        )
        website_documents.update(
            {
                "enabled": True,
                "status": str(website_task.get("status") or "pending"),
                "documents_scheduled": int(
                    (website_documents.get("by_kind") or {}).get("document") or 0
                ),
                "documents_archived": int(
                    website_documents.get("archived_documents") or 0
                ),
            }
        )
    elif enable_url_scan:
        website_documents = {
            "enabled": True,
            "status": "pending",
            "legacy_missing": True,
        }
    else:
        website_documents = {"enabled": False, "status": "disabled"}
    url_status = str(durable_url_task.get("status") or "").lower()
    if not url_status:
        url_status = "completed" if url_summary["processed"] else "pending"
    result = {
        "kind": "asset_url",
        "assets": {
            "enabled": enable_asset_discovery,
            "discovered": discovered,
            "alive": alive,
            "inserted": 0,
            "updated": 0,
            "unchanged": discovered,
            "scan_mode": "incremental" if incremental_scan else "full",
            "scan_candidates": url_summary["processed"],
            "providers": {},
            "restored": True,
        },
        "url_scan": {
            "enabled": enable_url_scan,
            "status": url_status if enable_url_scan else "disabled",
            "findings_count": findings,
            "copywritings_count": copywritings,
            "total_urls": int(durable_url_task.get("total_urls") or 0),
            "scanned_urls": url_summary["succeeded"],
            "failed_urls": url_summary["failed"],
            "remaining_urls": int(durable_url_task.get("remaining_urls") or 0),
            "error": str(durable_url_task.get("error") or ""),
            "restored": True,
        },
        "website_documents": website_documents,
    }
    from api.services.target_scan_profile import coverage_status_from_result

    result["status"] = (
        coverage_status_from_result("website", result)
        if enable_url_scan
        else "completed"
    )
    return result


async def restore_bidding(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    company_name: str,
) -> dict[str, Any]:
    from api.dao import url_scan as url_scan_dao

    archive_task_id = f"{task_id}_bidding"
    records = await db[BIDDING_RECORDS_COLLECTION].find(
        {"task_ids": archive_task_id},
        {
            "_id": 0,
            "attachments": 1,
            "raw_content_object_id": 1,
            "provider_payload_object_id": 1,
            "detail_html_object_id": 1,
            "attachment_urls": 1,
            "attachments_truncated": 1,
            "archive_errors": 1,
        },
    ).to_list(None)
    attachments = sum(
        sum(item.get("status") == "ready" for item in record.get("attachments") or [])
        for record in records
    )
    visual_task_id = f"{task_id}_bidding_visual"
    findings = await db[FINDINGS_COLLECTION].count_documents(
        {"task_id": visual_task_id, "source": "bidding"}
    )
    copywritings = await db[COPYWRITINGS_COLLECTION].count_documents(
        {"task_id": visual_task_id}
    )
    attachment_discovered = sum(
        len(record.get("attachment_urls") or []) for record in records
    )
    attachment_incomplete = 0
    for record in records:
        discovered_urls = list(dict.fromkeys(record.get("attachment_urls") or []))
        archived_entries = [
            item
            for item in record.get("attachments") or []
            if isinstance(item, dict)
        ]
        attachment_incomplete += sum(
            str(item.get("status") or "").lower() != "ready"
            for item in archived_entries
        )
        attachment_incomplete += max(
            0,
            len(discovered_urls) - len(archived_entries),
        )
        attachment_incomplete += int(record.get("attachments_truncated") or 0)
    archive_errors = [
        str(error)
        for record in records
        for error in record.get("archive_errors") or []
        if str(error or "").strip()
    ]
    visual_task = await url_scan_dao.get_task(db, task_id=visual_task_id)
    visual_status = str((visual_task or {}).get("status") or "completed").lower()
    visual_incomplete = (
        visual_status in {
            "partial", "error", "failed", "pending", "running", "probing",
            "scanning", "waiting_model",
        }
        or int((visual_task or {}).get("remaining_urls") or 0) > 0
    )
    status = (
        "partial"
        if archive_errors or attachment_incomplete or visual_incomplete
        else "completed"
    )
    return {
        "kind": "bidding",
        "enabled": True,
        "status": status,
        "query_name": company_name,
        "records_fetched": len(records),
        "total_reported": len(records),
        "attachments_archived": attachments,
        "attachments_discovered": attachment_discovered,
        "attachments_incomplete": attachment_incomplete,
        "raw_archived": sum(bool(item.get("raw_content_object_id")) for item in records),
        "provider_payloads_archived": sum(
            bool(item.get("provider_payload_object_id")) for item in records
        ),
        "detail_archived": sum(bool(item.get("detail_html_object_id")) for item in records),
        "archive_error_count": len(archive_errors),
        "archive_errors": archive_errors[:20],
        "visual_analysis": {
            "status": visual_status,
            "findings_count": findings,
            "copywritings_count": copywritings,
        },
        "restored": True,
    }


async def restore_scholar(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    unit: str,
) -> dict[str, Any]:
    article_query = {"task_ids": task_id}
    contact_query = {"task_ids": task_id}
    articles = db[SCHOLAR_ARTICLES_COLLECTION]
    contacts = db[SCHOLAR_CONTACTS_COLLECTION]
    articles_total = await articles.count_documents(article_query)
    verified = await articles.count_documents(
        {**article_query, "unit_verified": True}
    )
    contacts_total = await contacts.count_documents(contact_query)
    corresponding = await contacts.count_documents(
        {**contact_query, "is_corresponding": True}
    )
    sample = await articles.find_one(article_query, {"_id": 0, "direction": 1})
    return {
        "kind": "scholar",
        "status": "completed",
        "unit": unit,
        "direction": str((sample or {}).get("direction") or ""),
        "direction_source": "restored",
        "articles_total": articles_total,
        "verified_articles_total": verified,
        "unverified_articles_total": max(0, articles_total - verified),
        "contacts_total": contacts_total,
        "corresponding_count": corresponding,
        "restored": True,
    }


async def restore_control_structure(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    parent_target_id: str,
    max_depth: int = 2,
) -> dict[str, Any]:
    safe_depth = max(1, min(int(max_depth or 1), 2))
    entities = await db[COMPANY_META_COLLECTION].find(
        {
            "project_id": project_id,
            "relation.relation_type": "wholly_owned_direct_investment",
            "relation.relation_depth": {"$lte": safe_depth},
            "$or": [
                {"relation.root_target_id": parent_target_id},
                {
                    "relation.root_target_id": {"$exists": False},
                    "relation.parent_target_id": parent_target_id,
                },
            ],
        },
        {"_id": 0},
    ).sort(
        [("relation.relation_depth", 1), ("normalized_name", 1)]
    ).to_list(None)
    normalized = [
        {
            "name": str(item.get("normalized_name") or item.get("input_name") or ""),
            "target_id": str(item.get("target_id") or ""),
            "root_domain": str(item.get("root_domain") or ""),
            "aliases": list(item.get("aliases") or []),
            "icp_domains": list(item.get("icp_domains") or []),
            "root_target_id": str((item.get("relation") or {}).get("root_target_id") or parent_target_id),
            "root_target_name": str((item.get("relation") or {}).get("root_target_name") or ""),
            "parent_target_id": str((item.get("relation") or {}).get("parent_target_id") or ""),
            "parent_target_name": str((item.get("relation") or {}).get("parent_target_name") or ""),
            "relation_depth": int((item.get("relation") or {}).get("relation_depth") or 1),
            "ownership_percent": float((item.get("relation") or {}).get("ownership_percent") or 100),
            "lineage_target_ids": list((item.get("relation") or {}).get("lineage_target_ids") or []),
            "lineage_target_names": list((item.get("relation") or {}).get("lineage_target_names") or []),
            "relation": dict(item.get("relation") or {}),
        }
        for item in entities
    ]
    return {
        "kind": "control_structure",
        "result": {
            "enabled": True,
            "status": "completed",
            "relation_type": "wholly_owned_direct_investment",
            "max_depth": safe_depth,
            "relation_depth": max(
                (int(item.get("relation_depth") or 0) for item in normalized),
                default=0,
            ),
            "ownership_percent": 100.0,
            "entities": normalized,
            "errors": [],
            "restored": True,
        },
    }


async def restore_wechat(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
) -> dict[str, Any]:
    run_task_id = f"{task_id}_wechat"
    records = await db[MOBILE_COLLECT_RECORDS_COLLECTION].find(
        {"run_task_ids": run_task_id},
        {
            "_id": 0,
            "score": 1,
            "source_document_id": 1,
            "keyword": 1,
            "fields.contact": 1,
        },
    ).to_list(None)
    scores = [int(item.get("score") or 0) for item in records]
    return {
        "kind": "wechat",
        "status": "completed",
        "total": len(records),
        "new": 0,
        "changed": 0,
        "contacts": sum(bool((item.get("fields") or {}).get("contact")) for item in records),
        "documents": sum(bool(item.get("source_document_id")) for item in records),
        "high_score_records": sum(score >= 60 for score in scores),
        "high_score_documents": sum(
            score >= 60 and bool(item.get("source_document_id"))
            for item, score in zip(records, scores)
        ),
        "max_score": max(scores, default=0),
        "keywords_used": list(
            dict.fromkeys(str(item.get("keyword") or "") for item in records if item.get("keyword"))
        ),
        "stopped": False,
        "restored": True,
    }
