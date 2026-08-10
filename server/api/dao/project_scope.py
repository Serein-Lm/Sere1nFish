"""Shared MongoDB query semantics for records associated with a project."""
from __future__ import annotations

from typing import Any


def project_scope_query(
    project_id: str,
    conditions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Match an owning project or a secondary project association.

    Some immutable evidence is stored once and linked to several projects through
    ``project_ids``. Keeping this composition here prevents callers from
    overwriting an existing ``$or`` condition such as an incremental-state filter.
    """
    normalized = str(project_id or "").strip()
    scope = {
        "$or": [
            {"project_id": normalized},
            {"project_ids": normalized},
        ]
    }
    extra = dict(conditions or {})
    if not extra:
        return scope
    if "$or" not in extra and "$and" not in extra:
        return {**extra, **scope}
    return {"$and": [scope, extra]}
