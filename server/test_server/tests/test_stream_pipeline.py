"""
core.stream 框架的最小验证测试.

跑法:
    pytest test_server/tests/test_stream_pipeline.py -v
"""
import asyncio
import pytest


def _run(coro):
    return asyncio.run(coro)


def test_basic_three_stage_chain():
    _run(_test_basic_three_stage_chain())


def test_retry_and_dlq():
    _run(_test_retry_and_dlq())


def test_fanout():
    _run(_test_fanout())


def test_backpressure():
    _run(_test_backpressure())


from core.stream import (
    Pipeline,
    Stage,
    Item,
    RetryPolicy,
    InMemoryDeadLetter,
)


# ── 1. 三阶段直链: fetch → parse → save ──────────────────

class _Fetch(Stage):
    name = "fetch"
    concurrency = 3

    async def handle(self, item, ctx):
        await asyncio.sleep(0.01)
        await ctx.emit("parse", {"raw": item.payload, "len": len(item.payload)})


class _Parse(Stage):
    name = "parse"
    concurrency = 2

    async def handle(self, item, ctx):
        item.payload["parsed"] = True
        await ctx.emit("save", item.payload)


class _Save(Stage):
    name = "save"
    concurrency = 1

    async def handle(self, item, ctx):
        ctx.state.setdefault("saved", []).append(item.payload)


async def _test_basic_three_stage_chain():
    pipe = Pipeline()
    pipe.add(_Fetch(), downstream=["parse"])
    pipe.add(_Parse(), downstream=["save"])
    pipe.add(_Save())

    seeds = [f"url-{i}" for i in range(20)]
    metrics = await pipe.run(seeds=seeds, entry="fetch")

    saved = pipe.state["saved"]
    assert len(saved) == 20
    assert all(d["parsed"] for d in saved)

    assert metrics["fetch"].received == 20
    assert metrics["fetch"].succeeded == 20
    assert metrics["fetch"].emitted == {"parse": 20}
    assert metrics["parse"].received == 20
    assert metrics["save"].received == 20
    assert metrics["save"].failed == 0


# ── 2. 重试 + DLQ ────────────────────────────────────────

class _Flaky(Stage):
    name = "flaky"
    concurrency = 2
    retry = RetryPolicy(max_attempts=3, base_delay=0.01, jitter=False)

    async def handle(self, item, ctx):
        # 偶数 payload 第一次失败, 第二次成功; 奇数永远失败
        attempts = ctx.state.setdefault("attempts", {})
        attempts[item.payload] = attempts.get(item.payload, 0) + 1
        if item.payload % 2 == 1:
            raise RuntimeError(f"odd-{item.payload}-attempt-{item.attempt}")
        if item.attempt == 1:
            raise RuntimeError(f"first-fail-{item.payload}")


async def _test_retry_and_dlq():
    dlq = InMemoryDeadLetter()
    pipe = Pipeline(dlq=dlq)
    pipe.add(_Flaky())

    await pipe.run(seeds=list(range(6)), entry="flaky")

    m = pipe.metrics_summary()["flaky"]
    # 6 个: 偶数(0,2,4)各试 2 次成功; 奇数(1,3,5)各试 3 次失败
    assert m["received"] == 6
    assert m["succeeded"] == 3
    assert m["failed"] == 3
    assert m["retried"] == 6   # 全部至少重试过一次

    # DLQ 应有 3 条 (奇数)
    assert len(dlq.entries) == 3
    failed_payloads = sorted(e["payload"] for e in dlq.entries)
    assert failed_payloads == [1, 3, 5]

    # attempts 计数验证
    a = pipe.state["attempts"]
    for p in [0, 2, 4]:
        assert a[p] == 2
    for p in [1, 3, 5]:
        assert a[p] == 3


# ── 3. 扇出 (一个 stage 输出到多个下游) ────────────────────

class _Splitter(Stage):
    name = "split"
    concurrency = 1

    async def handle(self, item, ctx):
        # 每个数字同时送往 even/odd 两个下游中的一个
        if item.payload % 2 == 0:
            await ctx.emit("even", item.payload)
        else:
            await ctx.emit("odd", item.payload)


class _Sink(Stage):
    name = ""  # 占位, 子类设置
    concurrency = 1
    bucket_key = ""

    async def handle(self, item, ctx):
        ctx.state.setdefault(self.bucket_key, []).append(item.payload)


class _EvenSink(_Sink):
    name = "even"
    bucket_key = "evens"


class _OddSink(_Sink):
    name = "odd"
    bucket_key = "odds"


async def _test_fanout():
    pipe = Pipeline()
    pipe.add(_Splitter(), downstream=["even", "odd"])
    pipe.add(_EvenSink())
    pipe.add(_OddSink())

    await pipe.run(seeds=list(range(10)), entry="split")

    assert sorted(pipe.state["evens"]) == [0, 2, 4, 6, 8]
    assert sorted(pipe.state["odds"]) == [1, 3, 5, 7, 9]
    assert pipe.metrics_summary()["split"]["emitted"] == {"even": 5, "odd": 5}


# ── 4. 背压: 慢消费者下队列不会爆 ────────────────────────

class _FastProducer(Stage):
    name = "prod"
    concurrency = 5

    async def handle(self, item, ctx):
        await ctx.emit("slow", item.payload)


class _SlowConsumer(Stage):
    name = "slow"
    concurrency = 1
    queue_maxsize = 4   # 故意压小

    async def handle(self, item, ctx):
        await asyncio.sleep(0.01)
        ctx.state.setdefault("done", []).append(item.payload)
        # 探测队列大小, 不应超过 maxsize
        q = ctx.pipeline._runtime["slow"].queue
        ctx.state["max_qsize"] = max(ctx.state.get("max_qsize", 0), q.qsize())


async def _test_backpressure():
    pipe = Pipeline()
    pipe.add(_FastProducer(), downstream=["slow"])
    pipe.add(_SlowConsumer())

    await pipe.run(seeds=list(range(50)), entry="prod")

    assert len(pipe.state["done"]) == 50
    assert pipe.state["max_qsize"] <= 4
