from __future__ import annotations

from typing import Any

import pytest


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def to_list(self, _length: int | None) -> list[dict[str, Any]]:
        return list(self.rows)


class _Collection:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.find_query: dict[str, Any] = {}
        self.count_queries: list[dict[str, Any]] = []

    def find(
        self,
        query: dict[str, Any],
        _projection: dict[str, Any],
    ) -> _Cursor:
        self.find_query = query
        return _Cursor(self.rows)

    async def count_documents(self, query: dict[str, Any]) -> int:
        self.count_queries.append(query)
        return 3


class _Db:
    def __init__(self) -> None:
        from api.db.collections import (
            BIDDING_RECORDS_COLLECTION,
            COPYWRITINGS_COLLECTION,
            FINDINGS_COLLECTION,
        )

        self.collections = {
            BIDDING_RECORDS_COLLECTION: _Collection(
                [
                    {
                        "attachments": [{"status": "ready"}],
                        "raw_content_object_id": "raw-1",
                        "provider_payload_object_id": "payload-1",
                        "detail_html_object_id": "detail-1",
                    }
                ]
            ),
            FINDINGS_COLLECTION: _Collection(),
            COPYWRITINGS_COLLECTION: _Collection(),
        }

    def __getitem__(self, name: str) -> _Collection:
        return self.collections[name]


@pytest.mark.asyncio
async def test_bidding_recovery_uses_archive_and_visual_child_task_ids() -> None:
    from api.db.collections import (
        BIDDING_RECORDS_COLLECTION,
        COPYWRITINGS_COLLECTION,
        FINDINGS_COLLECTION,
    )
    from api.services.company_scan_recovery import restore_bidding

    db = _Db()

    result = await restore_bidding(
        db,  # type: ignore[arg-type]
        task_id="company-task",
        company_name="目标单位",
    )

    assert db[BIDDING_RECORDS_COLLECTION].find_query == {
        "task_ids": "company-task_bidding"
    }
    assert db[FINDINGS_COLLECTION].count_queries == [
        {"task_id": "company-task_bidding_visual", "source": "bidding"}
    ]
    assert db[COPYWRITINGS_COLLECTION].count_queries == [
        {"task_id": "company-task_bidding_visual"}
    ]
    assert result["records_fetched"] == 1
    assert result["attachments_archived"] == 1
    assert result["visual_analysis"]["findings_count"] == 3


@pytest.mark.asyncio
async def test_retryable_child_scans_invalidate_only_their_parent_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import url_scan as url_scan_dao
    from api.dao import website_crawl as website_crawl_dao
    from api.services.company_scan_recovery import find_retryable_core_modules

    captured: set[str] = set()

    async def retryable_task_ids(*_args: Any, **kwargs: Any) -> set[str]:
        nonlocal captured
        captured = set(kwargs["task_ids"])
        return {"company-task_url"}

    async def get_task(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def task_requires_retry(*_args: Any, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(url_scan_dao, "retryable_task_ids", retryable_task_ids)
    monkeypatch.setattr(url_scan_dao, "get_task", get_task)
    monkeypatch.setattr(
        website_crawl_dao,
        "task_requires_retry",
        task_requires_retry,
    )

    modules = await find_retryable_core_modules(
        object(),  # type: ignore[arg-type]
        task_id="company-task",
    )

    assert captured == {
        "company-task_url",
        "company-task_bidding_visual",
    }
    assert modules == {"asset_url"}


def test_legacy_website_checkpoint_requires_document_crawl() -> None:
    from api.services.company_scan_recovery import find_incompatible_core_modules

    assert find_incompatible_core_modules(
        {
            "asset_url": {
                "url_scan": {"enabled": True, "status": "completed"},
            }
        }
    ) == {"asset_url"}
    assert find_incompatible_core_modules(
        {
            "asset_url": {
                "url_scan": {"enabled": True, "status": "completed"},
                "website_documents": {"enabled": True, "status": "completed"},
            }
        }
    ) == set()


def test_bidding_checkpoint_reuses_equal_or_wider_windows() -> None:
    from api.services.company_scan_recovery import find_incompatible_core_modules

    assert find_incompatible_core_modules(
        {"bidding": {"records_fetched": 57}}
    ) == set()
    assert find_incompatible_core_modules(
        {
            "bidding": {
                "records_fetched": 30,
                "lookback_days": 15,
                "bid_types": ["1", "2", "4"],
            }
        }
    ) == {"bidding"}
    assert find_incompatible_core_modules(
        {
            "bidding": {
                "records_fetched": 60,
                "lookback_days": 30,
                "bid_types": ["1", "2", "4"],
            }
        }
    ) == set()
    assert find_incompatible_core_modules(
        {
            "bidding": {
                "records_fetched": 120,
                "lookback_days": 180,
                "bid_types": ["1", "2", "4"],
            }
        }
    ) == set()
