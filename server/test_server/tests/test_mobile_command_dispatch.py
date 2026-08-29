from __future__ import annotations

from typing import Any

import pytest


def test_browse_compiler_uses_short_verified_launch_settle() -> None:
    from core.mobile.command_executor import compile_mobile_actions

    actions = compile_mobile_actions("打开淘宝浏览首页推荐")

    assert actions is not None
    assert [action.kind for action in actions] == ["launch_app", "wait", "swipe", "wait", "swipe"]
    assert actions[1].args["seconds"] == 0.35


def test_app_compiler_preserves_cloned_instance_intent() -> None:
    from core.mobile.command_executor import compile_mobile_actions

    actions = compile_mobile_actions("打开微信分身")

    assert actions is not None
    assert len(actions) == 1
    assert actions[0].args == {
        "app_name": "微信",
        "app_instance": "clone",
    }


@pytest.mark.asyncio
async def test_compiled_stream_resolves_device_context_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.mobile import command_executor

    prepared = 0
    executed: list[str] = []

    class Dispatcher:
        async def execute(self, action):
            executed.append(action.kind)
            return True, "ok"

    def prepare(_device_id: str):
        nonlocal prepared
        prepared += 1
        return Dispatcher()

    monkeypatch.setattr(
        command_executor.MobileCommandDispatcher,
        "for_device",
        prepare,
    )
    actions = [
        command_executor.MobileAction("home", "主页"),
        command_executor.MobileAction("back", "返回"),
    ]
    events = [
        event
        async for event in command_executor.run_compiled_actions_stream(
            "device-1",
            "test",
            actions,
            task_id="task-1",
        )
    ]

    assert prepared == 1
    assert executed == ["home", "back"]
    assert events[-1]["data"]["success"] is True


@pytest.mark.asyncio
async def test_planned_subtask_uses_tool_dispatch_before_visual_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.mobile import planner

    visual_agent_built = False

    async def wake(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": True}

    async def plan(*_args: Any, **_kwargs: Any) -> list[str]:
        return ["打开淘宝"]

    async def compiled(*_args: Any, **_kwargs: Any):
        yield {
            "type": "step",
            "data": {"step": 1, "action": {"action": "launch_app"}, "success": True},
        }
        yield {"type": "done", "data": {"success": True, "steps": 1}}

    def build(*_args: Any, **_kwargs: Any):
        nonlocal visual_agent_built
        visual_agent_built = True
        raise AssertionError("simple planned action must not build a visual agent")

    monkeypatch.setattr(planner, "wake_device", wake)
    monkeypatch.setattr(planner, "plan_task", plan)
    monkeypatch.setattr(planner, "run_compiled_actions_stream", compiled)
    monkeypatch.setattr(planner, "build_executor_agent", build)

    events = [
        event
        async for event in planner.run_planned_task(
            "device-1",
            "完成应用启动任务",
            screen_aware=False,
            max_replans=0,
            plan_id="plan-1",
        )
    ]

    assert visual_agent_built is False
    assert events[-1]["stage"] == "done"
    assert events[-1]["data"]["completed"] == 1
