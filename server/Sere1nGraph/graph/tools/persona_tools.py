"""
虚构人设库检索工具 — 供 AI 中枢 ReAct Agent 调用。

Agent 可据此检索背景研究后生成的虚构人物，不得将其表述为真实自然人。
人设库全局化：默认不绑定项目，按公司/行业/职位/标签/关键词检索。
数据读取收敛在 api.dao.persons，本文件仅做同步 tool 封装。
"""
from __future__ import annotations

from typing import Any

from langchain.tools import tool

from . import _refs
from .builtin import _run_coro_sync


def _compact_dict(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts: list[str] = []
    for key, item in value.items():
        if isinstance(item, list):
            text = "、".join(str(entry) for entry in item if entry)
        else:
            text = str(item or "").strip()
        if text:
            parts.append(f"{key}={text}")
    return "，".join(parts)


def _format_person(p: dict[str, Any], *, brief: bool = True) -> str:
    parts = [f"姓名：{p.get('name', '未知')}"]
    if p.get("is_fictional"):
        parts.append("类型：虚构人设（不对应真实自然人）")
    if p.get("age") is not None:
        parts.append(f"年龄：{p['age']}")
    elif p.get("age_range"):
        parts.append(f"年龄段：{p['age_range']}")
    for label, key in (("公司", "company"), ("行业", "industry"), ("职位", "position"), ("所在地", "location")):
        val = p.get(key)
        if val:
            parts.append(f"{label}：{val}")
    if p.get("summary"):
        parts.append(f"摘要：{p['summary']}")
    if p.get("profile_version"):
        parts.append(
            f"资料版本：v{p['profile_version']}（研究 {p.get('research_rounds') or 0} 轮）"
        )
    if not brief:
        if p.get("aliases"):
            parts.append(f"别名：{', '.join(p['aliases'])}")
        if p.get("work_years"):
            parts.append(f"工作年限：{p['work_years']}")
        education = _compact_dict(p.get("education"))
        if education:
            parts.append(f"教育背景：{education}")
        contact = _compact_dict(p.get("contact"))
        if contact:
            parts.append(f"公开联系方式：{contact}")
        if p.get("background"):
            parts.append(f"背景：{p['background']}")
        if p.get("personality"):
            parts.append(f"性格：{p['personality']}")
        for label, key in (
            ("组织环境", "organization_context"),
            ("职业阶段", "career_stage"),
            ("职业路径", "career_path"),
            ("生活阶段", "life_stage"),
            ("工作场景", "work_context"),
            ("工作节奏", "work_rhythm"),
            ("决策方式", "decision_style"),
            ("沟通方式", "communication_style"),
            ("协作方式", "collaboration_style"),
            ("技术态度", "technology_attitude"),
            ("学习方式", "learning_style"),
            ("压力反应", "stress_response"),
        ):
            if p.get(key):
                parts.append(f"{label}：{p[key]}")
        if p.get("interests"):
            parts.append(f"兴趣：{', '.join(p['interests'])}")
        for label, key in (
            ("信息偏好", "information_preferences"),
            ("数字习惯", "digital_habits"),
            ("核心动机", "motivations"),
            ("阶段目标", "goals"),
            ("具体痛点", "pain_points"),
            ("价值取向", "values"),
            ("行为模式", "behavior_patterns"),
            ("内容偏好", "content_preferences"),
            ("选择考虑", "purchase_considerations"),
        ):
            if p.get(key):
                parts.append(f"{label}：{', '.join(p[key])}")
        if p.get("risk_signals"):
            parts.append(f"风险点：{', '.join(p['risk_signals'])}")
        if p.get("generation_brief"):
            parts.append(f"生成背景：{p['generation_brief']}")
        if p.get("source_urls"):
            parts.append("背景参考来源：" + "、".join(str(x) for x in p["source_urls"][:12]))
        if p.get("evidence"):
            parts.append("背景依据：" + "；".join(str(x) for x in p["evidence"][:8]))
        if p.get("research_evidence"):
            evidence_lines = []
            for item in p["research_evidence"][:12]:
                if not isinstance(item, dict):
                    continue
                evidence_lines.append(
                    "{dimension}｜{finding}｜适用：{applicability}｜来源：{sources}".format(
                        dimension=item.get("dimension") or "未分类",
                        finding=item.get("finding") or "",
                        applicability=item.get("applicability") or "",
                        sources="、".join(item.get("source_urls") or []),
                    )
                )
            if evidence_lines:
                parts.append("结构化研究证据：" + "；".join(evidence_lines))
        if p.get("confidence") is not None:
            parts.append(f"内部一致性：{p['confidence']}")
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
        "检索人设库中的虚构人物档案（全局，不绑定项目，不对应真实自然人）。"
        "支持按关键词、公司、行业、职位、年龄、性格、标签筛选，只返回 summary、"
        "核心标签和 person_id。必须先用本工具筛选，再对选中的 person_id 调用 get_persona，"
        "不要批量读取全库明细。"
        "参数均可选：keyword、company、industry、position、personality、age_min、age_max、"
        "tags（逗号分隔）、limit（默认5）。"
    ),
)
def search_personas(
    keyword: str = "",
    company: str = "",
    industry: str = "",
    position: str = "",
    personality: str = "",
    age_min: int | None = None,
    age_max: int | None = None,
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
            personality=personality,
            age_min=age_min,
            age_max=age_max,
            tags=tag_list,
            limit=max(1, min(limit, 20)),
            summary_only=True,
        )

    try:
        items, total = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"检索人设库失败：{exc}"

    if not items:
        return "人设库中未找到匹配的虚构人物。可放宽筛选条件或先生成一批人设。"

    lines = [f"共匹配 {total} 人，返回前 {len(items)} 人："]
    for idx, p in enumerate(items, 1):
        lines.append(f"{idx}. {_format_person(p, brief=True)}")
    return "\n".join(lines)


@tool(
    "get_persona",
    description=(
        "按 person_id 获取一个虚构人物的完整档案（含背景、年龄、性格、兴趣和背景参考来源）。"
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
