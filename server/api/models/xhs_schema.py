"""
XHS 小红书社工信息采集 - 数据模型定义
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ==================== Cookie 管理 ====================

class XhsCookieCreate(BaseModel):
    """创建 Cookie 请求"""
    account_name: str = Field(..., description="账号名称")
    cookie_string: str = Field(..., description="Cookie 字符串")


class XhsCookieUpdate(BaseModel):
    """更新 Cookie 请求"""
    new_account_name: str | None = Field(default=None, description="新账号名称")
    cookie_string: str | None = Field(default=None, description="Cookie 字符串")
    is_active: bool | None = Field(default=None, description="是否激活")
    is_enabled: bool | None = Field(default=None, description="是否纳入账号池")


class XhsCookieOut(BaseModel):
    """Cookie 输出"""
    id: str
    account_name: str
    is_active: bool = False
    is_enabled: bool = True
    is_valid: bool | None = None  # None 表示未验证
    last_verified_at: datetime | None = None
    last_used_at: datetime | None = None
    cooldown_until: datetime | None = None
    lease_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    quarantined_at: datetime | None = None
    quarantine_reason: str | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class XhsCookieDetail(BaseModel):
    """带完整 cookie_string 的 Cookie 详情"""
    id: str
    account_name: str
    cookie_string: str  # 完整的 Cookie 字符串
    is_active: bool = False
    is_enabled: bool = True
    is_valid: bool | None = None
    last_verified_at: datetime | None = None
    last_used_at: datetime | None = None
    cooldown_until: datetime | None = None
    lease_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    quarantined_at: datetime | None = None
    quarantine_reason: str | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


# ==================== 搜索任务 ====================

TaskStatus = Literal["pending", "running", "completed", "failed"]


class XhsSearchTaskCreate(BaseModel):
    """创建搜索任务请求"""
    project_id: str = Field(..., description="项目 ID")
    keyword: str = Field(..., description="搜索关键词")
    max_notes: int = Field(default=20, ge=1, le=100, description="最大笔记数")
    attention_threshold: int = Field(default=60, ge=0, le=100, description="关注度阈值")


class XhsSearchTaskOut(BaseModel):
    """搜索任务输出"""
    id: str
    project_id: str
    keyword: str
    max_notes: int
    attention_threshold: int
    status: TaskStatus
    notes_count: int = 0
    suspicious_count: int = 0
    profiles_count: int = 0
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


# ==================== 笔记打标结果 ====================

AttackSurfaceType = Literal[
    "employee_leak",       # 员工信息泄露
    "contact_info",        # 联系方式暴露
    "insider_info",        # 内部信息
    "credential_leak",     # 凭证泄露
    "org_structure",       # 组织架构
    "business_process",    # 业务流程
    "technical_info",      # 技术信息
    "location_info",       # 位置信息
    "social_relation",     # 社交关系
    "daily_routine",       # 日常作息/通勤
    "other",               # 其他
]

_VALID_ATTACK_TYPES = set(AttackSurfaceType.__args__)


class XhsNoteTagging(BaseModel):
    """笔记打标结果"""
    keyword_relevance: int = Field(default=0, ge=0, le=100, description="与搜索关键词的关联度")
    relevance_reason: str | None = Field(default=None, description="关联度分析原因")
    is_suspicious: bool = False
    attention_score: int = Field(default=0, ge=0, le=100)
    attack_surface_types: list[str] = Field(default_factory=list)
    reason: str | None = None
    evidence: str | None = Field(default=None, description="打分依据：XX(+N分) + XX(+N分) = 总分")
    company_mentioned: str | None = Field(default=None, description="提及的公司名称")
    key_info_extracted: list[str] = Field(default_factory=list)

    @field_validator("attention_score", "keyword_relevance", mode="before")
    @classmethod
    def clamp_score(cls, v):
        """LLM 可能输出超过 100 的分数，截断到 0-100"""
        if isinstance(v, (int, float)):
            return max(0, min(100, int(v)))
        return v

    @field_validator("attack_surface_types", mode="before")
    @classmethod
    def coerce_attack_types(cls, v):
        """LLM 可能输出未知类型，映射到 other"""
        if not isinstance(v, list):
            return []
        return [t if t in _VALID_ATTACK_TYPES else "other" for t in v]


class XhsNoteUserInfo(BaseModel):
    """笔记用户信息"""
    user_id: str
    nickname: str
    avatar: str | None = None


class XhsNoteCreate(BaseModel):
    """内部使用 - 创建笔记记录"""
    project_id: str
    task_id: str
    note_id: str
    xsec_token: str
    xsec_source: str
    title: str
    desc: str
    note_type: str
    liked_count: str
    user: XhsNoteUserInfo
    cover: str | None = None


class XhsNoteOut(BaseModel):
    """笔记输出"""
    id: str
    project_id: str
    task_id: str
    note_id: str
    xsec_token: str | None = None
    xsec_source: str | None = None
    title: str
    desc: str
    liked_count: str
    user: XhsNoteUserInfo
    cover: str | None = None
    publish_time_text: str | None = None  # 发布时间文本（如"1小时前""3天前"）
    tagging: XhsNoteTagging | None = None
    created_at: datetime


# ==================== 笔记详情打标 ====================

FindingType = Literal[
    "contact",      # 联系方式
    "insider",      # 内部信息
    "credential",   # 凭证信息
    "location",     # 位置信息
    "relation",     # 关系信息
    "process",      # 流程信息
    "other",        # 其他
]


class XhsDetailFinding(BaseModel):
    """详情发现"""
    type: FindingType
    value: str
    evidence: str
    attention_reason: str


class XhsCompanyIdentified(BaseModel):
    """识别出的公司信息"""
    name: str | None = Field(default=None, description="公司名称")
    confidence: Literal["high", "medium", "low"] | None = Field(default=None, description="可信度")
    evidence: str | list[str] | None = Field(default=None, description="判断依据")
    related_to_keyword: bool = Field(default=False, description="是否与搜索关键词相关")
    relationship_type: str | None = Field(default=None, description="与搜索关键词的关系类型")


class XhsDetailTagging(BaseModel):
    """详情打标结果"""
    keyword_relevance: int = Field(default=0, ge=0, le=100, description="与搜索关键词的关联度")
    keyword_analysis: str | None = Field(default=None, description="关键词关联分析")
    company_identified: XhsCompanyIdentified | None = Field(default=None, description="识别出的公司")
    attention_score: int = Field(default=0, ge=0, le=100)
    findings: list[XhsDetailFinding] = Field(default_factory=list)
    summary: str | None = None


class XhsNoteDetailOut(BaseModel):
    """笔记详情输出"""
    id: str
    note_id: str
    project_id: str
    xsec_token: str | None = None
    xsec_source: str | None = None
    content: str | None = None
    comments_summary: str | None = None
    tagging: XhsDetailTagging | None = None
    created_at: datetime


# ==================== 人物画像（新结构） ====================

class XhsProfileOut(BaseModel):
    """
    人物画像输出
    
    Agent 输出的 JSON 直接存储到数据库，前端按需解析
    """
    id: str
    project_id: str
    task_id: str = ""
    user_id: str
    finding_id: str | None = Field(default=None, description="关联的 finding ID，用于查话术")
    
    # 核心字段（顶层）
    nickname: str = Field(default="", description="用户昵称")
    avatar_url: str | None = Field(default=None, description="头像链接")
    
    # Agent 分析结果（直接存储 JSON）
    basic_info: dict | None = Field(default=None, description="基础信息")
    stats: dict | None = Field(default=None, description="账号数据")
    identity: dict | None = Field(default=None, description="身份信息（公司/行业/职位）")
    bio_analysis: dict | None = Field(default=None, description="简介分析")
    device_info: dict | None = Field(default=None, description="设备信息")
    avatar_analysis: dict | None = Field(default=None, description="头像分析")
    gender_analysis: dict | None = Field(default=None, description="性别分析")
    personality_profile: dict | None = Field(default=None, description="性格画像")
    notes_analysis: dict | None = Field(default=None, description="笔记分析")
    company_identification: dict | None = Field(default=None, description="公司判定")
    keyword_relevance: dict | None = Field(default=None, description="关键词关联度")
    attack_surface: dict | None = Field(default=None, description="攻击面分析")
    social_graph: dict | None = Field(default=None, description="社交图谱")
    timeline: dict | None = Field(default=None, description="时间线")
    
    profile_summary: str | None = Field(default=None, description="画像描述")
    attention_score: int = Field(default=0, description="关注度评分")
    recommended_actions: list[dict] | None = Field(default=None, description="建议行动")
    tags: list[str] = Field(default_factory=list, description="标签")
    
    # 兼容旧数据
    note_ids: list[str] = Field(default_factory=list)
    notes_count: int = 0
    
    created_at: datetime
    updated_at: datetime


# ==================== API 响应 ====================

class XhsSearchResponse(BaseModel):
    """搜索任务响应"""
    task: XhsSearchTaskOut
    message: str = "搜索任务已创建"


class XhsPipelineStatus(BaseModel):
    """流水线状态"""
    task_id: str
    status: TaskStatus
    current_stage: str
    progress: dict[str, Any] = Field(default_factory=dict)
