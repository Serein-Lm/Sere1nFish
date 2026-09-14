from __future__ import annotations

from typing import Any

import pytest

from api.dao import targets as targets_dao
from api.services import bidding_records


RELATIONS = [
    {"target_id": "nhc", "target_name": "中华人民共和国国家卫生健康委员会", "short_names": ["国家卫健委", "卫健委"]},
    {"target_id": "stats", "target_name": "国家卫生健康委统计信息中心", "parent_target_id": "nhc", "root_target_id": "nhc"},
    {"target_id": "other", "target_name": "示例大学"},
]


def announcement(record_id: str, purchaser: str, **extra: Any) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "purchaser": purchaser,
        "title": "国家卫健委有关规定中的采购要求",
        "content_preview": "遵守中华人民共和国国家卫生健康委员会规定。",
        "published_on": "2026-09-10",
        "target_ids": ["nhc"],
        "query_names": ["中华人民共和国国家卫生健康委员会"],
        "contact_candidates": [{"channel": "phone", "value": "010-12345678", "context": f"采购人：{purchaser} 联系电话：010-12345678"}],
        **extra,
    }


class EmptyFindings:
    def __getitem__(self, _name: str) -> "EmptyFindings":
        return self

    def find(self, *_args: Any, **_kwargs: Any) -> "EmptyFindings":
        return self

    async def to_list(self, _length: int | None) -> list:
        return []


@pytest.fixture
def records(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows = [
        announcement("coal", "山西保利合盛煤业有限公司"),
        announcement("nhc-own", "中华人民共和国国家卫生健康委员会"),
        announcement("stats-own", "国家卫生健康委统计信息中心"),
        announcement("local-health", "某市卫生健康委员会"),
        announcement("university", "示例大学"),
        announcement("no-party", "", detail_url="https://www.nhc.gov.cn/bid/1"),
    ]

    async def query(*_args: Any, **_kwargs: Any):
        return rows, len(rows)

    async def relations(*_args: Any, **_kwargs: Any):
        return RELATIONS

    monkeypatch.setattr(bidding_records.bidding_dao, "query_records", query)
    monkeypatch.setattr(bidding_records.bidding_dao, "query_project_records", query, raising=False)
    monkeypatch.setattr(targets_dao, "list_project_targets", relations)
    return rows


@pytest.mark.asyncio
async def test_list_rejects_incidental_mentions_before_pagination(records: list) -> None:
    items, total = await bidding_records.list_project_bidding_records(
        EmptyFindings(), project_id="project", target_id="nhc", limit=1, skip=1,
    )
    assert total == 2
    assert [item["record_id"] for item in items] == ["stats-own"]
    assert items[0]["target_ids"] == ["stats"]


@pytest.mark.asyncio
async def test_child_scope_uses_party_instead_of_global_discovery_ids(records: list) -> None:
    items, total = await bidding_records.list_project_bidding_records(
        EmptyFindings(), project_id="project", target_id="stats",
    )
    assert total == 1
    assert [item["record_id"] for item in items] == ["stats-own"]


@pytest.mark.asyncio
async def test_unassociated_target_has_no_visible_records(records: list) -> None:
    items, total = await bidding_records.list_project_bidding_records(
        EmptyFindings(), project_id="project", target_id="foreign-target",
    )
    assert (items, total) == ([], 0)


@pytest.mark.asyncio
async def test_project_list_and_target_counts_share_ownership_policy(records: list) -> None:
    items, total = await bidding_records.list_project_bidding_records(EmptyFindings(), project_id="project")
    counts = await bidding_records.count_project_bidding_records_by_target(
        EmptyFindings(), project_id="project", target_ids=["nhc", "stats", "other"],
    )
    assert total == 3
    assert {item["record_id"] for item in items} == {"nhc-own", "stats-own", "university"}
    assert counts == {"nhc": 2, "stats": 1, "other": 1}


@pytest.mark.parametrize("record,expected", [
    ({"purchaser": "国家卫健委"}, ["nhc"]),
    ({"purchaser": "某市卫生健康委员会"}, []),
    ({"purchaser": "国家卫生健康委统计信息中心"}, ["stats"]),
    ({"purchaser": "其他单位", "agency": "中华人民共和国国家卫生健康委员会"}, ["nhc"]),
    ({"winner": ["中华人民共和国国家卫生健康委员会", "其他单位"]}, ["nhc"]),
    ({"purchaser": "采购人：中华人民共和国国家卫生健康委员会"}, ["nhc"]),
    ({"title": "中华人民共和国国家卫生健康委员会采购公告"}, []),
    ({"summary": "采购人：中华人民共和国国家卫生健康委员会"}, []),
    ({"purchaser": "国家卫健委监督下的其他单位"}, []),
])
def test_ownership_requires_explicit_whole_party_name(record: dict, expected: list[str]) -> None:
    from api.services.bidding_ownership import BiddingOwnershipScope

    scope = BiddingOwnershipScope(RELATIONS)
    assert scope.owner_ids(record) == expected


def test_legacy_search_aliases_do_not_establish_party_ownership() -> None:
    from api.services.bidding_ownership import BiddingOwnershipScope

    scope = BiddingOwnershipScope([{
        "target_id": "medical", "target_name": "国家卫生健康委医疗管理服务指导中心",
        "aliases": ["电子税务局"], "scan_aliases": ["电子税务局"],
        "search_terms_by_channel": {"bidding": ["电子税务局"]},
    }])
    assert scope.owner_ids({"purchaser": "电子税务局"}) == []


def test_only_stored_descendants_join_target_scope() -> None:
    from api.services.bidding_ownership import BiddingOwnershipScope

    scope = BiddingOwnershipScope(RELATIONS + [{
        "target_id": "upstream", "target_name": "上级机构",
        "direction": "upstream", "related_target_id": "nhc",
    }])
    assert scope.target_ids("nhc") == {"nhc", "stats"}
    assert scope.target_ids("stats") == {"stats"}


@pytest.mark.asyncio
async def test_project_dao_uses_explicit_project_links() -> None:
    from api.dao import bidding
    from api.db.collections import BIDDING_RECORD_LINKS_COLLECTION, BIDDING_RECORDS_COLLECTION

    seen = {}

    class Collection:
        async def distinct(self, field: str, query: dict):
            seen["links"] = (field, query)
            return ["linked-record"]

        async def count_documents(self, query: dict):
            seen["count"] = query
            return 1

        def find(self, query: dict, _projection: dict):
            seen["records"] = query
            return self

        def sort(self, *_args):
            return self

        def skip(self, *_args):
            return self

        def limit(self, *_args):
            return self

        def __aiter__(self):
            async def iterator():
                yield {"record_id": "linked-record"}
            return iterator()

    db = {BIDDING_RECORD_LINKS_COLLECTION: Collection(), BIDDING_RECORDS_COLLECTION: Collection()}
    rows, total = await bidding.query_project_records(db, project_id="project")
    assert total == len(rows) == 1
    assert seen["links"] == ("record_id", {"project_id": "project"})
    assert seen["records"] == {"record_id": {"$in": ["linked-record"]}}
