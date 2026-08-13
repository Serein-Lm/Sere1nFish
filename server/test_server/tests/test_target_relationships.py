from __future__ import annotations

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
