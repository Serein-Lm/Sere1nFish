# Sere1nFish

> 面向授权红队与资产情报作业的一体化自动化平台：融合 Web 采集、AI 编排、浏览器自动化与远程手机执行，统一收敛在项目维度进行编排、观测与复盘。

Sere1nFish 是一个后端 (FastAPI) + 前端 (React) 的全栈平台，通过 Docker Compose 一键部署。它把网站/小红书/抖音/公众号采集、AI Agent 编排、Chrome 浏览器自动化、以及基于 EasyTier 组网的远程手机执行整合到同一套项目工作流中，所有能力都遵循「统一化、接口化、工厂化」的设计原则。

---

## ✨ 核心能力

- **多源数据采集**：网站、小红书、抖音、公众号等采集流水线，流式、队列化、并发编排，结果按稳定 ID 增量入库。
- **资产情报接入**：FOFA / Hunter 等外部资产情报经统一适配层接入，API Key 加密托管，结果 upsert 增量入库。
- **AI 编排 (Sere1nGraph)**：基于 LangChain + `chrome-devtools` MCP 的 Agent 节点，结构化输出约束，统一 token 归因与观测。
- **浏览器自动化**：项目专属 Chrome Docker 镜像，统一 provider 接入，支持公司名规范化、根域名判定等 AI 浏览能力。
- **远程手机执行**：基于 EasyTier 组网 + ADB 的手机自动化，含设备池、预约、自动聊天与人物画像落地。
- **可观测性**：Dashboard 展示 token 消耗、任务生命周期、观测事件，长流程可回归可复盘。
- **横切能力统一入口**：配置中心、通知 Hook、技能/提示词库、下载服务等均走统一 service，业务代码只表达领域意图。

---

## 🏗️ 架构总览

```text
┌──────────────────────────────────────────────────────────────┐
│                        nginx (HTTPS 443)                       │
│              前端 Vite dev server  +  后端 /api/v1/*            │
└───────────────┬──────────────────────────────┬───────────────┘
                │                              │
        ┌───────▼────────┐            ┌────────▼─────────┐
        │   view (React) │            │  server (FastAPI)│
        │  TS/Vite/AntD  │            │  Socket.IO 挂载   │
        └────────────────┘            └────────┬─────────┘
                                              │
        ┌──────────────┬────────────┬─────────┼───────────┬────────────┐
        ▼              ▼            ▼         ▼           ▼            ▼
    MongoDB         Redis      Chrome镜像  Sere1nGraph  MediaCrawler  EasyTier
   (持久化)        (缓存/队列)  (浏览器自动化) (AI 编排)   (采集能力)    (手机组网)
```

### 后端分层 (`server/`)

| 层 | 目录 | 职责 |
|----|------|------|
| 入口 | `run.py` / `api/main.py` | 启动、路由注册、生命周期、异常处理、健康检查 |
| Router | `api/routers/*` | HTTP 形态、鉴权、请求校验、响应组装（薄层） |
| Service | `api/services/*` | 业务流程、pipeline 编排、横切服务入口 |
| DAO | `api/dao/*` | MongoDB 持久化、索引初始化、读写封装 |
| Model/Schema | `api/models/*`、`api/schemas/*` | 领域模型、请求/响应结构 |
| Core | `core/*` | 手机执行、观测、流式能力、底层 runtime |

关键子系统：`browser_manager/`（Chrome 能力）、`Sere1nGraph/`（AI 编排与 token 观测）、`MediaCrawler/` + `crawler_tools/`（采集）、`AutoGLM-GUI-main/`（手机自动化）。

### 前端分层 (`view/`)

| 层 | 目录 | 职责 |
|----|------|------|
| 页面 | `src/pages/*` | 路由级页面与业务页面（`lazy` 懒加载） |
| 组件 | `src/components/*` | 可复用组件、布局、渲染器 |
| Service | `src/services/*` | 类型化 API 调用，统一走 `http.ts` + `config/api.ts` |
| Context | `src/contexts/*` | 跨页面状态（如主题） |
| 样式/类型/工具 | `src/styles/*`、`src/types/*`、`src/utils/*` | 主题 token、共享类型与工具 |

---

## 🚀 快速开始

### 前置要求

- Docker 与 Docker Compose
- 服务器可访问外网（采集与 AI 能力依赖）

### 启动

```bash
git clone git@github.com:Serein-Lm/Sere1nFish.git
cd Sere1nFish

# 准备环境变量
cp .env.example .env
#（按需编辑 .env）

# 启动全部服务
docker-compose up -d

# 健康检查
curl -k https://127.0.0.1/health
```

启动后通过浏览器访问 `https://127.0.0.1/`。

### 服务组成 (Docker Compose)

`mongo`、`redis`、`backend`、`frontend`、`nginx`、`chrome-image`、`easytier-*`，以及一次性 `db-import`。

---

## 🔒 端口与安全边界

- **业务入口仅暴露 HTTPS `443`**；远程手机组网额外暴露 EasyTier `11010-11013`。
- MongoDB、Redis、后端开发端口、前端 Vite、Chrome 调试端口均留在 Docker 内网，不对公网开放。
- 公网安全组只开放：`443/tcp`、`11010/tcp+udp`、`11011/tcp`、`11012/tcp`、`11013/udp`。
- 禁止对外开放：`5555`、`8000`、`5173`、`27017`、`6379`、`9222`、`5900`、`6080`。
- HTTPS 证书位于 `nginx/certs/`，数据库备份等运维产物均不纳入版本控制。

---

## 🧪 开发与验证

后端 `python run.py` 以 uvicorn reload 模式运行；前端 Vite dev server 启用 HMR；nginx 通过 HTTPS 代理二者。

| 变更类型 | 推荐验证 |
|----------|----------|
| 前端 | `cd view && npm run build`，并用 `chrome-devtools` 打开 `https://127.0.0.1/` 验证渲染/交互/控制台 |
| 后端 | Python 语法/导入检查，或针对触达模块的 pytest |
| 部署 | `docker-compose -f docker-compose.yml config` |
| 运行时冒烟 | `curl -k https://127.0.0.1/health` |

接入与运维教程：

- [钉钉 Stream、Webhook、富卡片与多格式产物接入](./docs/DINGTALK_INTEGRATION_TUTORIAL.md)
- [远程手机 EasyTier 接入](./docs/REMOTE_MOBILE_EASYTIER.md)
- [运行与验收教程](./docs/RUNTIME_OPERATIONS_TUTORIAL.md)

---

## 📐 设计原则

所有模块统一遵循 **统一化、接口化、工厂化**：新增能力按 `入口 -> 接口/协议 -> factory/registry -> adapter/runtime -> DAO/service` 设计，业务代码只表达领域意图，具体实现收敛在对应统一层。详见 [`AGENTS.md`](./AGENTS.md)。

---

## 📄 许可与用途

本项目仅用于**授权范围内**的安全测试、资产情报与自动化研究。使用者须自行确保所有采集、浏览与手机执行操作均已获得合法授权。
