# 观测层 API 文档

## 概述

观测层（Observability Layer）统一汇聚系统运行的三类数据，供单一看板总览、分层钻取与细粒度排查：

| 数据平面 | 内容 | 采集方式 | 存储 |
|---|---|---|---|
| **状态 Status** | 任务生命周期（pending/running/completed/error、耗时、错误） | 任务执行器写 `tasks` | MongoDB `tasks` |
| **用量 Token** | Token / 费用 / 耗时，多层级聚合（全局→项目→任务→轮次→阶段→Agent） | LangChain Callback 零侵入 | 进程内环形缓冲 |
| **日志 Logs** | 结构化日志 / 事件（任意模块） | `core.observability.obs_log()` | 进程内环形缓冲 |

> 设计原则（长生命周期系统）：**统一接入、轻量内存、可扩展、可读**。高频观测数据不写 MongoDB，避免运行日志和 token 记录打爆业务数据库。

## 架构总览

```
业务/各模块 ──obs_log()──►  ObservabilityLogger (core/observability)
LLM 调用   ──Callback───►  TokenTracker      (Sere1nGraph/graph/observability)
任务执行器 ──update_one──►  tasks 集合
	                              │  线程安全内存环形缓冲
	                              ▼
	                    内存: logs / token records      MongoDB: tasks
	                              ▲
	                Router (/api/v1/observability)  ──►  前端看板
```

- **写路径**：同步写入进程内 ring buffer（O(1)，不阻塞、不依赖 event loop），达到上限自动淘汰最旧记录。
- **读路径**：只读当前进程内环形缓冲；重启后不回放历史 token/log。
- **分层职责**：core 采集（与框架解耦）/ Router HTTP / 前端展示，互不耦合，便于迭代。

## 统一 API（`/api/v1/observability`，企业级入口）

> 所有端点需 `Authorization: Bearer <JWT>`。列表查询遵循统一范式 POST + 分页（`PageRequest`/`PageResponse`）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/observability/overview` | **看板首屏单一数据源**：全局 Token + 任务状态分布 + 日志级别分布 + 近期失败任务 + 近期告警/错误日志 |
| GET | `/api/v1/observability/stats?project_id=&task_id=&phase=&agent=&task_type=` | 多层级 Token 聚合（可组合维度，含任务场景 task_type） |
| GET | `/api/v1/observability/scenarios` | **按任务场景(task_type)汇总**：每个场景一行，合并 Token 用量与任务状态分布 |
| GET | `/api/v1/observability/hierarchy?project_id=` | Token 层级树（全局→项目→任务→阶段） |
| GET | `/api/v1/observability/turns?project_id=&task_id=&limit=` | 按轮次查看 Token 用量；无 `turn_id` 时退化为单次 LLM run；每轮含 `calls` 单次调用明细 |
| POST | `/api/v1/observability/logs/query` | 分页查询日志/事件（过滤：project_id/task_id/source/level/min_level/event/since） |
| POST | `/api/v1/observability/logs` | **通用写入**：外部模块/客户端把日志/事件 push 进观测层 |
| GET | `/api/v1/observability/tasks/{task_id}/logs?limit=&min_level=` | 便捷拉取某任务日志（时间倒序） |

`GET /overview` 响应示例：
```json
{
  "token": { "total_calls": 42, "total_tokens": 160000, "total_cost_yuan": 1.23, "by_model": {}, "by_phase": {}, "by_agent": {} },
  "tasks": { "total": 12, "by_status": { "completed": 9, "running": 1, "error": 2 } },
  "logs": { "by_level": { "info": 120, "notice": 30, "error": 5 },
            "recent_warn_error": [ { "log_id": "...", "ts": 1774502400.0, "level": "error", "source": "task_runner", "event": "task_error", "task_id": "...", "message": "..." } ] },
  "recent_failed_tasks": [ { "task_id": "...", "project_id": "...", "task_type": "url_scan", "error": "...", "updated_at": "..." } ]
}
```

> 兼容：旧端点 `/api/v1/stats/global|project/{id}|task/{id}|records|hierarchy` 仍可用，与 `/observability/stats|hierarchy` 等价。新前端建议统一走 `/api/v1/observability`。

## 数据模型

### UsageRecord — 单次 LLM 调用记录

```json
{
  "model": "qwen3-max",
  "input_tokens": 3200,
  "output_tokens": 850,
  "total_tokens": 4050,
  "cost_yuan": 0.0165,
  "duration_ms": 2340.5,
  "project_id": "proj_001",
  "task_id": "task_scan_001",
  "task_type": "url_scan",
  "turn_id": "task_scan_001",
  "run_id": "b8e7...",
  "phase": "scenario",
  "agent": "web_tagging",
  "langgraph_node": "web_tagging",
  "timestamp": 1774502400.0
}
```

### AggregatedStats — 聚合统计

```json
{
  "total_calls": 42,
  "total_input_tokens": 128000,
  "total_output_tokens": 32000,
  "total_tokens": 160000,
  "total_cost_yuan": 1.2345,
  "total_duration_ms": 85000.0,
  "by_model": {
    "qwen3-max": {
      "calls": 30,
      "input_tokens": 100000,
      "output_tokens": 25000,
      "cost_yuan": 0.95
    },
    "glm-5": {
      "calls": 12,
      "input_tokens": 28000,
      "output_tokens": 7000,
      "cost_yuan": 0.28
    }
  },
  "by_phase": {
    "scenario": {"calls": 10, "input_tokens": 30000, "output_tokens": 8000, "cost_yuan": 0.3},
    "script": {"calls": 15, "input_tokens": 50000, "output_tokens": 12000, "cost_yuan": 0.5},
    "objection": {"calls": 10, "input_tokens": 30000, "output_tokens": 8000, "cost_yuan": 0.3},
    "scan": {"calls": 7, "input_tokens": 18000, "output_tokens": 4000, "cost_yuan": 0.13}
  },
  "by_agent": {
    "web_tagging": {"calls": 7, "input_tokens": 18000, "output_tokens": 4000, "cost_yuan": 0.13},
    "copywriting": {"calls": 35, "input_tokens": 110000, "output_tokens": 28000, "cost_yuan": 1.1}
  },
  "by_task_type": {
    "url_scan": {"calls": 20, "input_tokens": 80000, "output_tokens": 20000, "cost_yuan": 0.7},
    "web_tagging": {"calls": 22, "input_tokens": 48000, "output_tokens": 12000, "cost_yuan": 0.53}
  }
}
```

### Hierarchy — 层级视图（看板用）

```json
{
  "global": { "total_calls": 42, "total_tokens": 160000, "total_cost_yuan": 1.23, "..." : "..." },
  "projects": {
    "proj_001": {
      "stats": { "total_calls": 20, "total_tokens": 80000, "..." : "..." },
      "tasks": {
        "task_scan_001": {
          "stats": { "total_calls": 10, "total_tokens": 40000, "..." : "..." },
          "phases": {
            "scan": { "total_calls": 3, "..." : "..." },
            "scenario": { "total_calls": 3, "..." : "..." },
            "script": { "total_calls": 4, "..." : "..." }
          }
        }
      }
    }
  }
}
```

### LogEntry — 结构化日志/事件（task_logs）

```json
{
  "log_id": "a1b2c3d4e5f6a7b8",
  "ts": 1774502400.0,
  "level": "notice",
  "source": "task_runner",
  "event": "task_start",
  "message": "任务启动",
  "data": { "task_type": "url_scan" },
  "project_id": "proj_001",
  "task_id": "task_scan_001",
  "phase": "",
  "agent": ""
}
```

字段：`level` ∈ debug/info/notice/warning/error；`source` 来源模块；`event` 可选事件类型；`data` 任意附加结构化字段。

## API 端点（已实现 · 详细规约）

> 全部需 `Authorization: Bearer <JWT>`。**新前端统一走 `/api/v1/observability/*`**；末尾「旧端点」与之等价，仅作兼容。

### GET /api/v1/observability/overview

看板首屏单一数据源。无参数。响应见上文「统一 API」示例：`{ token, tasks, logs, recent_failed_tasks }`。

### GET /api/v1/observability/stats

多层级 Token 聚合统计。

**Query**: `project_id` / `task_id` / `phase` / `agent` / `task_type`（均可选，可组合；全空=全局）
**响应**: `AggregatedStats`（含 `by_task_type` 分组）

### GET /api/v1/observability/scenarios

按任务场景(task_type)汇总。合并两源：Token 用量（TokenTracker `by_task_type`）与任务状态分布（`tasks` 集合按 task_type + status 分组）。场景全集 = tasks 集合出现的 task_type ∪ token 记录 by_task_type 的 key。无参数。

**响应**:
```json
{
  "items": [
    {
      "task_type": "url_scan",
      "token": {
        "total_calls": 20, "total_tokens": 100000, "total_cost_yuan": 0.7,
        "by_model": {}, "by_phase": {}, "by_agent": {}, "by_task_type": {"url_scan": {}}
      },
      "tasks": { "total": 8, "by_status": { "completed": 6, "running": 1, "error": 1 } }
    }
  ],
  "total": 1
}
```

> 历史 token 记录无 `task_type`，不计入场景聚合；仅在 `tasks` 中出现的场景其 `token` 为零值统计。

### GET /api/v1/observability/hierarchy

Token 层级树（全局→项目→任务→阶段）。**Query**: `project_id`（可选）。**响应**: `Hierarchy`

### GET /api/v1/observability/turns

按轮次查看 Token 用量。每个轮次同时返回聚合字段、模型/阶段/Agent 分布，以及 `calls` 单次 LLM 调用明细。无 `turn_id` 的历史记录会按单次 `run_id` 展示。

**Query**: `project_id` / `task_id` / `limit`（默认 100，最大 500）

**响应**:
```json
{
  "items": [
    {
      "turn_key": "turn_001",
      "turn_id": "turn_001",
      "project_id": "proj_001",
      "task_id": "task_scan_001",
      "started_at": 1774502400.0,
      "ended_at": 1774502412.0,
      "total_calls": 2,
      "total_input_tokens": 6200,
      "total_output_tokens": 1400,
      "total_tokens": 7600,
      "total_cost_yuan": 0.031,
      "total_duration_ms": 4320.5,
      "by_model": { "qwen3-max": { "calls": 2, "input_tokens": 6200, "output_tokens": 1400, "cost_yuan": 0.031 } },
      "by_phase": { "screen": { "calls": 1, "input_tokens": 3000, "output_tokens": 500, "cost_yuan": 0.012 } },
      "by_agent": { "mobile_executor": { "calls": 1, "input_tokens": 3200, "output_tokens": 900, "cost_yuan": 0.019 } },
      "calls": [
        {
          "call_index": 1,
          "model": "qwen3-max",
          "input_tokens": 3000,
          "output_tokens": 500,
          "total_tokens": 3500,
          "cost_yuan": 0.012,
          "duration_ms": 1700.0,
          "project_id": "proj_001",
          "task_id": "task_scan_001",
          "turn_id": "turn_001",
          "run_id": "run_001",
          "phase": "screen",
          "agent": "mobile_screen",
          "langgraph_node": "screen_reader",
          "timestamp": 1774502400.0
        }
      ]
    }
  ],
  "total": 1,
  "limit": 100
}
```

### POST /api/v1/observability/logs/query

分页查询日志/事件。

**请求体**（均可选）:
```json
{
  "page": 1, "page_size": 50,
  "project_id": "", "task_id": "", "source": "",
  "level": "", "min_level": "warning", "event": "", "since": 1774500000.0
}
```
- `level` 精确匹配；`min_level` 取该级别及以上（debug<info<notice<warning<error），二选一。
- `since`：unix 秒时间戳，取该时刻之后。

**响应**（标准分页，`items` 为 `LogEntry[]`，按 ts 倒序）:
```json
{ "items": [ { "log_id": "...", "ts": 1774502400.0, "level": "error", "source": "xhs_pipeline",
               "event": "fetch_error", "message": "...", "data": {}, "project_id": "...", "task_id": "..." } ],
  "total": 137, "page": 1, "page_size": 50 }
```

### POST /api/v1/observability/logs

通用日志/事件写入（外部模块/客户端接入观测层）。

**请求体**: `{ "message": "...", "task_id"?, "project_id"?, "source"?, "level"?, "event"?, "data"?, "phase"?, "agent"? }`
**响应**: `{ "ok": true, "log_id": "..." }`

### GET /api/v1/observability/tasks/{task_id}/logs

便捷拉取某任务日志（时间倒序）。

**Query**: `limit`（默认 200）, `min_level`（可选）
**响应**: `{ "task_id": "...", "items": LogEntry[], "total": <int> }`

### 旧端点（兼容，等价上面）

| 旧 | 等价新端点 |
|---|---|
| `GET /api/v1/stats/global` | `GET /api/v1/observability/stats`（含 projects 列表） |
| `GET /api/v1/stats/project/{project_id}` | `GET /api/v1/observability/stats?project_id=` |
| `GET /api/v1/stats/task/{task_id}` | `GET /api/v1/observability/stats?task_id=` |
| `GET /api/v1/stats/hierarchy?project_id=` | `GET /api/v1/observability/hierarchy?project_id=` |
| `GET /api/v1/stats/records?project_id=&task_id=&limit=` | （细粒度 Token 记录，保留） |

## 数据留存与实时监测（v4 · 重要）

Token 与结构化日志采用**进程内环形缓冲**，只服务实时观测，不写 MongoDB：

| 数据 | 默认上限 | 环境变量 | 说明 |
|---|---|---|---|
| Token records | 5000 条 | `TOKEN_TRACKER_MAX_RECORDS` | 超出后淘汰最旧记录；支持全局/项目/任务/轮次聚合 |
| Logs | 10000 条 | `OBS_LOG_MAX_RECORDS` | 超出后淘汰最旧记录；`OBS_LOG_MIN_LEVEL` 控制进入观测 API 的最低级别 |

删除项目/任务时会同步清理当前进程内的 token/log 缓冲。旧版本遗留的 `token_usage_records` / `task_logs` 集合只在删除流程里做兼容清理，运行时不再新增、不再查询。

## 接入新模块（扩展指南）

> 完整接入指南（理念 / 方式 A·B / 字段约定 / 分场景示例 / Token·Status 接入 / 生命周期清理 / 接入清单 / 排查）见专门沉淀文档：[`OBSERVABILITY_INTEGRATION.md`](./OBSERVABILITY_INTEGRATION.md)。下面是速览：

任意进程内模块接入观测层只需一行：

```python
from core.observability import obs_log

obs_log(
    "扫描开始",
    task_id=task_id, project_id=project_id,
    source="xhs_pipeline",   # 来源模块名（按模块筛选）
    level="info",            # debug/info/notice/warning/error
    event="scan_start",      # 可选事件类型（结构化筛选）
    data={"keyword": kw},    # 任意附加字段
)
```

- 同步、非阻塞、不依赖 event loop；写入当前进程内日志 ring buffer。
- 进程外 / 前端可走 `POST /api/v1/observability/logs` 写入。
- 写入后即可在 `/observability/logs/query`、`/observability/tasks/{id}/logs`、`/observability/overview` 看到。
- 任务执行器已内置 `task_start` / `task_done` / `task_error` 事件（`source=task_runner`）。

**约定**：`source` 用模块名（`task_runner` / `mobile_agent` / `xhs_pipeline` …），`level` 统一五档，`event` 用动词短语。新模块零额外开发即纳入统一看板。

> `GET /api/v1/projects/{id}/dashboard` 已聚合 findings/任务计数/各源计数/Top10/**token 消耗**，是项目级总览入口。

## 已接入观测的来源与事件（source / event 枚举）

前端可据此对 `source` / `event` 做过滤与可视化（均带 `task_id` / `project_id`）：

| source | event | 触发时机 |
|---|---|---|
| `task_runner` | `task_start` / `task_done` / `task_error` | 任意任务的生命周期（覆盖全部 5 类 pipeline，任务级） |
| `xhs_pipeline` | `pipeline_start` / `search_done` / `profiles_done` / `pipeline_done` / `pipeline_error` | 小红书采集（阶段级） |
| `douyin_pipeline` | `pipeline_start` / `search_done` / `pipeline_done` / `pipeline_error` | 抖音采集 |
| `url_scan_pipeline` | `pipeline_start` / `pipeline_done` / `pipeline_error` | URL 扫描 |
| `company_scan_pipeline` | `pipeline_start` / `pipeline_done` / `pipeline_error` | 综合公司扫描 |
| `web_tagging_pipeline` | `pipeline_start` / `pipeline_done` / `pipeline_error` | Web 打标 |

> `pipeline_done` 的 `data` 含各自的产出计数（如 notes/profiles/findings/elapsed_ms）；`pipeline_error` 的 `data.error` 为错误信息。新增 pipeline 按同一约定加 `obs_log` 即自动纳入（见集成指南）。

## 日志存储 · 与 logger 关系 · 容量控制（重要）

### 存哪里
`obs_log` 写入当前后端进程内的日志 ring buffer。Token 记录也只在 `TokenTracker` ring buffer。任务状态仍存 MongoDB `tasks`。

### 与原有 `logger.info` 的关系（互补，不冲突）

| | 标准 logger（`core.logger`） | `obs_log`（观测层） |
|---|---|---|
| 去向 | 控制台 / 文件 | 内存 ring buffer |
| 用途 | 开发/运维实时 tail、verbose 调试 | 看板实时查看、结构化筛选、当前进程排查 |
| 粒度 | 任意细节（每行） | **仅里程碑 / 事件 / 错误** |
| 入库 | 否 | 否 |

- 二者是**两个独立 sink，互不冲突**。同一里程碑可能两边都有（一个给控制台、一个给看板），属有意为之。
- 约定：**高频细节只走 logger，不走 obs_log**；obs_log 只记 `start/done/error` 与关键阶段。各 pipeline 已按此约定接入（每任务约 3–8 条）。

### 容量控制（防止打满内存）

| 手段 | 默认 | 调整 |
|---|---|---|
| **日志环形上限** | 10000 条 | 环境变量 `OBS_LOG_MAX_RECORDS` |
| **Token 环形上限** | 5000 条 | 环境变量 `TOKEN_TRACKER_MAX_RECORDS` |
| **记录级别阈值** | `info`（`debug` 不进观测 API） | 环境变量 `OBS_LOG_MIN_LEVEL` |
| **降采样约定** | 高频循环只记里程碑/异常 | 接入方遵守（集成指南 §8） |

- 环形缓冲达到上限后自动淘汰最旧记录，内存占用稳定。
- 重启会清空 token/log 实时观测数据；需要长期审计的业务结果应存入对应业务集合，而不是观测集合。

## 定价模型

### 阶梯定价

| 模型 | 总 Token ≤ | 输入 (¥/M) | 输出 (¥/M) |
|------|-----------|-----------|-----------|
| qwen3-max | 32K | 2.5 | 10.0 |
| qwen3-max | 128K | 4.0 | 16.0 |
| qwen3-max | 252K | 7.0 | 28.0 |
| qwen3.5-plus | 128K | 0.8 | 4.8 |
| qwen3.5-plus | 256K | 2.0 | 12.0 |
| qwen3.5-plus | 1M | 4.0 | 24.0 |

### 固定定价

| 模型 | 输入 (¥/M) | 输出 (¥/M) |
|------|-----------|-----------|
| kimi-k2.5 | 4.0 | 21.0 |
| glm-4.7 / glm-5 | 4.0 | 21.0 |
| claude-opus-4.6/4.5 | 7.5 | 37.5 |
| claude-opus-4.1/4 | 22.5 | 112.5 |
| claude-sonnet-4.x | 4.5 | 22.5 |

## 前端看板建议

### 全局卡片（Dashboard）
- 累计 Token（带千分位）
- 累计费用（¥ 前缀，4 位小数）
- 总任务数
- 模型用量分布（饼图/条形图）

### 任务详情侧边栏
- 总览 Tab：总 Token + 费用 + 输入/输出占比
- 模型 Tab：按模型分组的调用统计
- 阶段 Tab：按 phase 分组的进度条

## Roadmap

已落地（v4）：
- ✅ **内存任务日志** —— `core.observability.obs_log` + `/observability/logs/query`、`/observability/tasks/{id}/logs`。
- ✅ **统一观测总览** —— `GET /api/v1/observability/overview`（Token + 任务状态 + 日志 + 近期失败）。
- ✅ **轮次 Token 观测** —— `GET /api/v1/observability/turns`。

后续可扩展（向后兼容）：
- 指标/计时 span（`obs_span`）与 trace 关联，做端到端耗时火焰图。
- 观测导出到独立指标/日志系统，避免占用业务 MongoDB。
- 告警规则（错误率 / 费用阈值）+ Webhook（复用钉钉通道）。
- 接入 OpenTelemetry 导出，对接 Grafana/Tempo。
