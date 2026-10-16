"""Durable cancellation and ownership contracts on an isolated MongoDB."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from api.dao import website_crawl as dao
from api.services import website_documents as service


def arguments():
    return dict(crawl_task_id="parent_webdocs", parent_task_id="parent", project_id="project",
                target_id="target", target_name="Example", seeds=["https://example.org/"],
                root_domains=["example.org"], config={})


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["fetching", "archiving"])
async def test_cancel_preserves_page_checkpoint_and_parent_pause(monkeypatch, isolated_mongo_db, phase):
    db = isolated_mongo_db
    await dao.ensure_indexes(db)
    await db.tasks.insert_one({"task_id": "parent", "status": "paused", "checkpoint": {"cursor": 4}})
    entered = asyncio.Event()

    async def fetch(*args, **kwargs):
        if phase == "fetching":
            entered.set()
            await asyncio.Event().wait()
        return SimpleNamespace(url="https://example.org/", content_type="text/html", data=b"<html>Example</html>")

    async def ingest(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "fetch_resource_with_retry", fetch)
    monkeypatch.setattr(service, "ingest_source_url", ingest)
    collector = service.WebsiteDocumentCollectionService(
        db, policy=service.WebsiteCollectionPolicy(broad_official_discovery=False),
    )
    run = asyncio.create_task(collector.run(
        parent_task_id="parent", project_id="project",
        target={"target_id": "target", "canonical_name": "Example", "root_domains": ["example.org"]},
        seed_urls=["https://example.org/"],
    ))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        pages = await dao.list_pages(db, crawl_task_id="parent_webdocs")
        assert pages[0]["status"] == phase
    finally:
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
    crawl = await dao.get_task(db, "parent_webdocs")
    assert crawl["status"] == "interrupted"
    pages = await dao.list_pages(db, crawl_task_id="parent_webdocs")
    assert pages[0]["status"] == "pending"
    assert pages[0]["attempts"] > 0
    assert pages[0]["canonical_url"] == "https://example.org/"
    parent = await db.tasks.find_one({"task_id": "parent"})
    assert parent["status"] == "paused"
    assert parent["checkpoint"] == {"cursor": 4}


@pytest.mark.asyncio
async def test_expired_run_can_resume_but_stale_page_and_finish_writes_are_rejected(isolated_mongo_db):
    from api.dao.website_crawl_runs import WebsiteCrawlBusy, WebsiteCrawlOwnershipLost

    db = isolated_mongo_db
    await dao.ensure_indexes(db)
    await dao.begin_task(db, **arguments(), runtime_id="old")
    page_ids = await dao.enqueue_pages(db, crawl_task_id="parent_webdocs", project_id="project",
                                       target_id="target", pages=[{"canonical_url": "https://example.org/a", "depth": 1, "parent_url": "https://example.org/"}], runtime_id="old")
    await dao.mark_page_started(db, page_id=page_ids[0], status="archiving", runtime_id="old")
    with pytest.raises(WebsiteCrawlBusy):
        await dao.begin_task(db, **arguments(), runtime_id="new")
    await db.website_crawl_tasks.update_one({"crawl_task_id": "parent_webdocs"}, {
        "$set": {"lease_expires_at": datetime.now(timezone.utc)-timedelta(seconds=1)},
    })
    await dao.begin_task(db, **arguments(), runtime_id="new")
    with pytest.raises(WebsiteCrawlOwnershipLost):
        await dao.mark_page_terminal(db, page_id=page_ids[0], status="archived", runtime_id="old")
    with pytest.raises(WebsiteCrawlOwnershipLost):
        await dao.finish_task(db, crawl_task_id="parent_webdocs", status="completed", summary={}, runtime_id="old")
    assert not await dao.heartbeat_task(db, crawl_task_id="parent_webdocs", counters={}, runtime_id="old")
    assert (await dao.get_task(db, "parent_webdocs"))["runtime_id"] == "new"
    assert (await dao.list_pages(db, crawl_task_id="parent_webdocs"))[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_fatal_worker_failure_releases_siblings_and_queue_waiter():
    from api.services.website_crawl_runtime import WebsiteCrawlExecution

    queue = asyncio.Queue()
    await queue.put("pending")

    async def fail():
        raise RuntimeError("persistence unavailable")

    async def blocked():
        await asyncio.Event().wait()

    workers = [asyncio.create_task(fail()), asyncio.create_task(blocked())]
    execution = WebsiteCrawlExecution(None, crawl_task_id="crawl", dry_run=True)
    with pytest.raises(RuntimeError, match="persistence unavailable"):
        await asyncio.wait_for(execution.drain(queue, workers), 1)
    assert all(worker.done() for worker in workers)
