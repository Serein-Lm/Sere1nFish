# URL 扫描 API

## 范围

URL 扫描负责 URL 规范化、并发探活、浏览器读取、结构化分析、来源证据归档和 Finding 写入。话术生成是可选阶段；FindingContext 默认不自动生成，只在用户显式整理时运行。

统一任务契约见 [UNIFIED_TASK_API.md](./UNIFIED_TASK_API.md)。本文只描述 `url_scan` 的调用和结果读取。

所有接口前缀为 `/api/v1`，均需登录。

## 下发任务

### JSON

`POST /api/v1/projects/{project_id}/tasks`

```json
{
  "task_type": "url_scan",
  "params": {
    "urls": [
      "https://example.com",
      "https://example.org/contact"
    ],
    "min_attention_score": 40,
    "enable_copywriting": false,
    "selected_skill_ids": []
  }
}
```

也可使用每行一个 URL 的文本：

```json
{
  "task_type": "url_scan",
  "params": {
    "url_text": "https://example.com\nhttps://example.org/contact",
    "min_attention_score": 40
  }
}
```

`urls` 与 `url_text` 至少提供一个；同时存在时使用 `urls`。

成功响应：

```json
{
  "task_id": "a1b2c3d4e5f6",
  "task_type": "url_scan",
  "status": "pending"
}
```

### 文件上传

`POST /api/v1/projects/{project_id}/tasks/upload`

`multipart/form-data` 字段：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `file` | 是 | 文本文件，每行一个 URL |
| `task_type` | 是 | 固定为 `url_scan` |
| `params_json` | 否 | JSON 对象字符串 |

示例：

```bash
curl -k \
  -H "Authorization: Bearer $TOKEN" \
  -F 'file=@urls.txt' \
  -F 'task_type=url_scan' \
  -F 'params_json={"min_attention_score":40,"enable_copywriting":false}' \
  "https://127.0.0.1/api/v1/projects/$PROJECT_ID/tasks/upload"
```

`params_json` 不是合法 JSON 对象时返回 `400`。

## 参数

| 参数 | 默认 | 说明 |
|---|---:|---|
| `urls` | `[]` | URL 数组 |
| `url_text` | `""` | 每行一个 URL 的文本 |
| `min_attention_score` | `40` | Finding 最低关注分 |
| `enable_copywriting` | `true` | 是否在扫描后生成话术；批量采集建议关闭 |
| `selected_skill_ids` | `[]` | 显式 Skill；空数组表示运行时按场景选择 |
| `url_probe_concurrency` | 运行时配置 | 单任务探活并发覆盖值 |
| `url_scan_concurrency` | 运行时配置 | 单任务浏览器扫描并发覆盖值 |
| `copywriting_concurrency` | 运行时配置 | 单任务话术并发覆盖值 |

所有并发值最终由 `collection_runtime` 限幅。客户端不能依赖请求值被原样采用。

## 状态与控制

### 任务详情

`GET /api/v1/projects/{project_id}/tasks/{task_id}`

任务详情包含持久化 `status`、`progress`、参数、错误和结果。常见阶段状态可能包含 `pending`、探活、扫描、生成、完成、暂停或错误；调用方应把未知非终态作为“仍在处理”，不要硬编码完整枚举。

### 任务列表

`POST /api/v1/projects/{project_id}/tasks/list`

```json
{
  "project_id": "project-id",
  "task_type": "url_scan",
  "page": 1,
  "page_size": 20
}
```

返回 `{items,total,page,page_size}`。列表只包含展示所需投影，完整 result/checkpoint 必须通过任务详情按需读取。

### 暂停与恢复

```text
POST /api/v1/projects/{project_id}/tasks/{task_id}/pause
POST /api/v1/projects/{project_id}/tasks/{task_id}/resume
```

恢复沿用原 `task_id` 和持久化检查点；前端不能通过新建重复任务模拟恢复。

### 删除

```text
DELETE /api/v1/projects/{project_id}/tasks/{task_id}
DELETE /api/v1/projects/{project_id}/tasks?status=error
```

删除任务会同步清理其项目级 Finding、话术、Token 记录和任务日志。来源文档的全局身份及仍被其他场景引用的证据不得按任务误删。

## Findings

### 分页查询

`POST /api/v1/projects/{project_id}/findings`

```json
{
  "project_id": "project-id",
  "task_id": "a1b2c3d4e5f6",
  "target_id": "",
  "source": "",
  "type": "",
  "min_score": 40,
  "sort": "score_desc",
  "include_safe": false,
  "summary_only": true,
  "page": 1,
  "page_size": 20
}
```

- 路径中的 `project_id` 是实际查询边界。
- `task_id` 同时匹配任务及其子任务关联。
- `summary_only=true` 用于列表，详情另行读取。
- `include_safe=true` 时附加最多 500 条无 Finding URL；默认关闭以控制响应体积。

### 项目摘要与详情

```text
GET /api/v1/projects/{project_id}/findings/summary
GET /api/v1/findings/{finding_id}
GET /api/v1/findings/{finding_id}/copywriting
GET /api/v1/findings/{finding_id}/context
POST /api/v1/findings/{finding_id}/context/organize
```

Finding 必须保留 `target_id`、来源文档及版本 ID、原文 URL、邻近上下文和证据引用。相同联系方式通过稳定 `group_key` 在读模型聚合，不能通过覆盖或物理删除来源 Finding 实现去重。

## 来源与证据

扫描成功后，来源按以下层次保存：

```text
SourceDocument             规范 URL 的全局身份
SourceDocumentVersion      稳定正文哈希对应的不可变版本
SourceDocumentLink         Project/Target/Task 场景关系
Finding                    从版本证据派生的项目事实
FindingContext             用户按需生成的结构化上下文
```

原始 HTML、渲染 DOM、截图、原图和结构化来源 JSON 通过 `ObjectStorageService` 保存到私有对象存储。业务集合只保存 `storage_object_id`；前端通过鉴权 API 或短时签名 URL 读取。

## Skill 加载

`selected_skill_ids` 会在任务入库前按数据库运行时索引校验，最多 32 个。Agent 先加载索引，再按场景读取正文和所需资源；禁止把完整 Skill 资源树一次性写入 Prompt。

接口和同步细节见 [SKILL_SYSTEM_API.md](./SKILL_SYSTEM_API.md)。

## 验证要求

- 空 URL、未知 Skill 和无效并发参数返回明确 `400`。
- 同一 URL 的 `http/https` 及规范化变体按既有 canonical 规则归并。
- 已完成的探活结果在深扫入口复用，不重复探活。
- 页面失败、空截图、附件失败与目标站拒绝使用不同错误语义并保留恢复点。
- 重跑同一来源版本不会重复写 Finding evidence；重分析会按稳定记录 ID 对账。
- 列表响应不携带大型结果和证据正文。
