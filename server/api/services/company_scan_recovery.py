"""Rebuild company scan summaries from durable source collections."""
from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import (
    BIDDING_RECORDS_COLLECTION,
    COMPANY_META_COLLECTION,
    COPYWRITINGS_COLLECTION,
    FINDINGS_COLLECTION,
    FOFA_ASSETS_COLLECTION,
    MOBILE_COLLECT_RECORDS_COLLECTION,
    SCHOLAR_ARTICLES_COLLECTION,
    SCHOLAR_CONTACTS_COLLECTION,
    TASKS_COLLECTION,
)


async def load_recovery_state(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
) -> dict[str, Any]:
    task = await db[TASKS_COLLECTION].find_one(
        {"task_id": task_id},
        {"_id": 0, "resume": 1, "checkpoint.modules": 1},
    )
    task = task or {}
    return {
        "resume": dict(task.get("resume") or {}),
        "modules": dict((task.get("checkpoint") or {}).get("modules") or {}),
    }


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
) -> dict[str, Any]:
    from api.dao import url_scan as url_scan_dao

    asset_query: dict[str, Any] = {"project_id": project_id, "task_ids": task_id}
    if target_id:
        asset_query["$or"] = [{"target_ids": target_id}, {"target_id": target_id}]
    assets = db[FOFA_ASSETS_COLLECTION]
    discovered = await assets.count_documents(asset_query)
    alive = await assets.count_documents({**asset_query, "is_alive": True})
    url_task_id = f"{task_id}_url"
    url_summary = await url_scan_dao.summarize_task(db, task_id=url_task_id)
    findings = await db[FINDINGS_COLLECTION].count_documents(
        {"task_id": url_task_id, "source": "web_tagging"}
    )
    copywritings = await db[COPYWRITINGS_COLLECTION].count_documents(
        {"task_id": url_task_id}
    )
    return {
        "kind": "asset_url",
        "assets": {
            "enabled": True,
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
            "enabled": True,
            "status": "completed",
            "findings_count": findings,
            "copywritings_count": copywritings,
            "scanned_urls": url_summary["succeeded"],
            "failed_urls": url_summary["failed"],
            "restored": True,
        },
    }


async def restore_bidding(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    company_name: str,
) -> dict[str, Any]:
    archive_task_id = f"{task_id}_bidding"
    records = await db[BIDDING_RECORDS_COLLECTION].find(
        {"task_ids": archive_task_id},
        {
            "_id": 0,
            "attachments": 1,
            "raw_content_object_id": 1,
            "provider_payload_object_id": 1,
            "detail_html_object_id": 1,
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
    return {
        "kind": "bidding",
        "enabled": True,
        "status": "completed",
        "query_name": company_name,
        "records_fetched": len(records),
        "total_reported": len(records),
        "attachments_archived": attachments,
        "raw_archived": sum(bool(item.get("raw_content_object_id")) for item in records),
        "provider_payloads_archived": sum(
            bool(item.get("provider_payload_object_id")) for item in records
        ),
        "detail_archived": sum(bool(item.get("detail_html_object_id")) for item in records),
        "archive_error_count": 0,
        "visual_analysis": {
            "status": "completed",
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
) -> dict[str, Any]:
    entities = await db[COMPANY_META_COLLECTION].find(
        {
            "project_id": project_id,
            "relation.parent_target_id": parent_target_id,
            "relation.relation_type": "wholly_owned_direct_investment",
        },
        {"_id": 0},
    ).to_list(None)
    normalized = [
        {
            "name": str(item.get("normalized_name") or item.get("input_name") or ""),
            "target_id": str(item.get("target_id") or ""),
            "root_domain": str(item.get("root_domain") or ""),
            "aliases": list(item.get("aliases") or []),
            "icp_domains": list(item.get("icp_domains") or []),
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
            "relation_depth": 1,
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
