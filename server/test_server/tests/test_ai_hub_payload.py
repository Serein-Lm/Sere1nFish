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
    content = next(item for item in catalog["agents"] if item["name"] == "content")
    assert "generate_document_artifact" in content["tools"]


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


def test_document_artifact_registry_generates_multiple_formats() -> None:
    from api.services.artifact_files import generate_artifact, supported_formats

    assert supported_formats() == ("word", "markdown", "text", "json", "csv")

    markdown = generate_artifact(
        title="测试报告",
        content="# 结论\n正常",
        output_format="markdown",
    )
    assert markdown["filename"].endswith(".md")
    assert markdown["content_type"].startswith("text/markdown")
    assert markdown["data"].decode("utf-8") == "# 结论\n正常"

    markdown_alias = generate_artifact(
        title="别名测试",
        content="# 正常",
        output_format=".md",
    )
    assert markdown_alias["kind"] == "markdown"
    assert markdown_alias["filename"].endswith(".md")

    json_artifact = generate_artifact(
        title="结构化结果",
        content='{"status":"ok"}',
        output_format="json",
    )
    assert json_artifact["filename"].endswith(".json")
    assert '"status": "ok"' in json_artifact["data"].decode("utf-8")

    with pytest.raises(ValueError, match="JSON 正文格式错误"):
        generate_artifact(title="错误", content="{bad", output_format="json")


def test_artifact_download_content_type_is_format_aware() -> None:
    from api.routers.artifacts import _artifact_content_type

    assert _artifact_content_type({"filename": "report.docx"}).startswith(
        "application/vnd.openxmlformats"
    )
    assert _artifact_content_type({"filename": "report.csv"}) == "text/csv"
    assert (
        _artifact_content_type(
            {"filename": "report.bin", "content_type": "application/x-custom"}
        )
        == "application/x-custom"
    )
    assert (
        _artifact_content_type(
            {"filename": "report.json", "content_type": "text/plain\r\nX-Test: bad"}
        )
        == "application/json"
    )


def test_dingtalk_card_renders_progress_and_downloadable_artifacts() -> None:
    from api.services.dingtalk_card import DingTalkCardRenderer, build_artifact_buttons

    renderer = DingTalkCardRenderer()
    renderer.consume({
        "event": "start",
        "path": "graph",
        "data": {"type": "graph", "displayName": "AI 中枢"},
    })
    renderer.consume({
        "event": "start",
        "path": "graph.router.classify",
        "data": {"type": "node", "displayName": "分析查询"},
    })
    renderer.consume({
        "event": "end",
        "path": "graph.router.classify",
        "data": {"status": "success"},
    })
    renderer.consume({
        "event": "start",
        "path": "graph.router.content.tools.generate_document_artifact",
        "data": {"type": "tool", "displayName": "生成文档产物"},
    })
    renderer.consume({
        "event": "content",
        "path": "graph.router.content",
        "data": {"content": "正在整理可下载文档。"},
    })

    running = renderer.render_running()
    assert "执行进度" in running
    assert "生成文档产物" in running
    assert "调用 1 个工具" in running

    artifacts = [
        {
            "artifact_id": "art_word",
            "kind": "word",
            "title": "接入教程",
            "filename": "接入教程.docx",
            "size": 2048,
            "download_url": "/api/v1/artifacts/art_word/download",
        },
        {
            "artifact_id": "art_json",
            "kind": "json",
            "title": "配置示例",
            "filename": "config.json",
            "download_url": "/api/v1/artifacts/art_json/download",
        },
    ]
    final = renderer.render_final(
        "完成 [[artifact:art_word|接入教程]]\n下载链接：/api/v1/artifacts/art_word/download",
        artifacts,
        base_url="https://fish.example.com/",
    )
    assert "交付产物" in final
    assert "Word" in final and "JSON" in final
    assert "https://fish.example.com/phishing?ref_artifact=art_word" in final
    assert "[[artifact:" not in final
    assert "下载链接：/api/" not in final

    buttons = build_artifact_buttons(
        artifacts,
        base_url="https://fish.example.com/",
    )
    assert [button["text"].split(" · ")[0] for button in buttons] == [
        "打开/下载 Word",
        "打开/下载 JSON",
    ]
    assert build_artifact_buttons(artifacts, base_url="javascript:alert(1)") == []


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
