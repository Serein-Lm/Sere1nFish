# 统一任务下发 API 文档

## 当前状态

### 已有的下发接口（分散在各 router）

| 接口 | 位置 | 方式 | 说明 |
|------|------|------|------|
| `POST /api/v1/xhs/search` | `api/routers/xhs.py` | BackgroundTasks | 小红书关键词搜索 → 全流程 pipeline |
| `POST /api/v1/douyin/search` | `api/routers/douyin.py` | BackgroundTasks | 抖音关键词搜索 → 全流程 pipeline |
| `POST /api/v1/projects/{id}/web-tagging` | `api/routers/projects.py` | BackgroundTasks | 官网打标（通过项目路由触发） |

### 新增的统一接口

| 接口 | 位置 | 方式 | 说明 |
|------|------|------|------|
| `POST /api/v1/tasks/create` | `api/routers/tasks.py` | BackgroundTasks | 统一下发，task_type 分发 |
| `POST /api/v1/tasks/upload` | `api/routers/tasks.py` | BackgroundTasks | 带文件上传的统一下发 |

### 后续迁移计划

旧接口暂时保留（不破坏前端），新功能统一走 `/api/v1/tasks/create`。后续逐步把 xhs/douyin/web_tagging 的下发迁入统一接口。

---

## 统一下发接口

### POST /api/v1/tasks/create

JSON body 方式下发任务。

**请求**:
```json
{
  "project_id": "proj_001",
  "task_type": "url_scan | xhs_search | douyin_search | web_tagging",
  "params": { ... }
}
```

**响应**:
```json
{
  "task_id": "a1b2c3d4e5f6",
  "task_type": "url_scan",
  "status": "pending"
}
```

### POST /api/v1/tasks/upload

带文件上传的下发（multipart/form-data）。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | File | 是 | 上传文件（如 url.txt） |
| `project_id` | string | 是 | 项目 ID |
| `task_type` | string | 是 | 任务类型 |
| `params_json` | string | 否 | 其他参数（JSON 字符串） |

文件内容自动注入到 params 的对应字段（url_scan → `params.url_text`）。

---

## task_type 参数说明

### url_scan — URL 扫描 + 话术生成

```json
{
  "task_type": "url_scan",
  "params": {
    "urls": ["https://example.com", "target.cn"],
    "url_text": "也可以直接传文本，每行一个URL",
    "selected_categories": ["wechat", "email", "phone"],
    "min_attention_score": 40
  }
}
```

`urls` 和 `url_text` 二选一，`urls` 优先。`selected_categories` 为空 = 加载全部 skill。

**流程**: 解析标准化 → 探活 → Agent 扫描 → 提取信息节点 → 每个节点生成话术

### xhs_search — 小红书搜索

```json
{
  "task_type": "xhs_search",
  "params": {
    "keyword": "字节跳动 产品经理",
    "max_notes": 20,
    "attention_threshold": 60
  }
}
```

复用 `api/services/xhs_pipeline.run_xhs_pipeline()`。

### douyin_search — 抖音搜索

```json
{
  "task_type": "douyin_search",
  "params": {
    "keyword": "字节跳动",
    "max_results": 20,
    "publish_time": 7
  }
}
```

复用 `api/services/douyin_pipeline.run_douyin_pipeline()`。

### web_tagging — 官网打标

```json
{
  "task_type": "web_tagging",
  "params": {
    "company_name": "字节跳动",
    "max_urls": 50,
    "max_tagging_urls": 10
  }
}
```

复用 `api/services/web_tagging_pipeline.run_web_tagging_pipeline()`。

---

## 查询接口

### GET /api/v1/tasks?project_id=xxx&task_type=url_scan

列出任务。`task_type` 可选过滤。

### GET /api/v1/tasks/{task_id}

获取任务状态（前端轮询用）。

**响应**:
```json
{
  "task_id": "a1b2c3d4e5f6",
  "project_id": "proj_001",
  "task_type": "url_scan",
  "params": { ... },
  "status": "pending | running | completed | error",
  "progress": {},
  "error": null,
  "created_at": "2026-03-26T12:00:00",
  "updated_at": "2026-03-26T12:05:00"
}
```

### GET /api/v1/tasks/{task_id}/findings

获取信息节点（按 attention_score 降序）。

### GET /api/v1/findings/{finding_id}/copywriting

获取单个信息节点的话术。

---

## 观测层接口

### GET /api/v1/stats/global — 全局统计
### GET /api/v1/stats/project/{project_id} — 项目级
### GET /api/v1/stats/task/{task_id} — 任务级
### GET /api/v1/stats/hierarchy?project_id= — 层级树（看板用）
### GET /api/v1/stats/records?project_id=&task_id=&limit=50 — 原始记录

---

## Skills 接口

### GET /api/v1/skills — 列出所有 skills 索引
### GET /api/v1/skills/{skill_id} — 获取 skill 完整内容

---

## 话术生成核心

### Skill 系统

话术生成的核心是 Skill 系统（渐进式披露架构）。每个 Skill 包含：

- **SKILL.md** — 指令（明确引用 Pydantic Schema 字段名）
- **references/** — 实战案例（JSON Schema 格式）

当前 13 个内置 Skill，按渠道（wechat/email/phone/intranet/sms）和场景（recruitment/vendor/it-support）分类。

### 输出格式

所有话术输出为 `FindingCopywriting` Pydantic Schema：

- `scenario` — 场景伪造（FakedIdentity + LogicChainStep）
- `scripts[]` — 各渠道话术（channel 字段 = 前端渲染 type）
  - `channel: "wechat"` → 微信聊天气泡
  - `channel: "email"` → 邮件卡片
  - `channel: "phone"` → 电话对话
- `payload` — 样本文件规格
- `objections[]` — 质疑应对（每条带 tactic 心理策略标签）
- `target_analysis` — 对目标信息的理解
- `psychology_strategy` — 核心心理策略
- `case_reference` — 参考的实际案例

### Prompt 核心

话术生成的 prompt 由三部分组成：

1. **Skill body**（Layer 2）— 各渠道/场景的具体指令
2. **References**（Layer 3）— 实战案例，JSON 格式可直接参考
3. **Schema 约束** — Pydantic JSON Schema 注入到 system prompt，强制结构化输出

Skill 根据信息节点的 `channel` 和 `type` 自动选择：
- email finding → 加载 email + base-scenario + base-objection + payload skill
- hr_contact type → 额外加载 recruitment skill
- 所有场景都加载 real-cases skill 的 references
