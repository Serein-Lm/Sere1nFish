"""Unified adapter for token usage from non-LangChain model clients."""

from __future__ import annotations

from typing import Any


def record_llm_usage(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: float = 0.0,
    run_id: str = "",
) -> bool:
    """Record one OpenAI-compatible response through the shared tracker."""
    from Sere1nGraph.graph.observability import get_global_tracker

    return get_global_tracker().record_usage(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=duration_ms,
        run_id=run_id,
    )
