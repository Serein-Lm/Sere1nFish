"""综合扫描中的微信公众号采集适配层。"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import mobile_collect as collect_dao
from api.models.mobile_collect import CollectTaskDef
from api.services.mobile_collect_pipeline import run_mobile_collect_definition
from core.logger import get_logger
from core.mobile.collect.presets import get_preset_task


WECHAT_SOURCE_LINK_STRATEGY = "wechat_copy_link"
WECHAT_AUTO_TASK_NAME = "综合扫描公众号采集"
logger = get_logger("wechat_collection")
_DEFINITION_LOCKS: dict[tuple[int, str, str], asyncio.Lock] = {}
_WECHAT_APP_INSTANCES = frozenset({"primary", "clone"})


def _definition_lock(project_id: str, device_id: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = (id(loop), project_id, device_id)
    lock = _DEFINITION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _DEFINITION_LOCKS[key] = lock
    return lock


def normalize_wechat_app_instance(value: Any) -> str:
    normalized = str(value or "primary").strip().casefold()
    if normalized not in _WECHAT_APP_INSTANCES:
        raise ValueError("微信应用实例必须为 primary 或 clone")
    return normalized


def _company_wechat_defaults(*, app_instance: str = "primary") -> dict[str, Any]:
    """Build the complete WeChat article profile used by company scans."""
    normalized_instance = normalize_wechat_app_instance(app_instance)
    task = get_preset_task("wechat_official")
    task.update(
        {
            "name": WECHAT_AUTO_TASK_NAME,
            "app_instance": normalized_instance,
            "keywords": [],
            "use_target_keyword_library": True,
            "deep_collect": True,
            "source_link_strategy": WECHAT_SOURCE_LINK_STRATEGY,
            "notify_on": "none",
            "include_direct_children": True,
            "max_relation_depth": 2,
            "max_related_targets": 6,
            "skip_completed_related_targets": True,
            "max_resolved_keywords": 36,
            "swipe_times": 6,
            "detail_max_items": 4,
            "detail_max_total_items": 36,
            "detail_review_max_items": 6,
            "detail_review_max_total_items": 72,
            "detail_max_swipes": 6,
            "max_runtime_seconds": 14400,
        }
    )
    return task


def _is_company_wechat_task(task_def: dict[str, Any], *, device_id: str) -> bool:
    return _is_wechat_task(task_def, device_id=device_id) and (
        str(task_def.get("name") or "") == WECHAT_AUTO_TASK_NAME
        or str(task_def.get("source_link_strategy") or "")
        == WECHAT_SOURCE_LINK_STRATEGY
    )


def _wechat_definition_patch(
    task_def: dict[str, Any],
    *,
    app_instance: str = "primary",
) -> dict[str, Any]:
    """Repair link fields and keep system company scans within phone limits."""
    defaults = _company_wechat_defaults(app_instance=app_instance)
    patch: dict[str, Any] = {}
    if str(task_def.get("app_instance") or "primary") != defaults["app_instance"]:
        patch["app_instance"] = defaults["app_instance"]
    for field in ("extract_fields", "dedup_key_fields"):
        if not task_def.get(field):
            patch[field] = defaults[field]
    for field in ("search_hint", "source_link_strategy"):
        if not str(task_def.get(field) or "").strip():
            patch[field] = defaults[field]
    if not task_def.get("deep_collect"):
        patch["deep_collect"] = True
    is_auto_definition = str(task_def.get("name") or "") == WECHAT_AUTO_TASK_NAME
    if is_auto_definition and str(task_def.get("notify_on") or "") != defaults[
        "notify_on"
    ]:
        patch["notify_on"] = defaults["notify_on"]
    if "include_direct_children" not in task_def or (
        is_auto_definition
        and task_def.get("include_direct_children") != defaults["include_direct_children"]
    ):
        patch["include_direct_children"] = defaults["include_direct_children"]
    for field in (
        "max_relation_depth",
        "max_related_targets",
        "skip_completed_related_targets",
    ):
        if field not in task_def or (
            is_auto_definition and task_def.get(field) != defaults[field]
        ):
            patch[field] = defaults[field]
    if not task_def.get("max_resolved_keywords") or (
        is_auto_definition
        and int(task_def.get("max_resolved_keywords") or 0)
        != defaults["max_resolved_keywords"]
    ):
        patch["max_resolved_keywords"] = defaults["max_resolved_keywords"]
    for field in (
        "detail_max_items",
        "detail_max_total_items",
        "detail_review_max_items",
        "detail_review_max_total_items",
    ):
        if is_auto_definition and int(task_def.get(field) or 0) != defaults[field]:
            patch[field] = defaults[field]
    for field in ("swipe_times", "detail_max_swipes", "max_runtime_seconds"):
        if is_auto_definition and int(task_def.get(field) or 0) != int(defaults[field]):
            patch[field] = defaults[field]
    return patch


async def _repair_wechat_task_definition(
    db: AsyncIOMotorDatabase,
    task_def: dict[str, Any],
    *,
    project_id: str,
    device_id: str,
    app_instance: str = "primary",
) -> dict[str, Any]:
    """Persist missing link-extraction fields before every execution path."""
    patch = _wechat_definition_patch(task_def, app_instance=app_instance)
    if not patch:
        return task_def
    task_def_id = str(task_def.get("task_def_id") or "")
    repaired = await collect_dao.update_task_def(db, task_def_id, patch)
    if not repaired:
        raise ValueError(f"公众号采集任务定义不存在: {task_def_id}")
    logger.notice(
        "自动修复综合扫描公众号采集定义 | project=%s device=%s def=%s fields=%s",
        project_id,
        device_id,
        task_def_id,
        sorted(patch),
    )
    return repaired


def _is_wechat_task(task_def: dict[str, Any], *, device_id: str) -> bool:
    app_name = str(task_def.get("app_name") or "").strip().lower()
    return str(task_def.get("device_id") or "") == device_id and (
        "微信" in app_name or "wechat" in app_name
    )


def _select_wechat_task(
    candidates: list[dict[str, Any]],
    *,
    expected_target_id: str = "",
) -> dict[str, Any]:
    def rank(item: dict[str, Any]) -> tuple[int, int]:
        configured_target_id = str(item.get("target_id") or "")
        if expected_target_id and configured_target_id == expected_target_id:
            target_rank = 0
        elif not configured_target_id:
            target_rank = 1
        else:
            target_rank = 2
        strategy_rank = (
            0
            if str(item.get("source_link_strategy") or "")
            == WECHAT_SOURCE_LINK_STRATEGY
            else 1
        )
        return target_rank, strategy_rank

    return min(candidates, key=rank)


async def ensure_wechat_task_definition(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    device_id: str,
    app_instance: str = "primary",
) -> dict[str, Any]:
    """Ensure comprehensive scans can use a selected pool device directly."""
    normalized_device_id = str(device_id or "").strip()
    if not normalized_device_id:
        raise ValueError("启用公众号采集时必须选择执行手机")
    normalized_instance = normalize_wechat_app_instance(app_instance)

    async with _definition_lock(project_id, normalized_device_id):
        return await _ensure_wechat_task_definition(
            db,
            project_id=project_id,
            device_id=normalized_device_id,
            app_instance=normalized_instance,
        )


async def _ensure_wechat_task_definition(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    device_id: str,
    app_instance: str = "primary",
) -> dict[str, Any]:
    """Create or repair one definition while holding its project/device lock."""

    task_defs = await collect_dao.list_task_defs(db, project_id=project_id)
    reusable = [
        item
        for item in task_defs
        if _is_company_wechat_task(item, device_id=device_id)
        and not str(item.get("target_id") or "")
    ]
    if reusable:
        selected = _select_wechat_task(reusable)
        return await _repair_wechat_task_definition(
            db,
            selected,
            project_id=project_id,
            device_id=device_id,
            app_instance=app_instance,
        )

    payload = CollectTaskDef(
        **_company_wechat_defaults(app_instance=app_instance),
        project_id=project_id,
        device_id=device_id,
    ).model_dump()
    created = await collect_dao.create_task_def(db, payload)
    logger.notice(
        "自动创建综合扫描公众号采集定义 | project=%s device=%s def=%s",
        project_id,
        device_id,
        created.get("task_def_id"),
    )
    return created


async def resolve_wechat_task_definition(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    device_id: str,
    expected_target_id: str = "",
    allow_running: bool = False,
    create_if_missing: bool = False,
    app_instance: str = "primary",
) -> dict[str, Any]:
    """按手机匹配当前项目的微信采集配置，具体链接策略不暴露给调用侧。"""
    device_id = str(device_id or "").strip()
    if not device_id:
        raise ValueError("启用公众号采集时必须选择执行手机")
    normalized_instance = normalize_wechat_app_instance(app_instance)

    async with _definition_lock(project_id, device_id):
        return await _resolve_wechat_task_definition(
            db,
            project_id=project_id,
            device_id=device_id,
            expected_target_id=expected_target_id,
            allow_running=allow_running,
            create_if_missing=create_if_missing,
            app_instance=normalized_instance,
        )


async def _resolve_wechat_task_definition(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    device_id: str,
    expected_target_id: str = "",
    allow_running: bool = False,
    create_if_missing: bool = False,
    app_instance: str = "primary",
) -> dict[str, Any]:
    """Resolve and repair one definition while holding its shared lock."""

    task_defs = await collect_dao.list_task_defs(db, project_id=project_id)
    candidates = [
        item
        for item in task_defs
        if _is_company_wechat_task(item, device_id=device_id)
    ]
    if not candidates and create_if_missing:
        candidates = [
            await _ensure_wechat_task_definition(
                db,
                project_id=project_id,
                device_id=device_id,
                app_instance=app_instance,
            )
        ]
    if not candidates:
        raise ValueError("所选手机没有当前项目的微信采集配置")
    task_def = _select_wechat_task(
        candidates,
        expected_target_id=expected_target_id,
    )
    task_def = await _repair_wechat_task_definition(
        db,
        task_def,
        project_id=project_id,
        device_id=device_id,
        app_instance=app_instance,
    )
    if task_def.get("status") == "running" and not allow_running:
        raise ValueError("公众号手机采集任务正在运行中")

    configured_target_id = str(task_def.get("target_id") or "")
    if expected_target_id and configured_target_id and configured_target_id != expected_target_id:
        raise ValueError("公众号手机采集任务绑定的 Target 与本次公司不一致")
    return task_def


async def run_company_wechat_collection(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    project_id: str,
    target_id: str,
    target_name: str,
    device_id: str,
    app_instance: str = "primary",
    collection_priority: str = "normal",
    requested_by: str = "",
    on_started: Callable[[], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """用已配置手机发现文章链接，再复用 Chrome Provider 归档正文与图片。"""
    normalized_instance = normalize_wechat_app_instance(app_instance)
    task_def = await resolve_wechat_task_definition(
        db,
        project_id=project_id,
        device_id=device_id,
        expected_target_id=target_id,
        allow_running=True,
        create_if_missing=True,
        app_instance=normalized_instance,
    )
    task_def_id = str(task_def.get("task_def_id") or "")
    run_task_id = f"{task_id}_wechat"
    result = await run_mobile_collect_definition(
        db,
        run_task_id=run_task_id,
        project_id=project_id,
        task_def_id=task_def_id,
        runtime_overrides={
            **_company_wechat_defaults(app_instance=normalized_instance),
            "project_id": project_id,
            "target_id": target_id,
            "target_name": target_name,
            "target_type": "company",
            "direct_launch_app": True,
            "parent_task_id": task_id,
        },
        requested_by=requested_by,
        queue_priority=collection_priority,
        on_started=on_started,
    )
    keyword_resolution = dict(result.get("keyword_resolution") or {})
    keywords_completed = int(result.get("keywords_completed") or 0)
    keyword_total = int(result.get("keyword_total") or 0)
    failed_keywords = int(result.get("failed") or 0)
    persist_failed = int(result.get("persist_failed") or 0)
    partial = bool(
        result.get("timed_out")
        or result.get("stopped")
        or (keyword_total and keywords_completed < keyword_total)
        or failed_keywords
        or persist_failed
    )
    return {
        "kind": "wechat",
        "status": "partial" if partial else "completed",
        "task_def_id": task_def_id,
        "device_id": str(task_def.get("device_id") or ""),
        "app_instance": normalized_instance,
        "total": int(result.get("total") or 0),
        "new": int(result.get("new") or 0),
        "changed": int(result.get("changed") or 0),
        "contacts": int(result.get("contacts") or 0),
        "documents": int(result.get("documents") or 0),
        "high_score_records": int(result.get("high_score_records") or 0),
        "high_score_documents": int(result.get("high_score_documents") or 0),
        "max_score": int(result.get("max_score") or 0),
        "keywords_used": list(result.get("keywords_used") or []),
        "target_ids": list(keyword_resolution.get("target_ids") or []),
        "keyword_resolution": keyword_resolution,
        "keywords_completed": keywords_completed,
        "keyword_total": keyword_total,
        "failed_keywords": failed_keywords,
        "persist_failed": persist_failed,
        "stopped": bool(result.get("stopped")),
        "timed_out": bool(result.get("timed_out")),
    }
