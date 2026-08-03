from __future__ import annotations

import pytest

from api.models.target_research import TargetResearchPayload
from langchain_core.messages import AIMessage, ToolMessage

from api.services.target_research import (
    _eligible_related_targets,
    _extract_navigated_urls,
    _normalize_payload,
)


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


def test_target_research_rejects_unregistered_evidence_url() -> None:
    payload = _payload()
    payload["evidence"][0]["source_urls"] = ["https://unknown.example/evidence"]
    with pytest.raises(ValueError, match="sources"):
        _normalize_payload(TargetResearchPayload.model_validate(payload))


def test_target_research_requires_sources_to_be_actually_navigated() -> None:
    payload = TargetResearchPayload.model_validate(_payload())
    with pytest.raises(ValueError, match="实际导航"):
        _normalize_payload(
            payload,
            navigated_urls={"https://www.example.edu.cn/about"},
        )


def test_target_research_extracts_navigated_urls_from_agent_messages() -> None:
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
    assert _extract_navigated_urls(raw) == {"https://example.edu.cn/about"}


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
