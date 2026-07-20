"""
统一分页请求 / 响应模型

所有列表查询 API 统一使用 POST + 分页参数。
响应格式统一为 { items, total, page, page_size }。
"""
from __future__ import annotations

from typing import Any, Generic, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")


# ── 通用分页请求 ──

class PageRequest(BaseModel):
    """通用分页参数（所有列表查询的基类）"""
    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(default=10, ge=1, le=200, description="每页条数，默认 10")

    @property
    def skip(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


# ── 通用分页响应 ──

class PageResponse(BaseModel):
    """通用分页响应"""
    items: list[Any] = Field(default_factory=list)
    total: int = Field(default=0, description="总条数")
    page: int = Field(default=1, description="当前页码")
    page_size: int = Field(default=10, description="每页条数")

    @classmethod
    def build(cls, items: list, total: int, page: int, page_size: int, **extra) -> dict:
        """构建标准分页响应 dict（方便 router 直接 return）"""
        resp = {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
        resp.update(extra)
        return resp


# ── 各业务查询请求 ──

class ProjectListRequest(PageRequest):
    """项目列表"""
    pass


class TaskListRequest(PageRequest):
    """任务列表"""
    project_id: str = Field(description="项目 ID")
    task_type: str = Field(default="", description="任务类型过滤")


class FindingsQueryRequest(PageRequest):
    """Findings 查询"""
    project_id: str = Field(description="项目 ID")
    source: str = Field(default="", description="数据源过滤")
    task_id: str = Field(default="", description="任务 ID 过滤")
    target_id: str = Field(default="", description="Target ID 过滤")
    type: str = Field(default="", description="类型过滤")
    min_score: int = Field(default=0, ge=0, description="最低分数")
    sort: str = Field(default="score_desc", description="排序: score_desc / score_asc / time_desc")
    include_safe: bool = Field(default=False, description="是否包含安全 URL")


class WebTaggingListRequest(PageRequest):
    """Web Tagging 列表"""
    project_id: str = Field(description="项目 ID")
    source: str = Field(default="", description="数据源过滤")
    target_id: str | None = Field(default=None, description="目标 ID 过滤")


class XhsNotesListRequest(PageRequest):
    """小红书笔记列表"""
    project_id: str = Field(description="项目 ID")
    task_id: str | None = Field(default=None, description="任务 ID 过滤")
    target_id: str | None = Field(default=None, description="目标 ID 过滤")
    is_suspicious: bool | None = Field(default=None, description="是否可疑")
    sort_by: str = Field(default="relevance", description="排序: relevance / created_at")


class XhsProfilesListRequest(PageRequest):
    """小红书人物画像列表"""
    project_id: str = Field(description="项目 ID")
    task_id: str | None = Field(default=None, description="任务 ID 过滤")
    target_id: str | None = Field(default=None, description="目标 ID 过滤")


class XhsSearchTasksListRequest(PageRequest):
    """小红书搜索任务列表"""
    project_id: str | None = Field(default=None, description="项目 ID 过滤")


class DouyinSearchResultsListRequest(PageRequest):
    """抖音搜索结果列表"""
    project_id: str = Field(description="项目 ID")
    keyword: str | None = Field(default=None, description="关键词过滤")


class DouyinTaggedResultsListRequest(PageRequest):
    """抖音打标结果列表"""
    project_id: str = Field(description="项目 ID")
    tag: str | None = Field(default=None, description="标签过滤")


class DouyinProfilesListRequest(PageRequest):
    """抖音用户画像列表"""
    project_id: str = Field(description="项目 ID")


class ProjectNotesListRequest(PageRequest):
    """项目笔记列表（project_api）"""
    project_id: str = Field(description="项目 ID")
    task_id: str = Field(default="", description="任务 ID 过滤")
    target_id: str = Field(default="", description="目标 ID 过滤")
    is_suspicious: bool | None = Field(default=None, description="是否可疑")
    sort_by: str = Field(default="relevance", description="排序: relevance / created_at")


class ProjectProfilesListRequest(PageRequest):
    """项目画像列表（project_api）"""
    project_id: str = Field(description="项目 ID")
    target_id: str = Field(default="", description="目标 ID 过滤")
    min_score: int = Field(default=0, ge=0, description="最低分数")
    sort: str = Field(default="score_desc", description="排序: score_desc / time_desc")


class ScholarContactListRequest(PageRequest):
    """学者学术联系列表"""
    project_id: str = Field(description="项目 ID")
    unit: str = Field(default="", description="单位过滤")
    target_id: str = Field(default="", description="目标 ID 过滤")
    only_corresponding: bool = Field(default=False, description="仅通讯作者")
    only_verified: bool = Field(default=False, description="仅目标单位已验证(人物↔单位一致)")


class ScholarArticleListRequest(PageRequest):
    """学者文章列表"""
    project_id: str = Field(description="项目 ID")
    unit: str = Field(default="", description="单位过滤")
    only_verified: bool = Field(default=False, description="仅目标单位已验证文章")


class StatsRecordsRequest(PageRequest):
    """观测记录查询"""
    project_id: str = Field(default="", description="项目 ID")
    task_id: str = Field(default="", description="任务 ID")
