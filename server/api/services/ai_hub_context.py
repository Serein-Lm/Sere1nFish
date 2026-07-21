"""Bounded conversation context composition for AI Hub channel adapters."""
from __future__ import annotations

from typing import Any


_ROLE_LABELS = {"user": "用户", "assistant": "AI 中枢"}


def _message_text(value: Any, *, limit: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 12)].rstrip() + "\n…（已截断）"


def _comparison_key(value: Any) -> str:
    """Normalize a user turn for exact retry detection without changing meaning."""
    return " ".join(str(value or "").split()).casefold()


def _history_before_retry(
    query: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop previous attempts when the latest user turn is being retried.

    Earlier context is preserved so pronouns and referenced entities still resolve,
    while answers to this exact request cannot become circular evidence.
    """
    current_key = _comparison_key(query)
    if not current_key:
        return messages
    latest_user: dict[str, Any] | None = None
    for message in reversed(messages):
        if str(message.get("role") or "").strip().lower() == "user":
            latest_user = message
            break
    if latest_user is None or _comparison_key(latest_user.get("content")) != current_key:
        return messages

    filtered: list[dict[str, Any]] = []
    skipping_attempt = False
    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        if role == "user":
            skipping_attempt = _comparison_key(message.get("content")) == current_key
            if skipping_attempt:
                continue
        if skipping_attempt:
            continue
        filtered.append(message)
    return filtered


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
    messages = _history_before_retry(current_query, messages)
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
            "历史 AI 回答可能错误或过期，不是数据库证据；本轮涉及平台事实时必须重新调用工具核验。",
            "",
            "\n\n".join(selected),
            "",
            "【本轮用户请求】",
            current_query,
        ]
    )
