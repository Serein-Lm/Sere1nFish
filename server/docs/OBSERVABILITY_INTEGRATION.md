# 观测层接入指南（新模块对接 · 沉淀文档）

> 面向**任何新模块/新子系统**的开发者：如何在 5 分钟内把你的模块纳入统一观测层（状态 / 用量 / 日志），并在统一看板可见、可查、可回溯。
>
> 配套：API 参考见 [`OBSERVABILITY_API.md`](./OBSERVABILITY_API.md)。本文聚焦「怎么接入」。

---

## 0. TL;DR（最短路径）

进程内任意位置：

```python
from core.observability import obs_log

obs_log(
    "开始抓取",                 # message：人类可读
    task_id=task_id,            # 关联任务（强烈建议）
    project_id=project_id,      # 关联项目（强烈建议）
    source="xhs_pipeline",      # 来源模块名（按模块筛选）
    level="info",               # debug/info/notice/warning/error
    event="fetch_start",        # 可选事件类型（结构化筛选）
    data={"keyword": kw},       # 任意附加结构化字段
)
```

就这一行。日志会写入当前进程内 ring buffer，立刻能在
`GET /api/v1/observability/overview` / `POST /api/v1/observability/logs/query` /
`GET /api/v1/observability/tasks/{task_id}/logs` 看到。**无需建表、无需关心并发与 event loop。**

---

## 1. 观测层是什么（接入前必读）

统一汇聚三类数据，前端一个看板总览 + 分层钻取 + 细粒度排查：

| 平面 | 你要做什么 | 机制 |
|---|---|---|
| **日志/事件 Logs** | 调 `obs_log(...)` | `core.observability` sink → 进程内 ring buffer |
| **用量 Token** | 给 LLM 注入 `tracker.callback` + `push_context` | LangChain Callback → TokenTracker ring buffer |
| **状态 Status** | 写自己的任务文档 `status` 字段 | `tasks` 集合（如用统一任务体系则自动具备） |

> 多数新模块只需接 **Logs**；若你的模块调用 LLM，再接 **Token**（见 §4）；若你引入新的「任务」概念，建议复用统一任务体系以自动获得 **Status**（见 §5）。

---

## 2. 接入方式 A：进程内 `obs_log`（首选）

### 2.1 函数签名

```python
obs_log(
    message: str,                 # 必填
    *,
    task_id: str = "",
    project_id: str = "",
    source: str = "",
    level: str = "info",          # debug/info/notice/warning/error
    event: str = "",
    data: dict | None = None,
    phase: str = "",
    agent: str = "",
    ts: float | None = None,      # 默认 time.time()
) -> str                          # 返回 log_id
```

特性：**同步、非阻塞、不抛错、不依赖 event loop**。内部线程安全写入进程内环形缓冲，达到 `OBS_LOG_MAX_RECORDS` 后自动淘汰最旧记录。

### 2.2 字段约定（务必遵守，保证可筛选/可聚合）

| 字段 | 约定 | 例 |
|---|---|---|
| `source` | **模块名**，小写下划线，全局唯一稳定 | `task_runner` / `mobile_agent` / `xhs_pipeline` / `douyin_pipeline` |
| `level` | 固定五档；`error` 用于失败、`warning` 用于可恢复异常、`notice` 用于关键里程碑 | `notice` |
| `event` | **动词短语**，同一模块内枚举稳定，便于结构化筛选 | `fetch_start` / `fetch_done` / `item_skipped` |
| `task_id` / `project_id` | 有则必传，串起任务级/项目级视图 | — |
| `phase` / `agent` | 流水线阶段 / 子 agent，可选，用于更细分层 | `scenario` / `web_tagging` |
| `data` | 任意 JSON 附加字段，放可量化指标（数量、耗时、参数） | `{"count": 20, "elapsed_ms": 1200}` |

> 反例：`source="x"`、`level="err"`（非五档之一会被归一为 info）、`event="开始了"`（非稳定枚举，难筛选）。

### 2.3 错误也要记

```python
try:
    ...
except Exception as e:
    obs_log(f"抓取失败: {e}", task_id=task_id, project_id=project_id,
            source="xhs_pipeline", level="error", event="fetch_error",
            data={"error": str(e)})
    raise
```

---

## 3. 接入方式 B：进程外 / 前端 / 其它语言

走 HTTP（需 JWT）：

```http
POST /api/v1/observability/logs
{ "message": "外部任务完成", "task_id": "...", "project_id": "...",
  "source": "external_worker", "level": "notice", "event": "job_done",
  "data": { "rows": 1000 } }
```

返回 `{ "ok": true, "log_id": "..." }`。字段语义同 §2.2。

---

## 4. 接入 Token 观测（仅当你的模块调用 LLM）

用本项目统一 LLM 工厂时通常已自动注入；如手写 LLM，请注入 callback 并管理层级：

```python
from Sere1nGraph.graph.observability import get_global_tracker

tracker = get_global_tracker()

# 1) 注入 callback（自动抓取每次调用的 token/费用/耗时）
llm = ChatOpenAI(..., callbacks=[tracker.callback])

# 2) 管理层级上下文（决定这次调用归属到哪个项目/任务/轮次/阶段/agent）
tracker.push_context(project_id=pid, task_id=tid, turn_id=turn_id)
tracker.push_context(phase="scenario", agent="my_agent")
try:
    await llm.ainvoke(...)
finally:
    tracker.pop_context()   # 退出 agent/phase
    tracker.pop_context()   # 退出 task
```

之后 `/observability/stats?...` / `/hierarchy` / `/turns` 自动出现你的数据。**无需手动写库**，Token 观测不写 MongoDB。

---

## 5. 接入 Status（仅当你引入新的「任务」）

最省事：复用统一任务体系（`POST /api/v1/projects/{id}/tasks`，`task_type` 注册到 `TASK_DISPATCHERS`）。
这样自动获得：`status`(pending/running/completed/error) + `elapsed_ms` + 失败 `error`，并被 `/observability/overview` 的任务分布统计纳入；执行器还会自动发 `task_start/done/error` 日志事件。

若你**自建**任务存储，请保证文档含 `status` / `project_id` / `task_id` / `updated_at` 字段，便于后续统一聚合。

---

## 6. 数据查看（接入后去哪看）

| 目的 | 端点 |
|---|---|
| 看板首屏总览 | `GET /api/v1/observability/overview` |
| 按条件查日志 | `POST /api/v1/observability/logs/query`（filter: source/level/min_level/event/task_id/project_id/since + 分页） |
| 某任务全部日志 | `GET /api/v1/observability/tasks/{task_id}/logs` |
| Token 多层级 | `GET /api/v1/observability/stats` / `GET /api/v1/observability/hierarchy` |
| Token 按轮次 | `GET /api/v1/observability/turns?project_id=&task_id=&limit=` |

---

## 7. 生命周期与清理（避免脏数据）

- **日志**：删任务/项目时自动清理当前进程内日志 ring buffer。旧版 `task_logs` 集合仅做兼容清理。
- **若你新增了自己的集合**：把它加入项目删除级联（`api/routers/projects.py` 的 `collections_to_clean`，按 `project_id` 删），并在 `api/main.py` lifespan 注册其索引 + （如需）`drain`/关闭钩子。
- **Token**：删任务/项目时调用 `tracker.evict_records(...)` 清理当前进程内 ring buffer。旧版 `token_usage_records` 集合仅做兼容清理。

---

## 8. 健壮性约定（长周期系统必守）

- 写观测**绝不能影响主流程**：`obs_log` 已保证不抛错；你自己拼 `data` 时避免放不可序列化对象（放基本类型/字符串/数字）。
- 高频循环里记日志请**降采样**或只记里程碑/异常，避免挤占内存 ring buffer。容量护栏：`OBS_LOG_MAX_RECORDS`、`TOKEN_TRACKER_MAX_RECORDS` 与 `OBS_LOG_MIN_LEVEL`。详见 API 文档「日志存储·与 logger 关系·容量控制」。
- 不要在 `obs_log` 里做重计算；要量化指标就直接把结果丢进 `data`。

---

## 9. 接入清单（Checklist）

- [ ] 选定稳定的 `source` 模块名
- [ ] 关键里程碑 / 异常都有 `obs_log`（start / done / error 三件套）
- [ ] 传了 `task_id` / `project_id`（如适用）
- [ ] `event` 用稳定枚举；`data` 放可量化指标
- [ ] 调 LLM 的模块：注入 `tracker.callback` + `push_context/pop_context`
- [ ] 新增集合：登记项目删除级联 + 索引
- [ ] 自测：触发一次后 `GET /observability/tasks/{id}/logs` 能看到

---

## 10. 排查（接入后看不到数据？）

| 现象 | 排查 |
|---|---|
| 日志查不到 | 确认写入发生在当前后端进程；确认 `OBS_LOG_MIN_LEVEL`、`task_id` 和过滤条件正确 |
| Token 不统计 | 确认 LLM 注入了 `tracker.callback`；确认 `push_context` 设了 project/task；模型返回里有 usage |
| 重启后 token/log 清空 | 这是预期行为；观测数据是实时 ring buffer，不做 MongoDB 持久化 |
| 字段筛选失效 | `level` 必须是五档之一；`source`/`event` 拼写一致 |

---

## 附：相关代码位置

| 角色 | 文件 |
|---|---|
| 日志 sink（接入点） | `core/observability/logs.py`（`obs_log` / `ObservabilityLogger`） |
| 统一路由 | `api/routers/observability.py` |
| Token 观测 | `Sere1nGraph/graph/observability/tracker.py` |
| 生命周期接线 | `api/main.py`（lifespan：初始化 ring buffer / include_router） |
