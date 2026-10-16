"""Bounded public HTTP sessions and cancellation-safe in-flight read sharing."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Awaitable, Callable
from weakref import WeakKeyDictionary

import aiohttp
from yarl import URL

from .contracts import FetchedResource


@dataclass
class _Read:
    task: asyncio.Task
    waiters: int = 0


_reads: WeakKeyDictionary = WeakKeyDictionary()
_runtimes: WeakKeyDictionary = WeakKeyDictionary()


def _request_identity(session: aiohttp.ClientSession, url: str, policy: tuple) -> tuple:
    proxy_environment = tuple(
        os.environ.get(name, "") for name in (
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        )
    ) if session.trust_env else ()
    cookies = tuple(sorted((key, item.value) for key, item in session.cookie_jar.filter_cookies(URL(url)).items()))
    return (url, policy, tuple(sorted(session.headers.items())), cookies, proxy_environment,
            getattr(session, "_default_auth", None), getattr(session, "_default_proxy", None),
            getattr(session, "_default_proxy_auth", None), session.timeout)


async def coalesce_read(session: aiohttp.ClientSession, url: str, *, policy: tuple,
                        fetch: Callable[[], Awaitable[FetchedResource]]) -> FetchedResource:
    # Alternate transport adapters own their own request semantics.
    if not isinstance(session, aiohttp.ClientSession):
        return await fetch()
    requests = _reads.setdefault(session, {})
    key = _request_identity(session, url, policy)
    read = requests.get(key)
    if read is None or read.task.done():
        read = _Read(asyncio.create_task(fetch(), name="source-http-read"))
        requests[key] = read
    read.waiters += 1
    try:
        return await asyncio.shield(read.task)
    finally:
        read.waiters -= 1
        if not read.waiters:
            if requests.get(key) is read:
                requests.pop(key, None)
            if not read.task.done():
                read.task.cancel()
            # Retrieve errors even if the last waiter itself was cancelled.
            await asyncio.gather(read.task, return_exceptions=True)


def coalesced_read_count(session: aiohttp.ClientSession) -> int:
    return len(_reads.get(session, {}))


class PublicSourceHTTPRuntime:
    """Own anonymous HTTP only; authenticated/browser providers remain isolated."""
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None

    def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            from .resources import create_public_fetch_ssl_context

            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120, connect=20, sock_read=90),
                connector=aiohttp.TCPConnector(limit=64, limit_per_host=8, ttl_dns_cache=180,
                                              ssl=create_public_fetch_ssl_context()),
                cookie_jar=aiohttp.DummyCookieJar(), trust_env=True,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                       "(KHTML, like Gecko) Chrome/138.0 Safari/537.36",
                         "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6"},
            )
        return self.session

    async def close(self) -> None:
        if self.session is None:
            return
        pending = list(_reads.pop(self.session, {}).values())
        for read in pending:
            read.task.cancel()
        await asyncio.gather(*(read.task for read in pending), return_exceptions=True)
        await self.session.close()


def start_source_http_runtime() -> PublicSourceHTTPRuntime:
    loop = asyncio.get_running_loop()
    runtime = _runtimes.get(loop)
    if runtime is None:
        runtime = _runtimes[loop] = PublicSourceHTTPRuntime()
    return runtime


async def shutdown_source_http_runtime() -> None:
    runtime = _runtimes.pop(asyncio.get_running_loop(), None)
    if runtime is not None:
        await runtime.close()


@asynccontextmanager
async def public_source_session():
    runtime = _runtimes.get(asyncio.get_running_loop())
    owned = runtime is None
    runtime = runtime or PublicSourceHTTPRuntime()
    try:
        yield runtime.get_session()
    finally:
        if owned:
            await runtime.close()
