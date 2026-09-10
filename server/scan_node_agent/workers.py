"""Capability workers with result contracts matching the main-server adapters."""

from __future__ import annotations

import asyncio
import html
import time
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import quote, urlsplit, urlunsplit

import httpx


class WorkerFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class CapabilityWorker(Protocol):
    async def execute(
        self,
        payload: dict[str, Any],
        proxy: dict[str, Any] | None,
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._inside_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "title":
            self._inside_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self._inside_title = False

    def handle_data(self, data: str) -> None:
        if self._inside_title and len("".join(self.parts)) < 500:
            self.parts.append(data)

    @property
    def title(self) -> str:
        return " ".join(html.unescape("".join(self.parts)).split())[:200]


def _proxy_url(proxy: dict[str, Any] | None) -> str | None:
    if not proxy or not proxy.get("endpoint"):
        return None
    endpoint = str(proxy["endpoint"])
    parsed = urlsplit(endpoint)
    username = str(proxy.get("username") or "")
    password = str(proxy.get("password") or "")
    if not username and not password:
        return endpoint
    credentials = quote(username, safe="")
    if password:
        credentials += ":" + quote(password, safe="")
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    return urlunsplit(
        (
            parsed.scheme,
            f"{credentials}@{hostname}:{parsed.port}",
            "",
            "",
            "",
        )
    )


def _safe_error(exc: Exception, proxy: dict[str, Any] | None) -> str:
    message = f"{type(exc).__name__}: {exc}"
    if proxy:
        endpoint = str(proxy.get("endpoint") or "")
        try:
            endpoint_host = urlsplit(endpoint).hostname or ""
        except ValueError:
            endpoint_host = ""
        sensitive = {
            str(proxy.get("username") or ""),
            str(proxy.get("password") or ""),
            endpoint,
            endpoint_host,
        }
        for value in sorted((item for item in sensitive if item), key=len, reverse=True):
            message = message.replace(value, "***")
            message = message.replace(quote(value, safe=""), "***")
    return message[:500]


def _content_accessible(probe: dict[str, Any]) -> bool:
    try:
        status = int(probe.get("status_code") or 0)
        content_length = int(probe.get("content_length") or 0)
    except (TypeError, ValueError):
        return False
    return bool(probe.get("is_alive")) and 200 <= status < 400 and content_length > 0


def _probe_score(probe: dict[str, Any]) -> tuple[int, int, int, int]:
    try:
        status = int(probe.get("status_code") or 0)
        content_length = max(0, int(probe.get("content_length") or 0))
    except (TypeError, ValueError):
        status = 0
        content_length = 0
    return (
        int(_content_accessible(probe)),
        int(bool(probe.get("is_alive")) and 200 <= status < 400),
        int(bool(probe.get("is_alive"))),
        min(content_length, 10_000_000),
    )


def _alternate_default_transport(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return ""
    if not host or parsed.scheme not in {"http", "https"}:
        return ""
    if port is not None and not (
        (parsed.scheme == "http" and port == 80)
        or (parsed.scheme == "https" and port == 443)
    ):
        return ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    scheme = "http" if parsed.scheme == "https" else "https"
    return urlunsplit((scheme, host, parsed.path, parsed.query, ""))


class HttpProbeWorker:
    async def execute(
        self,
        payload: dict[str, Any],
        proxy: dict[str, Any] | None,
    ) -> dict[str, Any]:
        urls = [str(value) for value in payload.get("urls") or []]
        timeout = max(1.0, min(float(payload.get("timeout") or 10), 60.0))
        concurrency = max(1, min(int(payload.get("concurrency") or 20), 50))
        verify_tls = bool(payload.get("verify_tls", False))
        limiter = asyncio.Semaphore(concurrency)
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout),
            verify=verify_tls,
            proxy=_proxy_url(proxy),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "Chrome/124.0 Safari/537.36"
                )
            },
        ) as client:
            initial = await self._probe_many(client, urls, limiter, proxy)
            alternates = {
                url: alternate
                for url in urls
                if not _content_accessible(initial.get(url, {}))
                and (alternate := _alternate_default_transport(url))
                and alternate not in initial
            }
            alternate_results = await self._probe_many(
                client,
                list(dict.fromkeys(alternates.values())),
                limiter,
                proxy,
            )
            selected: dict[str, dict[str, Any]] = {}
            retry_urls: dict[str, str] = {}
            for requested in urls:
                current = initial.get(
                    requested,
                    {"url": requested, "is_alive": False, "error": "missing probe result"},
                )
                alternate = alternate_results.get(alternates.get(requested, ""))
                if alternate and _probe_score(alternate) > _probe_score(current):
                    current = alternate
                selected[requested] = current
                if not current.get("is_alive"):
                    retry_urls[requested] = str(current.get("url") or requested)
            retried = await self._probe_many(
                client,
                list(dict.fromkeys(retry_urls.values())),
                limiter,
                proxy,
            )

        output: dict[str, dict[str, Any]] = {}
        for requested in urls:
            current = selected[requested]
            retry = retried.get(retry_urls.get(requested, ""))
            if retry and _probe_score(retry) > _probe_score(current):
                current = retry
            selected_url = str(current.get("url") or requested)
            output[requested] = {
                **current,
                "requested_url": requested,
                "selected_url": selected_url,
                "transport_fallback_used": selected_url != requested,
                "is_content_accessible": _content_accessible(current),
                "probe_attempts": 2 if requested in retry_urls else 1,
                "probe_recovered": bool(retry and current is retry and retry.get("is_alive")),
            }
        return {"items": output}

    async def _probe_many(
        self,
        client: httpx.AsyncClient,
        urls: list[str],
        limiter: asyncio.Semaphore,
        proxy: dict[str, Any] | None,
    ) -> dict[str, dict[str, Any]]:
        if not urls:
            return {}

        async def _one(url: str) -> tuple[str, dict[str, Any]]:
            async with limiter:
                return url, await self._probe_one(client, url, proxy)

        return dict(await asyncio.gather(*(_one(url) for url in urls)))

    @staticmethod
    async def _probe_one(
        client: httpx.AsyncClient,
        url: str,
        proxy: dict[str, Any] | None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        try:
            async with client.stream("GET", url) as response:
                chunks: list[bytes] = []
                captured = 0
                async for chunk in response.aiter_bytes():
                    if captured < 1024 * 1024:
                        part = chunk[: 1024 * 1024 - captured]
                        chunks.append(part)
                        captured += len(part)
                    if captured >= 1024 * 1024:
                        break
                body = b"".join(chunks)
                parser = _TitleParser()
                parser.feed(body.decode(response.encoding or "utf-8", errors="replace"))
                try:
                    content_length = int(response.headers.get("content-length") or len(body))
                except ValueError:
                    content_length = len(body)
                return {
                    "url": str(response.url),
                    "is_alive": True,
                    "status_code": response.status_code,
                    "title": parser.title,
                    "content_length": max(0, content_length),
                    "response_time": round(time.monotonic() - started, 3),
                    "error": None,
                }
        except Exception as exc:  # noqa: BLE001
            return {
                "url": url,
                "is_alive": False,
                "status_code": None,
                "title": "",
                "content_length": 0,
                "response_time": round(time.monotonic() - started, 3),
                "error": _safe_error(exc, proxy),
            }

    async def close(self) -> None:
        return None


class BrowserProbeWorker:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None

    async def _get_browser(self) -> Any:
        if self._browser and self._browser.is_connected():
            return self._browser
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return self._browser
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )
            return self._browser

    async def execute(
        self,
        payload: dict[str, Any],
        proxy: dict[str, Any] | None,
    ) -> dict[str, Any]:
        urls = [str(value) for value in payload.get("urls") or []]
        timeout = max(5.0, min(float(payload.get("timeout") or 30), 120.0))
        include_html = bool(payload.get("include_html", False))
        browser = await self._get_browser()
        proxy_config: dict[str, str] | None = None
        if proxy and proxy.get("endpoint"):
            proxy_config = {
                "server": str(proxy["endpoint"]).replace("socks5h://", "socks5://", 1)
            }
            if proxy.get("username"):
                proxy_config["username"] = str(proxy["username"])
            if proxy.get("password"):
                proxy_config["password"] = str(proxy["password"])

        async def _probe(url: str) -> tuple[str, dict[str, Any]]:
            started = time.monotonic()
            context: Any = None
            try:
                context = await browser.new_context(
                    ignore_https_errors=bool(payload.get("ignore_https_errors", True)),
                    proxy=proxy_config,
                )
                page = await context.new_page()
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=int(timeout * 1000),
                )
                values = await page.evaluate(
                    """() => ({
                      href: location.href,
                      title: document.title,
                      contentLength: (document.body?.innerText || '').length
                    })"""
                )
                row: dict[str, Any] = {
                    "url": url,
                    "is_alive": True,
                    "final_url": str(values.get("href") or page.url or url),
                    "status_code": response.status if response else None,
                    "title": str(values.get("title") or "")[:200],
                    "content_length": max(0, int(values.get("contentLength") or 0)),
                    "response_time": round(time.monotonic() - started, 3),
                    "error": None,
                }
                if include_html:
                    row["html"] = (await page.content())[: 512 * 1024]
                return url, row
            except Exception as exc:  # noqa: BLE001
                return url, {
                    "url": url,
                    "is_alive": False,
                    "final_url": "",
                    "title": "",
                    "content_length": 0,
                    "response_time": round(time.monotonic() - started, 3),
                    "error": _safe_error(exc, proxy),
                }
            finally:
                if context:
                    await context.close()

        rows = await asyncio.gather(*(_probe(url) for url in urls))
        return {"items": dict(rows)}

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


class WorkerRegistry:
    def __init__(self) -> None:
        self._workers: dict[str, CapabilityWorker] = {
            "http_probe": HttpProbeWorker(),
            "browser_probe": BrowserProbeWorker(),
        }

    def get(self, kind: str) -> CapabilityWorker:
        worker = self._workers.get(kind)
        if not worker:
            raise WorkerFailure("unsupported_capability", f"不支持的工作类型: {kind}", retryable=False)
        return worker

    async def close(self) -> None:
        await asyncio.gather(*(worker.close() for worker in self._workers.values()))
