from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_mobile_execution_lease_is_exclusive_and_conditionally_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import mobile_execution

    active: dict[str, Any] | None = None
    lock = asyncio.Lock()

    async def try_acquire(_db: Any, **fields: Any):
        nonlocal active
        async with lock:
            if active is not None:
                return None
            active = dict(fields)
            return dict(active)

    async def get_active(_db: Any, _device_key: str):
        return dict(active) if active else None

    async def get_by_lease(_db: Any, **query: Any):
        if not active or active.get("lease_id") != query.get("lease_id"):
            return None
        return dict(active)

    async def renew(_db: Any, **_query: Any):
        return True

    async def release(_db: Any, **query: Any):
        nonlocal active
        if active and all(active.get(k) == query.get(k) for k in ("device_key", "lease_id", "runtime_id")):
            active = None
            return True
        return False

    monkeypatch.setattr(mobile_execution, "resolve_device_key", lambda _value: "serial-1")
    monkeypatch.setattr(mobile_execution.leases_dao, "try_acquire", try_acquire)
    monkeypatch.setattr(mobile_execution.leases_dao, "get_active_by_device", get_active)
    monkeypatch.setattr(mobile_execution.leases_dao, "get_by_lease", get_by_lease)
    monkeypatch.setattr(mobile_execution.leases_dao, "renew", renew)
    monkeypatch.setattr(mobile_execution.leases_dao, "release", release)

    first = await mobile_execution.acquire_mobile_execution(
        object(),
        device_id="device-a",
        task_id="task-a",
        owner="agent:user-a",
        requested_by="user-a",
        kind="mobile_agent",
    )
    with pytest.raises(mobile_execution.MobileExecutionBusyError):
        await mobile_execution.acquire_mobile_execution(
            object(),
            device_id="device-a",
            task_id="task-b",
            owner="agent:admin",
            requested_by="admin",
            kind="mobile_agent",
        )
    await first.close()

    second = await mobile_execution.acquire_mobile_execution(
        object(),
        device_id="device-a",
        task_id="task-c",
        owner="agent:user-c",
        requested_by="user-c",
        kind="mobile_agent",
    )
    assert second.device_key == "serial-1"
    await second.close()


def test_contact_identity_is_scoped_by_device_and_app_instance() -> None:
    from core.mobile.chat_assist import derive_contact_id

    base = derive_contact_id(
        "微信",
        "同名联系人",
        device_key="serial-a",
        app_instance="primary",
    )
    assert base == derive_contact_id(
        "微信",
        "同名联系人",
        device_key="serial-a",
        app_instance="primary",
    )
    assert base != derive_contact_id(
        "微信",
        "同名联系人",
        device_key="serial-b",
        app_instance="primary",
    )
    assert base != derive_contact_id(
        "微信",
        "同名联系人",
        device_key="serial-a",
        app_instance="secondary",
    )


def test_transient_identity_fallback_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.mobile import identity

    identity.forget()
    responses = iter(
        [
            SimpleNamespace(returncode=1, stdout=""),
            SimpleNamespace(returncode=0, stdout="hardware-serial\n"),
        ]
    )
    monkeypatch.setattr(identity.subprocess, "run", lambda *_a, **_k: next(responses))

    assert identity.resolve_device_key("10.0.0.2:5555") == "10.0.0.2:5555"
    assert identity.resolve_device_key("10.0.0.2:5555") == "hardware-serial"
    assert identity.resolve_device_key("10.0.0.2:5555") == "hardware-serial"


def test_agent_context_compaction_removes_all_historical_images() -> None:
    from AutoGLM_GUI.agents.base.async_agent_base import AsyncAgentBase
    from AutoGLM_GUI.model import MessageBuilder

    initial = MessageBuilder.create_system_message("system")
    holder = SimpleNamespace(
        _initial_system_message=initial,
        _context=[initial],
    )
    for index in range(80):
        holder._context.append(
            MessageBuilder.create_user_message(
                text=f"screen-{index}",
                image_base64="QUJD",
            )
        )

    AsyncAgentBase._compact_context(holder)

    assert len(holder._context) <= 65
    serialized = str(holder._context)
    assert "image_url" not in serialized
    assert "QUJD" not in serialized


def test_mobile_prompts_are_available_through_runtime_loader() -> None:
    from core.mobile.prompt_runtime import load_mobile_prompt

    assert "手机自动化规划层" in load_mobile_prompt("mobile_agent/planner")
    rendered = load_mobile_prompt(
        "mobile_collect/screen_summary",
        {"app_name": "微信", "keyword": "测试"},
    )
    assert "微信" in rendered
    assert "测试" in rendered
    assert "{{" not in rendered


def test_keyword_checkpoint_key_changes_with_definition_or_target() -> None:
    from api.dao.mobile_collect import keyword_checkpoint_key

    first = keyword_checkpoint_key(
        definition_fingerprint="definition-a",
        keyword="关键词",
        target_id="target-a",
    )
    assert first == keyword_checkpoint_key(
        definition_fingerprint="definition-a",
        keyword="关键词",
        target_id="target-a",
    )
    assert first != keyword_checkpoint_key(
        definition_fingerprint="definition-b",
        keyword="关键词",
        target_id="target-a",
    )
    assert first != keyword_checkpoint_key(
        definition_fingerprint="definition-a",
        keyword="关键词",
        target_id="target-b",
    )


@pytest.mark.asyncio
async def test_auto_chat_start_failure_cleans_session_and_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.db import mongodb
    from api.services import mobile_execution
    from core.mobile import auto_chat, identity

    class FakeLease:
        closed = False

        async def close(self) -> None:
            self.closed = True

    lease = FakeLease()

    async def acquire(*_args: Any, **_kwargs: Any) -> FakeLease:
        return lease

    def fail_create_task(coro: Any) -> None:
        coro.close()
        raise RuntimeError("event loop unavailable")

    monkeypatch.setattr(mongodb, "get_db", lambda: object())
    monkeypatch.setattr(mobile_execution, "acquire_mobile_execution", acquire)
    monkeypatch.setattr(identity, "resolve_device_key", lambda _value: "serial-1")
    monkeypatch.setattr(auto_chat.asyncio, "create_task", fail_create_task)

    manager = auto_chat.AutoChatManager()
    with pytest.raises(RuntimeError, match="event loop unavailable"):
        await manager.start("device-a", owner="admin")

    assert manager._sessions == {}
    assert manager._tasks == {}
    assert manager._leases == {}
    assert lease.closed is True
