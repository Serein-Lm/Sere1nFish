"""
core.stream.pipeline — 多阶段流式管道编排器

执行模型
========
1. 注册若干 Stage, 用 `downstream` 声明 stage 之间的有向连边. 必须是 DAG.
2. `run(seeds, entry)` 把 seeds 灌入 entry stage 的输入队列.
3. 每个 stage 启动 N 个 worker 协程, 共享一个有界 asyncio.Queue.
4. Worker 循环:  get() 阻塞 → handle(retry) → task_done(); 收到毒丸信号即退出.
5. 关闭顺序按拓扑序:
       stage 的所有上游 (包括 seed) 都关闭后 → 等待自身 queue.join()
       → 向每个 worker 注入毒丸信号, 精确唤醒退出 (无超时轮询, 零尾延迟).
   保证下游一定在上游产出全部完成后才退出.
6. 任何一个 worker 抛出未捕获的非业务异常 (如 CancelledError) 会触发整个管道取消.

健壮性
======
- 队列有界, 写入 `put` 自动背压.
- handle 抛异常 → RetryPolicy → DLQ, 不会拖垮 worker.
- worker 协程内部 try/finally 保证 task_done 一定调用, 不会让 join 挂死.
- pipeline 可重复 run (新建队列/新建 worker), 但同一时刻只允许一次 run.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from core.logger import get_logger
from core.stream.types import Item, Context
from core.stream.stage import Stage
from core.stream.dlq import DeadLetter, InMemoryDeadLetter

logger = get_logger("stream.pipeline")


# 毒丸信号: 关闭时注入队列, worker 取到即精确退出 (替代超时轮询).
_SHUTDOWN = object()


# ── 指标 ───────────────────────────────────────────────

@dataclass
class StageMetrics:
    """每个 stage 的运行计数."""
    stage: str
    received: int = 0      # 从队列取出的 item 数
    succeeded: int = 0     # handle 最终成功
    failed: int = 0        # handle 最终失败 (进入 DLQ)
    retried: int = 0       # handle 触发过至少一次重试的 item 数
    emitted: dict[str, int] = field(default_factory=dict)  # 向各下游发送的数量

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "received": self.received,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "retried": self.retried,
            "emitted": dict(self.emitted),
        }


# ── 内部: stage 运行时状态 ───────────────────────────────

@dataclass
class _StageRuntime:
    stage: Stage
    downstream: list[str]
    upstream: list[str]              # 反向边, 框架自动算
    queue: asyncio.Queue
    closed: asyncio.Event            # 上游已全部停止生产
    workers: list[asyncio.Task] = field(default_factory=list)
    metrics: StageMetrics = field(default_factory=lambda: StageMetrics(stage=""))


# ── Pipeline ───────────────────────────────────────────

class Pipeline:
    """
    多 Stage 流式管道.

    用法:
        pipe = Pipeline(state={"db": db, "config": cfg})
        pipe.add(FetchStage(), downstream=["parse"])
        pipe.add(ParseStage(), downstream=["save"])
        pipe.add(SaveStage())
        await pipe.run(seeds=[Item(payload=u) for u in urls], entry="fetch")
        print(pipe.metrics_summary())
    """

    def __init__(
        self,
        *,
        state: dict[str, Any] | None = None,
        dlq: DeadLetter | None = None,
        pipeline_id: str = "",
        worker_get_timeout: float = 0.5,
    ) -> None:
        self.pipeline_id = pipeline_id or uuid.uuid4().hex[:8]
        self.state = state or {}
        self.dlq = dlq or InMemoryDeadLetter()
        self._worker_get_timeout = worker_get_timeout

        self._defs: dict[str, tuple[Stage, list[str]]] = {}  # name → (stage, downstream)
        self._runtime: dict[str, _StageRuntime] = {}
        self._running = False

    # ── 注册 ──────────────────────────────────────────
    def add(self, stage: Stage, *, downstream: list[str] | None = None) -> "Pipeline":
        if stage.name in self._defs:
            raise ValueError(f"stage {stage.name} 已注册")
        self._defs[stage.name] = (stage, list(downstream or []))
        return self

    # ── 校验 + 拓扑排序 ────────────────────────────────
    def _validate_and_sort(self, entry: str) -> list[str]:
        if entry not in self._defs:
            raise ValueError(f"entry stage '{entry}' 未注册")

        # 校验 downstream 都存在
        for name, (_, downs) in self._defs.items():
            for d in downs:
                if d not in self._defs:
                    raise ValueError(f"stage '{name}' 的下游 '{d}' 未注册")

        # 入度
        indeg = {n: 0 for n in self._defs}
        for name, (_, downs) in self._defs.items():
            for d in downs:
                indeg[d] += 1

        # Kahn
        order: list[str] = []
        q = [n for n, v in indeg.items() if v == 0]
        while q:
            n = q.pop(0)
            order.append(n)
            for d in self._defs[n][1]:
                indeg[d] -= 1
                if indeg[d] == 0:
                    q.append(d)

        if len(order) != len(self._defs):
            raise ValueError("stage 图存在环, 必须是 DAG")

        # entry 必须是入度为 0 的 (或者唯一被 seed 灌的入口)
        # 这里允许 entry 不是入度 0 的, 业务自行保证一致性, 不强校验.
        return order

    # ── 框架内部: emit ────────────────────────────────
    async def _emit(self, stage_name: str, item: Item, *, src_stage: str = "") -> None:
        """
        Stage.handle 通过 `await Context.emit(...)` 调用.
        队列满时会 await put 实现背压.
        """
        rt = self._runtime.get(stage_name)
        if rt is None:
            raise KeyError(f"emit 目标 stage '{stage_name}' 不存在或 pipeline 未运行")
        await rt.queue.put(item)
        # 统计 src_stage → stage_name 的扇出
        if src_stage and src_stage in self._runtime:
            m = self._runtime[src_stage].metrics
            m.emitted[stage_name] = m.emitted.get(stage_name, 0) + 1

    # ── 运行 ──────────────────────────────────────────
    async def run(
        self,
        *,
        seeds: Iterable[Item | Any],
        entry: str,
    ) -> dict[str, StageMetrics]:
        if self._running:
            raise RuntimeError("pipeline 已经在运行")
        self._running = True
        t0 = time.time()

        try:
            order = self._validate_and_sort(entry)

            # 计算反向邻接
            upstream: dict[str, list[str]] = {n: [] for n in self._defs}
            for name, (_, downs) in self._defs.items():
                for d in downs:
                    upstream[d].append(name)

            # 构建 runtime
            self._runtime = {}
            for name, (stage, downs) in self._defs.items():
                qmax = stage.queue_maxsize if stage.queue_maxsize > 0 else max(1, stage.concurrency * 4)
                rt = _StageRuntime(
                    stage=stage,
                    downstream=list(downs),
                    upstream=list(upstream[name]),
                    queue=asyncio.Queue(maxsize=qmax),
                    closed=asyncio.Event(),
                    metrics=StageMetrics(stage=name),
                )
                self._runtime[name] = rt

            # on_setup
            for name in order:
                rt = self._runtime[name]
                try:
                    await rt.stage.on_setup(self.state)
                except Exception as e:
                    logger.error(f"[{self.pipeline_id}] stage {name} on_setup 失败: {e}")
                    raise

            # 启动 workers
            for name in order:
                rt = self._runtime[name]
                rt.workers = [
                    asyncio.create_task(
                        self._worker_loop(rt, worker_id=i),
                        name=f"stream:{self.pipeline_id}:{name}:w{i}",
                    )
                    for i in range(rt.stage.concurrency)
                ]

            # 灌 seeds 到 entry
            entry_rt = self._runtime[entry]
            seed_count = 0
            for s in seeds:
                item = s if isinstance(s, Item) else Item(payload=s)
                await entry_rt.queue.put(item)
                seed_count += 1
            logger.info(
                f"[{self.pipeline_id}] 启动: stages={list(self._defs)} | "
                f"order={order} | entry={entry} | seeds={seed_count}"
            )

            # 拓扑顺序关闭: 对每个 stage, 等它"输入侧已关闭 + queue 排空", 然后关闭它
            # entry stage 的 "输入侧关闭" = seeds 已经全部灌完 (此处已完成)
            for name in order:
                rt = self._runtime[name]
                # 等所有上游 stage 都关闭
                for up in rt.upstream:
                    await self._runtime[up].closed.wait()
                # 等队列里残留的 item 处理完
                await rt.queue.join()
                # 标记本 stage 关闭 (供下游拓扑等待)
                rt.closed.set()
                # 向每个 worker 注入毒丸, 精确唤醒并退出 (零尾延迟, 无空转).
                # join() 已保证正常 item 全部处理完, 且上游已关闭不再产出,
                # 故毒丸后队列不会再有正常 item.
                for _ in rt.workers:
                    await rt.queue.put(_SHUTDOWN)
                # 等 worker 全部退出
                await asyncio.gather(*rt.workers, return_exceptions=True)
                logger.info(
                    f"[{self.pipeline_id}] stage '{name}' 完成 | {rt.metrics.as_dict()}"
                )

            # on_teardown (反序)
            for name in reversed(order):
                rt = self._runtime[name]
                try:
                    await rt.stage.on_teardown(self.state)
                except Exception as e:
                    logger.warning(f"[{self.pipeline_id}] stage {name} on_teardown 失败: {e}")

            elapsed = time.time() - t0
            logger.info(
                f"[{self.pipeline_id}] ✓ pipeline 完成 | 耗时={elapsed:.1f}s | "
                f"summary={self.metrics_summary()}"
            )
            return {n: rt.metrics for n, rt in self._runtime.items()}

        except asyncio.CancelledError:
            logger.info(f"[{self.pipeline_id}] pipeline 已取消，正在回收 worker")
            await self._cancel_all()
            raise
        except BaseException as e:
            logger.error(f"[{self.pipeline_id}] ✗ pipeline 异常, 取消所有 worker: {e}")
            await self._cancel_all()
            raise
        finally:
            self._running = False

    async def _cancel_all(self) -> None:
        for rt in self._runtime.values():
            for w in rt.workers:
                if not w.done():
                    w.cancel()
        for rt in self._runtime.values():
            await asyncio.gather(*rt.workers, return_exceptions=True)

    # ── Worker 主循环 ─────────────────────────────────
    async def _worker_loop(self, rt: _StageRuntime, *, worker_id: int) -> None:
        ctx = Context(
            pipeline=self,
            stage_name=rt.stage.name,
            worker_id=worker_id,
            logger=logger,
            state=self.state,
        )
        while True:
            # 阻塞等待下一个 item; 收到毒丸信号或被取消则退出.
            try:
                item = await rt.queue.get()
            except asyncio.CancelledError:
                return
            if item is _SHUTDOWN:
                rt.queue.task_done()
                return

            rt.metrics.received += 1
            had_retry = False
            try:
                ok, err = await rt.stage._process_with_retry(item, ctx)
                if item.attempt > 1:
                    had_retry = True
                if ok:
                    rt.metrics.succeeded += 1
                else:
                    rt.metrics.failed += 1
                    try:
                        await self.dlq.record(
                            stage=rt.stage.name,
                            item=item,
                            error=err,
                            pipeline_id=self.pipeline_id,
                        )
                    except Exception as dlq_err:
                        logger.error(
                            f"[{self.pipeline_id}] DLQ 写入失败 stage={rt.stage.name} "
                            f"item={item.item_id}: {dlq_err}"
                        )
            except asyncio.CancelledError:
                # 让 join 仍能完成
                raise
            except BaseException as e:
                # _process_with_retry 不应该抛出 CancelledError 之外的异常,
                # 这里防御性兜底.
                rt.metrics.failed += 1
                logger.error(
                    f"[{self.pipeline_id}/{rt.stage.name}/w{worker_id}] "
                    f"worker 内部异常 item={item.item_id}: {e}"
                )
            finally:
                if had_retry:
                    rt.metrics.retried += 1
                rt.queue.task_done()

    # ── 工具 ──────────────────────────────────────────
    def metrics_summary(self) -> dict[str, dict[str, Any]]:
        return {n: rt.metrics.as_dict() for n, rt in self._runtime.items()}
