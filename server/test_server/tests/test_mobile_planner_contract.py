"""Provider output must retain actionable text before phone execution."""
import pytest
from pydantic import ValidationError

from core.mobile.planner_contract import TaskPlan


def test_plain_plan_and_finished_replan_remain_compatible():
    assert TaskPlan(subtasks=[" 打开微信 ", "搜索公开文章"]).subtasks == [
        "打开微信", "搜索公开文章",
    ]
    assert TaskPlan(subtasks=[]).subtasks == []


def test_object_steps_keep_intent_and_search_parameters():
    plan = TaskPlan(subtasks=[
        {"intent": "打开微信", "target": "微信"},
        {"subtask": "搜索公司", "description": "输入示例公司并搜索文章"},
        {"action": "input", "text": "示例公司", "target": "顶部搜索框"},
        {"action": "search", "parameters": {"query": "公开公告", "app": "微信"}},
    ])
    assert all(isinstance(step, str) for step in plan.subtasks)
    assert "打开微信" in plan.subtasks[0]
    assert "输入示例公司并搜索文章" in plan.subtasks[1]
    assert "示例公司" in plan.subtasks[2] and "顶部搜索框" in plan.subtasks[2]
    assert "公开公告" in plan.subtasks[3]


@pytest.mark.parametrize("step", ["", "  ", 23, None, {}, {"success": True}, {"action": {"x": 2}}])
def test_unusable_steps_fail_instead_of_executing_empty_or_invented_actions(step):
    with pytest.raises(ValidationError):
        TaskPlan(subtasks=[step])


def test_schema_still_requests_string_instructions():
    schema = TaskPlan.model_json_schema()
    assert schema["properties"]["subtasks"]["items"]["type"] == "string"
