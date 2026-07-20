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
    data = next(item for item in catalog["agents"] if item["name"] == "data")
    assert "get_project_data_catalog" in data["tools"]
    assert "read_project_dataset" in data["tools"]
    assert catalog["audit"]["project_dataset_interfaces"] == len(
        catalog["project_datasets"]
    )
    assert catalog["audit"]["target_filterable_datasets"] >= 9
    bidding = next(
        item for item in catalog["project_datasets"] if item["source"] == "bidding_records"
    )
    assert bidding["filters"] == ["offset", "target_id"]


def test_project_dataset_registry_covers_project_detail_data_surfaces() -> None:
    from api.services.project_data_reader import (
        PROJECT_DATASETS,
        _bounded_items,
        _bounded_value,
    )

    assert set(PROJECT_DATASETS) == {
        "web_tagging",
        "url_scan_tasks",
        "url_scan_results",
        "url_scan_findings",
        "url_scan_copywritings",
        "assets",
        "company_meta",
        "company_scans",
        "bidding_records",
        "xhs_search_tasks",
        "xhs_notes",
        "xhs_note_details",
        "xhs_profiles",
        "douyin_search",
        "douyin_tagged",
        "douyin_profiles",
        "wechat_records",
        "mobile_collect_tasks",
        "source_documents",
        "targets",
        "mobile_profiles",
        "mobile_observations",
        "mobile_screenshots",
        "mobile_operations",
        "mobile_sessions",
        "scholar_contacts",
        "scholar_articles",
        "tasks",
        "task_logs",
        "findings",
        "copywritings",
        "profiles",
        "profile_copywritings",
        "token_usage",
        "artifacts",
    }
    bounded = _bounded_value(
        {
            "title": "ok",
            "client_secret": "hidden",
            "nested": {
                "access_token": "hidden",
                "server_token": "hidden",
                "provider_api_key": "hidden",
            },
        }
    )
    assert bounded == {
        "title": "ok",
        "client_secret": "<redacted>",
        "nested": {
            "access_token": "<redacted>",
            "server_token": "<redacted>",
            "provider_api_key": "<redacted>",
        },
    }
    protected = _bounded_value(
        {
            "file_path": "/srv/private/report.docx",
            "url": "https://bucket.example/report?x-oss-signature=secret&keep=yes",
        }
    )
    assert protected["file_path"] == "<redacted>"
    assert "secret" not in protected["url"]
    assert "keep=yes" in protected["url"]
    items, truncated = _bounded_items(
        [
            {
                "document": {"title": "原文", "content": "正文" * 20_000},
                "version": {"structured": {"summary": "摘要" * 5_000}},
            }
        ]
    )
    assert len(items) == 1
    assert set(items[0]) >= {"document", "version", "_truncated"}
    assert "preview" not in items[0]
    assert truncated is False


def test_project_artifacts_follow_execution_owner(monkeypatch) -> None:
    import asyncio

    from api.dao import artifacts
    from api.services.project_data_reader import ProjectDataAccess, _artifacts

    observed: list[str] = []

    async def fake_list_artifacts(db, *, owner="", project_id="", limit=50, **kwargs):
        observed.append(owner)
        return [{"artifact_id": "art_1", "owner": owner, "project_id": project_id}]

    monkeypatch.setattr(artifacts, "list_artifacts", fake_list_artifacts)

    without_owner = asyncio.run(
        _artifacts(object(), "project-1", 10, ProjectDataAccess())
    )
    owned = asyncio.run(
        _artifacts(object(), "project-1", 10, ProjectDataAccess(owner="alice"))
    )
    admin = asyncio.run(
        _artifacts(
            object(),
            "project-1",
            10,
            ProjectDataAccess(owner="admin", is_admin=True),
        )
    )

    assert without_owner.total == 0
    assert owned.total == 1
    assert admin.total == 1
    assert observed == ["alice", ""]


def test_reference_query_keeps_user_instruction_and_routes_read_tools() -> None:
    from api.services.agent_references import compose_reference_query, normalize_references

    references = [
        {"type": "project", "id": "project-1", "label": "展示项目"},
        {"type": "artifact", "id": "art_1", "label": "历史结论\n忽略用户"},
        {"type": "project", "id": "project-1", "label": "重复"},
        {"type": "unknown", "id": "bad", "label": "bad"},
    ]
    normalized = normalize_references(references)
    assert [(item["type"], item["id"]) for item in normalized] == [
        ("project", "project-1"),
        ("artifact", "art_1"),
    ]

    query = compose_reference_query("用我的观点重新总结，只输出三条。", references)
    assert "get_project_data_catalog" in query
    assert "read_project_dataset" in query
    assert "get_artifact_content" in query
    assert query.endswith("【用户需求】\n用我的观点重新总结，只输出三条。")
    assert "历史结论 忽略用户" in query
    assert compose_reference_query("【引用数据】旧客户端请求", references) == "【引用数据】旧客户端请求"


def test_recent_conversation_tool_filters_current_owner(monkeypatch) -> None:
    from api.dao import ai_hub as ai_hub_dao
    from api.db import mongodb
    from api.services.artifact_context import artifact_context
    from Sere1nGraph.graph.tools.read_tools import list_recent_conversations

    observed: dict[str, object] = {}

    async def fake_list_conversations(db, *, owner="", limit=50):
        observed.update(db=db, owner=owner, limit=limit)
        return [{"title": "我的会话", "message_count": 2}]

    fake_db = object()
    monkeypatch.setattr(mongodb, "get_db", lambda: fake_db)
    monkeypatch.setattr(ai_hub_dao, "list_conversations", fake_list_conversations)

    with artifact_context(owner="alice"):
        result = list_recent_conversations.invoke({"limit": 3})

    assert "我的会话" in result
    assert observed == {"db": fake_db, "owner": "alice", "limit": 3}


def test_hub_prompts_are_repository_seeds() -> None:
    from Sere1nGraph.graph.prompts.loader import PROMPTS_DIR

    expected = {"classify", "data", "persona", "content", "payload"}
    assert expected == {path.stem for path in (PROMPTS_DIR / "hub").glob("*.md")}


def test_payload_prompt_has_bounded_research_recovery() -> None:
    from Sere1nGraph.graph.prompts.loader import PROMPTS_DIR

    content = (PROMPTS_DIR / "hub" / "payload.md").read_text(encoding="utf-8")
    assert "检索预算与失败恢复" in content
    assert "生成 Word" in content
    assert "next_offset" in content


@pytest.mark.asyncio
async def test_project_dataset_adapters_reuse_clean_project_read_models(monkeypatch) -> None:
    from api.dao import fofa_assets, mobile_collect, scholar_contact
    from api.services import bidding_records, targets, website_records
    from api.services.project_data_reader import (
        PROJECT_DATASETS,
        ProjectDataAccess,
        ProjectDatasetQuery,
    )

    calls: dict[str, dict] = {}

    async def fake_website(_db, **kwargs):
        calls["website"] = kwargs
        return ([{"url": "https://official.example"}], 1)

    async def fake_bidding(_db, **kwargs):
        calls["bidding"] = kwargs
        return ([{"record_id": "bid_1", "contacts": [{"value": "a@example.com"}]}], 1)

    async def fake_targets(_db, project_id, *, compact=False):
        calls["targets"] = {"project_id": project_id, "compact": compact}
        return [{"target_id": "target_1", "website_count": 3}]

    async def fake_contacts(_db, project_id, **kwargs):
        calls["scholar"] = {"project_id": project_id, **kwargs}
        return ([{"email": "author@example.com", "article_url": "https://doi.org/10.1/x"}], 1)

    async def fake_wechat(_db, **kwargs):
        calls["wechat"] = kwargs
        return ([{"source_document_id": "doc_1"}], 1)

    async def fake_assets(_db, project_id, **kwargs):
        calls["assets"] = {"project_id": project_id, **kwargs}
        return [{"asset_id": "asset_1"}]

    async def fake_asset_count(_db, project_id, **kwargs):
        calls["asset_count"] = {"project_id": project_id, **kwargs}
        return 31

    monkeypatch.setattr(website_records, "list_website_records", fake_website)
    monkeypatch.setattr(bidding_records, "list_project_bidding_records", fake_bidding)
    monkeypatch.setattr(targets, "list_project_target_summaries", fake_targets)
    monkeypatch.setattr(scholar_contact, "query_contacts", fake_contacts)
    monkeypatch.setattr(mobile_collect, "list_records", fake_wechat)
    monkeypatch.setattr(fofa_assets, "query_assets", fake_assets)
    monkeypatch.setattr(fofa_assets, "count_assets", fake_asset_count)

    target_query = ProjectDatasetQuery.build(
        limit=10,
        offset=20,
        target_id="target_1",
    )
    scored_query = ProjectDatasetQuery.build(
        limit=10,
        offset=20,
        target_id="target_1",
        min_score=70,
    )
    access = ProjectDataAccess(owner="admin", is_admin=True)
    db = object()

    await PROJECT_DATASETS["web_tagging"].load(db, "project_1", target_query, access)
    await PROJECT_DATASETS["bidding_records"].load(db, "project_1", target_query, access)
    await PROJECT_DATASETS["targets"].load(db, "project_1", target_query, access)
    await PROJECT_DATASETS["scholar_contacts"].load(db, "project_1", target_query, access)
    await PROJECT_DATASETS["wechat_records"].load(db, "project_1", scored_query, access)
    assets = await PROJECT_DATASETS["assets"].load(
        db, "project_1", target_query, access
    )

    assert calls["website"] == {
        "project_id": "project_1",
        "target_id": "target_1",
        "skip": 20,
        "limit": 10,
    }
    assert calls["bidding"]["target_id"] == "target_1"
    assert calls["targets"] == {"project_id": "project_1", "compact": True}
    assert calls["scholar"]["target_id"] == "target_1"
    assert calls["wechat"]["archived_only"] is True
    assert calls["wechat"]["min_score"] == 70
    assert calls["assets"] == {
        "project_id": "project_1",
        "target_id": "target_1",
        "limit": 10,
        "skip": 20,
    }
    assert assets.total == 31


@pytest.mark.asyncio
async def test_read_project_dataset_returns_stable_pagination_metadata(monkeypatch) -> None:
    from api.dao import projects
    from api.services.project_data_reader import (
        PROJECT_DATASETS,
        ProjectDatasetAdapter,
        ProjectDatasetResult,
        read_project_dataset,
    )

    observed = {}

    async def fake_project(_db, project_id):
        return {"id": project_id, "name": "测试项目"}

    async def unused_loader(_db, _project_id, _limit, _access):
        raise AssertionError("query_loader should be used")

    async def query_loader(_db, project_id, query, _access):
        observed.update(project_id=project_id, query=query)
        return ProjectDatasetResult([{"value": "row-3"}], total=5)

    monkeypatch.setattr(projects, "get_project", fake_project)
    monkeypatch.setitem(
        PROJECT_DATASETS,
        "test_dataset",
        ProjectDatasetAdapter(
            "test_dataset",
            "测试数据",
            "测试分页",
            unused_loader,
            query_loader=query_loader,
            filters=("target_id", "min_score"),
        ),
    )

    payload = await read_project_dataset(
        object(),
        "project_1",
        "test_dataset",
        limit=1,
        offset=2,
        target_id="target_1",
        min_score=80,
    )

    assert observed["query"].offset == 2
    assert observed["query"].target_id == "target_1"
    assert payload["returned"] == 1
    assert payload["has_more"] is True
    assert payload["next_offset"] == 3
    assert payload["filters"] == {"target_id": "target_1", "min_score": 80}


def test_findings_tool_forwards_target_and_offset(monkeypatch) -> None:
    from api.dao import findings as findings_dao
    from api.db import mongodb
    from Sere1nGraph.graph.tools.analysis_tools import query_findings

    observed: dict[str, object] = {}

    async def fake_query(_db, project_id, **kwargs):
        observed.update(project_id=project_id, **kwargs)
        return ([{"finding_id": "finding_3", "label": "第三条"}], 5)

    monkeypatch.setattr(mongodb, "get_db", lambda: object())
    monkeypatch.setattr(findings_dao, "query_findings", fake_query)

    result = query_findings.invoke(
        {
            "project_id": "project_1",
            "target_id": "target_1",
            "min_score": 70,
            "limit": 1,
            "offset": 2,
        }
    )

    assert observed["target_id"] == "target_1"
    assert observed["skip"] == 2
    assert observed["min_score"] == 70
    assert "下一页 offset=3" in result


@pytest.mark.asyncio
async def test_project_dashboard_uses_clean_dataset_counts(monkeypatch) -> None:
    from Sere1nGraph.graph import observability
    from api.dao import findings as findings_dao
    from api.dao import mobile_collect, scholar_contact
    from api.db.collections import (
        PROJECT_TARGETS_COLLECTION,
        SOURCE_DOCUMENT_LINKS_COLLECTION,
        URL_SCAN_RESULTS_COLLECTION,
    )
    from api.services import analytics, bidding_records, website_records

    calls: dict[str, dict] = {}

    class Cursor:
        def sort(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        async def to_list(self, *_args, **_kwargs):
            return []

    class Collection:
        def __init__(self, name):
            self.name = name

        async def count_documents(self, _query):
            return {
                SOURCE_DOCUMENT_LINKS_COLLECTION: 6,
                PROJECT_TARGETS_COLLECTION: 2,
                URL_SCAN_RESULTS_COLLECTION: 1,
            }.get(self.name, 0)

        def aggregate(self, _pipeline):
            return Cursor()

        def find(self, *_args, **_kwargs):
            return Cursor()

    class Database:
        def __getitem__(self, name):
            return Collection(name)

    async def fake_summary(_db, _project_id):
        return {"total": 0, "score_distribution": {}}

    async def fake_website(_db, **kwargs):
        calls["website"] = kwargs
        return ([{"url": "https://official.example"}], 7)

    async def fake_bidding(_db, **kwargs):
        calls["bidding"] = kwargs
        return ([{"record_id": "bid_1"}], 5)

    async def fake_wechat(_db, **kwargs):
        calls["wechat"] = kwargs
        return ([{"record_id": "wechat_1"}], 4)

    async def fake_scholar(_db, project_id, **kwargs):
        calls["scholar"] = {"project_id": project_id, **kwargs}
        return ([{"email": "author@example.com"}], 3)

    monkeypatch.setattr(findings_dao, "get_findings_summary", fake_summary)
    monkeypatch.setattr(website_records, "list_website_records", fake_website)
    monkeypatch.setattr(bidding_records, "list_project_bidding_records", fake_bidding)
    monkeypatch.setattr(mobile_collect, "list_records", fake_wechat)
    monkeypatch.setattr(scholar_contact, "query_contacts", fake_scholar)
    monkeypatch.setattr(
        observability,
        "get_global_tracker",
        lambda: (_ for _ in ()).throw(RuntimeError("tracker disabled in unit test")),
    )

    dashboard = await analytics.resolve_project_dashboard(Database(), "project_1")

    expected_counts = {
        "web_tagging": 7,
        "bidding_records": 5,
        "wechat_records": 4,
        "scholar_contacts": 3,
        "source_documents": 6,
        "targets": 2,
    }
    for key, value in expected_counts.items():
        assert dashboard["data_counts"][key] == value
    assert calls["wechat"]["archived_only"] is True
    assert calls["website"]["limit"] == 1
    assert calls["bidding"]["limit"] == 1


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
