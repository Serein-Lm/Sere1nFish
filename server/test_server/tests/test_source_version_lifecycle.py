"""Real Mongo atomicity plus cancellation of the public ingestion boundary."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from api.dao import source_documents as dao


@pytest.mark.asyncio
async def test_ready_version_stays_readable_during_cancelled_upgrade(isolated_mongo_db):
    db = isolated_mongo_db
    await dao.ensure_indexes(db)
    await dao.mark_version_ready(db, version_id="version", payload={"content": {"text": "original"}})
    await dao.begin_version(db, version_id="version", document_id="doc", content_hash="hash",
                            source_type="official_website_document", attempt_id="attempt")
    assert (await dao.get_version(db, "version"))["status"] == "ready"
    await dao.mark_version_error(db, "version", "cancelled", attempt_id="attempt", interrupted=True)
    version = await dao.get_version(db, "version")
    assert version["status"] == "ready"
    assert version["content"] == {"text": "original"}
    assert version["archive_attempt"]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_active_attempt_is_exclusive_and_expired_owner_cannot_commit(isolated_mongo_db):
    from api.dao.source_document_versions import SourceVersionBusy, SourceVersionOwnershipLost

    db = isolated_mongo_db
    await dao.ensure_indexes(db)
    arguments = dict(version_id="version", document_id="doc", content_hash="hash", source_type="official_website_document")
    await dao.begin_version(db, **arguments, attempt_id="old")
    with pytest.raises(SourceVersionBusy):
        await dao.begin_version(db, **arguments, attempt_id="new")
    await db.source_document_versions.update_one(
        {"version_id": "version"},
        {"$set": {"archive_attempt.lease_expires_at": datetime.now(timezone.utc)-timedelta(seconds=1)}},
    )
    await dao.begin_version(db, **arguments, attempt_id="new")
    with pytest.raises(SourceVersionOwnershipLost):
        await dao.mark_version_ready(db, version_id="version", payload={"content": {"text": "stale"}}, attempt_id="old")
    await dao.mark_version_error(db, "version", "stale cancellation", attempt_id="old", interrupted=True)
    await dao.mark_version_ready(db, version_id="version", payload={"content": {"text": "current"}}, attempt_id="new")
    version = await dao.get_version(db, "version")
    assert version["content"]["text"] == "current"
    assert version["archive_attempt"]["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("has_ready", [False, True])
async def test_ingest_cancellation_closes_attempt_and_keeps_existing_evidence(monkeypatch, isolated_mongo_db, has_ready):
    from api.services.source_documents import service
    from test_server.tests.test_source_documents import _capture

    db = isolated_mongo_db
    await dao.ensure_indexes(db)
    capture = _capture(raw_html=b"raw", rendered_html=b"dom")
    document_id = dao.document_id_for_url(capture.canonical_url)
    version_id = dao.version_id_for_content(document_id, service.stable_content_hash(capture))
    if has_ready:
        await dao.mark_version_ready(db, version_id=version_id, payload={"artifacts": {"raw_html_object_id": "original"}})

    async def capture_url(*_args, **_kwargs):
        return capture

    async def analyze(*_args, **_kwargs):
        return {"subject_match": 95, "score": 95, "review_decision": "accept"}

    async def images(*_args, **_kwargs):
        return [], ""

    async def cancel_during_archive(*_args, **_kwargs):
        during = await dao.get_version(db, version_id)
        assert during["status"] == ("ready" if has_ready else "processing")
        raise asyncio.CancelledError()

    monkeypatch.setattr(service, "get_source_document_provider", lambda _url: SimpleNamespace(capture=capture_url))
    monkeypatch.setattr(service, "_capture_has_more_complete_images", lambda *_args: True)
    monkeypatch.setattr(service, "analyze_and_review_article", analyze)
    monkeypatch.setattr(service, "analyze_article_images", images)
    monkeypatch.setattr(service, "_store_capture_artifacts", cancel_during_archive)
    with pytest.raises(asyncio.CancelledError):
        await service.ingest_source_url(db, url=capture.canonical_url, project_id="project", run_task_id="task")
    after = await dao.get_version(db, version_id)
    assert after["status"] == ("ready" if has_ready else "error")
    assert after["archive_attempt"]["status"] == "interrupted"
    if has_ready:
        assert after["artifacts"]["raw_html_object_id"] == "original"


@pytest.mark.asyncio
async def test_link_failure_after_commit_does_not_rollback_ready_version(isolated_mongo_db):
    from api.services.source_documents.version_lifecycle import SourceVersionAttempt

    db = isolated_mongo_db
    await dao.ensure_indexes(db)
    attempt = SourceVersionAttempt(db, version_id="v", document_id="d", content_hash="h", source_type="official_website_document")
    try:
        await attempt.start()
        await attempt.commit({"content": {"text": "committed"}})
        await attempt.fail(RuntimeError("link write failed"))
    finally:
        await attempt.close()
    version = await dao.get_version(db, "v")
    assert version["status"] == "ready"
    assert version["content"]["text"] == "committed"
