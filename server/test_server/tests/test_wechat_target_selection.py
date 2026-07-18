from __future__ import annotations

from typing import Any

import pytest

from api.services.wechat_target_selection import (
    WechatTargetCandidate,
    WechatTargetSelectionService,
)
from Sere1nGraph.graph.prompts.loader import load_prompt


def test_wechat_prompt_uses_mature_institution_policy() -> None:
    prompt = load_prompt(
        "wechat_target_selection/wechat_target_selection"
    )

    assert "公众号策略与小红书策略不同" in prompt
    assert "省级广播电视台在本策略中默认采集" in prompt
    assert "交易所" in prompt
    assert "清算" in prompt
    assert "大型互联网公司不因规模大自动入选" in prompt
    assert "疑似虚构" in prompt


@pytest.mark.asyncio
async def test_all_mode_explicitly_selects_every_target() -> None:
    result = await WechatTargetSelectionService(
        object(),
        mode="all",
    ).select(
        [
            WechatTargetCandidate(
                target_id="target-1",
                target_name="年轻互联网公司",
            )
        ],
        project_id="project-1",
        task_id="task-1",
    )

    assert result.selected_count == 1
    assert result.decisions[0].should_collect_wechat is True
    assert result.decisions[0].source == "all"


@pytest.mark.asyncio
async def test_auto_mode_fallback_keeps_obvious_traditional_units(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.services.wechat_target_selection as selection_module

    monkeypatch.setattr(
        selection_module,
        "create_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("model unavailable")
        ),
    )
    candidates = [
        WechatTargetCandidate(
            target_id="target-tv",
            target_name="安徽广播电视台",
            context={"industry": "media", "scale": "large"},
        ),
        WechatTargetCandidate(
            target_id="target-exchange",
            target_name="大连商品交易所",
            context={"industry": "finance"},
        ),
        WechatTargetCandidate(
            target_id="target-app",
            target_name="某年轻互联网应用有限公司",
            context={"industry": "internet", "scale": "medium"},
        ),
    ]

    result = await WechatTargetSelectionService(
        object(),
        mode="auto",
    ).select(
        candidates,
        project_id="project-1",
        task_id="task-1",
    )

    assert result.status == "fallback"
    assert [
        item.should_collect_wechat for item in result.decisions
    ] == [True, True, False]
    assert [item.target_category for item in result.decisions[:2]] == [
        "broadcast_news_media",
        "exchange_financial_infrastructure",
    ]
    assert all(item.source == "fallback" for item in result.decisions)


def test_ai_decision_rejects_name_authenticity_reason() -> None:
    import api.services.wechat_target_selection as selection_module

    with pytest.raises(ValueError, match="名称真实性"):
        selection_module._AiTargetDecision.model_validate(
            {
                "target_id": "target-1",
                "target_category": "unknown",
                "should_collect_wechat": False,
                "reason": "名称疑似虚构，可能不存在",
                "confidence": 0.8,
            }
        )
