from __future__ import annotations

from typing import Any

import pytest

from api.services.xhs_target_selection import (
    XhsTargetCandidate,
    XhsTargetSelectionService,
    merge_xhs_target_selection_results,
    parse_manual_targets,
)
from Sere1nGraph.graph.prompts.loader import load_prompt


def test_auto_selection_prompt_keeps_explicit_business_policy_overrides() -> None:
    prompt = load_prompt("xhs_target_selection/xhs_target_selection")

    assert "`安徽广播电视台` 必须采集" in prompt
    assert "交易所必须采集" in prompt
    assert "不得机械外推到名称为清算、结算、登记、支付" in prompt
    assert "`江苏省广电有线信息网络股份有限公司` 必须跳过" in prompt
    assert "`跨境银行间支付清算有限责任公司`和`农信银资金清算中心有限责任公司`必须采集" in prompt
    assert "名称以“中国广电”开头" in prompt
    assert "`爱上电视传媒（北京）有限公司`" in prompt
    assert "`中央广播电视总台`" in prompt
    assert "以下商业广电网络或传媒目标必须采集" in prompt
    assert "不得把“中国广电省网”或江苏省广电的跳过规则泛化" in prompt
    assert "省级广播电视台默认不采集" in prompt
    assert "禁止用“疑似虚构”“名称错误”“可能不存在”" in prompt


def test_auto_selection_rejects_name_authenticity_as_decision_reason() -> None:
    import api.services.xhs_target_selection as selection_module

    with pytest.raises(ValueError, match="名称真实性"):
        selection_module._AiTargetDecision.model_validate(
            {
                "target_id": "target-1",
                "target_category": "unknown",
                "should_collect_xhs": False,
                "reason": "名称疑似虚构，可能不存在。",
                "confidence": 0.8,
            }
        )


def test_parse_manual_targets_accepts_common_separators_and_deduplicates() -> None:
    assert parse_manual_targets("中国平安\n平安科技， 中国平安;平安银行") == [
        "中国平安",
        "平安科技",
        "平安银行",
    ]


@pytest.mark.asyncio
async def test_manual_selection_matches_legal_name_aliases_and_tracks_unmatched() -> None:
    service = XhsTargetSelectionService(
        object(),
        mode="manual",
        manual_targets=["中国平安", "平安科技"],
    )
    root = await service.select(
        [
            XhsTargetCandidate(
                target_id="target-root",
                target_name="中国平安保险（集团）股份有限公司",
                aliases=["中国平安", "平安集团"],
            )
        ],
        project_id="project-1",
        task_id="task-1",
    )
    child = await service.select(
        [
            XhsTargetCandidate(
                target_id="target-child",
                target_name="平安科技（深圳）有限公司",
                aliases=["平安科技"],
            ),
            XhsTargetCandidate(
                target_id="target-public",
                target_name="某市政务服务中心",
            ),
        ],
        project_id="project-1",
        task_id="task-1",
    )
    merged = merge_xhs_target_selection_results(root, child)

    assert [item.should_collect_xhs for item in merged.decisions] == [True, True, False]
    assert merged.selected_count == 2
    assert merged.skipped_count == 1
    assert merged.unmatched_manual_targets == []
    assert all(item.source == "manual" for item in merged.decisions)


class _FakeStructuredLlm:
    def __init__(self) -> None:
        self.messages: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> dict[str, Any]:
        self.messages.append(messages)
        if len(self.messages) == 1:
            return {
                "decisions": [
                    {
                        "target_id": "target-large",
                        "target_category": "large_enterprise",
                        "should_collect_xhs": True,
                        "reason": "全国性大型商业集团",
                        "confidence": 93,
                    }
                ]
            }
        return {
            "decisions": [
                {
                    "target_id": "target-large",
                    "target_category": "large_enterprise",
                    "should_collect_xhs": True,
                    "reason": "全国性大型商业集团",
                    "confidence": 93,
                },
                {
                    "target_id": "target-government",
                    "target_category": "government",
                    "should_collect_xhs": False,
                    "reason": "党政机关不进入小红书采集",
                    "confidence": 0.98,
                },
            ]
        }


class _FakeLlm:
    def __init__(self, structured: _FakeStructuredLlm) -> None:
        self.structured = structured

    def with_structured_output(self, _schema: Any) -> _FakeStructuredLlm:
        return self.structured


@pytest.mark.asyncio
async def test_auto_selection_retries_with_schema_when_target_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.services.xhs_target_selection as selection_module

    structured = _FakeStructuredLlm()
    monkeypatch.setattr(selection_module, "load_prompt", lambda _slug: "分类规则")
    monkeypatch.setattr(
        selection_module,
        "create_llm",
        lambda *_args, **_kwargs: _FakeLlm(structured),
    )
    service = XhsTargetSelectionService(object(), mode="auto")
    result = await service.select(
        [
            XhsTargetCandidate(
                target_id="target-large",
                target_name="大型保险集团",
                context={"industry": "finance", "scale": "large"},
            ),
            XhsTargetCandidate(
                target_id="target-government",
                target_name="某市人民政府",
                context={"industry": "government", "scale": "large"},
            ),
        ],
        project_id="project-1",
        task_id="task-1",
    )

    assert len(structured.messages) == 2
    assert "JSON Schema" in structured.messages[1][1].content
    assert result.status == "completed"
    assert result.selected_count == 1
    assert result.skipped_count == 1
    assert result.decisions[0].confidence == pytest.approx(0.93)
    assert result.decisions[1].target_category == "government"
    assert result.decisions[1].should_collect_xhs is False


@pytest.mark.asyncio
async def test_auto_selection_runtime_failure_conservatively_skips_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.services.xhs_target_selection as selection_module

    def unavailable(_slug: str) -> str:
        raise FileNotFoundError("prompt cache unavailable")

    monkeypatch.setattr(selection_module, "load_prompt", unavailable)
    service = XhsTargetSelectionService(object(), mode="auto")
    result = await service.select(
        [XhsTargetCandidate(target_id="target-1", target_name="待判断目标")],
        project_id="project-1",
        task_id="task-1",
    )

    assert result.status == "fallback"
    assert result.selected_count == 0
    assert result.skipped_count == 1
    assert result.decisions[0].source == "fallback"
    assert "prompt cache unavailable" in str(result.error)
