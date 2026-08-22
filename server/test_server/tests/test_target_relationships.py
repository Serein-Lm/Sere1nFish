from __future__ import annotations

from typing import Any

import pytest

from api.dao.target_relationships import (
    build_target_relationship_views,
    normalize_relationship,
    relationship_id,
)


def test_relationship_identity_is_project_scoped_and_directional() -> None:
    first = relationship_id(
        "project-1",
        "target-child",
        "target-parent",
        "parent_organization",
    )
    repeated = relationship_id(
        "project-1",
        "target-child",
        "target-parent",
        "PARENT_ORGANIZATION",
    )
    reversed_edge = relationship_id(
        "project-1",
        "target-parent",
        "target-child",
        "parent_organization",
    )

    assert first == repeated
    assert first != reversed_edge


def test_relationship_rejects_self_reference() -> None:
    with pytest.raises(ValueError, match="自身"):
        normalize_relationship(
            {
                "related_target_id": "target-1",
                "related_target_name": "同一机构",
                "relation_type": "parent_organization",
                "direction": "upstream",
            },
            project_id="project-1",
            subject_target_id="target-1",
            subject_target_name="同一机构",
            task_id="task-1",
            research_id="research-1",
        )


def test_relationship_views_expose_supervisor_without_inverting_tree() -> None:
    views = build_target_relationship_views(
        [
            {
                "subject_target_id": "target-center",
                "subject_target_name": "教育部教育管理信息中心",
                "related_target_id": "target-ministry",
                "related_target_name": "中华人民共和国教育部",
                "relation_type": "parent_organization",
                "direction": "upstream",
                "summary": "教育部直属事业单位",
                "confidence": 1.0,
                "source_urls": ["https://www.moe.gov.cn/unit"],
                "research_ids": ["research-1"],
            }
        ]
    )

    assert views["target-center"]["supervising_units"][0] == {
        "target_id": "target-ministry",
        "target_name": "中华人民共和国教育部",
        "relation_type": "parent_organization",
        "summary": "教育部直属事业单位",
        "confidence": 1.0,
        "source_urls": ["https://www.moe.gov.cn/unit"],
        "research_ids": ["research-1"],
    }
    assert views["target-ministry"]["supervised_units"][0]["target_id"] == (
        "target-center"
    )


def test_relationship_views_expose_lateral_units_without_child_semantics() -> None:
    views = build_target_relationship_views(
        [
            {
                "subject_target_id": "target-center",
                "subject_target_name": "教育部教育管理信息中心",
                "related_target_id": "target-peer",
                "related_target_name": "教育部直属同级单位",
                "relation_type": "affiliated_unit",
                "direction": "lateral",
                "summary": "同属教育部直属事业单位",
                "confidence": 0.9,
                "source_urls": ["https://www.moe.gov.cn/unit"],
                "research_ids": ["research-1"],
            }
        ]
    )

    assert views["target-center"]["related_units"][0]["target_id"] == "target-peer"
    assert views["target-peer"]["related_units"][0]["target_id"] == "target-center"


@pytest.mark.asyncio
async def test_clone_project_relationships_rekeys_and_limits_both_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import target_relationships
    from api.db.collections import TARGET_RELATIONSHIPS_COLLECTION

    captured: dict[str, Any] = {}

    class Cursor:
        async def to_list(self, _length: int | None) -> list[dict[str, Any]]:
            return [
                {
                    "relationship_id": "source-edge",
                    "project_id": "source-project",
                    "subject_target_id": "target-airport",
                    "subject_target_name": "支线机场",
                    "related_target_id": "target-group",
                    "related_target_name": "机场集团",
                    "relation_type": "parent_organization",
                    "direction": "upstream",
                    "summary": "集团成员机场",
                    "confidence": 1.0,
                    "source": "target_research",
                    "source_urls": ["https://example.test/members"],
                    "task_ids": ["task-1"],
                    "research_ids": ["research-1"],
                }
            ]

    class Collection:
        def find(
            self,
            query: dict[str, Any],
            _projection: dict[str, int],
        ) -> Cursor:
            captured["query"] = query
            return Cursor()

        async def bulk_write(
            self,
            operations: list[dict[str, Any]],
            *,
            ordered: bool,
        ) -> None:
            captured["operations"] = operations
            captured["ordered"] = ordered

    class Database:
        def __getitem__(self, name: str) -> Collection:
            assert name == TARGET_RELATIONSHIPS_COLLECTION
            return Collection()

    def update_one(
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool,
    ) -> dict[str, Any]:
        return {"query": query, "update": update, "upsert": upsert}

    monkeypatch.setattr(target_relationships, "UpdateOne", update_one)

    count = await target_relationships.clone_project_relationships(
        Database(),  # type: ignore[arg-type]
        source_project_id="source-project",
        destination_project_id="destination-project",
        target_ids=["target-group", "target-airport"],
    )

    assert count == 1
    assert captured["query"] == {
        "project_id": "source-project",
        "active": {"$ne": False},
        "subject_target_id": {"$in": ["target-group", "target-airport"]},
        "related_target_id": {"$in": ["target-group", "target-airport"]},
    }
    operation = captured["operations"][0]
    expected_id = relationship_id(
        "destination-project",
        "target-airport",
        "target-group",
        "parent_organization",
    )
    assert operation["query"] == {"relationship_id": expected_id}
    assert operation["update"]["$set"]["project_id"] == "destination-project"
    assert operation["update"]["$addToSet"]["merged_from_project_ids"] == (
        "source-project"
    )
    assert operation["update"]["$addToSet"]["source_urls"] == {
        "$each": ["https://example.test/members"]
    }
    assert operation["upsert"] is True
    assert captured["ordered"] is False
