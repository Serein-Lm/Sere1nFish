<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115+-green.svg" alt="FastAPI">
  <img src="https://img.shields.io/badge/LangGraph-0.2+-purple.svg" alt="LangGraph">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
</p>

<h1 align="center">� Sere1n AI Agent</h1>

<p align="center">
  <strong>基于 LangGraph 的智能信息采集与分析平台</strong>
</p>

<p align="center">
  自动化社交媒体信息采集 · AI 驱动的内容分析 · 多模态视觉理解 · 流式实时反馈
</p>

---

## ✨ 核心特性

- 🤖 **AI Agent 工作流** - 基于 LangGraph 构建的多 Agent 协作系统
- 📱 **小红书数据采集** - 自动化搜索、笔记采集、人物画像生成
- 🖼️ **多模态视觉分析** - VL 模型驱动的截图智能分析
- 🔐 **权限管理系统** - 管理员/普通用户角色分离
- 📡 **SSE 流式输出** - 实时展示 Agent 执行过程
- 🔌 **MCP 协议支持** - 灵活的工具扩展机制

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                           Frontend (Web UI)                         │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │ HTTP/SSE
┌─────────────────────────────────▼───────────────────────────────────┐
│                         FastAPI Backend                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │  Auth API   │  │  XHS API    │  │ Project API │  │ Agent API  │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────┐
│                        Sere1nGraph (LangGraph)                      │
│                                                                     │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │                      Workflow Executor                        │ │
│   │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────────┐  │ │
│   │  │ Router  │→ │ Browser │→ │   XHS   │→ │   Copywriting   │  │ │
│   │  │  Agent  │  │  Agent  │  │  Agent  │  │      Agent      │  │ │
│   │  └─────────┘  └─────────┘  └─────────┘  └─────────────────┘  │ │
│   └──────────────────────────────────────────────────────────────┘ │
│                                  │                                  │
│   ┌──────────────────────────────▼──────────────────────────────┐  │
│   │                        Tool Layer                            │  │
│   │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │  │
│   │  │ Builtin  │  │   MCP    │  │ Crawler  │  │   Vision    │  │  │
│   │  │  Tools   │  │  Tools   │  │  Tools   │  │   Tools     │  │  │
│   │  └──────────┘  └──────────┘  └──────────┘  └─────────────┘  │  │
│   └─────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────┐
│                          Data Layer                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐ │
│  │     MongoDB     │  │      Redis      │  │   Browser Data      │ │
│  │  (持久化存储)    │  │  (Token/缓存)   │  │   (Cookie/Session)  │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🔄 小红书采集流水线

```
                    ┌─────────────────────────────────────┐
                    │       第一层：搜索结果打标          │
                    │   输入: 标题 + 简介 + 用户信息      │
                    │   输出: 关联度 + 初步攻击面识别     │
                    └──────────────┬──────────────────────┘
                                   │
                          筛选 (is_suspicious = true)
                                   │
                    ┌──────────────▼──────────────────────┐
                    │       第二层：笔记详情打标          │
                    │   输入: 笔记全文 + 评论             │
                    │   输出: 深度关联度 + 公司识别       │
                    └──────────────┬──────────────────────┘
                                   │
                          按用户聚合
                                   │
                    ┌──────────────▼──────────────────────┐
                    │       第三层：人物画像生成          │
                    │   输入: 用户所有笔记的分析结果      │
                    │   输出: 综合画像 + 攻击向量         │
                    └─────────────────────────────────────┘
```

---

## 🖼️ 视觉分析流程

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   用户输入   │     │   自动截屏   │     │   VL 分析    │     │   结构化     │
│   主页 URL   │ ──▶ │  Playwright  │ ──▶ │  多模态模型  │ ──▶ │   JSON 输出  │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
                            │                    │                    │
                            ▼                    ▼                    ▼
                     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
                     │  滚动截取    │     │  信息提取    │     │  入库存储    │
                     │  多张截图    │     │  流式输出    │     │  MongoDB     │
                     └──────────────┘     └──────────────┘     └──────────────┘
```

---

## 🚀 快速开始

### 环境要求

- Python 3.11+
- MongoDB 4.4+
- Redis 6.0+
- Chrome/Chromium (用于 Playwright)

### 1. 克隆项目

```bash
git clone https://github.com/your-org/sere1n-agent.git
cd sere1n-agent
```

### 2. 安装依赖

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium
```

### 3. 配置

运行配置统一从前端配置页写入 MongoDB，敏感字段由后端 DAO 层加密保存，读接口只返回脱敏值。

本地 `config.json` 导入流程已经下线，`POST /api/v1/config/import` 固定返回 `410 Gone`。不要再通过复制或编辑 `config.json` 配置 LLM、工具、Chrome Docker、TTS、百炼、手机 Agent 等运行参数。

服务启动后登录前端，进入“配置管理”页面维护配置：

```text
LLM: api_key / base_url / default_model / vision_model / mobile_*_model
运行配置: runtime / mobile / chrome_docker / cosyvoice / bailian / tools / mcpServers
```

开发或 Docker Compose 启动时，MongoDB、Redis、代理、EasyTier 等基础连接仍通过环境变量或 Compose 注入；业务运行参数以 MongoDB 中的加密配置为准。

### 4. 启动服务

```bash
python run.py
```

服务启动后访问：
- API 地址: http://127.0.0.1:8000
- API 文档: http://127.0.0.1:8000/docs

---

## 👥 用户权限

系统支持两种角色：

| 角色 | 权限 | 默认账户 |
|------|------|----------|
| 管理员 (admin) | 全部功能 + 系统管理 | admin / admin123 |
| 普通用户 (user) | 基本功能 | user / user123 |

默认登录 Key: `accesskey`

```
┌─────────────────────────────────────────────────────────┐
│                      管理员                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │  项目管理   │  │  数据采集   │  │   系统管理 ✓    │  │
│  └─────────────┘  └─────────────┘  └─────────────────┘  │
│                                    │  · 用户 CRUD      │  │
│                                    │  · 修改用户名     │  │
│                                    │  · 重置密码       │  │
│                                    │  · 修改登录 Key   │  │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                     普通用户                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │  项目管理   │  │  数据采集   │  │  修改自己密码 ✓ │  │
│  └─────────────┘  └─────────────┘  └─────────────────┘  │
│                                    │  系统管理 ✗ 403  │  │
└─────────────────────────────────────────────────────────┘
```

### 用户自助 API

```bash
# 修改自己的密码（需验证原密码）
POST /api/v1/auth/change-password
{"old_password": "原密码", "new_password": "新密码"}
```

### 系统管理 API（仅管理员）

```bash
# 获取用户列表
GET /api/v1/auth/users

# 创建用户
POST /api/v1/auth/users
{"username": "newuser", "password": "pass123", "role": "user"}

# 更新用户（可修改用户名、密码、角色、禁用状态）
PUT /api/v1/auth/users/{username}
{"new_username": "新用户名", "password": "新密码", "role": "admin", "disabled": false}

# 删除用户
DELETE /api/v1/auth/users/{username}

# 获取当前登录 Key
GET /api/v1/auth/login-key

# 修改登录 Key（需验证原 Key）
POST /api/v1/auth/change-login-key
{"old_key": "原key", "new_key": "新key"}
```

> 普通用户调用系统管理 API 将返回 `403 Forbidden`

---

## 📚 API 模块

| 模块 | 路径 | 说明 |
|------|------|------|
| 🔐 认证 | `/api/v1/auth` | 登录、登出、用户管理 |
| 📁 项目 | `/api/v1/projects` | 项目 CRUD |
| 📱 小红书 | `/api/v1/xhs` | Cookie 管理、搜索任务、笔记、画像 |

### 核心接口

```bash
# 登录
POST /api/v1/auth/login
{"username": "admin", "password": "admin123", "key": "accesskey"}

# 创建搜索任务
POST /api/v1/xhs/search
{"project_id": "xxx", "keyword": "目标公司", "max_notes": 50}

# 流式视觉分析
POST /api/v1/xhs/vision-analysis/stream
{"user_url": "https://www.xiaohongshu.com/user/profile/xxx"}

# 流式人物画像生成
POST /api/v1/xhs/profile/generate/stream
{"user_url": "...", "project_id": "xxx", "keyword": "目标公司"}
```

---

## 📂 项目结构

```
.
├── api/                          # FastAPI 应用
│   ├── routers/                  # 路由模块
│   │   ├── auth.py              # 认证路由
│   │   ├── projects.py          # 项目路由
│   │   └── xhs.py               # 小红书路由
│   ├── services/                 # 业务逻辑
│   │   ├── xhs_pipeline.py      # 采集流水线
│   │   └── xhs_vision_tools.py  # 视觉分析工具
│   ├── dao/                      # 数据访问层
│   ├── models/                   # 数据模型
│   ├── db/                       # 数据库连接
│   ├── auth.py                   # 认证模块
│   └── config.py                 # 配置模块
│
├── Sere1nGraph/                  # LangGraph Agent 系统
│   └── graph/
│       ├── agents/               # Agent 实现
│       │   ├── factory.py       # Agent 工厂
│       │   ├── runtime.py       # 运行时
│       │   └── streaming.py     # 流式输出
│       ├── prompts/              # Prompt 模板
│       │   ├── xhs_collect/     # 小红书采集
│       │   ├── xhs_profile/     # 人物画像
│       │   ├── web_tagging/     # 官网打标
│       │   └── copywriting/     # 文案生成
│       ├── tools/                # 工具函数
│       │   ├── builtin.py       # 内置工具
│       │   └── mcp.py           # MCP 工具
│       └── workflow/             # 工作流
│           ├── executor.py      # 执行器
│           ├── router.py        # 路由器
│           └── events.py        # 事件系统
│
├── crawler_tools/                # 爬虫工具
│   ├── xhs_crawler.py           # 小红书爬虫
│   └── xhs_tools.py             # 工具函数
│
├── docs/                         # 文档
│   ├── USER_PERMISSION_API.md   # 权限 API 文档
│   ├── XHS_NOTE_API.md          # 笔记 API 文档
│   ├── XHS_PROFILE_API.md       # 画像 API 文档
│   └── XHS_PIPELINE_ARCHITECTURE.md  # 架构文档
│
├── api/services/runtime_config.py # MongoDB 运行配置入口
├── requirements.txt              # 依赖
├── run.py                        # 启动脚本
└── README.md
```

---

## 🔧 MCP 工具扩展

系统支持通过 MCP (Model Context Protocol) 扩展工具能力：

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest"],
      "transport": "stdio"
    },
    "xhs": {
      "url": "http://localhost:18060/mcp",
      "transport": "http"
    }
  }
}
```

---

## 📖 详细文档

| 文档 | 说明 |
|------|------|
| [用户权限 API](docs/USER_PERMISSION_API.md) | 认证与权限管理 |
| [小红书笔记 API](docs/XHS_NOTE_API.md) | 笔记采集与查询 |
| [小红书画像 API](docs/XHS_PROFILE_API.md) | 人物画像生成 |
| [Pipeline 架构](docs/XHS_PIPELINE_ARCHITECTURE.md) | 采集流水线详解 |

---

## 🤝 贡献指南

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 提交 Pull Request

---

## ⚠️ 免责声明

本项目仅供学习和研究使用。使用者应遵守相关法律法规，不得将本项目用于任何非法用途。

---

## 📄 License

[MIT License](LICENSE)

---

<p align="center">
  Made with ❤️ by Sere1n Team
</p>
