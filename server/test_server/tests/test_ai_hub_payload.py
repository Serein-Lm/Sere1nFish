from __future__ import annotations

import asyncio

import pytest


def test_artifact_context_tracks_created_artifacts() -> None:
    from api.services.artifact_context import (
        artifact_context,
        current_artifact_meta,
        get_artifact_context,
        record_created_artifact,
    )

    assert get_artifact_context() is None
    with artifact_context(
        owner="admin",
        is_admin=True,
        conversation_id="conv_1",
        project_id="project_1",
        references=[{"type": "project", "id": "project_1"}],
    ) as context:
        assert context.is_admin is True
        assert current_artifact_meta()["conversation_id"] == "conv_1"
        record_created_artifact({"artifact_id": "art_1", "title": "Test"})
        assert context.created == [{"artifact_id": "art_1", "title": "Test"}]
    assert get_artifact_context() is None


def test_hub_tool_catalog_registers_all_query_interfaces() -> None:
    from Sere1nGraph.graph.tools.catalog import get_hub_tool_catalog

    catalog = get_hub_tool_catalog(chrome_configured=True)
    assert catalog["audit"]["complete"] is True
    assert catalog["audit"]["missing_query_interfaces"] == []
    payload = next(item for item in catalog["agents"] if item["name"] == "payload")
    assert "chrome-devtools" in payload["mcp_servers"]
    assert "generate_payload_word" in payload["tools"]
    assert "get_artifact_content" in payload["tools"]


def test_hub_prompts_are_repository_seeds() -> None:
    from Sere1nGraph.graph.prompts.loader import PROMPTS_DIR

    expected = {"classify", "data", "persona", "content", "payload"}
    assert expected == {path.stem for path in (PROMPTS_DIR / "hub").glob("*.md")}


def test_payload_prompt_has_bounded_research_recovery() -> None:
    from Sere1nGraph.graph.prompts.loader import PROMPTS_DIR

    content = (PROMPTS_DIR / "hub" / "payload.md").read_text(encoding="utf-8")
    assert "检索预算与失败恢复" in content
    assert "生成 Word" in content


@pytest.mark.asyncio
async def test_mcp_tool_timeout_returns_recoverable_error() -> None:
    from langchain_core.tools import StructuredTool

    from Sere1nGraph.graph.agents.runtime import _wrap_tools_with_error_handling

    async def slow_tool() -> str:
        await asyncio.sleep(0.05)
        return "done"

    tool = StructuredTool.from_function(
        coroutine=slow_tool,
        name="slow_tool",
        description="用于验证工具调用超时",
    )
    wrapped = _wrap_tools_with_error_handling([tool], tool_timeout=0.01)

    result = await wrapped[0].ainvoke({})

    assert "TimeoutError" in result
    assert "调用超过 0.01s" in result


@pytest.mark.asyncio
async def test_mcp_tool_budget_does_not_call_adapter_after_limit() -> None:
    from langchain_core.tools import StructuredTool

    from Sere1nGraph.graph.agents.runtime import _wrap_tools_with_error_handling

    calls = 0

    async def counted_tool() -> str:
        nonlocal calls
        calls += 1
        return "done"

    tool = StructuredTool.from_function(
        coroutine=counted_tool,
        name="counted_tool",
        description="用于验证 MCP 调用预算",
    )
    wrapped = _wrap_tools_with_error_handling([tool], max_calls=1)

    assert await wrapped[0].ainvoke({}) == "done"
    blocked = await wrapped[0].ainvoke({})

    assert calls == 1
    assert "调用预算已用完" in blocked


def test_conversation_persistence_ignores_workflow_summary() -> None:
    from Sere1nGraph.graph.workflow.events import final
    from api.routers.agent import _extract_final_text

    sections: dict[str, str] = {}
    _extract_final_text(final("result", "正式结果"), sections)
    _extract_final_text(final("summary", "工作流执行完成"), sections)

    assert sections == {"result": "正式结果"}


def test_artifact_list_metadata_omits_large_content() -> None:
    from api.routers.artifacts import _public_meta

    doc = {
        "artifact_id": "art_1",
        "file_path": "/tmp/art_1.docx",
        "meta": {"content": "large body", "sources": [{"url": "https://example.test"}]},
    }

    listed = _public_meta(doc)
    detailed = _public_meta(doc, include_content=True)

    assert "file_path" not in listed
    assert "content" not in listed["meta"]
    assert detailed["meta"]["content"] == "large body"


@pytest.mark.asyncio
async def test_agent_stream_separates_trace_from_final_result() -> None:
    from langchain_core.messages import AIMessage, AIMessageChunk

    from Sere1nGraph.graph.agents.streaming import process_agent_stream_sse

    class FakeAgent:
        async def astream(self, *_args, **_kwargs):
            yield "messages", (AIMessageChunk(content="先检索资料。"), {})
            yield "updates", {
                "model": {
                    "messages": [AIMessage(
                        content="先检索资料。",
                        tool_calls=[{"name": "search", "args": {}, "id": "call_1"}],
                    )]
                }
            }
            yield "messages", (AIMessageChunk(content="最终交付结果"), {})
            yield "updates", {
                "model": {"messages": [AIMessage(content="最终交付结果")]}
            }

    events = [event async for event in process_agent_stream_sse(FakeAgent(), [])]

    assert "先检索资料。" in [event.get("data") for event in events if event["type"] == "content"]
    assert [event["data"] for event in events if event["type"] == "result"] == ["最终交付结果"]


def test_hub_specialist_query_preserves_original_parameters() -> None:
    from Sere1nGraph.graph.workflow.hub import _compose_specialist_query

    original = "读取 https://example.test/report 并引用 artifact_id=art_123"
    result = _compose_specialist_query(original, "核验报告并生成 Word")
    assert original in result
    assert "核验报告并生成 Word" in result


def test_dingtalk_stream_requires_complete_credentials() -> None:
    from api.services.dingtalk_stream import DingTalkStreamManager

    enabled = DingTalkStreamManager._enabled
    assert enabled({"enabled": True, "stream_enabled": True, "client_id": "id", "client_secret": "secret"})
    assert not enabled({"enabled": True, "stream_enabled": True, "client_id": "id"})
    assert not enabled({"enabled": True, "stream_enabled": False, "client_id": "id", "client_secret": "secret"})


def test_dingtalk_client_secret_is_encrypted_field() -> None:
    from api.utils.config_crypto import decrypt_config, encrypt_config, is_encrypted_value

    encrypted = encrypt_config({"client_id": "ding-id", "client_secret": "ding-secret"})
    assert encrypted["client_id"] == "ding-id"
    assert is_encrypted_value(encrypted["client_secret"])
    assert decrypt_config(encrypted)["client_secret"] == "ding-secret"
