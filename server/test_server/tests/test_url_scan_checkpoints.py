from __future__ import annotations

import pytest

from api.dao import url_scan
from api.db.collections import (
    URL_SCAN_RESULTS_COLLECTION,
    URL_SCAN_TASKS_COLLECTION,
)
from api.services.url_scan_pipeline import (
    _keep_verified_contact_finding,
    terminal_url_scan_status,
)


def test_terminal_url_scan_with_failures_is_partial() -> None:
    assert terminal_url_scan_status(0) == "completed"
    assert terminal_url_scan_status(1) == "partial"


def test_low_score_verified_contact_survives_display_threshold() -> None:
    assert _keep_verified_contact_finding(
        {
            "type": "customer_service",
            "channel": "phone",
            "value": "010-62677800",
            "source_url": "https://zwfw.cscse.edu.cn/",
            "evidence": "联系我们 咨询电话：010-62677800（客服中心）",
            "attention_score": 30,
            "target_relation": "confirmed",
        },
        min_attention_score=60,
    )


def test_low_score_non_contact_still_respects_display_threshold() -> None:
    assert not _keep_verified_contact_finding(
        {
            "type": "other",
            "channel": "other",
            "value": "普通页面信息",
            "source_url": "https://zwfw.cscse.edu.cn/",
            "evidence": "普通页面正文",
            "attention_score": 30,
            "target_relation": "confirmed",
        },
        min_attention_score=60,
    )


@pytest.mark.asyncio
async def test_failed_screenshot_refresh_preserves_existing_reference() -> None:
    db = _Db()

    fields = await url_scan.upsert_terminal_result(
        db,
        task_id="task-1",
        project_id="project-1",
        target_id="target-1",
        source="web_tagging",
        url="https://example.com",
        success=True,
    )

    assert "screenshot_object_id" not in fields
    assert "screenshot_url" not in fields
    assert "screenshot_object_id" not in db.collection.update["$set"]
    assert "screenshot_url" not in db.collection.update["$set"]


@pytest.mark.asyncio
async def test_url_scan_persists_screenshot_capture_metadata() -> None:
    db = _Db()

    fields = await url_scan.upsert_terminal_result(
        db,
        task_id="task-1",
        project_id="project-1",
        target_id="target-1",
        source="web_tagging",
        url="https://example.com",
        success=True,
        screenshot_object_id="object-1",
        screenshot_url="/objects/object-1/content",
        screenshot_captured_url="https://example.com/contact",
        screenshot_captured_at="2026-08-24T00:00:00+00:00",
        screenshot_width=1280,
        screenshot_height=720,
        screenshot_status="ready",
    )

    assert fields["screenshot_captured_url"] == "https://example.com/contact"
    assert fields["screenshot_width"] == 1280
    assert fields["screenshot_status"] == "ready"


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
        self.distinct_query = None
        self.find_one_query = None

    def find(self, query, _projection):
        self.find_query = query
        return _Cursor([{
            "url": "https://done.example",
            "endpoint_key": "done.example",
        }])

    async def update_one(self, query, update, **_kwargs):
        self.update_query = query
        self.update = update

    async def distinct(self, field, query):
        assert field == "task_id"
        self.distinct_query = query
        return ["task-retry"]

    async def find_one(self, query, _projection):
        self.find_one_query = query
        return {"_id": "task"}


class _Db:
    def __init__(self):
        self.collection = _Collection()

    def __getitem__(self, name):
        assert name in {URL_SCAN_RESULTS_COLLECTION, URL_SCAN_TASKS_COLLECTION}
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
async def test_project_target_endpoint_is_reused_across_scan_tasks() -> None:
    db = _Db()

    completed = await url_scan.completed_urls(
        db,
        task_id="task-new",
        project_id="project-1",
        target_id="target-1",
        urls=["http://done.example", "https://new.example"],
    )

    assert completed == {"http://done.example"}
    query = db.collection.find_query["$and"][1]["$or"][1]
    assert query["target_id"] == "target-1"
    assert query["endpoint_key"] == {
        "$in": ["done.example", "new.example"]
    }
    assert query["$or"] == [
        {"project_id": "project-1"},
        {"project_ids": "project-1"},
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
    assert result["failure_class"] == "infrastructure"
    assert db.collection.update["$unset"] == {"completed_at": ""}
    assert db.collection.update["$push"]["attempt_errors"]["$slice"] == -10


@pytest.mark.asyncio
async def test_retryable_task_ids_query_explicit_non_terminal_rows() -> None:
    db = _Db()

    task_ids = await url_scan.retryable_task_ids(
        db,
        task_ids={"task-done", "task-retry"},
    )

    assert task_ids == {"task-retry"}
    assert db.collection.distinct_query == {
        "task_id": {"$in": ["task-done", "task-retry"]},
        "terminal": False,
        "retryable": True,
    }


@pytest.mark.asyncio
async def test_incomplete_incremental_task_requires_full_scan() -> None:
    db = _Db()

    required = await url_scan.task_requires_full_scan(db, task_id="task-retry")

    assert required is True
    assert db.collection.find_one_query["task_id"] == "task-retry"
    assert db.collection.find_one_query["remaining_urls"] == {"$gt": 0}
