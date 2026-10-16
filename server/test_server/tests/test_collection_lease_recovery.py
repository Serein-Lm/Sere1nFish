from datetime import datetime, timedelta, timezone

import pytest


@pytest.mark.asyncio
async def test_expiry_preserves_ready_evidence_live_owners_and_paused_parent(isolated_mongo_db):
    from api.services.collection_lease_recovery import CollectionLeaseRecovery
    db = isolated_mongo_db
    past, future = datetime.now(timezone.utc) - timedelta(minutes=5), datetime.now(timezone.utc) + timedelta(minutes=5)
    await db.source_document_versions.insert_many([
        {"version_id": "ready", "status": "ready", "content": {"text": "saved"},
         "archive_attempt": {"id": "old", "status": "processing", "lease_expires_at": past}},
        {"version_id": "new", "status": "processing",
         "archive_attempt": {"id": "old-new", "status": "processing", "lease_expires_at": past}},
        {"version_id": "live", "status": "processing",
         "archive_attempt": {"id": "live", "status": "processing", "lease_expires_at": future}},
        {"version_id": "legacy", "status": "processing"},
    ])
    await db.tasks.insert_one({"task_id": "parent", "status": "paused"})
    await db.website_crawl_tasks.insert_many([
        {"crawl_task_id": "expired", "parent_task_id": "parent", "status": "running", "runtime_id": "old", "lease_expires_at": past},
        {"crawl_task_id": "live", "status": "running", "runtime_id": "current", "lease_expires_at": future},
        {"crawl_task_id": "legacy", "status": "running"},
    ])
    await db.website_crawl_pages.insert_many([
        {"crawl_task_id": "expired", "runtime_id": "old", "status": "archiving", "page_id": "old"},
        {"crawl_task_id": "expired", "runtime_id": "new", "status": "archiving", "page_id": "new"},
        {"crawl_task_id": "expired", "runtime_id": "old", "status": "archived", "page_id": "saved"},
    ])
    assert await CollectionLeaseRecovery().run_once(db) == (2, 1)
    ready = await db.source_document_versions.find_one({"version_id": "ready"})
    assert ready["status"] == "ready" and ready["content"] == {"text": "saved"}
    assert ready["archive_attempt"]["status"] == "interrupted"
    assert (await db.source_document_versions.find_one({"version_id": "new"}))["status"] == "error"
    for key in ["live", "legacy"]:
        assert (await db.source_document_versions.find_one({"version_id": key}))["status"] == "processing"
        assert (await db.website_crawl_tasks.find_one({"crawl_task_id": key}))["status"] == "running"
    pages = {p["page_id"]: p["status"] async for p in db.website_crawl_pages.find({})}
    assert pages == {"old": "pending", "new": "archiving", "saved": "archived"}
    assert (await db.tasks.find_one({"task_id": "parent"}))["status"] == "paused"
    assert await CollectionLeaseRecovery().run_once(db) == (0, 0)


@pytest.mark.asyncio
async def test_crash_between_crawl_and_page_recovery_is_resumable(isolated_mongo_db):
    from api.services.collection_lease_recovery import CollectionLeaseRecovery
    db = isolated_mongo_db
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    await db.website_crawl_tasks.insert_one({"crawl_task_id": "retry", "status": "interrupted",
                                            "runtime_id": "old", "lease_expires_at": past, "recovery_pending": True})
    await db.website_crawl_pages.insert_one({"crawl_task_id": "retry", "runtime_id": "old", "status": "fetching"})
    assert await CollectionLeaseRecovery().run_once(db) == (0, 1)
    assert (await db.website_crawl_pages.find_one({}))["status"] == "pending"
    assert "recovery_pending" not in await db.website_crawl_tasks.find_one({})
