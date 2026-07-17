from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(..., description="项目名称")
    description: str | None = Field(default=None, description="项目描述")


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, description="项目名称")
    description: str | None = Field(default=None, description="项目描述")


class ProjectAppendRequest(BaseModel):
    target: str | None = Field(default=None, description="项目目标（后续流程使用）")
    content: str = Field(..., description="追加内容（增量写入）")


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str | None = None
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
    created_at: datetime
    data: dict[str, Any]
