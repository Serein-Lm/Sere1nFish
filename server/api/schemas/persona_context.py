"""Complete fictional contexts; public references are optional inputs."""
from typing import Literal
from pydantic import BaseModel, Field
from Sere1nGraph.graph.skills.schemas import RichFictionalPersonaProfile


class ScenarioContact(BaseModel):
    origin: Literal["fictional"] = "fictional"
    phone: str = Field(min_length=8, pattern=r"^模拟", description="完整模拟办公号码及分机，以模拟开头")
    email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.example$", description="模拟工作邮箱，域名以 .example 结尾")
    wechat: str = Field(min_length=4, pattern=r"^模拟", description="模拟工作微信，以模拟开头")
    availability: str = Field(min_length=8, description="联系时段、值班安排和紧急事项处理方式")
    introduction: str = Field(min_length=15, description="联系人在组织中的位置、联络流程及沟通注意事项")


class FictionalContextProfile(RichFictionalPersonaProfile):
    sources: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    research_evidence: list = Field(default_factory=list)
    scenario_contact: ScenarioContact
    company_business: str = Field(min_length=30, description="虚构公司的主营业务、服务对象、规模与内部部门关系")
    company_address: str = Field(min_length=8, description="虚构办公地址或办公园区场景")
    company_website: str = Field(pattern=r"^https://[a-z0-9.-]+\.example(?:/.*)?$", description="模拟公司网站，域名使用 .example")


class ContextSlot(BaseModel):
    name: str = Field(min_length=2)
    industry: str = Field(min_length=2)
    position: str = Field(min_length=2)
    direction: str = Field(min_length=30, description="具体的组织、地区、年龄、职业阶段和生活背景差异")


class ContextPlan(BaseModel):
    slots: list[ContextSlot] = Field(min_length=1, max_length=60)


class ContextReview(BaseModel):
    consistent: bool = Field(description="修订后的全部字段是否前后一致")
    issues_found: list[str] = Field(description="审校发现的时间、经历、家庭年龄或职责矛盾")
    corrections_made: list[str] = Field(description="本次已实际修订的内容")
    profile: FictionalContextProfile = Field(description="修订后的完整档案")
