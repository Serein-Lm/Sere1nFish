from Sere1nGraph.graph.agents.factory import (
    DEFAULT_WEB_TAGGING_MCP_TOOL_LIMIT,
    WEB_TAGGING_RUNTIME_POLICY,
)
from Sere1nGraph.graph.prompts.loader import load_prompt
from api.services.info_collection.url_tools import (
    _build_web_scan_message,
    _prioritize_official_customer_service,
    _reconcile_rendered_evidence,
    _validate_web_tagging,
    _web_agent_timeout_budget,
    _web_agent_tool_limit,
)


def test_web_agent_budget_defaults_to_six_and_is_bounded() -> None:
    assert DEFAULT_WEB_TAGGING_MCP_TOOL_LIMIT == 6
    assert _web_agent_tool_limit({}) == 6
    assert _web_agent_tool_limit({"mcp_tool_limit": 1}) == 3
    assert _web_agent_tool_limit({"mcp_tool_limit": 99}) == 8


def test_web_agent_timeout_reserves_structured_extraction_budget() -> None:
    assert _web_agent_timeout_budget({}) == (900, 870)
    assert _web_agent_timeout_budget({"agent_timeout_seconds": 20}) == (60, 30)
    assert _web_agent_timeout_budget({"agent_timeout_seconds": 9999}) == (1500, 1470)
    assert _web_agent_timeout_budget({"agent_timeout_seconds": "invalid"}) == (
        900,
        870,
    )


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


def test_web_agent_message_prefers_complete_upstream_evidence() -> None:
    message = _build_web_scan_message(
        "https://example.com/bid",
        tool_limit=5,
        source_context="公告联系人：张三，电话 0551-12345678",
    )

    assert "证据已经足以确认" in message
    assert "不要调用浏览器工具" in message
    assert "0551-12345678" in message


def test_web_tagging_prompt_no_longer_limits_browsing_to_two_calls() -> None:
    prompt = load_prompt("web_tagging/web_tagging")

    assert "最多调用 6 次浏览器工具" in prompt
    assert "最多调用 2 次浏览器工具" not in prompt
    assert "在线客服（真人客服/企业微信客服入口） | 82-90" in prompt
    assert "客服中心/人工客服/咨询服务" in prompt


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
    assert captured["model_workload"] == "collection"
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


def test_web_tagging_keeps_verified_entry_without_dom_href() -> None:
    result = _validate_web_tagging(
        {
            "intro": {
                "url": "https://zwfw.cscse.edu.cn",
                "final_url": "https://zwfw.cscse.edu.cn/",
                "domain": "zwfw.cscse.edu.cn",
                "site_name": "教育部留学服务中心网上服务大厅",
                "entity_name": "教育部留学服务中心",
                "summary": "页面提供网上政务服务。",
            },
            "site_category": "target_official",
            "target_relation": "confirmed",
            "target_relation_reason": "目标主体官网",
            "has_findings": True,
            "findings": [
                {
                    "type": "customer_service",
                    "scope": "enterprise",
                    "channel": "link",
                    "role": "customer_service",
                    "subtype": "live_chat_native",
                    "label": "页面客服入口",
                    "value": None,
                    "context": "页面右下角显示客服入口",
                    "source_url": "https://service.cscse.edu.cn/aicc-im-base-web/",
                    "evidence": "目标官网固定区域存在可见客服按钮",
                    "attention_score": 72,
                    "attention_reason": "官网可直接触达的客服入口",
                    "party_name": "教育部留学服务中心",
                    "party_role": "other",
                    "target_relation": "confirmed",
                    "target_relation_reason": "目标主体官网",
                }
            ],
        },
        "https://zwfw.cscse.edu.cn",
        target_context={"root_domain": "cscse.edu.cn"},
    )

    assert result["has_findings"] is True
    assert result["findings"][0]["value"] is None
    assert result["findings"][0]["channel"] == "other"
    assert result["findings"][0]["source_url"].startswith(
        "https://service.cscse.edu.cn/"
    )


def test_web_tagging_normalizes_url_value_to_link_channel() -> None:
    result = _validate_web_tagging(
        {
            "intro": {
                "url": "https://zwfw.cscse.edu.cn",
                "final_url": "https://zwfw.cscse.edu.cn/",
                "domain": "zwfw.cscse.edu.cn",
                "site_name": "网上服务大厅",
                "entity_name": "教育部留学服务中心",
                "summary": "页面提供技术支持邮件咨询说明入口。",
            },
            "site_category": "target_official",
            "target_relation": "confirmed",
            "target_relation_reason": "目标主体官网",
            "has_findings": True,
            "findings": [
                {
                    "type": "customer_service",
                    "scope": "official",
                    "channel": "email",
                    "role": "support",
                    "label": "技术支持邮件咨询须知",
                    "value": "https://zwfw.cscse.edu.cn/support.html",
                    "context": "页面展示咨询说明入口",
                    "source_url": "https://zwfw.cscse.edu.cn/",
                    "evidence": "页面显示技术支持邮件咨询须知",
                    "attention_score": 45,
                    "attention_reason": "官方咨询入口",
                    "target_relation": "confirmed",
                    "target_relation_reason": "目标主体官网",
                }
            ],
        },
        "https://zwfw.cscse.edu.cn",
        target_context={"root_domain": "cscse.edu.cn"},
    )

    assert result["findings"][0]["channel"] == "link"
    reconciled = _reconcile_rendered_evidence(
        result,
        rendered_evidence={
            "final_url": "https://zwfw.cscse.edu.cn/",
            "service_entries": [
                {
                    "label": "技术支持邮件咨询须知",
                    "value": "https://zwfw.cscse.edu.cn/support.html",
                    "source_url": "https://zwfw.cscse.edu.cn/",
                    "context": "页面展示咨询说明入口",
                    "evidence": "页面显示技术支持邮件咨询须知",
                }
            ],
        },
        target_url="https://zwfw.cscse.edu.cn",
        target_context={"root_domain": "cscse.edu.cn"},
    )
    assert len(reconciled["findings"]) == 1
    assert reconciled["evidence_audit"]["reconciled_finding_count"] == 0


def test_rendered_evidence_repairs_agent_contact_omission() -> None:
    tagging = _validate_web_tagging(
        {
            "intro": {
                "url": "https://zwfw.cscse.edu.cn",
                "final_url": "https://zwfw.cscse.edu.cn/",
                "domain": "zwfw.cscse.edu.cn",
                "site_name": "教育部留学服务中心网上服务大厅",
                "entity_name": "教育部留学服务中心",
                "summary": "页面未直接展示联系电话或客服入口。",
            },
            "site_category": "target_official",
            "target_relation": "confirmed",
            "target_relation_reason": "目标主体官网",
            "has_findings": False,
            "no_findings_reason": "页面未发现联系方式",
            "findings": [],
        },
        "https://zwfw.cscse.edu.cn",
        target_context={"root_domain": "cscse.edu.cn"},
    )

    result = _reconcile_rendered_evidence(
        tagging,
        rendered_evidence={
            "final_url": "https://zwfw.cscse.edu.cn/",
            "content_length": 1129,
            "contacts": [
                {
                    "channel": "telephone",
                    "value": "010-62677800",
                    "label": "座机: 010-62677800",
                    "context": "联系我们 咨询电话：010-62677800（客服中心）",
                }
            ],
            "service_entries": [
                {
                    "label": "页面客服入口",
                    "value": None,
                    "source_url": "https://zwfw.cscse.edu.cn/",
                    "context": "页面提供公开客服或咨询交互入口",
                    "evidence": "页面右下角存在固定客服按钮",
                    "position": "fixed",
                },
                {
                    "label": "无关平台客服",
                    "value": "https://support.example.net/chat",
                    "source_url": "https://zwfw.cscse.edu.cn/",
                    "context": "外部平台入口",
                    "evidence": "无法确认目标主体背书",
                }
            ],
        },
        target_url="https://zwfw.cscse.edu.cn",
        target_context={"root_domain": "cscse.edu.cn"},
    )

    assert result["has_findings"] is True
    assert result["no_findings_reason"] is None
    assert {item["value"] for item in result["findings"]} == {
        "010-62677800",
        None,
    }
    assert "未直接展示" not in result["intro"]["summary"]
    assert "页面右下角提供官方在线客服入口" in result["intro"]["summary"]
    assert result["evidence_audit"]["reconciled_finding_count"] == 2
    scores = {
        item["subtype"]: item["attention_score"]
        for item in result["findings"]
    }
    assert scores["hotline_landline"] == 78
    assert scores["live_chat_native"] == 85


def test_customer_service_priority_does_not_promote_unrelated_platform() -> None:
    result = _prioritize_official_customer_service(
        {
            "site_category": "third_party",
            "target_relation": "not_target",
            "excluded": True,
            "findings": [
                {
                    "type": "customer_service",
                    "channel": "other",
                    "subtype": "live_chat_native",
                    "target_relation": "not_target",
                    "attention_score": 20,
                    "attention_reason": "无关平台客服",
                }
            ],
        }
    )

    assert result["findings"][0]["attention_score"] == 20
