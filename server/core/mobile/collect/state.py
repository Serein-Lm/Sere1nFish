"""State construction and terminal projection for mobile collection."""
from __future__ import annotations

import asyncio
from typing import Any

from api.models.mobile_collect import ExtractField
from core.mobile.collect.contracts import (
    MobileCollectExecution,
    MobileCollectPlan,
    MobileSeedPlan,
)


def build_counters() -> dict[str, int]:
    return {
        "total": 0,
        "new": 0,
        "changed": 0,
        "contacts": 0,
        "documents": 0,
        "media": 0,
        "media_failed": 0,
        "high_score_records": 0,
        "high_score_documents": 0,
        "max_score": 0,
        "duplicates_skipped": 0,
        "stale_skipped": 0,
    }


def build_stream_state(
    plan: MobileCollectPlan,
    seeds: MobileSeedPlan,
    *,
    stop_event: asyncio.Event,
) -> dict[str, Any]:
    """Build the compatibility state consumed by registered stream stages."""
    task_def = plan.task_def
    state = {
        "db": plan.db,
        "task_def_id": plan.task_def_id,
        "task_name": task_def.get("name", plan.task_def_id),
        "project_id": plan.project_id,
        "target": seeds.target,
        "device_id": plan.device_id,
        "app_name": task_def.get("app_name", ""),
        "search_hint": task_def.get("search_hint", ""),
        "swipe_times": task_def.get("swipe_times", 3),
        "swipe_interval": task_def.get("swipe_interval", 1.2),
        "extract_fields": [
            item if isinstance(item, ExtractField) else ExtractField(**item)
            for item in list(task_def.get("extract_fields") or [])
        ],
        "dedup_key_fields": task_def.get("dedup_key_fields") or [],
        "notify_on": task_def.get("notify_on", "new"),
        **_collection_policy_state(task_def),
        **_detail_policy_state(task_def),
        **_runtime_state(plan, seeds, stop_event),
    }
    return state


def _collection_policy_state(task_def: dict[str, Any]) -> dict[str, Any]:
    return {
        "deep_collect": bool(task_def.get("deep_collect", False)),
        "source_link_strategy": str(
            task_def.get("source_link_strategy") or "none"
        ),
        "search_navigation_strategy": str(
            task_def.get("search_navigation_strategy") or ""
        ),
        "candidate_policy": str(task_def.get("candidate_policy") or ""),
        "detail_capture_strategy": str(
            task_def.get("detail_capture_strategy") or "default"
        ),
        "score_policy": str(
            task_def.get("score_policy") or "contact_weighted"
        ),
        "extract_contact_findings": bool(
            task_def.get("extract_contact_findings", True)
        ),
        "resolve_target_context": bool(
            task_def.get("resolve_target_context", True)
        ),
        "require_persist_success": bool(
            task_def.get("require_persist_success", False)
        ),
        "direct_launch_app": bool(task_def.get("direct_launch_app", False)),
        "direct_app_ready": False,
        "app_instance": str(task_def.get("app_instance") or "primary"),
        "skip_previously_collected": bool(
            task_def.get("skip_previously_collected", True)
        ),
        "prefer_recent_items": bool(task_def.get("prefer_recent_items", False)),
        "max_item_age_days": int(task_def.get("max_item_age_days", 0) or 0),
        "no_new_stop_threshold": int(
            task_def.get("no_new_stop_threshold", 2) or 2
        ),
        "collection_subject": str(task_def.get("collection_subject") or ""),
        "collection_goal": str(task_def.get("collection_goal") or ""),
        "platform": str(task_def.get("platform") or ""),
        "media_max_items": int(task_def.get("media_max_items") or 0),
        "media_navigation_hint": str(
            task_def.get("media_navigation_hint") or ""
        ),
        "record_source_type": str(
            task_def.get("record_source_type") or "mobile"
        ),
        "progress_source": str(task_def.get("progress_source") or "wechat"),
        "progress_label": str(task_def.get("progress_label") or "公众号"),
        "social_collection_job_id": str(
            task_def.get("social_collection_job_id") or ""
        ),
    }


def _detail_policy_state(task_def: dict[str, Any]) -> dict[str, Any]:
    return {
        "detail_max_items": int(task_def.get("detail_max_items", 5) or 0),
        "detail_max_total_items": int(
            task_def.get("detail_max_total_items", 0) or 0
        ),
        "detail_review_max_items": int(
            task_def.get("detail_review_max_items", 0) or 0
        ),
        "detail_review_max_total_items": int(
            task_def.get("detail_review_max_total_items", 0) or 0
        ),
        "detail_max_swipes": int(task_def.get("detail_max_swipes", 12) or 12),
        "min_score_to_detail": int(
            task_def.get("min_score_to_detail", 60) or 0
        ),
        "notification_min_score": max(
            60,
            int(task_def.get("min_score_to_detail", 60) or 0),
        ),
        "min_subject_match": int(task_def.get("min_subject_match", 70) or 0),
        "min_score_to_persist": int(
            task_def.get("min_score_to_persist", 0) or 0
        ),
    }


def _runtime_state(
    plan: MobileCollectPlan,
    seeds: MobileSeedPlan,
    stop_event: asyncio.Event,
) -> dict[str, Any]:
    preview: list[dict[str, Any]] = []
    candidate_reviews: list[dict[str, Any]] = []
    detail_entry_reviews: list[dict[str, Any]] = []
    task_def = plan.task_def
    return {
        "run_task_id": plan.run_task_id,
        "owner": plan.owner,
        "stop_event": stop_event,
        "counters": build_counters(),
        "dry_run": plan.dry_run,
        "preview": preview,
        "preview_limit": plan.preview_limit,
        "candidate_reviews": candidate_reviews,
        "candidate_review_limit": max(
            20, min(int(plan.preview_limit or 50) * 4, 500)
        ),
        "detail_entry_reviews": detail_entry_reviews,
        "detail_entry_review_limit": max(
            20, min(int(plan.preview_limit or 50) * 2, 200)
        ),
        "keywords_used": seeds.keywords,
        "keyword_resolution": seeds.keyword_resolution,
        "definition_fingerprint": seeds.definition_fingerprint,
        "parent_task_id": str(task_def.get("parent_task_id") or ""),
        "keyword_total": len(seeds.seed_specs),
        "keywords_completed": seeds.completed_count,
        "keywords_processed": seeds.completed_count,
        "details_attempted": 0,
        "details_accepted": 0,
        "detailed_record_keys": set(),
        "candidate_history_cache": {},
    }


def project_terminal_result(execution: MobileCollectExecution) -> dict[str, Any]:
    state = execution.state
    return {
        "stopped": bool(state["stop_event"].is_set()),
        "timed_out": execution.timed_out,
        "preview": list(state.get("preview") or []),
        "candidate_reviews": list(state.get("candidate_reviews") or []),
        "detail_entry_reviews": list(state.get("detail_entry_reviews") or []),
        "keywords_used": execution.seeds.keywords,
        "keywords_completed": int(state.get("keywords_completed") or 0),
        "keyword_total": int(state.get("keyword_total") or 0),
        "keyword_resolution": execution.seeds.keyword_resolution,
        **dict(state.get("counters") or {}),
    }
