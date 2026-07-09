"""
系统2 — 规划层(执行层之上)。

用本项目 mobile_planner 模型把高层目标拆成有序子任务,
再逐个交执行层(AutoGLM 视觉 agent, executor.run_task_stream)执行。

这就是「规划层 + 执行层」:
- 规划层:理解意图、拆解步骤(我们的 LLM);
- 执行层:看屏→动作完成每个子任务(复用 AutoGLM)。
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from Sere1nGraph.graph.agents.runtime import create_llm
from api.services.runtime_config import get_runtime_app_config

from core.mobile.command_executor import (
    compile_mobile_actions,
    describe_compiled_actions,
    run_compiled_actions_stream,
)
from core.mobile.executor import (
    _log_data,
    _log_operation,
    _public_agent_event,
    _save_event_screenshot,
    build_executor_agent,
    register_agent,
    unregister_agent,
)
from core.mobile.manager import MobileDeviceManager
from core.mobile.screen_capture import capture_ready_screen, wake_device
from core.observability import observation_context


_PLANNER_SYSTEM = (
    "你是手机自动化的规划层。把用户的高层目标拆成一组**有序、原子、可在手机上逐步执行**的子任务。\n"
    "要求:\n"
    "- 每个子任务是一句明确的操作意图,必须能交给执行层直接看屏执行。\n"
    "- 不要假设必须先读屏;目标已明确时可直接规划 Home、打开应用、搜索、输入等动作。\n"
    "- 不要输出'观察屏幕'、'分析页面'、'判断是否成功'这类元步骤。\n"
    "- 不要输出'唤醒屏幕'、'亮屏'这类设备前置步骤;系统会在规划/执行前自动处理。\n"
    "- 如果目标明确要求'回到桌面/主屏幕'后打开应用,合并成一个子任务,如'回到主屏幕并打开应用商店'。\n"
    "- 涉及应用时,第一步通常是'打开X'或'回到主屏幕并打开X';已在当前屏幕上下文明确处于目标应用时可省略。\n"
    "- 涉及搜索/输入时,优先合并为一个可验证意图,如'在应用商店搜索微信';只有后续目标依赖搜索结果时再单独列'点击搜索结果中的微信'。\n"
    "- 不要把'点击搜索框'、'输入关键词'、'点击搜索按钮'机械拆成三步;执行层可以在同一屏内批量点击、输入并提交。\n"
    "- 涉及点击时,子任务应说明可见目标,如'点击底部购物车'或'点击搜索结果中的第一个商品'。\n"
    "- 不要太碎(避免一步一次无语义点击),也不要太大(一步只含一个意图)。\n"
    "- 简单目标保留 1 步,常规任务 2-6 步,复杂任务最多 8 步。\n"
    "- 只输出 JSON object,字段为 subtasks,不要解释。"
)

_FRESH_START_RE = re.compile(
    r"(回到桌面|返回桌面|主屏幕|回首页|回到主页|打开|启动|进入|运行|拉起)"
)


def _should_describe_screen_before_plan(goal: str) -> bool:
    """Screen context is useful for continuing current UI, not for fresh starts."""
    normalized = goal.strip()
    return bool(normalized) and not _FRESH_START_RE.search(normalized)


class TaskPlan(BaseModel):
    subtasks: list[str] = Field(description="有序的子任务列表")


async def plan_task(goal: str, *, screen_analysis: str | None = None) -> list[str]:
    """把高层目标拆成有序子任务列表(可带当前屏幕上下文)。"""
    compiled_actions = compile_mobile_actions(goal)
    if compiled_actions:
        return describe_compiled_actions(compiled_actions)

    app_config = await get_runtime_app_config()
    llm = create_llm(
        app_config,
        model_name=app_config.runtime.models.mobile_planner_model,
        streaming=False,
    )
    structured = llm.with_structured_output(TaskPlan)
    human = f"目标:{goal}"
    if screen_analysis:
        human += f"\n\n当前手机界面:\n{screen_analysis}"
    with observation_context(phase="mobile_plan", agent="mobile_planner"):
        plan: TaskPlan = await structured.ainvoke(
            [
                SystemMessage(content=_PLANNER_SYSTEM),
                HumanMessage(content=human),
            ]
        )
    return plan.subtasks


_VISION_DESCRIBE = (
    "简要描述这张手机截图当前所在的界面、关键可见元素与可执行操作,中文,3-5 句。"
)


async def describe_screen(
    device_id: str,
    *,
    project_id: str | None = None,
    plan_id: str | None = None,
) -> str:
    """用视觉模型对当前屏幕做通用描述(供看屏规划/重规划参考)。"""
    mgr = MobileDeviceManager()
    capture = await capture_ready_screen(device_id, manager=mgr)
    shot = capture.screenshot
    app_config = await get_runtime_app_config()
    llm = create_llm(
        app_config,
        model_name=app_config.runtime.models.mobile_screen_model,
        streaming=False,
    )
    with observation_context(project_id=project_id, task_id=plan_id, phase="mobile_screen", agent="mobile_screen"):
        resp = await llm.ainvoke(
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": _VISION_DESCRIBE},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{shot.base64_data}"
                            },
                        },
                    ]
                )
            ]
        )
    if project_id:
        try:
            from api.db.mongodb import get_db
            from api.dao import mobile_artifacts as ma_dao

            await ma_dao.save_screenshot(
                get_db(),
                image_base64=shot.base64_data,
                project_id=project_id,
                task_id=plan_id,
                device_id=device_id,
                source="planned_screen",
                width=shot.width,
                height=shot.height,
                note=(
                    "planned task screen context "
                    f"(attempts={capture.attempts}, blank_frames={capture.blank_frames})"
                ),
            )
        except Exception:
            pass
    return resp.content if isinstance(resp.content, str) else str(resp.content)


_REPLAN_SYSTEM = (
    "你是手机自动化的规划层,正在「重规划」。前面某个子任务失败了。"
    "请基于:原始目标、已完成的子任务、失败的子任务、以及当前手机界面,"
    "给出**接下来要做的新子任务序列**(只含剩余步骤,不要重复已完成)。"
    "如判断目标已无法继续,返回空列表。只输出 JSON object,字段为 subtasks。"
)


async def replan_remaining(
    goal: str,
    completed: list[str],
    failed_subtask: str,
    screen_analysis: str,
) -> list[str]:
    """失败后基于当前界面重规划剩余子任务。"""
    app_config = await get_runtime_app_config()
    llm = create_llm(
        app_config,
        model_name=app_config.runtime.models.mobile_planner_model,
        streaming=False,
    )
    structured = llm.with_structured_output(TaskPlan)
    human = (
        f"原始目标:{goal}\n\n"
        f"已完成:{completed or '无'}\n\n"
        f"失败的子任务:{failed_subtask}\n\n"
        f"当前手机界面:\n{screen_analysis}"
    )
    with observation_context(phase="mobile_replan", agent="mobile_planner"):
        plan: TaskPlan = await structured.ainvoke(
            [SystemMessage(content=_REPLAN_SYSTEM), HumanMessage(content=human)]
        )
    return plan.subtasks


async def run_planned_task(
    device_id: str,
    goal: str,
    *,
    max_steps_per_subtask: int | None = None,
    screen_aware: bool = True,
    max_replans: int = 2,
    project_id: str | None = None,
    contact_id: str | None = None,
    owner: str | None = None,
    plan_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """
    规划 + 执行(流式)。关键改进:
    - 整个计划复用**同一个执行层 agent**,子任务间上下文(历史截图/动作)自动累积;
    - 子任务失败时,看当前屏幕**重规划**剩余步骤(最多 max_replans 次);
    - plan_id 即可作为 /agent/cancel 的 task_id 取消整轮。

    事件(stage):planning / screen / plan / subtask_start / exec /
    subtask_done / replanning / aborted / done / error / cancelled
    """
    plan_id = plan_id or uuid.uuid4().hex[:12]
    compiled_actions = compile_mobile_actions(goal)
    yield {
        "stage": "planning",
        "data": {
            "plan_id": plan_id,
            "project_id": project_id,
            "goal": goal,
            "mode": "compiled_tools" if compiled_actions else "planner",
        },
    }
    await _log_operation(
        operation_type="planned_task",
        device_id=device_id,
        project_id=project_id,
        task_id=plan_id,
        contact_id=contact_id,
        action="planning",
        data={"goal": goal, "screen_aware": screen_aware, "max_replans": max_replans},
    )

    wake_result = await wake_device(device_id, stay_on=True)
    yield {
        "stage": "device_ready",
        "data": {
            "plan_id": plan_id,
            "device_id": device_id,
            "wake_ok": bool(wake_result.get("ok")),
        },
    }

    if compiled_actions:
        subtasks = describe_compiled_actions(compiled_actions)
        yield {
            "stage": "plan",
            "data": {
                "plan_id": plan_id,
                "subtasks": subtasks,
                "mode": "compiled_tools",
            },
        }

        completed = 0
        last_done: dict[str, Any] | None = None
        try:
            async for event in run_compiled_actions_stream(
                device_id,
                goal,
                compiled_actions,
                task_id=plan_id,
                project_id=project_id,
            ):
                etype = str(event.get("type") or "")
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                if etype == "task_start":
                    continue
                if etype == "step":
                    index = max(0, int(data.get("step") or 1) - 1)
                    yield {
                        "stage": "subtask_start",
                        "data": {
                            "plan_id": plan_id,
                            "project_id": project_id,
                            "index": index,
                            "total": len(subtasks),
                            "task": subtasks[index] if index < len(subtasks) else "",
                        },
                    }
                    yield {"stage": "exec", "data": {"index": index, "event": event}}
                    success = bool(data.get("success", False))
                    await _log_operation(
                        operation_type="planned_step",
                        device_id=device_id,
                        project_id=project_id,
                        task_id=plan_id,
                        contact_id=contact_id,
                        action=str(data.get("action") or etype),
                        status="ok" if success else "failed",
                        message=str(data.get("message") or ""),
                        data={
                            "subtask": subtasks[index] if index < len(subtasks) else "",
                            "index": index,
                            **_log_data(data),
                        },
                    )
                    yield {
                        "stage": "subtask_done",
                        "data": {"index": index, "result": data, "success": success},
                    }
                    if success:
                        completed += 1
                    else:
                        yield {
                            "stage": "aborted",
                            "data": {
                                "plan_id": plan_id,
                                "reason": data.get("message") or "编译动作执行失败",
                            },
                        }
                        return
                elif etype == "done":
                    last_done = data
                    done_index = min(max(completed - 1, 0), len(subtasks) - 1)
                    yield {
                        "stage": "exec",
                        "data": {"index": done_index, "event": event},
                    }
        except asyncio.CancelledError:
            yield {"stage": "cancelled", "data": {"plan_id": plan_id}}
            raise

        success = bool((last_done or {}).get("success", completed == len(subtasks)))
        await _log_operation(
            operation_type="planned_task",
            device_id=device_id,
            project_id=project_id,
            task_id=plan_id,
            contact_id=contact_id,
            action="done" if success else "failed",
            status="ok" if success else "failed",
            data={"subtasks": len(subtasks), "completed": completed},
        )
        yield {
            "stage": "done",
            "data": {
                "plan_id": plan_id,
                "subtasks": len(subtasks),
                "completed": completed,
                "mode": "compiled_tools",
            },
        }
        return

    screen_ctx: str | None = None
    if screen_aware and _should_describe_screen_before_plan(goal):
        try:
            screen_ctx = await describe_screen(
                device_id, project_id=project_id, plan_id=plan_id
            )
            yield {"stage": "screen", "data": {"analysis": screen_ctx}}
        except Exception as exc:  # noqa: BLE001
            yield {"stage": "screen_error", "data": {"message": str(exc)}}

    try:
        subtasks = await plan_task(goal, screen_analysis=screen_ctx)
    except Exception as exc:  # noqa: BLE001
        yield {"stage": "error", "data": {"message": f"规划失败: {exc}"}}
        return
    if not subtasks:
        yield {"stage": "error", "data": {"message": "规划结果为空"}}
        return
    yield {"stage": "plan", "data": {"plan_id": plan_id, "subtasks": subtasks}}

    try:
        app_config = await get_runtime_app_config()
        agent = build_executor_agent(
            device_id, max_steps=max_steps_per_subtask, app_config=app_config
        )
    except Exception as exc:  # noqa: BLE001
        yield {"stage": "error", "data": {"message": f"执行层初始化失败: {exc}"}}
        return
    register_agent(plan_id, agent, owner=owner)

    completed: list[str] = []
    replans = 0
    try:
        i = 0
        while i < len(subtasks):
            sub = subtasks[i]
            yield {
                "stage": "subtask_start",
                "data": {
                    "plan_id": plan_id,
                    "project_id": project_id,
                    "index": i,
                    "total": len(subtasks),
                    "task": sub,
                },
            }
            await _log_operation(
                operation_type="planned_subtask",
                device_id=device_id,
                project_id=project_id,
                task_id=plan_id,
                contact_id=contact_id,
                action="subtask_start",
                data={"index": i, "task": sub},
            )
            last_done: dict[str, Any] | None = None
            try:
                async for event in agent.stream(sub):  # 同一 agent → 记得前面做了什么
                    public_event = _public_agent_event(event)
                    if public_event is None:
                        continue
                    event = public_event
                    event, screenshot_id = await _save_event_screenshot(
                        event,
                        device_id=device_id,
                        project_id=project_id,
                        task_id=plan_id,
                        contact_id=contact_id,
                    )
                    yield {"stage": "exec", "data": {"index": i, "event": event}}
                    data = event.get("data") if isinstance(event.get("data"), dict) else {}
                    etype = str(event.get("type") or "")
                    if etype in {"step", "done", "cancelled", "error"}:
                        await _log_operation(
                            operation_type=f"planned_{etype}",
                            device_id=device_id,
                            project_id=project_id,
                            task_id=plan_id,
                            contact_id=contact_id,
                            action=str(data.get("action") or etype),
                            status=(
                                "error" if etype == "error"
                                else "cancelled" if etype == "cancelled"
                                else "ok" if data.get("success", True)
                                else "failed"
                            ),
                            message=str(data.get("message") or ""),
                            data={"subtask": sub, "index": i, **_log_data(data)},
                            screenshot_id=screenshot_id,
                        )
                    if event.get("type") == "done":
                        last_done = event.get("data")
            except asyncio.CancelledError:
                yield {"stage": "cancelled", "data": {"plan_id": plan_id, "index": i}}
                raise

            success = bool((last_done or {}).get("success", False))
            yield {
                "stage": "subtask_done",
                "data": {"index": i, "result": last_done, "success": success},
            }

            if success:
                completed.append(sub)
                i += 1
                continue

            if replans < max_replans:
                replans += 1
                yield {
                    "stage": "replanning",
                    "data": {"plan_id": plan_id, "failed_index": i, "attempt": replans},
                }
                try:
                    cur = await describe_screen(
                        device_id, project_id=project_id, plan_id=plan_id
                    )
                    new_subs = await replan_remaining(goal, completed, sub, cur)
                except Exception as exc:  # noqa: BLE001
                    yield {"stage": "error", "data": {"message": f"重规划失败: {exc}"}}
                    return
                if not new_subs:
                    yield {
                        "stage": "aborted",
                        "data": {"plan_id": plan_id, "reason": "重规划判定无法继续"},
                    }
                    return
                subtasks = completed + new_subs
                i = len(completed)
                yield {
                    "stage": "plan",
                    "data": {
                        "plan_id": plan_id,
                        "subtasks": subtasks,
                        "replanned": True,
                    },
                }
                continue

            yield {
                "stage": "aborted",
                "data": {"plan_id": plan_id, "reason": "子任务失败且重规划次数用尽"},
            }
            return

        await _log_operation(
            operation_type="planned_task",
            device_id=device_id,
            project_id=project_id,
            task_id=plan_id,
            contact_id=contact_id,
            action="done",
            data={"subtasks": len(subtasks), "completed": len(completed)},
        )
        yield {
            "stage": "done",
            "data": {
                "plan_id": plan_id,
                "subtasks": len(subtasks),
                "completed": len(completed),
            },
        }
    finally:
        unregister_agent(plan_id)
