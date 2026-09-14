"""Public mobile increment bulletin contract."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class IncrementalEvent(BaseModel):
    event_id: str
    project_id: str
    target_id: str
    target_name: str
    task_def_id: str
    run_task_id: str
    record_id: str
    kind: Literal["new", "changed"]
    title: str
    summary: str = ""
    source_url: str = ""
    published_at: datetime | None = None
    published_label: str = ""
    detected_at: datetime
    window_since: datetime | None = None
    window_until: datetime | None = None
    delivery_status: str = "pending"


class IncrementalFeed(BaseModel):
    items: list[IncrementalEvent] = Field(default_factory=list)
    new_count: int = 0
    changed_count: int = 0
    unread_count: int = 0
    generated_at: datetime
    days: int = 7
