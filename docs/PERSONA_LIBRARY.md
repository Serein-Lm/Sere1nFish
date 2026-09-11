# 虚构人设完整性

人物仍由 `api.services.persona_collect` 的研究、原型生成、一致性审核与 DAO 持久化流程生成。`api.services.persona_quality` 统一校验完整性和来源质量；批量生成与 `researched_persona` 的 Agent 写入共用此边界。

`RichFictionalPersonaProfile` 的必填字段不得为缺失值、空串或全空白；列表不能用空白条目凑数量，教育背景四个字段必须完整。已有占位文字、研究缺口、摘要及背景丰富度检查继续生效。校验只返回缺口，不在代码中拼接或硬编码人物事实。

持续升级保留结构化身份字段后，摘要中的明确姓名与年龄陈述仍须与该身份一致；冲突会进入既有 AI 审核纠正流程，不能仅凭模型返回的一致性评分通过。亲属年龄与“某岁时”的历史经历不作为当前年龄判断。

资料升级调用 `POST /api/v1/persons/{person_id}/enrich`，保留稳定身份并累加 `profile_version`、`research_rounds` 和来源。新的人设通过 `POST /api/v1/persons/generate` 生成；行业、年龄及性格覆盖由请求参数交给 AI 规划，代码不预置人名与人物样本。任务进度保存在 `persona_research_tasks`，查询入口为 `GET /api/v1/persons/tasks/{task_id}`。

浏览器研究按规范 URL 去重所有已尝试候选，包括下载失败的页面；已达到同站来源上限的候选在导航前跳过。每个研究分片最多读取 48 个不同候选，目标为 12 个有效页面，仍至少要求 8 个有效来源和 12 条有引用的洞察。来源不足继续明确失败，不使用重复 URL 或无关页面补足数量。

完整性针对虚构人物的身份、职业、教育、行为、背景与研究证据。`contact` 和 `company_root_domain` 按既有隔离规则留空，避免将虚构人设包装成真实身份；来源证明行业和岗位背景，不证明该人物存在。真实人物资料仍归 `person_intelligence`。
