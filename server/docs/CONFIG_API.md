# 系统配置管理 API 文档

## 概述

系统配置管理模块提供以下功能：
- LLM 大模型配置管理
- 工具 API Key 管理（天眼查、Hunter、博查等）
- LangSmith 追踪配置
- Langfuse 追踪配置
- 任意运行时配置段管理
- MongoDB 加密存储和读接口脱敏

基础路径: `/api/v1/config`

---

## 存储模型

配置统一从前端配置页或配置 API 写入 MongoDB。敏感字段在 DAO 层加密保存，读取时默认只返回脱敏值。

前端系统配置页的“运行配置”页签可直接编辑 `bailian`、`cosyvoice`、`chrome_docker`、`mobile`、`runtime`、`mcpServers`、`logging`、`xhs_crawler`、`douyin_crawler` 等通用配置段。

旧版 `config.json` 导入流程已下线。`POST /api/v1/config/import` 仅保留为兼容提示入口，固定返回 `410 Gone`。

---

## 配置分类

| 分类 | 说明 |
|------|------|
| `llm` | LLM 大模型配置（API Key、Base URL、模型名称） |
| `tools` | 工具 API Key（tianyancha、hunter、bocha 等） |
| `langsmith` | LangSmith 追踪配置 |
| `langfuse` | Langfuse 追踪配置 |
| `dingtalk` | 钉钉机器人配置 |
| `cosyvoice` | 阿里云百炼 Qwen-Audio / 实时 TTS 配置 |
| `bailian` | 阿里云百炼图片编辑、视频生成配置 |
| `chrome_docker` | 后端浏览器自动化容器配置 |
| `mobile` | 手机操作 Agent 运行配置 |
| `mcpServers` | MCP 服务配置 |

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
  "vision_model": "qwen3-vl-plus",
  "mobile_planner_model": "qwen3-max",
  "mobile_executor_model": "qwen-vl-max",
  "mobile_screen_model": "qwen-vl-max",
  "mobile_chat_model": "qwen3-max"
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
  "vision_model": "gpt-4o",
  "mobile_planner_model": "gpt-4",
  "mobile_executor_model": "gpt-4o",
  "mobile_screen_model": "gpt-4o",
  "mobile_chat_model": "gpt-4"
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

### 6. 钉钉机器人配置

#### 6.1 列出所有钉钉机器人

```http
GET /api/v1/config/dingtalk
```

响应:
```json
{
  "bots": [
    {
      "bot_name": "default",
      "access_token": "xxxx...xxxx",
      "secret": "SEC1...xxxx",
      "keyword": "安全资讯",
      "enabled": true,
      "has_token": true
    },
    {
      "bot_name": "alert",
      "access_token": "yyyy...yyyy",
      "secret": "SEC2...yyyy",
      "keyword": "告警",
      "enabled": true,
      "has_token": true
    }
  ]
}
```

#### 6.2 获取指定钉钉机器人配置

```http
GET /api/v1/config/dingtalk/{bot_name}
```

示例:
```http
GET /api/v1/config/dingtalk/default
```

响应:
```json
{
  "bot_name": "default",
  "access_token": "xxxx...xxxx",
  "secret": "SEC1...xxxx",
  "keyword": "安全资讯",
  "enabled": true,
  "has_token": true
}
```

#### 6.3 设置钉钉机器人配置

```http
POST /api/v1/config/dingtalk/{bot_name}
Content-Type: application/json

{
  "access_token": "your-access-token",
  "secret": "SECxxxxxxxx",
  "keyword": "安全资讯",
  "enabled": true
}
```

配置说明：
- `access_token`: Webhook URL 中的 access_token 参数
- `secret`: 签名密钥（安全设置中的加签密钥）
- `keyword`: 关键词（安全设置中的自定义关键词，消息中必须包含此关键词）
- `enabled`: 是否启用

> 可以只更新部分字段，未提供的字段保持不变

响应: 同 6.2

#### 6.4 快速开关钉钉机器人

```http
POST /api/v1/config/dingtalk/{bot_name}/toggle?enabled=true
```

响应:
```json
{
  "bot_name": "default",
  "enabled": true
}
```

#### 6.5 测试钉钉机器人

```http
POST /api/v1/config/dingtalk/{bot_name}/test
```

发送一条测试消息验证配置是否正确。

成功响应:
```json
{
  "ok": true,
  "message": "测试消息发送成功"
}
```

失败响应:
```json
{
  "detail": "发送失败: 关键词不匹配 (errcode: 310000)"
}
```

#### 6.6 删除钉钉机器人配置

```http
DELETE /api/v1/config/dingtalk/{bot_name}
```

响应:
```json
{
  "ok": true
}
```

---

### 7. 通用配置段

```http
GET /api/v1/config/sections/{category}
```

响应:
```json
{
  "category": "bailian",
  "config": {
    "api_key": "sk-...abcd",
    "workspace_id": "your-workspace-id",
    "region": "beijing",
    "qwen_image_edit_model": "qwen-image-3.0-pro",
    "wanx_image_edit_model": "wanx2.1-imageedit",
    "text_to_video_model": "wan2.7-t2v-2026-06-12",
    "image_to_video_model": "wan2.7-i2v-2026-04-25",
    "timeout_seconds": 300
  },
  "storage": "mongodb_encrypted"
}
```

```http
POST /api/v1/config/sections/{category}
Content-Type: application/json

{
  "config": {
    "api_key": "sk-your-secret",
    "enabled": true
  }
}
```

说明:
- `category`: 配置段名称，例如 `bailian`、`cosyvoice`、`mobile`、`chrome_docker`
- `config`: 完整或局部配置对象
- 敏感字段会在 DAO 层加密保存
- 响应中的敏感字段自动脱敏

响应:
```json
{
  "category": "bailian",
  "config": {
    "api_key": "sk-y...cret",
    "enabled": true
  },
  "storage": "mongodb_encrypted"
}
```

### 8. 已下线: config.json 导入

```http
POST /api/v1/config/import
```

该入口不再读取本地 `config.json`，固定返回:

```json
{
  "detail": "config.json 导入入口已下线；请在前端配置页写入 MongoDB 加密配置。"
}
```

HTTP 状态码为 `410 Gone`。

---

## 数据模型

### LLM 配置

| 字段 | 类型 | 说明 |
|------|------|------|
| api_key | string | API Key |
| base_url | string | API Base URL |
| default_model | string | 默认文本模型 |
| vision_model | string | 视觉模型 |
| mobile_planner_model | string | 手机任务规划模型；为空回退到默认文本模型 |
| mobile_executor_model | string | 手机动作执行模型；为空回退到视觉模型 |
| mobile_screen_model | string | 手机读屏/界面分析模型；为空回退到手机执行模型 |
| mobile_chat_model | string | 手机聊天状态解析/回复模型；为空回退到默认文本模型 |

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

### 钉钉机器人配置

| 字段 | 类型 | 说明 |
|------|------|------|
| bot_name | string | 机器人名称（用于区分多个机器人） |
| access_token | string | Webhook access_token |
| secret | string | 签名密钥 |
| keyword | string | 关键词（消息中必须包含） |
| enabled | boolean | 是否启用 |
| has_token | boolean | 是否已配置 Token |

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
    "vision_model": "gpt-4o",
    "mobile_planner_model": "gpt-4",
    "mobile_executor_model": "gpt-4o",
    "mobile_screen_model": "gpt-4o",
    "mobile_chat_model": "gpt-4"
  }'

# 2. 设置 Hunter API Key
curl -X POST http://localhost:8000/api/v1/config/tools/hunter \
  -H "Content-Type: application/json" \
  -d '{"api_key": "your-hunter-key"}'

# 3. 设置阿里云百炼配置
curl -X POST http://localhost:8000/api/v1/config/sections/bailian \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "api_key": "sk-your-bailian-key",
      "workspace_id": "your-workspace-id",
      "region": "beijing",
      "qwen_image_edit_model": "qwen-image-3.0-pro",
      "wanx_image_edit_model": "wanx2.1-imageedit",
      "text_to_video_model": "wan2.7-t2v-2026-06-12",
      "image_to_video_model": "wan2.7-i2v-2026-04-25",
      "timeout_seconds": 300
    }
  }'
```

### 开关追踪

```bash
# 关闭 LangSmith
curl -X POST "http://localhost:8000/api/v1/config/langsmith/toggle?enabled=false"

# 开启 Langfuse
curl -X POST "http://localhost:8000/api/v1/config/langfuse/toggle?enabled=true"
```

### 配置钉钉机器人

```bash
# 配置默认机器人
curl -X POST http://localhost:8000/api/v1/config/dingtalk/default \
  -H "Content-Type: application/json" \
  -d '{
    "access_token": "your-access-token",
    "secret": "SECxxxxxxxx",
    "keyword": "安全资讯",
    "enabled": true
  }'

# 配置告警机器人
curl -X POST http://localhost:8000/api/v1/config/dingtalk/alert \
  -H "Content-Type: application/json" \
  -d '{
    "access_token": "alert-bot-token",
    "secret": "SECyyyyyyyy",
    "keyword": "告警",
    "enabled": true
  }'

# 测试机器人
curl -X POST http://localhost:8000/api/v1/config/dingtalk/default/test

# 开关机器人
curl -X POST "http://localhost:8000/api/v1/config/dingtalk/default/toggle?enabled=false"
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
2. **数据库存储**: 配置存储在 MongoDB 的 `system_config` 集合中
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
