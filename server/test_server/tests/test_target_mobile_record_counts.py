"""Project cards must count the same historical mobile evidence as their lists."""

from __future__ import annotations

import pytest

from api.db.collections import MOBILE_COLLECT_RECORDS_COLLECTION
from api.services.targets import list_project_target_summaries


def matches(document: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$or":
            if not any(matches(document, child) for child in expected):
                return False
        elif isinstance(expected, dict):
            if "$exists" in expected and (key in document) != expected["$exists"]:
                return False
            if "$in" in expected and document.get(key) not in expected["$in"]:
                return False
            if "$nin" in expected and document.get(key) in expected["$nin"]:
                return False
        elif isinstance(document.get(key), list):
            if expected not in document[key]:
                return False
        elif document.get(key) != expected:
            return False
    return True


class Cursor:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    async def to_list(self, _length: int | None) -> list:
        return self.rows

    def sort(self, *_args, **_kwargs) -> "Cursor":
        return self

    def __aiter__(self):
        async def _iterate():
            for row in self.rows:
                yield row

        return _iterate()


class Collection:
    def __init__(self, records: list) -> None:
        self.records = records

    def aggregate(self, pipeline: list) -> Cursor:
        if not self.records:
            return Cursor([])
        rows = [record for record in self.records if matches(record, pipeline[0]["$match"])]
        count_field = next(key for key in pipeline[1]["$group"] if key != "_id")
        return Cursor([{"_id": "target", count_field: len(rows)}])

    def find(self, *_args, **_kwargs) -> Cursor:
        return Cursor([])


class Database:
    def __init__(self, records: list) -> None:
        self.records = records

    def __getitem__(self, name: str) -> Collection:
        return Collection(self.records if name == MOBILE_COLLECT_RECORDS_COLLECTION else [])


@pytest.mark.asyncio
async def test_summary_counts_shared_legacy_records_without_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_counts(*_args, **_kwargs):
        return {}

    async def no_findings(*_args, **_kwargs):
        return []

    monkeypatch.setattr("api.dao.findings.aggregate_target_finding_counts", no_findings)
    monkeypatch.setattr("api.services.website_records.count_project_website_records_by_target", no_counts)
    monkeypatch.setattr("api.services.bidding_records.count_project_bidding_records_by_target", no_counts)
    monkeypatch.setattr("api.dao.scholar_contact.count_contacts_by_target", no_counts)
    records = [
        {"project_id": "original", "project_ids": ["destination"], "target_id": "target"},
        {"project_id": "destination", "target_id": "target", "source_document_id": "doc-1"},
        {"project_id": "destination", "target_id": "target", "superseded_by_record_id": "record-new"},
        {"project_id": "foreign", "target_id": "target"},
    ]
    summaries = await list_project_target_summaries(
        Database(records), "destination",
        relations=[{"target_id": "target", "target_name": "示例单位"}], target_relationships=[],
    )
    assert summaries[0]["record_count"] == 2
    assert summaries[0]["wechat_count"] == 2
