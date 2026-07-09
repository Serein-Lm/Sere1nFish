from __future__ import annotations

from pydantic import BaseModel, Field


class CompanyInput(BaseModel):
    project_id: str = Field(..., description="项目ID")
    company_name: str = Field(..., description="公司名称")
