"""Real MongoDB integration: crash recovery and concurrent immutable versions."""
import asyncio
import os
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_atomic_profile_outbox_recovers_crash_and_concurrent_writes(monkeypatch):
    name = os.environ.get("SF_TEST_MONGO_DATABASE", "")
    if not name.startswith("sf_test_"):
        pytest.skip("requires an explicitly named disposable sf_test_ Mongo database")
    from api.db.mongodb import init_mongo, get_db
    from api.dao import person_versions as versions
    init_mongo()
    client = get_db().client
    assert name not in await client.list_database_names()
    db = client[name]
    try:
        await versions.ensure_indexes(db)
        first = {"$set": {"person_id": "p", "name": "$literal name", "summary": "first"}, "$inc": {"profile_version": 1}, "$addToSet": {"source_urls": {"$each": ["https://example.test/a"]}}}
        original = versions.flush
        monkeypatch.setattr(versions, "flush", AsyncMock(side_effect=RuntimeError("simulated projection outage")))
        with pytest.raises(RuntimeError):
            await versions.update_with_version(db, "p", first)
        stored = await db.persons.find_one({"person_id": "p"})
        assert stored[versions.PENDING][0]["summary"] == "first"
        assert stored["name"] == "$literal name"
        monkeypatch.setattr(versions, "flush", original)
        await versions.recover_and_backfill(db)
        await asyncio.gather(*(versions.update_with_version(db, "p", {"$set": {"summary": f"revision-{i}"}, "$inc": {"profile_version": 1}}) for i in range(4)))
        history = await versions.list_versions(db, "p")
        assert history["total"] == 5
        assert {row["profile_version"] for row in history["items"]} == set(range(1, 6))
        assert {row["profile"]["summary"] for row in history["items"]} == {"first", *(f"revision-{i}" for i in range(4))}
        await versions.recover_and_backfill(db)
        assert await db.person_profile_versions.count_documents({}) == 5
        assert not (await db.persons.find_one({"person_id": "p"}))[versions.PENDING]
    finally:
        await client.drop_database(name)
