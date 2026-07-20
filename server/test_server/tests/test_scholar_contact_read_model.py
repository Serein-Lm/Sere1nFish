from __future__ import annotations

from typing import Any

import pytest

from api.dao import scholar_contact as scholar_dao


def test_scholar_article_url_requires_a_public_source() -> None:
    assert scholar_dao.scholar_article_url({"doi": "10.1000/example"}) == (
        "https://doi.org/10.1000/example"
    )
    assert scholar_dao.scholar_article_url({"pmcid": "pmc12345"}) == (
        "https://europepmc.org/article/PMC/12345"
    )
    assert scholar_dao.scholar_article_url(
        {"landing_page": "https://example.org/article/1"}
    ) == "https://example.org/article/1"
    assert scholar_dao.scholar_article_url({"article_id": "synthetic"}) == ""


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def to_list(
        self,
        _length: int | None = None,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        return list(self.rows)


class _Collection:
    def __init__(self) -> None:
        self.count_query: dict[str, Any] = {}
        self.pipelines: list[list[dict[str, Any]]] = []

    def find(self, *_args: Any, **_kwargs: Any) -> _Cursor:
        return _Cursor([])

    def aggregate(self, pipeline: list[dict[str, Any]]) -> _Cursor:
        self.pipelines.append(pipeline)
        return _Cursor([])

    async def count_documents(self, query: dict[str, Any]) -> int:
        self.count_query = query
        return 0


class _Db:
    def __init__(self) -> None:
        self.contacts = _Collection()
        self.articles = _Collection()

    def __getitem__(self, name: str) -> _Collection:
        if name == "scholar_contacts":
            return self.contacts
        if name == "scholar_articles":
            return self.articles
        raise KeyError(name)


@pytest.mark.asyncio
async def test_query_contacts_hides_orphans_before_pagination() -> None:
    db = _Db()

    items, total = await scholar_dao.query_contacts(
        db,  # type: ignore[arg-type]
        "project-1",
        limit=20,
    )

    assert items == []
    assert total == 0
    assert db.contacts.count_query["email"] == {"$nin": [None, ""]}
    assert db.contacts.count_query["article_url"] == {
        "$regex": r"^https?://",
        "$options": "i",
    }
    pipeline = db.contacts.pipelines[0]
    limit_index = pipeline.index({"$limit": 20})
    lookup_index = next(
        index for index, stage in enumerate(pipeline) if "$lookup" in stage
    )
    assert limit_index < lookup_index


@pytest.mark.asyncio
async def test_upsert_contacts_rejects_unlinked_article_ids() -> None:
    db = _Db()

    result = await scholar_dao.upsert_contacts_batch(
        db,  # type: ignore[arg-type]
        project_id="project-1",
        unit="示例单位",
        direction="示例方向",
        contacts=[
            {
                "email": "author@example.org",
                "article_id": "synthetic:query",
            }
        ],
    )

    assert result == {"inserted": 0, "updated": 0, "total": 0}
