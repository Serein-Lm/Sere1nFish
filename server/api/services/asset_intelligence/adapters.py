"""FOFA、Hunter 与 HTTP 探活适配器。"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import asdict
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

from crawler_tools import fofa_tools, hunter_tools
from core.async_limiter import ResizableLimiter

from .contracts import AssetCandidate, AssetIdentity, ProviderSearchResult


class _AsyncRequestGate:
    """Pace request starts across provider instances in one event loop."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._next_allowed_at = 0.0

    async def wait(self, interval_seconds: float) -> None:
        interval = max(0.0, float(interval_seconds))
        if interval <= 0:
            return
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._lock is None:
            self._loop = loop
            self._lock = asyncio.Lock()
            self._next_allowed_at = 0.0
        async with self._lock:
            delay = max(0.0, self._next_allowed_at - self._clock())
            if delay > 0:
                await self._sleep(delay)
            self._next_allowed_at = self._clock() + interval

    async def penalize(self, delay_seconds: float) -> None:
        """Push the shared gate forward after a provider-side throttle response."""
        delay = max(0.0, float(delay_seconds))
        if delay <= 0:
            return
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._lock is None:
            self._loop = loop
            self._lock = asyncio.Lock()
            self._next_allowed_at = 0.0
        async with self._lock:
            self._next_allowed_at = max(
                self._next_allowed_at,
                self._clock() + delay,
            )


_FOFA_REQUEST_GATE = _AsyncRequestGate()
_BROWSER_PROBE_LIMITER: ResizableLimiter | None = None
_BROWSER_PROBE_LIMITER_LOOP: asyncio.AbstractEventLoop | None = None


def _get_browser_probe_limiter(concurrency: int) -> ResizableLimiter:
    """Share the configured Chrome budget across all concurrent company scans."""
    global _BROWSER_PROBE_LIMITER, _BROWSER_PROBE_LIMITER_LOOP
    loop = asyncio.get_running_loop()
    safe_concurrency = max(1, int(concurrency))
    if _BROWSER_PROBE_LIMITER is None or _BROWSER_PROBE_LIMITER_LOOP is not loop:
        _BROWSER_PROBE_LIMITER = ResizableLimiter(safe_concurrency)
        _BROWSER_PROBE_LIMITER_LOOP = loop
    elif _BROWSER_PROBE_LIMITER.limit != safe_concurrency:
        _BROWSER_PROBE_LIMITER.resize(safe_concurrency)
    return _BROWSER_PROBE_LIMITER


async def _run_paced_queries(
    specs: list[tuple[str, str]],
    search: Callable[[str, str], Awaitable[Any]],
    *,
    interval_seconds: float,
    retry_delay_seconds: float,
    max_attempts: int,
) -> list[Any]:
    """同一供应商内按节奏查询，供应商级并发由上层 service 保留。"""
    responses: list[Any] = []
    for index, (search_type, query) in enumerate(specs):
        if index and interval_seconds > 0:
            await asyncio.sleep(interval_seconds)
        last_error: Exception | None = None
        for attempt in range(max(1, max_attempts)):
            try:
                responses.append(await search(search_type, query))
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt + 1 < max_attempts and retry_delay_seconds > 0:
                    await asyncio.sleep(retry_delay_seconds * (attempt + 1))
        if last_error is not None:
            responses.append(last_error)
    return responses


class FofaAssetProvider:
    name = "fofa"

    def __init__(
        self,
        *,
        query_interval_seconds: float = 4.0,
        retry_delay_seconds: float = 4.0,
        max_attempts: int = 2,
    ) -> None:
        self.query_interval_seconds = max(0.0, query_interval_seconds)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self.max_attempts = max(1, max_attempts)

    async def search(self, identity: AssetIdentity, *, size: int) -> ProviderSearchResult:
        result = ProviderSearchResult(provider=self.name)
        domains = identity.domains
        if not domains:
            result.errors.append("缺少根域名，FOFA 查询已跳过")
            return result
        api_key = await fofa_tools.get_configured_api_key()
        if not api_key:
            result.errors.append("FOFA API Key 未配置")
            return result
        specs = [("domain", domain) for domain in domains]
        specs.append(("cert", domains[0]))

        async def _search(search_type: str, query: str) -> list[Any]:
            await _FOFA_REQUEST_GATE.wait(self.query_interval_seconds)
            try:
                return await fofa_tools.search_fofa(
                    query=query,
                    search_type=search_type,
                    size=size,
                    api_key=api_key,
                    raise_on_error=True,
                )
            except Exception as exc:
                if "45012" in str(exc):
                    await _FOFA_REQUEST_GATE.penalize(12.0)
                raise

        responses = await _run_paced_queries(
            specs,
            _search,
            interval_seconds=0,
            retry_delay_seconds=self.retry_delay_seconds,
            max_attempts=self.max_attempts,
        )
        for (search_type, query), response in zip(specs, responses):
            query_label = f"fofa:{search_type}:{query}"
            result.queries.append(query_label)
            if isinstance(response, Exception):
                result.errors.append(f"{query_label}: {response}")
                continue
            for item in response:
                value = item.as_dict() if hasattr(item, "as_dict") else dict(item)
                result.candidates.append(
                    AssetCandidate(
                        host=str(value.get("host") or ""),
                        ip=str(value.get("ip") or ""),
                        port=str(value.get("port") or ""),
                        protocol=str(value.get("protocol") or ""),
                        domain=str(value.get("domain") or ""),
                        title=str(value.get("title") or ""),
                        link=str(value.get("link") or ""),
                        cert_domain=str(value.get("cert_domain") or ""),
                        sources=[self.name],
                        source_queries=[query_label],
                    )
                )
        return result


class HunterAssetProvider:
    name = "hunter"

    def __init__(
        self,
        *,
        query_interval_seconds: float = 1.0,
        retry_delay_seconds: float = 3.0,
        max_attempts: int = 2,
    ) -> None:
        self.query_interval_seconds = max(0.0, query_interval_seconds)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self.max_attempts = max(1, max_attempts)

    async def search(self, identity: AssetIdentity, *, size: int) -> ProviderSearchResult:
        result = ProviderSearchResult(provider=self.name)
        specs: list[tuple[str, str]] = []
        for domain in identity.domains:
            specs.append(("domain", domain))
        if identity.normalized_name:
            specs.append(("icp", identity.normalized_name))
        if not specs:
            result.errors.append("缺少公司名称和根域名，Hunter 查询已跳过")
            return result
        api_key = await hunter_tools.get_configured_api_key()
        if not api_key:
            result.errors.append("Hunter API Key 未配置")
            return result
        responses = await _run_paced_queries(
            specs,
            lambda search_type, query: hunter_tools.search_hunter(
                query=query,
                search_type=search_type,
                size=size,
                api_key=api_key,
                raise_on_error=True,
            ),
            interval_seconds=self.query_interval_seconds,
            retry_delay_seconds=self.retry_delay_seconds,
            max_attempts=self.max_attempts,
        )
        for (search_type, query), response in zip(specs, responses):
            query_label = f"hunter:{search_type}:{query}"
            result.queries.append(query_label)
            if isinstance(response, Exception):
                result.errors.append(f"{query_label}: {response}")
                continue
            for item in response:
                value = asdict(item) if hasattr(item, "__dataclass_fields__") else dict(item)
                result.candidates.append(
                    AssetCandidate(
                        host=str(value.get("url") or value.get("domain") or ""),
                        ip=str(value.get("ip") or ""),
                        port=str(value.get("port") or ""),
                        protocol=str(value.get("protocol") or ""),
                        domain=str(value.get("domain") or ""),
                        title=str(value.get("web_title") or ""),
                        link=str(value.get("url") or ""),
                        fingerprints=[str(item) for item in value.get("fingerprints") or []],
                        sources=[self.name],
                        source_queries=[query_label],
                    )
                )
        return result


class HttpAssetProbe:
    @staticmethod
    def _as_probe(item: Any) -> dict[str, Any]:
        return {
            "url": str(getattr(item, "url", "") or ""),
            "is_alive": bool(getattr(item, "is_alive", False)),
            "status_code": getattr(item, "status_code", None),
            "title": getattr(item, "title", None),
            "content_length": getattr(item, "content_length", None),
            "response_time": getattr(item, "response_time", None),
            "error": getattr(item, "error", None),
        }

    @staticmethod
    def _content_accessible(probe: dict[str, Any]) -> bool:
        try:
            status = int(probe.get("status_code") or 0)
            content_length = int(probe.get("content_length") or 0)
        except (TypeError, ValueError):
            return False
        return bool(probe.get("is_alive")) and 200 <= status < 400 and content_length > 0

    @classmethod
    def _probe_score(cls, probe: dict[str, Any]) -> tuple[int, int, int, int]:
        try:
            status = int(probe.get("status_code") or 0)
            content_length = max(0, int(probe.get("content_length") or 0))
        except (TypeError, ValueError):
            status = 0
            content_length = 0
        return (
            int(cls._content_accessible(probe)),
            int(bool(probe.get("is_alive")) and 200 <= status < 400),
            int(bool(probe.get("is_alive"))),
            min(content_length, 10_000_000),
        )

    @staticmethod
    def _alternate_default_transport(url: str) -> str:
        try:
            parsed = urlsplit(str(url or ""))
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
        alternate_scheme = "http" if parsed.scheme == "https" else "https"
        return urlunsplit((alternate_scheme, host, parsed.path, parsed.query, ""))

    async def probe(
        self,
        urls: list[str],
        *,
        concurrency: int,
        timeout: float,
    ) -> dict[str, dict]:
        results = await hunter_tools.probe_urls_batch(
            urls,
            concurrency=concurrency,
            timeout=timeout,
            only_alive=False,
        )
        initial = {item.url: self._as_probe(item) for item in results}
        alternates = {
            url: alternate
            for url in urls
            if not self._content_accessible(initial.get(url, {}))
            and (alternate := self._alternate_default_transport(url))
            and alternate not in initial
        }
        alternate_results: dict[str, dict[str, Any]] = {}
        if alternates:
            probed = await hunter_tools.probe_urls_batch(
                list(dict.fromkeys(alternates.values())),
                concurrency=concurrency,
                timeout=timeout,
                only_alive=False,
            )
            alternate_results = {item.url: self._as_probe(item) for item in probed}

        selected_before_retry: dict[str, dict[str, Any]] = {}
        retry_url_by_requested: dict[str, str] = {}
        for requested_url in urls:
            requested = initial.get(
                requested_url,
                {"url": requested_url, "is_alive": False, "error": "missing probe result"},
            )
            alternate = alternate_results.get(alternates.get(requested_url, ""))
            selected = requested
            if alternate and self._probe_score(alternate) > self._probe_score(requested):
                selected = alternate
            selected_before_retry[requested_url] = selected
            if not selected.get("is_alive"):
                retry_url_by_requested[requested_url] = str(
                    selected.get("url") or requested_url
                )

        retry_results: dict[str, dict[str, Any]] = {}
        if retry_url_by_requested:
            retried = await hunter_tools.probe_urls_batch(
                list(dict.fromkeys(retry_url_by_requested.values())),
                concurrency=max(1, min(int(concurrency), 24)),
                timeout=max(10.0, min(float(timeout) * 2, 30.0)),
                only_alive=False,
            )
            retry_results = {item.url: self._as_probe(item) for item in retried}

        output: dict[str, dict[str, Any]] = {}
        for requested_url in urls:
            selected = selected_before_retry[requested_url]
            retry_url = retry_url_by_requested.get(requested_url, "")
            retry_result = retry_results.get(retry_url)
            if retry_result and self._probe_score(retry_result) > self._probe_score(selected):
                selected = retry_result
            selected_url = str(selected.get("url") or requested_url)
            output[requested_url] = {
                **selected,
                "requested_url": requested_url,
                "selected_url": selected_url,
                "transport_fallback_used": selected_url != requested_url,
                "is_content_accessible": self._content_accessible(selected),
                "probe_attempts": 2 if retry_url else 1,
                "probe_recovered": bool(
                    retry_result
                    and selected is retry_result
                    and retry_result.get("is_alive")
                ),
            }
        return output


class BrowserAssetProbe:
    """Use the managed Chrome pool only for URLs unresolved by HTTP probing."""

    _NON_WEB_PORTS = frozenset({21, 22, 25, 53, 110, 143, 445, 3306, 5432, 6379})

    def __init__(self, *, provider: Any | None = None, probe_func: Any | None = None) -> None:
        self._provider = provider
        self._probe_func = probe_func

    @classmethod
    def supports(cls, url: str) -> bool:
        try:
            parsed = urlsplit(str(url or ""))
            port = parsed.port
        except ValueError:
            return False
        return bool(
            parsed.hostname
            and parsed.scheme in {"http", "https"}
            and port not in cls._NON_WEB_PORTS
        )

    async def probe(
        self,
        urls: list[str],
        *,
        concurrency: int,
        timeout: float,
    ) -> dict[str, dict[str, Any]]:
        from browser_manager.provider import get_browser_provider
        from api.services.web_capture import probe_cdp_page_access

        selected = list(dict.fromkeys(url for url in urls if self.supports(url)))
        if not selected:
            return {}
        provider = self._provider or get_browser_provider()
        probe_func = self._probe_func or probe_cdp_page_access
        limiter = _get_browser_probe_limiter(concurrency)

        async def _probe_one(url: str) -> tuple[str, dict[str, Any]]:
            digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
            task_id = f"asset_probe_{digest}"
            await limiter.acquire()
            try:
                cdp_url = ""
                try:
                    cdp_url = await provider.get_cdp_endpoint(
                        task_id=task_id,
                        purpose="url_scan",
                    )
                    if not cdp_url:
                        raise RuntimeError("无法获取 Chrome 容器")
                    result = await probe_func(
                        cdp_url,
                        url,
                        timeout_seconds=max(5.0, min(float(timeout), 30.0)),
                    )
                    return url, dict(result or {})
                except Exception as exc:  # noqa: BLE001
                    return url, {
                        "url": url,
                        "is_alive": False,
                        "error": f"{type(exc).__name__}: {exc}"[:500],
                    }
                finally:
                    if cdp_url:
                        try:
                            await provider.release_cdp_endpoint(task_id=task_id)
                        except Exception:
                            pass
            finally:
                limiter.release()

        rows = await asyncio.gather(*(_probe_one(url) for url in selected))
        return {url: result for url, result in rows}
