"""Explicit browser lease identity, owner fencing and execution budgets."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from uuid import uuid4

from core.logger import get_logger

logger = get_logger("browser_manager.leases")


@dataclass(frozen=True)
class BrowserLeaseRequest:
    lease_id: str
    purpose: str = "general"
    project_id: str = ""
    task_id: str = ""
    owner_token: str = field(default_factory=lambda: uuid4().hex, repr=False)
    owner_task: asyncio.Task | None = field(default=None, repr=False, compare=False)
    wait_timeout_seconds: float = 60.0
    max_hold_seconds: float | None = None
    execution_deadline: float | None = None
    priority_class: str = ""

    def __post_init__(self):
        if not self.lease_id or not self.owner_token or self.wait_timeout_seconds <= 0:
            raise ValueError("浏览器租约必须有身份、拥有者和正数等待预算")
        if self.max_hold_seconds is not None and self.max_hold_seconds <= 0:
            raise ValueError("浏览器租约执行预算必须为正数")


class BrowserLeaseConflict(RuntimeError):
    """The supplied identity belongs to another owner or is still acquiring."""


@dataclass
class BrowserLease:
    request: BrowserLeaseRequest
    queued_at: float = field(default_factory=time.monotonic)
    acquired_at: float | None = None
    deadline: float | None = None
    endpoint: str = ""
    expiry_reported: bool = False


class BrowserLeaseRegistry:
    def __init__(self):
        self.leases: dict[str, BrowserLease] = {}

    def reserve(self, request: BrowserLeaseRequest) -> tuple[BrowserLease, bool]:
        previous = self.leases.get(request.lease_id)
        if previous:
            if previous.request == request and previous.endpoint and not self.termination_reason(request.lease_id):
                return previous, True
            raise BrowserLeaseConflict(f"浏览器租约已被占用: {request.lease_id}")
        lease = self.leases[request.lease_id] = BrowserLease(request)
        self.event("queued", lease)
        return lease, False

    def assigned(self, lease: BrowserLease, endpoint: str, default_hold_seconds: float) -> None:
        now = time.monotonic()
        lease.acquired_at = now
        lease.endpoint = endpoint
        hold = lease.request.max_hold_seconds
        lease.deadline = lease.request.execution_deadline
        if lease.deadline is None:
            lease.deadline = now + (hold if hold is not None else default_hold_seconds)
        elif hold is not None:
            lease.deadline = min(lease.deadline, now + hold)
        self.event("assigned", lease)

    def get(self, lease_id: str | None) -> BrowserLease | None:
        return self.leases.get(lease_id or "")

    def matches(self, lease_id: str, owner_token: str | None) -> bool:
        lease = self.get(lease_id)
        return owner_token is None or bool(lease and lease.request.owner_token == owner_token)

    def release(self, lease_id: str | None, owner_token: str | None = None) -> BrowserLease | None:
        if not lease_id or not self.matches(lease_id, owner_token):
            return None
        lease = self.leases.pop(lease_id, None)
        if lease:
            self.event("released", lease)
        return lease

    def termination_reason(self, lease_id: str | None) -> str:
        lease = self.get(lease_id)
        if not lease:
            return ""
        if lease.request.owner_task is not None and lease.request.owner_task.done():
            return "owner_exited"
        if lease.deadline is not None and time.monotonic() >= lease.deadline and not lease.expiry_reported:
            return "execution_budget_exhausted"
        return ""

    def describe(self, lease_id: str | None) -> dict:
        lease = self.get(lease_id)
        if not lease:
            return {}
        owner = lease.request.owner_task
        return {"lease_id": lease.request.lease_id, "project_id": lease.request.project_id,
                "task_id": lease.request.task_id, "owner_alive": None if owner is None else not owner.done(),
                "remaining_seconds": None if lease.deadline is None else max(0, round(lease.deadline-time.monotonic(), 1))}

    @staticmethod
    def event(event: str, lease: BrowserLease) -> None:
        now = time.monotonic()
        request = lease.request
        logger.info("event_type=browser_lease_%s project_id=%s task_id=%s lease_id=%s purpose=%s "
                    "wait_seconds=%.3f hold_seconds=%.3f", event, request.project_id, request.task_id,
                    request.lease_id, request.purpose, (lease.acquired_at or now)-lease.queued_at,
                    now-lease.acquired_at if lease.acquired_at is not None else 0)
