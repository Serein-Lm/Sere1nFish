"""手机后台任务租约服务。

统一处理交互租约到后台任务租约的原子转交，以及 MongoDB 持久化清理。
调用侧只表达任务发起人和运行实例，不直接操作设备池或预约集合。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Any

from api.dao import device_reservations as reservations_dao
from core.logger import get_logger
from core.mobile.identity import resolve_device_key
from core.mobile.pool import DevicePool

logger = get_logger("mobile_device_leases")

_DEVICE_QUEUE_LOCKS: dict[str, asyncio.Lock] = {}


def _device_queue_lock(device_key: str) -> asyncio.Lock:
    lock = _DEVICE_QUEUE_LOCKS.get(device_key)
    if lock is None:
        lock = asyncio.Lock()
        _DEVICE_QUEUE_LOCKS[device_key] = lock
    return lock


@asynccontextmanager
async def background_device_lease(
    db: Any,
    *,
    device_id: str,
    run_task_id: str,
    requested_by: str = "",
) -> AsyncIterator[str]:
    """为后台手机任务申请并持久化独占租约。"""
    device_key = await asyncio.to_thread(resolve_device_key, device_id)
    owner = f"collect:{run_task_id}"
    pool = DevicePool.get_instance()
    queue_lock = _device_queue_lock(device_key)
    if queue_lock.locked():
        logger.info(
            "手机任务进入等待队列 device=%s run=%s",
            device_key,
            run_task_id,
        )
    async with queue_lock:
        reservation = await asyncio.to_thread(
            pool.acquire_for_task,
            device_key,
            owner,
            initiated_by=requested_by,
            note="mobile_collect",
            device_id=device_id,
        )
        try:
            await reservations_dao.upsert_reservation(
                db,
                reservation.device_key,
                reservation.owner,
                note=reservation.note,
                since=reservation.since,
                device_id=device_id,
            )
        except Exception:
            await asyncio.to_thread(pool.release, device_key, owner, force=True)
            raise

        try:
            yield owner
        finally:
            try:
                await asyncio.to_thread(pool.release, device_key, owner, force=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("后台设备内存租约清理失败 device=%s: %s", device_id, exc)
            try:
                await reservations_dao.delete_reservation(db, device_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("后台设备持久化租约清理失败 device=%s: %s", device_id, exc)
