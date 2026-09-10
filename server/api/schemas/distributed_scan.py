"""HTTP schemas for the distributed scan control plane."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class NodeBootstrapCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    allowed_capabilities: list[str] = Field(
        default_factory=lambda: ["http_probe", "browser_probe"],
        min_length=1,
        max_length=20,
    )
    labels: dict[str, str] = Field(default_factory=dict, max_length=30)
    expires_minutes: int = Field(default=30, ge=5, le=1440)


class NodeRegister(BaseModel):
    bootstrap_token: str = Field(min_length=32, max_length=512)
    display_name: str = Field(default="", max_length=120)
    capabilities: dict[str, str] = Field(default_factory=dict, min_length=1, max_length=20)
    capacity: dict[str, int] = Field(default_factory=dict, max_length=20)
    labels: dict[str, str] = Field(default_factory=dict, max_length=30)
    version: str = Field(default="unknown", max_length=80)


class NodeHeartbeat(BaseModel):
    usage: dict[str, int] = Field(default_factory=dict, max_length=50)
    capabilities: dict[str, str] | None = Field(default=None, max_length=20)
    capacity: dict[str, int] | None = Field(default=None, max_length=20)
    active_work_item_ids: list[str] = Field(default_factory=list, max_length=200)
    version: str = Field(default="", max_length=80)


class NodeLeaseRequest(BaseModel):
    kinds: list[str] = Field(default_factory=list, max_length=20)
    wait_seconds: int = Field(default=10, ge=0, le=25)


class WorkStarted(BaseModel):
    lease_token: str = Field(min_length=32, max_length=512)
    event_seq: int = Field(ge=1)


class WorkLeaseRenew(BaseModel):
    lease_token: str = Field(min_length=32, max_length=512)
    lease_seconds: int = Field(default=90, ge=30, le=300)


class WorkProgress(BaseModel):
    lease_token: str = Field(min_length=32, max_length=512)
    event_seq: int = Field(ge=1)
    progress: dict[str, Any] = Field(default_factory=dict)


class WorkCompleted(BaseModel):
    lease_token: str = Field(min_length=32, max_length=512)
    event_seq: int = Field(ge=1)
    result: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list, max_length=50)


class WorkFailed(BaseModel):
    lease_token: str = Field(min_length=32, max_length=512)
    event_seq: int = Field(ge=1)
    error_code: str = Field(default="worker_error", max_length=100)
    error: str = Field(min_length=1, max_length=4000)
    retryable: bool = True
    retry_delay_seconds: int = Field(default=5, ge=0, le=3600)


class WorkCreate(BaseModel):
    kind: Literal["http_probe", "browser_probe"]
    payload: dict[str, Any]
    project_id: str = Field(default="", max_length=128)
    task_id: str = Field(default="", max_length=128)
    target_id: str = Field(default="", max_length=128)
    proxy_profile_id: str = Field(default="", max_length=128)
    proxy_mode: Literal["none", "best_effort", "required"] = "none"
    affinity_key: str = Field(default="", max_length=300)
    priority: int = Field(default=50, ge=0, le=100)
    max_attempts: int = Field(default=3, ge=1, le=10)
    idempotency_key: str = Field(default="", max_length=256)


class NodeStatusUpdate(BaseModel):
    status: Literal["online", "draining", "offline", "disabled"]


class ProxyProfileUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    endpoint: str | None = Field(default=None, max_length=1000)
    username: str | None = Field(default=None, max_length=500)
    password: str | None = Field(default=None, max_length=1000)
    dns_mode: Literal["remote", "local"] = "remote"
    max_concurrency: int = Field(default=8, ge=1, le=500)
    failure_threshold: int = Field(default=3, ge=1, le=20)
    cooldown_seconds: int = Field(default=300, ge=10, le=86400)
    bypass_classes: list[str] = Field(default_factory=list, max_length=50)
    labels: dict[str, str] = Field(default_factory=dict, max_length=30)
    status: Literal["active", "disabled"] = "active"
