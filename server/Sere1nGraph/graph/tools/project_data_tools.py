"""Unified project dataset tools for AI Hub specialists."""
from __future__ import annotations

import json

from langchain.tools import tool

from .builtin import _run_coro_sync


def _current_access() -> tuple[str, bool]:
    from api.services.artifact_context import get_artifact_context

    context = get_artifact_context()
    return (context.owner, context.is_admin) if context else ("", False)


@tool(
    "get_project_data_catalog",
    description=(
        "检查一个项目可读取的数据源、可用性、精确数量及支持的过滤条件。返回经过业务规则"
        "清洗的网站、招投标、资产、小红书、抖音、公众号原文、Target 看板、学者联系、"
        "任务、日志、finding、手机数据和 AI 产物等数据源。引用整个项目或不确定应该读取"
        "哪个数据源时先调用。参数：project_id。"
    ),
)
def get_project_data_catalog(project_id: str) -> str:
    project_id = str(project_id or "").strip()
    if not project_id:
        return "请提供 project_id。"

    owner, is_admin = _current_access()

    async def _load():
        from api.db.mongodb import get_db
        from api.services.project_data_reader import inspect_project_datasets

        return await inspect_project_datasets(
            get_db(), project_id, owner=owner, is_admin=is_admin
        )

    try:
        payload = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"读取项目数据目录失败：{exc}"
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@tool(
    "read_project_dataset",
    description=(
        "读取项目的一个具体数据源，返回经过脱敏和长度限制的结构化 JSON。"
        "source 必须使用 get_project_data_catalog 返回的 source 值；新增数据源无需修改调用方式。"
        "参数：project_id、source、limit（默认20，上限50）、offset（翻页偏移）、"
        "target_id（按 Target 聚焦，可用性以目录 filters 为准）、min_score（0-100）。"
        "返回 has_more 和 next_offset；需要继续读取时使用 next_offset，禁止反复读取第一页。"
    ),
)
def read_project_dataset(
    project_id: str,
    source: str,
    limit: int = 20,
    offset: int = 0,
    target_id: str = "",
    min_score: int = 0,
) -> str:
    project_id = str(project_id or "").strip()
    source = str(source or "").strip().lower()
    if not project_id:
        return "请提供 project_id。"
    if not source:
        return "请提供 source；可先调用 get_project_data_catalog。"

    owner, is_admin = _current_access()

    async def _load():
        from api.db.mongodb import get_db
        from api.services.project_data_reader import read_project_dataset as read_dataset

        return await read_dataset(
            get_db(),
            project_id,
            source,
            limit=max(1, min(int(limit or 20), 50)),
            offset=max(0, min(int(offset or 0), 10_000)),
            target_id=str(target_id or "").strip(),
            min_score=max(0, min(int(min_score or 0), 100)),
            owner=owner,
            is_admin=is_admin,
        )

    try:
        payload = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"读取项目数据失败：{exc}"
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


PROJECT_DATA_TOOLS = [get_project_data_catalog, read_project_dataset]
