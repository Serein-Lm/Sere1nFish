# 分布式扫描节点与 SOCKS5 代理方案

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
        ^  WSS/HTTPS + 节点身份
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

中心只接收控制事件和小型结构化结果。HTML、截图、附件等大对象由节点直接上传 OSS，再回传 `storage_object_id`、SHA-256、大小和 MIME。

## 领域组件

### Control Plane

- `NodeRegistryService`：注册、身份轮换、能力和健康状态。
- `WorkSchedulerService`：把 Project task 拆成独立 work item，并按能力、资源和亲和性调度。
- `WorkLeaseService`：原子认领、续租、取消和过期回收。
- `ResultCommitService`：验证幂等键、对象校验和与结果 schema，再提交领域数据。
- `ProxyRegistryService`：代理配置、健康、容量、冷却和租约。
- `NodeEventService`：统一日志、进度、指标和审计。

### Scan Node

- `NodeAgent`：只主动连接中心，维护心跳并领取工作。
- `CapabilityRegistry`：声明 `http_probe`、`browser_scan`、`resource_parse`、`ocr` 等能力及版本。
- `WorkerFactory`：按 work item 选择 worker，不感知 Project 页面或 HTTP Router。
- `LocalResourceManager`：管理 Chrome slot、CPU、内存、临时空间和优雅取消。
- `ArtifactUploader`：使用短时凭据上传 OSS，校验后删除临时文件。
- `LocalProxyAdapter`：把统一 ProxyLease 转换为 HTTP 客户端或 Chrome 可消费的 loopback 地址。

## 持久化模型

实现时新增 collection 必须先在 `api/db/collections.py` 声明并由 DAO 幂等建索引。

### `scan_nodes`

```json
{
  "node_id": "node_xxx",
  "display_name": "hangzhou-01",
  "identity_fingerprint": "sha256:...",
  "status": "online|draining|offline|disabled",
  "capabilities": {"browser_scan": "1", "http_probe": "1"},
  "capacity": {"browser_slots": 24, "http_slots": 128},
  "usage": {"browser_slots": 12, "http_slots": 40},
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
  "kind": "browser_scan",
  "payload_version": 1,
  "payload": {"source_document_id": "...", "url": "..."},
  "requirements": {"capabilities": ["browser_scan"], "browser_slots": 1},
  "proxy_profile_id": null,
  "affinity_key": "target:...",
  "status": "queued|leased|running|completed|retry|failed|cancelled",
  "lease": {"node_id": "...", "token_hash": "...", "expires_at": "..."},
  "attempt": 1,
  "checkpoint_object_id": null,
  "idempotency_key": "..."
}
```

关键索引：`work_item_id` 唯一、`idempotency_key` 唯一、`status+available_at+priority`、`lease.expires_at`、`task_id+status`。

### `proxy_profiles`

保存名称、类型、加密 endpoint/用户名/密码、DNS 策略、并发上限、区域标签、失败阈值和冷却策略。业务 Task 只能引用 `proxy_profile_id`，不得保存解密后的 URI。

### `proxy_leases`

保存 `lease_id`、profile、node、work item、sticky key、到期时间和状态。原始密码不进入租约文档和日志。

## 节点协议

节点通过 WSS 建立控制流，HTTPS 作为断线恢复和对象授权通道。建议协议动作：

1. `register`：一次性 bootstrap token 换取节点身份。
2. `hello`：版本、capability、容量和 nonce 签名。
3. `heartbeat`：当前使用量、租约和资源指标，默认 30 秒。
4. `lease.request`：节点按可用 slot 主动拉取，不由中心盲目推满。
5. `lease.granted`：返回 work item、短时执行 token 和必要对象引用。
6. `work.started/progress/checkpoint`：带单调 `event_seq`，服务端幂等消费。
7. `work.completed`：先核验产物对象，再原子提交结果。
8. `work.failed`：使用领域错误码和可重试标志。
9. `work.cancel`：中心传播取消，节点释放浏览器、代理和临时文件。

建议租约 90 秒、每 30 秒续期。网络断开后节点停止领取新任务；超过租约的工作由中心重新排队。旧节点迟到的完成事件因 lease token 不匹配而被拒绝，但已上传对象可由清理任务回收。

## 调度策略

调度顺序：

1. 过滤具备 capability 和兼容 payload 版本的在线节点。
2. 过滤剩余 slot、内存和临时空间不足的节点。
3. 应用地区、数据边界和代理要求。
4. 优先复用相同 `affinity_key` 的节点，利用连接、DNS 和浏览器缓存。
5. 按加权最少负载选择，避免单纯轮询造成大任务堆积。

Project task 保持现有状态；分布式 work item 是内部执行单元。只有所有必需 work item 到达终态且 finalizer 成功，Project task 才能完成。`partial/truncated` 仍按现有官网归档语义保存。

## SOCKS5 设计

### 统一调用语义

Worker 只接收：

```text
ProxyPolicy(required, profile_id, sticky_key, dns_mode, bypass_classes)
```

它不读取 Mongo 配置，也不拼接带密码的 URI。`ProxyProvider` 负责申请租约，`LocalProxyAdapter` 返回节点本机短时 endpoint。

### 浏览器兼容

Chromium 对带认证 SOCKS 的行为和扩展认证方式不应泄漏到业务层。节点运行只监听 `127.0.0.1` 的 sidecar（如 sing-box/等价适配器），sidecar 连接加密配置中的上游 SOCKS5；Chrome 只拿无凭据的本机短时端口。端口随租约释放，启动参数和进程列表不出现密码。

### DNS 与回退

- 公网目标默认远端 DNS 解析，避免本地 DNS 泄漏。
- 中心域名、OSS 内网 endpoint、EasyTier/ADB 和节点健康检查明确 bypass。
- `required=true` 时代理不可用必须返回 `proxy_unavailable`，禁止静默直连。
- `best_effort` 只能由任务策略显式选择，回退事件必须记录。

### 健康与冷却

健康维度分为 TCP 建连、代理握手、出口身份、目标类别可达性和延迟。一次业务站点 403 不能直接判代理死亡；认证失败立即禁用，连续网络失败达到阈值后冷却，冷却结束重新探测。sticky lease 在 Target/站点任务期间尽量保持同一出口。

## 安全边界

- 主服务器继续只开放业务 `443` 和现有 EasyTier 端口；分布式节点不要求新增主服务器入站端口。
- 节点除运维 SSH 白名单外不开放业务入站端口。
- 节点注册 token 一次使用、短时有效；节点证书可吊销和轮换。
- 每个 work token 仅允许读取该工作需要的对象、上传指定前缀并提交对应 work item。
- OSS 使用 STS 或预签名请求，禁止复制主服务 AK/SK。
- payload、事件和日志经过字段白名单；代理密码、Cookie、Authorization、节点私钥不得记录。
- 节点镜像固定 digest，协议包含 agent/version，中心可以拒绝过旧版本。

## 观测指标

- 节点：在线率、心跳延迟、CPU/内存/磁盘、各 capability slot。
- 队列：排队深度、等待时间、租约过期、重试和取消延迟。
- 浏览器：启动时间、页面成功率、崩溃、空截图、每任务耗时。
- 代理：可用率、握手延迟、出口变化、冷却次数、按错误类别失败率。
- 业务：按 Project/Target/source 的完成、partial 和证据提交量。

所有事件携带 `project_id/task_id/work_item_id/node_id/target_id/source/event_type`，并继续进入现有 observability 入口。

## 分阶段落地

### Phase 0：本机抽象

- 把现有本机 Chrome/HTTP worker 包装为 capability worker。
- 仍在主服务器执行，验证 payload、结果和取消契约不改变业务结果。

### Phase 1：单远程节点

- 实现 node registry、WSS、租约和短时 OSS 上传。
- 只分流无状态 HTTP 探活和单 URL 浏览器读取。
- 通过 feature flag 按 Project 灰度，失败自动回到队列，不自动直连回退代理。

### Phase 2：多节点调度

- 加容量、亲和性、drain、版本兼容和全链路指标。
- 压测节点失联、重复完成、中心重启、取消和 OSS 暂时失败。

### Phase 3：SOCKS5

- 上线 proxy registry、sidecar adapter、租约、健康和 DNS 策略。
- 先对显式测试任务启用，再按 source/Target 配置。

### Phase 4：stage 级分布

- 在公司扫描与手机交接 pipeline stage 化后，扩展资源解析、OCR 等能力。
- 手机 ADB 和公众号应用内发现仍保留在设备所在节点，通过已有设备租约调度；不与通用浏览器节点混为一类。

## 验收与回滚

- 相同输入在本机和远程节点得到相同规范 URL、来源版本、Finding 关联和对象校验和。
- 杀死节点后 90 秒内重新排队，Project task 不丢失、不误完成。
- 重复 `work.completed` 只提交一次。
- 取消后 Chrome、代理租约和临时文件均释放。
- 代理要求为 required 时，抓包确认没有直连和 DNS 泄漏。
- 中心和节点重启后可从持久化状态恢复。
- feature flag 可将新 work 停止分流；已有租约 drain 后回到当前本机 runtime，无需迁移业务数据。
