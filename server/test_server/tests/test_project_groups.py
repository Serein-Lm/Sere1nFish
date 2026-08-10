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


def test_batch_partition_selects_tagged_roots_and_all_descendants() -> None:
    from api.services.project_partition import select_batch_relations

    relations = [
        {
            "target_id": "root-a",
            "target_name": "甲单位",
            "batch_tags": ["第一批"],
        },
        {
            "target_id": "child-a",
            "target_name": "甲子单位",
            "root_target_id": "root-a",
            "parent_target_id": "root-a",
            "relation_depth": 1,
        },
        {
            "target_id": "grandchild-a",
            "target_name": "甲孙单位",
            "root_target_id": "root-a",
            "parent_target_id": "child-a",
            "relation_depth": 2,
        },
        {
            "target_id": "root-b",
            "target_name": "乙单位",
            "batch_tags": ["第二批"],
        },
    ]

    selected = select_batch_relations(relations, "第一批")
    assert [item["target_id"] for item in selected] == [
        "root-a",
        "child-a",
        "grandchild-a",
    ]


def test_partition_link_ids_are_project_scoped_and_stable() -> None:
    from api.dao.bidding import bidding_record_link_id
    from api.dao.source_documents import document_link_id

    assert bidding_record_link_id("project-a", "target-a", "record-a") == (
        bidding_record_link_id("project-a", "target-a", "record-a")
    )
    assert bidding_record_link_id("project-a", "target-a", "record-a") != (
        bidding_record_link_id("project-b", "target-a", "record-a")
    )
    assert document_link_id("project-a", "target-a", "document-a") != (
        document_link_id("project-b", "target-a", "document-a")
    )


def test_project_scope_query_keeps_domain_or_conditions() -> None:
    from api.dao.project_scope import project_scope_query

    query = project_scope_query(
        "project-a",
        {"$or": [{"is_new": True}, {"is_changed": True}], "score": {"$gte": 70}},
    )

    assert query == {
        "$and": [
            {
                "$or": [
                    {"project_id": "project-a"},
                    {"project_ids": "project-a"},
                ]
            },
            {
                "$or": [{"is_new": True}, {"is_changed": True}],
                "score": {"$gte": 70},
            },
        ]
    }


def test_merge_routes_intentional_target_overlap_once_per_project() -> None:
    from api.services.project_data_merge import MergeDestination, route_destinations

    destinations = [
        MergeDestination("project-first", frozenset({"target-a", "target-b"})),
        MergeDestination("project-education", frozenset({"target-b"})),
    ]

    selected = route_destinations(["target-b", "target-b"], destinations)

    assert [item.project_id for item in selected] == [
        "project-first",
        "project-education",
    ]


def test_merge_rekeys_finding_and_filters_project_targets() -> None:
    from api.services.project_data_merge import (
        MergeDestination,
        _CLONE_ADAPTERS,
        prepare_project_clone,
    )

    adapter = next(item for item in _CLONE_ADAPTERS if item.name == "findings")
    source = {
        "finding_id": "old-finding",
        "project_id": "source-project",
        "target_id": "target-a",
        "target_ids": ["target-a", "target-outside"],
        "source": "web_tagging",
        "url": "https://example.com/login",
        "channel": "email",
        "value": "contact@example.com",
        "type": "contact",
    }

    clone, identity = prepare_project_clone(
        adapter,
        source,
        MergeDestination("destination-project", frozenset({"target-a"})),
    )

    assert identity.startswith("fnd_")
    assert identity != source["finding_id"]
    assert clone["project_id"] == "destination-project"
    assert clone["target_ids"] == ["target-a"]


@pytest.mark.asyncio
async def test_partition_archive_requires_data_merge() -> None:
    from api.models.projects import ProjectBatchPartitionSpec, ProjectPartitionRequest
    from api.services.project_partition import partition_project_by_batch_tags

    request = ProjectPartitionRequest(
        group_name="批次",
        batches=[ProjectBatchPartitionSpec(batch_tag="第一批", project_name="新项目")],
        archive_source_after_merge=True,
    )

    with pytest.raises(ValueError, match="必须同时启用历史数据合并"):
        await partition_project_by_batch_tags(
            object(),
            source_project_id="source-project",
            request=request,
        )


def test_merge_project_ids_removes_source_and_preserves_destinations() -> None:
    from api.services.project_data_merge import _merge_project_ids

    merged = _merge_project_ids(
        {"project_ids": ["source", "destination-a"]},
        ["destination-a", "destination-b"],
        "source",
    )

    assert merged == ["destination-a", "destination-b"]
