"""Agent 主动研究并保存虚构人设的写工具。"""
from __future__ import annotations

from typing import Any

from langchain.tools import tool
from Sere1nGraph.graph.skills.schemas import RichFictionalPersonaProfile

from . import _refs
from .builtin import _run_coro_sync


@tool(
    "save_researched_persona",
    description=(
        "保存 AI 基于公开行业/岗位背景主动研究生成的完整虚构人设。"
        "profile 必须完整符合工具公开的 RichFictionalPersonaProfile schema，明确 is_fictional=true，"
        "包含 generation_brief、具体且自洽的多维信息、公开背景 sources 和 research_evidence；"
        "不得对应或冒充真实自然人，不得保存真实联系方式。"
    ),
)
def save_researched_persona(
    profile: RichFictionalPersonaProfile,
    project_id: str = "",
    task_id: str = "",
) -> str:
    try:
        profile_data: dict[str, Any] = (
            profile.model_dump() if isinstance(profile, RichFictionalPersonaProfile) else dict(profile)
        )

        async def _save():
            from api.db.mongodb import get_db
            from api.services.researched_persona import save_researched_persona as save

            return await save(
                get_db(), profile=profile_data, project_id=project_id, task_id=task_id
            )

        item = _run_coro_sync(_save())
    except Exception as exc:  # noqa: BLE001
        return f"保存主动研究人设失败：{exc}"
    ref = _refs.person_ref(str(item.get("person_id") or ""), str(item.get("name") or ""))
    return (
        f"虚构人设已保存：{item.get('name')}；{item.get('industry')}；"
        f"{item.get('position')}；资料版本=v{int(item.get('profile_version') or 1)}；"
        f"研究轮次={int(item.get('research_rounds') or 1)}；"
        f"person_id={item.get('person_id')} {ref}"
    ).strip()


PERSONA_COLLECTION_TOOLS = [save_researched_persona]
