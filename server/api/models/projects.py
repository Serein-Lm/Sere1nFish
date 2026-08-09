from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="项目名称")
    description: str | None = Field(default=None, description="项目描述")
    group_id: str | None = Field(default=None, description="项目分组 ID")


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200, description="项目名称")
    description: str | None = Field(default=None, description="项目描述")
    group_id: str | None = Field(default=None, description="项目分组 ID；null 表示移出分组")


class ProjectGroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="分组名称")
    description: str = Field(default="", max_length=500, description="分组描述")
    sort_order: int = Field(default=0, ge=-10000, le=10000, description="显示顺序")


class ProjectGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    sort_order: int | None = Field(default=None, ge=-10000, le=10000)


class ProjectGroupOut(BaseModel):
    group_id: str
    name: str
    description: str = ""
    sort_order: int = 0
    project_count: int = 0
    created_at: datetime
    updated_at: datetime


class ProjectAppendRequest(BaseModel):
    target: str | None = Field(default=None, description="项目目标（后续流程使用）")
    content: str = Field(..., description="追加内容（增量写入）")


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    group_id: str | None = None
    group_name: str | None = None
    target: str | None = None
    contents: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class WebTaggingCreateRequest(BaseModel):
    project_id: str
    url: str


class CompanyTaggingRequest(BaseModel):
    project_id: str
    company_name: str


class WebTaggingResultOut(BaseModel):
    id: str
    project_id: str
    url: str
    task_id: str = ""
    source: str = "web_tagging"
    target_id: str = ""
    target_relation: dict[str, Any] | None = None
    created_at: datetime
    data: dict[str, Any]
