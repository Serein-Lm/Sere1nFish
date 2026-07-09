"""Shared LLM request parameter helpers."""

from __future__ import annotations

from typing import Any


def disable_thinking_extra_body(
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an OpenAI-compatible extra_body with thinking explicitly disabled."""
    merged = dict(extra_body or {})
    merged["enable_thinking"] = False
    return merged
