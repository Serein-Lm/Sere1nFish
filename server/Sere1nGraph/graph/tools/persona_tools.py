"""
人设库检索工具 — 供 AI 中枢 ReAct Agent 调用。

Agent 可据此在生成钓鱼话术/邮件/社工方案前，检索人设库中的真实人物背景作为上下文。
人设库全局化：默认不绑定项目，按公司/行业/职位/标签/关键词检索。
数据读取收敛在 api.dao.persons，本文件仅做同步 tool 封装。
"""
from __future__ import annotations

from typing import Any

from langchain.tools import tool

from . import _refs
from .builtin import _run_coro_sync


def _format_person(p: dict[str, Any], *, brief: bool = True) -> str:
    parts = [f"姓名：{p.get('name', '未知')}"]
    for label, key in (("公司", "company"), ("行业", "industry"), ("职位", "position"), ("所在地", "location")):
        val = p.get(key)
        if val:
            parts.append(f"{label}：{val}")
    if p.get("summary"):
        parts.append(f"摘要：{p['summary']}")
    if not brief:
        if p.get("background"):
            parts.append(f"背景：{p['background']}")
        if p.get("personality"):
            parts.append(f"性格：{p['personality']}")
        if p.get("interests"):
            parts.append(f"兴趣：{', '.join(p['interests'])}")
        if p.get("risk_signals"):
            parts.append(f"风险点：{', '.join(p['risk_signals'])}")
    if p.get("tags"):
        parts.append(f"标签：{', '.join(p['tags'])}")
    parts.append(f"person_id：{p.get('person_id', '')}")
    ref = _refs.person_ref(p.get("person_id", ""), p.get("name", ""))
    if ref:
        parts.append(ref)
    return "；".join(parts)


@tool(
    "search_personas",
    description=(
        "检索人设库中的真实人物档案（全局，不绑定项目）。"
        "支持按关键词、公司、行业、职位、标签筛选，返回匹配人物的结构化摘要。"
        "在生成针对特定人物/公司的钓鱼邮件、社工话术或攻击方案前，先用它获取真实人物背景。"
        "参数均可选：keyword（姓名/公司/职位模糊词）、company、industry、position、tags（逗号分隔）、limit（默认5）。"
    ),
)
def search_personas(
    keyword: str = "",
    company: str = "",
    industry: str = "",
    position: str = "",
    tags: str = "",
    limit: int = 5,
) -> str:
    """检索人设库并返回人物摘要列表。"""

    async def _load() -> tuple[list[dict[str, Any]], int]:
        from api.dao import persons as persons_dao
        from api.db.mongodb import get_db

        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        return await persons_dao.search_persons(
            get_db(),
            keyword=keyword,
            company=company,
            industry=industry,
            position=position,
            tags=tag_list,
            limit=max(1, min(limit, 20)),
        )

    try:
        items, total = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"检索人设库失败：{exc}"

    if not items:
        return "人设库中未找到匹配的人物。可放宽筛选条件或先在人设库中采集该人物。"

    lines = [f"共匹配 {total} 人，返回前 {len(items)} 人："]
    for idx, p in enumerate(items, 1):
        lines.append(f"{idx}. {_format_person(p, brief=True)}")
    return "\n".join(lines)


@tool(
    "get_persona",
    description=(
        "按 person_id 获取人设库中单个人物的完整档案（含背景、性格、兴趣、风险点等）。"
        "在 search_personas 定位到目标人物后，用它拉取完整信息用于个性化话术生成。"
    ),
)
def get_persona(person_id: str) -> str:
    """获取单个人设完整档案。"""

    async def _load() -> dict[str, Any] | None:
        from api.dao import persons as persons_dao
        from api.db.mongodb import get_db

        return await persons_dao.get_person(get_db(), person_id)

    try:
        doc = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"获取人设失败：{exc}"

    if not doc:
        return f"未找到 person_id={person_id} 的人设。"
    return _format_person(doc, brief=False)


# 供 Agent 复用的人设工具集
PERSONA_TOOLS = [search_personas, get_persona]
