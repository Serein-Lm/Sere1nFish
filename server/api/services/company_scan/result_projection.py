"""Stable result construction and summary projection for company scans."""
from __future__ import annotations

from typing import Any

from api.services.company_control.contracts import (
    investment_relation_type,
    normalize_control_ownership_threshold,
)
from api.services.company_scan.contracts import CompanyScanPlan


def build_initial_result(
    plan: CompanyScanPlan,
    *,
    manual_xhs_targets: list[str],
    wechat_app_instance: str,
) -> dict[str, Any]:
    return {
        "task_id": plan.task_id,
        "company_name": plan.company_name,
        "status": "running",
        "identity": {},
        "router_result": None,
        "control_structure": _control_result(plan),
        "assets": _assets_result(plan),
        "url_scan": _url_result(plan),
        "website_documents": _website_result(plan),
        "xhs": _xhs_result(plan, manual_xhs_targets),
        "bidding": _bidding_result(plan),
        "wechat": _wechat_result(plan, wechat_app_instance),
        "scholar": _scholar_result(plan),
        "profile_copywritings": {"count": 0},
        "sub_errors": [],
        "error": None,
    }


def _control_result(plan: CompanyScanPlan) -> dict[str, Any]:
    minimum_ownership_percent = normalize_control_ownership_threshold(
        plan.control_min_ownership_percent
    )
    return {
        "enabled": plan.enable_control_structure,
        "status": "pending" if plan.enable_control_structure else "disabled",
        "relation_type": investment_relation_type(minimum_ownership_percent),
        "max_depth": max(1, min(int(plan.control_max_depth or 1), 2)),
        "relation_depth": 0,
        "ownership_percent": minimum_ownership_percent,
        "minimum_ownership_percent": minimum_ownership_percent,
        "scan_policy": {
            "max_entities": max(1, min(int(plan.subsidiary_scan_limit or 12), 100)),
            "skip_completed": bool(plan.skip_completed_subsidiaries),
            "selected_count": 0,
            "skipped_count": 0,
            "requested_channels": [],
        },
        "entities": [],
        "errors": [],
    }


def _assets_result(plan: CompanyScanPlan) -> dict[str, Any]:
    return {
        "enabled": plan.enable_asset_discovery,
        "discovered": 0,
        "alive": 0,
        "inserted": 0,
        "updated": 0,
        "scan_mode": "incremental" if plan.incremental_scan else "full",
        "scan_candidates": 0,
        "providers": {},
    }


def _url_result(plan: CompanyScanPlan) -> dict[str, Any]:
    return {
        "enabled": plan.enable_url_scan,
        "status": "pending" if plan.enable_url_scan else "disabled",
        "findings_count": 0,
        "copywritings_count": 0,
    }


def _website_result(plan: CompanyScanPlan) -> dict[str, Any]:
    return {
        "enabled": plan.enable_url_scan,
        "status": "pending" if plan.enable_url_scan else "disabled",
        "documents_archived": 0,
        "attachments_archived": 0,
        "failed_pages": 0,
    }


def _xhs_result(
    plan: CompanyScanPlan,
    manual_targets: list[str],
) -> dict[str, Any]:
    return {
        "enabled": plan.enable_xhs,
        "status": "pending" if plan.enable_xhs else "disabled",
        "subsidiaries_enabled": plan.subsidiary_xhs_enabled,
        "keywords_used": [],
        "notes_count": 0,
        "profiles_count": 0,
        "root_selected": False,
        "selection": {
            "mode": str(plan.xhs_target_selection_mode or "auto"),
            "status": "pending" if plan.enable_xhs else "disabled",
            "prompt_slug": None,
            "manual_targets": manual_targets,
            "matched_manual_targets": [],
            "unmatched_manual_targets": manual_targets,
            "decisions": [],
            "selected_count": 0,
            "skipped_count": 0,
            "error": None,
        },
    }


def _bidding_result(plan: CompanyScanPlan) -> dict[str, Any]:
    return {
        "enabled": plan.enable_bidding,
        "subsidiaries_enabled": plan.subsidiary_bidding_enabled,
        "status": "pending" if plan.enable_bidding else "disabled",
        "query_name": "",
        "records_fetched": 0,
        "total_reported": 0,
        "attachments_archived": 0,
        "findings_count": 0,
        "copywritings_count": 0,
    }


def _wechat_result(plan: CompanyScanPlan, app_instance: str) -> dict[str, Any]:
    return {
        "enabled": plan.enable_wechat,
        "status": "pending" if plan.enable_wechat else "disabled",
        "selected": False,
        "priority": None,
        "device_id": plan.wechat_device_id,
        "app_instance": app_instance,
        "task_def_id": "",
        "total": 0,
        "new": 0,
        "changed": 0,
        "contacts": 0,
        "documents": 0,
        "high_score_records": 0,
        "high_score_documents": 0,
        "max_score": 0,
        "keywords_used": [],
        "selection": {
            "mode": str(plan.wechat_target_selection_mode or "auto"),
            "status": "pending" if plan.enable_wechat else "disabled",
            "prompt_slug": None,
            "decisions": [],
            "selected_count": 0,
            "skipped_count": 0,
            "error": None,
        },
    }


def _scholar_result(plan: CompanyScanPlan) -> dict[str, Any]:
    direction = str(plan.scholar_direction or "").strip()
    return {
        "enabled": plan.enable_scholar,
        "status": "pending" if plan.enable_scholar else "disabled",
        "unit": "",
        "direction": direction,
        "direction_source": "manual" if direction else "pending",
        "direction_terms": [],
        "articles_total": 0,
        "verified_articles_total": 0,
        "unverified_articles_total": 0,
        "contacts_total": 0,
        "corresponding_count": 0,
        "descendant_entities_total": 0,
        "descendant_entities_completed": 0,
        "descendant_articles_total": 0,
        "descendant_verified_articles_total": 0,
        "descendant_contacts_total": 0,
        "related_entities": [],
    }


def completion_observation(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "url_findings": result["url_scan"].get("findings_count", 0),
        "assets": result["assets"].get("discovered", 0),
        "alive_assets": result["assets"].get("alive", 0),
        "xhs_notes": result["xhs"].get("notes_count", 0),
        "xhs_profiles": result["xhs"].get("profiles_count", 0),
        "wechat_records": result["wechat"].get("total", 0),
        "wechat_documents": result["wechat"].get("documents", 0),
        "bidding_records": result["bidding"].get("records_fetched", 0),
        "bidding_findings": result["bidding"].get("findings_count", 0),
        "scholar_articles": result["scholar"].get("articles_total", 0),
        "scholar_contacts": result["scholar"].get("contacts_total", 0),
        "profile_copywritings": result["profile_copywritings"].get("count", 0),
        "xhs_targets_selected": result["xhs"]["selection"].get("selected_count", 0),
        "xhs_targets_skipped": result["xhs"]["selection"].get("skipped_count", 0),
    }
