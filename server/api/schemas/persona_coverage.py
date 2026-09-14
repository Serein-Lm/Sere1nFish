from typing import Literal
from pydantic import BaseModel, Field


class CoverageStart(BaseModel):
    industry_codes: list[str] = Field(default_factory=list, description="留空补齐全部 97 个行业大类")
    minimum_personas: int = Field(4, ge=1, le=20)


class OrganizationSourceFact(BaseModel):
    organization_name: str = Field(min_length=2, max_length=160)
    organization_type: Literal["company", "institution"] = "company"
    source_url: str
    excerpt: str = Field(min_length=10, max_length=1200)
    office_phone: str = ""
    website: str = ""
    address: str = ""


class OrganizationResearchResult(BaseModel):
    organizations: list[OrganizationSourceFact] = Field(default_factory=list, max_length=12)
