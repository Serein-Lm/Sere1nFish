"""Target summary aggregation tests."""

from api.services.targets import (
    _select_target_relation_page,
    _summarize_finding_counts,
    _target_summary_sort_key,
)


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


def test_target_page_paginates_roots_by_high_score() -> None:
    relations = [
        {
            "project_target_id": "pt-a",
            "target_id": "root-a",
            "target_name": "机构 A",
            "relation_depth": 0,
        },
        {
            "project_target_id": "pt-a-child",
            "target_id": "child-a",
            "target_name": "机构 A 子单位",
            "root_target_id": "root-a",
            "parent_target_id": "root-a",
            "relation_depth": 1,
        },
        {
            "project_target_id": "pt-b",
            "target_id": "root-b",
            "target_name": "机构 B",
            "relation_depth": 0,
        },
    ]

    result = _select_target_relation_page(
        relations,
        {},
        query="",
        page=1,
        page_size=1,
        root_stats={
            "root-a": {"high_score_finding_count": 2},
            "root-b": {"high_score_finding_count": 8},
        },
    )

    assert result["root_total"] == 2
    assert result["project_total"] == 3
    assert [item["target_id"] for item in result["relations"]] == ["root-b"]

    second_page = _select_target_relation_page(
        relations,
        {},
        query="",
        page=2,
        page_size=1,
        root_stats={
            "root-a": {"high_score_finding_count": 2},
            "root-b": {"high_score_finding_count": 8},
        },
    )
    assert [item["target_id"] for item in second_page["relations"]] == ["root-a"]
    assert second_page["child_counts"]["root-a"] == 1
    assert second_page["descendant_counts"]["root-a"] == 1


def test_target_search_uses_aliases_and_returns_matching_hierarchy() -> None:
    relations = [
        {
            "project_target_id": "pt-root",
            "target_id": "root",
            "target_name": "教育主管机构",
            "relation_depth": 0,
        },
        {
            "project_target_id": "pt-child",
            "target_id": "child",
            "target_name": "教育管理信息中心",
            "root_target_id": "root",
            "parent_target_id": "root",
            "relation_depth": 1,
        },
        {
            "project_target_id": "pt-grandchild",
            "target_id": "grandchild",
            "target_name": "直属数据服务单位",
            "root_target_id": "root",
            "parent_target_id": "child",
            "relation_depth": 2,
        },
        {
            "project_target_id": "pt-other",
            "target_id": "other",
            "target_name": "无关机构",
            "relation_depth": 0,
        },
    ]
    targets = {
        "child": {
            "target_id": "child",
            "canonical_name": "教育管理信息中心",
            "aliases": ["教管中心"],
        }
    }

    result = _select_target_relation_page(
        relations,
        targets,
        query="教管中心",
        page=1,
        page_size=10,
        root_stats={},
    )

    assert result["matched_target_ids"] == ["child"]
    assert [item["target_id"] for item in result["relations"]] == [
        "root",
        "child",
    ]
    assert result["expanded_project_target_ids"] == ["pt-root"]


def test_target_search_does_not_expand_an_entire_matching_root() -> None:
    relations = [
        {
            "project_target_id": "pt-root",
            "target_id": "root",
            "target_name": "教育主管机构",
            "relation_depth": 0,
        },
        {
            "project_target_id": "pt-child",
            "target_id": "child",
            "target_name": "直属服务单位",
            "root_target_id": "root",
            "parent_target_id": "root",
            "relation_depth": 1,
        },
    ]

    result = _select_target_relation_page(
        relations,
        {},
        query="教育主管机构",
        page=1,
        page_size=10,
        root_stats={},
    )

    assert result["matched_target_ids"] == ["root"]
    assert [item["target_id"] for item in result["relations"]] == ["root"]
