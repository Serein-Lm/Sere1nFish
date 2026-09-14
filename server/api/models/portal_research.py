"""Task-scoped official portal research policy and evidence-backed section output."""
from typing import Literal
from pydantic import BaseModel, Field

class PortalResearchOptions(BaseModel):
    include_subordinates: bool = True
    include_parent: bool = False
    max_runtime_seconds: int = Field(default=3600, ge=900, le=7200)
    max_tool_calls: int = Field(default=120, ge=32, le=240)
    context_tokens: int = Field(default=24000, ge=12000, le=48000)
    max_pages: int = Field(default=50, ge=10, le=100)
    dry_run: bool = False

class PortalSection(BaseModel):
    category: Literal["business", "recruitment", "procurement", "investment", "feedback", "organization"]
    summary: str = Field(default="", max_length=6000)
    source_urls: list[str] = Field(default_factory=list, max_length=30)
    status: Literal["covered", "partial", "not_found", "blocked"] = "partial"
    gaps: list[str] = Field(default_factory=list, max_length=20)
