"""FOFA、Hunter 与 HTTP 探活适配器。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from typing import Any, Awaitable, Callable

from crawler_tools import fofa_tools, hunter_tools

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


_FOFA_REQUEST_GATE = _AsyncRequestGate()


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
        query_interval_seconds: float = 2.0,
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
            return await fofa_tools.search_fofa(
                query=query,
                search_type=search_type,
                size=size,
                api_key=api_key,
                raise_on_error=True,
            )

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
        return {
            item.url: {
                "is_alive": item.is_alive,
                "status_code": item.status_code,
                "title": item.title,
                "content_length": item.content_length,
                "response_time": item.response_time,
                "error": item.error,
            }
            for item in results
        }
