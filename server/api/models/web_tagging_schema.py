from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


FindingType = Literal[
    "personal_mobile",
    "personal_email",
    "personal_wechat",
    "enterprise_wechat",
    "hr_contact",
    "business_contact",
    "media_contact",
    "customer_service",
    "group_chat",
    "other",
]


InfoScope = Literal["official", "personal", "enterprise"]


InfoChannel = Literal["email", "phone", "wechat", "link", "form", "other"]


InfoRole = Literal[
    "hr",
    "business",
    "media",
    "customer_service",
    "sales",
    "support",
    "pr",
    "other",
]


PartyRole = Literal[
    "purchaser",
    "agency",
    "supplier",
    "publisher",
    "other",
    "unknown",
]


TargetRelation = Literal[
    "confirmed",
    "related",
    "not_target",
    "uncertain",
]


class WebTaggingFinding(BaseModel):
    type: FindingType
    scope: InfoScope
    channel: InfoChannel
    role: InfoRole
    subtype: str | None = None
    label: str | None = None
    value: str | None = None
    context: str
    source_url: str
    evidence: str
    attention_score: int = Field(ge=0, le=100)
    attention_reason: str
    party_name: str | None = None
    party_role: PartyRole = "unknown"
    target_relation: TargetRelation = "uncertain"
    target_relation_reason: str = ""


class WebTaggingIntro(BaseModel):
    url: str
    final_url: str | None = None
    domain: str | None = None
    site_name: str | None = None
    entity_name: str | None = None
    summary: str | None = None


class WebTaggingOutput(BaseModel):
    intro: WebTaggingIntro
    has_findings: bool
    no_findings_reason: str | None = None
    findings: list[WebTaggingFinding]
