"""Atomic ownership and terminal state of durable website crawl executions."""
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from api.db.collections import WEBSITE_CRAWL_PAGES_COLLECTION, WEBSITE_CRAWL_TASKS_COLLECTION

CRAWL_LEASE_SECONDS = 120


class WebsiteCrawlBusy(RuntimeError):
    """A live execution already owns this crawl."""


class WebsiteCrawlOwnershipLost(RuntimeError):
    """A previous execution cannot write the current crawl."""


def owned(crawl_task_id: str, runtime_id: str) -> dict[str, Any]:
    query = {"crawl_task_id": crawl_task_id}
    if runtime_id:
        query.update(runtime_id=runtime_id, status="running")
    return query


async def claim_run(db: Any, *, crawl_task_id: str, runtime_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    query = {"crawl_task_id": crawl_task_id, "$or": [
        {"status": {"$ne": "running"}}, {"lease_expires_at": {"$lte": now}},
        {"lease_expires_at": {"$exists": False}}, {"runtime_id": runtime_id},
    ]}
    try:
        previous = await db[WEBSITE_CRAWL_TASKS_COLLECTION].find_one_and_update(
            query, {"$set": {**fields, "runtime_id": runtime_id, "status": "running", "error": "",
                             "updated_at": now, "heartbeat_at": now, "started_at": now,
                             "lease_expires_at": now + timedelta(seconds=CRAWL_LEASE_SECONDS)},
                    "$unset": {"completed_at": "", "recovery_pending": ""},
                    "$setOnInsert": {"crawl_task_id": crawl_task_id, "created_at": now}},
            upsert=True, return_document=ReturnDocument.BEFORE,
        )
    except DuplicateKeyError as exc:
        raise WebsiteCrawlBusy(f"官网采集已有有效执行租约: {crawl_task_id}") from exc
    return previous or {}


async def assert_run_owner(db: Any, *, crawl_task_id: str, runtime_id: str) -> None:
    if runtime_id and not await db[WEBSITE_CRAWL_TASKS_COLLECTION].find_one(
        owned(crawl_task_id, runtime_id), {"_id": 1},
    ):
        raise WebsiteCrawlOwnershipLost(f"官网采集执行拥有者已失效: {crawl_task_id}")


async def renew_run(db: Any, *, crawl_task_id: str, runtime_id: str,
                    counters: dict[str, Any] | None = None) -> bool:
    now = datetime.now(timezone.utc)
    fields = {"updated_at": now, "heartbeat_at": now,
              "lease_expires_at": now + timedelta(seconds=CRAWL_LEASE_SECONDS)}
    if counters is not None:
        fields["counters"] = counters
    result = await db[WEBSITE_CRAWL_TASKS_COLLECTION].update_one(
        owned(crawl_task_id, runtime_id), {"$set": fields},
    )
    return bool(result.matched_count)


async def finish_run(db: Any, *, crawl_task_id: str, runtime_id: str, status: str,
                     summary: dict[str, Any], counters: dict[str, Any], error: str) -> None:
    now = datetime.now(timezone.utc)
    result = await db[WEBSITE_CRAWL_TASKS_COLLECTION].update_one(
        owned(crawl_task_id, runtime_id), {"$set": {
            "status": status, "summary": summary, "counters": counters, "error": str(error or "")[:4000],
            "updated_at": now, "completed_at": now, "lease_expires_at": now,
        }},
    )
    if runtime_id and not result.matched_count:
        raise WebsiteCrawlOwnershipLost(f"官网采集执行拥有者已失效: {crawl_task_id}")


async def interrupt_run(db: Any, *, crawl_task_id: str, runtime_id: str, error: str) -> None:
    now = datetime.now(timezone.utc)
    # Page ownership prevents a late cancellation from changing a newer run.
    await db[WEBSITE_CRAWL_PAGES_COLLECTION].update_many(
        {"crawl_task_id": crawl_task_id, "runtime_id": runtime_id,
         "status": {"$in": ["fetching", "archiving"]}},
        {"$set": {"status": "pending", "interrupted_at": now, "updated_at": now,
                  "last_error": str(error or "采集被中断")[:2000]}},
    )
    await db[WEBSITE_CRAWL_TASKS_COLLECTION].update_one(
        owned(crawl_task_id, runtime_id), {"$set": {
            "status": "interrupted", "error": str(error or "采集被中断")[:4000],
            "updated_at": now, "completed_at": now, "lease_expires_at": now,
        }},
    )


async def expire_abandoned_runs(db: Any) -> int:
    now = datetime.now(timezone.utc)
    tasks = db[WEBSITE_CRAWL_TASKS_COLLECTION]
    expired = await tasks.find({"runtime_id": {"$nin": [None, ""]}, "lease_expires_at": {"$lte": now},
                                "$or": [{"status": "running"}, {"status": "interrupted", "recovery_pending": True}]},
                               {"crawl_task_id": 1, "runtime_id": 1, "lease_expires_at": 1, "status": 1}).limit(100).to_list(100)
    changed = 0
    for run in expired:
        query = {"crawl_task_id": run["crawl_task_id"], "runtime_id": run["runtime_id"], "status": run["status"],
                 "lease_expires_at": run["lease_expires_at"]}
        result = await tasks.update_one(query, {"$set": {
            "status": "interrupted", "error": "官网采集执行租约已过期，保留队列等待按需恢复",
            "updated_at": now, "completed_at": now, "recovery_pending": True,
        }})
        if not result.modified_count:
            continue  # A heartbeat or takeover won; leave all its pages alone.
        changed += 1
        await db[WEBSITE_CRAWL_PAGES_COLLECTION].update_many(
            {"crawl_task_id": run["crawl_task_id"], "runtime_id": run["runtime_id"],
             "status": {"$in": ["fetching", "archiving"]}},
            {"$set": {"status": "pending", "interrupted_at": now, "updated_at": now}},
        )
        await tasks.update_one(
            {"crawl_task_id": run["crawl_task_id"], "runtime_id": run["runtime_id"], "status": "interrupted"},
            {"$unset": {"recovery_pending": ""}},
        )
    return changed
