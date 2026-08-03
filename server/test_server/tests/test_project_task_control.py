from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest


def _read_path(document: dict[str, Any], path: str) -> Any:
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _write_path(document: dict[str, Any], path: str, value: Any) -> None:
    target = document
    parts = path.split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value


def _remove_path(document: dict[str, Any], path: str) -> None:
    target = document
    parts = path.split(".")
    for part in parts[:-1]:
        value = target.get(part)
        if not isinstance(value, dict):
            return
        target = value
    target.pop(parts[-1], None)


class _Result:
    def __init__(self, modified_count: int) -> None:
        self.modified_count = modified_count


class _Collection:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document

    def _matches(self, query: dict[str, Any]) -> bool:
        for key, expected in query.items():
            actual = _read_path(self.document, key)
            if isinstance(expected, dict) and "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            elif actual != expected:
                return False
        return True

    def _apply(self, update: dict[str, Any]) -> None:
        for key, value in update.get("$set", {}).items():
            _write_path(self.document, key, value)
        for key, value in update.get("$inc", {}).items():
            _write_path(
                self.document,
                key,
                int(_read_path(self.document, key) or 0) + int(value),
            )
        for key in update.get("$unset", {}):
            _remove_path(self.document, key)

    async def find_one(self, query: dict[str, Any], _projection=None):
        return deepcopy(self.document) if self._matches(query) else None

    async def find_one_and_update(self, query, update, **_kwargs):
        if not self._matches(query):
            return None
        self._apply(update)
        return deepcopy(self.document)

    async def update_one(self, query, update):
        if not self._matches(query):
            return _Result(0)
        self._apply(update)
        return _Result(1)


class _Db:
    def __init__(self, document: dict[str, Any]) -> None:
        self.collection = _Collection(document)

    def __getitem__(self, _name: str) -> _Collection:
        return self.collection


@pytest.mark.asyncio
async def test_task_pause_and_resume_preserve_checkpoint() -> None:
    from api.dao import tasks

    document = {
        "project_id": "project-1",
        "task_id": "task-1",
        "task_type": "company_scan",
        "status": "running",
        "runtime_id": "runtime-1",
        "checkpoint": {"modules": {"asset_url": {"status": "completed"}}},
        "progress": {"stage": "url_scan"},
    }
    db = _Db(document)

    pausing = await tasks.request_task_pause(
        db,
        project_id="project-1",
        task_id="task-1",
    )
    assert pausing and pausing["status"] == "pausing"
    assert await tasks.is_pause_requested(
        db,
        task_id="task-1",
        runtime_id="runtime-1",
    )
    assert await tasks.mark_task_paused(
        db,
        task_id="task-1",
        runtime_id="runtime-1",
    )
    resumed = await tasks.resume_task(
        db,
        project_id="project-1",
        task_id="task-1",
    )

    assert resumed and resumed["status"] == "pending"
    assert resumed["manual_resume_count"] == 1
    assert resumed["checkpoint"] == document["checkpoint"]
    assert "runtime_id" not in resumed
