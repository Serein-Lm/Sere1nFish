"""Event-driven browser admission, fair by project within workload classes."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

from .leases import BrowserLeaseRequest

_WORKLOAD_CLASSES = {
    "url_scan": "batch", "persona_research": "batch", "target_research": "batch",
    "scholar_contact": "batch", "xhs": "batch",
}


def register_browser_workload(purpose: str, priority_class: str) -> None:
    if priority_class not in {"batch", "interactive"}:
        raise ValueError("未知浏览器工作负载类别")
    _WORKLOAD_CLASSES[purpose] = priority_class


def workload_class(purpose: str) -> str:
    return _WORKLOAD_CLASSES.get(purpose, "interactive")


def request_class(request: BrowserLeaseRequest) -> str:
    return request.priority_class or workload_class(request.purpose)


@dataclass
class _Ticket:
    request: BrowserLeaseRequest
    future: asyncio.Future


class FairBrowserWaitQueue:
    """Grant one reservation at a time; never hold a turn through external I/O."""
    _cycle = ("interactive", "interactive", "interactive", "batch")

    def __init__(self, available: Callable[[BrowserLeaseRequest], bool]):
        self.available = available
        self._tickets: list[_Ticket] = []
        self._granted: str | None = None
        self._cursor = 0
        self._last_project: dict[str, str] = {}
        self._closed = False

    async def wait_turn(self, request: BrowserLeaseRequest) -> None:
        if self._closed:
            raise RuntimeError("浏览器队列正在关闭")
        ticket = _Ticket(request, asyncio.get_running_loop().create_future())
        self._tickets.append(ticket)
        self.wake()
        try:
            await ticket.future
        except BaseException:
            if ticket in self._tickets:
                self._tickets.remove(ticket)
            self.reserved(request.lease_id)
            raise

    def reserved(self, lease_id: str) -> None:
        if self._granted == lease_id:
            self._granted = None
        self.wake()

    def wake(self) -> None:
        if self._closed or self._granted:
            return
        eligible = [t for t in self._tickets if not t.future.done() and self.available(t.request)]
        for offset in range(len(self._cycle)):
            cursor = (self._cursor + offset) % len(self._cycle)
            priority = self._cycle[cursor]
            group = [ticket for ticket in eligible if request_class(ticket.request) == priority]
            if not group:
                continue
            projects = sorted({ticket.request.project_id or "unscoped" for ticket in group})
            previous = self._last_project.get(priority, "")
            project = next((name for name in projects if name > previous), projects[0])
            selected = next(ticket for ticket in group if (ticket.request.project_id or "unscoped") == project)
            self._tickets.remove(selected)
            self._last_project[priority] = project
            self._cursor = (cursor + 1) % len(self._cycle)
            self._granted = selected.request.lease_id
            selected.future.set_result(None)
            return

    def status(self) -> dict:
        waiting = [ticket for ticket in self._tickets if not ticket.future.done()]
        return {"waiting": len(waiting),
                "waiting_by_class": {priority: sum(request_class(t.request) == priority for t in waiting)
                                     for priority in {"interactive", "batch"}},
                "waiting_projects": len({t.request.project_id or "unscoped" for t in waiting})}

    def close(self) -> None:
        self._closed = True
        for ticket in self._tickets:
            if not ticket.future.done():
                ticket.future.set_exception(RuntimeError("浏览器队列正在关闭"))
        self._tickets.clear()

    def reopen(self) -> None:
        self._closed = False
        self.wake()
