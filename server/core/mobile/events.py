"""
进程内事件总线 — 让画像/建议/自动聊天的变化实时推送给前端(SSE/WS)。

设计:
- publish(event):广播一个事件 dict。约定字段 type(必填),device_id/contact_id(可选,用于过滤),data。
- subscribe(...):异步迭代事件,可按 device_id/contact_id/types 过滤;全局事件(无 device_id)对所有订阅者可见。
- recent(...):取最近 N 条历史。late subscriber 可先补历史再实时跟,实现「随时查看」。

注意:单进程内存版。多 worker 部署需替换为 Redis pub/sub —— 这里集中封装,迁移只改本文件。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator
from typing import Any


def _match(
    ev: dict[str, Any],
    device_id: str | None,
    contact_id: str | None,
    project_id: str | None,
    types: set[str] | None,
) -> bool:
    if types and ev.get("type") not in types:
        return False
    # 事件无 device_id 视为全局,对任何订阅者可见;有则需匹配
    if device_id and ev.get("device_id") not in (None, device_id):
        return False
    if contact_id and ev.get("contact_id") not in (None, contact_id):
        return False
    if project_id and ev.get("project_id") not in (None, project_id):
        return False
    return True


class EventBus:
    """进程内发布/订阅(单例)。"""

    _instance: "EventBus | None" = None

    def __init__(self, history: int = 300) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._history: deque[dict[str, Any]] = deque(maxlen=history)

    @classmethod
    def get_instance(cls) -> "EventBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def publish(self, event: dict[str, Any]) -> None:
        if "ts" not in event:
            event = {**event, "ts": time.time()}
        self._history.append(event)
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # 慢消费者丢弃最旧一条再放,避免阻塞发布方
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass

    def recent(
        self,
        *,
        device_id: str | None = None,
        contact_id: str | None = None,
        project_id: str | None = None,
        types: set[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        out = [
            ev
            for ev in list(self._history)
            if _match(ev, device_id, contact_id, project_id, types)
        ]
        return out[-limit:]

    async def subscribe(
        self,
        *,
        device_id: str | None = None,
        contact_id: str | None = None,
        project_id: str | None = None,
        types: set[str] | None = None,
        heartbeat: float | None = 15.0,
    ) -> AsyncIterator[dict[str, Any] | None]:
        """订阅事件流。

        heartbeat>0 时，空闲超过该秒数会 yield 一个 None 心跳标记，调用方据此
        发送 SSE keepalive。心跳同时让服务端能及时感知客户端断开（向已关闭连接
        写入会抛错 → 退出循环 → finally 清理队列），避免空闲断连订阅者永久泄漏。
        """
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        try:
            while True:
                if heartbeat and heartbeat > 0:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=heartbeat)
                    except asyncio.TimeoutError:
                        yield None
                        continue
                else:
                    ev = await q.get()
                if _match(ev, device_id, contact_id, project_id, types):
                    yield ev
        finally:
            self._subscribers.discard(q)


def publish(event: dict[str, Any]) -> None:
    """便捷发布。"""
    EventBus.get_instance().publish(event)
