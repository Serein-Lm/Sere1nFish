from __future__ import annotations

import pytest

from api.dao import url_scan
from api.db.collections import URL_SCAN_RESULTS_COLLECTION


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Collection:
    def __init__(self):
        self.find_query = None
        self.update_query = None
        self.update = None

    def find(self, query, _projection):
        self.find_query = query
        return _Cursor([{"url": "https://done.example"}])

    async def update_one(self, query, update, **_kwargs):
        self.update_query = query
        self.update = update


class _Db:
    def __init__(self):
        self.collection = _Collection()

    def __getitem__(self, name):
        assert name == URL_SCAN_RESULTS_COLLECTION
        return self.collection


@pytest.mark.asyncio
async def test_explicit_retryable_result_is_not_completed() -> None:
    db = _Db()

    completed = await url_scan.completed_urls(
        db,
        task_id="task-1",
        urls=["https://done.example", "https://retry.example"],
    )

    assert completed == {"https://done.example"}
    assert db.collection.find_query["$or"] == [
        {"terminal": True},
        {
            "terminal": {"$exists": False},
            "success": {"$exists": True},
        },
    ]


@pytest.mark.asyncio
async def test_retryable_result_keeps_attempt_history_without_completion() -> None:
    db = _Db()

    result = await url_scan.upsert_retryable_result(
        db,
        task_id="task-1",
        project_id="project-1",
        target_id="target-1",
        source="web_tagging",
        url="https://retry.example",
        error="模型额度暂不可用",
    )

    assert result["terminal"] is False
    assert result["retryable"] is True
    assert db.collection.update["$unset"] == {"completed_at": ""}
    assert db.collection.update["$push"]["attempt_errors"]["$slice"] == -10
