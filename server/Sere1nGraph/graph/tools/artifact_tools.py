"""Read-only tools for AI-generated artifacts."""
from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from .builtin import _run_coro_sync


def _current_access() -> tuple[str, bool]:
    from api.services.artifact_context import get_artifact_context

    context = get_artifact_context()
    return (context.owner, context.is_admin) if context else ("", False)


@tool(
    "list_my_artifacts",
    description=(
        "列出当前用户已生成的 AI 产物（只读），可按 kind 过滤。"
        "返回 artifact_id、标题、类型、时间和引用标记。参数：kind（可选）、limit（默认20）。"
    ),
)
def list_my_artifacts(kind: str = "", limit: int = 20) -> str:
    owner, is_admin = _current_access()
    if not owner:
        return "当前执行上下文没有用户归属，无法查询个人产物。"

    async def _load() -> list[dict[str, Any]]:
        from api.dao import artifacts as artifacts_dao
        from api.db.mongodb import get_db

        return await artifacts_dao.list_artifacts(
            get_db(),
            owner="" if is_admin else owner,
            kind=(kind or "").strip(),
            limit=max(1, min(limit, 50)),
        )

    try:
        items = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"查询产物失败：{exc}"
    if not items:
        return "当前用户暂无匹配的 AI 产物。"

    lines = [f"当前用户共有 {len(items)} 个匹配产物："]
    for index, item in enumerate(items, 1):
        artifact_id = item.get("artifact_id", "")
        title = item.get("title") or artifact_id
        lines.append(
            f"{index}. {title}（{item.get('kind', 'word')}，{item.get('created_at', '')}）"
            f" [[artifact:{artifact_id}|{title}]]"
        )
    return "\n".join(lines)


@tool(
    "get_artifact_content",
    description=(
        "按 artifact_id 读取当前用户已有 AI 产物的正文、来源与平台引用（只读）。"
        "用于把历史 Word 产物作为新任务输入；不会直接读取任意服务器文件。"
    ),
)
def get_artifact_content(artifact_id: str) -> str:
    artifact_id = (artifact_id or "").strip()
    owner, is_admin = _current_access()
    if not artifact_id:
        return "请提供 artifact_id。"
    if not owner:
        return "当前执行上下文没有用户归属，无法读取个人产物。"

    async def _load() -> dict[str, Any] | None:
        from api.dao import artifacts as artifacts_dao
        from api.db.mongodb import get_db

        return await artifacts_dao.get_artifact(get_db(), artifact_id)

    try:
        item = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"读取产物失败：{exc}"
    if not item:
        return f"未找到 artifact_id={artifact_id} 的产物。"
    artifact_owner = str(item.get("owner") or "")
    if artifact_owner and artifact_owner != owner and not is_admin:
        return "无权读取该产物。"

    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    payload = {
        "artifact_id": artifact_id,
        "title": item.get("title", ""),
        "kind": item.get("kind", ""),
        "content": str(meta.get("content") or "")[:100_000],
        "sources": meta.get("sources") or [],
        "references": meta.get("references") or [],
        "created_at": item.get("created_at", ""),
    }
    if not payload["content"]:
        payload["content"] = "（该历史产物未保存可引用正文，仅可下载原文件。）"
    return json.dumps(payload, ensure_ascii=False, indent=2)


ARTIFACT_QUERY_TOOLS = [list_my_artifacts, get_artifact_content]
