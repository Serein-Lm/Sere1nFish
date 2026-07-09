# 系统配置管理 API 文档

## 概述

系统配置管理模块提供以下功能：
- LLM 大模型配置管理
- 工具 API Key 管理（天眼查、Hunter、博查等）
- LangSmith 追踪配置
- Langfuse 追踪配置
- 通用运行配置段管理（runtime、mobile、bailian、cosyvoice、chrome_docker 等）

基础路径: `/api/v1/config`

---

## 配置分类

| 分类 | 说明 |
|------|------|
| `llm` | LLM 大模型配置（API Key、Base URL、模型名称） |
| `tools` | 工具 API Key（tianyancha、hunter、bocha 等） |
| `langsmith` | LangSmith 追踪配置 |
| `langfuse` | Langfuse 追踪配置 |
| `sections/{category}` | 任意运行配置段，MongoDB 加密存储，读接口自动脱敏 |

---

## API 接口

### 1. 获取所有配置

```http
GET /api/v1/config
```

响应:
```json
{
  "llm": {
    "api_key": "sk-9...c92",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "default_model": "qwen3-max",
    "vision_model": "qwen3-vl-plus"
  },
  "tools": {
    "tianyancha": {
      "api_key": "3333...05f4"
    },
    "hunter": {
      "api_key": "xxxx...xxxx"
    }
  },
  "langsmith": {
    "enabled": true,
    "api_key": "lsv2...805b",
    "project": "langgraph-app",
    "endpoint": "https://api.smith.langchain.com"
  },
  "langfuse": {
    "enabled": true,
    "secret_key": "sk-l...5b97",
    "public_key": "pk-l...20da",
    "base_url": "https://cloud.langfuse.com"
  }
}
```

> 注意：API Key 会被遮蔽，只显示前4位和后4位

---

### 2. LLM 配置

#### 2.1 获取 LLM 配置

```http
GET /api/v1/config/llm
```

响应:
```json
{
  "api_key": "sk-9...c92",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "default_model": "qwen3-max",
  "vision_model": "qwen3-vl-plus"
}
```

#### 2.2 设置 LLM 配置

```http
POST /api/v1/config/llm
Content-Type: application/json

{
  "api_key": "sk-your-api-key",
  "base_url": "https://api.openai.com/v1",
  "default_model": "gpt-4",
  "vision_model": "gpt-4o"
}
```

> 可以只更新部分字段，未提供的字段保持不变

响应: 同 2.1

#### 2.3 删除 LLM 配置

```http
DELETE /api/v1/config/llm
```

响应:
```json
{
  "ok": true
}
```

---

### 3. 工具配置

#### 3.1 列出所有工具配置

```http
GET /api/v1/config/tools
```

响应:
```json
{
  "tools": [
    {
      "tool_name": "tianyancha",
      "api_key": "3333...05f4",
      "has_key": true
    },
    {
      "tool_name": "hunter",
      "api_key": "xxxx...xxxx",
      "has_key": true
    },
    {
      "tool_name": "bocha",
      "api_key": "sk-f...73a7",
      "has_key": true
    }
  ]
}
```

#### 3.2 获取指定工具配置

```http
GET /api/v1/config/tools/{tool_name}
```

示例:
```http
GET /api/v1/config/tools/hunter
```

响应:
```json
{
  "tool_name": "hunter",
  "api_key": "xxxx...xxxx",
  "has_key": true
}
```

#### 3.3 设置工具配置

```http
POST /api/v1/config/tools/{tool_name}
Content-Type: application/json

{
  "api_key": "your-api-key"
}
```

示例:
```http
POST /api/v1/config/tools/hunter
Content-Type: application/json

{
  "api_key": "your-hunter-api-key"
}
```

响应: 同 3.2

#### 3.4 删除工具配置

```http
DELETE /api/v1/config/tools/{tool_name}
```

响应:
```json
{
  "ok": true
}
```

#### 支持的工具

| 工具名 | 说明 |
|--------|------|
| `tianyancha` | 天眼查 API |
| `hunter` | 奇安信 Hunter API |
| `bocha` | 博查 API |

> 可以添加任意工具名，不限于上述列表

---

### 4. LangSmith 配置

#### 4.1 获取 LangSmith 配置

```http
GET /api/v1/config/langsmith
```

响应:
```json
{
  "enabled": true,
  "api_key": "lsv2...805b",
  "project": "langgraph-app",
  "endpoint": "https://api.smith.langchain.com"
}
```

#### 4.2 设置 LangSmith 配置

```http
POST /api/v1/config/langsmith
Content-Type: application/json

{
  "enabled": true,
  "api_key": "lsv2_pt_xxx",
  "project": "my-project",
  "endpoint": "https://api.smith.langchain.com"
}
```

> 可以只更新部分字段

响应: 同 4.1

#### 4.3 快速开关 LangSmith

```http
POST /api/v1/config/langsmith/toggle?enabled=true
```

响应:
```json
{
  "enabled": true
}
```

#### 4.4 删除 LangSmith 配置

```http
DELETE /api/v1/config/langsmith
```

响应:
```json
{
  "ok": true
}
```

---

### 5. Langfuse 配置

#### 5.1 获取 Langfuse 配置

```http
GET /api/v1/config/langfuse
```

响应:
```json
{
  "enabled": true,
  "secret_key": "sk-l...5b97",
  "public_key": "pk-l...20da",
  "base_url": "https://cloud.langfuse.com"
}
```

#### 5.2 设置 Langfuse 配置

```http
POST /api/v1/config/langfuse
Content-Type: application/json

{
  "enabled": true,
  "secret_key": "sk-lf-xxx",
  "public_key": "pk-lf-xxx",
  "base_url": "https://cloud.langfuse.com"
}
```

> 可以只更新部分字段

响应: 同 5.1

#### 5.3 快速开关 Langfuse

```http
POST /api/v1/config/langfuse/toggle?enabled=false
```

响应:
```json
{
  "enabled": false
}
```

#### 5.4 删除 Langfuse 配置

```http
DELETE /api/v1/config/langfuse
```

响应:
```json
{
  "ok": true
}
```

---

### 6. 通用配置段

用于前端配置页维护运行配置。敏感字段由后端 DAO 层加密存储，读接口只返回脱敏值；前端回传 `***` 或 `sk-...xxxx` 这类脱敏占位时，后端会保留原始密钥，不会把占位符写成真实配置。

#### 6.1 读取配置段

```http
GET /api/v1/config/sections/runtime
```

响应:
```json
{
  "category": "runtime",
  "storage": "mongodb_encrypted",
  "config": {
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "sk-...abcd",
    "models": {
      "default": "qwen3-max",
      "vision": "qwen-vl-max",
      "mobile_planner_model": "qwen3-max",
      "mobile_executor_model": "qwen-vl-max",
      "mobile_screen_model": "qwen-vl-max",
      "mobile_chat_model": "qwen3-max"
    }
  }
}
```

#### 6.2 写入配置段

```http
POST /api/v1/config/sections/bailian
Content-Type: application/json

{
  "config": {
    "api_key": "sk-xxx",
    "workspace_id": "your-workspace-id",
    "region": "beijing",
    "qwen_image_edit_model": "qwen-image-2.0-pro",
    "wanx_image_edit_model": "wanx2.1-imageedit",
    "text_to_video_model": "wan2.7-t2v-2026-06-12",
    "image_to_video_model": "wan2.7-i2v-2026-04-25",
    "timeout_seconds": 300
  }
}
```

响应:
```json
{
  "category": "bailian",
  "storage": "mongodb_encrypted",
  "config": {
    "api_key": "sk-...xxxx",
    "workspace_id": "your-workspace-id",
    "region": "beijing",
    "qwen_image_edit_model": "qwen-image-2.0-pro",
    "wanx_image_edit_model": "wanx2.1-imageedit",
    "text_to_video_model": "wan2.7-t2v-2026-06-12",
    "image_to_video_model": "wan2.7-i2v-2026-04-25",
    "timeout_seconds": 300
  }
}
```

#### 6.3 已下线的导入入口

`POST /api/v1/config/import` 只保留为旧客户端兼容提示，固定返回 `410 Gone`:

```json
{
  "detail": "config.json 导入入口已下线；请在前端配置页写入 MongoDB 加密配置。"
}
```

---

## 数据模型

### LLM 配置

| 字段 | 类型 | 说明 |
|------|------|------|
| api_key | string | API Key |
| base_url | string | API Base URL |
| default_model | string | 默认文本模型 |
| vision_model | string | 视觉模型 |

### 工具配置

| 字段 | 类型 | 说明 |
|------|------|------|
| tool_name | string | 工具名称 |
| api_key | string | API Key |
| has_key | boolean | 是否已配置 Key |

### LangSmith 配置

| 字段 | 类型 | 说明 |
|------|------|------|
| enabled | boolean | 是否启用 |
| api_key | string | API Key |
| project | string | 项目名称 |
| endpoint | string | API 端点 |

### Langfuse 配置

| 字段 | 类型 | 说明 |
|------|------|------|
| enabled | boolean | 是否启用 |
| secret_key | string | Secret Key |
| public_key | string | Public Key |
| base_url | string | API Base URL |

---

## 使用示例

### 初始化配置

```bash
# 1. 设置 LLM 配置
curl -X POST http://localhost:8000/api/v1/config/llm \
  -H "Content-Type: application/json" \
  -d '{
    "api_key": "sk-xxx",
    "base_url": "https://api.openai.com/v1",
    "default_model": "gpt-4",
    "vision_model": "gpt-4o"
  }'

# 2. 设置运行配置段
curl -X POST http://localhost:8000/api/v1/config/sections/runtime \
  -H "Content-Type: application/json" \
  -d '{"config":{"agent_timeout":500,"max_tokens":3000}}'

# 3. 设置 Hunter API Key
curl -X POST http://localhost:8000/api/v1/config/tools/hunter \
  -H "Content-Type: application/json" \
  -d '{"api_key": "your-hunter-key"}'
```

### 开关追踪

```bash
# 关闭 LangSmith
curl -X POST "http://localhost:8000/api/v1/config/langsmith/toggle?enabled=false"

# 开启 Langfuse
curl -X POST "http://localhost:8000/api/v1/config/langfuse/toggle?enabled=true"
```

### 查看配置

```bash
# 查看所有配置
curl http://localhost:8000/api/v1/config

# 查看 LLM 配置
curl http://localhost:8000/api/v1/config/llm

# 查看所有工具配置
curl http://localhost:8000/api/v1/config/tools
```

---

## 安全说明

1. **API Key 遮蔽**: 所有 GET 请求返回的 API Key 都会被遮蔽（只显示前4位和后4位）
2. **数据库加密存储**: 配置存储在 MongoDB 的 `system_config` 集合中，敏感字段加密落库
3. **认证要求**: 所有接口都需要登录认证

---

## 错误响应

```json
{
  "detail": "错误信息"
}
```

常见错误码:
- `400`: 请求参数错误
- `404`: 配置不存在
- `500`: 服务器内部错误
