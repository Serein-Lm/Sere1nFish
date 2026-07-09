# core.stream — 统一流式消费框架

> 目标：把项目里所有"队列 + worker + Event 完成检测"的手写模板，收敛到一个稳定、易读、易扩展的抽象。

## 为什么有这个模块

历史代码（`api/services/url_scan_pipeline.py`、`company_scan_pipeline.py`、`xhs_pipeline.py`）里同一段范式被复制了至少三遍：

```python
queue = asyncio.Queue()
done = asyncio.Event()
while True:
    if queue.empty() and done.is_set(): break
    try:
        item = await asyncio.wait_for(queue.get(), timeout=2)
    except asyncio.TimeoutError:
        if done.is_set() and queue.empty(): break
        continue
    ...
```

存在的问题：

- 完成检测是"empty + Event"双条件，多生产者拓扑下容易漏 item。
- 队列无界，没有背压。
- 每个 worker 都用闭包写死在 pipeline 主体里，新增/切换消费类型必须改 pipeline。
- 重试/失败处理散在各处，没有统一的 DLQ。
- 并发数硬编码、计数器靠 `nonlocal`，难以观测和测试。

`core.stream` 提供统一抽象，下面是所有它已经替你处理掉的事：

| 关注点 | 框架做了什么 |
|---|---|
| 退出协议 | 拓扑序 `queue.join()` + `closed.set()`，**无竞态** |
| 背压 | 每 stage 有界队列 (`maxsize = concurrency*4`)，`emit` 队列满会等 |
| 重试 | `RetryPolicy`：次数 + 指数退避 + 抖动 + 异常谓词 |
| DLQ | 超限失败自动入死信 (`InMemoryDeadLetter` / `MongoDeadLetter`) |
| 取消 | 任意 worker 异常 → 级联取消所有 worker |
| 观测 | 每 stage 自带 `StageMetrics` (received / ok / failed / retried / emitted) |
| 资源 | `on_setup` / `on_teardown` 钩子管理共享资源 |

## 快速上手

```python
from core.stream import Pipeline, Stage, RetryPolicy

class FetchStage(Stage):
    name = "fetch"
    concurrency = 5
    retry = RetryPolicy(max_attempts=3, base_delay=1.0)

    async def handle(self, item, ctx):
        html = await fetch(item.payload)
        await ctx.emit("parse", {"url": item.payload, "html": html})

class ParseStage(Stage):
    name = "parse"
    concurrency = 3

    async def handle(self, item, ctx):
        for finding in extract(item.payload["html"]):
            await ctx.emit("save", finding)

class SaveStage(Stage):
    name = "save"
    concurrency = 1

    async def on_setup(self, state):
        state["coll"] = state["db"]["findings"]

    async def handle(self, item, ctx):
        await ctx.state["coll"].insert_one(item.payload)


pipe = Pipeline(state={"db": db})
pipe.add(FetchStage(), downstream=["parse"])
pipe.add(ParseStage(), downstream=["save"])
pipe.add(SaveStage())

metrics = await pipe.run(seeds=urls, entry="fetch")
print(pipe.metrics_summary())
```

## 接口契约（写新 Stage 唯一要看的）

```python
class Stage(ABC):
    name: str                  # 唯一标识 (必填)
    concurrency: int = 1       # worker 协程数
    queue_maxsize: int = 0     # 0 → concurrency * 4
    retry: RetryPolicy = ...   # 默认不重试

    async def on_setup(self, state) -> None: ...      # 可选
    async def on_teardown(self, state) -> None: ...   # 可选

    async def handle(self, item: Item, ctx: Context) -> None:
        # 业务在这里. 失败抛异常, 框架会按 retry 重试, 超限进 DLQ.
        # 通过 await ctx.emit("downstream_name", payload) 送往下游.
        ...
```

`ctx` 提供：

- `await ctx.emit(stage_name, payload, meta=...)` — 唯一推送下游的入口
- `ctx.state` — 整个 pipeline 共享 dict（推荐放 `db`、`config`、累计结果）
- `ctx.logger` — 统一 logger
- `ctx.worker_id`, `ctx.stage_name` — 日志用

## 重试策略

```python
RetryPolicy(
    max_attempts=3,
    base_delay=1.0,
    max_delay=30.0,
    jitter=True,
    retry_on=lambda e: not isinstance(e, ValueError),  # 校验类异常不重试
)
```

## DLQ

```python
from core.stream import MongoDeadLetter

dlq = MongoDeadLetter(db, collection="stream_dlq")
pipe = Pipeline(state={"db": db}, dlq=dlq)
```

死信文档结构：
```
{ pipeline_id, stage, item_id, payload, meta, attempt, error, ts }
```

复跑直接 `find({stage: "..."}).map(... → enqueue)`。

## 测试

```
pytest test_server/tests/test_stream_pipeline.py -v
```

覆盖：基础三阶段直链、重试 + DLQ、扇出、背压。

## 迁移现有 pipeline 的建议路径

针对当前的 `url_scan_pipeline` / `xhs_pipeline` / `company_scan_pipeline`：

1. **先小后大**：先迁移 `url_scan_pipeline.run_pipeline` 里"扫描 → 提取 → 话术"那段，因为它最像教科书例子。
2. **每个闭包 worker 抽一个 Stage 子类**，文件放在 `api/services/<domain>/stages/`：
   - `url_scan_pipeline._worker` → `UrlScanStage`
   - `_copywriting_worker` → `CopywritingStage`
   - `_tagging_worker` → `NoteTaggingStage`
   - `_detail_worker` → `NoteDetailStage`
   - `_prof_w` / `_cw_w` → `ProfileStage` / `XhsCopywritingStage`
3. **共享资源（V2 client、agent）放进 `state`**，在 `on_setup` 里初始化、`on_teardown` 关闭。
4. **并发数迁出常量、读 config**：`stage.concurrency = config.get("xhs_tagging_concurrency", 7)`。
5. **重试/DLQ 替代手写 try/except**：把当前的 `update_note_tagging(reason="打标失败")` 改成抛异常 + DLQ + 后台复跑。
6. 旧 pipeline 入口函数保留同名签名，内部组装 Stage 后 `await Pipeline.run(...)`，**对外 API 不变**。

迁移一个 pipeline 大约 1~2 小时；建议 PR 拆三个，每个独立可回滚。

## 设计取舍记录

- **没有用进程池**：当前业务瓶颈在 LLM/IO，不在 CPU；浏览器隔离已经在 `DockerProvider` 里以容器形式完成。Python 层用 asyncio 协程足够，调度开销最低、共享 state 最方便。
- **没有引入 Redis/NATS**：本框架是"进程内流水线"。需要跨进程/持久化时，用 `MongoDeadLetter` + 手工复跑，或在外面再包一层 Job Runner。
- **emit 是 async**：保证背压语义清晰，handle 写法跟用 `asyncio.Queue` 一样。
- **拓扑序关闭** vs Event 信号：拓扑序无竞态，且语义跟 DAG 一致。
