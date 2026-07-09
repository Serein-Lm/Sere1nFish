# URL 扫描 + 话术生成 API 文档

## 概述

上传 url.txt → URL 标准化 → 探活 → Agent 扫描网站 → 提取信息节点 → 每个节点生成话术 → JSON 存储

Base URL: `/api/v1`

## 下发任务

### 方式 1: JSON 提交

`POST /api/v1/tasks/create`

```json
{
  "project_id": "proj_001",
  "task_type": "url_scan",
  "params": {
    "urls": ["https://example.com", "https://target.cn", "target2.com"],
    "min_attention_score": 40
  }
}
```

或传文本内容：

```json
{
  "project_id": "proj_001",
  "task_type": "url_scan",
  "params": {
    "url_text": "https://example.com\nhttps://target.cn\ntarget2.com",
    "min_attention_score": 40
  }
}
```

`urls` 和 `url_text` 二选一，`urls` 优先。

**响应**:
```json
{"task_id": "a1b2c3d4e5f6", "task_type": "url_scan", "status": "pending"}
```

### 方式 2: 上传文件

`POST /api/v1/tasks/upload`（multipart/form-data）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | File | 是 | .txt 文件，每行一个 URL |
| `project_id` | string | 是 | 项目 ID |
| `task_type` | string | 是 | 固定 `url_scan` |
| `params_json` | string | 否 | 其他参数 JSON（如 `{"min_attention_score": 40}`） |

**响应**: 同上

## 查询端点

### GET /api/v1/tasks?project_id=xxx&task_type=url_scan

列出某项目的任务。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_id` | string | 是 | 项目 ID |
| `task_type` | string | 否 | 按任务类型过滤 |

### GET /api/v1/tasks/{task_id}

获取任务状态（轮询用）。

### GET /api/v1/tasks/{task_id}/findings?include_safe=false

获取任务的信息节点。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `include_safe` | bool | false | 是否返回无风险 URL 列表 |

**响应**:
```json
{
  "findings": [
    {
      "finding_id": "a1b2c3d4e5f6",
      "url": "https://example.com",
      "type": "hr_contact",
      "attention_score": 80,
      "...": "..."
    }
  ],
  "safe_count": 3,
  "safe_urls": ["https://safe1.com", "https://safe2.com", "https://safe3.com"]
}
```

- `findings`: 有风险的 findings，按 `attention_score` 降序排列
- `safe_count`: 无风险 URL 数量（始终返回）
- `safe_urls`: 仅当 `include_safe=true` 时返回

前端建议：
- 默认展示 findings 列表（有风险的）
- 底部显示"N 个无风险站点"折叠区域
- 用户点击展开时，带 `include_safe=true` 重新请求

### GET /api/v1/findings/{finding_id}/copywriting

获取单个信息节点的话术。

### DELETE /api/v1/tasks/{task_id}

删除单个任务及其关联数据（findings、copywritings、scan_results）。

**响应**:
```json
{
  "task_id": "a1b2c3d4e5f6",
  "deleted": true,
  "deleted_findings": 5,
  "deleted_copywritings": 3
}
```

### DELETE /api/v1/tasks?project_id=xxx&task_type=&status=

批量删除任务。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_id` | string | 是 | 项目 ID |
| `task_type` | string | 否 | 按任务类型过滤 |
| `status` | string | 否 | 按状态过滤（如 `error`、`completed`） |

**响应**:
```json
{
  "deleted_count": 3,
  "task_ids": ["id1", "id2", "id3"]
}
```

## 观测层端点

### GET /api/v1/stats/global — 全局统计
### GET /api/v1/stats/project/{project_id} — 项目级统计
### GET /api/v1/stats/task/{task_id} — 任务级统计
### GET /api/v1/stats/hierarchy?project_id= — 层级视图（看板用）
### GET /api/v1/stats/records?project_id=&task_id=&limit=50 — 原始记录

## Skills 端点

### GET /api/v1/skills — 列出所有 skills（Layer 1 索引）
### GET /api/v1/skills/{skill_id} — 获取 skill 完整内容

## 数据模型

### UrlScanTask — 任务状态

```json
{
  "task_id": "string",
  "project_id": "string",
  "total_urls": 10,
  "alive_urls": 7,
  "scanned_urls": 5,
  "total_findings": 12,
  "total_copywritings": 8,
  "status": "pending | probing | scanning | generating | completed | error",
  "error": null,
  "updated_at": "2026-03-26T12:00:00"
}
```

### InfoFinding — 信息节点（一个 URL 可产出多个）

```json
{
  "finding_id": "a1b2c3d4e5f6",
  "url": "https://example.com",
  "domain": "example.com",
  "site_name": "示例公司",
  "entity_name": "示例科技有限公司",
  "summary": "企业官网，提供XX服务",
  "type": "hr_contact | business_contact | customer_service | tech_support | social_media | download | form | other",
  "channel": "email | phone | wechat | qq | form | app | other",
  "role": "hr | sales | support | admin | developer | unknown",
  "label": "简历投递",
  "value": "hr@example.com",
  "context": "首页 Footer > 联系我们 > 招聘合作模块",
  "evidence": "页面显示：简历投递 hr@example.com",
  "attention_score": 80,
  "attention_reason": "可直接触达的招聘渠道"
}
```

### FindingCopywriting — 话术（每个信息节点独立生成）

前端根据 `finding_channel` 和 `scripts[].channel` 渲染不同 UI 组件。

```json
{
  "finding_id": "a1b2c3d4e5f6",
  "url": "https://example.com",
  "finding_type": "hr_contact",
  "finding_channel": "email",
  "finding_label": "简历投递",
  "finding_value": "hr@example.com",
  "scenario": {
    "scenario_name": "猎头推荐高薪岗位",
    "target_background": "...",
    "scenario_overview": "...",
    "faked_identity": {
      "name": "张明",
      "company": "锐才猎头",
      "company_desc": "...",
      "position": "高级猎头顾问",
      "background": "...",
      "personality": "..."
    },
    "logic_chain": [
      {"step": 1, "channel": "email", "action": "发送候选人推荐邮件", "fallback": "电话跟进"},
      {"step": 2, "channel": "phone", "action": "电话确认收到", "fallback": null},
      {"step": 3, "channel": "wechat", "action": "微信发送简历压缩包", "fallback": "邮件发送"}
    ],
    "risk_notes": "..."
  },
  "scripts": [
    {
      "channel": "email",
      "dialogue": [],
      "email_template": "发件人: 张明 <zhangming@ruicai.com>\n主题: ...\n\n正文...",
      "key_points": ["发件人域名要可信", "主题包含岗位名"]
    },
    {
      "channel": "wechat",
      "dialogue": [
        {"role": "attacker", "content": "您好，我是锐才猎头的张明...", "tactic": "互惠原则"},
        {"role": "target", "content": "什么机会？", "tactic": null}
      ],
      "email_template": null,
      "key_points": ["好友验证消息建议", "备注名建议"]
    },
    {
      "channel": "phone",
      "dialogue": [
        {"role": "attacker", "content": "喂，您好，请问是XX公司的李总吗？", "tactic": "确认身份"},
        {"role": "target", "content": "是的，你是？", "tactic": null}
      ],
      "email_template": null,
      "key_points": ["语气专业冷静", "30秒内建立身份"]
    }
  ],
  "payload": {
    "archive_name": "候选人简历汇总_2026Q1.zip",
    "exe_name": "简历汇总表.pdf.exe",
    "icon_disguise": "PDF图标",
    "compression_method": "zip_double",
    "password": "hr2026",
    "notes": "第一层包含真实PDF和伪装exe"
  },
  "objections": [
    {
      "objection": "你怎么知道我邮箱的？",
      "response": "您的邮箱在贵公司官网招聘页面公开的",
      "tactic": "合理化",
      "context_note": "目标邮箱来源于官网公开信息，可直接说明"
    }
  ],
  "target_analysis": "目标为HR，对简历文件有天然打开习惯...",
  "psychology_strategy": "核心策略：互惠原则（提供高薪岗位信息）+ 紧迫感（HC即将关闭）",
  "case_reference": "参考案例：猎头推荐（recruitment-cases.md）",
  "loaded_skills": ["base-scenario", "email", "wechat", "phone", "recruitment", "payload"],
  "status": "completed",
  "error": null
}
```

## 前端渲染指南

### 渠道类型 → UI 组件映射

| `scripts[].channel` | 渲染方式 |
|---------------------|---------|
| `wechat` | 微信聊天气泡（绿色/白色，区分 attacker/target） |
| `email` | 邮件卡片（发件人/主题/正文/签名） |
| `phone` | 电话对话（📞/📱 图标区分双方） |
| `sms` | 短信气泡 |
| `intranet` | 内部通知卡片 |

### 心理策略标签

每条 `dialogue[].tactic` 和 `objections[].tactic` 都是心理策略名称，前端可渲染为彩色标签：

| tactic | 颜色建议 |
|--------|---------|
| 互惠原则 | 蓝色 |
| 权威效应 | 紫色 |
| 紧迫感 | 红色 |
| 社会认同 | 绿色 |
| 虚荣心 | 金色 |
| 合理化 | 灰色 |

### 层级结构

```
项目
  └── 任务（UrlScanTask）
        └── URL 扫描结果（UrlScanResult）
              └── 信息节点（InfoFinding）× N
                    └── 话术（FindingCopywriting）× 1
```
