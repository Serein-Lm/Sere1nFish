"""Typed external prerequisites shared by task runtimes and adapters."""
from __future__ import annotations

from typing import Any


class TaskDependencyBlocked(RuntimeError):
    """Pause until a person restores an external prerequisite and resumes."""

    def __init__(self, reason: str, message: str, *, context: dict[str, Any] | None = None,
                 result: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.context = dict(context or {})
        self.result = dict(result or {})

    def as_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "message": str(self), **self.context}
