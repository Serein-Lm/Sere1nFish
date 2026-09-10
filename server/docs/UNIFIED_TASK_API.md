# 统一项目任务 API

## 设计边界

项目任务统一通过 `api.services.project_tasks` 下发：

```text
HTTP / AI 工具 / 文件上传
  -> submit_project_task
  -> 参数规范化与能力校验
  -> tasks 持久化
  -> ProjectTaskDefinition registry
  -> project_task_runtime
  -> 对应 pipeline adapter
```

- Router 只负责鉴权、HTTP 输入输出和错误码映射。
- `registry.py` 是任务能力的唯一注册表；前端不得硬编码另一套任务清单。
- JSON 与文件上传入口共用同一套校验、持久化和调度逻辑。
- 任务在调度前写入 MongoDB，后台执行通过 `core.background.spawn_background` 持有。
- 具体采集实现收敛在 dispatcher/pipeline，调用方不感知第三方 SDK。

所有接口都需要登录，基础前缀为 `/api/v1`。

## 能力发现

### `GET /api/v1/project-task-types`

返回当前注册的任务能力，供前端、AI 中枢和外部控制面动态生成入口。

```json
{
  "items": [
    {
      "task_type": "url_scan",
      "label": "URL 扫描",
      "supports_file": true,
      "supports_skills": true
    }
  ]
}
```

当前注册能力：

| `task_type` | 说明 | 文件输入 | Skill 选择 |
|---|---|---:|---:|
| `url_scan` | URL 扫描 | 是 | 是 |
| `xhs_search` | 小红书搜索 | 否 | 否 |
| `douyin_search` | 抖音搜索 | 否 | 否 |
| `web_tagging` | 网站标注 | 否 | 否 |
| `company_scan` | 综合公司扫描 | 是 | 是 |
| `fofa_collect` | 资产发现与深扫 | 否 | 是 |
| `scholar_contact` | 学者联系采集 | 否 | 否 |
| `mobile_collect` | 手机采集 | 否 | 否 |
| `target_research` | Target 深研 | 否 | 否 |
| `social_media_collect` | 社交地点媒体采集 | 否 | 否 |

新增任务类型时，必须在 `api/services/project_tasks/registry.py` 注册定义并实现 dispatcher，不能在 Router 增加平行分发分支。

## 任务下发

### `POST /api/v1/projects/{project_id}/tasks`

JSON 方式下发单个任务。

```json
{
  "task_type": "url_scan",
  "params": {
    "urls": ["https://example.com"],
    "min_attention_score": 40,
    "enable_copywriting": false,
    "selected_skill_ids": ["docx"]
  }
}
```

成功响应：

```json
{
  "task_id": "a1b2c3d4e5f6",
  "task_type": "url_scan",
  "status": "pending"
}
```

错误语义：

| HTTP 状态 | 含义 |
|---:|---|
| `400` | 未注册任务类型、参数错误、Skill 不存在或任务不支持文件输入 |
| `401/403` | 未登录或无权限 |
| `404` | 项目不存在 |

### `POST /api/v1/projects/{project_id}/tasks/upload`

使用 `multipart/form-data` 上传文本文件并下发任务：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `file` | File | 是 | UTF-8 文本；无法解码的字节被忽略 |
| `task_type` | string | 是 | 当前仅 `url_scan`、`company_scan` 支持 |
| `params_json` | string | 否 | JSON 对象字符串，默认 `{}` |

文件正文由注册表定义注入 `params.url_text`。`params_json` 格式错误、数组或标量输入均返回 `400`，不会静默回退为空参数。

## 主要参数

### URL 扫描

```json
{
  "task_type": "url_scan",
  "params": {
    "urls": ["https://example.com"],
    "url_text": "https://example.org\nhttps://example.net",
    "min_attention_score": 40,
    "enable_copywriting": true,
    "url_probe_concurrency": 20,
    "url_scan_concurrency": 16,
    "copywriting_concurrency": 8,
    "selected_skill_ids": []
  }
}
```

`urls` 与 `url_text` 至少提供一个；两者同时存在时 dispatcher 使用 `urls`。空 `selected_skill_ids` 表示由运行时按场景渐进选择 Skill。

### 综合公司扫描

```json
{
  "task_type": "company_scan",
  "params": {
    "company_name": "示例单位",
    "target_id": "tgt_xxx",
    "target_batch_tags": ["第三批", "重点"],
    "enable_url_scan": true,
    "enable_asset_discovery": true,
    "enable_wechat": false,
    "wechat_app_instance": "clone",
    "enable_xhs": false,
    "enable_scholar": true,
    "enable_bidding": true,
    "bidding_lookback_days": 30,
    "enable_control_structure": true,
    "control_max_depth": 2,
    "subsidiary_scan_limit": 12,
    "website_collection_mode": "deep",
    "incremental_scan": false,
    "enable_copywriting": false,
    "selected_skill_ids": []
  }
}
```

关键约束：

- `company_name` 必填。
- 官网归档模式只能是 `standard` 或 `deep`。
- 招投标回溯窗口为 `1..30` 天。
- 控股钻取深度只能是 `1` 或 `2`。
- 公众号启用时会在任务入库前校验设备及微信实例定义。
- 并发参数经过 `collection_runtime` 统一限幅，任务参数只作单次覆盖。

### 其他任务的最低必填参数

| `task_type` | 必填参数 |
|---|---|
| `xhs_search` | `keyword`；可选 `target_id` 必须属于当前项目 |
| `douyin_search` | `keyword` |
| `web_tagging` | `company_name` |
| `fofa_collect` | `company_name` |
| `scholar_contact` | `unit`、`direction` |

`mobile_collect`、`target_research`、`social_media_collect` 的完整参数由对应 service schema 管理；前端应先读取能力清单，再使用其类型化 service 构造参数。

## 批量与补扫

### `POST /api/v1/projects/{project_id}/tasks/company-scan-batch`

为每个公司创建独立、可追踪的 `company_scan` 任务。

```json
{
  "company_names": ["单位甲", "单位乙"],
  "params": {
    "enable_url_scan": true,
    "enable_wechat": false,
    "website_collection_mode": "deep"
  }
}
```

批量任务不能向多个公司共用 `urls`、`url_text` 或 `website_root_domains`，避免错误归属。实际核心并发与 dispatch 上限由采集运行时配置控制。

### `POST /api/v1/projects/{project_id}/tasks/company-scan-coverage`

按 Target、业务批次和所需渠道规划缺口补扫。默认 `dry_run=true`，先返回去重后的计划；确认后以 `dry_run=false` 下发。该接口用于补齐覆盖，不应用来重复执行已有完整渠道。

## 查询与控制

| 接口 | 说明 |
|---|---|
| `POST /api/v1/projects/{project_id}/tasks/list` | 分页返回轻量任务摘要；大型结果不随列表返回 |
| `GET /api/v1/projects/{project_id}/tasks/{task_id}` | 按需读取任务详情、进度和结果 |
| `POST /api/v1/projects/{project_id}/tasks/{task_id}/pause` | 暂停可暂停任务并保留恢复点 |
| `POST /api/v1/projects/{project_id}/tasks/{task_id}/resume` | 使用原 `task_id` 恢复任务 |
| `DELETE /api/v1/projects/{project_id}/tasks/{task_id}` | 删除任务及其项目级派生数据 |
| `DELETE /api/v1/projects/{project_id}/tasks` | 按可选 `status` 批量删除 |

任务列表使用 POST 是为了承载统一分页筛选 schema。状态以持久化值为准，前端不得从进程内事件推断最终状态。

## Findings 与观测

| 接口 | 说明 |
|---|---|
| `GET /api/v1/projects/{project_id}/findings/summary` | 项目 Finding 轻量摘要 |
| `GET /api/v1/findings/{finding_id}` | Finding 详情与证据引用 |
| `GET /api/v1/findings/{finding_id}/context` | 按需读取 FindingContext |
| `POST /api/v1/findings/{finding_id}/context/organize` | 显式启动上下文整理；默认采集不自动运行 |
| `GET /api/v1/findings/{finding_id}/copywriting` | 读取话术 |
| `GET /api/v1/stats/global` | 全局观测统计 |
| `GET /api/v1/stats/project/{project_id}` | 项目观测统计 |
| `GET /api/v1/stats/task/{task_id}` | 任务观测统计 |
| `GET /api/v1/stats/hierarchy` | 观测层级树 |
| `GET /api/v1/stats/records` | 分页观测记录 |

采集、上下文整理和话术是不同阶段。`finding_context.auto_generate` 默认关闭，不能因下发扫描任务而同步消耗上下文整理 Token。

## 兼容与扩展要求

- 旧来源专用 API 可在迁移期保留，但新入口必须汇入 `submit_project_task` 或同一领域 service。
- 新增 public 参数时保持向后兼容，并在 validation 层设置范围与默认值。
- 新增任务必须覆盖：参数错误、项目不存在、持久化成功、后台调度、失败终态、重复执行和资源释放。
- 前端使用 `view/src/services` 中的类型化调用，不直接拼接这些路径。
