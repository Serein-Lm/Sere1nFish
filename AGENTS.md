# Sere1nFish 开发指南

本文件是 `/root/Sere1nFish` 工作区的最高优先级开发约束。执行任何代码、部署、文档或配置修改前，先按本文确认项目基础信息、设计原则、分层归属、前后端规则、验证方式和 Git 提交方式。

## 最高优先级：设计原则与范式

- 这是整个项目的默认设计约束，不是某个模块的局部规则。后端、前端、部署脚本、手机执行、AI 编排、采集流水线、配置中心、通知 Hook、观测日志等所有模块都按同一套原则演进。
- 所有模块都应优先遵循统一化、接口化、工厂化的设计原则。新增能力先判断它属于哪一个统一能力层，再通过清晰接口接入；不要在业务流程里散落具体实现、第三方 SDK、临时函数或重复逻辑。
- 业务代码只表达业务意图和领域事件，例如“任务失败”“需要通知”“读取配置”“执行手机动作”。具体实现由对应 service、factory、adapter、DAO 或 runtime 层完成。调用侧不应感知具体通道、存储、模型、设备协议、浏览器实现或第三方服务细节。
- 新增外部能力时先建立稳定抽象，再接具体实现。典型结构是：统一入口负责校验和编排，接口/协议定义调用语义，工厂选择具体实现，适配器封装第三方细节，DAO/service 负责数据和领域行为。
- 后续扩展应优先通过注册、配置、策略、工厂或适配器完成，而不是在业务代码中持续增加 `if/else` 特例。确实需要分支时，把分支收敛在统一层内部，并保持调用侧语义稳定。
- 可读性优先于炫技抽象。统一层必须让调用方更短、更清晰、更可测试；如果抽象只增加绕路而没有减少耦合、重复或风险，就不要添加。
- 配置、通知、AI 模型、技能/提示词、手机执行、浏览器、采集 pipeline、观测日志等横切能力必须通过统一服务入口接入，避免模块之间直接互相调用底层实现。
- 采集流水线、AI 执行和手机执行等长流程必须保持流式、队列化和并发编排。主流程不得退回批量串行 `for` 循环；平台差异、账号池、代理、限速、重试和失败恢复应收敛在 tool、runtime、stage 或 factory 内，以保证扫描速率、可读性和可回归测试。
- 当局部实现习惯与本章冲突时，以本章为准；先调整方案，再写代码。
- 先收敛语义，再实现细节：调用侧使用“做什么”的接口，实现层处理“怎么做”。
- 新增能力按 `入口 -> 接口/协议 -> factory/registry -> adapter/runtime -> DAO/service` 的顺序设计，实际文件可按复杂度裁剪。
- 优先扩展已有 service、factory、registry、DAO 和配置模型；只有当已有抽象无法表达新语义时才新增抽象。
- 分支逻辑尽量位于统一层内部。业务流程中的分支只保留领域含义，不出现第三方名称、协议细节或存储字段拼接。
- 错误、日志、通知、观测事件应携带稳定的领域上下文，例如 `project_id`、`task_id`、`device_id`、`source`、`event_type`，避免只记录底层异常字符串。
- 新增 public API、前端 service 或持久化字段时，优先保证向后兼容；必须破坏兼容时，在提交说明和交接中明确迁移影响。
- 不为了“统一”做空抽象。没有减少重复、耦合或测试成本的抽象不要引入。

## 项目基础信息

- 工作区根目录：`/root/Sere1nFish`。
- 后端源码目录：`server`，对应项目 `Sere1nFishServer`。
- 前端源码目录：`view`，对应项目 `Sere1nFishView`。
- 当前运行时部署使用 Docker Compose，核心文件位于根目录：`docker-compose.yml`、`compose.yaml`、`.env`、`.env.example`、`nginx/`、`downloads/`。
- Compose 服务包含 MongoDB、Redis、后端、前端、nginx、EasyTier，以及一个由后端管理的独立 Chrome 浏览器镜像。
- 业务入口只暴露 HTTPS `443`；远程手机组网额外暴露 EasyTier 固定端口 `11010-11013`。
- MongoDB、Redis、后端开发端口、前端 Vite 端口、Chrome 调试端口都应留在 Docker 内部网络中，不对公网开放。
- 运行时源码挂载关系：

```text
/root/Sere1nFish/server    -> 后端容器 /app
/root/Sere1nFish/view      -> 前端容器 /app
/root/Sere1nFish/downloads -> 后端容器 /srv/downloads，只读
```

## 代码架构速览

- 后端入口：`server/run.py` 启动 `api.main:socket_app`，通过 Uvicorn reload 模式运行。
- FastAPI 应用：`server/api/main.py`，集中注册 `/api/v1/*` 路由、生命周期初始化、统一异常处理、`/health` 健康检查和 Socket.IO 挂载。
- 后端 API 分层：
  - `server/api/routers/*`：HTTP 形态、鉴权依赖、请求校验和响应组装。
  - `server/api/services/*`：业务流程、pipeline 编排、横切服务入口。
  - `server/api/dao/*`：MongoDB 持久化、索引初始化、数据读写封装。
  - `server/api/db/*`：数据库连接和 collection 名称声明。
  - `server/api/models/*`、`server/api/schemas/*`：领域模型、请求/响应结构、分页等共享 schema。
  - `server/core/*`：手机执行、观测、流式能力、底层 runtime 和通用基础能力。
- 重要后端子系统：
  - `browser_manager/`：项目 Chrome Docker 浏览器能力。
  - `Sere1nGraph/`：AI 编排和 token 观测相关能力。
  - `MediaCrawler/`、`crawler_tools/`：采集能力和外部 crawler 适配。
  - `AutoGLM-GUI-main/`：手机自动化相关上游/集成能力，内部有独立 `AGENTS.md` 时按其局部规则处理。
- 前端入口：`view/src/App.tsx`，React Router 路由集中注册，路由级页面通过 `lazy` 懒加载。
- 前端 API 层：`view/src/services/*`，统一通过 `view/src/services/http.ts` 和 `view/src/config/api.ts` 管理请求、鉴权和端点。
- 前端主要分层：
  - `view/src/pages/*`：路由级页面和业务页面。
  - `view/src/components/*`：可复用组件、布局、登录、渲染器等。
  - `view/src/services/*`：类型化 API 调用和前端数据访问入口。
  - `view/src/contexts/*`：跨页面状态，如主题。
  - `view/src/styles/*`：全局主题和样式 token。
  - `view/src/types/*`、`view/src/utils/*`：共享类型与工具。

## Project、Target 与来源文档关系

- `Project` 是一次工作的组织边界，承载任务、搜索目标和项目内展示；它不是公司实体，也不能作为跨项目公司的唯一标识。
- `Target` 是跨项目复用的全局目标实体，当前主要表示公司或机构，持久化在 `targets`。公司规范化结果通过 `target_id` 归入同一实体，名称别名和根域名只用于解析，调用侧使用稳定 `target_id`。
- `ProjectTarget` 是项目关注某个 Target 的关系，持久化在 `project_targets`，保存搜索词、目标、采集任务和最近增量采集时间。一个 Project 可关联多个 Target，一个 Target 也可被多个 Project 复用。
- `SourceDocument` 是按规范 URL 全局去重的来源文档，持久化在 `source_documents`；同一文章在不同项目或 Target 中只保存一份来源身份。
- `SourceDocumentVersion` 是按稳定正文哈希生成的内容版本，持久化在 `source_document_versions`。版本的内容身份不可变，但允许幂等补齐同一版本中曾下载失败的图片等证据。原始响应 HTML、渲染 DOM、原图、图片识别、浏览器截图和结构化来源 JSON 通过私有 OSS 对象引用永久保存；历史记录必须按自身 `version_id` 读取，不能静默切换到最新版本。
- `SourceDocumentLink` 是 Project、Target、任务、关键词发现某个文档的关系，持久化在 `source_document_links`。任务字段、主体对应度和相关性评分属于该场景关联，不得写成全局来源事实；相同场景可按分析指纹复用，不同 Target 必须独立分析。
- `Finding` 是从来源证据派生的项目级事实。联系方式 Finding 必须保留 `target_id`、`source_document_id`、`source_document_version_id`、原文 URL、联系方式邻近上下文和证据引用；同一联系方式的多次发现累计 evidence，不覆盖历史来源。
- 公众号深采采用“手机发现、浏览器读取”的职责划分：手机只负责应用内搜索、命中文章和复制真实链接；链接交给 `api.services.source_documents` 的 Provider registry，由项目 Chrome 池读取全文和媒体。浏览器读取失败时才回退原有手机逐屏深采。
- 来源版本层只保存文章自身事实和证据；ProjectTarget 关联层保存搜索场景和任务分析；手机采集记录保存本次任务结果；前端按项目过滤记录并可按 Target 聚合。禁止在这些层之间复制原始 HTML 或把搜索关键词自动当作公司名。

## 分层规范与记录

- 新增能力前先记录归属：它是 API 表面、领域服务、持久化、运行时适配、前端页面、前端 service、部署配置还是测试工具。
- Router/page 层只做输入输出适配，不承载可复用业务规则。
- Service 层表达领域动作和流程编排，例如创建任务、运行 pipeline、发送通知、读取配置、执行手机动作。
- DAO 层负责 collection、查询条件、索引、更新语义和数据兼容，不向上暴露 MongoDB 细节。
- Adapter/runtime 层封装第三方 SDK、设备协议、浏览器容器、AI 模型、网络客户端和外部系统差异。
- Factory/registry/strategy 层负责选择具体实现；调用侧只依赖稳定接口。
- 配置、通知、AI 模型、技能库、提示词库、设备池、浏览器、观测日志、下载等横切能力必须有统一入口，禁止页面、router 或 pipeline 直接绕过统一层调用底层实现。
- 新增目录、collection、环境变量、Compose 服务、外部端口或长期运行进程时，同步更新本文或对应模块文档，避免下一次修改反复重新摸索项目结构。

## 后端关键设计规则

- API router 保持薄层：负责路径、鉴权、请求/响应模型、HTTP 错误码和调用 service/DAO；不要在 router 中堆业务流程。
- 数据库访问放在 `api/dao/*` 或专门持久化模块中。除非路由刻意保持极薄且逻辑完全局部，否则不要在 router 直接访问 collection。
- 新增 MongoDB collection 必须先在 `api/db/collections.py` 声明常量，再在应用启动或 DAO `ensure_indexes` 中幂等初始化索引。
- 共享领域行为放在 `api/services/*` 或 `core/*`。pipeline、auth、device、observability、browser orchestration、runtime config 不要重复实现。
- 通知类能力必须走统一通知 Hook/Service，例如 `api.services.notifications.notify_event` 或 `notify_event_background`。业务流程只表达事件、级别、标题和上下文，不直接 import 钉钉、邮件、Webhook 等具体通道。
- 配置读取和敏感字段处理应通过 `api.services.runtime_config`、`api.dao.config`、配置加密工具或既有配置入口接入，不在业务模块散落解析逻辑。
- AI 技能、提示词、模型客户端和 AIGC 能力应通过技能/提示词库、runtime service 或模型适配层接入；业务模块不要直接绑定单一模型供应商。
- 手机相关能力通过 `core/mobile/*`、设备池、预约 DAO、mobile router/service 统一接入；不要在业务流程里直接写 ADB、EasyTier 或设备协议细节。
- 手机附件、图片和音频传递统一通过 `api.services.mobile_transfer` 接入：先写私有对象存储留档，再按媒体类型推送到 Android 公共媒体目录并触发媒体扫描；上传临时文件必须在成功、失败和取消分支释放，页面不得直接执行 ADB。
- 浏览器相关能力通过 `browser_manager` 和后端统一 provider 接入；不要在业务代码中临时启动独立 Chrome 或暴露调试端口。
- 截图、Word 产物、语音上传和受保护下载文件统一通过 `api.storage.ObjectStorageService` 写入；业务集合只保存 `storage_object_id`，不得直接调用 OSS SDK 或拼接 Object Key。读取按对象元数据选择 Provider，允许迁移期本地与 OSS 对象并存。
- 对象存储 Bucket 必须为私有读写，服务端使用内网 Endpoint，浏览器下载使用短时签名 URL，图片通过鉴权 API 读取。AK/SK 只存 MongoDB 加密配置，不写入环境文件、日志、迁移报告或 Git。
- 观测能力通过 `core/observability`、`Sere1nGraph` token tracker 或统一日志入口接入；新增长流程应记录开始、结束、失败和关键资源标识。
- LLM token 归因必须通过 `core.observability.observation_context` 包裹 AI 调用(浏览器 agent、结构化解析、修复重试),携带 `project_id/task_id/phase/agent/task_type`;不要直接操作 `TokenTracker`。凡是新接入的 AI 链路(人设采集、公司规范化、采集分析、手机规划等)都要确认 token 与日志观测已连通,可在 Observability/Dashboard 看到归因。
- 已知缺口:`AutoGLM-GUI-main` 手机执行器使用原生 OpenAI 客户端,绕过 LangChain 回调,其 token 暂未纳入统一 tracker;修改该 vendored 代码风险高,接入前先评估影响并在交接说明。
- 后台 fire-and-forget 任务统一用 `core.background.spawn_background` 启动,避免任务被 GC 回收并统一记录异常;不要在业务代码里散落裸 `asyncio.create_task` 且不持引用。
- 采集/分析等长流程应提供「试跑预览(dry_run)」能力:仍执行导航、截屏、结构化,但不入库、不发通知,而是把结构化结果收集到返回值的 `preview` 列表,供前端评估效果。dry_run 分支收敛在 runtime/stage 内(如 `run_collect_task(dry_run=True)`),调用侧只切换标志,不复制流程。
- 新增 API 表面优先使用明确的请求/响应模型。响应结构要稳定，字段命名与既有 API 保持一致。
- 配置项默认从环境变量、Mongo 托管配置或配置示例进入系统；不要硬编码 secret、登录密钥、API key 或仅本地可用的凭据。
- 后端异步流程不要阻塞事件循环；外部 I/O、长任务、流式响应和后台任务要沿用现有 runtime/pipeline 模式。
- 生命周期初始化放在 `api.main` 或对应 service/DAO 的幂等初始化函数里；不要靠首次请求隐式创建关键索引或全局状态。
- 公司名规范化、根域名判断等 AI 浏览器能力必须复用 `Sere1nGraph` 的 `create_agent_node` + `chrome-devtools` MCP，禁止另起浏览器；AI 输出必须用结构化 schema（如 `CompanyNormalization`）约束并落库元信息（如 `company_meta`）。
- 外部资产情报（FOFA/Hunter 等）经 `api.services.asset_intelligence` 统一协议、工厂和 Provider 接入，底层查询复用 `crawler_tools/*_tools.py`；API Key 走 `api.dao.config` 的 tools 分类加密存储。候选 URL 必须先跨来源规范化去重和并发存活探测，再按稳定 `asset_id` 增量 upsert 到 `fofa_assets`。普通任务默认深扫本轮发现的全部存活资产；只有用户显式选择增量扫描时，才仅深扫新增或发生实质变化且存活的资产。已在资产发现阶段完成探活的 URL 必须复用结果，不得在深扫入口重复探活。工具 Key 有效性探测统一走 `api.services.tool_key_test` 分派，各工具校验收敛在其 `validate_key`。
- 信息采集并发预算统一由 MongoDB `collection_runtime` 配置段和 `api.services.info_collection.tuning` 加载、校验与限幅；任务参数只做单次覆盖。浏览器 worker 数必须小于 Chrome 池上限，为公司规范化、公众号和其他并行任务保留容量；小红书搜索并发必须同时受任务上限、关键词数和当前可用账号数约束。
- 新增 MongoDB collection（如 `fofa_assets`、`company_meta`）先在 `api/db/collections.py` 声明常量，再在 `api.main` 生命周期或 DAO `ensure_indexes` 中幂等建索引。
- 招投标数据通过 `crawler_tools.tianyancha_tools` 查询规范化法定主体，由 `api.services.bidding_pipeline` 统一归档供应商原始 JSON、正文、详情页和附件到 OSS，并按稳定 `record_id` 写入 `bidding_records`；记录累积 `target_ids/project_ids` 供公司与项目双向检索，重采集失败不得覆盖此前成功归档的证据引用。后续视觉识别、Finding 和话术生成复用 `UrlScanPipeline`，禁止另建平行分析链路。

## 前端关键设计规则

- 前端使用 React、TypeScript、Vite、React Router 和 Ant Design。新增源码优先使用 `.tsx`/`.ts`，保持类型明确。
- API 调用必须放在 `src/services/*` 中，页面和组件消费带类型的 service 函数；不要在页面里内联拼接 URL 或直接散落 `fetch`。
- 请求鉴权、错误处理和下载鉴权复用 `src/services/http.ts`；API 基础地址和端点集中在 `src/config/api.ts` 或对应 service 中维护。
- 新路由通过 `src/App.tsx` 添加，并对路由级页面使用 `lazy` 懒加载。
- 大页面、设备工具、可观测性、编辑器、视频流、图表和 AI 对话类功能应通过动态导入排除在初始 bundle 之外。
- 页面层负责状态组合、交互流程和展示；复杂数据转换、轮询、流式连接、下载、上传等逻辑应收敛到 service、hook 或专门工具。
- 项目维度的数据面(网站/小红书/抖音/公众号/手机操作)统一作为 `ProjectDetail` 的 tab 接入,复用带类型的 service 按 `project_id` 过滤,不新开独立路由页;新增数据源优先加 tab 并复用既有采集/记录 service。
- 长耗时预览(如采集「试跑」/dry-run)使用抽屉承载,明确加载态与「不入库、不发送通知」提示,预览结果只读展示;截图等鉴权资源统一走 service 的鉴权 blob 取图,不在页面内联拼 URL。
- 添加新抽象前，优先复用现有 layout、theme token、Ant Design 组件模式和 `src/components/*` 中的通用组件。
- UI 文案默认中文；保留项目既有英文术语，如 API、Agent、Prompt、Skill、Dashboard、Token。避免同一位置中英重复解释。
- 表单、表格、弹窗、抽屉、筛选器、分页、状态标签和错误提示优先遵循 Ant Design 交互范式，保证加载态、空态、错误态和禁用态完整。
- 页面元素应有稳定尺寸和响应式约束，避免按钮文字、表格列、卡片内容在桌面或移动视口互相遮挡。
- 前端相关变更需要使用 Codex `chrome-devtools` 打开 `https://127.0.0.1/` 验证页面渲染、交互、控制台错误、网络请求和响应式表现。
- 不提交生成的 `dist` 输出、`node_modules` 或本地运行缓存。

## 测试与验证范式

- 测试优先覆盖被修改的最小行为面，再根据风险扩展到集成、页面和部署检查。
- 后端纯逻辑变更：运行 Python 语法/导入检查，必要时运行触达模块的 pytest。
- 后端 API 变更：验证请求/响应模型、鉴权、错误码、空数据、异常分支和 MongoDB 索引初始化；至少补充或运行对应 API 测试。
- 后端可用性变更：验证 `curl -k https://127.0.0.1/health`，并确认 MongoDB 状态、服务日志和关键初始化没有新增错误。
- pipeline、采集、AI、手机、浏览器或长任务变更：验证开始、取消/失败、资源释放、通知/观测日志和重复执行的幂等性。
- 前端 service 变更：验证 API 路径、鉴权头、错误处理、下载/上传、空响应和类型定义。
- 前端页面变更：用 `chrome-devtools` 验证目标页面首屏、关键元素、表单校验、按钮状态、表格/列表、弹窗/抽屉、加载态、空态、错误态。
- 前端响应式变更：至少检查桌面和窄屏视口，确认文本不溢出、不遮挡，操作入口可见可用。
- 部署配置变更：运行 `docker-compose -f /root/Sere1nFish/docker-compose.yml config`，并确认没有意外新增公网端口、卷挂载或 secret 泄露。
- 推送实质性变更前，按范围运行最窄且有用的检查：
  - 前端：`cd /root/Sere1nFish/view && npm run build`
  - 前端调试：使用 `chrome-devtools` 打开 `https://127.0.0.1/`
  - 后端：Python 语法/导入检查，或针对已触达模块的测试
  - 部署：`docker-compose -f /root/Sere1nFish/docker-compose.yml config`
  - 运行时冒烟：`curl -k https://127.0.0.1/health`
- 如果某项检查无法运行，最终交接必须说明原因、影响范围和未覆盖风险。

## Git 管理与 Commit 范式

- 每次实际修改可用功能后都需要提交 commit；保持 commit 小而完整，提交信息直接描述结果。
- 将 `main` 视为回滚基线。进行高风险变更前，先提交有意义的检查点。
- 本工作区根目录 `/root/Sere1nFish` 是唯一的 Git 仓库，后端 `server`、前端 `view`、部署文件都在同一仓库内统一管理；所有 commit 和 push 都从根目录进行，远程为 `git@github.com:Serein-Lm/Sere1nFish.git`。不再使用后端、前端各自独立的子仓库。
- 提交前先看状态：`git status --short --branch`。确认工作区是否已有用户改动，不能回滚或覆盖不属于本次任务的修改。
- 只暂存本次修改过且属于本次任务的文件或目录，使用 `git add <file>` 或 `git add <dir>`；不要使用 `git add .`。
- 提交前检查暂存范围：`git diff --cached --name-only` 和必要的 `git diff --cached`，防止把运行时数据、用户改动或无关文件带入 commit。
- 不提交运行时数据和生成物：`node_modules`、`dist`、日志、备份、生成证书、本地配置、浏览器 profile、数据库卷、临时截图、缓存目录。
- 推荐提交信息使用祈使句或直接动宾结构，例如 `Add mobile device metadata API`、`Lazy load frontend routes`、`Refine AGENTS development guide`。
- 修改部署运行时文件时，确认是否需要同步 `server/deploy` 模板或文档，避免运行时和模板长期漂移。

## 热重载与本地运行

- 后端通过 `python run.py` 运行，该命令会以 reload 模式启动 Uvicorn。挂载到后端应用下的源码变更会触发进程重载。
- 前端通过 Vite dev server 运行，并启用 HMR。`view/src` 下的挂载源码变更会触发 HMR 或页面刷新。
- nginx 会通过 HTTPS 代理前端 dev server 和后端 API。浏览器验证使用 `https://127.0.0.1/`。

## 部署与安全边界

- 当前服务器的运行时部署文件位于 `/root/Sere1nFish` 根目录：`docker-compose.yml`、`compose.yaml`、`.env`、`nginx/`、`downloads/`。
- 后端 Git 仓库内的部署模板仍位于 `Sere1nFishServer/deploy`；修改运行时部署后，应按需同步模板或文档。
- HTTPS 证书位于 `/root/Sere1nFish/nginx/certs/`，不要提交证书文件。
- 数据库备份属于运维产物。将它们保留在 Git 之外，并通过 Compose `db-import` 服务或迁移脚本导入。
- 现有业务文件迁移使用 `python -m scripts.migrate_object_storage` 先盘点，再用 `--apply --concurrency 16` 幂等上传。迁移会校验远端大小和下载 SHA-256，成功前保留本地源文件；不得手工批量移动或提前删除源文件。
- 公网安全组只开放：`443/tcp`、`11010/tcp+udp`、`11011/tcp`、`11012/tcp`、`11013/udp`。
- 不要开放：`5555`、`8000`、`5173`、`27017`、`6379`、`9222`、`5900`、`6080`。

## 浏览器工具

- 后端扫描和浏览器自动化使用项目 Chrome Docker 镜像。
- 开发观察应使用 Codex `chrome-devtools` MCP server，该 server 会启动隔离的浏览器实例。不要复用扫描或浏览器容器进行开发观察。
- 前端相关变更需要通过 `chrome-devtools` 进行调试和验证，便于检查页面渲染、交互、控制台错误、网络请求和响应式表现。
