# 核心 API 文档

Base URL: `/api/v1`  
认证: 所有接口需 Bearer Token（`Authorization: Bearer <token>`）  
标注 `[admin]` 的接口仅 admin 角色可访问

---

## 数据架构

```
项目 (project_id)
  ├── 任务 (tasks)              ← 统一任务层，所有类型
  ├── Findings (web_tagging_results) ← 统一数据层，所有来源
  │     每个 URL 一条记录，内含 findings[]
  │     每个 finding 有唯一 finding_id
  └── 话术 (url_scan_copywritings)  ← 按 finding_id 关联
```

检索链路: `project_id → web_tagging_results → finding_id → copywriting`

---

## 1. 认证

### POST /auth/login

```json
// 请求
{ "username": "admin", "password": "xxx" }
// 响应
{ "access_token": "eyJ...", "token_type": "bearer" }
```

---

## 2. 任务下发

### POST /tasks/create

```json
// 请求
{
  "project_id": "69c530f7aa48d63d0ff776cf",
  "task_type": "url_scan",
  "note": "第一批教育类网站",
  "params": {
    "urls": ["https://example.com", "target.cn"],
    "min_attention_score": 40
  }
}
// 响应
{ "task_id": "e5d03076fcbf", "task_type": "url_scan", "status": "pending" }
```

| task_type | params |
|-----------|--------|
| `url_scan` | `urls: string[]` 或 `url_text: string`, `min_attention_score: int` |
| `xhs_search` | `keyword: string`, `max_notes: int`, `attention_threshold: int` |
| `douyin_search` | `keyword: string`, `max_videos: int`, `publish_time: int` |
| `web_tagging` | `company_name: string`, `max_urls: int` |

### POST /tasks/upload

multipart/form-data，带文件上传。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | File | 是 | 上传文件（如 url.txt） |
| `project_id` | string | 是 | 项目 ID |
| `task_type` | string | 是 | 任务类型 |
| `params_json` | string | 否 | 其他参数 JSON |

响应同上。

---

## 3. 任务管理

### GET /tasks?project_id=xxx&task_type=

列出项目下所有任务（按创建时间倒序）。

```json
{
  "tasks": [
    {
      "task_id": "e5d03076fcbf",
      "project_id": "69c530f7...",
      "task_type": "url_scan",
      "note": "第一批教育类网站",
      "status": "completed",
      "progress": {
        "total_urls": 10,
        "alive_urls": 7,
        "scanned_urls": 5,
        "total_findings": 12,
        "total_copywritings": 8
      },
      "elapsed_ms": 125000,
      "error": null,
      "created_at": "2026-03-27T02:30:00",
      "updated_at": "2026-03-27T02:32:05"
    }
  ]
}
```

**status 枚举**:

| status | 说明 | 前端颜色 |
|--------|------|---------|
| `pending` | 等待执行 | 灰色 |
| `running` | 执行中 | 蓝色 |
| `completed` | 完成 | 绿色 |
| `error` | 失败 | 红色 |

### GET /tasks/{task_id}

获取单个任务详情（轮询用）。响应同上单条。

### DELETE /tasks/{task_id}

删除单个任务及关联数据（web_tagging_results + copywritings）。

```json
// 响应
{
  "task_id": "e5d03076fcbf",
  "deleted": true,
  "deleted_findings": 5,
  "deleted_copywritings": 3
}
```

### DELETE /tasks?project_id=xxx&task_type=&status=

批量删除。

```json
// 响应
{ "deleted_count": 3, "task_ids": ["id1", "id2", "id3"] }
```

---

## 4. Findings（项目主页面）

### GET /projects/{project_id}/web-tagging?limit=50&skip=0

项目主页面核心接口。返回所有来源的扫描结果，每条是一个 URL。

```json
[
  {
    "id": "69c59489be58...",
    "project_id": "69c530f7...",
    "url": "https://example.com",
    "task_id": "e5d03076fcbf",
    "created_at": "2026-03-27T02:30:00",
    "data": {
      "intro": {
        "url": "https://example.com",
        "final_url": "https://example.com",
        "domain": "example.com",
        "site_name": "示例公司",
        "entity_name": "示例科技有限公司",
        "summary": "企业官网，提供XX服务"
      },
      "has_findings": true,
      "no_findings_reason": null,
      "findings": [
        {
          "finding_id": "c160cd4d8eb4",
          "task_id": "e5d03076fcbf",
          "project_id": "69c530f7...",
          "url": "https://example.com",
          "type": "hr_contact",
          "scope": "official",
          "channel": "email",
          "role": "hr",
          "subtype": "resume_email",
          "label": "简历投递",
          "value": "hr@example.com",
          "context": "首页 Footer > 联系我们 > 招聘合作模块",
          "source_url": "https://example.com/contact",
          "evidence": "页面显示：简历投递 hr@example.com",
          "attention_score": 80,
          "attention_reason": "可直接触达的招聘渠道"
        }
      ]
    }
  }
]
```

前端展示逻辑：
- 遍历数组，每条记录展开 `data.findings[]`
- 按 `attention_score` 降序排列所有 findings
- `has_findings=false` 的记录默认折叠，底部显示"N 个无风险站点"
- 每个 finding 的 `finding_id` 用于获取话术

---

## 5. 话术

### GET /findings/{finding_id}/copywriting

获取话术。不存在返回 404。

```json
{
  "finding_id": "c160cd4d8eb4",
  "url": "https://example.com",
  "finding_type": "hr_contact",
  "finding_channel": "email",
  "finding_label": "简历投递",
  "finding_value": "hr@example.com",
  "scenario": {
    "scenario_name": "猎头推荐高薪岗位",
    "target_background": "...",
    "faked_identity": { "name": "张明", "company": "锐才猎头", "position": "高级猎头顾问" },
    "logic_chain": [
      { "step": 1, "channel": "email", "action": "发送候选人推荐邮件", "fallback": "电话跟进" }
    ]
  },
  "scripts": [
    {
      "channel": "email",
      "dialogue": [],
      "email_template": { "from": "...", "subject": "...", "body": "...", "signature": "..." },
      "key_points": ["发件人域名要可信"]
    },
    {
      "channel": "wechat",
      "dialogue": [
        { "role": "attacker", "content": "您好，我是锐才猎头的张明...", "tactic": "互惠原则" },
        { "role": "target", "content": "什么机会？", "tactic": null }
      ],
      "key_points": ["好友验证消息建议"]
    }
  ],
  "payload": {
    "archive_name": "候选人简历汇总_2026Q1.zip",
    "exe_name": "简历汇总表.pdf.exe",
    "icon_disguise": "PDF图标",
    "password": "hr2026"
  },
  "objections": [
    { "objection": "你怎么知道我邮箱的？", "response": "贵公司官网招聘页面公开的", "tactic": "合理化" }
  ],
  "loaded_skills": ["base-scenario", "email", "wechat"],
  "status": "completed"
}
```

### POST /findings/{finding_id}/generate-copywriting

按需生成话术。已存在直接返回完整话术，不存在则后台生成。

```json
// 响应（生成中）
{ "finding_id": "c160cd4d8eb4", "status": "generating", "message": "话术生成中，请稍后查询" }
```

前端流程: 点击 finding → POST generate → 返回话术则展示，返回 `generating` 则轮询 GET（5s 间隔）。

---

## 6. 观测统计

### GET /stats/global

全局汇总 + 项目列表摘要。

```json
{
  "global": {
    "total_calls": 100,
    "total_input_tokens": 500000,
    "total_output_tokens": 20000,
    "total_tokens": 520000,
    "total_cost_yuan": 5.2,
    "total_duration_ms": 300000,
    "by_model": { "qwen-max": { "calls": 100, "total_tokens": 520000, "cost_yuan": 5.2 } }
  },
  "projects": [
    { "project_id": "69c530f7...", "total_calls": 50, "total_tokens": 260000, "total_cost_yuan": 2.6 }
  ]
}
```

### GET /stats/project/{project_id}

项目汇总 + 任务列表摘要。

```json
{
  "stats": {
    "total_calls": 20,
    "total_tokens": 80000,
    "total_cost_yuan": 0.8,
    "total_duration_ms": 60000,
    "by_model": { "qwen-max": { "calls": 20, "total_tokens": 80000, "cost_yuan": 0.8 } }
  },
  "tasks": [
    { "task_id": "task_001", "total_calls": 10, "total_tokens": 40000, "total_cost_yuan": 0.4 },
    { "task_id": "task_002", "total_calls": 10, "total_tokens": 40000, "total_cost_yuan": 0.4 }
  ]
}
```

### GET /stats/task/{task_id}

任务汇总 + 各 Agent 明细。

```json
{
  "stats": {
    "total_calls": 10,
    "total_tokens": 40000,
    "total_cost_yuan": 0.4,
    "total_duration_ms": 25000,
    "by_model": { "qwen-max": { "calls": 10, "total_tokens": 40000, "cost_yuan": 0.4 } }
  },
  "agents": [
    { "agent": "web_tagging", "total_calls": 3, "total_tokens": 12000, "total_cost_yuan": 0.13, "total_duration_ms": 5200 },
    { "agent": "copywriting", "total_calls": 7, "total_tokens": 28000, "total_cost_yuan": 0.27, "total_duration_ms": 18000 }
  ]
}
```

### GET /stats/records?project_id=&task_id=&limit=50 `[admin]`

原始 LLM 调用记录（调试用，仅 admin）。

```json
{
  "records": [
    {
      "model": "qwen-max",
      "input_tokens": 3500,
      "output_tokens": 200,
      "cost_yuan": 0.015,
      "duration_ms": 2500,
      "project_id": "69c530f7...",
      "task_id": "e5d03076fcbf",
      "phase": "scan",
      "agent": "web_tagging",
      "timestamp": 1774549804.5
    }
  ]
}
```

---

## 7. Skills

### GET /skills

列出所有可用 skills。

```json
{
  "skills": [
    {
      "id": "wechat",
      "name": "微信话术",
      "description": "微信渠道的完整社工话术...",
      "category": "wechat",
      "phases": ["scenario", "script", "objection"],
      "tags": ["微信", "即时通讯"],
      "priority": 1,
      "enabled": true
    }
  ],
  "summary": {
    "total": 13,
    "by_phase": { "scenario": 8, "script": 10, "objection": 7, "finalize": 1 },
    "by_category": { "general": 3, "wechat": 1, "email": 1 }
  }
}
```

### GET /skills/{skill_id}

获取单个 skill 完整内容。

---

## 8. 前端同步策略

| 场景 | 方式 | 接口 | 间隔 |
|------|------|------|------|
| 任务列表 | 轮询 | `GET /tasks?project_id=xxx` | 5s |
| 任务状态 | 轮询 | `GET /tasks/{task_id}` | 3s |
| 话术生成 | 轮询 | `GET /findings/{finding_id}/copywriting` | 5s |
| 项目数据 | 首次加载 + 任务完成后刷新 | `GET /projects/{project_id}/web-tagging` | - |

---

## API 总览

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| POST | `/auth/login` | 登录 | 公开 |
| POST | `/tasks/create` | 下发任务 | 登录 |
| POST | `/tasks/upload` | 带文件下发任务 | 登录 |
| GET | `/tasks` | 任务列表 | 登录 |
| GET | `/tasks/{task_id}` | 任务详情 | 登录 |
| DELETE | `/tasks/{task_id}` | 删除任务 | 登录 |
| DELETE | `/tasks` | 批量删除 | 登录 |
| GET | `/projects/{project_id}/web-tagging` | 项目 findings | 登录 |
| GET | `/findings/{finding_id}/copywriting` | 获取话术 | 登录 |
| POST | `/findings/{finding_id}/generate-copywriting` | 按需生成话术 | 登录 |
| GET | `/stats/global` | 全局统计 | 登录 |
| GET | `/stats/project/{project_id}` | 项目统计 | 登录 |
| GET | `/stats/task/{task_id}` | 任务统计 | 登录 |
| GET | `/stats/records` | 原始调用记录 | admin |
| GET | `/skills` | Skills 列表 | 登录 |
| GET | `/skills/{skill_id}` | Skill 详情 | 登录 |
