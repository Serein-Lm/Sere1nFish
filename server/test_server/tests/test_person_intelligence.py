from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.dao.person_intelligence import intelligence_id, merge_intelligence
from api.models.person_intelligence import PersonIntelligencePayload
from api.services.person_intelligence import _decorate_signal_status, _normalize_payload
from Sere1nGraph.graph.workflow.hub import (
    _direct_url_classifications,
    _osint_classifications,
    _persona_collection_classifications,
)
from Sere1nGraph.graph.agents.streaming import _is_internal_summary


def _payload(**overrides):
    value = {
        "name": "Yan Lu",
        "organization": "Chinese CDC",
        "position": "Researcher",
        "summary": "Public professional profile.",
        "sources": [
            {
                "title": "Official profile",
                "url": "https://example.org/people/yan-lu#profile",
                "summary": "Institutional biography",
                "source_type": "official",
            }
        ],
        "evidence": [
            {
                "dimension": "position",
                "finding": "Listed as a researcher.",
                "evidence_type": "fact",
                "confidence": 0.9,
                "source_urls": ["https://example.org/people/yan-lu"],
            }
        ],
        "confidence": 0.9,
    }
    value.update(overrides)
    return value


def test_identity_is_stable_and_organization_scoped() -> None:
    assert intelligence_id("Yan Lu", "Chinese CDC") == intelligence_id(
        " Yan Lu ", "Chinese-CDC"
    )
    assert intelligence_id("Yan Lu", "Chinese CDC") != intelligence_id(
        "Yan Lu", "Another Institute"
    )


def test_normalize_payload_requires_traceable_sources() -> None:
    model = PersonIntelligencePayload.model_validate(_payload())
    normalized = _normalize_payload(model)
    assert normalized["sources"][0]["url"] == "https://example.org/people/yan-lu"
    assert normalized["evidence"][0]["source_urls"] == [
        "https://example.org/people/yan-lu"
    ]

    invalid = _payload(
        public_contacts=[
            {
                "channel": "email",
                "value": "public@example.org",
                "source_url": "https://unverified.example.org/contact",
            }
        ]
    )
    with pytest.raises(ValueError, match="公开联系方式必须引用"):
        _normalize_payload(PersonIntelligencePayload.model_validate(invalid))

    normalized_update = _normalize_payload(
        PersonIntelligencePayload.model_validate(invalid),
        allowed_source_urls={"https://unverified.example.org/contact"},
    )
    assert normalized_update["public_contacts"][0]["source_url"] == (
        "https://unverified.example.org/contact"
    )

    invalid_signal = _payload(
        context_signals=[{
            "title": "Conference",
            "observed_at": "2026-08-03",
            "source_urls": ["https://unverified.example.org/event"],
        }]
    )
    with pytest.raises(ValueError, match="时间与热点信号必须引用"):
        _normalize_payload(PersonIntelligencePayload.model_validate(invalid_signal))

    expired_signal = _payload(
        context_signals=[{
            "title": "Expired conference",
            "observed_at": "2020-01-01",
            "expires_at": "2020-02-01",
            "source_urls": ["https://example.org/people/yan-lu"],
        }]
    )
    with pytest.raises(ValueError, match="已经过期"):
        _normalize_payload(PersonIntelligencePayload.model_validate(expired_signal))


def test_merge_preserves_and_upgrades_evidence() -> None:
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    existing = {
        "intel_id": "poi_1",
        "name": "Yan Lu",
        "organization": "Chinese CDC",
        "project_ids": ["project-a"],
        "sources": [{"url": "https://example.org/a", "title": "Old"}],
        "evidence": [
            {
                "dimension": "position",
                "finding": "Researcher",
                "evidence_type": "fact",
                "confidence": 0.7,
                "source_urls": ["https://example.org/a"],
            }
        ],
        "profile": {"priorities": ["research"]},
        "profile_version": 2,
        "research_rounds": 2,
    }
    patch = {
        "intel_id": "poi_1",
        "name": "Yan Lu",
        "organization": "Chinese CDC",
        "project_ids": ["project-b"],
        "sources": [
            {"url": "https://example.org/a", "title": "Updated"},
            {"url": "https://example.org/b", "title": "Second"},
        ],
        "evidence": [
            {
                "dimension": "position",
                "finding": "Researcher",
                "evidence_type": "fact",
                "confidence": 0.95,
                "source_urls": ["https://example.org/a", "https://example.org/b"],
            }
        ],
        "profile": {"priorities": ["public health"]},
        "context_signals": [{
            "title": "Public health conference",
            "signal_type": "conference",
            "observed_at": "2026-08-03",
            "source_urls": ["https://example.org/b"],
        }],
        "recommended_personas": [{
            "person_id": "person_1",
            "name": "Fictional researcher",
            "score": 0.8,
        }],
        "scenarios": [{
            "scenario_id": "scenario_conference",
            "title": "Conference follow-up",
            "objective": "Discuss research collaboration",
            "source_urls": ["https://example.org/b"],
            "persona_ids": ["person_1"],
        }],
        "sample_copywritings": [{
            "title": "Follow-up",
            "content": "Discuss the published research.",
            "scenario_ids": ["scenario_conference"],
            "source_urls": ["https://example.org/b"],
        }],
    }
    merged = merge_intelligence(existing, patch, now=now)
    assert merged["project_ids"] == ["project-a", "project-b"]
    assert len(merged["sources"]) == 2
    assert len(merged["evidence"]) == 1
    assert merged["sources"][0]["title"] == "Updated"
    assert merged["evidence"][0]["confidence"] == 0.95
    assert merged["evidence"][0]["source_urls"] == [
        "https://example.org/a",
        "https://example.org/b",
    ]
    assert merged["profile"]["priorities"] == ["research", "public health"]
    assert merged["profile_version"] == 3
    assert merged["research_rounds"] == 3
    assert merged["last_researched_at"] == now
    assert merged["signal_count"] == 1
    assert merged["scenario_count"] == 1
    assert merged["copywriting_count"] == 1
    assert merged["scenarios"][0]["scenario_id"] == "scenario_conference"
    node_ids = {node["node_id"] for node in merged["lineage"]["nodes"]}
    assert "persona:person_1" in node_ids
    assert "scenario:scenario_conference" in node_ids
    assert any(
        edge["source"] == "persona:person_1"
        and edge["target"] == "scenario:scenario_conference"
        and edge["relation"] == "informs"
        for edge in merged["lineage"]["edges"]
    )


def test_hub_routes_public_research_to_osint_and_keeps_url_fast_path() -> None:
    query = "请在公开网络深入了解 Yan Lu，并基于核验信息生成沟通话术"
    assert _osint_classifications(query) == [
        {"source": "osint", "query": query, "requires_tools": True}
    ]

    url_query = "读取 https://example.org/profile 并生成话术"
    assert _direct_url_classifications(url_query) == [
        {"source": "payload", "query": url_query, "requires_tools": True}
    ]

    persona_query = "主动收集一名适合高校科研合作场景的虚构人设"
    assert _persona_collection_classifications(persona_query) == [
        {"source": "persona", "query": persona_query, "requires_tools": True}
    ]

    history_only = (
        "历史消息：请主动收集虚构人设\n"
        "【本轮用户请求】\n"
        "只查询现有人设的数量"
    )
    assert _persona_collection_classifications(history_only) is None


def test_signal_ids_merge_updates_and_read_status_is_derived() -> None:
    merged = merge_intelligence(
        {
            "intel_id": "poi_signal",
            "name": "Example",
            "organization": "Example Org",
            "context_signals": [{
                "signal_id": "sig_same",
                "title": "Research event",
                "observed_at": "2025-01-01",
                "expires_at": "2025-12-31",
            }],
        },
        {
            "intel_id": "poi_signal",
            "name": "Example",
            "organization": "Example Org",
            "context_signals": [{
                "signal_id": "sig_same",
                "title": "Research event updated",
                "observed_at": "2026-08-03",
                "expires_at": "2099-12-31",
            }],
        },
    )
    assert merged["signal_count"] == 1
    assert merged["context_signals"][0]["title"] == "Research event updated"

    decorated = _decorate_signal_status({
        "context_signals": [
            {"signal_id": "past", "expires_at": "2020-01-01"},
            {"signal_id": "future", "expires_at": "2099-12-31"},
        ]
    })
    assert [signal["status"] for signal in decorated["context_signals"]] == [
        "expired",
        "active",
    ]
    assert decorated["active_signal_count"] == 1


def test_osint_intent_ignores_old_conversation_wording() -> None:
    query = (
        "历史消息：请做背景调查\n"
        "【本轮用户请求】\n"
        "只把已有项目数量告诉我"
    )
    assert _osint_classifications(query) is None


def test_internal_summarization_stream_is_hidden() -> None:
    assert _is_internal_summary({"lc_source": "summarization"}) is True
    assert _is_internal_summary({"metadata": {"lc_source": "summarization"}}) is True
    assert _is_internal_summary({"langgraph_node": "model"}) is False


def test_active_persona_tool_exposes_strict_profile_schema() -> None:
    from Sere1nGraph.graph.tools.catalog import get_hub_tool_groups
    from Sere1nGraph.graph.tools.persona_collection_tools import save_researched_persona
    from Sere1nGraph.graph.tools.osint_tools import save_person_intelligence

    schema = save_researched_persona.args_schema.model_json_schema()
    assert "profile" in schema["properties"]
    profile_schema = schema["properties"]["profile"]
    assert "$ref" in profile_schema or "properties" in profile_schema

    intelligence_schema = save_person_intelligence.args_schema.model_json_schema()
    assert "sources" in intelligence_schema["properties"]
    assert "recommended_personas" in intelligence_schema["properties"]
    assert "scenarios" in intelligence_schema["properties"]
    assert save_researched_persona in get_hub_tool_groups()["persona"]


def test_osint_write_tool_accepts_structured_and_legacy_json_values() -> None:
    from Sere1nGraph.graph.tools.osint_tools import _coerce_list, _coerce_mapping

    assert _coerce_list('["a", "b"]', field="aliases") == ["a", "b"]
    assert _coerce_list(["a"], field="aliases") == ["a"]
    assert _coerce_mapping('{"tone": "formal"}', field="profile") == {
        "tone": "formal"
    }
    with pytest.raises(ValueError, match="必须是 JSON 数组"):
        _coerce_list('{"not": "a list"}', field="aliases")


def test_agent_persona_refresh_preserves_identity_and_evidence() -> None:
    from api.services.persona_collect import merge_existing_persona_profile

    merged = merge_existing_persona_profile(
        {
            "name": "陈望舒",
            "gender": "女",
            "age": 38,
            "age_range": "35-39",
            "company": "澄明研究中心",
            "generation_key": "agent_stable",
            "sources": [{"source": "synthetic_research"}],
            "source_urls": ["https://example.org/old"],
            "evidence": ["既有行业依据"],
            "research_evidence": [{
                "dimension": "work_rhythm",
                "finding": "项目节点前工作节奏明显加快",
                "applicability": "研究项目负责人",
                "source_urls": ["https://example.org/old"],
            }],
        },
        {
            "name": "另一个名字",
            "gender": "男",
            "age": 29,
            "generation_key": "agent_stable",
            "sources": ["https://example.org/new"],
            "evidence": ["新增沟通依据"],
            "research_evidence": [{
                "dimension": "communication_style",
                "finding": "偏好先给结论再补充证据",
                "applicability": "跨团队沟通",
                "source_urls": ["https://example.org/new"],
            }],
        },
    )

    assert merged["name"] == "陈望舒"
    assert merged["gender"] == "女"
    assert merged["age"] == 38
    assert merged["sources"] == [
        "https://example.org/old",
        "https://example.org/new",
    ]
    assert merged["evidence"] == ["既有行业依据", "新增沟通依据"]
    assert len(merged["research_evidence"]) == 2


@pytest.mark.asyncio
async def test_sse_does_not_emit_internal_summary_tokens() -> None:
    from langchain_core.messages import AIMessageChunk
    from Sere1nGraph.graph.agents.streaming import process_agent_stream_sse

    class FakeAgent:
        async def astream(self, *_args, **_kwargs):
            yield "messages", (
                AIMessageChunk(content="internal summary"),
                {"lc_source": "summarization"},
            )
            yield "messages", (AIMessageChunk(content="visible answer"), {})

    events = [event async for event in process_agent_stream_sse(FakeAgent(), [])]
    assert {event.get("data") for event in events if event["type"] == "content"} == {
        "visible answer"
    }
