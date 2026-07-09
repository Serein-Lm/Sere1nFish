"""
Skill 工具 — 供 ReAct Agent 调用

Agent 通过这些 tool 自主决定加载哪些 skill 和案例。
渐进式披露：先看索引(list) → 加载指令(load) → 加载案例(reference)
"""

from __future__ import annotations

from langchain.tools import tool


@tool(
    "list_available_skills",
    description=(
        "列出所有可用的话术生成 Skills 的索引摘要。"
        "返回每个 skill 的 id、名称、描述、类别、适用阶段、标签。"
        "在生成话术前先调用此工具，了解有哪些 skill 可用，然后决定加载哪些。"
    ),
)
def list_available_skills() -> str:
    """列出所有 skill 索引（Layer 1）"""
    from ..skills.registry import get_skill_registry
    registry = get_skill_registry()
    return registry.get_index_prompt()


@tool(
    "load_skill",
    description=(
        "加载指定 skill 的完整指令内容（SKILL.md body）。"
        "传入 skill_id（如 'wechat'、'email'、'base-scenario'），"
        "返回该 skill 的详细指令，包含输出 Schema 字段映射、规则、模板。"
        "同时返回该 skill 的 references 文件列表，可按需进一步加载案例。"
    ),
)
def load_skill(skill_id: str) -> str:
    """加载 skill 完整指令（Layer 2）"""
    from ..skills.registry import get_skill_registry
    registry = get_skill_registry()
    skill = registry.load_skill(skill_id)
    if not skill:
        return f"Skill '{skill_id}' 不存在。请先调用 list_available_skills 查看可用 skills。"

    parts = [f"# Skill: {skill.name}\n\n{skill.body}"]
    if skill.references:
        parts.append(
            f"\n\n## 可用案例文件（用 load_skill_reference 加载）:\n"
            + "\n".join(f"- {ref}" for ref in skill.references)
        )
    return "\n".join(parts)


@tool(
    "load_skill_reference",
    description=(
        "加载指定 skill 的某个案例/参考文件（references/ 目录下的文件）。"
        "传入 skill_id 和 reference_name（如 'wechat-dialogue-cases.md'），"
        "返回该案例文件的完整内容，包含实战案例的 JSON 示例。"
        "在需要参考具体案例时调用。"
    ),
)
def load_skill_reference(skill_id: str, reference_name: str) -> str:
    """加载 skill 案例文件（Layer 3）"""
    from ..skills.registry import get_skill_registry
    registry = get_skill_registry()
    skill = registry.load_skill(skill_id)
    if not skill:
        return f"Skill '{skill_id}' 不存在。"

    content = skill.load_reference(reference_name)
    if not content:
        available = ", ".join(skill.references) if skill.references else "无"
        return f"案例文件 '{reference_name}' 不存在。可用文件: {available}"

    return content


# 工具列表，供 factory 使用
SKILL_TOOLS = [list_available_skills, load_skill, load_skill_reference]
