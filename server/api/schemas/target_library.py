"""Versioned library contracts retain source IDs for downstream analysis joins."""
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field, ConfigDict


class LibraryTarget(BaseModel):
    model_config = ConfigDict(extra="allow")
    target_id: str
    target_name: str
    member_target_ids: list[str]
    aliases: list[str] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    parent_target_id: str = ""
    parent_target_name: str = ""
    child_count: int = 0
    merged_count: int = 1
    relation_conflict: bool = False
    document_count: int = 0
    version_count: int = 0
    change_count: int = 0
    mobile_count: int = 0
    bidding_count: int = 0
    scan_count: int = 0
    first_scan_at: datetime | None = None
    last_scan_at: datetime | None = None
    last_success_at: datetime | None = None
    latest_run: dict[str, Any] | None = None
    latest_incremental_run: dict[str, Any] | None = None
    incremental_scopes: list[dict[str, Any]] = Field(default_factory=list)


class LibraryPage(BaseModel):
    items: list[LibraryTarget]
    total: int
    page: int
    page_size: int
    target_count: int
    original_target_count: int
    scoped_target_count: int
    generated_at: datetime


class LibraryHistoryPage(BaseModel):
    items: list[dict[str, Any]]
    total: int
    skip: int
    limit: int
