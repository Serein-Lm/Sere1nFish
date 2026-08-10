"""Finding 上下文 Agent 的稳定结构化契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ContextFact(BaseModel):
    statement: str = ""
    kind: Literal["fact", "inference"] = "fact"
    confidence: int = Field(default=0, ge=0, le=100)
    evidence_refs: list[str] = Field(default_factory=list)


class ContextNarrative(BaseModel):
    text: str = ""
    kind: Literal["fact", "inference"] = "fact"
    confidence: int = Field(default=0, ge=0, le=100)
    evidence_refs: list[str] = Field(default_factory=list)


class ContextParty(BaseModel):
    name: str = ""
    role: str = ""
    relationship: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class ContextTimelineEvent(BaseModel):
    time: str = ""
    event: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class ContextVisualFinding(BaseModel):
    evidence_ref: str = ""
    summary: str = ""
    visible_text: str = ""
    relevance: str = ""


class FindingContextResult(BaseModel):
    title: str = ""
    overview: ContextNarrative = Field(default_factory=ContextNarrative)
    target_relationship: ContextNarrative = Field(default_factory=ContextNarrative)
    source_overview: ContextNarrative = Field(default_factory=ContextNarrative)
    business_background: ContextNarrative = Field(default_factory=ContextNarrative)
    event_context: ContextNarrative = Field(default_factory=ContextNarrative)
    contact_context: ContextNarrative = Field(default_factory=ContextNarrative)
    finding_interpretation: ContextNarrative = Field(default_factory=ContextNarrative)
    parties: list[ContextParty] = Field(default_factory=list)
    timeline: list[ContextTimelineEvent] = Field(default_factory=list)
    key_facts: list[ContextFact] = Field(default_factory=list)
    visual_findings: list[ContextVisualFinding] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    reading_guide: list[str] = Field(default_factory=list)
