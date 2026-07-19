"""统一后台任务工具。

asyncio 事件循环只对 task 持弱引用,裸 asyncio.create_task 产生的
fire-and-forget 任务可能在运行中被 GC 静默取消。业务侧只表达"后台跑一个协程"
的意图,保活与异常记录收敛在这里。
"""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from core.logger import get_logger

logger = get_logger("core.background")

# 保活引用,防止运行中的后台任务被 GC 回收。
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def spawn_background(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str | None = None,
) -> asyncio.Task[Any]:
    """在当前事件循环中启动一个受保活管理的后台任务。

    - 持有强引用直到任务结束,避免被 GC 静默取消;
    - 任务异常统一记录,不向上冒泡影响调用方。
    """
    task = asyncio.create_task(coro, name=name)
    _BACKGROUND_TASKS.add(task)

    def _done(done: asyncio.Task[Any]) -> None:
        _BACKGROUND_TASKS.discard(done)
        if done.cancelled():
            return
        exc = done.exception()
        if exc is not None:
            logger.warning(f"后台任务异常 name={done.get_name()}: {exc}")

    task.add_done_callback(_done)
    return task


def background_task_count() -> int:
    """当前保活中的后台任务数量(供观测/自检)。"""
    return len(_BACKGROUND_TASKS)


async def cancel_background_tasks(*, timeout: float = 30.0) -> int:
    """Cancel and drain retained background work before runtime shutdown."""
    current = asyncio.current_task()
    tasks = [
        task
        for task in tuple(_BACKGROUND_TASKS)
        if task is not current and not task.done()
    ]
    if not tasks:
        return 0

    for task in tasks:
        task.cancel()
    _done, pending = await asyncio.wait(tasks, timeout=max(0.1, timeout))
    if pending:
        logger.warning(
            "后台任务关闭超时，仍有 %s 条等待事件循环终止",
            len(pending),
        )
    return len(tasks) - len(pending)
