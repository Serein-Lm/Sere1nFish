from __future__ import annotations

from typing import Any

import pytest


def test_web_tagging_drops_findings_from_external_pages() -> None:
    from api.services.info_collection.url_tools import _validate_web_tagging

    payload = {
        "intro": {
            "url": "https://bids.example.com/notice/1",
            "final_url": "https://bids.example.com/notice/1",
            "domain": "bids.example.com",
            "site_name": "采购公告",
            "entity_name": "示例单位",
            "summary": "采购公告",
        },
        "has_findings": True,
        "no_findings_reason": None,
        "findings": [
            {
                "type": "business_contact",
                "scope": "official",
                "channel": "phone",
                "role": "business",
                "label": "采购联系电话",
                "value": "0551-00000000",
                "context": "采购人信息",
                "source_url": "https://bids.example.com/notice/1",
                "evidence": "采购人信息段公开电话",
                "attention_score": 70,
                "attention_reason": "可直接联系采购人",
            },
            {
                "type": "customer_service",
                "scope": "official",
                "channel": "link",
                "role": "customer_service",
                "label": "第三方机器人",
                "value": "https://chat.example.net/bot",
                "context": "外部页面",
                "source_url": "https://chat.example.net/bot",
                "evidence": "外部机器人",
                "attention_score": 60,
                "attention_reason": "外部系统",
            },
        ],
    }

    result = _validate_web_tagging(payload, "https://bids.example.com/notice/1")

    assert [item["label"] for item in result["findings"]] == ["采购联系电话"]
    assert result["has_findings"] is True


def test_web_agent_navigation_guard_blocks_other_sites() -> None:
    from Sere1nGraph.graph.agents.factory import _build_same_site_navigation_guard

    guard = _build_same_site_navigation_guard("https://bids.example.com/notice/1")

    assert guard is not None
    assert (
        guard("navigate_page", (), {"url": "https://docs.bids.example.com/a"})
        is None
    )
    assert "导航已阻止" in str(
        guard("navigate_page", (), {"url": "https://www.bing.com/search?q=test"})
    )


@pytest.mark.asyncio
async def test_xhs_cookie_lookup_does_not_evaluate_database_truthiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import xhs as xhs_dao
    from api.services import xhs_vision_tools

    class Database:
        def __bool__(self) -> bool:
            raise NotImplementedError("database truthiness is forbidden")

    called = False

    async def get_active_cookie(_db: Any) -> None:
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(xhs_dao, "get_active_cookie", get_active_cookie)
    stream = xhs_vision_tools.screenshot_note_detail_stream(
        "note-1",
        db=Database(),
    )
    first = await anext(stream)
    second = await anext(stream)
    await stream.aclose()

    assert first["type"] == "progress"
    assert second["message"] == "正在申请 Chrome 容器..."
    assert called is True
