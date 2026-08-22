"""Bounded asyncio task execution and cancellation helpers."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable
from typing import Any, TypeVar


ResultT = TypeVar("ResultT")


def consume_task_result(task: asyncio.Future[Any]) -> None:
    """Retrieve a detached task result so late failures are not left unobserved."""
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


def cancel_and_detach(task: asyncio.Future[Any]) -> None:
    """Request cancellation without waiting for a cancellation-resistant awaitable."""
    if not task.done():
        task.cancel()
    task.add_done_callback(consume_task_result)


async def await_with_hard_timeout(
    awaitable: Awaitable[ResultT],
    timeout: float,
) -> ResultT:
    """Await up to ``timeout`` without waiting indefinitely for cancellation cleanup.

    ``asyncio.wait_for`` waits for the cancelled child to finish. Third-party SDKs
    may suppress cancellation during cleanup, turning a timeout into an unbounded
    wait. This helper detaches that cleanup after requesting cancellation.
    """
    if timeout <= 0:
        return await awaitable

    task = asyncio.ensure_future(awaitable)
    try:
        done, _pending = await asyncio.wait({task}, timeout=float(timeout))
    except BaseException:
        cancel_and_detach(task)
        raise
    if task in done:
        return task.result()

    cancel_and_detach(task)
    raise asyncio.TimeoutError


async def cancel_tasks_bounded(
    tasks: Iterable[asyncio.Task[Any]],
    *,
    timeout: float,
) -> set[asyncio.Task[Any]]:
    """Cancel tasks and return any that did not finish within the grace period."""
    active = {task for task in tasks if not task.done()}
    if not active:
        return set()
    for task in active:
        task.add_done_callback(consume_task_result)
        task.cancel()
    _done, pending = await asyncio.wait(active, timeout=max(0.0, float(timeout)))
    return set(pending)
