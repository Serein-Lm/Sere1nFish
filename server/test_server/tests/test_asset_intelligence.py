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
from api.services.asset_intelligence.triage import (
    AssetTriageBatch,
    AssetTriageDecision,
    AssetTriageService,
)
from crawler_tools.fofa_tools import FOFA_FIELDS, _parse_results


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


def test_fofa_parser_accepts_json_object_rows() -> None:
    assets = _parse_results(
        [
            {
                "host": "https://portal.ahtv.cn",
                "ip": "203.0.113.10",
                "port": 443,
                "protocol": "https",
                "domain": "portal.ahtv.cn",
                "title": "安徽广播电视台",
                "link": "https://portal.ahtv.cn/login",
                "cert": {"domain": ["ahtv.cn", "*.ahtv.cn"]},
            }
        ],
        FOFA_FIELDS,
    )

    assert len(assets) == 1
    assert assets[0].host == "https://portal.ahtv.cn"
    assert assets[0].port == "443"
    assert assets[0].cert_domain == "ahtv.cn,*.ahtv.cn"


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
async def test_fofa_queries_are_paced_and_retry_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from crawler_tools import fofa_tools

    calls: list[str] = []

    async def configured_key() -> str:
        return "configured"

    async def search_fofa(*, search_type: str, **_kwargs: Any) -> list[Any]:
        calls.append(search_type)
        if calls == ["domain"]:
            raise RuntimeError("rate limited")
        return []

    monkeypatch.setattr(fofa_tools, "get_configured_api_key", configured_key)
    monkeypatch.setattr(fofa_tools, "search_fofa", search_fofa)
    result = await FofaAssetProvider(
        query_interval_seconds=0,
        retry_delay_seconds=0,
        max_attempts=2,
    ).search(
        AssetIdentity(
            input_name="Example",
            normalized_name="Example Ltd",
            root_domain="example.com",
        ),
        size=20,
    )

    assert calls == ["domain", "domain", "cert"]
    assert result.errors == []


@pytest.mark.asyncio
async def test_fofa_searches_all_trusted_root_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from crawler_tools import fofa_tools

    queries: list[tuple[str, str]] = []

    async def configured_key() -> str:
        return "configured"

    async def search_fofa(*, search_type: str, query: str, **_kwargs: Any) -> list[Any]:
        queries.append((search_type, query))
        return []

    monkeypatch.setattr(fofa_tools, "get_configured_api_key", configured_key)
    monkeypatch.setattr(fofa_tools, "search_fofa", search_fofa)
    result = await FofaAssetProvider(
        query_interval_seconds=0,
        retry_delay_seconds=0,
    ).search(
        AssetIdentity(
            input_name="大连商品交易所",
            normalized_name="大连商品交易所",
            root_domain="dce.com.cn",
            root_domains=["dlspjys.cn", "dce.com.cn"],
        ),
        size=20,
    )

    assert queries == [
        ("domain", "dce.com.cn"),
        ("domain", "dlspjys.cn"),
        ("cert", "dce.com.cn"),
    ]
    assert result.errors == []


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
        assert "category" not in doc
        assert "relevance_score" not in doc
        assert "reason" not in doc
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


@pytest.mark.asyncio
async def test_discover_discards_and_orders_assets_before_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services.asset_intelligence import service as module

    candidates = [
        AssetCandidate(link="https://official.example.com", title="官网", sources=["fofa"]),
        AssetCandidate(link="https://third-party.example.net", title="第三方", sources=["fofa"]),
        AssetCandidate(link="https://oa.example.com", title="OA", sources=["fofa"]),
    ]

    class _ManyProvider:
        name = "fofa"

        async def search(
            self,
            _identity: AssetIdentity,
            *,
            size: int,
        ) -> ProviderSearchResult:
            assert size == 25
            return ProviderSearchResult(provider=self.name, candidates=candidates)

    class _Triage:
        async def prioritize(self, values: list[AssetCandidate], **_kwargs: Any) -> list[AssetCandidate]:
            assert values == candidates
            return [values[2], values[0]]

    persisted_urls: list[str] = []

    async def upsert(_db: Any, **kwargs: Any) -> dict[str, Any]:
        docs = kwargs["assets"]
        persisted_urls.extend(str(doc["canonical_url"]) for doc in docs)
        changed_ids = [
            assets_dao.fofa_asset_id(
                kwargs["project_id"],
                str(doc["host"]),
                str(doc["ip"]),
                str(doc["port"]),
            )
            for doc in docs
        ]
        return {
            "inserted": len(docs),
            "updated": 0,
            "unchanged": 0,
            "total": len(docs),
            "inserted_asset_ids": changed_ids,
            "changed_asset_ids": changed_ids,
        }

    monkeypatch.setattr(module.AssetProviderFactory, "available", lambda: ("fofa",))
    monkeypatch.setattr(module.AssetProviderFactory, "create", lambda _name: _ManyProvider())
    monkeypatch.setattr(module.assets_dao, "upsert_assets_batch", upsert)

    result = await AssetIntelligenceService(
        object(),
        probe=_Probe(),
        triage=_Triage(),
    ).discover(
        identity=AssetIdentity("示例", "示例公司", "example.com"),
        project_id="project-1",
        task_id="task-1",
        provider_sizes={"fofa": 25},
    )

    expected = ["https://oa.example.com", "https://official.example.com"]
    assert persisted_urls == expected
    assert result["alive_urls"] == expected
    assert result["scan_urls"] == expected
    assert result["discovered"] == 2


@pytest.mark.asyncio
async def test_llm_triage_discards_third_party_and_prioritizes_business_system(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Sere1nGraph.graph.agents import runtime as agent_runtime
    from Sere1nGraph.graph.prompts import loader as prompt_loader

    class _Structured:
        async def ainvoke(self, _messages: Any) -> AssetTriageBatch:
            return AssetTriageBatch(
                items=[
                    AssetTriageDecision(
                        index=0,
                        category="official_public_system",
                        relevance_score=75,
                    ),
                    AssetTriageDecision(
                        index=1,
                        category="third_party_system",
                        relevance_score=10,
                    ),
                    AssetTriageDecision(
                        index=2,
                        category="business_system",
                        relevance_score=96,
                    ),
                ]
            )

    class _Llm:
        def with_structured_output(self, _schema: Any) -> _Structured:
            return _Structured()

    monkeypatch.setattr(agent_runtime, "create_llm", lambda *_args, **_kwargs: _Llm())
    monkeypatch.setattr(prompt_loader, "load_prompt", lambda _name: "asset triage")
    candidates = [
        AssetCandidate(link="https://www.example.com", title="示例公司官网"),
        AssetCandidate(link="https://generic-saas.example.net", title="通用 SaaS 登录"),
        AssetCandidate(link="https://srm.example.com", title="示例公司供应商门户"),
    ]
    result = await AssetTriageService(object()).prioritize(
        candidates,
        identity=AssetIdentity(
            input_name="示例",
            normalized_name="示例公司",
            root_domain="example.com",
        ),
        project_id="project-1",
        task_id="task-1",
    )

    assert [candidate.canonical_url for candidate in result] == [
        "https://srm.example.com",
        "https://www.example.com",
    ]


@pytest.mark.asyncio
async def test_llm_triage_failure_keeps_assets_for_safe_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Sere1nGraph.graph.agents import runtime as agent_runtime
    from Sere1nGraph.graph.prompts import loader as prompt_loader

    class _Structured:
        async def ainvoke(self, _messages: Any) -> Any:
            raise RuntimeError("model unavailable")

    class _Llm:
        def with_structured_output(self, _schema: Any) -> _Structured:
            return _Structured()

    monkeypatch.setattr(agent_runtime, "create_llm", lambda *_args, **_kwargs: _Llm())
    monkeypatch.setattr(prompt_loader, "load_prompt", lambda _name: "asset triage")
    candidates = [
        AssetCandidate(link="https://a.example.com"),
        AssetCandidate(link="https://b.example.com"),
    ]
    result = await AssetTriageService(object()).prioritize(
        candidates,
        identity=AssetIdentity("示例", "示例公司", "example.com"),
        project_id="project-1",
        task_id="task-1",
    )

    assert result == candidates
