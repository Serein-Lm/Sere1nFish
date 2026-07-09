"""Shared observation context helpers.

Business modules should use this module instead of importing TokenTracker
directly.  The implementation can change without changing route/service code.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator


@contextmanager
def observation_context(
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    turn_id: str | None = None,
    phase: str | None = None,
    agent: str | None = None,
    task_type: str | None = None,
) -> Iterator[None]:
    """Scope LLM token attribution for the current async task.

    Empty fields inherit from any parent context.  This keeps service code
    decoupled from the concrete TokenTracker implementation while preserving
    project/task/task_type/phase/agent attribution for Dashboard and
    Observability views.
    """
    from Sere1nGraph.graph.observability import get_global_tracker

    tracker = get_global_tracker()
    tracker.push_context(
        project_id=project_id or "",
        task_id=task_id or "",
        turn_id=turn_id or "",
        phase=phase or "",
        agent=agent or "",
        task_type=task_type or "",
    )
    try:
        yield
    finally:
        tracker.pop_context()
