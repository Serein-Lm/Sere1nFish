"""
core.stream.types — 流式管道的基础数据类型
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.stream.pipeline import Pipeline
    import logging


@dataclass
class Item:
    """
    管道中流转的单元.

    payload 是业务数据 (任意类型, 由 Stage 自行约定).
    meta 用于附带跟踪/调试信息, 不影响业务逻辑.
    attempt 由框架在重试时自增, 业务无需手动维护.
    """
    payload: Any
    meta: dict[str, Any] = field(default_factory=dict)
    attempt: int = 0
    item_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)

    def with_payload(self, new_payload: Any) -> "Item":
        """生成一个继承 meta/item_id 但替换 payload 的新 Item (扇出常用)."""
        return Item(
            payload=new_payload,
            meta=dict(self.meta),
            attempt=0,
            item_id=uuid.uuid4().hex[:12],
        )


@dataclass
class Context:
    """
    Stage.handle 执行时的运行时上下文.

    - emit(stage_name, payload_or_item): 将结果发送到指定下游 stage 队列.
    - logger: 框架统一日志器.
    - state: 用户共享状态字典 (整个 pipeline 共享, 如 db / config).
    - pipeline: 反向引用, 高级场景使用.
    """
    pipeline: "Pipeline"
    stage_name: str
    worker_id: int
    logger: "logging.Logger"
    state: dict[str, Any]

    async def emit(self, stage: str, payload: Any, *, meta: dict[str, Any] | None = None) -> None:
        """
        把结果送往下游 stage. 业务最常用的接口.

        - 必须 await: 目标队列满时会自然背压 (await put).
        - 如果传入的已经是 Item, 会直接路由 (保留 meta, attempt 重置为 0).
        """
        if isinstance(payload, Item):
            item = payload
            if meta:
                item.meta.update(meta)
        else:
            item = Item(payload=payload, meta=dict(meta or {}))
        await self.pipeline._emit(stage, item, src_stage=self.stage_name)
