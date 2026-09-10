# 分布式扫描节点与 SOCKS5 代理方案

实现状态：Phase 0/1 已落地，默认关闭，可按 Project 灰度。当前可远程执行 `http_probe` 与 `browser_probe`；公司扫描写入、来源归档、Finding、手机 ADB、AI 中枢、钉钉、MongoDB 和 Redis 仍由主服务持有。

## 目标与边界

目标是在不改变 Project、Target、SourceDocument、Finding 和现有任务语义的前提下，把浏览器深扫、HTTP 探活、资源解析等可并行工作扩展到多台节点，并支持按策略使用 SOCKS5 代理。

第一阶段不分布手机 ADB、AI 中枢对话、钉钉连接、MongoDB、Redis 和配置中心。它们继续由主服务管理。远程节点不成为第二套后端，也不直接操作业务数据库。

## 推荐拓扑

```text
用户 / AI / 定时任务
        |
        v
主服务 Control Plane :443
  - RBAC / 配置 / Task
  - Scheduler / Work Queue
  - Node & Proxy Registry
  - Audit / Observability
        ^  HTTPS 长轮询 + HMAC 节点身份
        |
  +-----+-------------------+
  |                         |
Scan Node A             Scan Node B
  - Node Agent             - Node Agent
  - Chrome Pool            - Chrome Pool
  - HTTP/Parser Worker      - HTTP/Parser Worker
  - loopback proxy adapter  - loopback proxy adapter
        |                         |
        +------ Private OSS -----+
```

中心只接收控制事件和小型结构化结果。当前两类 probe 不产生大对象；后续 `resource_parse`/`website_page` 能力上线时，HTML、截图、附件必须由节点使用短时授权直接上传 OSS，再回传 `storage_object_id`、SHA-256、大小和 MIME，不能穿过工作结果 JSON。

## 已实现代码边界

| 层 | 实现 | 责任 |
| --- | --- | --- |
| API | `api.routers.distributed_scan` | 管理端总览、节点、工作项、代理配置 |
| 节点协议 | `api.routers.distributed_scan_node` | 注册、心跳、拉取、续租、进度和终态回传 |
| Service | `api.services.distributed_scan` | 网关、节点身份、工作租约、结果校验、代理策略、恢复循环 |
| DAO | `api.dao.scan_nodes/distributed_work/proxy_profiles` | 原子状态变更、索引和加密字段 |
| 节点端 | `server/scan_node_agent` | 独立 HTTPS pull agent 与 capability worker registry |
| 业务接入 | `api.services.asset_intelligence.service` | HTTP 探活和浏览器兜底探活的首个灰度入口 |
| 前端 | `view/src/pages/Infrastructure` | 真实节点、队列、SOCKS5 和本机 Chrome 状态 |

## 领域组件

### Control Plane

- `NodeRegistryService`：注册、身份轮换、能力和健康状态。
- `WorkSchedulerService`：把 Project task 拆成独立 work item，并按能力、资源和亲和性调度。
- `WorkLeaseService`：原子认领、续租、取消和过期回收。
- `ResultCommitService`：验证幂等键、对象校验和与结果 schema，再提交领域数据。
- `ProxyRegistryService`：代理配置、健康、容量、冷却和租约。
- `NodeEventService`：统一日志、进度、指标和审计。

### Scan Node

- `NodeRuntime`：只主动连接中心，维护心跳、按空闲 slot 领取工作并处理优雅取消。
- `WorkerRegistry`：当前声明 `http_probe`、`browser_probe`；worker 不感知 Project 页面、Router、MongoDB 或 Redis。
- `HttpProbeWorker`：有界并发、HTTP/HTTPS 传输回退、失败重试和 SOCKS5 接入。
- `BrowserProbeWorker`：复用一个 Chromium 进程，以独立 BrowserContext 隔离工作和代理配置。
- `ArtifactUploader` 与 loopback proxy adapter 尚未上线；它们分别随产物型 capability 和 Phase 3 代理 sidecar 实现。

## 持久化模型

实现时新增 collection 必须先在 `api/db/collections.py` 声明并由 DAO 幂等建索引。

### `scan_nodes`

```json
{
  "node_id": "node_xxx",
  "display_name": "hangzhou-01",
  "identity_fingerprint": "sha256:...",
  "status": "online|draining|offline|disabled",
  "capabilities": {"browser_probe": "1", "http_probe": "1"},
  "capacity": {"browser_probe_slots": 24, "http_probe_slots": 128},
  "usage": {"browser_probe_slots": 12, "http_probe_slots": 40},
  "labels": {"region": "cn-hangzhou"},
  "last_heartbeat_at": "...",
  "version": "..."
}
```

### `distributed_work_items`

```json
{
  "work_item_id": "work_xxx",
  "task_id": "task_xxx",
  "project_id": "...",
  "target_id": "...",
  "kind": "browser_probe",
  "payload_version": 1,
  "payload": {"urls": ["https://example.com"], "timeout": 30},
  "requirements": {"capabilities": ["browser_probe"]},
  "proxy_profile_id": "",
  "proxy_mode": "none|best_effort|required",
  "affinity_key": "target:...",
  "status": "queued|leased|running|completed|retry|failed|cancelled",
  "lease": {"node_id": "...", "token_digest": "...", "expires_at": "..."},
  "attempt": 1,
  "last_event_seq": 2,
  "idempotency_key": "..."
}
```

关键索引：`work_item_id` 唯一、`idempotency_key` 唯一、`status+available_at+priority`、`lease.expires_at`、`task_id+status`。事件序号在整个工作项生命周期内单调递增，重试租约从上一序号继续，防止迟到事件覆盖新尝试。

### `proxy_profiles`

保存名称、类型、加密 endpoint/用户名/密码、DNS 策略、并发上限、区域标签、失败阈值和冷却策略。业务 Task 只能引用 `proxy_profile_id`，不得保存解密后的 URI。

### `proxy_leases`

保存 `lease_id`、profile、node、work item、工作尝试序号、sticky key、到期时间和状态。同一 work item 重试时原子复用租约记录，避免唯一索引阻断后续尝试；原始密码不进入租约文档和日志。

## 节点协议

当前实现使用 HTTPS 长轮询，节点始终主动出站，断线恢复不依赖进程内连接状态。WSS 可作为后续降低空轮询延迟的兼容传输，但不会替换持久租约语义。协议动作：

1. `register`：一次性 bootstrap token 换取节点身份。
2. `heartbeat`：版本、capability、容量、使用量和活动工作，默认 20 秒；响应携带取消列表。
3. `lease`：节点按本机剩余 slot 主动长轮询，不由中心盲目推满。
4. `started/progress/renew`：带工作租约 token；事件使用单调 `event_seq`。
5. `completed`：先验证 URL/结果 schema 和 OSS 对象，再原子提交；相同内容可幂等重放。
6. `failed`：使用领域错误码和可重试标志，达到尝试上限后进入终态。
7. `cancel`：中心持久化取消，节点从下一次心跳获得取消指令并释放资源。

建议租约 90 秒、每 30 秒续期。网络断开后节点停止领取新任务；超过租约的工作由中心重新排队。旧节点迟到的完成事件因 lease token 不匹配而被拒绝，但已上传对象可由清理任务回收。

### HTTP API

管理 API 需要 RBAC：

- `GET /api/v1/distributed-scan/overview`
- `POST /api/v1/distributed-scan/nodes/bootstrap`
- `GET|PUT /api/v1/distributed-scan/nodes/*`
- `GET|POST /api/v1/distributed-scan/work-items/*`
- `GET|POST|PUT|DELETE /api/v1/distributed-scan/proxy-profiles/*`

节点注册仅接受一次性 bootstrap token。其他节点 API 携带 `X-Scan-Node-ID`、时间戳、随机 nonce 和 HMAC-SHA256 签名；签名覆盖 HTTP 方法、路径和正文摘要。服务端保存 token 的 SHA-256 摘要及加密密文，nonce 使用唯一索引和 TTL 阻止重放，原始 token 不在请求头或日志中传输。

## 部署扫描节点

1. 在“基础设施 -> 扫描节点”生成一次性凭据。
2. 将 `server/scan_node_agent` 目录部署到节点，使用示例 Compose 构建。
3. 设置以下环境变量并启动：

```bash
export SCAN_CONTROL_PLANE_URL=https://your-domain.example/api/v1
export SCAN_NODE_BOOTSTRAP_TOKEN=snb_xxx
export SCAN_NODE_NAME=hangzhou-01
export SCAN_NODE_LABELS='{"region":"cn-hangzhou"}'
export SCAN_NODE_HTTP_SLOTS=32
export SCAN_NODE_BROWSER_SLOTS=4
docker compose -f server/scan_node_agent/compose.example.yml up -d --build
```

注册成功后从部署环境删除 bootstrap token，保留 `scan-node-state` 卷。节点身份文件必须为 `0600`。使用私有 CA 时挂载 CA 文件并设置 `SCAN_NODE_CA_BUNDLE`；生产环境不得关闭 TLS 校验。

节点无需新增公网入站端口，只需出站访问主服务 HTTPS `443` 及目标网站。节点 Docker 建议为每个浏览器 slot 预留约 0.5-1 GiB 内存，并根据实际页面稳定性逐步提高 `SCAN_NODE_BROWSER_SLOTS`。

## 运行配置

配置保存在 MongoDB `distributed_scan` 段，首次启动会写入以下默认值：

```json
{
  "enabled": false,
  "kinds": ["http_probe", "browser_probe"],
  "project_ids": [],
  "fallback_local": true,
  "wait_seconds": 60,
  "poll_seconds": 0.5,
  "batch_sizes": {"http_probe": 20, "browser_probe": 1},
  "dispatch_concurrency": 8,
  "default_proxy_profile_id": "",
  "proxy_mode": "none"
}
```

上线时先填写单个 `project_ids` 灰度，再设 `enabled=true`。空 `project_ids` 表示所有项目，不能作为首次启用方式。`proxy_mode=required` 时必须同时设置有效代理配置；否则工作保持重试/失败，绝不直连。

## 调度策略

当前采用可信节点主动拉取：队列按 `priority -> available_at -> created_at` 排序，中心校验节点状态、capability 和工作 requirements；节点 Agent 根据本机 HTTP/Browser 空闲 slot 决定本次请求的 kind，因此不会由中心盲目推满。工作租约和最大尝试次数由中心强制执行。

`affinity_key`、节点 labels 和容量均已进入稳定协议，但按地域过滤、服务端容量硬限制、亲和性复用以及加权最少负载属于 Phase 2。上线多个异构节点前必须完成这些策略及并发竞争压测，不能把持久化字段误认为已经生效的调度算法。

Project task 保持现有状态；分布式 work item 是内部执行单元。只有所有必需 work item 到达终态且 finalizer 成功，Project task 才能完成。`partial/truncated` 仍按现有官网归档语义保存。

## SOCKS5 设计

### 统一调用语义

业务调用侧只接收：

```text
ProxyPolicy(required, profile_id, sticky_key, dns_mode, bypass_classes)
```

它不读取 Mongo 配置，也不拼接带密码的 URI。中心 `ProxyProvider` 在工作被租用后原子申请容量，解密凭据并只通过该节点的 HTTPS 租约响应短时下发；密码不写入 work item、proxy lease、日志或前端响应。节点 worker 只消费本次 `ProxyLease`，不会访问配置库。

### 浏览器兼容

Phase 1 直接使用 HTTPX/Playwright 的每任务代理配置，凭据只存在节点进程内存。HTTPX 支持带认证 SOCKS5；Playwright 官方只明确保证 SOCKS server 和 HTTP proxy 认证，因此带认证 SOCKS5 的浏览器流量必须在真实代理上单独验收。

Phase 3 会增加仅监听 `127.0.0.1` 的 sidecar（如 sing-box/等价适配器），由 sidecar 连接带认证的上游 SOCKS5，Chrome 只拿无凭据的本机短时端口。该能力上线前不能宣称浏览器带认证 SOCKS 已完成生产验收。

### DNS 与回退

- 公网目标默认远端 DNS 解析，避免本地 DNS 泄漏。
- 控制面连接不使用工作代理，天然 bypass。`bypass_classes` 已持久化并随租约下发，OSS/内网目标的具体映射随 sidecar 一起实现。
- `required=true` 时代理不可用必须返回 `proxy_unavailable`，禁止静默直连。
- `best_effort` 只能由任务策略显式选择，回退事件必须记录。

### 健康与冷却

当前健康检查覆盖 TCP 建连、SOCKS5 握手、认证、目标 CONNECT 和延迟；连续失败达到阈值后冷却，成功后清零。出口身份、目标类别可达性、认证失败立即禁用和 sticky lease 复用属于 Phase 3，不能仅根据一个业务站点的 403 判定代理死亡。

## 安全边界

- 主服务器继续只开放业务 `443` 和现有 EasyTier 端口；分布式节点不要求新增主服务器入站端口。
- 节点除运维 SSH 白名单外不开放业务入站端口。
- 节点注册 token 一次使用、短时有效；节点可禁用，HMAC 凭据可轮换。
- 当前每个 work token 仅能变更对应 work item 的租约生命周期；产物型 capability 上线时再增加绑定 work/project 前缀的 OSS 短时上传授权。
- 后续节点访问 OSS 必须使用 STS 或预签名请求，禁止复制主服务 AK/SK。
- payload、事件和日志经过字段白名单；代理密码、Cookie、Authorization、节点私钥不得记录。
- 协议已包含 agent/version；生产发布时应固定节点镜像 digest，最低兼容版本拒绝策略在 Phase 2 实现。

## 观测指标

- 节点：在线率、心跳延迟、CPU/内存/磁盘、各 capability slot。
- 队列：排队深度、等待时间、租约过期、重试和取消延迟。
- 浏览器：启动时间、页面成功率、崩溃、空截图、每任务耗时。
- 代理：可用率、握手延迟、出口变化、冷却次数、按错误类别失败率。
- 业务：按 Project/Target/source 的完成、partial 和证据提交量。

所有事件携带 `project_id/task_id/work_item_id/node_id/target_id/source/event_type`，并继续进入现有 observability 入口。

## 分阶段落地

### Phase 0：本机抽象（已完成）

- 把现有本机 Chrome/HTTP worker 包装为 capability worker。
- 仍在主服务器执行，验证 payload、结果和取消契约不改变业务结果。

### Phase 1：单远程节点（已完成基础闭环）

- 已实现 node registry、HTTPS 长轮询、租约、恢复和严格结果提交。
- 只分流无状态 HTTP 探活和单 URL 浏览器读取。
- 通过 feature flag 按 Project 灰度，失败自动回到队列，不自动直连回退代理。
- 当前 probe 不产生 OSS 产物；短时 OSS 上传授权随首个产物型 capability 一起上线。

### Phase 2：多节点调度

- 加容量、亲和性、drain、版本兼容和全链路指标。
- 压测节点失联、重复完成、中心重启、取消和 OSS 暂时失败。

### Phase 3：SOCKS5（基础能力已完成）

- 已完成加密 proxy registry、容量租约、续期/释放、SOCKS5 CONNECT 健康检查、失败冷却和 `required` 禁止直连。
- 待完成 browser loopback sidecar、出口身份检查、类别化 bypass/DNS、sticky 调度和真实代理故障注入。
- 先对显式测试任务启用，再按 source/Target 配置。

### Phase 4：stage 级分布

- 公司扫描与手机采集的本机 Plan、runtime、registry 和 stage contract 已完成；后续按稳定 contract 扩展资源解析、官网页面、OCR 等无状态能力，不迁移 finalizer 和领域写入。
- 手机 ADB 和公众号应用内发现仍保留在设备所在节点，通过已有设备租约调度；不与通用浏览器节点混为一类。

## 验收与回滚

- 相同输入在本机和远程节点得到相同规范 URL、来源版本、Finding 关联和对象校验和。
- 杀死节点后 90 秒内重新排队，Project task 不丢失、不误完成。
- 重复 `work.completed` 只提交一次。
- 取消后 Chrome、代理租约和临时文件均释放。
- 代理要求为 required 时，抓包确认没有直连和 DNS 泄漏。
- 中心和节点重启后可从持久化状态恢复。
- feature flag 可将新 work 停止分流；已有租约 drain 后回到当前本机 runtime，无需迁移业务数据。

当前已自动验证：默认关闭的本机兼容路径、真实 HTTPS 注册/心跳/租用/执行/完成闭环、结果 URL 白名单、工作幂等键、required 代理约束、身份文件权限和既有资产探活测试。远程主机、真实 SOCKS5 出口及节点故障注入需在提供节点资源后按本节验收清单执行。
