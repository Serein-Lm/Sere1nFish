"""
core.stream.dlq — 死信队列 (Dead Letter Queue) 接口与默认实现
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from core.stream.types import Item


class DeadLetter(ABC):
    """
    DLQ 接口. 当一个 item 超过最大重试次数仍失败, 框架会调用 `record()` 归档.
    """

    @abstractmethod
    async def record(
        self,
        *,
        stage: str,
        item: Item,
        error: BaseException | None,
        pipeline_id: str = "",
    ) -> None:
        ...


class InMemoryDeadLetter(DeadLetter):
    """默认内存实现, 用于测试和小规模任务. 不持久化."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def record(
        self,
        *,
        stage: str,
        item: Item,
        error: BaseException | None,
        pipeline_id: str = "",
    ) -> None:
        self.entries.append({
            "pipeline_id": pipeline_id,
            "stage": stage,
            "item_id": item.item_id,
            "payload": item.payload,
            "meta": dict(item.meta),
            "attempt": item.attempt,
            "error": f"{type(error).__name__}: {error}" if error else "",
            "ts": time.time(),
        })


class MongoDeadLetter(DeadLetter):
    """
    Mongo 持久化实现. 把死信写入指定集合, 便于事后人工复跑.

    用法:
        dlq = MongoDeadLetter(db, collection="stream_dlq")
        pipe = Pipeline(dlq=dlq)
    """

    def __init__(self, db: Any, collection: str = "stream_dlq") -> None:
        self.db = db
        self.collection = collection

    async def record(
        self,
        *,
        stage: str,
        item: Item,
        error: BaseException | None,
        pipeline_id: str = "",
    ) -> None:
        # payload 可能是不可序列化对象, 退化为 repr.
        try:
            import bson  # noqa: F401
            payload = item.payload
            # 简单探测: 不是基本类型就转 repr
            if not isinstance(payload, (str, int, float, bool, list, dict, type(None))):
                payload = repr(payload)
        except Exception:
            payload = repr(item.payload)

        doc = {
            "pipeline_id": pipeline_id,
            "stage": stage,
            "item_id": item.item_id,
            "payload": payload,
            "meta": dict(item.meta),
            "attempt": item.attempt,
            "error": f"{type(error).__name__}: {error}" if error else "",
            "ts": time.time(),
        }
        try:
            await self.db[self.collection].insert_one(doc)
        except Exception:
            # DLQ 失败不能再抛, 只能吞 (上层日志已经打过了)
            pass
