"""Narrow compare-and-set maintenance for legacy collection state mismatches."""
from datetime import datetime, timezone
from typing import Any

from api.db.collections import (
    TASKS_COLLECTION, SOURCE_DOCUMENT_VERSIONS_COLLECTION,
    WEBSITE_CRAWL_TASKS_COLLECTION, WEBSITE_CRAWL_PAGES_COLLECTION,
)

COLLECTIONS = {"mobile_partial": TASKS_COLLECTION, "crawl_interrupted": WEBSITE_CRAWL_TASKS_COLLECTION,
               "version_readable": SOURCE_DOCUMENT_VERSIONS_COLLECTION, "version_interrupted": SOURCE_DOCUMENT_VERSIONS_COLLECTION}


async def legacy_candidates(db: Any, *, cutoff: datetime) -> dict[str, list[dict[str, Any]]]:
    return {
        "mobile": await db[TASKS_COLLECTION].find({"task_type": "mobile_collect", "status": "completed",
            "result_status": "completed", "progress.sources.mobile_collect.status": "partial"}).to_list(None),
        "crawls": await db[WEBSITE_CRAWL_TASKS_COLLECTION].find({"status": "running",
            "runtime_id": {"$exists": False}, "updated_at": {"$lt": cutoff}}).to_list(None),
        "versions": await db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].find({"status": "processing",
            "archive_attempt": {"$exists": False}, "updated_at": {"$lt": cutoff}}).to_list(None),
    }


async def find_crawl_parent(db, crawl):
    identity = str(crawl.get("parent_task_id") or "")
    # Explicit historic child identity contract: root_entity_<hash>.
    if "_entity_" in identity:
        identity = identity.split("_entity_", 1)[0]
    return await db[TASKS_COLLECTION].find_one({"task_id": identity, "project_id": crawl.get("project_id")})


async def backup_documents(db, actions):
    for action in actions:
        yield {"collection": COLLECTIONS[action["kind"]], "document": action["snapshot"]}
        if action["kind"] == "crawl_interrupted":
            async for page in db[WEBSITE_CRAWL_PAGES_COLLECTION].find({
                "crawl_task_id": action["snapshot"]["crawl_task_id"],
                "runtime_id": {"$exists": False}, "status": {"$in": ["fetching", "archiving"]},
                "updated_at": {"$lt": action["cutoff"]},
            }):
                yield {"collection": WEBSITE_CRAWL_PAGES_COLLECTION, "document": page}


async def apply_action(db, action, *, plan_id: str):
    kind, snapshot = action["kind"], action["snapshot"]
    now = datetime.now(timezone.utc)
    query = {"_id": snapshot["_id"], "updated_at": snapshot.get("updated_at"), "status": snapshot["status"]}
    fields = {"updated_at": now, "reconciliation": {"id": plan_id, "kind": kind, "at": now}}
    if kind == "mobile_partial":
        query.update({"result_status": "completed", "progress.sources.mobile_collect.status": "partial"})
        fields.update({"result_status": "partial", "result.status": "partial", "result.result_version": 1,
                       "progress.stage": "partial", "progress.message": "任务已结束，但手机采集未完整完成；历史状态已对账"})
    elif kind == "crawl_interrupted":
        query["runtime_id"] = {"$exists": False}
        parent = await find_crawl_parent(db, snapshot)
        expected_parent = action["parent"]
        if not parent or parent.get("status") != expected_parent["status"] or parent.get("updated_at") != expected_parent.get("updated_at"):
            return {"applied": False, "reason": "parent_changed"}
        fields.update({"status": "interrupted", "error": "历史运行状态已与父任务对账，未发现有效执行租约；保留队列等待按需继续",
                       "completed_at": now})
    else:
        query["archive_attempt"] = {"$exists": False}
        fields.update({"status": "ready" if kind == "version_readable" else "error",
                       "error": "" if kind == "version_readable" else "历史来源归档未完成，已结束遗留运行状态；等待按需重试",
                       "archive_attempt": {"id": "reconciliation:" + plan_id, "status": "interrupted",
                                           "finished_at": now, "lease_expires_at": now,
                                           "recovery_proof": action["proof"]}})
    updated = await db[COLLECTIONS[kind]].update_one(query, {"$set": fields})
    if not updated.modified_count:
        return {"applied": False, "reason": "snapshot_changed"}
    pages = 0
    if kind == "crawl_interrupted":
        result = await db[WEBSITE_CRAWL_PAGES_COLLECTION].update_many(
            {"crawl_task_id": snapshot["crawl_task_id"], "runtime_id": {"$exists": False},
             "status": {"$in": ["fetching", "archiving"]}, "updated_at": {"$lt": action["cutoff"]}},
            {"$set": {"status": "pending", "interrupted_at": now, "updated_at": now,
                      "reconciliation": {"id": plan_id, "kind": kind, "at": now}}},
        )
        pages = result.modified_count
    return {"applied": True, "pages_requeued": pages}
