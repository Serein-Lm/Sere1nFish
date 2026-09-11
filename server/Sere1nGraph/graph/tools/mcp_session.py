"""Shared MCP session ownership and bounded recovery for browser adapters."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from core.async_runtime import await_with_hard_timeout
from core.logger import get_logger

logger = get_logger("mcp_session")


@asynccontextmanager
async def bounded_mcp_session(
    client: MultiServerMCPClient,
    server_name: str,
    *,
    close_timeout: float = 10,
):
    """由专属 task 持有 MCP 上下文，保证 AnyIO 在同一 task 中进出。"""
    loop = asyncio.get_running_loop()
    session_ready: asyncio.Future[Any] = loop.create_future()
    close_requested = asyncio.Event()

    async def _session_owner() -> None:
        try:
            async with client.session(server_name) as session:
                if not session_ready.done():
                    session_ready.set_result(session)
                await close_requested.wait()
        except BaseException as exc:
            if not session_ready.done():
                session_ready.set_exception(exc)
                return
            raise

    owner = asyncio.create_task(
        _session_owner(),
        name=f"mcp-session:{server_name}",
    )
    try:
        session = await session_ready
    except BaseException:
        owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)
        raise

    body_error: BaseException | None = None
    try:
        yield session
    except BaseException as exc:
        body_error = exc
        raise
    finally:
        close_requested.set()
        try:
            await await_with_hard_timeout(owner, close_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "MCP stdio 会话关闭超过 %.0fs，已取消会话 owner | server=%s",
                close_timeout,
                server_name,
            )
        except asyncio.CancelledError:
            raise
        except BaseException:
            if body_error is None:
                raise
            logger.warning(
                "MCP stdio 会话清理失败，保留原始 Agent 异常 | server=%s",
                server_name,
                exc_info=True,
            )


class McpRecoveryExhausted(RuntimeError):
    """The bounded session recovery budget has been consumed."""


class RecoveringMcpSession:
    """Reuse healthy sessions and discard a timed-out session before the next call.

    Failed actions are never replayed here: the browser adapter decides whether
    to continue with another source. The caller must issue calls sequentially.
    """

    def __init__(
        self, client: MultiServerMCPClient, server_name: str, *,
        call_timeout: float = 24, close_timeout: float = 10,
        max_reconnects: int = 6, task_id: str = "",
    ) -> None:
        self.client = client
        self.server_name = server_name
        self.call_timeout = call_timeout
        self.close_timeout = close_timeout
        self.max_reconnects = max(0, int(max_reconnects))
        self.task_id = task_id
        self._context: Any = None
        self._session: Any = None
        self._timeouts = 0

    async def __aenter__(self) -> RecoveringMcpSession:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self._close(exc_type, exc, traceback)

    async def _close(self, exc_type=None, exc=None, traceback=None) -> None:
        context, self._context = self._context, None
        self._session = None
        if context is not None:
            await context.__aexit__(exc_type, exc, traceback)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if self._timeouts > self.max_reconnects:
            raise McpRecoveryExhausted(
                f"MCP {self.server_name} 会话重建已达到 {self.max_reconnects} 次上限"
            )
        if self._session is None:
            context = bounded_mcp_session(
                self.client, self.server_name, close_timeout=self.close_timeout,
            )
            self._session = await context.__aenter__()
            self._context = context
        try:
            return await await_with_hard_timeout(
                self._session.call_tool(name, arguments), self.call_timeout,
            )
        except TimeoutError as exc:
            self._timeouts += 1
            logger.warning(
                "MCP 调用超时，关闭当前会话 | task=%s server=%s tool=%s timeout=%s resets=%s",
                self.task_id, self.server_name, name, self.call_timeout, self._timeouts,
            )
            await self._close(type(exc), exc, exc.__traceback__)
            raise TimeoutError(
                f"MCP {self.server_name}.{name} 超过 {self.call_timeout:g}s，已关闭超时会话"
            ) from exc
