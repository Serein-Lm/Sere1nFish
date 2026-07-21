from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "docs/dingtalk/Sere1nFish-AI-Card-Responsive.json"


def _components(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("componentName"):
            result.append(value)
        for child in value.get("children", []):
            result.extend(_components(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(_components(child))
    return result


def test_responsive_dingtalk_card_template_is_compact_and_importable() -> None:
    exported = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    editor = json.loads(exported["editorData"])
    components = _components(editor["schema"]["componentsTree"])
    statuses = [
        item
        for item in components
        if item["componentName"] == "AICardStatusContainer"
        and item.get("props", {}).get("status") in {2, 3}
    ]

    assert exported["type"] == "im"
    assert exported["mode"] == "card"
    assert len(statuses) == 2
    container = next(
        item for item in components if item["componentName"] == "AICardContainer"
    )
    assert container["props"]["enableGradientBorder"] is False

    for status in statuses:
        content = next(
            child
            for child in status["children"]
            if child["componentName"] == "AICardContent"
        )
        query = content["children"][0]
        assert query["componentName"] == "BaseText"
        assert query["props"]["enableIcon"] is False
        if status["props"]["status"] == 2:
            assert [child["componentName"] for child in content["children"]] == [
                "BaseText",
                "Loop",
                "Divider",
                "MarkdownBlock",
            ]
            progress_loop = content["children"][1]
            assert progress_loop["props"]["width"] == 100
            assert progress_loop["props"]["childWidth"] == "match_parent"
            assert not any(
                item["componentName"] == "ProgressBar"
                for item in _components(progress_loop)
            )
        else:
            assert [child["componentName"] for child in content["children"]] == [
                "BaseText",
                "Divider",
                "MarkdownBlock",
            ]
        assert not any(
            item["componentName"] == "Chart" for item in _components(content)
        )

    assert "@subdata{&#039;progress&#039;}" not in exported["widgetInfo"]
    assert "<DDAnimationView" not in exported["widgetInfo"]
    assert "icon_question_fill" not in exported["widgetInfo"]
    assert "data.cardData.preparations" in exported["widgetInfo"]
    assert "data.cardData.content" in exported["widgetInfo"]
    assert "data.cardData.query" in exported["widgetInfo"]
    assert "data.cardData.charts" not in exported["widgetInfo"]
