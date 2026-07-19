from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from Sere1nGraph.graph.company_router.router import CompanyRouter


@pytest.mark.asyncio
async def test_company_router_retries_invalid_json_with_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import search_terms

    monkeypatch.setattr(search_terms, "get_keyword_skill_context", lambda _channels: "")
    outputs = [
        "not-json",
        json.dumps(
            {
                "company_profile": {
                    "icp_name": "大连商品交易所",
                    "colloquial_names": ["大商所"],
                    "industry": "finance",
                },
                "search_strategy": {},
                "reasoning": "交易所金融基础设施",
            },
            ensure_ascii=False,
        ),
    ]

    class _FakeLlm:
        calls = 0

        def bind(self, **_kwargs):
            return self

        async def ainvoke(self, _messages):
            self.calls += 1
            return SimpleNamespace(content=outputs.pop(0))

    llm = _FakeLlm()
    router = CompanyRouter(object())
    router._llm = llm

    result = await router.route("大连商品交易所")

    assert result.success is True
    assert result.company_profile is not None
    assert result.company_profile.icp_name == "大连商品交易所"
    assert llm.calls == 2
