"""后台任务运行时恢复入口。"""
from __future__ import annotations

from typing import Any

from api.dao import device_reservations, mobile_collect, tasks


async def recover_interrupted_runtime(db: Any) -> dict[str, int]:
    """终结上一进程任务，并清理无法恢复的手机运行态。"""
    return {
        "tasks": await tasks.mark_interrupted_tasks(db),
        "mobile_task_defs": await mobile_collect.reset_interrupted_task_defs(db),
        "mobile_leases": await device_reservations.delete_background_reservations(db),
    }
