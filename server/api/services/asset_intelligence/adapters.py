"""FOFA、Hunter 与 HTTP 探活适配器。"""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from crawler_tools import fofa_tools, hunter_tools

from .contracts import AssetCandidate, AssetIdentity, ProviderSearchResult


class FofaAssetProvider:
    name = "fofa"

    async def search(self, identity: AssetIdentity, *, size: int) -> ProviderSearchResult:
        result = ProviderSearchResult(provider=self.name)
        if not identity.root_domain:
            result.errors.append("缺少根域名，FOFA 查询已跳过")
            return result
        api_key = await fofa_tools.get_configured_api_key()
        if not api_key:
            result.errors.append("FOFA API Key 未配置")
            return result
        specs = [("domain", identity.root_domain), ("cert", identity.root_domain)]
        responses = await asyncio.gather(
            *[
                fofa_tools.search_fofa(
                    query=query,
                    search_type=search_type,
                    size=size,
                    api_key=api_key,
                    raise_on_error=True,
                )
                for search_type, query in specs
            ],
            return_exceptions=True,
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

    async def search(self, identity: AssetIdentity, *, size: int) -> ProviderSearchResult:
        result = ProviderSearchResult(provider=self.name)
        specs: list[tuple[str, str]] = []
        if identity.root_domain:
            specs.append(("domain", identity.root_domain))
        if identity.normalized_name:
            specs.append(("icp", identity.normalized_name))
        if not specs:
            result.errors.append("缺少公司名称和根域名，Hunter 查询已跳过")
            return result
        api_key = await hunter_tools.get_configured_api_key()
        if not api_key:
            result.errors.append("Hunter API Key 未配置")
            return result
        responses = await asyncio.gather(
            *[
                hunter_tools.search_hunter(
                    query=query,
                    search_type=search_type,
                    size=size,
                    api_key=api_key,
                    raise_on_error=True,
                )
                for search_type, query in specs
            ],
            return_exceptions=True,
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
