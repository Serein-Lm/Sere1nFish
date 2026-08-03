from __future__ import annotations

import asyncio

import pytest

from Sere1nGraph.graph.agents.runtime import _await_tool_call, _bounded_mcp_session


@pytest.mark.asyncio
async def test_tool_timeout_does_not_wait_for_slow_cancellation() -> None:
    async def slow_cancel() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await asyncio.sleep(0.1)

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(asyncio.TimeoutError):
        await _await_tool_call(slow_cancel(), 0.01)
    assert loop.time() - started < 0.08

    # Let the detached cancellation branch finish so the test leaves no task behind.
    await asyncio.sleep(0.12)


@pytest.mark.asyncio
async def test_mcp_session_cleanup_is_bounded() -> None:
    class SlowContext:
        entered_task = None

        async def __aenter__(self):
            self.entered_task = asyncio.current_task()
            return object()

        async def __aexit__(self, *_exc_info):
            assert asyncio.current_task() is self.entered_task
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await asyncio.sleep(0.1)

    class Client:
        def session(self, _server_name):
            return SlowContext()

    loop = asyncio.get_running_loop()
    started = loop.time()
    async with _bounded_mcp_session(
        Client(),
        "test-server",
        close_timeout=0.01,
    ):
        pass
    assert loop.time() - started < 0.08
    await asyncio.sleep(0.12)
