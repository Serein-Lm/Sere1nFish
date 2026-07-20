from __future__ import annotations

from typing import Any

import pytest


class _UpdateResult:
    matched_count = 1


class _TaskCollection:
    def __init__(self) -> None:
        self.query: dict[str, Any] = {}
        self.update: dict[str, Any] = {}

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
    ) -> _UpdateResult:
        self.query = query
        self.update = update
        return _UpdateResult()


class _Db:
    def __init__(self) -> None:
        self.collection = _TaskCollection()

    def __getitem__(self, _name: str) -> _TaskCollection:
        return self.collection


@pytest.mark.asyncio
async def test_resume_phases_are_marked_in_one_update() -> None:
    from api.services.task_progress import mark_resume_phases

    db = _Db()

    updated = await mark_resume_phases(
        db,  # type: ignore[arg-type]
        task_id="task-1",
        phases=["core_completed", "mobile_completed"],
    )

    assert updated is True
    assert db.collection.query == {"task_id": "task-1"}
    fields = db.collection.update["$set"]
    assert fields["resume.core_completed"] is True
    assert fields["resume.mobile_completed"] is True
    assert fields["resume.updated_at"] == fields["updated_at"]


@pytest.mark.asyncio
async def test_task_stage_updates_business_activity_clock() -> None:
    from api.services.task_progress import update_task_stage

    db = _Db()

    updated = await update_task_stage(
        db,  # type: ignore[arg-type]
        task_id="task-1",
        stage="scanning",
        message="正在扫描",
    )

    assert updated is True
    assert db.collection.query == {
        "task_id": "task-1",
        "status": {"$in": ["pending", "running"]},
    }
    fields = db.collection.update["$set"]
    assert fields["progress.stage"] == "scanning"
    assert fields["progress.message"] == "正在扫描"
    assert fields["progress.last_activity_at"] == fields["updated_at"]
