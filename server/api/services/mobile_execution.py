"""Unified cross-process execution guard for every mutating mobile workflow."""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from api.dao import mobile_execution_leases as leases_dao
from core.logger import get_logger
from core.mobile.identity import resolve_device_key


logger = get_logger("mobile_execution")
_RUNTIME_ID = f"{os.getpid()}:{uuid.uuid4().hex}"
_DEFAULT_TTL_SECONDS = 90.0
_RENEW_INTERVAL_SECONDS = 15.0
_CONTROL_POLL_SECONDS = 1.0


class MobileExecutionBusyError(RuntimeError):
    def __init__(self, device_id: str, active: dict[str, Any] | None = None) -> None:
        self.device_id = device_id
        self.active = active or {}
        owner = str(self.active.get("owner") or "其他任务")
        task_id = str(self.active.get("task_id") or "")
        suffix = f"（任务 {task_id}）" if task_id else ""
        super().__init__(f"设备 {device_id} 正由 {owner} 使用{suffix}")


class MobileExecutionLeaseLostError(RuntimeError):
    pass


@dataclass(slots=True)
class MobileExecutionLease:
    db: Any
    device_key: str
    device_id: str
    lease_id: str
    task_id: str
    owner: str
    requested_by: str
    kind: str
    ttl_seconds: float = _DEFAULT_TTL_SECONDS
    cancel_requested: asyncio.Event = field(default_factory=asyncio.Event)
    lease_lost: asyncio.Event = field(default_factory=asyncio.Event)
    _monitor_task: asyncio.Task[Any] | None = field(default=None, init=False)
    _bound_task: asyncio.Task[Any] | None = field(default=None, init=False)
    _closed: bool = field(default=False, init=False)

    def start(self) -> "MobileExecutionLease":
        if self._monitor_task is None:
            self._monitor_task = asyncio.create_task(
                self._monitor(),
                name=f"mobile-lease:{self.task_id}",
            )
        return self

    def bind_current_task(self) -> "MobileExecutionLease":
        """Cancel the owning coroutine when another process requests cancellation."""
        self._bound_task = asyncio.current_task()
        return self

    def _request_local_stop(self) -> None:
        bound = self._bound_task
        if bound is not None and not bound.done():
            bound.cancel()

    def _mark_unavailable(self, reason: str) -> None:
        self.lease_lost.set()
        self.cancel_requested.set()
        self._request_local_stop()
        logger.error(
            "手机执行租约失效 device=%s task=%s lease=%s reason=%s",
            self.device_key,
            self.task_id,
            self.lease_id,
            reason,
        )

    async def _monitor(self) -> None:
        next_renewal = time.monotonic() + _RENEW_INTERVAL_SECONDS
        last_confirmed = time.monotonic()
        validation_timeout = min(15.0, max(3.0, self.ttl_seconds / 6.0))
        validation_failures = 0
        while not self._closed:
            await asyncio.sleep(_CONTROL_POLL_SECONDS)
            try:
                document = await leases_dao.get_by_lease(
                    self.db,
                    lease_id=self.lease_id,
                    runtime_id=_RUNTIME_ID,
                )
                if not document:
                    self._mark_unavailable("租约不存在或已过期")
                    return
                if document.get("cancel_requested_at"):
                    self.cancel_requested.set()
                    self._request_local_stop()
                if time.monotonic() >= next_renewal:
                    renewed = await leases_dao.renew(
                        self.db,
                        device_key=self.device_key,
                        lease_id=self.lease_id,
                        runtime_id=_RUNTIME_ID,
                        ttl_seconds=self.ttl_seconds,
                    )
                    if not renewed:
                        self._mark_unavailable("续租条件不再匹配")
                        return
                    next_renewal = time.monotonic() + _RENEW_INTERVAL_SECONDS
                last_confirmed = time.monotonic()
                validation_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                validation_failures += 1
                if validation_failures == 1:
                    logger.warning(
                        "手机执行租约校验暂时失败 device=%s task=%s: %s",
                        self.device_key,
                        self.task_id,
                        exc,
                    )
                if time.monotonic() - last_confirmed >= validation_timeout:
                    self._mark_unavailable(
                        f"连续 {validation_failures} 次无法校验租约"
                    )
                    return

    def raise_if_unavailable(self) -> None:
        if self.lease_lost.is_set():
            raise MobileExecutionLeaseLostError(
                f"设备 {self.device_id} 的执行租约已失效，任务已停止"
            )
        if self.cancel_requested.is_set():
            raise asyncio.CancelledError

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
            self._monitor_task = None
        try:
            await leases_dao.release(
                self.db,
                device_key=self.device_key,
                lease_id=self.lease_id,
                runtime_id=_RUNTIME_ID,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "手机执行租约释放失败 device=%s task=%s: %s",
                self.device_key,
                self.task_id,
                exc,
            )


async def acquire_mobile_execution(
    db: Any,
    *,
    device_id: str,
    task_id: str,
    owner: str,
    requested_by: str,
    kind: str,
    wait_timeout: float = 0.0,
    ttl_seconds: float = _DEFAULT_TTL_SECONDS,
) -> MobileExecutionLease:
    device_key = await asyncio.to_thread(resolve_device_key, device_id)
    lease_id = uuid.uuid4().hex
    deadline = time.monotonic() + max(0.0, wait_timeout)
    while True:
        document = await leases_dao.try_acquire(
            db,
            device_key=device_key,
            device_id=device_id,
            lease_id=lease_id,
            runtime_id=_RUNTIME_ID,
            task_id=task_id,
            owner=owner,
            requested_by=requested_by,
            kind=kind,
            ttl_seconds=ttl_seconds,
        )
        if document:
            return MobileExecutionLease(
                db=db,
                device_key=device_key,
                device_id=device_id,
                lease_id=lease_id,
                task_id=task_id,
                owner=owner,
                requested_by=requested_by,
                kind=kind,
                ttl_seconds=ttl_seconds,
            ).start()
        if time.monotonic() >= deadline:
            active = await leases_dao.get_active_by_device(db, device_key)
            raise MobileExecutionBusyError(device_id, active)
        await asyncio.sleep(min(0.5, max(0.05, deadline - time.monotonic())))


@asynccontextmanager
async def mobile_execution_lease(
    db: Any,
    **kwargs: Any,
) -> AsyncIterator[MobileExecutionLease]:
    lease = await acquire_mobile_execution(db, **kwargs)
    lease.bind_current_task()
    try:
        yield lease
    finally:
        await lease.close()


async def request_mobile_execution_cancel(
    db: Any,
    *,
    task_id: str,
    requested_by: str,
    is_admin: bool = False,
) -> bool:
    return await leases_dao.request_cancel(
        db,
        task_id=task_id,
        requested_by=requested_by,
        is_admin=is_admin,
    )
