# Sere1nFish Server API 文档

> Base URL: `http://127.0.0.1:8000/api/v1`
> 认证方式: Bearer Token（通过 `/api/v1/auth/login` 获取）
> Swagger UI: `http://127.0.0.1:8000/docs`

---

## 1. 认证 (`/api/v1/auth`)

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/auth/login` | 公开 | 登录获取 Token |

### POST `/auth/login`

```json
// Request
{ "username": "admin", "password": "admin123", "key": "accesskey" }

// Response 200
{ "access_token": "xxx", "token_type": "bearer", "server_token": null }
```

---

## 2. 配置管理 (`/api/v1/config`)

> **读操作**: 需登录 | **写操作**: 需 admin 权限

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/config` | 登录 | 获取所有配置 |
| GET | `/config/llm` | 登录 | 获取 LLM 配置 |
| POST | `/config/llm` | admin | 设置 LLM 配置 |
| DELETE | `/config/llm` | admin | 删除 LLM 配置 |
| GET | `/config/tools` | 登录 | 列出所有工具配置 |
| GET | `/config/tools/{tool_name}` | 登录 | 获取指定工具配置 |
| POST | `/config/tools/{tool_name}` | admin | 设置工具配置 |
| DELETE | `/config/tools/{tool_name}` | admin | 删除工具配置 |
| GET | `/config/langsmith` | 登录 | 获取 LangSmith 配置 |
| POST | `/config/langsmith` | admin | 设置 LangSmith 配置 |
| POST | `/config/langsmith/toggle` | admin | 开关 LangSmith |
| DELETE | `/config/langsmith` | admin | 删除 LangSmith 配置 |
| GET | `/config/langfuse` | 登录 | 获取 Langfuse 配置 |
| POST | `/config/langfuse` | admin | 设置 Langfuse 配置 |
| POST | `/config/langfuse/toggle` | admin | 开关 Langfuse |
| DELETE | `/config/langfuse` | admin | 删除 Langfuse 配置 |
| GET | `/config/dingtalk` | 登录 | 列出钉钉机器人 |
| GET | `/config/dingtalk/{bot_name}` | 登录 | 获取钉钉机器人配置 |
| POST | `/config/dingtalk/{bot_name}` | admin | 设置钉钉机器人 |
| POST | `/config/dingtalk/{bot_name}/toggle` | admin | 开关钉钉机器人 |
| POST | `/config/dingtalk/{bot_name}/test` | admin | 测试钉钉机器人 |
| DELETE | `/config/dingtalk/{bot_name}` | admin | 删除钉钉机器人 |
| GET | `/config/sections/{category}` | 登录 | 读取任意配置段，返回值自动脱敏 |
| POST | `/config/sections/{category}` | admin | 写入任意配置段，敏感字段在 MongoDB 中加密 |
| POST | `/config/import` | admin | 旧 config.json 导入入口已下线，返回 410 |

> 配置统一通过前端配置页或上述 API 写入 MongoDB；敏感字段由 DAO 层加密存储，读接口只返回脱敏值。不要再依赖本地 `config.json` 导入。

---

## 3. 百炼 AIGC (`/api/v1/aigc`)

> 需登录。百炼配置通过 `/config/sections/bailian` 写入 MongoDB 加密配置；完整教程见 `docs/BAILIAN_AIGC_API.md`。

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/aigc/config` | 登录 | 获取百炼 AIGC 配置状态，不返回明文 API Key |
| POST | `/aigc/images/qwen-edit` | 登录 | Qwen Image Edit 同步图片指令编辑 |
| POST | `/aigc/images/wanx-edit` | 登录 | 万相异步图片编辑，返回 `task_id` |
| POST | `/aigc/videos/text-to-video` | 登录 | 万相 2.7 文生视频，返回 `task_id` |
| POST | `/aigc/videos/image-to-video` | 登录 | 万相 2.7 图生视频，支持 `media` 数组和首尾帧 |
| GET | `/aigc/tasks/{task_id}` | 登录 | 查询百炼异步任务状态和结果 |

核心配置字段：

```json
{
  "api_key": "sk-your-bailian-key",
  "workspace_id": "your-workspace-id",
  "region": "beijing",
  "qwen_image_edit_model": "qwen-image-3.0-pro",
  "wanx_image_edit_model": "wanx2.1-imageedit",
  "text_to_video_model": "wan2.7-t2v-2026-06-12",
  "image_to_video_model": "wan2.7-i2v-2026-04-25"
}
```

---

## 4. 技能库 (`/api/v1/skills`)

### 4.1 Skill 主体

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/skills` | 登录 | 分页列表（支持筛选/搜索） |
| GET | `/skills/grouped` | 登录 | 按分类分组（场景化披露） |
| GET | `/skills/stats` | 登录 | 按状态统计数量 |
| GET | `/skills/{skill_id}` | 登录 | 获取详情（支持 id 或 slug） |
| POST | `/skills` | 登录 | 创建（admin 直接 approved，普通用户 pending_review） |
| PUT | `/skills/{skill_id}` | 登录 | 更新（仅创建者或 admin） |
| DELETE | `/skills/{skill_id}` | admin | 删除 |

#### GET `/skills` 查询参数

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| category | string | - | 按分类筛选 |
| tag | string | - | 按标签筛选 |
| status | string | - | `draft`/`pending_review`/`approved`/`rejected`/`archived` |
| search | string | - | 全文搜索（name/slug/description/tags） |
| page | int | 1 | 页码 |
| page_size | int | 20 | 每页条数（1-100） |
| sort_by | string | updated_at | 排序字段 |
| sort_order | string | desc | `asc`/`desc` |
| include_content | bool | false | 是否包含 content_raw |

#### POST `/skills` 请求体

```json
{
  "slug": "my-skill",
  "name": "我的技能",
  "category": "ai-ops",
  "description": "技能描述",
  "content_raw": "技能完整内容（Markdown）",
  "tags": ["ai", "prompt"],
  "triggers": ["关键词1"],
  "anti_triggers": ["排除词"],
  "aliases": [],
  "requires": [],
  "related": [],
  "file_signals": [],
  "risk_signals": [],
  "priority": 0,
  "meta": {}
}
```

#### 响应格式

```json
// 列表 GET /skills
{
  "items": [...],
  "total": 106,
  "page": 1,
  "page_size": 20,
  "pages": 6
}

// 单条
{
  "skill_id": "abc123",
  "slug": "my-skill",
  "name": "我的技能",
  "category": "ai-ops",
  "description": "...",
  "content_raw": "...",
  "tags": ["ai"],
  "status": "approved",
  "version": 1,
  "created_by": "admin",
  "reviewed_by": "admin",
  "review_comment": "",
  "created_at": "2026-05-31T...",
  "updated_at": "2026-05-31T..."
}
```

### 4.2 分类管理

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/skills/categories` | 登录 | 列出所有分类 |
| GET | `/skills/categories/tree` | 登录 | 获取分类树 |
| GET | `/skills/categories/{id}` | 登录 | 获取分类详情 |
| POST | `/skills/categories` | admin | 创建分类 |
| PUT | `/skills/categories/{id}` | admin | 更新分类 |
| DELETE | `/skills/categories/{id}` | admin | 删除分类 |

```json
// POST /skills/categories
{
  "slug": "my-category",
  "name": "我的分类",
  "description": "分类描述",
  "parent_id": null,
  "sort_order": 0
}
```

### 4.3 标签管理

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/skills/tags` | 登录 | 列出所有标签 |
| POST | `/skills/tags` | admin | 创建标签 |
| PUT | `/skills/tags/{tag_id}` | admin | 更新标签 |
| DELETE | `/skills/tags/{tag_id}` | admin | 删除标签（同步清理引用） |

```json
// POST /skills/tags
{ "name": "安全", "color": "#ff0000", "description": "" }
```

### 4.4 审核流

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/skills/{id}/submit-review` | 登录 | 提交审核（draft/rejected → pending_review） |
| GET | `/skills/review/pending` | admin | 列出待审核 |
| POST | `/skills/{id}/review` | admin | 审核（通过/拒绝） |
| POST | `/skills/{id}/archive` | admin | 归档 |

```json
// POST /skills/{id}/review
{ "approved": true, "comment": "审核通过" }
```

#### 状态流转

```
draft → pending_review → approved
                       → rejected → pending_review（重新提交）
approved → archived
```

---

## 5. 提示词库 (`/api/v1/prompts`)

### 5.1 Prompt 主体

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/prompts` | 登录 | 分页列表（支持筛选/搜索） |
| GET | `/prompts/stats` | 登录 | 按状态统计数量 |
| GET | `/prompts/{prompt_id}` | 登录 | 获取详情（支持 id 或 slug） |
| POST | `/prompts` | 登录 | 创建 |
| PUT | `/prompts/{prompt_id}` | 登录 | 更新 |
| DELETE | `/prompts/{prompt_id}` | admin | 删除 |

#### GET `/prompts` 查询参数

同 Skills 的 `GET /skills`，支持 category/tag/status/search/分页/排序。

#### POST `/prompts` 请求体

```json
{
  "slug": "my-prompt",
  "name": "我的提示词",
  "category": "system-prompts",
  "description": "用途描述",
  "content": "完整提示词文本",
  "system_prompt": "你是一名...",
  "user_prompt_template": "请分析以下内容：\n{input}",
  "variables": ["input"],
  "tags": ["analysis"],
  "model_hint": "qwen3.7-max",
  "temperature": 0.7,
  "max_tokens": 4096,
  "meta": {}
}
```

### 5.2 分类管理

同 Skills 分类，路径为 `/prompts/categories`。

预置分类：`system-prompts` / `task-templates` / `analysis` / `generation` / `review` / `extraction` / `conversation` / `custom`

### 5.3 标签管理

同 Skills 标签，路径为 `/prompts/tags`。

### 5.4 审核流

同 Skills 审核流，路径为 `/prompts/{id}/submit-review`、`/prompts/review/pending`、`/prompts/{id}/review`、`/prompts/{id}/archive`。

---

## 6. 权限体系

| 角色 | Config 读 | Config 写 | Skills/Prompts 读 | 创建 | 编辑自己的 | 编辑他人的 | 删除 | 审核 |
|------|-----------|-----------|-------------------|------|-----------|-----------|------|------|
| user | ✅ | ❌ | ✅ | ✅（需审核） | ✅ | ❌ | ❌ | ❌ |
| admin | ✅ | ✅ | ✅ | ✅（直接通过） | ✅ | ✅ | ✅ | ✅ |

---

## 7. 数据同步脚本

`scripts.sync_to_db` 只负责 Skills / Prompts 资料同步。系统运行配置不再由脚本或本地文件导入，请在前端配置页写入 MongoDB 加密配置。

```bash
cd Sere1nFishServer

# 同步全部（不覆盖已有）
python -m scripts.sync_to_db

# 强制覆盖
python -m scripts.sync_to_db --overwrite

# 仅同步某一模块
python -m scripts.sync_to_db --only skills
python -m scripts.sync_to_db --only prompts
```

---

## 8. 通用响应格式

### 分页列表

```json
{
  "items": [...],
  "total": 100,
  "page": 1,
  "page_size": 20,
  "pages": 5
}
```

### 错误响应

```json
// 401 Unauthorized
{ "detail": "无效的认证凭证" }

// 403 Forbidden
{ "detail": "需要管理员权限" }

// 404 Not Found
{ "detail": "Skill 不存在" }

// 409 Conflict
{ "detail": "Skill slug 'xxx' 已存在" }

// 422 Validation Error
{ "detail": [{"loc": [...], "msg": "...", "type": "..."}] }
```
