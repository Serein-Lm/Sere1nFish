# 抖音社工信息采集 API 文档

## 概述

抖音社工信息采集模块提供以下功能：
- Cookie 管理
- 搜索结果管理
- 打标结果管理
- 用户画像管理
- 截图与视觉分析

基础路径: `/api/v1/douyin`

---

## 数据模型

### 搜索结果 (DouyinSearchResult)

```json
{
  "id": "MongoDB ObjectId",
  "project_id": "项目ID",
  "aweme_id": "作品ID（唯一标识）",
  "keyword": "搜索关键词",
  "title": "作品描述/标题",
  "create_time_str": "2026-01-28 19:00:00",
  "nickname": "用户昵称",
  "user_id": "用户ID",
  "sec_uid": "用户安全ID",
  "avatar": "头像URL（用户头像）",
  "cover_url": "封面URL（视频封面图）",
  "ip_location": "IP属地",
  "liked_count": "23",
  "collected_count": "5",
  "comment_count": "1",
  "share_count": "1",
  "video_download_url": "视频下载链接",
  "aweme_url": "作品链接（点击跳转抖音）",
  "user_profile_url": "用户主页链接",
  "created_at": "2026-01-28T11:00:00Z"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | MongoDB ObjectId |
| project_id | string | 项目ID |
| aweme_id | string | 作品ID（唯一标识） |
| keyword | string | 搜索关键词 |
| title | string | 作品描述/标题 |
| create_time_str | string | 发布时间 |
| nickname | string | 用户昵称 |
| user_id | string | 用户ID |
| sec_uid | string | 用户安全ID |
| avatar | string | 用户头像URL |
| cover_url | string | 视频封面图URL |
| ip_location | string | IP属地（可能为空） |
| liked_count | string | 点赞数 |
| collected_count | string | 收藏数 |
| comment_count | string | 评论数 |
| share_count | string | 分享数 |
| video_download_url | string | 视频下载链接 |
| aweme_url | string | 作品链接（点击跳转抖音） |
| user_profile_url | string | 用户主页链接 |
| created_at | string | 入库时间 |
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
  "user_id": "用户ID",
  "sec_uid": "用户安全ID",
  "avatar": "用户头像URL",
  "cover_url": "视频封面图URL",
  "ip_location": "IP属地",
  "liked_count": "23",
  "collected_count": "5",
  "comment_count": "1",
  "share_count": "1",
  "aweme_url": "作品链接",
  "user_profile_url": "用户主页链接",
  "create_time_str": "2026-01-28 19:00:00",
  "tag": "potential_employee | marketing | uncertain",
  "tag_reason": "打标理由",
  "confidence": "high | medium | low",
  "key_evidence": ["证据1", "证据2"],
  "company_mentioned": "提及的公司",
  "position_mentioned": "提及的职位",
  "priority": 8,
  "created_at": "2026-01-28T11:00:00Z"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | MongoDB ObjectId |
| project_id | string | 项目ID |
| aweme_id | string | 作品ID |
| keyword | string | 搜索关键词 |
| title | string | 作品描述 |
| nickname | string | 用户昵称 |
| user_id | string | 用户ID |
| sec_uid | string | 用户安全ID |
| avatar | string | 用户头像URL |
| cover_url | string | 视频封面图URL |
| ip_location | string | IP属地 |
| liked_count | string | 点赞数 |
| collected_count | string | 收藏数 |
| comment_count | string | 评论数 |
| share_count | string | 分享数 |
| aweme_url | string | 作品链接 |
| user_profile_url | string | 用户主页链接 |
| create_time_str | string | 发布时间 |
| tag | string | 标签：potential_employee/marketing/uncertain |
| tag_reason | string | 打标理由 |
| confidence | string | 置信度：high/medium/low |
| key_evidence | array | 关键证据列表 |
| company_mentioned | string | 提及的公司 |
| position_mentioned | string | 提及的职位 |
| priority | number | 优先级 1-10 |
| created_at | string | 入库时间 |
```

### 用户画像 (DouyinProfile)

```json
{
  "id": "MongoDB ObjectId",
  "project_id": "项目ID",
  "sec_uid": "用户安全ID（唯一标识）",
  "user_id": "用户ID",
  "nickname": "用户昵称",
  "avatar_url": "头像URL",
  "user_profile_url": "用户主页链接",
  "ip_location": "IP属地",
  
  "sample_title": "示例作品标题",
  "tag_reason": "打标理由",
  "confidence": "high | medium | low",
  "key_evidence": ["证据1", "证据2"],
  "company_mentioned": "提及的公司",
  "position_mentioned": "提及的职位",
  "priority": 8,
  "aweme_count": 3,
  
  "basic_info": {
    "douyin_id": "抖音号",
    "ip_location": "IP属地",
    "account_type": "个人号/营销号/企业号",
    "gender": "男/女/未知"
  },
  "stats": {
    "follows": "关注数",
    "fans": "粉丝数",
    "interaction": "获赞数",
    "videos_count": "作品数"
  },
  "identity": {
    "company": "公司名称",
    "industry": "所属行业",
    "position": "职位",
    "department": "部门"
  },
  "company_identification": {
    "identified_company": "判定的公司名称",
    "confidence": "high/medium/low/none",
    "evidence": ["判断依据"]
  },
  "keyword_relevance": {
    "score": 85,
    "target_company": "目标公司",
    "relationship": "直接员工/前员工/关联公司员工/无关"
  },
  "attack_surface": {
    "risk_score": 75,
    "risk_level": "高",
    "exposed_information": [...]
  },
  "profile_summary": "综合人物画像描述",
  "attention_score": 85,
  "recommended_actions": [...],
  "tags": ["标签1", "标签2"],
  "screenshot_paths": ["截图路径1", "截图路径2"],
  
  "created_at": "2026-01-28T11:00:00Z",
  "updated_at": "2026-01-28T11:00:00Z"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | MongoDB ObjectId |
| project_id | string | 项目ID |
| sec_uid | string | 用户安全ID（唯一标识） |
| user_id | string | 用户ID |
| nickname | string | 用户昵称 |
| avatar_url | string | 头像URL（爬取数据） |
| user_profile_url | string | 用户主页链接 |
| ip_location | string | IP属地 |
| sample_title | string | 示例作品标题 |
| tag_reason | string | 打标理由 |
| confidence | string | 置信度 |
| key_evidence | array | 关键证据 |
| company_mentioned | string | 提及的公司 |
| position_mentioned | string | 提及的职位 |
| priority | number | 优先级 1-10 |
| aweme_count | number | 作品数量 |
| basic_info | object | 基础信息（视觉分析） |
| stats | object | 统计数据（视觉分析） |
| identity | object | 身份信息（视觉分析） |
| company_identification | object | 公司识别（视觉分析） |
| keyword_relevance | object | 关键词关联度（视觉分析） |
| attack_surface | object | 攻击面分析（视觉分析） |
| profile_summary | string | 画像摘要（视觉分析） |
| attention_score | number | 关注价值分数 0-100 |
| recommended_actions | array | 推荐行动 |
| tags | array | 标签列表 |
| screenshot_paths | array | 截图文件路径 |
| created_at | string | 创建时间 |
| updated_at | string | 更新时间 |


---

## API 接口

### 1. Cookie 管理

#### 1.1 添加/更新 Cookie

```http
POST /api/v1/douyin/cookies
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
  "last_verified_at": null,
  "created_at": "2026-01-28T11:00:00Z",
  "updated_at": "2026-01-28T11:00:00Z"
}
```

#### 1.2 列出所有 Cookie

```http
GET /api/v1/douyin/cookies?limit=50&skip=0
```

响应:
```json
[
  {
    "id": "xxx",
    "account_name": "default",
    "is_active": true,
    "is_valid": true,
    "last_verified_at": "2026-01-28T11:00:00Z",
    "created_at": "2026-01-28T11:00:00Z",
    "updated_at": "2026-01-28T11:00:00Z"
  }
]
```

#### 1.3 获取账号基本信息

```http
GET /api/v1/douyin/cookies/{account_name}
```

响应:
```json
{
  "id": "xxx",
  "account_name": "default",
  "is_active": true,
  "is_valid": true,
  "last_verified_at": "2026-01-28T11:00:00Z",
  "created_at": "2026-01-28T11:00:00Z",
  "updated_at": "2026-01-28T11:00:00Z"
}
```

#### 1.4 获取账号详情（含 Cookie 字符串）

```http
GET /api/v1/douyin/cookies/{account_name}/detail
```

响应:
```json
{
  "id": "xxx",
  "account_name": "default",
  "cookie_string": "cookie内容...",
  "is_active": true,
  "is_valid": true,
  "last_verified_at": "2026-01-28T11:00:00Z",
  "created_at": "2026-01-28T11:00:00Z",
  "updated_at": "2026-01-28T11:00:00Z"
}
```

#### 1.5 更新账号

```http
PUT /api/v1/douyin/cookies/{account_name}
```

请求体（所有字段可选）:
```json
{
  "cookie_string": "新的cookie内容...",
  "is_active": true,
  "new_account_name": "new_name"
}
```

响应: 同 1.3

#### 1.6 激活 Cookie

```http
POST /api/v1/douyin/cookies/{account_name}/activate
```

说明: 激活指定账号，同时取消其他账号的激活状态

响应:
```json
{
  "id": "xxx",
  "account_name": "default",
  "is_active": true,
  "is_valid": true,
  "last_verified_at": "2026-01-28T11:00:00Z",
  "created_at": "2026-01-28T11:00:00Z",
  "updated_at": "2026-01-28T11:00:00Z"
}
```

#### 1.7 验证 Cookie 有效性

```http
POST /api/v1/douyin/cookies/{account_name}/verify
```

说明: 通过访问抖音页面测试 Cookie 是否有效

响应:
```json
{
  "id": "xxx",
  "account_name": "default",
  "is_active": true,
  "is_valid": true,
  "last_verified_at": "2026-01-29T10:30:00Z",
  "created_at": "2026-01-28T11:00:00Z",
  "updated_at": "2026-01-29T10:30:00Z"
}
```

#### 1.8 删除 Cookie

```http
DELETE /api/v1/douyin/cookies/{account_name}
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
GET /api/v1/douyin/{project_id}/search-results?keyword=b站实习&limit=50&skip=0
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
GET /api/v1/douyin/{project_id}/search-results/{aweme_id}
```

响应: 完整的 DouyinSearchResult 对象

#### 2.3 统计搜索结果

```http
GET /api/v1/douyin/{project_id}/search-results/count?keyword=b站实习
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
GET /api/v1/douyin/{project_id}/tagged-results?tag=potential_employee&limit=50&skip=0
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
GET /api/v1/douyin/{project_id}/potential-users
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
GET /api/v1/douyin/{project_id}/tagged-results/stats
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
GET /api/v1/douyin/{project_id}/profiles?limit=50&skip=0
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
GET /api/v1/douyin/profiles/{profile_id}
```

响应: 完整的 DouyinProfile 对象

#### 4.3 删除用户画像

```http
DELETE /api/v1/douyin/profiles/{profile_id}
```

响应:
```json
{
  "ok": true
}
```

#### 4.4 统计用户画像

```http
GET /api/v1/douyin/{project_id}/profiles/count
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
POST /api/v1/douyin/screenshot/stream
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
POST /api/v1/douyin/vision-analysis/stream
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
POST /api/v1/douyin/sse/cancel/{task_id}
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

---

## 6. 流水线 API

### 6.1 运行完整流水线

```http
POST /api/v1/douyin/{project_id}/pipeline
Content-Type: application/json

{
  "keyword": "b站实习",
  "max_videos": 20,
  "publish_time": 0,
  "enable_profile": true
}
```

参数说明:
- `keyword`: 搜索关键词（如 "b站实习"）
- `max_videos`: 最大视频数（默认 20）
- `publish_time`: 发布时间筛选
  - `0`: 不限（默认）
  - `1`: 一天内
  - `7`: 一周内
  - `180`: 半年内
- `enable_profile`: 是否生成人物画像（默认 true）

响应:
```json
{
  "project_id": "xxx",
  "keyword": "b站实习",
  "videos_count": 20,
  "potential_count": 5,
  "profiles_count": 5,
  "error": null
}
```

### 6.2 运行流水线（SSE 流式）

```http
POST /api/v1/douyin/{project_id}/pipeline/stream
Content-Type: application/json

{
  "keyword": "b站实习",
  "max_videos": 20,
  "publish_time": 0,
  "enable_profile": true
}
```

SSE 响应格式:
```
data: {"type": "init", "task_id": "abc123", "stage": "search"}

data: {"type": "status", "message": "🔍 搜索关键词: b站实习", "stage": "search"}

data: {"type": "status", "message": "✅ 搜索完成，获取 20 条结果", "stage": "search"}

data: {"type": "status", "message": "🏷️ 开始 Agent 打标...", "stage": "tagging"}

data: {"type": "status", "message": "✅ 打标完成，发现 5 个潜在员工", "stage": "tagging"}

data: {"type": "status", "message": "👤 开始生成人物画像...", "stage": "profile"}

data: {"type": "status", "message": "✅ 画像生成完成，共 5 个", "stage": "profile"}

data: {"type": "done", "message": "流水线完成", "videos_count": 20, "potential_count": 5, "profiles_count": 5}
```

### 6.3 人物画像生成（SSE 流式）

```http
POST /api/v1/douyin/profile/generate/stream
Content-Type: application/json

{
  "user_url": "https://www.douyin.com/user/MS4wLjABAAAAxxx",
  "project_id": "xxx",
  "keyword": "b站实习"
}
```

SSE 响应格式:
```
data: {"type": "init", "task_id": "abc123", "sec_uid": "MS4wLjABAAAAxxx", "stage": "screenshot"}

data: {"type": "status", "message": "📸 开始截屏...", "stage": "screenshot"}

data: {"type": "status", "message": "✅ 截屏完成，共 3 张", "stage": "screenshot"}

data: {"type": "status", "message": "🔍 开始视觉分析...", "stage": "vision"}

data: {"type": "content", "content": "视觉分析内容片段..."}

data: {"type": "status", "message": "✅ 视觉分析完成", "stage": "vision"}

data: {"type": "status", "message": "💾 正在保存到数据库...", "stage": "save"}

data: {"type": "status", "message": "✅ 入库完成", "stage": "save"}

data: {"type": "done", "message": "人物画像生成完成", "sec_uid": "xxx", "profile_id": "xxx"}
```

---

## 完整流程示例

### 1. 准备工作

```bash
# 1. 添加 Cookie
curl -X POST http://localhost:8000/api/v1/douyin/cookies \
  -H "Content-Type: application/json" \
  -d '{"account_name": "default", "cookie_string": "your_cookie..."}'

# 2. 激活 Cookie
curl -X POST http://localhost:8000/api/v1/douyin/cookies/default/activate

# 3. 验证 Cookie
curl -X POST http://localhost:8000/api/v1/douyin/cookies/default/verify
```

### 2. 运行流水线

```bash
# 运行完整流水线（同步）
curl -X POST http://localhost:8000/api/v1/douyin/{project_id}/pipeline \
  -H "Content-Type: application/json" \
  -d '{"keyword": "b站实习", "max_videos": 20}'

# 或使用 SSE 流式
curl -X POST http://localhost:8000/api/v1/douyin/{project_id}/pipeline/stream \
  -H "Content-Type: application/json" \
  -d '{"keyword": "b站实习", "max_videos": 20}'
```

### 3. 查看结果

```bash
# 查看搜索结果
curl http://localhost:8000/api/v1/douyin/{project_id}/search-results

# 查看打标结果
curl http://localhost:8000/api/v1/douyin/{project_id}/tagged-results

# 查看潜在用户
curl http://localhost:8000/api/v1/douyin/{project_id}/potential-users

# 查看用户画像
curl http://localhost:8000/api/v1/douyin/{project_id}/profiles
```

### 4. 单独生成画像

```bash
# 对特定用户生成画像
curl -X POST http://localhost:8000/api/v1/douyin/profile/generate/stream \
  -H "Content-Type: application/json" \
  -d '{
    "user_url": "https://www.douyin.com/user/MS4wLjABAAAAxxx",
    "project_id": "xxx",
    "keyword": "b站实习"
  }'
```
