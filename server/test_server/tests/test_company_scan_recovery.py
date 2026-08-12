from __future__ import annotations

from typing import Any

import pytest


def test_prior_incomplete_sources_reopen_their_own_modules() -> None:
    from api.services.company_scan_recovery import (
        recovery_modules_for_incomplete_sources,
    )

    assert recovery_modules_for_incomplete_sources(
        {
            "incomplete_sources": [
                "url_scan",
                "website_documents",
                "asset_intelligence",
                "wechat",
                "scholar",
                "bidding",
                "xhs",
                "control_structure",
                "subtasks",
            ]
        }
    ) == {
        "asset_url",
        "wechat",
        "scholar",
        "bidding",
        "xhs",
        "control_structure",
    }


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
async def test_bidding_recovery_uses_archive_and_visual_child_task_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import url_scan as url_scan_dao
    from api.db.collections import (
        BIDDING_RECORDS_COLLECTION,
        COPYWRITINGS_COLLECTION,
        FINDINGS_COLLECTION,
    )
    from api.services.company_scan_recovery import restore_bidding

    db = _Db()

    async def get_visual_task(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["task_id"] == "company-task_bidding_visual"
        return {"status": "completed", "remaining_urls": 0}

    monkeypatch.setattr(url_scan_dao, "get_task", get_visual_task)

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
    assert result["attachments_discovered"] == 0
    assert result["status"] == "completed"
    assert result["visual_analysis"]["findings_count"] == 3


@pytest.mark.asyncio
async def test_bidding_recovery_preserves_partial_archive_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import url_scan as url_scan_dao
    from api.db.collections import BIDDING_RECORDS_COLLECTION
    from api.services.company_scan_recovery import restore_bidding

    db = _Db()
    db[BIDDING_RECORDS_COLLECTION].rows = [
        {
            "attachment_urls": ["https://example.gov.cn/file.pdf"],
            "attachments": [{"status": "error"}],
            "attachments_truncated": 1,
            "archive_errors": ["附件读取失败"],
        }
    ]

    async def get_visual_task(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "partial", "remaining_urls": 1}

    monkeypatch.setattr(url_scan_dao, "get_task", get_visual_task)

    result = await restore_bidding(
        db,  # type: ignore[arg-type]
        task_id="company-task",
        company_name="目标单位",
    )

    assert result["status"] == "partial"
    assert result["attachments_discovered"] == 1
    assert result["attachments_incomplete"] == 2
    assert result["archive_error_count"] == 1
    assert result["visual_analysis"]["status"] == "partial"


@pytest.mark.asyncio
async def test_bidding_recovery_detects_discovered_but_missing_attachments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import url_scan as url_scan_dao
    from api.db.collections import BIDDING_RECORDS_COLLECTION
    from api.services.company_scan_recovery import restore_bidding

    db = _Db()
    db[BIDDING_RECORDS_COLLECTION].rows = [
        {
            "attachment_urls": [
                "https://example.gov.cn/a.pdf",
                "https://example.gov.cn/b.docx",
            ],
            "attachments": [],
            "archive_errors": [],
        }
    ]

    async def get_visual_task(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "completed", "remaining_urls": 0}

    monkeypatch.setattr(url_scan_dao, "get_task", get_visual_task)

    result = await restore_bidding(
        db,  # type: ignore[arg-type]
        task_id="company-task",
        company_name="目标单位",
    )

    assert result["status"] == "partial"
    assert result["attachments_discovered"] == 2
    assert result["attachments_incomplete"] == 2


@pytest.mark.asyncio
async def test_terminal_website_repair_reconciles_parent_read_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.db.collections import COMPANY_SCAN_COLLECTION, TASKS_COLLECTION
    from api.services import target_scan_profile
    from api.services.company_scan_recovery import (
        reconcile_terminal_company_website_result,
    )

    class _UpdateResult:
        modified_count = 1

    class _TaskCollection:
        def __init__(self) -> None:
            self.document = {
                "task_id": "task-1",
                "task_type": "company_scan",
                "project_id": "project-1",
                "status": "completed",
                "result": {
                    "status": "partial",
                    "identity": {
                        "target_id": "target-1",
                        "scan_profile_fingerprint": "profile-1",
                    },
                    "assets": {
                        "enabled": True,
                        "status": "completed",
                        "providers": {
                            "fofa": {"errors": []},
                            "hunter": {"errors": ["not configured"]},
                        },
                    },
                    "url_scan": {"enabled": True, "status": "completed"},
                    "website_documents": {
                        "enabled": True,
                        "status": "partial",
                    },
                    "control_structure": {"enabled": False},
                    "bidding": {"enabled": False},
                    "wechat": {"enabled": False},
                    "scholar": {"enabled": False},
                    "xhs": {"enabled": False},
                    "sub_errors": [],
                },
            }
            self.update: dict[str, Any] = {}

        async def find_one(self, _query: dict[str, Any], _projection: dict[str, Any]):
            return dict(self.document)

        async def update_one(self, _query: dict[str, Any], update: dict[str, Any]):
            self.update = update
            return _UpdateResult()

    class _CompanyCollection:
        def __init__(self) -> None:
            self.update: dict[str, Any] = {}

        async def update_one(
            self,
            _query: dict[str, Any],
            update: dict[str, Any],
            **_kwargs: Any,
        ) -> _UpdateResult:
            self.update = update
            return _UpdateResult()

    class _ReconcileDb:
        def __init__(self) -> None:
            self.tasks = _TaskCollection()
            self.company = _CompanyCollection()

        def __getitem__(self, name: str):
            return self.tasks if name == TASKS_COLLECTION else self.company

    coverage: dict[str, Any] = {}

    async def record_coverage(*_args: Any, **kwargs: Any) -> None:
        coverage.update(kwargs)

    monkeypatch.setattr(
        target_scan_profile,
        "record_target_scan_coverage",
        record_coverage,
    )
    db = _ReconcileDb()
    website = {
        "enabled": True,
        "status": "completed",
        "documents_scheduled": 12,
        "documents_archived": 12,
        "documents_partial": 0,
        "failed_pages": 0,
        "attachments_archived": 3,
        "truncated": False,
    }

    changed = await reconcile_terminal_company_website_result(
        db,  # type: ignore[arg-type]
        parent_task_id="task-1",
        website_summary=website,
    )

    assert changed is True
    task_result = db.tasks.update["$set"]["result"]
    assert task_result["status"] == "completed"
    assert task_result["incomplete_sources"] == []
    assert task_result["website_documents"] == website
    assert db.tasks.update["$set"]["result_status"] == "completed"
    assert db.company.update["$set"]["result"] == task_result
    assert coverage["status"] == "completed"
    assert coverage["summary"]["documents_archived"] == 12


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


@pytest.mark.asyncio
async def test_asset_only_recovery_does_not_require_website_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import url_scan as url_scan_dao
    from api.dao import website_crawl as website_crawl_dao
    from api.services.company_scan_recovery import restore_asset_url

    class Collection:
        async def count_documents(self, _query: dict[str, Any]) -> int:
            return 0

    class Db:
        def __getitem__(self, _name: str) -> Collection:
            return Collection()

    async def summarize(*_args: Any, **_kwargs: Any) -> dict[str, int]:
        return {"processed": 0, "succeeded": 0, "failed": 0}

    async def get_url_task(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "completed", "remaining_urls": 0}

    async def unexpected_website(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("资产单通道恢复不应读取官网归档任务")

    monkeypatch.setattr(url_scan_dao, "summarize_task", summarize)
    monkeypatch.setattr(url_scan_dao, "get_task", get_url_task)
    monkeypatch.setattr(website_crawl_dao, "get_task", unexpected_website)

    result = await restore_asset_url(
        Db(),  # type: ignore[arg-type]
        task_id="asset-only",
        project_id="project-1",
        target_id="target-1",
        incremental_scan=False,
        enable_asset_discovery=True,
        enable_url_scan=False,
    )

    assert result["status"] == "completed"
    assert result["assets"]["enabled"] is True
    assert result["url_scan"]["status"] == "disabled"
    assert result["website_documents"] == {
        "enabled": False,
        "status": "disabled",
    }


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
