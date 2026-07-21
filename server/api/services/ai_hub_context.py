"""Bounded conversation context composition for AI Hub channel adapters."""
from __future__ import annotations

from typing import Any


_ROLE_LABELS = {"user": "用户", "assistant": "AI 中枢"}


def _message_text(value: Any, *, limit: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 12)].rstrip() + "\n…（已截断）"


def compose_conversation_query(
    query: str,
    messages: list[dict[str, Any]],
    *,
    max_messages: int = 12,
    max_history_chars: int = 12_000,
    max_message_chars: int = 3_000,
) -> str:
    """Attach recent user/assistant turns while keeping the current request primary."""
    current_query = str(query or "").strip()
    if max_messages <= 0 or max_history_chars <= 0:
        return current_query
    candidates: list[str] = []
    for message in messages[-max_messages:]:
        role = str(message.get("role") or "").strip().lower()
        label = _ROLE_LABELS.get(role)
        content = _message_text(message.get("content"), limit=max_message_chars)
        if label and content:
            candidates.append(f"{label}：\n{content}")

    selected: list[str] = []
    used = 0
    for block in reversed(candidates):
        cost = len(block) + 2
        if selected and used + cost > max_history_chars:
            break
        if not selected and cost > max_history_chars:
            block = block[:max_history_chars].rstrip()
            cost = len(block)
        selected.append(block)
        used += cost
    selected.reverse()

    if not selected:
        return current_query
    return "\n".join(
        [
            "【同一会话最近上下文】",
            "以下是此前已发送的对话，只用于理解指代和延续任务；若有冲突，以本轮请求为准。",
            "",
            "\n\n".join(selected),
            "",
            "【本轮用户请求】",
            current_query,
        ]
    )
