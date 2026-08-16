from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from api.models.target_research import TargetResearchPayload
from langchain_core.messages import AIMessage, ToolMessage

from api.services.target_research import (
    _build_navigation_evidence_observer,
    _candidate_scan_params,
    _eligible_related_targets,
    _extract_navigated_urls,
    _filter_scan_urls_by_domains,
    _normalize_payload,
    _prepare_payload_for_validation,
    _relationship_direction,
    _schedule_company_scans,
    _shared_government_path_segments,
    _unverified_navigation_domains,
    _identity_key_matches,
    _with_target_identity_default,
    run_target_research,
)


def test_related_target_scan_disables_costly_mobile_and_bidding_by_default() -> None:
    base = {
        "enable_asset_discovery": True,
        "enable_url_scan": True,
        "enable_bidding": True,
        "enable_wechat": True,
        "bidding_max_records": 5,
    }

    root = _candidate_scan_params(base, name="主目标", is_root=True)
    related = _candidate_scan_params(
        base,
        name="子单位",
        target_id="target-child",
        is_root=False,
    )
    explicit = _candidate_scan_params(
        {
            **base,
            "enable_subsidiary_bidding": True,
            "enable_subsidiary_wechat": True,
        },
        name="显式开启的子单位",
        is_root=False,
    )

    assert root["enable_bidding"] is True
    assert root["enable_wechat"] is True
    assert related["enable_bidding"] is False
    assert related["enable_wechat"] is False
    assert related["enable_url_scan"] is True
    assert related["target_id"] == "target-child"
    assert explicit["enable_bidding"] is True
    assert explicit["enable_wechat"] is True


def _payload(**overrides):
    value = {
        "canonical_name": "教育机构 A",
        "summary": "负责教育公共服务。",
        "industry": "教育",
        "organization_type": "事业单位",
        "root_domains": ["https://www.example.edu.cn/path"],
        "web_scan_urls": ["https://www.example.edu.cn/about#intro"],
        "sources": [
            {
                "title": "机构官网",
                "url": "https://www.example.edu.cn/about#intro",
                "source_type": "official",
            },
            {
                "title": "主管部门",
                "url": "https://gov.example.cn/unit",
                "source_type": "government",
            },
        ],
        "evidence": [
            {
                "dimension": "identity",
                "finding": "主管部门确认机构身份",
                "confidence": 0.95,
                "source_urls": ["https://gov.example.cn/unit"],
            },
            {
                "dimension": "domain",
                "finding": "官网使用 example.edu.cn",
                "confidence": 0.9,
                "source_urls": ["https://www.example.edu.cn/about"],
            },
        ],
        "related_targets": [
            {
                "name": "直属服务中心 B",
                "relation_type": "service_unit",
                "relationship_summary": "官网列为直属服务单位",
                "confidence": 0.92,
                "source_urls": ["https://www.example.edu.cn/about"],
                "web_scan_urls": ["https://www.example.edu.cn/about"],
                "scan_priority": 90,
                "should_scan": True,
            }
        ],
        "confidence": 0.92,
    }
    value.update(overrides)
    return value


def test_target_research_normalizes_domains_and_traceable_urls() -> None:
    normalized = _normalize_payload(TargetResearchPayload.model_validate(_payload()))
    assert normalized["root_domains"] == ["example.edu.cn"]
    assert normalized["sources"][0]["url"] == "https://www.example.edu.cn/about"
    assert normalized["evidence"][1]["source_urls"] == [
        "https://www.example.edu.cn/about"
    ]
    assert normalized["web_scan_urls"] == ["https://www.example.edu.cn/about"]
    assert normalized["related_targets"][0]["web_scan_urls"] == [
        "https://www.example.edu.cn/about"
    ]


def test_target_research_scan_urls_stay_under_verified_domains() -> None:
    assert _filter_scan_urls_by_domains(
        [
            "https://service.example.edu.cn/login",
            "https://example.edu.cn/portal",
            "https://vendor.example.com/product",
        ],
        ["example.edu.cn"],
    ) == [
        "https://service.example.edu.cn/login",
        "https://example.edu.cn/portal",
    ]


def test_shared_government_portal_scan_is_path_scoped() -> None:
    urls = [
        "https://wjw.anqing.gov.cn/public/4018278/2006741581.html",
        "https://wjw.anqing.gov.cn/public/column/4018278?action=list",
    ]

    assert _shared_government_path_segments(urls, ["anqing.gov.cn"]) == [
        "4018278"
    ]

    params = _candidate_scan_params(
        {"enable_asset_discovery": True, "enable_url_scan": True},
        name="安庆市疾病预防控制中心",
        is_root=True,
        seed_urls=urls,
        root_domains=["anqing.gov.cn"],
    )

    assert params["website_required_path_segments"] == ["4018278"]
    assert params["enable_asset_discovery"] is False


def test_dedicated_official_homepage_does_not_require_identity_in_title() -> None:
    payload = _payload(
        canonical_name="中国疾病预防控制中心麻风病控制中心",
        aliases=["麻风病控制中心"],
        root_domains=["nclepc.cn"],
        web_scan_urls=["https://www.nclepc.cn/"],
        sources=[
            {
                "title": "中心概况 - 组织机构",
                "url": "https://www.nclepc.cn/",
                "source_type": "official",
            },
            {
                "title": "挂靠单位",
                "url": "https://www.chinacdc.cn/jgxx/gkdw/",
                "source_type": "official",
            },
        ],
        evidence=[
            {
                "dimension": "identity",
                "finding": "中心官网和中国疾控中心官网共同确认机构身份",
                "confidence": 0.95,
                "source_urls": [
                    "https://www.nclepc.cn/",
                    "https://www.chinacdc.cn/jgxx/gkdw/",
                ],
            }
        ],
        related_targets=[],
    )

    normalized = _normalize_payload(TargetResearchPayload.model_validate(payload))

    assert normalized["root_domains"] == ["nclepc.cn"]
    assert normalized["web_scan_urls"] == ["https://www.nclepc.cn/"]


def test_generic_government_homepage_is_not_target_scan_scope() -> None:
    payload = _payload(
        canonical_name="梅州市疾病预防控制中心",
        aliases=["梅州疾控"],
        root_domains=["meizhou.gov.cn"],
        web_scan_urls=["https://www.meizhou.gov.cn/"],
        sources=[
            {
                "title": "梅州市人民政府门户网站",
                "url": "https://www.meizhou.gov.cn/",
                "source_type": "government",
            },
            {
                "title": "广东省事业单位名录",
                "url": "https://www.gd.gov.cn/directory/meizhou-cdc",
                "source_type": "government",
            },
        ],
        evidence=[
            {
                "dimension": "identity",
                "finding": "省级名录确认机构身份",
                "confidence": 0.9,
                "source_urls": ["https://www.gd.gov.cn/directory/meizhou-cdc"],
            }
        ],
        related_targets=[],
    )

    normalized = _normalize_payload(TargetResearchPayload.model_validate(payload))

    assert normalized["root_domains"] == []
    assert normalized["web_scan_urls"] == []


def test_target_identity_accepts_official_qualifier_and_fills_missing_name() -> None:
    existing = "江苏省疾病预防控制中心"
    qualified = "江苏省疾病预防控制中心江苏省预防医学科学院"

    assert _identity_key_matches(qualified, existing) is True
    assert _identity_key_matches("南京市疾病预防控制中心", existing) is False
    assert _with_target_identity_default(
        {"canonical_name": "", "summary": "已核验"},
        canonical_name=existing,
    )["canonical_name"] == existing


def test_target_research_drops_unregistered_evidence_url() -> None:
    payload = _payload()
    payload["evidence"][0]["source_urls"] = ["https://unknown.example/evidence"]
    normalized = _normalize_payload(TargetResearchPayload.model_validate(payload))
    assert [item["dimension"] for item in normalized["evidence"]] == ["domain"]


def test_target_research_prevalidation_drops_invalid_nested_evidence() -> None:
    payload = _payload()
    payload["evidence"].append({
        "dimension": "invalid",
        "finding": "引用了未打开页面",
        "confidence": 0.8,
        "source_urls": ["https://unknown.example/evidence"],
    })

    prepared = _prepare_payload_for_validation(
        payload,
        navigated_urls={
            "https://www.example.edu.cn/about",
            "https://gov.example.cn/unit",
        },
    )
    validated = TargetResearchPayload.model_validate(prepared)

    assert [item.dimension for item in validated.evidence] == ["identity", "domain"]


def test_target_research_requires_sources_to_be_actually_navigated() -> None:
    payload = TargetResearchPayload.model_validate(_payload())
    with pytest.raises(ValueError, match="实际导航"):
        _normalize_payload(
            payload,
            navigated_urls={"https://www.example.edu.cn/about"},
        )


def test_target_research_drops_search_result_source() -> None:
    payload = _payload()
    payload["sources"].append({
        "title": "监管页面",
        "url": "https://regulator.example.cn/entity",
        "source_type": "regulator",
    })
    payload["sources"][0]["url"] = "https://cn.bing.com/search?q=education"
    normalized = _normalize_payload(TargetResearchPayload.model_validate(payload))
    assert len(normalized["sources"]) == 2
    assert all("bing.com/search" not in item["url"] for item in normalized["sources"])


@pytest.mark.asyncio
async def test_company_scan_dispatch_deduplicates_same_target_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inserted: list[dict] = []

    async def latest_params(*_args, **_kwargs):
        return {"enable_url_scan": True, "enable_bidding": True}

    async def no_existing(*_args, **_kwargs):
        return None

    async def insert_tasks(_db, documents):
        inserted.extend(documents)

    def close_background(coro, **_kwargs):
        coro.close()
        return None

    monkeypatch.setattr(
        "api.services.target_research._latest_scan_params", latest_params
    )
    monkeypatch.setattr(
        "api.services.target_research.tasks_dao.find_latest_matching_task",
        no_existing,
    )
    monkeypatch.setattr(
        "api.services.target_research.tasks_dao.insert_tasks", insert_tasks
    )
    monkeypatch.setattr(
        "api.services.target_research.spawn_background", close_background
    )

    task_ids, skipped = await _schedule_company_scans(
        object(),
        project_id="project-1",
        root_target={
            "target_id": "target-root",
            "canonical_name": "主目标",
            "latest_research_id": "research-1",
        },
        expanded_targets=[
            {"target_id": "target-root", "canonical_name": "主目标平台"},
            {
                "target_id": "target-child",
                "canonical_name": "独立子单位",
                "root_domains": ["child.example.cn"],
                "research_relation": {
                    "web_scan_urls": ["https://child.example.cn/service"]
                },
            },
        ],
        task_id="research-task",
        requested_by="admin",
        scan_params=None,
        rescan_root=True,
        root_seed_urls=["https://root.example.cn/portal"],
    )

    assert len(task_ids) == 2
    assert [item["params"]["company_name"] for item in inserted] == [
        "主目标",
        "独立子单位",
    ]
    assert [item["params"]["target_id"] for item in inserted] == [
        "target-root",
        "target-child",
    ]
    assert inserted[0]["params"]["enable_bidding"] is True
    assert inserted[0]["params"]["urls"] == ["https://root.example.cn/portal"]
    assert inserted[1]["params"]["enable_bidding"] is False
    assert inserted[1]["params"]["urls"] == ["https://child.example.cn/service"]
    assert skipped == [{
        "target_id": "target-root",
        "reason": "与本轮其他扫描归并为同一 Target",
    }]


@pytest.mark.asyncio
async def test_target_research_hot_swaps_after_browser_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import target_research as research_dao
    from api.dao import targets as targets_dao
    from api.services import target_research
    from browser_manager import provider as browser_provider
    from Sere1nGraph.graph.agents import factory as agent_factory
    from Sere1nGraph.graph.agents import runtime as agent_runtime
    from Sere1nGraph.graph.prompts import loader as prompt_loader

    target = {
        "target_id": "target-1",
        "canonical_name": "教育机构 A",
        "identity_aliases": ["教育机构 A"],
        "root_domains": ["example.edu.cn"],
        "aliases": ["教育机构 A"],
    }

    class Provider:
        def __init__(self) -> None:
            self.hot_swaps = 0
            self.releases = 0

        async def get_cdp_endpoint(self, **_kwargs):
            return "ws://chrome-first"

        async def hot_swap_container(self, **_kwargs):
            self.hot_swaps += 1
            return "ws://chrome-second"

        async def release_cdp_endpoint(self, *_args, **_kwargs):
            self.releases += 1

    provider = Provider()
    agent_creations = 0
    linked_targets: list[dict] = []
    persisted_profile: dict = {}

    async def create_agent(*_args, **_kwargs):
        nonlocal agent_creations
        agent_creations += 1
        attempt = agent_creations

        async def agent(_request):
            if attempt == 1:
                raise RuntimeError(
                    "Tool 'navigate_page' error: TimeoutError: 调用超过 60s"
                )
            return {
                "messages": [
                        ToolMessage(
                            name="navigate_page",
                            tool_call_id="official",
                        content=(
                            "Successfully navigated to "
                                "https://www.example.edu.cn/about."
                            ),
                        ),
                        ToolMessage(
                            name="evaluate_script",
                            tool_call_id="official-read",
                            content=(
                                "Script ran on page and returned:\n"
                                '{"url":"https://www.example.edu.cn/about",'
                                '"text":"official body"}'
                            ),
                        ),
                        ToolMessage(
                            name="navigate_page",
                            tool_call_id="government",
                        content=(
                            "Successfully navigated to "
                                "https://gov.example.cn/unit."
                            ),
                        ),
                        ToolMessage(
                            name="evaluate_script",
                            tool_call_id="government-read",
                            content=(
                                "Script ran on page and returned:\n"
                                '{"url":"https://gov.example.cn/unit",'
                                '"text":"government body"}'
                            ),
                        ),
                ]
            }

        return agent

    async def extract(_raw, _config, **kwargs):
        payload = _payload(related_targets=[])
        kwargs["validator"](payload)
        return payload

    async def get_target(*_args, **_kwargs):
        return dict(target)

    async def get_relation(*_args, **_kwargs):
        return {
            "target_id": "target-1",
            "target_name": "教育机构 A",
            "relation_depth": 0,
        }

    async def merge(*_args, **_kwargs):
        return dict(target)

    async def noop(*_args, **_kwargs):
        return None

    async def link_target(*_args, **kwargs):
        linked_targets.append(kwargs)
        return {}

    async def save(_db, **kwargs):
        return {
            **kwargs["document"],
            "research_id": "research-1",
        }

    async def enrich(*_args, **_kwargs):
        return dict(target)

    async def sync_relationships(*_args, **_kwargs):
        return []

    async def persist_scan_profile(_db, **kwargs):
        persisted_profile.update(kwargs)
        return {
            **kwargs["target"],
            "scan_profile": kwargs["profile"],
        }

    monkeypatch.setattr(browser_provider, "get_browser_provider", lambda: provider)
    monkeypatch.setattr(agent_factory, "create_target_research_agent", create_agent)
    monkeypatch.setattr(agent_runtime, "extract_with_retry", extract)
    monkeypatch.setattr(prompt_loader, "load_prompt", lambda _slug: "prompt")
    monkeypatch.setattr(targets_dao, "get_target", get_target)
    monkeypatch.setattr(targets_dao, "get_project_target", get_relation)
    monkeypatch.setattr(targets_dao, "merge_target_research_identity", merge)
    monkeypatch.setattr(targets_dao, "link_project_target", link_target)
    monkeypatch.setattr(targets_dao, "enrich_target_from_research", enrich)
    monkeypatch.setattr(
        target_research,
        "persist_target_scan_profile",
        persist_scan_profile,
    )
    monkeypatch.setattr(research_dao, "save_research", save)
    monkeypatch.setattr(
        target_research.relationships_dao,
        "sync_research_relationships",
        sync_relationships,
    )
    monkeypatch.setattr(target_research, "update_task_stage", noop)
    monkeypatch.setattr(target_research, "observation_context", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(target_research, "obs_log", lambda *_args, **_kwargs: None)

    result = await run_target_research(
        object(),
        SimpleNamespace(
            mcp_servers={"chrome-devtools": SimpleNamespace(args=[])}
        ),
        task_id="task-1",
        project_id="project-1",
        target_id="target-1",
        scan_discovered_targets=False,
        rescan_root=False,
    )

    assert result["research_id"] == "research-1"
    assert result["source_count"] == 2
    assert provider.hot_swaps == 1
    assert provider.releases == 1
    assert agent_creations == 2
    assert persisted_profile["profile"]["search_aliases"] == ["教育机构 A"]
    assert persisted_profile["additional_search_terms"] == []
    assert linked_targets[0]["objectives"] == ["机构公开情报深研与高置信关联 Target 扩展"]


def test_target_research_rejects_unconfirmed_navigation_tool_call() -> None:
    raw = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "navigate_page",
                    "args": {"type": "url", "url": "https://example.edu.cn/about#intro"},
                    "id": "call_1",
                    "type": "tool_call",
                }],
            )
        ]
    }
    assert _extract_navigated_urls(raw) == set()


def test_target_research_requires_dom_read_after_successful_navigation() -> None:
    raw = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "navigate_page",
                    "args": {"url": "https://example.edu.cn/about"},
                    "id": "call_1",
                    "type": "tool_call",
                }],
            ),
            ToolMessage(
                name="navigate_page",
                tool_call_id="call_1",
                content=[{
                    "type": "text",
                    "text": (
                        "Successfully navigated to https://example.edu.cn/about.\n"
                        "## Pages\n"
                        "1: About (https://example.edu.cn/about/index.html) [selected]\n"
                        "2: Candidate (https://third-party.example/link)"
                    ),
                }],
            ),
        ]
    }

    assert _extract_navigated_urls(raw) == set()

    raw["messages"].append(
        ToolMessage(
            name="take_snapshot",
            tool_call_id="call_2",
            content=(
                "## Latest page snapshot\n"
                'uid=1_0 RootWebArea "About" '
                'url="https://example.edu.cn/about/index.html"\n'
                '  uid=1_1 StaticText "Verified body"'
            ),
        )
    )
    assert _extract_navigated_urls(raw) == {
        "https://example.edu.cn/about/index.html",
    }


def test_target_research_rejects_error_page_snapshot() -> None:
    raw = {
        "messages": [
            ToolMessage(
                name="navigate_page",
                tool_call_id="call_1",
                content=(
                    "Successfully navigated to https://example.gov.cn/missing.\n"
                    "## Pages\n"
                    "1: 404 Not Found (https://example.gov.cn/missing) [selected]"
                ),
            ),
            ToolMessage(
                name="take_snapshot",
                tool_call_id="call_2",
                content=(
                    "## Latest page snapshot\n"
                    'uid=1_0 RootWebArea "404 Not Found" '
                    'url="https://example.gov.cn/missing"\n'
                    '  uid=1_1 StaticText "Not Found"'
                ),
            ),
        ]
    }

    assert _extract_navigated_urls(raw) == set()


def test_target_research_rejects_selected_url_after_navigation_timeout() -> None:
    raw = {
        "messages": [
            ToolMessage(
                name="navigate_page",
                tool_call_id="call_1",
                content=(
                    "Unable to navigate in the selected page: Navigation timeout.\n"
                    "## Pages\n"
                    "1: Stale (https://example.edu.cn/stale) [selected]"
                ),
            )
        ]
    }

    assert _extract_navigated_urls(raw) == set()


def test_target_research_accepts_timed_out_navigation_after_dom_read() -> None:
    raw = {
        "messages": [
            ToolMessage(
                name="navigate_page",
                tool_call_id="call_1",
                content=(
                    "Unable to navigate in the selected page: Navigation timeout.\n"
                    "## Pages\n"
                    "1: Current (https://example.edu.cn/current) [selected]"
                ),
            ),
            ToolMessage(
                name="evaluate_script",
                tool_call_id="call_2",
                content="Script ran on page and returned:\n{\"title\": \"Current\"}",
            ),
        ]
    }

    assert _extract_navigated_urls(raw) == {"https://example.edu.cn/current"}


def test_navigation_evidence_observer_survives_message_compaction() -> None:
    urls: set[str] = set()
    attempted: list[str] = []
    observe = _build_navigation_evidence_observer(urls, attempted)

    observe(
        "navigate_page",
        ([{
            "type": "text",
            "text": (
                "Successfully navigated to https://example.edu.cn/about.\n"
                "## Pages\n"
                "1: About (https://example.edu.cn/about/index.html) [selected]"
            ),
        }], {"raw": "artifact"}),
    )
    assert urls == set()
    assert attempted == ["https://example.edu.cn/about/index.html"]

    observe(
        "take_snapshot",
        (
            "## Latest page snapshot\n"
            'uid=1_0 RootWebArea "About" '
            'url="https://example.edu.cn/about/index.html"\n'
            '  uid=1_1 StaticText "Verified body"'
        ),
    )

    assert urls == {
        "https://example.edu.cn/about/index.html",
    }


def test_unverified_navigation_domains_exclude_successfully_read_hosts() -> None:
    assert _unverified_navigation_domains(
        [
            "https://official.example.cn/about",
            "https://directory.example.com/item/1",
            "https://directory.example.com/item/2",
        ],
        {"https://official.example.cn/about"},
    ) == ["directory.example.com"]


def test_slim_navigation_requires_bounded_evaluation() -> None:
    raw = {
        "messages": [
            ToolMessage(
                name="navigate",
                tool_call_id="call_1",
                content="Navigated to https://example.edu.cn/about.",
            ),
            ToolMessage(
                name="evaluate",
                tool_call_id="call_2",
                content=(
                    '{"url":"https://example.edu.cn/about",'
                    '"title":"About","text":"verified body","links":[]}'
                ),
            ),
        ]
    }

    assert _extract_navigated_urls(raw) == {
        "https://example.edu.cn/about",
    }


def test_slim_evaluation_records_url_after_navigation_timeout() -> None:
    raw = {
        "messages": [
            ToolMessage(
                name="navigate",
                tool_call_id="call_1",
                content="Navigation timeout of 30000 ms exceeded",
            ),
            ToolMessage(
                name="evaluate",
                tool_call_id="call_2",
                content=(
                    '{"url":"https://example.edu.cn/loaded",'
                    '"title":"Loaded","text":"verified body","links":[]}'
                ),
            ),
        ]
    }

    assert _extract_navigated_urls(raw) == {
        "https://example.edu.cn/loaded",
    }


def test_auto_expansion_requires_first_party_evidence_and_control_relation() -> None:
    normalized = _normalize_payload(TargetResearchPayload.model_validate(_payload()))
    assert [item["name"] for item in _eligible_related_targets(
        normalized, current_name="教育机构 A", limit=8
    )] == ["直属服务中心 B"]

    normalized["related_targets"].append({
        "name": "主管部门 D",
        "relation_type": "parent_organization",
        "relationship_summary": "政府机构设置页确认直接主管",
        "confidence": 0.98,
        "source_urls": ["https://gov.example.cn/unit"],
        "scan_priority": 95,
        "should_scan": True,
    })
    assert [item["name"] for item in _eligible_related_targets(
        normalized, current_name="教育机构 A", limit=8
    )] == ["主管部门 D", "直属服务中心 B"]
    assert _relationship_direction("parent_organization") == "upstream"
    assert _relationship_direction("affiliated_unit") == "lateral"
    assert _relationship_direction("service_unit") == "downstream"

    normalized["related_targets"].append({
        "name": "第三方供应商 C",
        "relation_type": "vendor",
        "relationship_summary": "提供产品",
        "confidence": 0.99,
        "source_urls": ["https://gov.example.cn/unit"],
        "scan_priority": 100,
        "should_scan": True,
    })
    assert [item["name"] for item in _eligible_related_targets(
        normalized, current_name="教育机构 A", limit=8
    )] == ["主管部门 D", "直属服务中心 B"]
