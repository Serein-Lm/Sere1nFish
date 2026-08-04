from __future__ import annotations

import pytest

from api.models.target_research import TargetResearchPayload
from langchain_core.messages import AIMessage, ToolMessage

from api.services.target_research import (
    _build_navigation_evidence_observer,
    _candidate_scan_params,
    _eligible_related_targets,
    _extract_navigated_urls,
    _normalize_payload,
    _prepare_payload_for_validation,
    _schedule_company_scans,
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
    related = _candidate_scan_params(base, name="子单位", is_root=False)
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
    assert explicit["enable_bidding"] is True
    assert explicit["enable_wechat"] is True


def _payload(**overrides):
    value = {
        "canonical_name": "教育机构 A",
        "summary": "负责教育公共服务。",
        "industry": "教育",
        "organization_type": "事业单位",
        "root_domains": ["https://www.example.edu.cn/path"],
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
            {"target_id": "target-child", "canonical_name": "独立子单位"},
        ],
        task_id="research-task",
        requested_by="admin",
        scan_params=None,
        rescan_root=True,
    )

    assert len(task_ids) == 2
    assert [item["params"]["company_name"] for item in inserted] == [
        "主目标",
        "独立子单位",
    ]
    assert inserted[0]["params"]["enable_bidding"] is True
    assert inserted[1]["params"]["enable_bidding"] is False
    assert skipped == [{
        "target_id": "target-root",
        "reason": "与本轮其他扫描归并为同一 Target",
    }]


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


def test_target_research_accepts_successful_navigation_redirect_url() -> None:
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

    assert _extract_navigated_urls(raw) == {
        "https://example.edu.cn/about",
        "https://example.edu.cn/about/index.html",
    }


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
    observe = _build_navigation_evidence_observer(urls)

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

    assert urls == {
        "https://example.edu.cn/about",
        "https://example.edu.cn/about/index.html",
    }


def test_auto_expansion_requires_first_party_evidence_and_control_relation() -> None:
    normalized = _normalize_payload(TargetResearchPayload.model_validate(_payload()))
    assert [item["name"] for item in _eligible_related_targets(
        normalized, current_name="教育机构 A", limit=8
    )] == ["直属服务中心 B"]

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
    )] == ["直属服务中心 B"]
