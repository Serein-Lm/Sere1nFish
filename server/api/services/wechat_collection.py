"""综合扫描中的微信公众号采集适配层。"""
from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import mobile_collect as collect_dao
from api.services.mobile_collect_pipeline import run_mobile_collect_definition


WECHAT_SOURCE_LINK_STRATEGY = "wechat_copy_link"


async def resolve_wechat_task_definition(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    device_id: str,
    expected_target_id: str = "",
) -> dict[str, Any]:
    """按手机匹配当前项目的微信采集配置，具体链接策略不暴露给调用侧。"""
    device_id = str(device_id or "").strip()
    if not device_id:
        raise ValueError("启用公众号采集时必须选择执行手机")

    task_defs = await collect_dao.list_task_defs(db, project_id=project_id)
    candidates = [
        item
        for item in task_defs
        if str(item.get("device_id") or "") == device_id
        and (
            "微信" in str(item.get("app_name") or "").strip().lower()
            or "wechat" in str(item.get("app_name") or "").strip().lower()
        )
    ]
    if not candidates:
        raise ValueError("所选手机没有当前项目的微信采集配置")
    task_def = next(
        (
            item
            for item in candidates
            if str(item.get("source_link_strategy") or "")
            == WECHAT_SOURCE_LINK_STRATEGY
        ),
        candidates[0],
    )
    if task_def.get("status") == "running":
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
) -> dict[str, Any]:
    """用已配置手机发现文章链接，再复用 Chrome Provider 归档正文与图片。"""
    task_def = await resolve_wechat_task_definition(
        db,
        project_id=project_id,
        device_id=device_id,
        expected_target_id=target_id,
    )
    task_def_id = str(task_def.get("task_def_id") or "")
    run_task_id = f"{task_id}_wechat"
    result = await run_mobile_collect_definition(
        db,
        run_task_id=run_task_id,
        project_id=project_id,
        task_def_id=task_def_id,
        runtime_overrides={
            "project_id": project_id,
            "target_id": target_id,
            "target_name": target_name,
            "target_type": "company",
            "use_target_keyword_library": True,
            "app_name": "微信",
            "direct_launch_app": True,
            "deep_collect": True,
            "source_link_strategy": WECHAT_SOURCE_LINK_STRATEGY,
        },
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
