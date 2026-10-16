import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from api.services.source_documents.recovery_proof import verify_archived_version


class MemoryStorage:
    def __init__(self, objects):
        self.objects = objects
        self.metadata = {key: {"status": "ready", "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
                         for key, value in objects.items()}

    async def get(self, identity):
        return self.metadata.get(identity)

    async def iter_bytes(self, identity):
        yield self.objects[identity]


def committed_version():
    version = {"version_id": "v1", "document_id": "d1", "content_hash": "h1", "status": "processing",
               "content": {"text": "saved original evidence"}, "identity": {"title": "document"}, "analysis": {},
               "archive_status": "partial", "archive_warnings": ["historic image missing"],
               "storage_object_ids": ["raw", "dom", "structured"],
               "artifacts": {"raw_html_object_id": "raw", "rendered_html_object_id": "dom", "structured_object_id": "structured"}}
    payload = {key: version[key] for key in ("version_id", "document_id", "content_hash")}
    payload["source"] = {"content": version["content"]}
    return version, MemoryStorage({"raw": b"original", "dom": b"rendered", "structured": json.dumps(payload).encode()})


@pytest.mark.asyncio
async def test_readability_requires_actual_hashes_and_own_structured_version():
    version, storage = committed_version()
    assert (await verify_archived_version(version, storage))["readable"]
    storage.objects["raw"] = b"tampered"
    proof = await verify_archived_version(version, storage)
    assert not proof["readable"] and proof["reason"] in {"object_size_mismatch", "object_digest_mismatch"}
    version, storage = committed_version()
    version["version_id"] = "another-version"
    proof = await verify_archived_version(version, storage)
    assert not proof["readable"] and proof["reason"] == "structured_identity_mismatch"
    assert not (await verify_archived_version({"version_id": "bare"}, storage))["readable"]


async def legacy_state(db):
    old = datetime.now(timezone.utc) - timedelta(days=5)
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    version, storage = committed_version()
    await db.source_document_versions.insert_many([
        {**version, "updated_at": old}, {"version_id": "empty", "status": "processing", "updated_at": old},
        {"version_id": "claimed", "status": "processing", "updated_at": old, "archive_attempt": {"id": "active"}},
    ])
    await db.tasks.insert_many([
        {"task_id": "mobile", "project_id": "p", "task_type": "mobile_collect", "status": "completed",
         "result_status": "completed", "updated_at": old, "result": {"keywords_completed": 1, "keyword_total": 2},
         "progress": {"sources": {"mobile_collect": {"status": "partial"}}}},
        {"task_id": "parent", "project_id": "p", "status": "paused", "updated_at": old, "checkpoint": {"saved": True}},
    ])
    await db.website_crawl_tasks.insert_one({"crawl_task_id": "crawl", "parent_task_id": "parent_entity_child",
                                            "project_id": "p", "status": "running", "updated_at": old})
    await db.website_crawl_pages.insert_many([
        {"page_id": "old", "crawl_task_id": "crawl", "status": "archiving", "updated_at": old, "attempts": 4},
        {"page_id": "new-owner", "crawl_task_id": "crawl", "status": "archiving", "updated_at": old, "runtime_id": "new"},
        {"page_id": "recent", "crawl_task_id": "crawl", "status": "fetching", "updated_at": datetime.now(timezone.utc)},
    ])
    return cutoff, storage


@pytest.mark.asyncio
async def test_reconciliation_proves_readability_preserves_pauses_and_is_idempotent(isolated_mongo_db):
    from api.services.collection_reconciliation import build_reconciliation_plan, public_plan
    from api.dao.collection_reconciliation import apply_action, backup_documents
    db = isolated_mongo_db
    cutoff, storage = await legacy_state(db)
    plan = await build_reconciliation_plan(db, storage, cutoff=cutoff)
    assert sorted(a["kind"] for a in plan["actions"]) == ["crawl_interrupted", "mobile_partial", "version_interrupted", "version_readable"]
    assert all("snapshot" not in a for a in public_plan(plan)["actions"])
    before = [row async for row in backup_documents(db, plan["actions"])]
    assert len(before) == 5
    assert (await db.source_document_versions.find_one({"version_id": "v1"}))["status"] == "processing"
    outcomes = [await apply_action(db, action, plan_id="review") for action in plan["actions"]]
    assert all(o["applied"] for o in outcomes)
    assert sum(o["pages_requeued"] for o in outcomes) == 1
    restored = await db.source_document_versions.find_one({"version_id": "v1"})
    assert restored["status"] == "ready" and restored["archive_status"] == "partial"
    assert restored["content"] == {"text": "saved original evidence"}
    assert (await db.source_document_versions.find_one({"version_id": "empty"}))["status"] == "error"
    assert (await db.source_document_versions.find_one({"version_id": "claimed"}))["status"] == "processing"
    assert (await db.tasks.find_one({"task_id": "parent"}))["status"] == "paused"
    mobile = await db.tasks.find_one({"task_id": "mobile"})
    assert mobile["status"] == "completed" and mobile["result_status"] == "partial"
    assert (await db.website_crawl_pages.find_one({"page_id": "old"}))["attempts"] == 4
    assert (await db.website_crawl_pages.find_one({"page_id": "new-owner"}))["status"] == "archiving"
    assert not (await build_reconciliation_plan(db, storage, cutoff=cutoff))["actions"]


@pytest.mark.asyncio
async def test_changed_snapshot_and_resumed_parent_reject_old_plan(isolated_mongo_db):
    from api.services.collection_reconciliation import build_reconciliation_plan
    from api.dao.collection_reconciliation import apply_action
    db = isolated_mongo_db
    cutoff, storage = await legacy_state(db)
    plan = await build_reconciliation_plan(db, storage, cutoff=cutoff)
    await db.tasks.update_one({"task_id": "parent"}, {"$set": {"status": "running"}})
    await db.source_document_versions.update_one({"version_id": "v1"}, {"$set": {"updated_at": datetime.now(timezone.utc)}})
    for action in plan["actions"]:
        if action["kind"] in {"crawl_interrupted", "version_readable"}:
            assert not (await apply_action(db, action, plan_id="stale"))["applied"]
    assert (await db.website_crawl_pages.find_one({"page_id": "old"}))["status"] == "archiving"
