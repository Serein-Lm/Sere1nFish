from __future__ import annotations

import asyncio
from typing import Any

import pytest

from api.dao import fofa_assets as assets_dao
from api.services.asset_intelligence.adapters import FofaAssetProvider, HunterAssetProvider
from api.services.asset_intelligence.contracts import (
    AssetCandidate,
    AssetIdentity,
    ProviderSearchResult,
    canonical_asset_url,
)
from api.services.asset_intelligence.service import AssetIntelligenceService


class _Provider:
    def __init__(self, name: str, candidate: AssetCandidate) -> None:
        self.name = name
        self.candidate = candidate

    async def search(self, _identity: AssetIdentity, *, size: int) -> ProviderSearchResult:
        assert size == 25
        return ProviderSearchResult(provider=self.name, candidates=[self.candidate])


class _Probe:
    async def probe(self, urls: list[str], **_kwargs: Any) -> dict[str, dict]:
        return {
            url: {
                "is_alive": True,
                "status_code": 200,
                "title": "Example",
                "response_time": 0.1,
            }
            for url in urls
        }


class _EmptyCursor:
    def sort(self, *_args: Any) -> "_EmptyCursor":
        return self

    def limit(self, *_args: Any) -> "_EmptyCursor":
        return self

    def __aiter__(self) -> "_EmptyCursor":
        return self

    async def __anext__(self) -> dict[str, Any]:
        raise StopAsyncIteration


class _QueryCollection:
    def __init__(self) -> None:
        self.queries: list[dict[str, Any]] = []

    def find(self, query: dict[str, Any], *_args: Any) -> _EmptyCursor:
        self.queries.append(query)
        return _EmptyCursor()

    async def count_documents(self, query: dict[str, Any]) -> int:
        self.queries.append(query)
        return 0


class _QueryDb:
    def __init__(self, collection: _QueryCollection) -> None:
        self.collection = collection

    def __getitem__(self, _name: str) -> _QueryCollection:
        return self.collection


def test_canonical_url_and_cross_provider_merge() -> None:
    assert canonical_asset_url("EXAMPLE.com/path", protocol="https", port="443") == "https://example.com"
    fofa = AssetCandidate(
        host="https://example.com:443/path",
        ip="1.2.3.4",
        port="443",
        protocol="https",
        sources=["fofa"],
    )
    hunter = AssetCandidate(
        link="https://example.com/other",
        port="443",
        title="Example",
        sources=["hunter"],
    )

    merged = AssetIntelligenceService._merge_candidates(
        [
            ProviderSearchResult(provider="fofa", candidates=[fofa]),
            ProviderSearchResult(provider="hunter", candidates=[hunter]),
        ]
    )

    assert len(merged) == 1
    assert merged[0].sources == ["fofa", "hunter"]
    assert merged[0].title == "Example"


def test_probe_content_length_does_not_trigger_asset_change() -> None:
    first = {
        "host": "example.com",
        "port": "443",
        "canonical_url": "https://example.com",
        "is_alive": True,
        "probe": {"is_alive": True, "status_code": 200, "content_length": 1000},
    }
    second = {
        **first,
        "probe": {"is_alive": True, "status_code": 200, "content_length": 2500},
    }

    assert assets_dao._content_hash(first) == assets_dao._content_hash(second)


@pytest.mark.asyncio
async def test_missing_provider_keys_are_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from crawler_tools import fofa_tools, hunter_tools

    async def no_key() -> str:
        return ""

    monkeypatch.setattr(fofa_tools, "get_configured_api_key", no_key)
    monkeypatch.setattr(hunter_tools, "get_configured_api_key", no_key)
    identity = AssetIdentity(
        input_name="Example",
        normalized_name="Example Ltd",
        root_domain="example.com",
    )

    fofa_result, hunter_result = await asyncio.gather(
        FofaAssetProvider().search(identity, size=10),
        HunterAssetProvider().search(identity, size=10),
    )

    assert fofa_result.errors == ["FOFA API Key 未配置"]
    assert hunter_result.errors == ["Hunter API Key 未配置"]


@pytest.mark.asyncio
async def test_asset_queries_accept_legacy_and_multi_target_fields() -> None:
    collection = _QueryCollection()
    db = _QueryDb(collection)

    await assets_dao.query_assets(db, "project_1", target_id="target_1")
    await assets_dao.count_assets(db, "project_1", target_id="target_1")

    for query in collection.queries:
        assert query["$or"] == [
            {"target_ids": "target_1"},
            {"target_id": "target_1"},
        ]


@pytest.mark.asyncio
async def test_discover_persists_once_and_returns_only_changed_alive_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services.asset_intelligence import service as module

    providers = {
        "fofa": _Provider(
            "fofa",
            AssetCandidate(
                host="https://example.com",
                ip="1.2.3.4",
                port="443",
                protocol="https",
                sources=["fofa"],
            ),
        ),
        "hunter": _Provider(
            "hunter",
            AssetCandidate(
                link="https://example.com/login",
                ip="1.2.3.4",
                port="443",
                sources=["hunter"],
            ),
        ),
    }
    monkeypatch.setattr(module.AssetProviderFactory, "available", lambda: ("fofa", "hunter"))
    monkeypatch.setattr(module.AssetProviderFactory, "create", lambda name: providers[name])

    async def upsert(_db: Any, **kwargs: Any) -> dict[str, Any]:
        assert len(kwargs["assets"]) == 1
        doc = kwargs["assets"][0]
        asset_id = assets_dao.fofa_asset_id(
            kwargs["project_id"], doc["host"], doc["ip"], doc["port"]
        )
        return {
            "inserted": 1,
            "updated": 0,
            "unchanged": 0,
            "total": 1,
            "inserted_asset_ids": [asset_id],
            "changed_asset_ids": [asset_id],
        }

    monkeypatch.setattr(module.assets_dao, "upsert_assets_batch", upsert)
    result = await AssetIntelligenceService(object(), probe=_Probe()).discover(
        identity=AssetIdentity(
            input_name="Example",
            normalized_name="Example Ltd",
            root_domain="example.com",
            target_id="tgt_1",
        ),
        project_id="project_1",
        task_id="task_1",
        provider_sizes={"fofa": 25, "hunter": 25},
    )

    assert result["discovered"] == 1
    assert result["alive"] == 1
    assert result["scan_urls"] == ["https://example.com"]
    assert set(result["providers"]) == {"fofa", "hunter"}
