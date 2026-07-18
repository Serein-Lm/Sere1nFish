"""综合扫描中的微信公众号采集适配层。"""
from __future__ import annotations

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


def _company_wechat_defaults() -> dict[str, Any]:
    """Build the complete WeChat article profile used by company scans."""
    task = get_preset_task("wechat_official")
    task.update(
        {
            "name": WECHAT_AUTO_TASK_NAME,
            "keywords": [],
            "use_target_keyword_library": True,
            "deep_collect": True,
            "source_link_strategy": WECHAT_SOURCE_LINK_STRATEGY,
        }
    )
    return task


def _is_company_wechat_task(task_def: dict[str, Any], *, device_id: str) -> bool:
    return _is_wechat_task(task_def, device_id=device_id) and (
        str(task_def.get("name") or "") == WECHAT_AUTO_TASK_NAME
        or str(task_def.get("source_link_strategy") or "")
        == WECHAT_SOURCE_LINK_STRATEGY
    )


def _wechat_definition_patch(task_def: dict[str, Any]) -> dict[str, Any]:
    """Repair only missing critical profile fields without replacing tuning."""
    defaults = _company_wechat_defaults()
    patch: dict[str, Any] = {}
    for field in ("extract_fields", "dedup_key_fields"):
        if not task_def.get(field):
            patch[field] = defaults[field]
    for field in ("search_hint", "source_link_strategy"):
        if not str(task_def.get(field) or "").strip():
            patch[field] = defaults[field]
    if not task_def.get("deep_collect"):
        patch["deep_collect"] = True
    return patch


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
) -> dict[str, Any]:
    """Ensure comprehensive scans can use a selected pool device directly."""
    normalized_device_id = str(device_id or "").strip()
    if not normalized_device_id:
        raise ValueError("启用公众号采集时必须选择执行手机")

    task_defs = await collect_dao.list_task_defs(db, project_id=project_id)
    reusable = [
        item
        for item in task_defs
        if _is_company_wechat_task(item, device_id=normalized_device_id)
        and not str(item.get("target_id") or "")
    ]
    if reusable:
        selected = _select_wechat_task(reusable)
        patch = _wechat_definition_patch(selected)
        if patch:
            repaired = await collect_dao.update_task_def(
                db,
                str(selected.get("task_def_id") or ""),
                patch,
            )
            if repaired:
                logger.notice(
                    "自动修复综合扫描公众号采集定义 | project=%s device=%s def=%s fields=%s",
                    project_id,
                    normalized_device_id,
                    selected.get("task_def_id"),
                    sorted(patch),
                )
                return repaired
        return selected

    payload = CollectTaskDef(
        **_company_wechat_defaults(),
        project_id=project_id,
        device_id=normalized_device_id,
    ).model_dump()
    created = await collect_dao.create_task_def(db, payload)
    logger.notice(
        "自动创建综合扫描公众号采集定义 | project=%s device=%s def=%s",
        project_id,
        normalized_device_id,
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
) -> dict[str, Any]:
    """按手机匹配当前项目的微信采集配置，具体链接策略不暴露给调用侧。"""
    device_id = str(device_id or "").strip()
    if not device_id:
        raise ValueError("启用公众号采集时必须选择执行手机")

    task_defs = await collect_dao.list_task_defs(db, project_id=project_id)
    candidates = [
        item
        for item in task_defs
        if _is_company_wechat_task(item, device_id=device_id)
    ]
    if not candidates:
        raise ValueError("所选手机没有当前项目的微信采集配置")
    task_def = _select_wechat_task(
        candidates,
        expected_target_id=expected_target_id,
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
    requested_by: str = "",
) -> dict[str, Any]:
    """用已配置手机发现文章链接，再复用 Chrome Provider 归档正文与图片。"""
    task_def = await resolve_wechat_task_definition(
        db,
        project_id=project_id,
        device_id=device_id,
        expected_target_id=target_id,
        allow_running=True,
    )
    task_def_id = str(task_def.get("task_def_id") or "")
    run_task_id = f"{task_id}_wechat"
    result = await run_mobile_collect_definition(
        db,
        run_task_id=run_task_id,
        project_id=project_id,
        task_def_id=task_def_id,
        runtime_overrides={
            **_company_wechat_defaults(),
            "project_id": project_id,
            "target_id": target_id,
            "target_name": target_name,
            "target_type": "company",
            "direct_launch_app": True,
        },
        requested_by=requested_by,
    )
    return {
        "kind": "wechat",
        "status": "completed",
        "task_def_id": task_def_id,
        "device_id": str(task_def.get("device_id") or ""),
        "total": int(result.get("total") or 0),
        "new": int(result.get("new") or 0),
        "changed": int(result.get("changed") or 0),
        "contacts": int(result.get("contacts") or 0),
        "documents": int(result.get("documents") or 0),
        "keywords_used": list(result.get("keywords_used") or []),
        "stopped": bool(result.get("stopped")),
    }
