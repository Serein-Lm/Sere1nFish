"""Target summary aggregation tests."""

import pytest

from api.dao import findings as findings_dao
from api.dao import targets as targets_dao
from api.services.targets import (
    _select_target_relation_page,
    _summarize_finding_counts,
    _target_summary_sort_key,
    list_project_target_summary_page,
)


def test_target_relation_view_calculates_effective_root_ownership() -> None:
    relations = {
        "root": {
            "target_id": "root",
            "target_name": "主目标集团",
        },
        "child": {
            "target_id": "child",
            "target_name": "一级单位",
            "root_target_id": "root",
            "parent_target_id": "root",
            "relation_depth": 1,
            "ownership_percent": 80,
            "lineage_target_ids": ["root", "child"],
        },
        "grandchild": {
            "target_id": "grandchild",
            "target_name": "二级单位",
            "root_target_id": "root",
            "root_target_name": "主目标集团",
            "parent_target_id": "child",
            "parent_target_name": "一级单位",
            "relation_type": "controlled_entity",
            "relation_depth": 2,
            "ownership_percent": 75,
            "lineage_target_ids": ["root", "child", "grandchild"],
            "lineage_target_names": ["主目标集团", "一级单位", "二级单位"],
        },
    }

    result = targets_dao.build_project_target_relation_view(
        relations["grandchild"],
        relations,
    )

    assert result["root_target_name"] == "主目标集团"
    assert result["ownership_percent"] == 75
    assert result["effective_ownership_percent"] == 60
    assert result["control_kind"] == "controlled"
    assert result["lineage_target_names"] == ["主目标集团", "一级单位", "二级单位"]


def test_target_relation_view_keeps_explicit_wholly_owned_without_ratio() -> None:
    relations = {
        "root": {"target_id": "root", "target_name": "主目标集团"},
        "child": {
            "target_id": "child",
            "target_name": "全资单位",
            "root_target_id": "root",
            "parent_target_id": "root",
            "relation_type": "wholly_owned_direct_investment",
            "relation_depth": 1,
            "lineage_target_ids": ["root", "child"],
        },
    }

    result = targets_dao.build_project_target_relation_view(
        relations["child"],
        relations,
    )

    assert result["effective_ownership_percent"] is None
    assert result["control_kind"] == "wholly_owned"


def test_finding_target_ids_accepts_legacy_scalar_value() -> None:
    assert findings_dao._finding_target_ids({"target_ids": "child"}) == ["child"]


@pytest.mark.asyncio
async def test_finding_read_model_attaches_current_target_relation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def load_relations(_db, *, project_id, target_ids):
        assert project_id == "project-1"
        assert target_ids == ["child"]
        return {
            "child": {
                "target_id": "child",
                "target_name": "子单位",
                "root_target_id": "root",
                "root_target_name": "主目标",
                "relation_depth": 1,
                "effective_ownership_percent": 100,
                "control_kind": "wholly_owned",
                "is_primary": False,
            }
        }

    monkeypatch.setattr(
        targets_dao,
        "get_project_target_relation_views",
        load_relations,
    )

    result = await findings_dao.enrich_with_target_relations(
        object(),
        [{
            "finding_id": "finding-1",
            "project_id": "project-1",
            "target_id": "child",
        }],
    )

    assert result[0]["target_name"] == "子单位"
    assert result[0]["target_relation"]["root_target_name"] == "主目标"
    assert result[0]["target_relations"] == [result[0]["target_relation"]]


@pytest.mark.asyncio
async def test_project_target_can_replace_authoritative_search_terms() -> None:
    class Collection:
        update = None

        async def find_one_and_update(self, _query, update, **_kwargs):
            self.update = update
            return {"project_target_id": "pt-1"}

    class Db:
        collection = Collection()

        def __getitem__(self, _name):
            return self.collection

    db = Db()
    await targets_dao.link_project_target(
        db,
        project_id="project-1",
        target={
            "target_id": "target-1",
            "canonical_name": "目标机构",
        },
        search_terms=["目标机构", "目标机构"],
        search_terms_by_channel={
            "web": ["目标机构 官网", "目标机构 官网"],
        },
        objectives=["深研"],
        task_def_id="task-1",
        replace_search_terms=True,
    )

    assert db.collection.update["$set"]["search_terms"] == ["目标机构"]
    assert db.collection.update["$set"]["search_terms_by_channel"] == {
        "web": ["目标机构 官网"],
    }
    additions = db.collection.update["$addToSet"]
    assert "search_terms" not in additions
    assert all(not key.startswith("search_terms_by_channel") for key in additions)


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


def test_target_search_ignores_untrusted_legacy_aliases() -> None:
    relations = [{
        "project_target_id": "pt-health",
        "target_id": "health",
        "target_name": "医疗管理服务指导中心",
        "relation_depth": 0,
    }]
    targets = {
        "health": {
            "target_id": "health",
            "canonical_name": "医疗管理服务指导中心",
            "identity_aliases": ["医管中心"],
            "aliases": ["医管中心", "电子税务"],
        }
    }

    trusted = _select_target_relation_page(
        relations,
        targets,
        query="医管中心",
        page=1,
        page_size=10,
        root_stats={},
    )
    polluted = _select_target_relation_page(
        relations,
        targets,
        query="电子税务",
        page=1,
        page_size=10,
        root_stats={},
    )

    assert trusted["matched_target_ids"] == ["health"]
    assert polluted["matched_target_ids"] == []


@pytest.mark.asyncio
async def test_target_summary_page_loads_trusted_identity_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relations = [{
        "project_target_id": "pt-health",
        "target_id": "health",
        "target_name": "医疗管理服务指导中心",
        "relation_depth": 0,
    }]
    target_doc = {
        "target_id": "health",
        "canonical_name": "医疗管理服务指导中心",
        "identity_aliases": ["医管中心"],
        "aliases": ["医管中心", "电子税务"],
    }

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        async def to_list(self, _length):
            return self.rows

    class TargetCollection:
        def find(self, _query, projection):
            projected = {
                key: value
                for key, value in target_doc.items()
                if projection.get(key)
            }
            return Cursor([projected])

    class FindingCollection:
        def aggregate(self, _pipeline):
            return Cursor([])

    class Db:
        def __getitem__(self, name):
            if name == "targets":
                return TargetCollection()
            return FindingCollection()

    async def list_relations(*_args, **_kwargs):
        return relations

    async def list_summaries(*_args, **_kwargs):
        return []

    monkeypatch.setattr(targets_dao, "list_project_targets", list_relations)
    monkeypatch.setattr(
        "api.services.targets.list_project_target_summaries",
        list_summaries,
    )

    result = await list_project_target_summary_page(
        Db(),
        "project-1",
        query="电子税务",
    )

    assert result["matched_total"] == 0
    assert result["matched_target_ids"] == []


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
