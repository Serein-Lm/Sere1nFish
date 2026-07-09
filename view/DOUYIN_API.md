# 抖音社工信息采集 API 文档

## 概述

抖音社工信息采集模块提供以下功能：
- Cookie 管理
- 搜索结果管理
- 打标结果管理
- 用户画像管理
- 截图与视觉分析

基础路径: `/api/douyin`

---

## 数据模型

### 搜索结果 (DouyinSearchResult)

```json
{
  "id": "MongoDB ObjectId",
  "project_id": "项目ID",
  "aweme_id": "作品ID（唯一标识）",
  "keyword": "搜索关键词",
  "aweme_type": "作品类型",
  "title": "作品描述/标题",
  "create_time": 1234567890,
  "create_time_str": "2026-01-28 19:00:00",
  "ip_location": "IP属地",
  "liked_count": 12345,
  "collected_count": 1234,
  "comment_count": 567,
  "share_count": 89,
  "user_id": "用户ID",
  "sec_uid": "用户安全ID",
  "nickname": "用户昵称",
  "avatar": "头像URL",
  "cover_url": "封面URL",
  "video_download_url": "视频下载链接",
  "aweme_url": "作品链接",
  "user_profile_url": "用户主页链接",
  "source_keyword": "来源关键词",
  "created_at": "2026-01-28T11:00:00Z",
  "updated_at": "2026-01-28T11:00:00Z"
}
```

### 打标结果 (DouyinTaggedResult)

```json
{
  "id": "MongoDB ObjectId",
  "project_id": "项目ID",
  "aweme_id": "作品ID",
  "keyword": "搜索关键词",
  "title": "作品描述",
  "nickname": "用户昵称",
  "sec_uid": "用户安全ID",
  "user_id": "用户ID",
  "user_profile_url": "用户主页链接",
  "aweme_url": "作品链接",
  "liked_count": 12345,
  "comment_count": 567,
  "create_time_str": "2026-01-28 19:00:00",
  "tag": "potential_employee | marketing | uncertain",
  "tag_reason": "打标理由",
  "confidence": "high | medium | low",
  "key_evidence": ["证据1", "证据2"],
  "company_mentioned": "提及的公司",
  "position_mentioned": "提及的职位",
  "priority": 8,
  "created_at": "2026-01-28T11:00:00Z",
  "updated_at": "2026-01-28T11:00:00Z"
}
```

### 用户画像 (DouyinProfile)

```json
{
  "id": "MongoDB ObjectId",
  "project_id": "项目ID",
  "sec_uid": "用户安全ID（唯一标识）",
  "user_id": "用户ID",
  "nickname": "用户昵称",
  "user_profile_url": "用户主页链接",
  "sample_title": "示例作品标题",
  "tag_reason": "打标理由",
  "confidence": "high | medium | low",
  "priority": 8,
  "vision_analysis": "视觉分析结果（JSON字符串）",
  "screenshot_paths": ["截图路径1", "截图路径2"],
  "created_at": "2026-01-28T11:00:00Z",
  "updated_at": "2026-01-28T11:00:00Z"
}
```


---

## API 接口

### 1. Cookie 管理

#### 1.1 添加/更新 Cookie

```http
POST /api/douyin/cookies
```

请求体:
```json
{
  "account_name": "default",
  "cookie_string": "cookie内容..."
}
```

响应:
```json
{
  "id": "xxx",
  "account_name": "default",
  "is_active": false,
  "is_valid": null,
  "created_at": "2026-01-28T11:00:00Z",
  "updated_at": "2026-01-28T11:00:00Z"
}
```

#### 1.2 列出所有 Cookie

```http
GET /api/douyin/cookies?limit=50&skip=0
```

响应:
```json
[
  {
    "id": "xxx",
    "account_name": "default",
    "is_active": true,
    "is_valid": true,
    "created_at": "2026-01-28T11:00:00Z"
  }
]
```

#### 1.3 激活 Cookie

```http
POST /api/douyin/cookies/{account_name}/activate
```

响应:
```json
{
  "id": "xxx",
  "account_name": "default",
  "is_active": true
}
```

#### 1.4 删除 Cookie

```http
DELETE /api/douyin/cookies/{account_name}
```

响应:
```json
{
  "ok": true
}
```

---

### 2. 搜索结果

#### 2.1 列出搜索结果

```http
GET /api/douyin/{project_id}/search-results?keyword=b站实习&limit=50&skip=0
```

参数:
- `project_id`: 项目ID（路径参数）
- `keyword`: 筛选关键词（可选）
- `limit`: 返回数量（默认50）
- `skip`: 跳过数量（默认0）

响应:
```json
{
  "total": 100,
  "items": [
    {
      "id": "xxx",
      "aweme_id": "7471165520058862848",
      "title": "B站实习日记...",
      "nickname": "用户昵称",
      "user_profile_url": "https://www.douyin.com/user/xxx",
      "liked_count": 12345,
      "comment_count": 567
    }
  ]
}
```

#### 2.2 获取单条搜索结果

```http
GET /api/douyin/{project_id}/search-results/{aweme_id}
```

响应: 完整的 DouyinSearchResult 对象

#### 2.3 统计搜索结果

```http
GET /api/douyin/{project_id}/search-results/count?keyword=b站实习
```

响应:
```json
{
  "total": 100
}
```

---

### 3. 打标结果

#### 3.1 列出打标结果

```http
GET /api/douyin/{project_id}/tagged-results?tag=potential_employee&limit=50&skip=0
```

参数:
- `project_id`: 项目ID（路径参数）
- `tag`: 筛选标签（可选）: `potential_employee` | `marketing` | `uncertain`
- `limit`: 返回数量（默认50）
- `skip`: 跳过数量（默认0）

响应:
```json
{
  "total": 50,
  "stats": {
    "potential_employee": 15,
    "marketing": 20,
    "uncertain": 15
  },
  "items": [
    {
      "id": "xxx",
      "aweme_id": "7471165520058862848",
      "nickname": "用户昵称",
      "tag": "potential_employee",
      "tag_reason": "内容明确提及在B站实习...",
      "confidence": "high",
      "priority": 9,
      "user_profile_url": "https://www.douyin.com/user/xxx"
    }
  ]
}
```

#### 3.2 获取潜在用户列表（去重）

```http
GET /api/douyin/{project_id}/potential-users
```

响应:
```json
{
  "total": 10,
  "users": [
    {
      "sec_uid": "MS4wLjABAAAAxxx",
      "nickname": "用户昵称",
      "user_profile_url": "https://www.douyin.com/user/xxx",
      "tag_reason": "打标理由",
      "confidence": "high",
      "priority": 9,
      "aweme_count": 3
    }
  ]
}
```

#### 3.3 统计打标结果

```http
GET /api/douyin/{project_id}/tagged-results/stats
```

响应:
```json
{
  "total": 50,
  "potential_employee": 15,
  "marketing": 20,
  "uncertain": 15
}
```


---

### 4. 用户画像

#### 4.1 列出用户画像

```http
GET /api/douyin/{project_id}/profiles?limit=50&skip=0
```

响应:
```json
{
  "total": 10,
  "items": [
    {
      "id": "xxx",
      "sec_uid": "MS4wLjABAAAAxxx",
      "nickname": "用户昵称",
      "user_profile_url": "https://www.douyin.com/user/xxx",
      "confidence": "high",
      "priority": 9,
      "vision_analysis": null
    }
  ]
}
```

#### 4.2 获取单个用户画像

```http
GET /api/douyin/profiles/{profile_id}
```

响应: 完整的 DouyinProfile 对象

#### 4.3 删除用户画像

```http
DELETE /api/douyin/profiles/{profile_id}
```

响应:
```json
{
  "ok": true
}
```

#### 4.4 统计用户画像

```http
GET /api/douyin/{project_id}/profiles/count
```

响应:
```json
{
  "total": 10
}
```

---

### 5. 截图与视觉分析

#### 5.1 截图用户主页（SSE 流式）

```http
POST /api/douyin/screenshot/stream
Content-Type: application/json

{
  "user_url": "https://www.douyin.com/user/MS4wLjABAAAAxxx",
  "max_screenshots": 5
}
```

SSE 响应格式:
```
data: {"type": "progress", "message": "正在启动浏览器..."}

data: {"type": "progress", "message": "正在访问用户主页..."}

data: {"type": "progress", "message": "正在截取第 1 张截图..."}

data: {"type": "result", "data": {"screenshots": [...], "error": null}}
```

#### 5.2 视觉分析（SSE 流式）

```http
POST /api/douyin/vision-analysis/stream
Content-Type: application/json

{
  "user_url": "https://www.douyin.com/user/MS4wLjABAAAAxxx",
  "project_id": "xxx"
}
```

SSE 响应格式:
```
data: {"type": "init", "task_id": "abc123", "stage": "screenshot"}

data: {"type": "status", "message": "📸 开始截屏...", "stage": "screenshot"}

data: {"type": "status", "message": "🔍 开始视觉分析...", "stage": "vision"}

data: {"type": "content", "content": "分析内容片段..."}

data: {"type": "done", "message": "分析完成"}
```

#### 5.3 取消 SSE 任务

```http
POST /api/douyin/sse/cancel/{task_id}
```

响应:
```json
{
  "success": true,
  "message": "任务已取消",
  "stage": "screenshot",
  "immediate": true
}
```

---

## 打标标签说明

| 标签 | 说明 |
|------|------|
| `potential_employee` | 潜在目标员工，内容明确提及在目标公司工作/实习 |
| `marketing` | 营销号，内容为广告、推广性质 |
| `uncertain` | 不确定，信息不足以判断 |

## 置信度说明

| 置信度 | 说明 |
|--------|------|
| `high` | 高置信度，有明确证据 |
| `medium` | 中置信度，有较强线索 |
| `low` | 低置信度，证据不充分 |

## 优先级说明

优先级范围: 1-10，数值越高优先级越高

| 优先级 | 说明 |
|--------|------|
| 9-10 | 高价值目标，确认身份 |
| 7-8 | 中高价值，身份基本确认 |
| 5-6 | 中等价值，需进一步验证 |
| 3-4 | 低价值，信息有限 |
| 1-2 | 无价值，与目标无关 |

---

## 用户主页 URL 格式

抖音用户主页链接格式:
```
https://www.douyin.com/user/{sec_uid}
```

示例:
```
https://www.douyin.com/user/MS4wLjABAAAA8U_l6rBzmy7bcy6xOJel4v0RzoR_wfAubGPeJimN__4
```

---

## 错误响应

所有接口在发生错误时返回:

```json
{
  "detail": "错误信息"
}
```

常见错误码:
- `400`: 请求参数错误
- `404`: 资源不存在
- `500`: 服务器内部错误
