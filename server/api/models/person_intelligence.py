"""人物 OSINT 情报领域模型。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PublicSource(BaseModel):
    title: str = Field(default="", max_length=500)
    url: str = Field(min_length=1, max_length=4000)
    summary: str = Field(default="", max_length=4000)
    source_type: str = Field(default="web", max_length=80)
    published_at: str = Field(default="", max_length=80)


class PublicContact(BaseModel):
    channel: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=500)
    context: str = Field(default="", max_length=2000)
    source_url: str = Field(min_length=1, max_length=4000)


class IntelligenceEvidence(BaseModel):
    evidence_id: str = Field(default="", max_length=120)
    dimension: str = Field(min_length=1, max_length=120)
    finding: str = Field(min_length=1, max_length=4000)
    evidence_type: Literal["fact", "inference"] = "fact"
    confidence: float = Field(ge=0.01, le=1.0, description="该证据结论的置信度 0-1")
    source_urls: list[str] = Field(default_factory=list, max_length=20)


class PersonaMatch(BaseModel):
    person_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=2000)
    score: float = Field(ge=0.01, le=1.0, description="人设与沟通环境的匹配度 0-1")


class ContextSignal(BaseModel):
    signal_id: str = Field(default="", max_length=120)
    signal_type: str = Field(default="current_event", max_length=80)
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(default="", max_length=4000)
    relevance: str = Field(default="", max_length=3000)
    observed_at: str = Field(min_length=1, max_length=80)
    expires_at: str = Field(default="", max_length=80)
    source_urls: list[str] = Field(min_length=1, max_length=20)


class EngagementScenario(BaseModel):
    scenario_id: str = Field(default="", max_length=120)
    title: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=3000)
    rationale: str = Field(min_length=1, max_length=4000)
    timing: str = Field(min_length=1, max_length=1000)
    priority: int = Field(ge=1, le=100, description="优先级 1-100，高价值场景通常不低于 70")
    source_urls: list[str] = Field(min_length=1, max_length=20)
    persona_ids: list[str] = Field(min_length=1, max_length=20)


class SampleCopywriting(BaseModel):
    copywriting_id: str = Field(default="", max_length=120)
    title: str = Field(default="沟通话术", max_length=200)
    channel: str = Field(default="通用", max_length=80)
    content: str = Field(min_length=1, max_length=10000)
    basis: str = Field(min_length=1, max_length=3000)
    scenario_ids: list[str] = Field(min_length=1, max_length=20)
    source_urls: list[str] = Field(min_length=1, max_length=20)


class PersonIntelligencePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    organization: str = Field(min_length=1, max_length=500)
    position: str = Field(default="", max_length=300)
    department: str = Field(default="", max_length=300)
    location: str = Field(default="", max_length=300)
    summary: str = Field(default="", max_length=8000)
    background: str = Field(default="", max_length=20000)
    affiliations: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    career_history: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    research_areas: list[str] = Field(default_factory=list, max_length=100)
    public_contacts: list[PublicContact] = Field(default_factory=list, max_length=50)
    profile: dict[str, Any] = Field(default_factory=dict)
    sources: list[PublicSource] = Field(min_length=1, max_length=100)
    evidence: list[IntelligenceEvidence] = Field(default_factory=list, max_length=200)
    context_signals: list[ContextSignal] = Field(default_factory=list, max_length=100)
    recommended_personas: list[PersonaMatch] = Field(default_factory=list, max_length=20)
    scenarios: list[EngagementScenario] = Field(default_factory=list, max_length=50)
    engagement_plan: dict[str, Any] = Field(default_factory=dict)
    sample_copywritings: list[SampleCopywriting] = Field(default_factory=list, max_length=30)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    target_id: str = Field(default="", max_length=120)
    project_id: str = Field(default="", max_length=120)
    task_id: str = Field(default="", max_length=160)
