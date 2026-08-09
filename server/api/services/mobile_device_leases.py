"""手机后台任务租约服务。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Any

from api.services.mobile_execution import mobile_execution_lease
from core.logger import get_logger
from core.mobile.identity import resolve_device_key
from core.mobile.pool import DevicePool

logger = get_logger("mobile_device_leases")

@asynccontextmanager
async def background_device_lease(
    db: Any,
    *,
    device_id: str,
    run_task_id: str,
    requested_by: str = "",
) -> AsyncIterator[str]:
    """为后台手机任务申请跨进程独占租约，并保留人工预约语义。"""
    import asyncio

    device_key = await asyncio.to_thread(resolve_device_key, device_id)
    owner = f"collect:{run_task_id}"
    pool = DevicePool.get_instance()
    # 人工预约只表达谁有权启动任务，不再被后台任务改写或删除。
    await asyncio.to_thread(pool.ensure_owner, device_key, requested_by)
    logger.info("手机后台任务等待执行租约 device=%s run=%s", device_key, run_task_id)
    async with mobile_execution_lease(
        db,
        device_id=device_id,
        task_id=run_task_id,
        owner=owner,
        requested_by=requested_by,
        kind="mobile_collect",
        wait_timeout=24 * 60 * 60,
    ):
        # 排队期间预约可能被释放或转交，真正开始操作前必须再次确认。
        await asyncio.to_thread(pool.ensure_owner, device_key, requested_by)
        yield owner
