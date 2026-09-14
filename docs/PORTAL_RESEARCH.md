# 官网门户深研

项目的 Target 操作区及 Target 概览提供“官网门户深研”。研究业务职责、招聘人才、招标采购、招商合作、反馈服务、组织与直属关系六类栏目，生成业务摘要、公开办公联系方式、来源及覆盖缺口。栏目不存在或访问失败会如实标注；“已收集”表示已覆盖报告描述的要点，不表示全站完成。

## 接口与范围

复用 `POST /api/v1/targets/{target_id}/research`，批量研究入口接受相同的可选 `portal_options`。未传此字段的旧调用继续执行普通机构研究，不改变默认预算或缓存语义。范围由 `PortalResearchOptions` 统一校验，任务 dispatcher 只透传参数。

| 参数 | 默认值 | 范围与含义 |
| --- | --- | --- |
| include_subordinates | true | 收集官网明确的直属下级 |
| include_parent | false | 核验一层直接上级，不递归向上 |
| max_runtime_seconds | 3600 | 单次执行 900–7200 秒，浏览重试共用剩余预算 |
| max_tool_calls | 120 | 单个 Agent 32–240 次调用；同时受页面和时间边界约束 |
| context_tokens | 24000 | 12K–48K 历史压缩触发阈值，保留近期消息与证据账本 |
| max_pages | 50 | 10–100 个导航 URL，包含失败尝试；每个 URL 至多两次导航 |
| dry_run | false | 只返回任务预览，不写入机构档案、来源或关系，不启动后续采集或通知 |

现有 `max_related_targets` 仍限制本轮实际扩展数量（0–12）。直属关系必须有正文、机构设置或名录支持，导航分组只作候选线索；关系置信度与主体归属继续复用普通研究校验。没有独立官网时保留关系候选事实，不猜测域名。直接上级保存为独立 Target 与 `target_relationships`，不反向放入控股子树。

Agent 从已核验官网出发，可访问官网明确链接的招聘、采购和服务入口，以及用户开启的直属单位方向；不得跟随友情链接、合作伙伴、供应商和普通外部推荐。采集层 HTML/渲染 DOM 链接解析复用同一友情链接分类器。链接上下文未明确时按普通外链处理，不自动承认隶属关系。

## 执行与存储

- API：`api.routers.source_documents`；前端 API：`sourceDocumentService`；配置与报告：`components/PortalResearch`。
- 编排：既有 `target_research` 任务 runtime 调用 `PortalResearchSession`，复用 Chrome 池、Prompt 库与模型 factory，无独立浏览器或任务生命周期。
- 阅读：仅开放 `navigate_page/evaluate_script`，固定脚本读取正文、标题及链接上下文，禁止模型自定义脚本。工具异步观察器等待 checkpoint 写入后返回。
- 检查点：`tasks.checkpoint.portal_research.pages.<URL哈希>` 保存 URL、标题、最多 12000 字正文摘要、最多 100 条链接及 `read_at`；最终已校验结果保存于 `.result`。暂停恢复复用阅读账本和友链边界，时间预算按本次恢复执行重新计算。
- 来源：两路并发调用 `source_documents.ingest_source_url`，每条至多 180 秒且受剩余研究预算限制；正文原件、附件和视觉资料仍由统一来源服务保存到私有 OSS。报告保存自身 `source_document_id/source_document_version_id`，保留不可变来源版本。
- 报告：复用 `target_research` 版本与最新投影，新增 `portal_sections/portal_options/portal_page_count/portal_excluded_link_count/portal_archive_pending`；不同研究轮次保留历史。
- 长报告在浏览完成后从完整阅读账本统一生成，所有已读正文 URL 均进入有界上下文，长页按可用预算保留首尾节选；不使用普通机构研究仅四页的修复摘要，也不依赖浏览聊天压缩后剩余的页面。整理使用同一模型 factory、结构化 Schema、来源校验与独立观测阶段。
- 后续采集：可选继续门户文档和附件深采，复用公司 pipeline 与 `website_documents` 持久化队列；根单位与关联单位仅开启官网渠道，不自动启动手机、付费招投标或话术生成。
- 观测：浏览、结构化提取、修复及来源归档携带 Project、Task 和研究阶段的统一观测上下文；阅读进度在任务列表中显示。

## 失败与恢复边界

单页失败允许有界切换来源，浏览器基础设施故障沿用既有热切换与资源释放。没有两条可校验来源时研究任务失败，已读账本保留，不能用空报告冒充完成。归档失败不抹除已读证据，来源逐条记为 pending，最终报告标记缺口；当前没有独立门户归档补录 worker，可从任务恢复或后续研究补齐。后续官网文档队列具有自身的恢复与重试，不与 Agent 研究预算混用。

新增字段无需数据迁移，不新增 MongoDB collection、索引、后台进程、配置环境变量或公网端口。试跑结果仍以任务 `preview` 保存以便用户查看，阅读账本与业务档案不写入。

## 验证

回归覆盖静态与浏览器友链过滤、关系方向关闭、URL 改写限制、固定只读脚本、页面/时间预算、批量参数、暂停账本恢复、同步/异步工具观察器、试跑无业务写入，以及旧 Target 研究、来源归档和官网队列兼容。发布时执行前端构建，并使用 Chrome DevTools 检查配置、报告、窄屏、控制台和真实任务进度。
