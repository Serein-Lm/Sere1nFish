"""Target 机构公开情报深研领域模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TargetResearchSource(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=4000)
    summary: str = Field(default="", max_length=4000)
    source_type: str = Field(default="web", max_length=80)
    published_at: str | None = Field(default="", max_length=80)


class TargetResearchEvidence(BaseModel):
    dimension: str = Field(min_length=1, max_length=120)
    finding: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0.01, le=1.0)
    source_urls: list[str] = Field(min_length=1, max_length=20)


class TargetPublicContact(BaseModel):
    channel: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=500)
    context: str = Field(default="", max_length=2000)
    source_url: str = Field(min_length=1, max_length=4000)


class TargetKeyPerson(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    position: str = Field(default="", max_length=300)
    department: str = Field(default="", max_length=300)
    source_urls: list[str] = Field(min_length=1, max_length=20)


class RelatedTargetCandidate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    relation_type: Literal[
        "subsidiary",
        "controlled_entity",
        "affiliated_unit",
        "service_unit",
        "operating_entity",
        "platform_owner",
        "parent_organization",
        "partner",
        "vendor",
        "other",
    ] = "other"
    relationship_summary: str = Field(min_length=1, max_length=3000)
    root_domains: list[str] = Field(default_factory=list, max_length=12)
    web_scan_urls: list[str] = Field(default_factory=list, max_length=30)
    confidence: float = Field(ge=0.01, le=1.0)
    source_urls: list[str] = Field(min_length=1, max_length=20)
    scan_priority: int = Field(default=50, ge=0, le=100)
    should_scan: bool = False


class TargetResearchPayload(BaseModel):
    canonical_name: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=12000)
    industry: str = Field(default="", max_length=300)
    organization_type: str = Field(default="", max_length=300)
    responsibilities: list[str] = Field(default_factory=list, max_length=80)
    services: list[str] = Field(default_factory=list, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    root_domains: list[str] = Field(default_factory=list, max_length=20)
    web_scan_urls: list[str] = Field(default_factory=list, max_length=60)
    business_keywords: list[str] = Field(default_factory=list, max_length=100)
    search_terms_by_channel: dict[str, list[str]] = Field(default_factory=dict)
    public_contacts: list[TargetPublicContact] = Field(default_factory=list, max_length=50)
    key_people: list[TargetKeyPerson] = Field(default_factory=list, max_length=50)
    related_targets: list[RelatedTargetCandidate] = Field(default_factory=list, max_length=40)
    sources: list[TargetResearchSource] = Field(min_length=2, max_length=100)
    evidence: list[TargetResearchEvidence] = Field(min_length=1, max_length=200)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
