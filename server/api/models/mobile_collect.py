"""手机采集任务框架 — 领域模型 (Pydantic)。

统一抽象:一个采集任务 = 应用 → 搜索 → 截屏(滑动) → 分析(结构化) → 增量入库 → 增量通知。
养号 / 刷小红书 / 公众号搜索入库分析 均为该模型的配置化实例。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


NotifyOn = Literal["new", "changed", "both", "none"]
AppInstance = Literal["primary", "clone"]


class ExtractField(BaseModel):
    """分析阶段要从截图中提取的一个结构化字段。"""

    name: str = Field(description="字段名(英文/拼音,作为结构化 key)")
    description: str = Field(default="", description="字段含义,用于指导视觉模型提取")
    type: Literal["string", "number", "boolean", "list"] = Field(
        default="string", description="字段类型"
    )


class CollectTaskDef(BaseModel):
    """自定义采集任务定义 (配置)。"""

    name: str = Field(description="任务名称")
    project_id: str | None = Field(default=None, description="归属项目")
    target_id: str | None = Field(default=None, description="关联的全局 Target ID")
    target_name: str | None = Field(
        default=None, description="明确的目标公司/机构名称，用于跨项目聚类"
    )
    target_type: str = Field(default="company", description="Target 类型")
    device_id: str = Field(description="执行设备 device_id")
    app_name: str = Field(description="目标应用名,如 微信 / 小红书")
    app_instance: AppInstance = Field(
        default="primary",
        description="双开应用实例;primary 为主应用,clone 为应用分身",
    )
    keywords: list[str] = Field(default_factory=list, description="搜索关键词列表(逐个执行)")
    use_target_keyword_library: bool = Field(
        default=True,
        description="合并项目 Target、第一层全资子公司及数据库渠道 Skill 的搜索词",
    )
    include_direct_children: bool = Field(
        default=True,
        description="从项目 Target 词库解析关键词时是否包含第一层全资子公司",
    )
    max_resolved_keywords: int = Field(
        default=60,
        ge=1,
        le=200,
        description="从目标关系和数据库词库聚合后的最大关键词数",
    )
    swipe_times: int = Field(default=3, ge=0, le=50, description="每个关键词的滑动次数")
    swipe_interval: float = Field(default=1.2, ge=0.2, le=10, description="滑动间隔秒")
    extract_fields: list[ExtractField] = Field(
        default_factory=list, description="要结构化提取的字段;为空则仅记录整屏摘要"
    )
    dedup_key_fields: list[str] = Field(
        default_factory=list,
        description="用于派生稳定 record_id 的字段名;为空则按内容哈希去重",
    )
    notify_on: NotifyOn = Field(default="new", description="增量通知策略")
    search_hint: str = Field(
        default="", description="可选的搜索步骤补充说明,注入规划层目标"
    )
    deep_collect: bool = Field(
        default=False, description="是否启用详情页深入采集(点进列表条目采集富信息)"
    )
    source_link_strategy: str = Field(
        default="none",
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="详情页原文链接提取策略;none 表示仅使用视觉模型结果",
    )
    detail_max_items: int = Field(
        default=5, ge=0, le=20, description="每个列表页最多点进几条做详情深采"
    )
    detail_max_total_items: int = Field(
        default=0,
        ge=0,
        le=200,
        description="单次任务最多深采条数;0 表示仅受每关键词上限约束",
    )
    detail_max_swipes: int = Field(
        default=12, ge=0, le=20, description="详情页最多滑动几屏以滑到底(视觉到底检测提前停止)"
    )
    min_score_to_detail: int = Field(
        default=60, ge=0, le=100, description="triage 相关性分达到该阈值才点进详情深采"
    )
    min_subject_match: int = Field(
        default=70, ge=0, le=100, description="主体对应程度达到该阈值才点进详情深采(避免什么都点)"
    )
    min_score_to_persist: int = Field(
        default=0, ge=0, le=100, description="入库最低相关性分(0=全收)"
    )
    max_runtime_seconds: int = Field(
        default=0,
        ge=0,
        le=14400,
        description="单次运行总时限秒数;0 表示不限制",
    )


class CollectTaskUpdate(BaseModel):
    """任务定义部分更新。"""

    name: str | None = None
    project_id: str | None = None
    target_id: str | None = None
    target_name: str | None = None
    target_type: str | None = None
    device_id: str | None = None
    app_name: str | None = None
    app_instance: AppInstance | None = None
    keywords: list[str] | None = None
    use_target_keyword_library: bool | None = None
    include_direct_children: bool | None = None
    max_resolved_keywords: int | None = Field(default=None, ge=1, le=200)
    swipe_times: int | None = Field(default=None, ge=0, le=50)
    swipe_interval: float | None = Field(default=None, ge=0.2, le=10)
    extract_fields: list[ExtractField] | None = None
    dedup_key_fields: list[str] | None = None
    notify_on: NotifyOn | None = None
    search_hint: str | None = None
    deep_collect: bool | None = None
    source_link_strategy: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    detail_max_items: int | None = Field(default=None, ge=0, le=20)
    detail_max_total_items: int | None = Field(default=None, ge=0, le=200)
    detail_max_swipes: int | None = Field(default=None, ge=0, le=20)
    min_score_to_detail: int | None = Field(default=None, ge=0, le=100)
    min_subject_match: int | None = Field(default=None, ge=0, le=100)
    min_score_to_persist: int | None = Field(default=None, ge=0, le=100)
    max_runtime_seconds: int | None = Field(default=None, ge=0, le=14400)


class TriggerDef(BaseModel):
    """调度触发器:interval(间隔秒) 或 cron(分 时 日 月 周)。"""

    type: Literal["interval", "cron"] = Field(default="interval")
    interval_seconds: int | None = Field(
        default=None, ge=30, description="interval 类型的间隔秒(>=30)"
    )
    cron: str | None = Field(
        default=None, description="cron 类型的表达式: 分 时 日 月 周,如 '0 9 * * *'"
    )


class ScheduleCreate(BaseModel):
    """创建调度。"""

    name: str = Field(description="调度名称")
    target_id: str = Field(description="目标采集任务定义 task_def_id")
    trigger: TriggerDef = Field(description="触发器")
    enabled: bool = Field(default=True)


class ScheduleUpdate(BaseModel):
    """更新调度。"""

    name: str | None = None
    trigger: TriggerDef | None = None
    enabled: bool | None = None


class RecordsListRequest(BaseModel):
    """采集记录分页查询。"""

    task_def_id: str | None = None
    project_id: str | None = None
    target_id: str | None = None
    only_incremental: bool = Field(default=False, description="仅返回 new/changed 记录")
    min_score: int | None = Field(
        default=None, ge=0, le=100, description="仅返回相关性分>=该值的记录"
    )
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
