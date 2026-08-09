from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


class _FakeDatabase:
    def __init__(self, collections: dict[str, Any]) -> None:
        self.collections = collections

    def __getitem__(self, name: str) -> Any:
        return self.collections[name]


@pytest.mark.asyncio
async def test_delete_group_only_ungroups_projects() -> None:
    from api.dao import project_groups
    from api.db.collections import PROJECT_GROUPS_COLLECTION, PROJECTS_COLLECTION

    class GroupCollection:
        async def find_one(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"group_id": "pg-one", "name": "第一批"}

        async def delete_one(self, query: dict[str, Any]) -> Any:
            assert query == {"group_id": "pg-one"}
            return SimpleNamespace(deleted_count=1)

    class ProjectCollection:
        async def update_many(
            self,
            query: dict[str, Any],
            update: dict[str, Any],
        ) -> Any:
            assert query == {"group_id": "pg-one"}
            assert update["$unset"] == {"group_id": ""}
            assert "updated_at" in update["$set"]
            return SimpleNamespace(modified_count=3)

    db = _FakeDatabase(
        {
            PROJECT_GROUPS_COLLECTION: GroupCollection(),
            PROJECTS_COLLECTION: ProjectCollection(),
        }
    )

    assert await project_groups.delete_group(db, "pg-one") == (True, 3)


@pytest.mark.asyncio
async def test_project_update_null_group_uses_unset() -> None:
    from api.dao import projects
    from api.db.collections import PROJECTS_COLLECTION

    class ProjectCollection:
        update: dict[str, Any] = {}

        async def find_one_and_update(
            self,
            _query: dict[str, Any],
            update: dict[str, Any],
            **_kwargs: Any,
        ) -> dict[str, Any]:
            self.update = update
            return {"name": "项目", "updated_at": update["$set"]["updated_at"]}

    collection = ProjectCollection()
    db = _FakeDatabase({PROJECTS_COLLECTION: collection})
    doc = await projects.update_project(
        db,
        "66b6116af7f3f1dcf8b6ddef",
        {"group_id": None},
    )

    assert doc is not None
    assert collection.update["$unset"] == {"group_id": ""}
    assert "group_id" not in collection.update["$set"]


@pytest.mark.asyncio
async def test_create_project_rejects_unknown_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException
    from api.models.projects import ProjectCreate
    from api.routers import projects as projects_router

    monkeypatch.setattr(projects_router, "get_db", lambda: object())

    async def get_group(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def create_project(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("未知分组不能创建项目")

    monkeypatch.setattr(projects_router.project_groups_dao, "get_group", get_group)
    monkeypatch.setattr(projects_router.projects_dao, "create_project", create_project)

    with pytest.raises(HTTPException) as raised:
        await projects_router.create_project(
            ProjectCreate(name="第一批", group_id="pg-missing")
        )

    assert raised.value.status_code == 422
    assert raised.value.detail == "项目分组不存在"


def test_project_group_models_enforce_stable_limits() -> None:
    from pydantic import ValidationError
    from api.models.projects import ProjectGroupCreate

    assert ProjectGroupCreate(name="  扫描批次  ").name == "  扫描批次  "
    with pytest.raises(ValidationError):
        ProjectGroupCreate(name="x" * 101)
    with pytest.raises(ValidationError):
        ProjectGroupCreate(name="批次", sort_order=10001)
