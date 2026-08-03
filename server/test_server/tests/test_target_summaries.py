"""Target summary aggregation tests."""

from api.services.targets import _summarize_finding_counts, _target_summary_sort_key


def test_summarize_finding_counts_groups_high_scores_by_frontend_module() -> None:
    rows = [
        {
            "_id": {"target_id": "target-1", "source": "web_tagging"},
            "finding_count": 5,
            "high_score_count": 2,
        },
        {
            "_id": {"target_id": "target-1", "source": "wechat_article"},
            "finding_count": 3,
            "high_score_count": 1,
        },
        {
            "_id": {"target_id": "target-1", "source": "mobile"},
            "finding_count": 2,
            "high_score_count": 2,
        },
        {
            "_id": {"target_id": "target-2", "source": "xhs"},
            "finding_count": 4,
            "high_score_count": 3,
        },
        {
            "_id": {"target_id": "", "source": "bidding"},
            "finding_count": 9,
            "high_score_count": 9,
        },
    ]

    result = _summarize_finding_counts(rows)

    assert result["target-1"] == {
        "finding_count": 10,
        "high_score_finding_count": 5,
        "high_score_by_source": {
            "website": 2,
            "xiaohongshu": 0,
            "wechat": 1,
            "bidding": 0,
            "scholars": 0,
            "other": 2,
        },
    }
    assert result["target-2"]["high_score_by_source"]["xiaohongshu"] == 3
    assert "" not in result


def test_target_summary_sort_prioritizes_high_scores_then_completion() -> None:
    items = [
        {
            "target_id": "completed-low",
            "target_name": "已完成低分",
            "high_score_finding_count": 2,
            "collection_complete": True,
            "finding_count": 100,
        },
        {
            "target_id": "running-high",
            "target_name": "运行中高分",
            "high_score_finding_count": 12,
            "collection_complete": False,
            "finding_count": 20,
        },
        {
            "target_id": "completed-high",
            "target_name": "已完成高分",
            "high_score_finding_count": 12,
            "collection_complete": True,
            "finding_count": 18,
        },
    ]

    items.sort(key=_target_summary_sort_key)

    assert [item["target_id"] for item in items] == [
        "completed-high",
        "running-high",
        "completed-low",
    ]
