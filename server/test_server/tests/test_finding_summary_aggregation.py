"""Regression tests for lightweight Finding dashboard aggregations."""

from typing import Any

import pytest

from api.dao import findings as findings_dao
from api.db.collections import FINDINGS_COLLECTION


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def to_list(self, _length: int | None) -> list[dict[str, Any]]:
        return self.rows


class _Collection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.pipeline: list[dict[str, Any]] = []

    def aggregate(self, pipeline: list[dict[str, Any]]) -> _Cursor:
        self.pipeline = pipeline
        return _Cursor(self.rows)


class _Db:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.collection = _Collection(rows)

    def __getitem__(self, name: str) -> _Collection:
        assert name == FINDINGS_COLLECTION
        return self.collection


def _logical_group_stage(pipeline: list[dict[str, Any]]) -> dict[str, Any]:
    return next(
        stage
        for stage in pipeline
        if "$group" in stage
        and "$top" in stage["$group"].get("representative", {})
    )


@pytest.mark.asyncio
async def test_target_finding_counts_use_minimal_top_aggregation() -> None:
    rows = [
        {
            "_id": {"target_id": "target-1", "source": "web_tagging"},
            "finding_count": 8,
            "high_score_count": 3,
        }
    ]
    db = _Db(rows)

    result = await findings_dao.aggregate_target_finding_counts(
        db,  # type: ignore[arg-type]
        project_id="project-1",
        target_ids=["target-1", "target-1"],
    )

    assert result == rows
    assert not any("$sort" in stage for stage in db.collection.pipeline)
    match = db.collection.pipeline[0]["$match"]
    assert match["target_id"]["$in"] == ["target-1"]
    top = _logical_group_stage(db.collection.pipeline)["$group"][
        "representative"
    ]["$top"]
    assert top["output"] == {
        "target_id": "$target_id",
        "source": {"$ifNull": ["$source", ""]},
    }
    assert "$$ROOT" not in str(top)


@pytest.mark.asyncio
async def test_findings_summary_keeps_response_shape_without_full_documents() -> None:
    db = _Db(
        [
            {
                "total": [{"count": 9}],
                "by_source": [
                    {"_id": "web_tagging", "count": 7},
                    {"_id": "", "count": 2},
                ],
                "by_type": [{"_id": "contact", "count": 9}],
                "score_high": [{"count": 4}],
                "score_medium": [{"count": 3}],
                "score_low": [{"count": 2}],
            }
        ]
    )

    result = await findings_dao.get_findings_summary(
        db,  # type: ignore[arg-type]
        "project-1",
    )

    assert result == {
        "total": 9,
        "by_source": {"web_tagging": 7},
        "by_type": {"contact": 9},
        "score_distribution": {"high": 4, "medium": 3, "low": 2},
    }
    assert not any("$sort" in stage for stage in db.collection.pipeline)
    top = _logical_group_stage(db.collection.pipeline)["$group"][
        "representative"
    ]["$top"]
    assert top["output"] == {
        "source": {"$ifNull": ["$source", ""]},
        "type": {"$ifNull": ["$type", ""]},
    }
    assert "$$ROOT" not in str(top)
