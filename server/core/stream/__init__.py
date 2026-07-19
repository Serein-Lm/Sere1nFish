"""
core.stream — 统一流式消费框架

设计目标:
1. 接口统一: 所有"扫描/打标/详情/画像/话术"等阶段都实现同一个 `Stage` 抽象。
2. 易读易查: 每个 Stage 单独一个类, 不再写在闭包里, 一眼能看到边界。
3. 健壮: 有界队列做背压、严格 task_done/join、独立重试、DLQ、级联取消。
4. 可扩展: 新增 consumer 类型只需写一个 Stage 子类并注册到 Pipeline。

最小示例:
    from core.stream import Pipeline, Stage, Item

    class FetchStage(Stage):
        name = "fetch"
        concurrency = 5
        async def handle(self, item, ctx):
            data = await fetch(item.payload)
            ctx.emit("parse", data)

    class ParseStage(Stage):
        name = "parse"
        concurrency = 3
        async def handle(self, item, ctx):
            await save(item.payload)

    pipe = Pipeline()
    pipe.add(FetchStage(), downstream=["parse"])
    pipe.add(ParseStage())
    await pipe.run(seeds=[Item(payload=url) for url in urls], entry="fetch")
"""

from core.stream.types import Item, Context
from core.stream.stage import Stage, RetryPolicy
from core.stream.pipeline import Pipeline, StageMetrics
from core.stream.dlq import DeadLetter, InMemoryDeadLetter, MongoDeadLetter
from core.stream.errors import PipelineAbortError

__all__ = [
    "Item",
    "Context",
    "Stage",
    "RetryPolicy",
    "Pipeline",
    "StageMetrics",
    "DeadLetter",
    "InMemoryDeadLetter",
    "MongoDeadLetter",
    "PipelineAbortError",
]
