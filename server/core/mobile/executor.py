"""
执行层桥接 — 复用 AutoGLM 现成的视觉 agent,配置/模型来自数据库运行时配置。

设计原则(见项目记忆):
- 执行层(截图→LLM→动作的视觉循环)直接复用 AutoGLM 的 create_agent / async_agent;
- 但模型配置不用 AutoGLM 的 config_manager,而是用本项目 AppConfig
  (Sere1nGraph runtime: base_url / api_key / models.mobile_executor);
- agent_type 默认 general-vision(OpenAI 兼容 function calling,适配 qwen-vl)。

事件(agent.stream 产出,原样透传给上层 SSE):
- {"type": "thinking", "data": {"chunk": str}}
- {"type": "step",     "data": {step, thinking, action, success, finished, message, screenshot}}
- {"type": "done",     "data": {message, steps, success, stop_reason?}}
- {"type": "cancelled","data": {message, stop_reason}}
- {"type": "error",    "data": {message}}
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

from AutoGLM_GUI.agents import create_agent
from AutoGLM_GUI.config import AgentConfig, ModelConfig
from AutoGLM_GUI.devices.adb_device import ADBDevice
from core.mobile.command_executor import (
    compile_mobile_actions,
    run_compiled_actions_stream,
)
from core.mobile.manager import MobileDeviceManager
from core.mobile.screen_capture import wake_device
from core.llm_params import disable_thinking_extra_body
from core.mobile.llm_usage import instrument_agent

# 正在运行的执行层 agent: task_id -> agent (供取消)
_running: dict[str, Any] = {}
_running_owners: dict[str, str | None] = {}


def _log_data(data: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(data)
    if "screenshot" in cleaned:
        cleaned["screenshot"] = "<stored-on-disk>"
    return cleaned


def _public_agent_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize public mobile execution events before persistence/streaming."""
    return event


async def _log_operation(
    *,
    operation_type: str,
    device_id: str,
    project_id: str | None,
    task_id: str | None,
    contact_id: str | None = None,
    action: str = "",
    status: str = "ok",
    message: str = "",
    data: dict[str, Any] | None = None,
    screenshot_id: str | None = None,
) -> None:
    if not project_id:
        return
    try:
        from api.db.mongodb import get_db
        from api.dao import mobile_artifacts as ma_dao

        await ma_dao.log_operation(
            get_db(),
            operation_type=operation_type,
            device_id=device_id,
            project_id=project_id,
            task_id=task_id,
            contact_id=contact_id,
            action=action,
            status=status,
            message=message,
            data=data,
            screenshot_id=screenshot_id,
        )
    except Exception:
        pass


async def _save_event_screenshot(
    event: dict[str, Any],
    *,
    device_id: str,
    project_id: str | None,
    task_id: str | None,
    contact_id: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    if not project_id:
        return event, None
    data = event.get("data")
    if not isinstance(data, dict):
        return event, None
    image_base64 = data.get("screenshot")
    if not isinstance(image_base64, str) or not image_base64.strip():
        return event, None
    try:
        from api.db.mongodb import get_db
        from api.dao import mobile_artifacts as ma_dao

        saved = await ma_dao.save_screenshot(
            get_db(),
            image_base64=image_base64,
            project_id=project_id,
            task_id=task_id,
            device_id=device_id,
            contact_id=contact_id,
            source="agent_step",
            note=str(data.get("message") or data.get("action") or ""),
            meta={
                "step": data.get("step"),
                "action": data.get("action"),
                "success": data.get("success"),
                "finished": data.get("finished"),
            },
        )
        enriched = dict(event)
        enriched_data = dict(data)
        enriched_data["screenshot_id"] = saved["screenshot_id"]
        enriched_data["screenshot_url"] = saved["url"]
        enriched["data"] = enriched_data
        return enriched, saved["screenshot_id"]
    except Exception:
        return event, None


def _build_model_config(app_config: Any | None = None) -> ModelConfig:
    """用本项目 AppConfig 构造 AutoGLM ModelConfig(绕过 AutoGLM config_manager)。"""
    if app_config is None:
        raise RuntimeError("缺少数据库运行时配置,无法启动执行层 agent。")
    rt = app_config.runtime
    if not rt.base_url:
        raise RuntimeError(
            "数据库 runtime.base_url 未配置,无法启动执行层 agent。"
            "请先在前端配置 runtime.base_url / api_key / models.mobile_executor。"
        )
    executor_max_tokens = app_config.mobile.executor_max_tokens
    model_name = rt.models.mobile_executor_model
    if executor_max_tokens is None and model_name == "gui-plus":
        max_tokens = None
    else:
        max_tokens = executor_max_tokens or rt.max_tokens

    return ModelConfig(
        base_url=rt.base_url,
        api_key=rt.api_key or "EMPTY",
        model_name=model_name,
        max_tokens=max_tokens,
        temperature=rt.temperature,
        top_p=rt.top_p,
        frequency_penalty=rt.frequency_penalty,
        extra_body=disable_thinking_extra_body(
            {"vl_high_resolution_images": True}
        ),
    )


def build_executor_agent(
    device_id: str,
    *,
    agent_type: str = "general-vision",
    max_steps: int | None = None,
    system_prompt: str | None = None,
    app_config: Any | None = None,
    project_id: str = "",
    task_id: str = "",
) -> Any:
    """构造一个 AutoGLM 执行层 agent(配置来自本项目)。"""
    model_config = _build_model_config(app_config)
    adb_device_id = MobileDeviceManager().resolve_adb_device_id(device_id)

    kwargs: dict[str, Any] = {"device_id": adb_device_id}
    if max_steps is not None:
        kwargs["max_steps"] = max_steps
    if system_prompt is not None:
        kwargs["system_prompt"] = system_prompt
    agent_config = AgentConfig(**kwargs)

    device = ADBDevice(adb_device_id)
    agent = create_agent(
        agent_type=agent_type,
        model_config=model_config,
        agent_config=agent_config,
        agent_specific_config={},
        device=device,
    )
    return instrument_agent(
        agent,
        model=model_config.model_name,
        project_id=project_id,
        task_id=task_id,
    )


async def run_task_stream(
    device_id: str,
    task: str,
    *,
    agent_type: str = "general-vision",
    max_steps: int | None = None,
    task_id: str | None = None,
    project_id: str | None = None,
    contact_id: str | None = None,
    owner: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """
    让执行层 agent 自助完成一个手机任务,流式产出事件。

    首个事件为 {"type": "task_start", "data": {task_id, device_id, task}},
    之后透传 agent.stream 的全部事件。task_id 可用于 /agent/cancel 取消。
    """
    task_id = task_id or uuid.uuid4().hex[:12]
    wake_result = await wake_device(device_id, stay_on=True)
    yield {
        "type": "device_ready",
        "data": {
            "task_id": task_id,
            "device_id": device_id,
            "wake_ok": bool(wake_result.get("ok")),
        },
    }
    compiled_actions = compile_mobile_actions(task)
    if compiled_actions:
        async for event in run_compiled_actions_stream(
            device_id,
            task,
            compiled_actions,
            task_id=task_id,
            project_id=project_id,
        ):
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            etype = str(event.get("type") or "")
            if etype == "task_start":
                await _log_operation(
                    operation_type="agent_task",
                    device_id=device_id,
                    project_id=project_id,
                    task_id=task_id,
                    contact_id=contact_id,
                    action="start",
                    data={
                        "task": task,
                        "agent_type": "compiled_tools",
                        "actions": [a.to_public_action() for a in compiled_actions],
                    },
                )
            elif etype in {"step", "done", "cancelled", "error"}:
                await _log_operation(
                    operation_type=f"agent_{etype}",
                    device_id=device_id,
                    project_id=project_id,
                    task_id=task_id,
                    contact_id=contact_id,
                    action=str(data.get("action") or etype),
                    status=(
                        "error" if etype == "error"
                        else "cancelled" if etype == "cancelled"
                        else "ok" if data.get("success", True)
                        else "failed"
                    ),
                    message=str(data.get("message") or ""),
                    data=_log_data(data),
                )
            yield event
        return

    from api.services.runtime_config import get_runtime_app_config

    app_config = await get_runtime_app_config()
    agent = build_executor_agent(
        device_id,
        agent_type=agent_type,
        max_steps=max_steps,
        app_config=app_config,
        project_id=str(project_id or ""),
        task_id=task_id,
    )
    _running[task_id] = agent
    _running_owners[task_id] = owner

    yield {
        "type": "task_start",
        "data": {
            "task_id": task_id,
            "project_id": project_id,
            "device_id": device_id,
            "task": task,
        },
    }
    await _log_operation(
        operation_type="agent_task",
        device_id=device_id,
        project_id=project_id,
        task_id=task_id,
        contact_id=contact_id,
        action="start",
        data={"task": task, "agent_type": agent_type, "max_steps": max_steps},
    )
    try:
        async for event in agent.stream(task):
            public_event = _public_agent_event(event)
            if public_event is None:
                continue
            event = public_event
            event, screenshot_id = await _save_event_screenshot(
                event,
                device_id=device_id,
                project_id=project_id,
                task_id=task_id,
                contact_id=contact_id,
            )
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            etype = str(event.get("type") or "")
            if etype in {"step", "done", "cancelled", "error"}:
                await _log_operation(
                    operation_type=f"agent_{etype}",
                    device_id=device_id,
                    project_id=project_id,
                    task_id=task_id,
                    contact_id=contact_id,
                    action=str(data.get("action") or etype),
                    status=(
                        "error" if etype == "error"
                        else "cancelled" if etype == "cancelled"
                        else "ok" if data.get("success", True)
                        else "failed"
                    ),
                    message=str(data.get("message") or ""),
                    data=_log_data(data),
                    screenshot_id=screenshot_id,
                )
            yield event
    except asyncio.CancelledError:
        await _log_operation(
            operation_type="agent_cancelled",
            device_id=device_id,
            project_id=project_id,
            task_id=task_id,
            contact_id=contact_id,
            action="cancelled",
            status="cancelled",
            message="Task cancelled",
        )
        yield {
            "type": "cancelled",
            "data": {"message": "Task cancelled", "stop_reason": "user_stopped"},
        }
        raise
    except Exception as exc:
        await _log_operation(
            operation_type="agent_error",
            device_id=device_id,
            project_id=project_id,
            task_id=task_id,
            contact_id=contact_id,
            action="error",
            status="error",
            message=str(exc),
        )
        raise
    finally:
        _running.pop(task_id, None)
        _running_owners.pop(task_id, None)


async def cancel_task(
    task_id: str,
    *,
    owner: str | None = None,
    is_admin: bool = False,
) -> bool:
    """取消正在运行的执行层任务。"""
    agent = _running.get(task_id)
    if agent is None:
        return False
    task_owner = _running_owners.get(task_id)
    if task_owner and owner != task_owner and not is_admin:
        return False
    await agent.cancel()
    return True


def running_task_ids() -> list[str]:
    """当前正在运行的执行层任务 id 列表。"""
    return list(_running.keys())


def register_agent(task_id: str, agent: Any, *, owner: str | None = None) -> None:
    """登记一个正在运行的 agent(供 /agent/cancel 取消)。"""
    _running[task_id] = agent
    _running_owners[task_id] = owner


def unregister_agent(task_id: str) -> None:
    """注销已结束的 agent。"""
    _running.pop(task_id, None)
    _running_owners.pop(task_id, None)
