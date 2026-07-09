"""
core.stream.stage — Stage 抽象基类与重试策略
"""
from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from core.stream.types import Item, Context


@dataclass
class RetryPolicy:
    """
    Stage 级重试策略.

    - max_attempts: 总尝试次数 (1 表示不重试).
    - base_delay / max_delay: 指数退避 (秒).
    - jitter: 加入随机抖动避免雪崩.
    - retry_on: 谓词, 决定某个异常是否值得重试. 默认所有异常都重试.
                例: lambda e: not isinstance(e, ValueError)  # 校验类异常不重试
    """
    max_attempts: int = 1
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: bool = True
    retry_on: Callable[[BaseException], bool] = field(default=lambda e: True)

    def delay_for(self, attempt: int) -> float:
        """attempt 从 1 起计."""
        d = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        if self.jitter:
            d *= 0.5 + random.random()
        return d


class Stage(ABC):
    """
    业务阶段抽象基类.

    子类需要:
    1. 类属性 `name` (字符串, pipeline 内唯一).
    2. 实现 `async handle(item, ctx)`.

    可选覆盖:
    - concurrency:   该 stage 启动的 worker 协程数 (默认 1).
    - queue_maxsize: 该 stage 输入队列的最大长度 (默认 concurrency*4, 用于背压).
                     0 表示无界 (不推荐).
    - retry:         RetryPolicy 实例 (默认不重试).
    - on_setup / on_teardown: 整个 stage 启动/结束时各调用一次 (worker 之外).

    `handle` 内部:
    - 通过 ctx.emit("downstream_name", payload) 把结果送往下游.
    - 抛出异常会触发 RetryPolicy; 超过上限会进入 DLQ.
    - 不要直接调 queue API; 也不要捕获后吞掉异常 (除非业务上确实要丢弃).
    """

    name: str = ""
    concurrency: int = 1
    queue_maxsize: int = 0  # 0 → pipeline 自动算 concurrency*4
    retry: RetryPolicy = RetryPolicy()  # 默认不重试

    def __init__(self, **overrides: Any) -> None:
        # 允许实例化时覆盖类属性, 方便配置驱动.
        for k, v in overrides.items():
            if not hasattr(self.__class__, k):
                raise AttributeError(f"Stage {self.__class__.__name__} 没有属性 {k}")
            setattr(self, k, v)
        if not self.name:
            raise ValueError(f"Stage {self.__class__.__name__} 必须设置 name")

    # ── 钩子, 默认空实现 ────────────────────────────────
    async def on_setup(self, state: dict[str, Any]) -> None:
        """整个 stage 启动前调用一次 (在所有 worker 启动前). 用于打开共享资源."""
        return None

    async def on_teardown(self, state: dict[str, Any]) -> None:
        """所有 worker 结束后调用一次. 用于关闭共享资源."""
        return None

    # ── 业务必须实现 ───────────────────────────────────
    @abstractmethod
    async def handle(self, item: Item, ctx: Context) -> None:
        """处理单个 item. 通过 ctx.emit() 送往下游."""
        ...

    # ── 框架内部使用: 单 item 的重试包装 ──────────────────
    async def _process_with_retry(self, item: Item, ctx: Context) -> tuple[bool, BaseException | None]:
        """
        返回 (是否最终成功, 最后一次异常).
        """
        last_err: BaseException | None = None
        policy = self.retry
        for attempt in range(1, max(1, policy.max_attempts) + 1):
            item.attempt = attempt
            try:
                await self.handle(item, ctx)
                return True, None
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                last_err = e
                will_retry = (
                    attempt < policy.max_attempts and policy.retry_on(e)
                )
                ctx.logger.warning(
                    f"[{self.name}/w{ctx.worker_id}] handle 失败 "
                    f"item={item.item_id} attempt={attempt}/{policy.max_attempts} "
                    f"err={type(e).__name__}: {e} | retry={will_retry}"
                )
                if not will_retry:
                    return False, e
                await asyncio.sleep(policy.delay_for(attempt))
        return False, last_err
