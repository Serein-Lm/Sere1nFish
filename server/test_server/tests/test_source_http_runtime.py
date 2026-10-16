"""Concurrent public reads, policy isolation and HTTP lifecycle contracts."""
import asyncio

import aiohttp
import pytest

from api.services.source_documents import resources
from api.services.source_documents.contracts import FetchedResource


@pytest.mark.asyncio
async def test_same_read_is_coalesced_and_one_cancel_does_not_cancel_other(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def fetch(*args, **kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return FetchedResource("https://example.org/", b"body", "text/plain", "body.txt")

    monkeypatch.setattr(resources, "fetch_resource", fetch)
    async with aiohttp.ClientSession() as session:
        first = asyncio.create_task(resources.fetch_resource_with_retry(session, "https://example.org/", max_bytes=1024))
        await entered.wait()
        second = asyncio.create_task(resources.fetch_resource_with_retry(session, "https://example.org/", max_bytes=1024))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert (await second).data == b"body"
        assert calls == 1
        await resources.fetch_resource_with_retry(session, "https://example.org/", max_bytes=1024)
        assert calls == 2  # Only in-flight reads merge; the next run must check freshness.


@pytest.mark.asyncio
async def test_last_waiter_cancellation_releases_network_work(monkeypatch):
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def fetch(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(resources, "fetch_resource", fetch)
    async with aiohttp.ClientSession() as session:
        task = asyncio.create_task(resources.fetch_resource_with_retry(session, "https://example.org/", max_bytes=1024))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()


@pytest.mark.asyncio
async def test_sessions_and_request_limits_never_share_a_read(monkeypatch):
    from api.services.source_documents.http_runtime import coalesced_read_count

    release = asyncio.Event()
    calls = 0

    async def fetch(*args, **kwargs):
        nonlocal calls
        calls += 1
        await release.wait()
        return FetchedResource("https://example.org/", b"body", "text/plain", "body.txt")

    monkeypatch.setattr(resources, "fetch_resource", fetch)
    async with aiohttp.ClientSession(headers={"Authorization": "test-a"}) as a, aiohttp.ClientSession(headers={"Authorization": "test-b"}) as b:
        tasks = [asyncio.create_task(resources.fetch_resource_with_retry(session, "https://example.org/", max_bytes=limit))
                 for session, limit in [(a, 32), (b, 32), (a, 64)]]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(*tasks)
        assert calls == 3
        assert coalesced_read_count(a) == coalesced_read_count(b) == 0


@pytest.mark.asyncio
async def test_public_runtime_reuses_tcp_connections_and_closes_sessions(monkeypatch):
    from aiohttp import web
    from api.services.source_documents.http_runtime import (
        public_source_session, shutdown_source_http_runtime, start_source_http_runtime,
    )

    peers = []
    cookies = []

    async def page(request):
        peers.append(request.transport.get_extra_info("peername"))
        cookies.append(request.headers.get("Cookie"))
        response = web.Response(text="body")
        response.set_cookie("site_state", "must-not-leak")
        return response

    async def public_test_url(url):
        return None

    app = web.Application()
    app.router.add_get("/", page)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(resources, "assert_public_http_url", public_test_url)
    start_source_http_runtime()
    sessions = []
    try:
        for _ in range(2):
            async with public_source_session() as session:
                sessions.append(session)
                await resources.fetch_resource_with_retry(session, f"http://127.0.0.1:{port}/", max_bytes=1024)
        assert sessions[0] is sessions[1]
        assert peers[0] == peers[1]
        assert cookies == [None, None]
    finally:
        await shutdown_source_http_runtime()
        await runner.cleanup()
    assert all(session.closed for session in sessions)
