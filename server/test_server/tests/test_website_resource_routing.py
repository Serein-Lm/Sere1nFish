"""Dynamic attachment routing and permanent HTTP failures."""
import asyncio
from types import SimpleNamespace

import aiohttp
import pytest

from api.dao import website_crawl as dao
from api.services import website_documents as website
from api.services.source_documents import web
from api.services.source_documents.resources import FetchedResource


@pytest.mark.asyncio
@pytest.mark.parametrize("media_type,data", [
    ("application/pdf", b"%PDF-1.7 sample"),
    ("text/html", b"%PDF-1.7 sample"),
    ("application/vnd.ms-excel", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 sample"),
])
async def test_dynamic_attachment_discovery_reuses_download(monkeypatch, media_type, data):
    requested = []
    captured = []
    resource = FetchedResource(url="https://example.org/result.php", data=data,
                               content_type=media_type, filename="result.php")

    async def fetch(_session, url, **kwargs):
        requested.append(url)
        return resource

    async def forbidden_fetch(*args, **kwargs):
        raise AssertionError("discovered attachment was downloaded twice")

    async def ingest(_db, **kwargs):
        document = await web.OfficialWebDocumentProvider().capture(
            kwargs["url"], prefetched=kwargs.get("prefetched_resource"),
        )
        captured.append(document)
        return {"archive_status": "complete", "attachment_count": 1, "contacts": []}

    monkeypatch.setattr(website, "fetch_resource_with_retry", fetch)
    monkeypatch.setattr(website, "ingest_source_url", ingest)
    monkeypatch.setattr(web, "fetch_resource_with_retry", forbidden_fetch)
    monkeypatch.setattr(web, "extract_attachment_text", lambda *a, **k: ("Archived body", "", "document"))
    collector = website.WebsiteDocumentCollectionService(
        None, policy=website.WebsiteCollectionPolicy(broad_official_discovery=False, max_page_attempts=1),
    )
    result = await collector.run(parent_task_id="parent", project_id="project",
        target={"target_id": "target", "root_domains": ["example.org"]},
        seed_urls=["https://example.org/"], dry_run=True)
    assert result["status"] == "completed"
    assert len(requested) == 1
    assert len(captured) == 1
    assert captured[0].attachments[0].data == data
    assert captured[0].metadata["direct_attachment"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 410])
async def test_missing_pages_are_not_retried_in_this_or_next_run(monkeypatch, isolated_mongo_db, status):
    db = isolated_mongo_db
    await dao.ensure_indexes(db)
    calls = 0

    async def fetch(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise aiohttp.ClientResponseError(request_info=SimpleNamespace(real_url="https://example.org/"),
                                          history=(), status=status, message="Missing")

    monkeypatch.setattr(website, "fetch_resource_with_retry", fetch)
    collector = website.WebsiteDocumentCollectionService(db, policy=website.WebsiteCollectionPolicy(max_page_attempts=3))
    arguments = dict(parent_task_id="parent", project_id="project",
                     target={"target_id": "target", "root_domains": ["example.org"]}, seed_urls=["https://example.org/"])
    for _ in range(2):
        result = await collector.run(**arguments)
        assert result["status"] == "partial"
    assert calls == 1
    page = (await dao.list_pages(db, crawl_task_id="parent_webdocs"))[0]
    assert page["status"] == "error"
    assert page["retryable"] is False
    assert page["http_status"] == status


@pytest.mark.asyncio
async def test_transient_http_failure_remains_retryable(monkeypatch):
    from api.services.source_documents import resources

    calls = 0

    async def fetch(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise aiohttp.ClientResponseError(request_info=SimpleNamespace(real_url="https://example.org/"),
                                              history=(), status=503, message="Busy")
        return FetchedResource("https://example.org/", b"body", "text/plain", "body.txt")

    monkeypatch.setattr(resources, "fetch_resource", fetch)
    result = await resources.fetch_resource_with_retry(None, "https://example.org/", max_bytes=1024)
    assert result.data == b"body"
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("body,expected_type", [(b"%PDF-1.7" + b"x"*72, "application/pdf"),
                                                (b"<html>" + b"x"*72, "")])
async def test_real_http_redirect_uses_signature_and_separate_size_limits(monkeypatch, body, expected_type):
    from aiohttp import web as http
    from api.services.source_documents import resources

    calls = []
    validated = []

    async def public_test_url(url):
        validated.append(url)

    async def redirect(request):
        calls.append(request.path)
        raise http.HTTPFound("/document.php")

    async def document(request):
        calls.append(request.path)
        return http.Response(body=body, content_type="text/html")

    app = http.Application()
    app.router.add_get("/start", redirect)
    app.router.add_get("/document.php", document)
    runner = http.AppRunner(app)
    await runner.setup()
    site = http.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(resources, "assert_public_http_url", public_test_url)
    try:
        async with aiohttp.ClientSession() as session:
            read = resources.fetch_resource_with_retry(
                session, f"http://127.0.0.1:{port}/start", max_bytes=32, max_attachment_bytes=128,
            )
            if expected_type:
                fetched = await read
                assert fetched.content_type == expected_type
                assert fetched.reported_content_type == "text/html"
                assert fetched.data == body
                assert fetched.url.endswith("/document.php")
            else:
                with pytest.raises(ValueError, match="限制"):
                    await read
    finally:
        await runner.cleanup()
    assert calls == ["/start", "/document.php"]
    assert len(validated) == 2
