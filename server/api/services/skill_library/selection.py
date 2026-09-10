"""Request-scoped Skill selection shared by web, DingTalk and task agents."""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_selected_skill_ids: ContextVar[frozenset[str] | None] = ContextVar(
    "selected_skill_ids",
    default=None,
)


def normalize_selected_skill_ids(value: Any, *, limit: int = 32) -> list[str]:
    if value is None or value == "":
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        slug = str(item or "").strip()
        if not slug:
            continue
        if not _SLUG_RE.fullmatch(slug):
            raise ValueError(f"Skill ID 格式无效: {slug}")
        if slug not in seen:
            result.append(slug)
            seen.add(slug)
        if len(result) > limit:
            raise ValueError(f"一次最多选择 {limit} 个 Skill")
    return result


def validate_selected_skill_ids(value: Any) -> list[str]:
    from Sere1nGraph.graph.skills.registry import get_skill_registry

    selected = normalize_selected_skill_ids(value)
    registry = get_skill_registry()
    missing: list[str] = []
    for skill_id in selected:
        index = registry.get_index(skill_id)
        if index is None or not index.enabled:
            missing.append(skill_id)
    if missing:
        raise ValueError(f"Skill 不存在或未通过审核: {', '.join(missing)}")
    return selected


def current_selected_skill_ids() -> frozenset[str] | None:
    return _selected_skill_ids.get()


@contextmanager
def skill_selection(skill_ids: list[str] | tuple[str, ...] | None) -> Iterator[None]:
    normalized = normalize_selected_skill_ids(skill_ids)
    token = _selected_skill_ids.set(frozenset(normalized) if normalized else None)
    try:
        yield
    finally:
        _selected_skill_ids.reset(token)


def compose_selection_instruction(query: str, skill_ids: list[str]) -> str:
    if not skill_ids:
        return query
    selected = ", ".join(skill_ids)
    return (
        f"{query}\n\n"
        "【本轮显式 Skill 约束】\n"
        f"用户选择了以下 Skill：{selected}。先读取它们的索引，再按任务需要加载正文或资源；"
        "不得加载选择范围外的 Skill。"
    )
