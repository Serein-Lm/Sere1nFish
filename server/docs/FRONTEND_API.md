# 前端 API 对接文档

> Base URL: `/api/v1`
> 认证: 所有接口（除 login/health）需要 `Authorization: Bearer <token>`

---

## 数据存储架构

```
Project (项目)
  │
  ├── Tasks[] (任务)
  │     task_type: company_scan / url_scan / xhs_search / douyin_search / web_tagging
  │     status: pending → running → completed / error
  │
  ├── Findings[] (统一发现，所有数据源汇聚)
  │     ├── source: web_tagging  → 来自官网打标
  │     ├── source: xhs          → 来自小红书
  │     └── source: douyin       → 来自抖音
  │           │
  │           ├── → Copywriting (话术方案，1:1 关联 finding)
  │           └── → Profile (人物画像，1:1 关联 finding)
  │
  ├── XHS Notes[] (小红书笔记原始数据)
  │     └── Note Detail (笔记详情分析)
  │
  ├── XHS Profiles[] (小红书人物画像)
  │
  ├── Web Tagging Results[] (官网打标原始数据)
  │
  ├── Douyin Search Results[] (抖音搜索原始数据)
  ├── Douyin Tagged Results[] (抖音打标结果)
  └── Douyin Profiles[] (抖音用户画像)
```

核心关联：
- `finding_id` 全局唯一，串联 finding → copywriting → profile → notes
- 画像表（xhs_profiles / douyin_profiles）直接存 `finding_id`，前端可直接用它查话术
- `project_id` 所有数据的归属维度
- `task_id` 标记数据由哪个任务产生

---

## 统一分页规范

所有列表接口统一 **POST** + JSON Body。

```json
// 请求
{ "page": 1, "page_size": 10 }
// 响应
{ "items": [...], "total": 42, "page": 1, "page_size": 10 }
```

| 字段 | 类型 | 默认 | 范围 |
|------|------|------|------|
| `page` | int | 1 | ≥1 |
| `page_size` | int | 10 | 1-200 |

总页数 = `Math.ceil(total / page_size)`

---

## 1. 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/auth/login` | 登录 |
| POST | `/auth/logout` | 登出 |
| GET | `/auth/me` | 当前用户信息 |
| POST | `/auth/change-password` | 修改密码 |

```json
// POST /auth/login
{ "username": "admin", "password": "xxx", "key": "登录密钥" }
// 响应
{ "access_token": "xxx", "token_type": "bearer" }
```

---

## 2. 项目

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/projects` | 创建项目 |
| POST | `/projects/list` | 列出项目（分页） |
| GET | `/projects/{id}` | 获取项目 |
| PATCH | `/projects/{id}` | 更新项目 |
| DELETE | `/projects/{id}` | 删除项目（级联删除所有关联数据） |

---

## 3. 任务

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/projects/{id}/tasks` | 下发任务 |
| POST | `/projects/{id}/tasks/upload` | 带文件下发 |
| POST | `/projects/{id}/tasks/list` | 列出任务（分页） |
| GET | `/projects/{id}/tasks/{tid}` | 任务状态（轮询 3s） |
| DELETE | `/projects/{id}/tasks/{tid}` | 删除任务 |
| DELETE | `/projects/{id}/tasks?status=error` | 批量删除 |

### POST /projects/{id}/tasks

```json
{
  "task_type": "company_scan",
  "params": { "company_name": "百度", "urls": ["https://www.baidu.com"] }
}
```

| task_type | 说明 | 核心参数 |
|-----------|------|---------|
| `company_scan` | 综合扫描 | `company_name`, `urls`, `enable_url_scan`, `enable_xhs`, `enable_subsidiary_xhs`（默认 `false`） |
| `url_scan` | URL 扫描 | `urls` / `url_text`, `min_attention_score` |
| `xhs_search` | 小红书搜索 | `keyword`, `max_notes`, `attention_threshold` |
| `douyin_search` | 抖音搜索 | `keyword`, `max_videos`, `publish_time` |
| `web_tagging` | 官网打标 | `company_name`, `max_urls` |

### POST /projects/{id}/tasks/list

```json
{ "project_id": "660a...", "page": 1, "page_size": 10, "task_type": "" }
```

---

## 4. Findings（核心聚合层）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/projects/{id}/findings/summary` | 项目看板统计 |
| POST | `/projects/{id}/findings` | 分页查询 findings |
| GET | `/findings/{fid}` | finding 详情 |
| GET | `/findings/{fid}/copywriting` | 关联话术 |
| GET | `/findings/{fid}/profile` | 关联人物画像 |
| GET | `/findings/{fid}/notes` | 关联笔记 |
| POST | `/findings/{fid}/generate-copywriting` | 按需生成话术 |

### POST /projects/{id}/findings

```json
{
  "project_id": "660a...",
  "page": 1, "page_size": 10,
  "source": "", "task_id": "", "type": "",
  "min_score": 0, "sort": "score_desc",
  "include_safe": false
}
```

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `source` | string | "" | `web_tagging` / `xhs` / `douyin` |
| `task_id` | string | "" | 按任务过滤 |
| `type` | string | "" | `hr_contact` / `business_contact` / `customer_service` / `personal_info` / `other` |
| `min_score` | int | 0 | 最低关注度 |
| `sort` | string | `score_desc` | `score_desc` / `score_asc` / `time_desc` |
| `include_safe` | bool | false | 附带无风险 URL 列表 |

---

## 5. 项目看板 + 聚合 API（新增）

前端做看板、图表、关联展示的核心接口。

| 方法 | 路径 | 说明 | 用途 |
|------|------|------|------|
| GET | `/projects/{id}/dashboard` | 综合看板 | 项目首页，一次拿全 |
| GET | `/projects/{id}/timeline` | 项目时间线 | 活动流 |
| GET | `/projects/{id}/score-distribution` | 分数分布 | 直方图 |
| GET | `/projects/{id}/source-breakdown` | 数据源分布 | 饼图/柱状图 |
| GET | `/projects/{id}/type-breakdown` | 类型分布 | 饼图/柱状图 |
| GET | `/projects/{id}/high-value-targets` | 高价值目标 | 优先级列表 |
| GET | `/projects/{id}/copywriting-coverage` | 话术覆盖率 | 进度条 |

### GET /projects/{id}/dashboard

一次请求拿到项目所有看板数据，避免前端多次请求。

```json
{
  "findings": {
    "total": 42,
    "by_source": { "web_tagging": 28, "xhs": 12, "douyin": 2 },
    "by_type": { "hr_contact": 8, "business_contact": 6, "customer_service": 10 },
    "score_distribution": { "high": 15, "medium": 18, "low": 9 }
  },
  "tasks": {
    "total": 5,
    "by_status": { "completed": 3, "running": 1, "error": 1 }
  },
  "data_counts": {
    "xhs_notes": 120,
    "xhs_profiles": 15,
    "web_tagging": 8,
    "douyin_search": 200,
    "douyin_tagged": 30,
    "douyin_profiles": 10,
    "copywritings": 25
  },
  "top_findings": [
    { "finding_id": "a1b2c3", "source": "xhs", "type": "personal_info", "label": "疑似百度员工", "attention_score": 95 }
  ],
  "safe_count": 3,
  "token_usage": { "total_tokens": 12345, "total_cost": 1.23 }
}
```

### GET /projects/{id}/timeline?limit=50

按时间倒序聚合所有活动（任务、发现、笔记、画像）。

```json
{
  "events": [
    { "type": "finding", "id": "a1b2c3", "label": "疑似百度员工", "source": "xhs", "score": 95, "time": "2026-03-30T10:00:00Z" },
    { "type": "task", "id": "t1", "label": "company_scan 任务", "status": "completed", "time": "2026-03-30T09:50:00Z" },
    { "type": "xhs_note", "id": "note_1", "label": "百度实习第一天", "time": "2026-03-30T09:45:00Z" },
    { "type": "xhs_profile", "id": "user_1", "label": "小明在百度", "score": 75, "time": "2026-03-30T09:40:00Z" }
  ]
}
```

| type 值 | 说明 | 额外字段 |
|---------|------|---------|
| `task` | 任务 | `status` |
| `finding` | 发现 | `source`, `score` |
| `xhs_note` | 小红书笔记 | — |
| `xhs_profile` | 小红书画像 | `score` |

### GET /projects/{id}/score-distribution?source=

关注度分数直方图数据，10 分一档。

```json
{
  "bins": [
    { "min": 0, "count": 2 },
    { "min": 10, "count": 3 },
    { "min": 20, "count": 5 },
    { "min": 70, "count": 12 },
    { "min": 80, "count": 8 },
    { "min": 90, "count": 5 }
  ],
  "source": "all"
}
```

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `source` | string | "" | 按数据源过滤，空=全部 |

### GET /projects/{id}/source-breakdown

各数据源的 findings 统计。

```json
{
  "sources": [
    { "source": "web_tagging", "count": 28, "avg_score": 62.3, "max_score": 95, "min_score": 15 },
    { "source": "xhs", "count": 12, "avg_score": 71.5, "max_score": 92, "min_score": 40 },
    { "source": "douyin", "count": 2, "avg_score": 55.0, "max_score": 60, "min_score": 50 }
  ]
}
```

### GET /projects/{id}/type-breakdown?source=

各发现类型的统计，可按 source 过滤。

```json
{
  "types": [
    { "type": "personal_info", "count": 12, "avg_score": 72.1, "max_score": 95 },
    { "type": "hr_contact", "count": 8, "avg_score": 65.3, "max_score": 88 },
    { "type": "customer_service", "count": 10, "avg_score": 45.2, "max_score": 70 }
  ],
  "source": "all"
}
```

### GET /projects/{id}/high-value-targets?min_score=60&limit=20

高分 findings + 关联状态（是否有画像、是否有话术），方便前端做优先级排序。

```json
{
  "items": [
    {
      "finding_id": "a1b2c3",
      "source": "xhs",
      "type": "personal_info",
      "label": "疑似百度员工",
      "attention_score": 95,
      "has_copywriting": true,
      "has_profile": true
    },
    {
      "finding_id": "d4e5f6",
      "source": "web_tagging",
      "type": "hr_contact",
      "label": "招聘邮箱",
      "attention_score": 80,
      "has_copywriting": false,
      "has_profile": false
    }
  ],
  "total": 15,
  "min_score": 60
}
```

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `min_score` | int | 60 | 最低分数阈值 |
| `limit` | int | 20 | 返回条数 |

### GET /projects/{id}/copywriting-coverage

话术生成进度，用于进度条展示。

```json
{
  "total_findings": 42,
  "total_copywritings": 25,
  "coverage_rate": 59.5,
  "high_score": {
    "total": 15,
    "covered": 12,
    "uncovered": 3,
    "coverage_rate": 80.0
  }
}
```

`high_score` 单独统计 attention_score ≥ 60 的 findings 覆盖情况。

---

## 6. 项目下的原始数据

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/projects/{id}/notes` | 小红书笔记列表 |
| POST | `/projects/{id}/profiles` | 人物画像列表 |
| POST | `/projects/{id}/web-tagging` | 官网打标结果列表 |

### POST /projects/{id}/notes

```json
{ "project_id": "660a...", "page": 1, "page_size": 10, "task_id": "", "is_suspicious": null, "sort_by": "relevance" }
```

| 字段 | 说明 |
|------|------|
| `task_id` | 按任务过滤 |
| `is_suspicious` | 是否可疑（null=全部） |
| `sort_by` | `relevance`（关联度）/ `created_at`（时间） |

### POST /projects/{id}/profiles

```json
{ "project_id": "660a...", "page": 1, "page_size": 10, "min_score": 0, "sort": "score_desc" }
```

画像响应中包含 `finding_id`，前端可直接用它查话术：
```json
{
  "items": [
    {
      "user_id": "user_xxx",
      "nickname": "小明在百度",
      "finding_id": "a1b2c3d4e5f6",
      "attention_score": 75
    }
  ],
  "total": 12, "page": 1, "page_size": 10
}
```

前端拿到 `finding_id` 后：
- 查话术：`GET /findings/{finding_id}/copywriting`
- 生成话术：`POST /findings/{finding_id}/generate-copywriting`
- 查 finding 详情：`GET /findings/{finding_id}`

### POST /projects/{id}/web-tagging

```json
{ "project_id": "660a...", "page": 1, "page_size": 10 }
```

---

## 6.5 画像 → 话术 快捷查询

当画像没有 `finding_id`（历史数据）时，可通过以下 API 反查。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/profiles/xhs/{user_id}/finding?project_id=` | XHS 画像反查 finding |
| GET | `/profiles/douyin/{sec_uid}/finding?project_id=` | 抖音画像反查 finding |
| GET | `/profiles/{user_id}/copywriting?source=xhs&project_id=` | 画像直接查话术 |

响应中附带 `has_copywriting: true/false`，前端据此决定显示"查看话术"还是"生成话术"。

---

## 7. 小红书（数据源操作）

列表查询统一走项目维度（第 6 节），这里只有 Cookie 管理和单条详情。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/xhs/cookies` | 添加账号 |
| GET | `/xhs/cookies` | 列出账号 |
| GET | `/xhs/cookies/{name}` | 获取账号 |
| GET | `/xhs/cookies/{name}/detail` | 账号详情（含 Cookie） |
| PUT | `/xhs/cookies/{name}` | 更新账号 |
| DELETE | `/xhs/cookies/{name}` | 删除账号 |
| POST | `/xhs/cookies/{name}/verify` | 验证 Cookie |
| POST | `/xhs/cookies/{name}/activate` | 激活账号 |
| POST | `/xhs/search` | 创建搜索任务 |
| GET | `/xhs/tasks/{task_id}` | 搜索任务状态 |
| GET | `/xhs/notes/{note_id}` | 笔记详情 |
| GET | `/xhs/notes/{note_id}/detail` | 笔记详情分析 |
| GET | `/xhs/profiles/{profile_id}` | 画像详情 |
| DELETE | `/xhs/profiles/{profile_id}` | 删除画像 |
| POST | `/xhs/vision-analysis/stream` | 视觉分析（SSE） |
| POST | `/xhs/profile/generate/stream` | 画像生成（SSE） |
| POST | `/xhs/sse/cancel/{task_id}` | 取消 SSE 任务 |

---

## 8. 抖音（数据源操作）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/douyin/cookies` | 添加账号 |
| GET | `/douyin/cookies` | 列出账号 |
| GET | `/douyin/cookies/{name}` | 获取账号 |
| GET | `/douyin/cookies/{name}/detail` | 账号详情（含 Cookie） |
| PUT | `/douyin/cookies/{name}` | 更新账号 |
| DELETE | `/douyin/cookies/{name}` | 删除账号 |
| POST | `/douyin/cookies/{name}/verify` | 验证 Cookie |
| POST | `/douyin/cookies/{name}/activate` | 激活账号 |
| POST | `/douyin/{pid}/search-results` | 搜索结果列表（分页） |
| GET | `/douyin/{pid}/search-results/{aweme_id}` | 单条搜索结果 |
| POST | `/douyin/{pid}/tagged-results` | 打标结果列表（分页） |
| GET | `/douyin/{pid}/tagged-results/stats` | 打标统计 |
| GET | `/douyin/{pid}/potential-users` | 潜在用户列表 |
| POST | `/douyin/{pid}/profiles` | 画像列表（分页） |
| GET | `/douyin/profiles/{profile_id}` | 画像详情 |
| DELETE | `/douyin/profiles/{profile_id}` | 删除画像 |
| POST | `/douyin/{pid}/pipeline` | 运行流水线 |
| POST | `/douyin/{pid}/pipeline/stream` | 流水线（SSE） |
| POST | `/douyin/screenshot/stream` | 截图（SSE） |
| POST | `/douyin/vision-analysis/stream` | 视觉分析（SSE） |
| POST | `/douyin/profile/generate/stream` | 画像生成（SSE） |
| POST | `/douyin/sse/cancel/{task_id}` | 取消 SSE 任务 |

### POST /douyin/{pid}/tagged-results

```json
{ "project_id": "660a...", "page": 1, "page_size": 10, "tag": null }
```

响应额外字段 `stats`：
```json
{ "stats": { "total": 30, "potential_employee": 12, "marketing": 10, "uncertain": 8 } }
```

---

## 9. 观测层

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/stats/global` | 全局统计 + 项目列表摘要 |
| GET | `/stats/project/{id}` | 项目统计 + 任务列表摘要 |
| GET | `/stats/task/{task_id}` | 任务统计 + Agent 明细 |
| GET | `/stats/records` | 原始调用记录（admin） |

---

## 10. AI 工具

### 百炼 AIGC

前端“AI 工具”页使用这些接口，配置来源为 `/config/sections/bailian`。完整字段见后端 `docs/BAILIAN_AIGC_API.md`。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/aigc/config` | 百炼配置状态，返回模型名和是否已配置 Key/Workspace |
| POST | `/aigc/images/qwen-edit` | Qwen 图片指令编辑，同步返回结果图 |
| POST | `/aigc/images/wanx-edit` | 万相图片编辑，异步返回 `task_id` |
| POST | `/aigc/videos/text-to-video` | 万相 2.7 文生视频，异步返回 `task_id` |
| POST | `/aigc/videos/image-to-video` | 万相 2.7 图生视频，支持 `media` 数组、首尾帧和驱动音频 |
| GET | `/aigc/tasks/{task_id}?protocol=workspace` | 轮询图片/视频异步任务 |

常用请求体：

```json
// POST /aigc/images/qwen-edit
{
  "images": ["https://example.com/input.png"],
  "prompt": "把背景改成清晨的办公室，保留主体人物。",
  "parameters": { "n": 1, "watermark": false, "prompt_extend": true }
}
```

```json
// POST /aigc/videos/image-to-video
{
  "prompt": "产品照片变成 5 秒展示视频，镜头缓慢环绕。",
  "media": [
    { "type": "first_frame", "url": "https://example.com/product.png" }
  ],
  "parameters": { "resolution": "720P", "duration": 5, "prompt_extend": true }
}
```

---

## 11. 系统管理

### 配置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/config` | 获取所有配置，返回脱敏值 |
| GET/POST/DELETE | `/config/llm` | LLM 配置 |
| GET | `/config/tools` | 工具配置列表 |
| GET/POST/DELETE | `/config/tools/{name}` | 单个工具配置 |
| GET/POST/DELETE | `/config/langsmith` | LangSmith 配置 |
| GET/POST/DELETE | `/config/langfuse` | Langfuse 配置 |
| GET | `/config/dingtalk` | 钉钉机器人列表 |
| GET/POST/DELETE | `/config/dingtalk/{bot}` | 钉钉配置 |
| POST | `/config/dingtalk/{bot}/toggle` | 开关钉钉机器人 |
| POST | `/config/dingtalk/{bot}/test` | 测试钉钉机器人 |
| GET/POST | `/config/sections/{category}` | 通用配置段，MongoDB 加密存储 |
| POST | `/config/import` | 旧 config.json 导入入口已下线，返回 410 |

> 前端配置页应统一使用 `/config` 与 `/config/sections/{category}` 写入配置；后端读取 MongoDB 加密配置并返回脱敏值，不再从本地 `config.json` 导入。

### 用户管理（admin）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/auth/users` | 用户列表 |
| POST | `/auth/users` | 创建用户 |
| PUT | `/auth/users/{username}` | 更新用户 |
| DELETE | `/auth/users/{username}` | 删除用户 |
| POST | `/auth/users/{username}/reset-password` | 重置密码 |
| POST | `/auth/change-login-key` | 修改登录密钥 |

### 浏览器 / Skills

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/browser/pool/status` | 容器池状态 |
| GET | `/browser/pool/config` | 容器池配置 |
| POST | `/browser/pool/shutdown` | 关闭所有容器 |
| GET | `/skills` | 列出所有 skills |
| GET | `/skills/{skill_id}` | skill 详情 |

---

## 前端页面结构建议

```
项目列表 (POST /projects/list)
  └── 项目详情
        │
        ├── 📊 看板 (GET /projects/{id}/dashboard)
        │     ├── 数据源饼图 ← findings.by_source
        │     ├── 分数直方图 ← GET /projects/{id}/score-distribution
        │     ├── 任务状态 ← tasks.by_status
        │     ├── 数据计数卡片 ← data_counts
        │     ├── Top10 高分发现 ← top_findings
        │     ├── 话术覆盖进度 ← GET /projects/{id}/copywriting-coverage
        │     └── Token 消耗 ← token_usage
        │
        ├── 📋 任务管理 (POST /projects/{id}/tasks/list)
        │     ├── 下发任务 (POST /projects/{id}/tasks)
        │     └── 轮询状态 (GET /projects/{id}/tasks/{tid})
        │
        ├── 🔍 Findings (POST /projects/{id}/findings)
        │     ├── 筛选: source / type / min_score / sort
        │     ├── [web_tagging] 🌐 → 话术 (GET /findings/{fid}/copywriting)
        │     ├── [xhs] 📕 → 画像 + 笔记 + 话术
        │     └── [douyin] 🎵 → 画像 + 话术
        │
        ├── 🎯 高价值目标 (GET /projects/{id}/high-value-targets)
        │     └── 按分数排序，标记话术/画像状态
        │
        ├── 📈 分析图表
        │     ├── 数据源分布 (GET /projects/{id}/source-breakdown)
        │     └── 类型分布 (GET /projects/{id}/type-breakdown)
        │
        ├── 🧰 AI 工具
        │     ├── 百炼图片编辑 (POST /aigc/images/qwen-edit, /aigc/images/wanx-edit)
        │     ├── 百炼视频生成 (POST /aigc/videos/text-to-video, /aigc/videos/image-to-video)
        │     └── 任务轮询 (GET /aigc/tasks/{task_id})
        │
        ├── 🕐 时间线 (GET /projects/{id}/timeline)
        │
        └── 📦 原始数据
              ├── 笔记 (POST /projects/{id}/notes) → 详情 (GET /xhs/notes/{id}/detail)
              ├── 画像 (POST /projects/{id}/profiles) → 详情 (GET /xhs/profiles/{id})
              └── 打标 (POST /projects/{id}/web-tagging)
```
