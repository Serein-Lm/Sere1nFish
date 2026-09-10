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
    from api.services.skill_library.selection import current_selected_skill_ids

    selected = current_selected_skill_ids()
    prompt = registry.get_index_prompt(selected)
    if selected is not None:
        return "本轮用户显式选择的 Skill：\n" + (prompt or "（无可用 Skill）")
    return prompt


def _selection_error(skill_id: str) -> str:
    from api.services.skill_library.selection import current_selected_skill_ids

    selected = current_selected_skill_ids()
    if selected is not None and skill_id not in selected:
        return f"Skill '{skill_id}' 不在本轮用户选择范围内。"
    return ""


def _load_enabled_skill(skill_id: str):
    from ..skills.registry import get_skill_registry

    registry = get_skill_registry()
    index = registry.get_index(skill_id)
    if index is None or not index.enabled:
        return None
    return registry.load_skill(skill_id)


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
    selection_error = _selection_error(skill_id)
    if selection_error:
        return selection_error
    skill = _load_enabled_skill(skill_id)
    if not skill:
        return f"Skill '{skill_id}' 不存在。请先调用 list_available_skills 查看可用 skills。"

    parts = [f"# Skill: {skill.name}\n\n{skill.body}"]
    if skill.references:
        parts.append(
            f"\n\n## 可用案例文件（用 load_skill_reference 加载）:\n"
            + "\n".join(f"- {ref}" for ref in skill.references)
        )
    other_resources = [path for path in skill.resources if path not in skill.references]
    if other_resources:
        parts.append(
            "\n\n## 可用支持资源（用 load_skill_resource 按需加载）:\n"
            + "\n".join(f"- {path}" for path in other_resources[:120])
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
    selection_error = _selection_error(skill_id)
    if selection_error:
        return selection_error
    skill = _load_enabled_skill(skill_id)
    if not skill:
        return f"Skill '{skill_id}' 不存在。"

    content = skill.load_reference(reference_name)
    if not content:
        available = ", ".join(skill.references) if skill.references else "无"
        return f"案例文件 '{reference_name}' 不存在。可用文件: {available}"

    if len(content) > 40_000:
        return content[:40_000] + "\n\n[资源正文已在 40000 字符处截断，请只读取任务需要的更具体资源。]"
    return content


@tool(
    "load_skill_resource",
    description=(
        "按需读取 Skill 包内的脚本、模板说明、规范或其他文本资源。"
        "先调用 load_skill 查看资源路径，再传入 skill_id 与 resource_path；"
        "不要一次读取无关资源。"
    ),
)
def load_skill_resource(skill_id: str, resource_path: str) -> str:
    selection_error = _selection_error(skill_id)
    if selection_error:
        return selection_error
    skill = _load_enabled_skill(skill_id)
    if not skill:
        return f"Skill '{skill_id}' 不存在。"
    content = skill.load_resource(resource_path)
    if not content:
        available = ", ".join(skill.resources[:80]) if skill.resources else "无"
        return f"资源 '{resource_path}' 不存在或不是文本。可用资源: {available}"
    if len(content) > 40_000:
        return content[:40_000] + "\n\n[资源正文已在 40000 字符处截断。]"
    return content


# 工具列表，供 factory 使用
SKILL_TOOLS = [
    list_available_skills,
    load_skill,
    load_skill_reference,
    load_skill_resource,
]
