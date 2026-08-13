"""Target summary aggregation tests."""

import pytest

from api.dao import findings as findings_dao
from api.dao import targets as targets_dao
from api.services.targets import (
    _dashboard_contact_from_finding,
    _merge_target_dashboard_contacts,
    _select_target_relation_page,
    _summarize_finding_counts,
    _task_collection_status,
    _target_batch_priority,
    _target_scan_coverage_summary,
    _target_summary_sort_key,
    assign_project_target_batches,
    list_project_target_summary_page,
)


def test_task_collection_status_exposes_partial_terminal_result() -> None:
    assert _task_collection_status(
        {"status": "completed", "result": {"status": "partial"}}
    ) == "partial"
    assert _task_collection_status(
        {"status": "completed", "result_status": "partial"}
    ) == "partial"
    assert _task_collection_status(
        {"status": "completed", "result": {"status": "completed"}}
    ) == "completed"


def test_target_collection_complete_requires_current_core_channel_coverage() -> None:
    relation = {
        "last_collected_at": "legacy-value",
        "scan_profile_fingerprint": "current",
        "scan_coverage": {
            channel: {
                "status": "completed",
                "profile_fingerprint": "current",
            }
            for channel in ("website", "wechat", "scholar")
        },
    }

    partial = _target_scan_coverage_summary(relation)
    assert partial["collection_complete"] is False
    assert partial["coverage_completed_count"] == 3
    assert partial["coverage_missing_channels"] == ["bidding"]

    relation["scan_coverage"]["bidding"] = {
        "status": "completed",
        "profile_fingerprint": "current",
    }
    complete = _target_scan_coverage_summary(relation)
    assert complete["collection_complete"] is True
    assert complete["coverage_completed_count"] == 4


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


def test_target_batch_tags_are_normalized_and_deduplicated() -> None:
    assert targets_dao.normalize_batch_tags(
        [" 第一批 ", "第一批", "教育   专项", ""]
    ) == ["第一批", "教育 专项"]


def test_finding_target_ids_accepts_legacy_scalar_value() -> None:
    assert findings_dao._finding_target_ids({"target_ids": "child"}) == ["child"]


def test_task_finding_scope_includes_child_tasks_and_history() -> None:
    scope = findings_dao.task_finding_scope("task.1")

    primary_pattern = scope["$or"][0]["task_id"]
    history_pattern = scope["$or"][1]["task_ids"]
    assert primary_pattern.pattern == r"^task\.1(?:_|$)"
    assert history_pattern.pattern == primary_pattern.pattern
    assert primary_pattern.match("task.1")
    assert primary_pattern.match("task.1_webdocs")
    assert not primary_pattern.match("task.10_webdocs")


def test_finding_summary_projection_excludes_heavy_evidence_fields() -> None:
    projection = findings_dao._FINDING_SUMMARY_PROJECTION

    assert projection["finding_id"] == 1
    assert projection["attention_reason"] == 1
    assert "article_context" not in projection
    assert "evidence" not in projection
    assert "evidence_refs" not in projection


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


@pytest.mark.asyncio
async def test_child_project_target_inherits_parent_batch_tags() -> None:
    class Collection:
        update = None

        async def find_one(self, _query, _projection, **_kwargs):
            return {"batch_tags": ["第一批", "教育专项"]}

        async def find_one_and_update(self, _query, update, **_kwargs):
            self.update = update
            return {"project_target_id": "pt-child"}

    class Db:
        collection = Collection()

        def __getitem__(self, _name):
            return self.collection

    db = Db()
    await targets_dao.link_project_target(
        db,
        project_id="project-1",
        target={"target_id": "child", "canonical_name": "子单位"},
        relation={
            "root_target_id": "root",
            "parent_target_id": "root",
            "relation_depth": 1,
        },
    )

    assert db.collection.update["$addToSet"]["batch_tags"] == {
        "$each": ["第一批", "教育专项"]
    }


@pytest.mark.asyncio
async def test_project_target_relation_is_only_cleared_explicitly() -> None:
    class Collection:
        updates = []

        async def find_one_and_update(self, _query, update, **_kwargs):
            self.updates.append(update)
            return {"project_target_id": "pt-1"}

    class Db:
        collection = Collection()

        def __getitem__(self, _name):
            return self.collection

    db = Db()
    target = {"target_id": "target-1", "canonical_name": "目标机构"}
    await targets_dao.link_project_target(
        db,
        project_id="project-1",
        target=target,
    )
    await targets_dao.link_project_target(
        db,
        project_id="project-1",
        target=target,
        clear_relation=True,
    )

    assert "$unset" not in db.collection.updates[0]
    assert "parent_target_id" in db.collection.updates[1]["$unset"]


@pytest.mark.asyncio
async def test_batch_assignment_propagates_only_to_selected_descendants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relations = [
        {"target_id": "root"},
        {"target_id": "child", "parent_target_id": "root"},
        {
            "target_id": "grandchild",
            "root_target_id": "root",
            "parent_target_id": "child",
            "lineage_target_ids": ["root", "child", "grandchild"],
        },
        {"target_id": "other"},
    ]
    mutation: dict = {}

    async def list_relations(*_args, **_kwargs):
        return relations

    async def update_tags(_db, **kwargs):
        mutation.update(kwargs)
        return {"matched_count": 3, "modified_count": 3}

    monkeypatch.setattr(targets_dao, "list_project_targets", list_relations)
    monkeypatch.setattr(
        targets_dao,
        "update_project_target_batch_tags",
        update_tags,
    )

    result = await assign_project_target_batches(
        object(),
        project_id="project-1",
        target_ids=["root"],
        batch_tags=["第一批"],
        include_descendants=True,
    )

    assert mutation["target_ids"] == ["child", "grandchild", "root"]
    assert "other" not in mutation["target_ids"]
    assert result["target_count"] == 3


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


def test_target_batch_priority_parses_numeric_and_chinese_level_tags() -> None:
    assert _target_batch_priority(["核心关键扫描", "第二等级"]) == {
        "batch_priority_rank": 2,
        "batch_priority_label": "第二等级",
        "is_expanded_target": False,
    }
    assert _target_batch_priority(["第12等级", "拓展目标"])[
        "batch_priority_rank"
    ] == 12
    assert _target_batch_priority(["普通批次"])["batch_priority_rank"] is None


def test_target_summary_sort_prioritizes_level_then_high_scores() -> None:
    items = [
        {
            "target_id": "completed-low",
            "target_name": "已完成低分",
            "batch_tags": ["第一等级"],
            "high_score_finding_count": 2,
            "collection_complete": True,
            "finding_count": 100,
        },
        {
            "target_id": "running-high",
            "target_name": "运行中高分",
            "batch_tags": ["第二等级"],
            "high_score_finding_count": 12,
            "collection_complete": False,
            "finding_count": 20,
        },
        {
            "target_id": "completed-high",
            "target_name": "已完成高分",
            "batch_tags": ["第二等级"],
            "high_score_finding_count": 12,
            "collection_complete": True,
            "finding_count": 18,
        },
    ]

    items.sort(key=_target_summary_sort_key)

    assert [item["target_id"] for item in items] == [
        "completed-low",
        "completed-high",
        "running-high",
    ]


def test_target_summary_sort_keeps_primary_before_expansion_within_level() -> None:
    items = [
        {
            "target_id": "expanded",
            "target_name": "拓展目标",
            "batch_tags": ["第一等级", "拓展目标"],
            "high_score_finding_count": 50,
        },
        {
            "target_id": "primary",
            "target_name": "主目标",
            "batch_tags": ["第一等级"],
            "high_score_finding_count": 1,
        },
    ]

    items.sort(key=_target_summary_sort_key)

    assert [item["target_id"] for item in items] == ["primary", "expanded"]


def test_target_dashboard_contact_uses_persisted_personal_taxonomy() -> None:
    contact = _dashboard_contact_from_finding({
        "finding_id": "finding-1",
        "type": "personal_mobile",
        "scope": "official",
        "channel": "phone",
        "subtype": "mobile_personal",
        "value": "138-0013-8000",
        "source": "bidding",
        "source_url": "https://example.cn/bid/1",
        "attention_score": 91,
    })

    assert contact is not None
    assert contact["kind"] == "personal_phone"
    assert contact["module"] == "bidding"
    assert contact["source_url"] == "https://example.cn/bid/1"
    assert _dashboard_contact_from_finding({
        "type": "business_contact",
        "scope": "official",
        "channel": "phone",
        "subtype": "hotline_landline",
        "value": "010-12345678",
    }) is None


def test_target_dashboard_contact_accepts_generic_public_mobile_number() -> None:
    contact = _dashboard_contact_from_finding({
        "finding_id": "finding-mobile",
        "type": "contact",
        "channel": "phone",
        "label": "手机号",
        "value": "+86 175-2686-8257",
        "source": "wechat_article",
        "attention_score": 92,
    })

    assert contact is not None
    assert contact["kind"] == "personal_phone"
    assert contact["module"] == "wechat"


def test_target_dashboard_contacts_deduplicate_and_merge_richer_evidence() -> None:
    contacts = _merge_target_dashboard_contacts([
        {
            "contact_id": "finding-1",
            "kind": "personal_phone",
            "channel": "phone",
            "value": "+86 138-0013-8000",
            "attention_score": 92,
            "contact_name": "",
            "source_url": "https://example.cn/1",
            "evidence_count": 2,
            "verified": True,
        },
        {
            "contact_id": "finding-2",
            "kind": "personal_phone",
            "channel": "phone",
            "value": "13800138000",
            "attention_score": 80,
            "contact_name": "张老师",
            "source_url": "",
            "evidence_count": 1,
            "verified": False,
        },
    ])

    assert len(contacts) == 1
    assert contacts[0]["contact_id"] == "finding-1"
    assert contacts[0]["contact_name"] == "张老师"
    assert contacts[0]["evidence_count"] == 3
    assert contacts[0]["verified"] is True


@pytest.mark.asyncio
async def test_project_target_dashboard_combines_findings_and_scholars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import targets as targets_service

    async def load_summary(*_args, **_kwargs):
        return {"target_id": "target-1", "finding_count": 3}

    async def load_findings(*_args, **_kwargs):
        personal_email = {
            "finding_id": "finding-email",
            "type": "personal_email",
            "channel": "email",
            "value": "author@example.edu.cn",
            "source": "web_tagging",
            "attention_score": 88,
            "source_url": "https://example.edu.cn/contact",
        }
        personal_phone = {
            "finding_id": "finding-phone",
            "type": "personal_mobile",
            "channel": "phone",
            "value": "13800138000",
            "source": "bidding",
            "attention_score": 95,
            "source_url": "https://example.edu.cn/bid",
        }
        return [personal_phone], [personal_email, personal_phone]

    async def load_scholars(*_args, **_kwargs):
        return ([{
            "doc_id": "scholar-1",
            "email": "author@example.edu.cn",
            "email_kind": "personal",
            "author_name": "李老师",
            "unit": "目标机构",
            "article_url": "https://doi.org/10.1/example",
            "unit_verified": True,
            "is_corresponding": True,
        }], 1)

    monkeypatch.setattr(targets_service, "get_project_target_summary", load_summary)
    monkeypatch.setattr(
        findings_dao,
        "query_target_dashboard_findings",
        load_findings,
    )
    monkeypatch.setattr(targets_service.scholar_dao, "query_contacts", load_scholars)

    dashboard = await targets_service.get_project_target_dashboard(
        object(),
        project_id="project-1",
        target_id="target-1",
    )

    assert dashboard is not None
    assert dashboard["contact_counts"] == {
        "personal_phone": 1,
        "personal_email": 1,
    }
    assert dashboard["personal_emails"][0]["contact_name"] == "李老师"
    assert dashboard["top_findings"][0]["module"] == "bidding"


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


def test_target_page_paginates_roots_by_level_before_high_score() -> None:
    relations = [
        {
            "project_target_id": "pt-first",
            "target_id": "first",
            "target_name": "第一等级机构",
            "batch_tags": ["第一等级"],
            "relation_depth": 0,
        },
        {
            "project_target_id": "pt-second",
            "target_id": "second",
            "target_name": "第二等级机构",
            "batch_tags": ["第二等级"],
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
            "first": {"high_score_finding_count": 1},
            "second": {"high_score_finding_count": 100},
        },
    )

    assert [item["target_id"] for item in result["relations"]] == ["first"]


def test_target_page_filters_business_batch_and_keeps_branch_context() -> None:
    relations = [
        {
            "project_target_id": "pt-a",
            "target_id": "root-a",
            "target_name": "第一批机构",
            "batch_tags": ["第一批"],
            "relation_depth": 0,
        },
        {
            "project_target_id": "pt-a-child",
            "target_id": "child-a",
            "target_name": "第一批子单位",
            "root_target_id": "root-a",
            "parent_target_id": "root-a",
            "relation_depth": 1,
        },
        {
            "project_target_id": "pt-b",
            "target_id": "root-b",
            "target_name": "第二批机构",
            "batch_tags": ["第二批"],
            "relation_depth": 0,
        },
    ]

    result = _select_target_relation_page(
        relations,
        {},
        query="",
        batch_tag="第一批",
        page=1,
        page_size=10,
        root_stats={},
    )

    assert result["root_total"] == 1
    assert result["project_total"] == 2
    assert result["all_root_total"] == 2
    assert result["all_project_total"] == 3
    assert [item["target_id"] for item in result["relations"]] == ["root-a"]
    assert result["descendant_counts"]["root-a"] == 1


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
            "scan_aliases": ["教管中心"],
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
