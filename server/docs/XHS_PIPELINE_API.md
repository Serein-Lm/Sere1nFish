# 小红书社工信息采集 API 文档

## 概述

本模块提供小红书社工信息采集的完整 API 接口，包括账号管理、搜索任务、笔记查询和人物画像生成。

**核心特性**：
- 多账号 Cookie 管理（激活/切换/验证）
- 基于关键词的智能搜索与打标
- 三层 AI 分析：搜索结果 → 笔记详情 → 人物画像
- **关键词关联度**作为核心评估维度
- **公司归属识别**贯穿全流程

---

## 账号（Cookie）管理 API

### Cookie 激活机制说明

系统支持存储多个小红书账号的 Cookie，但**同一时间只能有一个账号处于激活状态**。

- **激活 (activate)**: 将某个账号设为当前使用的账号，爬取操作使用该账号的 Cookie
- **切换账号**: 激活新账号时，旧账号自动变为非激活状态
- **用途**: 支持多账号轮换，避免单账号频繁请求被风控

---

### 添加账号

```http
POST /api/v1/xhs/cookies
Content-Type: application/json

{
    "account_name": "work_account_1",
    "cookie_string": "a1=xxx; webId=xxx; web_session=xxx; ..."
}
```

**响应**:
```json
{
    "id": "678abc...",
    "account_name": "work_account_1",
    "is_active": false,
    "is_valid": null,
    "created_at": "2026-01-21T10:00:00Z",
    "updated_at": "2026-01-21T10:00:00Z"
}
```

---

### 获取账号列表

```http
GET /api/v1/xhs/cookies
```

**响应**:
```json
[
    {
        "id": "678abc...",
        "account_name": "work_account_1",
        "is_active": true,
        "is_valid": true,
        "last_verified_at": "2026-01-21T10:30:00Z",
        "created_at": "2026-01-21T10:00:00Z",
        "updated_at": "2026-01-21T10:30:00Z"
    }
]
```

---

### 获取账号详情（含 Cookie 字符串）

获取完整的账号信息，包括 Cookie 字符串。

```http
GET /api/v1/xhs/cookies/{account_name}/detail
```

**响应**:
```json
{
    "id": "678abc...",
    "account_name": "work_account_1",
    "cookie_string": "a1=xxx; webId=xxx; web_session=xxx; ...",
    "is_active": true,
    "is_valid": true,
    "last_verified_at": "2026-01-21T10:30:00Z",
    "created_at": "2026-01-21T10:00:00Z",
    "updated_at": "2026-01-21T10:30:00Z"
}
```

---

### 验证账号有效性

验证 Cookie 是否仍然有效（未过期、未被封禁）。

```http
POST /api/v1/xhs/cookies/{account_name}/verify
```

**响应**:
```json
{
    "account_name": "work_account_1",
    "is_valid": true,
    "message": "Cookie 有效"
}
```

---

### 激活账号

将指定账号设为当前激活账号，用于后续爬取操作。

```http
POST /api/v1/xhs/cookies/{account_name}/activate
```

**响应**:
```json
{
    "message": "账号 work_account_1 已激活",
    "account": { ... }
}
```

---

### 删除账号

```http
DELETE /api/v1/xhs/cookies/{account_name}
```

**响应**:
```json
{
    "ok": true
}
```

---

### 更新账号

可修改账号名称、Cookie 字符串、激活状态。

```http
PUT /api/v1/xhs/cookies/{account_name}
Content-Type: application/json

{
    "new_account_name": "new_name",
    "cookie_string": "a1=xxx; webId=xxx; web_session=xxx; ..."
}
```

**参数说明**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| new_account_name | string | 否 | 新账号名称 |
| cookie_string | string | 否 | Cookie 字符串 |
| is_active | bool | 否 | 是否激活 |

**响应**:
```json
{
    "id": "678abc...",
    "account_name": "new_name",
    "is_active": true,
    "is_valid": null,
    "created_at": "2026-01-21T10:00:00Z",
    "updated_at": "2026-01-21T11:00:00Z"
}
```

---

## 搜索任务 API

### 创建搜索任务

创建搜索任务后，系统自动在后台执行完整流水线。

```http
POST /api/v1/xhs/search
Content-Type: application/json

{
    "project_id": "console-test-project",
    "keyword": "字节跳动",
    "max_notes": 20,
    "attention_threshold": 60
}
```

**参数说明**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| project_id | string | 是 | 项目 ID |
| keyword | string | 是 | 搜索关键词（如公司名、产品名） |
| max_notes | int | 否 | 最大搜索笔记数（1-100，默认 20） |
| attention_threshold | int | 否 | 关注度阈值（0-100，默认 60） |

**响应**:
```json
{
    "task": {
        "id": "task_123...",
        "project_id": "console-test-project",
        "keyword": "字节跳动",
        "status": "pending",
        "notes_count": 0,
        "suspicious_count": 0,
        "profiles_count": 0,
        "created_at": "2026-01-21T10:00:00Z"
    },
    "message": "搜索任务已创建，正在后台处理"
}
```

---

### 查询任务状态

```http
GET /api/v1/xhs/tasks/{task_id}
```

**响应**:
```json
{
    "id": "task_123...",
    "status": "completed",
    "notes_count": 20,
    "suspicious_count": 8,
    "profiles_count": 5,
    "error_message": null
}
```

**status 状态值**:
- `pending`: 等待执行
- `running`: 执行中
- `completed`: 已完成
- `failed`: 执行失败

---

## 笔记查询 API

### 查询项目下的笔记

```http
GET /api/v1/xhs/{project_id}/notes?suspicious_only=true&limit=50
```

**查询参数**:
| 参数 | 类型 | 说明 |
|------|------|------|
| task_id | string | 按任务 ID 筛选 |
| suspicious_only | bool | 仅返回可疑笔记 |
| min_score | int | 最小关注度分数 |
| limit | int | 返回数量限制 |

**响应**:
```json
[
    {
        "id": "note_doc_123...",
        "note_id": "67890abcdef",
        "title": "在字节的第三年",
        "desc": "分享一下我的工作心得...",
        "user": {
            "user_id": "user_123",
            "nickname": "小明@抖音研发",
            "avatar": "..."
        },
        "tagging": {
            "keyword_relevance": 95,
            "relevance_reason": "直接提及字节跳动，且用户昵称包含抖音",
            "is_suspicious": true,
            "attention_score": 82,
            "attack_surface_types": ["employee_leak", "org_structure"],
            "company_mentioned": "字节跳动",
            "key_info_extracted": ["研发岗位", "抖音部门", "三年工作经验"]
        }
    }
]
```

---

## 人物画像 API

### 查询项目下的人物画像

```http
GET /api/v1/xhs/{project_id}/profiles?min_score=70
```

**查询参数**:
| 参数 | 类型 | 说明 |
|------|------|------|
| task_id | string | 按任务 ID 筛选 |
| min_score | int | 最小关注度分数 |
| limit | int | 返回数量限制 |

**响应**:
```json
[
    {
        "id": "profile_123...",
        "user_id": "user_123",
        "nickname": "小明@抖音研发",
        "notes_count": 3,
        "tagging": {
            "keyword_relevance": 92,
            "keyword_relevance_reason": "确认为字节跳动员工，抖音研发部门",
            "company_identified": {
                "name": "字节跳动",
                "confidence": "high",
                "evidence": ["昵称包含抖音", "多篇笔记提及字节内部系统"],
                "related_to_keyword": true,
                "relationship_type": "等于搜索关键词"
            },
            "profile_summary": "该用户为字节跳动抖音研发部门员工，工作年限约3年...",
            "risk_assessment": "高价值目标，暴露了部门架构和内部工具信息",
            "attention_score": 85,
            "potential_attack_vectors": [
                "可利用已知的抖音内部系统名称构造钓鱼邮件",
                "可冒充其同事进行社工攻击"
            ],
            "recommended_actions": [
                "继续监控该用户的新发布内容",
                "关联分析其关注/粉丝中的同事账号"
            ]
        }
    }
]
```

---

## 数据模型

### 关键词关联度 (keyword_relevance)

**评分标准 (0-100)**:
| 分数段 | 含义 | 示例 |
|--------|------|------|
| 90-100 | 直接相关 | 搜索"字节跳动"，笔记直接讨论字节跳动 |
| 70-89 | 高度相关 | 提及抖音、今日头条、飞书等子产品 |
| 50-69 | 中度相关 | 提及互联网大厂、竞争对手 |
| 20-49 | 弱相关 | 仅行业层面相关 |
| 0-19 | 无关 | 与搜索关键词无关联 |

### 公司归属识别 (company_identified)

```json
{
    "name": "字节跳动",
    "confidence": "high",
    "evidence": ["直接在笔记中提及", "昵称包含公司信息"],
    "related_to_keyword": true,
    "relationship_type": "等于搜索关键词"
}
```

**relationship_type 可选值**:
- `等于搜索关键词`: 用户属于搜索的公司
- `子公司`: 用户属于搜索公司的子公司
- `投资公司`: 用户属于搜索公司投资的公司
- `竞争对手`: 用户属于竞争对手公司
- `无关`: 与搜索关键词无关

### 关注度评分 (attention_score)

**计算公式**:
```
attention_score = keyword_relevance × 权重 + 信息泄露评分
```

**评分标准**:
| 分数段 | 风险等级 | 说明 |
|--------|----------|------|
| 90-100 | 极高风险 | 直接暴露关键凭证，可立即利用 |
| 70-89 | 高风险 | 暴露大量内部信息，攻击路径清晰 |
| 50-69 | 中等风险 | 信息足够构造针对性攻击 |
| 30-49 | 低风险 | 信息有限但可作为情报积累 |
| 0-29 | 最低风险 | 无显著可利用信息 |

---

## 错误响应

```json
{
    "detail": "错误信息描述"
}
```

**常见错误码**:
| 状态码 | 说明 |
|--------|------|
| 400 | 请求参数错误 |
| 404 | 资源不存在（如账号名不存在） |
| 500 | 服务器内部错误 |
