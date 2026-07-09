"""
Skill 输出 Pydantic Schema

所有阶段的输出都是结构化 JSON，方便：
- LLM structured output 直接解析
- MongoDB 存储
- 前端渲染
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════
# 场景伪造阶段输出
# ═══════════════════════════════════════════

class FakedIdentity(BaseModel):
    """伪造身份"""
    name: str = Field(description="伪造姓名")
    company: str = Field(description="伪造公司名")
    company_desc: str = Field(description="公司业务描述及与目标的关联")
    position: str = Field(description="伪造职位")
    background: str = Field(description="职业背景/历史经历")
    personality: str = Field(description="性格特征")


class LogicChainStep(BaseModel):
    """逻辑链条中的一步"""
    step: int = Field(description="步骤序号")
    channel: str = Field(description="渠道: email/phone/wechat/sms/intranet")
    action: str = Field(description="动作描述")
    fallback: str | None = Field(default=None, description="失败时的备选方案")


class ScenarioItem(BaseModel):
    """单个场景"""
    scenario_name: str = Field(description="场景名称")
    target_background: str = Field(description="目标背景（已知信息汇总）")
    scenario_overview: str = Field(description="场景概述")
    faked_identity: FakedIdentity = Field(description="伪造的背景身份")
    logic_chain: list[LogicChainStep] = Field(description="逻辑链条")
    risk_notes: str | None = Field(default=None, description="风险提示/注意事项")


class ScenarioOutput(BaseModel):
    """场景伪造阶段的完整输出"""
    scenarios: list[ScenarioItem] = Field(description="生成的场景列表")
    loaded_skills: list[str] = Field(default_factory=list, description="本次加载的 skill id 列表")


# ═══════════════════════════════════════════
# 话术生成阶段输出
# ═══════════════════════════════════════════

class DialogueTurn(BaseModel):
    """一轮对话"""
    role: str = Field(description="角色: attacker / target")
    content: str = Field(description="对话内容")
    tactic: str | None = Field(default=None, description="使用的心理策略")


class ChannelScript(BaseModel):
    """单个渠道的话术"""
    channel: str = Field(description="渠道: wechat/email/phone/sms/intranet")
    dialogue: list[DialogueTurn] = Field(default_factory=list, description="对话内容")
    email_template: str | None = Field(default=None, description="邮件模板（仅 email 渠道）")
    key_points: list[str] = Field(default_factory=list, description="关键要点")


class PayloadSpec(BaseModel):
    """样本文件规格"""
    archive_name: str = Field(description="压缩包文件名")
    exe_name: str = Field(description="exe 文件名")
    icon_disguise: str = Field(description="图标伪装建议")
    compression_method: str = Field(description="压缩方式: zip_double / 7z")
    password: str = Field(description="压缩密码")
    notes: str | None = Field(default=None, description="补充说明")


class ScriptItem(BaseModel):
    """单个场景的话术"""
    scenario_name: str = Field(description="对应的场景名称")
    channel_scripts: list[ChannelScript] = Field(description="各渠道话术")
    payload: PayloadSpec | None = Field(default=None, description="样本文件规格")
    alternative_approach: str | None = Field(default=None, description="无法发送压缩包时的替代方案")


class ScriptOutput(BaseModel):
    """话术生成阶段的完整输出"""
    scripts: list[ScriptItem] = Field(description="各场景的话术")
    loaded_skills: list[str] = Field(default_factory=list, description="本次加载的 skill id 列表")


# ═══════════════════════════════════════════
# 质疑应对阶段输出
# ═══════════════════════════════════════════

class ObjectionItem(BaseModel):
    """单条质疑与应对"""
    objection: str = Field(description="质疑内容")
    response: str = Field(description="应对话术")
    tactic: str = Field(description="使用的心理策略")
    context_note: str = Field(description="上下文关联说明")


class ScenarioObjections(BaseModel):
    """单个场景的质疑应对"""
    scenario_name: str = Field(description="对应的场景名称")
    objections: list[ObjectionItem] = Field(description="质疑与应对列表")


class ObjectionOutput(BaseModel):
    """质疑应对阶段的完整输出"""
    scenario_objections: list[ScenarioObjections] = Field(description="各场景的质疑应对")
    loaded_skills: list[str] = Field(default_factory=list, description="本次加载的 skill id 列表")


# ═══════════════════════════════════════════
# 整合输出
# ═══════════════════════════════════════════

class FinalOutput(BaseModel):
    """整合阶段输出 — 最终交付物"""
    markdown: str = Field(description="整合后的完整 Markdown 文档")
    loaded_skills: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════
# 完整结果（存储用）
# ═══════════════════════════════════════════

class CopywritingResult(BaseModel):
    """一次完整话术生成的结果，直接存 MongoDB"""
    task_id: str = Field(description="任务 ID")
    project_id: str = Field(default="", description="项目 ID")
    input_metadata: str = Field(description="输入的元数据/信息汇总")
    selected_skills: list[str] = Field(default_factory=list, description="用户选择的 skill 类别")
    scenario: ScenarioOutput | None = None
    script: ScriptOutput | None = None
    objection: ObjectionOutput | None = None
    final: FinalOutput | None = None
    status: str = Field(default="pending", description="pending/running/completed/error")
    error: str | None = None


# ═══════════════════════════════════════════
# URL 扫描流水线 Schema
# ═══════════════════════════════════════════

class UrlProbeItem(BaseModel):
    """单个 URL 探活结果"""
    url: str = Field(description="原始 URL")
    is_alive: bool = Field(description="是否存活")
    status_code: int | None = Field(default=None, description="HTTP 状态码")
    title: str | None = Field(default=None, description="页面标题")
    response_time: float | None = Field(default=None, description="响应时间(秒)")
    error: str | None = Field(default=None, description="错误信息")


class InfoFinding(BaseModel):
    """
    信息节点 — Agent 从网站提取的单个有效信息

    一个 URL 可能产出多个 InfoFinding（比如一个网站有 HR 邮箱、客服微信、商务电话）。
    每个 InfoFinding 都会独立触发话术生成。
    """
    finding_id: str = Field(description="信息节点 ID")
    url: str = Field(description="来源 URL")
    type: str = Field(description="信息类型: hr_contact/business_contact/customer_service/tech_support/social_media/download/form/other")
    channel: str = Field(description="渠道: email/phone/wechat/qq/form/app/other")
    role: str = Field(description="角色: hr/sales/support/admin/developer/unknown")
    label: str = Field(description="标签（如：简历投递、商务合作、在线客服）")
    value: str = Field(description="具体值（邮箱/电话/微信号/URL）")
    context: str = Field(description="上下文描述（触达路径、页面位置、可执行动作、社工风险点）")
    evidence: str = Field(description="页面证据（原文片段 + 定位信息）")
    attention_score: int = Field(default=50, description="关注度 0-100")
    attention_reason: str = Field(default="", description="关注度理由")


class UrlScanResult(BaseModel):
    """单个 URL 的扫描结果"""
    url: str = Field(description="扫描的 URL")
    domain: str = Field(default="", description="域名")
    site_name: str | None = Field(default=None, description="站点名称")
    entity_name: str | None = Field(default=None, description="主体名称")
    summary: str | None = Field(default=None, description="站点简介")
    has_findings: bool = Field(default=False, description="是否有有效信息")
    findings: list[InfoFinding] = Field(default_factory=list, description="信息节点列表")
    scan_status: str = Field(default="pending", description="pending/scanning/completed/error")
    error: str | None = None


class FindingCopywriting(BaseModel):
    """
    单个信息节点的话术 — 前端渲染的最小单元

    每个 finding 独立生成话术，type 字段决定前端渲染方式。
    """
    finding_id: str = Field(description="关联的信息节点 ID")
    url: str = Field(description="来源 URL")
    finding_type: str = Field(description="信息类型（同 InfoFinding.type）")
    finding_channel: str = Field(description="信息渠道（同 InfoFinding.channel）")
    finding_label: str = Field(description="信息标签（同 InfoFinding.label）")
    finding_value: str = Field(description="信息值（同 InfoFinding.value）")
    # 话术内容
    scenario: ScenarioItem | None = Field(default=None, description="场景伪造")
    scripts: list[ChannelScript] = Field(default_factory=list, description="各渠道话术")
    payload: PayloadSpec | None = Field(default=None, description="样本文件")
    objections: list[ObjectionItem] = Field(default_factory=list, description="质疑应对")
    # 元数据
    target_analysis: str = Field(default="", description="对目标信息的理解和分析")
    psychology_strategy: str = Field(default="", description="核心心理策略说明")
    case_reference: str = Field(default="", description="参考的实际案例")
    loaded_skills: list[str] = Field(default_factory=list, description="加载的 skill id")
    status: str = Field(default="pending", description="pending/generating/completed/error")
    error: str | None = None


class UrlScanTask(BaseModel):
    """URL 扫描任务 — 整体任务状态"""
    task_id: str = Field(description="任务 ID")
    project_id: str = Field(description="项目 ID")
    total_urls: int = Field(default=0, description="总 URL 数")
    alive_urls: int = Field(default=0, description="存活 URL 数")
    scanned_urls: int = Field(default=0, description="已扫描 URL 数")
    total_findings: int = Field(default=0, description="总信息节点数")
    total_copywritings: int = Field(default=0, description="已生成话术数")
    status: str = Field(default="pending", description="pending/probing/scanning/generating/completed/error")
    error: str | None = None


# ═══════════════════════════════════════════
# 公司名规范化输出
# ═══════════════════════════════════════════

class CompanyNormalization(BaseModel):
    """
    公司名规范化结果 — AI 浏览器搜索 + 天眼查 ICP 交叉验证输出。

    normalized_name 用作招投标查询的规范全称；root_domain 用作 FOFA 资产检索。
    """
    normalized_name: str = Field(description="规范化后的公司全称（工商注册全称，招投标查询可直接使用）")
    root_domain: str = Field(default="", description="官网根域名（如 example.com，不含协议与子域）")
    aliases: list[str] = Field(default_factory=list, description="常见别名/简称列表")
    confidence: float = Field(default=0.0, description="规范化结果置信度 0-1")
    source: str = Field(default="", description="结论来源说明（如 bing_search / tianyancha_icp / cross_validated）")


# ═══════════════════════════════════════════
# 人设库 — 真实人物档案结构化输出
# ═══════════════════════════════════════════

class PersonaEducation(BaseModel):
    """教育背景"""
    school: str = Field(default="", description="毕业院校")
    degree: str = Field(default="", description="学历/学位")
    major: str = Field(default="", description="专业")
    graduation_year: str = Field(default="", description="毕业年份")


class PersonaContact(BaseModel):
    """公开可得的联系方式"""
    phone: str = Field(default="", description="电话")
    email: str = Field(default="", description="邮箱")
    wechat: str = Field(default="", description="微信")
    other_social: list[str] = Field(default_factory=list, description="其他社交账号/主页")


class PersonaProfile(BaseModel):
    """
    人设档案 — 浏览器搜集的真实人物信息结构化输出。

    真实信息为后续话术、人物画像背景、招股书等产物生成提供丰富元信息。
    company_root_domain 用于关联 company_meta 公司元信息，形成人-公司图。
    """
    name: str = Field(description="真实姓名或常用名（必填）")
    gender: str = Field(default="", description="性别：男/女/未知")
    company: str = Field(default="", description="所属公司工商全称")
    company_root_domain: str = Field(default="", description="公司官网根域名，用于关联公司元信息")
    industry: str = Field(default="", description="所属行业")
    position: str = Field(default="", description="职位")
    position_level: str = Field(default="", description="职级，如高管/中层/基层")
    department: str = Field(default="", description="部门")
    work_years: str = Field(default="", description="工作年限")
    education: PersonaEducation = Field(default_factory=PersonaEducation, description="教育背景")
    location: str = Field(default="", description="所在城市/地区")
    contact: PersonaContact = Field(default_factory=PersonaContact, description="公开可得的联系方式")
    background: str = Field(default="", description="职业背景与经历综述")
    personality: str = Field(default="", description="性格特点")
    interests: list[str] = Field(default_factory=list, description="兴趣/关注点")
    tags: list[str] = Field(default_factory=list, description="标签（供检索符合人设的人物）")
    risk_signals: list[str] = Field(default_factory=list, description="沟通风险/敏感点")
    summary: str = Field(default="", description="综合人设摘要")
    aliases: list[str] = Field(default_factory=list, description="别名/曾用名/昵称")
    sources: list[str] = Field(default_factory=list, description="信息来源说明（网站/平台）")
    evidence: list[str] = Field(default_factory=list, description="关键结论的证据摘录")
    confidence: float = Field(default=0.0, description="整体置信度 0-1")
