"""公司扫描批次的覆盖规划与统一队列入口。"""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import target_relationships as target_relationships_dao
from api.dao import targets as targets_dao
from api.dao import tasks as tasks_dao
from api.services.target_scan_profile import (
    SCAN_CHANNELS,
    is_scan_coverage_current,
)


DEFAULT_COMPLETE_CHANNELS = ("website", "wechat", "scholar", "bidding")
_FINANCIAL_MARKERS = (
    "银行",
    "证券",
    "交易所",
    "金融",
    "人民币",
    "征信",
    "清算",
    "中证",
)
_enqueue_locks: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True, slots=True)
class CompanyScanJobSpec:
    target_id: str
    company_name: str
    params: dict[str, Any]


def _project_target_identity_names(relation: dict[str, Any]) -> list[str]:
    profile = dict(relation.get("scan_profile") or {})
    return _dedupe_text(
        [
            str(relation.get("target_name") or ""),
            str(relation.get("display_name") or ""),
            *[str(value) for value in relation.get("short_names") or []],
            *[str(value) for value in relation.get("scan_aliases") or []],
            *[str(value) for value in profile.get("search_aliases") or []],
        ]
    )


async def resolve_company_scan_job_specs(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    company_names: list[str],
    shared_params: dict[str, Any] | None = None,
) -> list[CompanyScanJobSpec]:
    """Resolve exact project Target identities before creating scan jobs.

    Ambiguous aliases deliberately remain unpinned so the normal identity pipeline can
    disambiguate them. A unique project identity is safe to reuse and avoids spending
    model capacity on names and domains the project has already verified.
    """
    relations = await targets_dao.list_project_targets(
        db,
        project_id,
        summary_only=True,
    )
    identity_index: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for relation in relations:
        target_id = str(relation.get("target_id") or "").strip()
        if not target_id:
            continue
        for identity_name in _project_target_identity_names(relation):
            normalized_name = targets_dao.normalize_target_name(identity_name)
            if normalized_name:
                identity_index[normalized_name][target_id] = relation

    specs: list[CompanyScanJobSpec] = []
    for company_name in company_names:
        candidates = identity_index.get(
            targets_dao.normalize_target_name(company_name),
            {},
        )
        relation = next(iter(candidates.values())) if len(candidates) == 1 else None
        target_id = str((relation or {}).get("target_id") or "").strip()
        canonical_name = str(
            (relation or {}).get("target_name") or company_name
        ).strip()
        params = dict(shared_params or {})
        if target_id:
            params.setdefault("refresh_target_identity", False)
            if "target_batch_tags" not in params:
                params["target_batch_tags"] = list(
                    (relation or {}).get("batch_tags") or []
                )
        specs.append(
            CompanyScanJobSpec(
                target_id=target_id,
                company_name=canonical_name,
                params=params,
            )
        )
    return specs


def _dedupe_text(values: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip().lower()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def normalize_required_channels(
    values: list[str] | tuple[str, ...] | None,
) -> list[str]:
    channels = _dedupe_text(list(values or DEFAULT_COMPLETE_CHANNELS))
    invalid = [channel for channel in channels if channel not in SCAN_CHANNELS]
    if invalid:
        raise ValueError(f"不支持的完整性渠道: {', '.join(invalid)}")
    if not channels:
        raise ValueError("至少需要一个完整性渠道")
    return channels


def classify_target_sector(relation: dict[str, Any]) -> str:
    """保守识别当前仅需排除的金融主体，不推断其他行业。"""
    text = " ".join(
        str(value or "")
        for value in (
            relation.get("target_name"),
            relation.get("display_name"),
            relation.get("industry"),
        )
    )
    if any(marker in text for marker in _FINANCIAL_MARKERS):
        return "financial"
    return "other"


def _task_identity(task: dict[str, Any]) -> tuple[str, str]:
    params = dict(task.get("params") or {})
    return (
        str(params.get("target_id") or "").strip(),
        targets_dao.normalize_target_name(
            str(params.get("company_name") or "")
        ),
    )


def _inflight_task_channels(task: dict[str, Any]) -> set[str]:
    """Return channels an existing task is expected to complete."""
    params = dict(task.get("params") or {})
    channels: set[str] = set()
    if params.get("enable_asset_discovery", True) or params.get(
        "enable_url_scan", True
    ):
        channels.add("website")
    if params.get("enable_wechat", False):
        channels.add("wechat")
    if params.get("enable_scholar", True):
        channels.add("scholar")
    if params.get("enable_bidding", False):
        channels.add("bidding")
    if params.get("enable_xhs", False):
        channels.add("xhs")
    if params.get("enable_control_structure", False):
        channels.add("control")
    return channels


async def plan_company_scan_coverage(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    batch_tag: str = "",
    required_channels: list[str] | tuple[str, ...] | None = None,
    excluded_sectors: list[str] | tuple[str, ...] | None = None,
    target_ids: list[str] | tuple[str, ...] | None = None,
    wechat_device_id: str = "",
    wechat_app_instance: str = "primary",
    subsidiary_scan_limit: int = 12,
    bidding_max_records: int = 10,
    bidding_lookback_days: int = 30,
    enable_copywriting: bool = True,
) -> dict[str, Any]:
    """按当前画像指纹规划根 Target 缺失渠道，结果可直接进入统一队列。"""
    channels = normalize_required_channels(required_channels)
    excluded = set(_dedupe_text(list(excluded_sectors or [])))
    requested_target_ids = {
        str(value or "").strip()
        for value in target_ids or []
        if str(value or "").strip()
    }
    safe_subsidiary_limit = max(1, min(int(subsidiary_scan_limit or 12), 100))
    safe_bidding_limit = max(1, min(int(bidding_max_records or 10), 20))
    safe_bidding_lookback = max(
        1,
        min(int(bidding_lookback_days or 30), 30),
    )

    relations = await targets_dao.list_project_targets(
        db,
        project_id,
        summary_only=True,
    )
    from api.services.targets import apply_project_target_hierarchy

    hierarchy_relationships = await target_relationships_dao.list_for_targets(
        project_id=project_id,
        db=db,
        target_ids=[
            str(relation.get("target_id") or "")
            for relation in relations
            if str(relation.get("target_id") or "")
        ],
    )
    relations = apply_project_target_hierarchy(
        relations,
        hierarchy_relationships,
    )
    inflight_tasks = await tasks_dao.list_inflight_company_scans(
        db,
        project_id=project_id,
    )
    inflight_target_ids: dict[str, list[dict[str, Any]]] = defaultdict(list)
    inflight_names: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in inflight_tasks:
        inflight_target_id, inflight_name = _task_identity(task)
        if inflight_target_id:
            inflight_target_ids[inflight_target_id].append(task)
        if inflight_name:
            inflight_names[inflight_name].append(task)

    items: list[dict[str, Any]] = []
    excluded_items: list[dict[str, Any]] = []
    completed_items: list[dict[str, Any]] = []
    inflight_items: list[dict[str, Any]] = []
    for relation in relations:
        target_id = str(relation.get("target_id") or "").strip()
        target_name = str(relation.get("target_name") or "").strip()
        relation_depth = max(
            int(relation.get("relation_depth") or 0),
            int(relation.get("hierarchy_depth") or 0),
        )
        if not target_id or not target_name or relation_depth != 0:
            continue
        if batch_tag and batch_tag not in list(relation.get("batch_tags") or []):
            continue
        if requested_target_ids and target_id not in requested_target_ids:
            continue

        sector = classify_target_sector(relation)
        base = {
            "target_id": target_id,
            "target_name": target_name,
            "display_name": str(
                relation.get("display_name") or target_name
            ),
            "sector": sector,
            "batch_tags": list(relation.get("batch_tags") or []),
            "profile_version": int(relation.get("scan_profile_version") or 0),
            "profile_fingerprint": str(
                relation.get("scan_profile_fingerprint")
                or (relation.get("scan_profile") or {}).get("fingerprint")
                or ""
            ),
        }
        if sector in excluded:
            excluded_items.append({**base, "reason": f"excluded_sector:{sector}"})
            continue

        missing_channels = [
            channel
            for channel in channels
            if not is_scan_coverage_current(relation, channel)
        ]
        if not missing_channels:
            completed_items.append({**base, "completed_channels": channels})
            continue

        normalized_name = targets_dao.normalize_target_name(target_name)
        matching_inflight: dict[str, dict[str, Any]] = {}
        for task in (
            *inflight_target_ids.get(target_id, []),
            *inflight_names.get(normalized_name, []),
        ):
            task_id = str(task.get("task_id") or "")
            if task_id:
                matching_inflight[task_id] = task
        inflight_channels = set().union(
            *(
                _inflight_task_channels(task)
                for task in matching_inflight.values()
            )
        ) if matching_inflight else set()
        missing_channels = [
            channel for channel in missing_channels if channel not in inflight_channels
        ]
        if not missing_channels and matching_inflight:
            inflight_task_ids = list(matching_inflight)
            inflight_items.append(
                {
                    **base,
                    "missing_channels": [],
                    "inflight_channels": sorted(inflight_channels),
                    "task_id": inflight_task_ids[0],
                    "task_ids": inflight_task_ids,
                }
            )
            continue

        website_missing = "website" in missing_channels
        bidding_missing = "bidding" in missing_channels
        params: dict[str, Any] = {
            "company_name": target_name,
            "target_id": target_id,
            "target_batch_tags": list(relation.get("batch_tags") or []),
            "coverage_channels": missing_channels,
            "enable_asset_discovery": website_missing,
            "enable_url_scan": website_missing,
            "enable_bidding_visual_analysis": bidding_missing,
            "enable_wechat": "wechat" in missing_channels,
            "wechat_device_id": str(wechat_device_id or "").strip(),
            "wechat_app_instance": str(wechat_app_instance or "primary"),
            "wechat_target_selection_mode": "all",
            "enable_xhs": "xhs" in missing_channels,
            "enable_subsidiary_xhs": False,
            "enable_bidding": bidding_missing,
            "bidding_page_size": safe_bidding_limit,
            "bidding_max_records": safe_bidding_limit,
            "bidding_lookback_days": safe_bidding_lookback,
            "enable_scholar": "scholar" in missing_channels,
            "scholar_limit": 20,
            "enable_control_structure": "control" in missing_channels,
            "control_max_depth": 2,
            "subsidiary_scan_limit": safe_subsidiary_limit,
            "skip_completed_subsidiaries": True,
            "enable_copywriting": bool(enable_copywriting),
            "incremental_scan": False,
        }
        items.append(
            {
                **base,
                "missing_channels": missing_channels,
                "inflight_channels": sorted(inflight_channels),
                "params": params,
            }
        )

    return {
        "project_id": project_id,
        "batch_tag": str(batch_tag or ""),
        "required_channels": channels,
        "excluded_sectors": sorted(excluded),
        "planned_count": len(items),
        "completed_count": len(completed_items),
        "excluded_count": len(excluded_items),
        "inflight_count": len(inflight_items),
        "items": items,
        "completed": completed_items,
        "excluded": excluded_items,
        "inflight": inflight_items,
    }


async def enqueue_company_scan_jobs(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    specs: list[CompanyScanJobSpec],
    requested_by: str,
    concurrency: int,
    aggregate_notification: bool = True,
) -> dict[str, Any]:
    """统一持久化并启动公司扫描任务，调用侧不感知队列实现。"""
    from api.services.project_task_batch import (
        MAX_COMPANY_SCAN_BATCH_SIZE,
        ProjectTaskJob,
        run_project_task_batch,
    )
    from api.services.project_task_runtime import execute_project_task
    from core.background import spawn_background

    if not specs:
        return {
            "batch_id": "",
            "task_type": "company_scan",
            "task_count": 0,
            "task_ids": [],
            "concurrency": 0,
            "status": "completed",
        }
    if len(specs) > MAX_COMPANY_SCAN_BATCH_SIZE:
        raise ValueError(f"一次最多下发 {MAX_COMPANY_SCAN_BATCH_SIZE} 家公司")

    lock = _enqueue_locks.setdefault(project_id, asyncio.Lock())
    async with lock:
        batch_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc)
        total = len(specs)
        safe_concurrency = max(1, min(int(concurrency or 1), 12, total))
        documents: list[dict[str, Any]] = []
        jobs: list[ProjectTaskJob] = []
        for index, spec in enumerate(specs, start=1):
            task_id = uuid.uuid4().hex[:12]
            task_params = {
                **dict(spec.params or {}),
                "company_name": spec.company_name,
                "target_id": spec.target_id,
            }
            documents.append(
                {
                    "task_id": task_id,
                    "project_id": project_id,
                    "task_type": "company_scan",
                    "params": task_params,
                    "requested_by": requested_by,
                    "batch_id": batch_id,
                    "batch_index": index,
                    "batch_total": total,
                    "batch_concurrency": safe_concurrency,
                    "status": "pending",
                    "progress": {},
                    "created_at": now,
                    "updated_at": now,
                }
            )
            jobs.append(
                ProjectTaskJob(
                    task_id=task_id,
                    project_id=project_id,
                    task_type="company_scan",
                    params={
                        **task_params,
                        "_requested_by": requested_by,
                        "_batch_id": batch_id,
                        "_batch_total": total,
                    },
                )
            )

        await tasks_dao.insert_tasks(db, documents)
        spawn_background(
            run_project_task_batch(
                batch_id=batch_id,
                project_id=project_id,
                jobs=jobs,
                executor=execute_project_task,
                concurrency=safe_concurrency,
                dispatch_concurrency=total,
                aggregate_notification=aggregate_notification and total > 1,
            ),
            name=f"task-batch:{batch_id}",
        )

    return {
        "batch_id": batch_id,
        "task_type": "company_scan",
        "task_count": total,
        "task_ids": [job.task_id for job in jobs],
        "concurrency": safe_concurrency,
        "status": "pending",
    }
