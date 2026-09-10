# 后端架构审查与演进基线

审查日期：2026-09-10

## 结论

当前系统的核心采集、手机、AI、对象存储和恢复机制已经具备清晰的领域入口，能够继续在线运行。主要风险不是“缺少架构”，而是若干早期大型模块同时承担编排、策略、适配和数据转换，后续继续直接追加功能会放大回归范围。

本轮先处理了风险最高且重复最明显的项目任务入口：JSON 下发、文件下发、任务类型选择、参数校验、持久化和后台启动已汇入统一 service；各任务实现由声明式 registry 选择。`project_api.py` 从 1626 行降到 1139 行，任务入口具备独立契约测试。

本轮进一步落地了分布式扫描的 Phase 0/1，而不是把现有大型 pipeline 复制到远端：新增统一执行网关、节点注册与鉴权、持久化工作租约、结果校验、SOCKS5 配置/租约和独立节点 Agent。现有资产发现中的 HTTP 探活与浏览器兜底探活已接入网关；数据库开关默认关闭，配置不可达时也明确保持本机执行，因此原扫描路径和结果契约不变。

没有发现需要暂停当前扫描任务的 P0 数据损坏问题。后续拆分应保持小步迁移，不能为了目录整洁重写正在工作的采集 pipeline。

## 当前基线

### 已形成的稳定边界

- 配置：`api.services.runtime_config` 与加密配置 DAO。
- 对象存储：`api.storage.ObjectStorageService`。
- 浏览器：`browser_manager` provider 与 Chrome 池。
- 来源归档：`api.services.source_documents` provider/factory/version。
- 手机动作：`core.mobile` dispatcher、设备池和采集 runtime。
- 通知：统一 notification hook/service。
- 任务执行：`api.services.project_task_runtime` 的认领、心跳、暂停和恢复。
- 任务提交：`api.services.project_tasks` 的 registry、validation、dispatcher 与 service。
- AI 观测：统一 observation context 与 token tracker。
- 分布式执行：`api.services.distributed_scan` 网关、registry、work/proxy lease 和节点协议。

### 规模热点

| 模块 | 行数 | 主要职责混合 | 优先级 |
| --- | ---: | --- | --- |
| `api/services/company_scan_pipeline.py` | 3783 | 公司身份、渠道编排、恢复、通知、汇总 | P1 |
| `core/mobile/collect/pipeline.py` | 2374 | 导航、候选判断、详情、证据、状态 | P1 |
| `api/services/targets.py` | 2240 | Target 命令、查询、聚合、迁移兼容 | P1 |
| `api/routers/mobile.py` | 2197 | HTTP、ADB/流控制、设备行为 | P1 |
| `browser_manager/provider.py` | 1934 | 池、容器、会话、健康和协议 | P2 |
| `api/services/source_documents/service.py` | 1864 | 来源编排、版本、媒体和恢复 | P2 |
| `api/services/target_research.py` | 1794 | 检索、核验、关系、扫描触发 | P2 |
| `api/services/url_scan_pipeline.py` | 1659 | 探活、浏览器、分析、Finding | P2 |

行数不是单独的缺陷。真正需要拆分的是多个变更原因共享可变状态、无法独立测试，或 Router 直接承担领域规则。

## 主要发现

### P1：大型 pipeline 的阶段边界仍是隐式的

`company_scan_pipeline` 和手机采集 pipeline 已有阶段概念，但较多状态通过大对象和内部函数传递。修改单一渠道时容易触发整条 pipeline 回归。

目标结构：

```text
command/service
  -> plan builder
  -> stage registry
  -> stage runtime + resource lease
  -> checkpoint writer
  -> result projector
```

每个 stage 使用带版本的输入/输出 schema，只读取所需状态；恢复逻辑由 runtime 统一决定，stage 不自行猜测前序是否完成。

### P1：部分 Router 仍直接访问 MongoDB

`project_api`、`mobile`、`observability`、`skills`、`prompts` 仍有局部 collection 查询。简单只读投影不是立即故障，但会让索引、兼容字段和授权过滤散落。

迁移原则：

1. 新增查询必须进入 DAO/service。
2. 触达旧查询时先补响应契约测试，再迁移该查询，不整文件重写。
3. Router 只保留 HTTP 状态码和 schema 转换。
4. 聚合投影由 read service 统一提供，前端不拼多个全量接口。

### P1：任务触发路径曾重复实现

此前 JSON 和上传任务分别实现 task type 判断、Skill 校验、公司参数校验、Mongo 插入和后台启动，存在行为漂移风险。该项本轮已修复：

- `project_tasks.registry`：任务定义和 capability 元数据。
- `project_tasks.validation`：按任务类型规范化参数。
- `project_tasks.service`：项目校验、入库、执行。
- `project_tasks.dispatchers`：底层 pipeline 适配。
- `dao.tasks.insert_task`：统一单任务持久化。

### P2：后台协程需要区分“受控 worker”和“丢弃任务”

静态搜索到的 `asyncio.create_task` 不能一律替换。心跳、WebSocket 双向转发、队列 worker 等只要被 owner 持有、取消并 await，就是正确的生命周期实现。禁止的是创建后没有引用、没有异常消费、也无法恢复的任务。

审查每个调用点时必须验证：

- owner 保存引用；
- shutdown/cancel 分支结束任务；
- 异常被记录或传播；
- 需要跨进程恢复的业务状态已持久化；
- API 提前 ACK 后不会只依赖当前进程内存。

### P2：避免形成万能 util

“小工具统一复用”不等于把所有逻辑放进 `utils.py`。纯规范化、哈希、时间和分页可进入 util；涉及 Mongo、网络、权限、模型、设备或领域状态的功能必须留在 DAO/service/adapter。

### P1：分布式边界必须停留在无状态能力层（本轮已建立）

已完成的边界：

- `DistributedExecutionGateway` 是业务调用侧唯一入口；feature flag、Project 灰度、批次、等待和本机回退都收敛在网关。
- `NodeRegistryService` 只处理一次性 bootstrap、节点身份、心跳和令牌轮换。
- `work_service` 只处理工作排队、租约、单调事件、结果提交、取消和恢复。
- `proxy_service` 只处理 SOCKS5 配置、CONNECT 健康、容量、冷却和随工作续期的租约；浏览器 loopback sidecar 与类别化 DNS/bypass 明确留在后续阶段。
- `scan_node_agent.WorkerRegistry` 只按 capability 选择 worker，不访问 Project、Target、MongoDB 或 Redis。
- HTTP 与浏览器 probe 的远端结果先按版本化白名单校验；URL 集合、结果大小和 OSS 引用不满足租约时拒绝提交。

当前没有远程化公司 finalizer、Target 关系写入、Finding 写入、手机 ADB 和 AI 对话。这些仍由主服务持有，避免形成跨节点多写者和网络耦合。

### P2：分布式加速不能成为主链路单点（本轮已修复）

分布式配置默认 `enabled=false`。启用后仍按 `project_ids` 灰度；没有健康节点、节点超时或配置中心暂时不可用时，仅在策略允许时回退现有本机 adapter。`proxy_mode=required` 永远不允许直连回退。这个规则由网关和代理 service 强制执行，不由各 pipeline 自行判断。

## 分阶段拆分顺序

### 阶段 A：任务边界（已完成）

- 统一任务 registry、validation、service、dispatcher。
- JSON 与文件任务同路。
- 前端暴露既有完整扫描参数。
- 保留运行中任务的持久化语义。

### 阶段 B：公司扫描 stage 化

1. 抽出 `CompanyScanPlan` 与参数 schema。
2. 把 identity、assets、website、wechat、scholar、bidding、control 变成注册 stage。
3. 把渠道检查点读写收敛到 checkpoint repository。
4. 把汇总和通知移到 finalizer。
5. 对暂停、恢复、单渠道失败和重复执行建立契约测试。

一次只迁移一个 stage；新旧实现并行时由 registry 配置选择，不在业务代码中写渠道名称分支。

### 阶段 C：手机采集 runtime 分离

1. 导航与确定性动作归 `MobileCommandDispatcher`。
2. 平台搜索策略归 adapter。
3. 候选评分、详情审查、链接交接分别成为 stage。
4. 截图/证据写入统一 evidence service。
5. 设备租约、ADB 恢复和任务恢复由 runtime 持有。

### 阶段 D：Target 命令与读模型

1. 将写操作、关系解析、批次继承移入 command service。
2. 将项目看板、Target 搜索、模块数量移入 read service。
3. DAO 只处理稳定查询和索引。
4. 通过投影版本保持旧前端字段兼容。

### 阶段 E：分布式执行（Phase 0/1 已完成）

已完成：

1. 节点、工作项、事件、代理配置和代理租约 collection 及索引。
2. 一次性节点引导、令牌加密/摘要存储、HMAC 请求签名与 nonce 防重放、心跳、drain/disable 和轮换。
3. 90 秒工作租约、续期、过期重排、最大尝试次数、取消传播和幂等完成。
4. HTTP/浏览器 probe worker、Docker 节点模板和管理端真实状态页。
5. 资产发现 HTTP 探活及浏览器兜底通过统一网关按 Project 灰度。

后续迁移顺序仍受阶段 B/C 约束：先稳定 stage schema，再逐项增加 `resource_parse`、`website_page`、`ocr` 等 capability。不能把 `company_scan_pipeline` 或手机主循环整体复制到节点。

## 本轮验证

- `test_distributed_scan.py` 覆盖配置限幅、本机兼容回退、代理 required 约束、代理重试租约复用、最大尝试次数、工作幂等、结果 URL 白名单、HMAC nonce 防重放和节点身份文件权限。
- 既有 `test_asset_intelligence.py` 全量回归，确认网关接入没有改变本机探活、HTTP/HTTPS 选择和浏览器恢复行为。
- 使用真实 FastAPI、MongoDB 和 HTTPS 路由完成 `bootstrap -> register -> heartbeat -> lease -> started -> execute -> completed`，并在完成后清理验证数据。
- 扫描节点 Docker build context 已限制在独立 Agent 目录，避免把整个后端工作区和运行数据发送给 Docker daemon。
- 前端 TypeScript/Vite 构建及 Chrome DevTools 桌面/窄屏验收属于发布必检项。

## 每次拆分的完成标准

- 调用侧只依赖稳定接口，不导入具体 Provider。
- JSON、上传、定时、AI 和外部渠道复用同一 service。
- 新增字段有 schema、DAO、索引和兼容策略。
- 单元测试覆盖正常、无数据、失败、取消、恢复和幂等。
- 日志含 `project_id/task_id/target_id/source/event_type`。
- 没有新增公网端口或明文 secret。
- 当前扫描任务无需迁移或重启即可继续；必须重启时先验证恢复检查点。
