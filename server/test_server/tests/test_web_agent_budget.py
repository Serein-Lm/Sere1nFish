from Sere1nGraph.graph.agents.factory import (
    DEFAULT_WEB_TAGGING_MCP_TOOL_LIMIT,
    WEB_TAGGING_RUNTIME_POLICY,
)
from Sere1nGraph.graph.prompts.loader import load_prompt
from api.services.info_collection.url_tools import (
    _build_web_scan_message,
    _validate_web_tagging,
    _web_agent_tool_limit,
)


def test_web_agent_budget_defaults_to_six_and_is_bounded() -> None:
    assert DEFAULT_WEB_TAGGING_MCP_TOOL_LIMIT == 6
    assert _web_agent_tool_limit({}) == 6
    assert _web_agent_tool_limit({"mcp_tool_limit": 1}) == 3
    assert _web_agent_tool_limit({"mcp_tool_limit": 99}) == 8


def test_web_agent_message_allows_https_retry_and_login_modal_recovery() -> None:
    message = _build_web_scan_message(
        "http://example.com",
        tool_limit=5,
    )

    assert "最多调用 5 次" in message
    assert "改为 HTTPS 重试一次" in message
    assert "登录弹窗" in message
    assert "最多尝试关闭一次" in message
    assert "hover" in message
    assert "一旦获得至少一个真实值" in message


def test_web_tagging_prompt_no_longer_limits_browsing_to_two_calls() -> None:
    prompt = load_prompt("web_tagging/web_tagging")

    assert "最多调用 6 次浏览器工具" in prompt
    assert "最多调用 2 次浏览器工具" not in prompt


def test_web_agent_runtime_policy_overrides_stale_prompt_cache() -> None:
    assert "最多调用 6 次" in WEB_TAGGING_RUNTIME_POLICY
    assert "HTTP 转 HTTPS" in WEB_TAGGING_RUNTIME_POLICY
    assert "hover" in WEB_TAGGING_RUNTIME_POLICY
    assert "立即停止调用" in WEB_TAGGING_RUNTIME_POLICY


def test_web_agent_factory_scales_model_call_limit(monkeypatch) -> None:
    import asyncio
    import Sere1nGraph.graph.agents.factory as factory

    captured = {}

    def fake_create_agent_node(**kwargs):
        captured.update(kwargs)
        return "agent"

    monkeypatch.setattr(factory, "create_agent_node", fake_create_agent_node)
    result = asyncio.run(
        factory.create_web_tagging_agent(
            object(),
            streaming=False,
            mcp_tool_limit=5,
        )
    )

    assert result == "agent"
    assert captured["mcp_tool_limit"] == 5
    assert WEB_TAGGING_RUNTIME_POLICY in captured["system_prompt"]
    assert getattr(captured["middleware"][0], "run_limit", None) == 9


def test_web_tagging_discards_label_only_contact_entries() -> None:
    base_finding = {
        "type": "business_contact",
        "scope": "official",
        "channel": "phone",
        "role": "business",
        "label": "咨询热线",
        "context": "首页显示咨询热线入口",
        "source_url": "https://example.com/contact",
        "evidence": "顶部导航显示咨询热线",
        "attention_score": 45,
        "attention_reason": "官方公开业务入口",
        "party_name": "示例单位",
        "party_role": "publisher",
        "target_relation": "confirmed",
        "target_relation_reason": "页面由示例单位运营",
    }
    result = _validate_web_tagging(
        {
            "intro": {
                "url": "https://example.com",
                "final_url": "https://example.com/contact",
                "domain": "example.com",
                "site_name": "示例站点",
                "entity_name": "示例单位",
                "summary": "页面包含联系方式入口",
            },
            "has_findings": True,
            "no_findings_reason": None,
            "findings": [
                {**base_finding, "value": None},
                {**base_finding, "value": "010-12345678"},
            ],
        },
        "https://example.com",
    )

    assert result["has_findings"] is True
    assert [item["value"] for item in result["findings"]] == ["010-12345678"]
