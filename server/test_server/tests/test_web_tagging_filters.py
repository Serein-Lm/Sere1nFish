from __future__ import annotations

import pytest

from api.dao import web_tagging


class _AsyncDocs:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def __aiter__(self):
        async def _iterate():
            for doc in self._docs:
                yield doc

        return _iterate()


class _Collection:
    def __init__(self) -> None:
        self.count_query: dict | None = None
        self.pipeline: list[dict] | None = None

    async def count_documents(self, query: dict) -> int:
        self.count_query = query
        return 1

    def aggregate(self, pipeline: list[dict]) -> _AsyncDocs:
        self.pipeline = pipeline
        return _AsyncDocs([])


class _Db:
    def __init__(self) -> None:
        self.collection = _Collection()

    def __getitem__(self, _name: str) -> _Collection:
        return self.collection


@pytest.mark.asyncio
async def test_web_tagging_source_filter_keeps_legacy_records() -> None:
    db = _Db()

    items, total = await web_tagging.list_web_tagging_results(
        db,
        project_id="6a5a1f59518d9e71e1887ab0",
        source="web_tagging",
    )

    assert items == []
    assert total == 1
    assert db.collection.count_query is not None
    assert db.collection.count_query["$or"] == [
        {"source": "web_tagging"},
        {"source": {"$exists": False}},
        {"source": None},
    ]
    assert db.collection.pipeline is not None
    assert db.collection.pipeline[0]["$match"] == db.collection.count_query


@pytest.mark.asyncio
async def test_web_tagging_source_filter_can_select_other_sources() -> None:
    db = _Db()

    await web_tagging.list_web_tagging_results(
        db,
        project_id="6a5a1f59518d9e71e1887ab0",
        source="bidding",
    )

    assert db.collection.count_query is not None
    assert db.collection.count_query["source"] == "bidding"
