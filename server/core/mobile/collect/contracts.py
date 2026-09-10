"""Versioned contracts for the mobile collection runtime."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase


MOBILE_COLLECT_PLAN_VERSION = 1


@dataclass(slots=True)
class MobileCollectPlan:
    """Validated, immutable-at-the-boundary input for one collection run."""

    db: AsyncIOMotorDatabase
    run_task_id: str
    project_id: str | None
    task_def: dict[str, Any]
    dry_run: bool = False
    preview_limit: int = 50
    version: int = MOBILE_COLLECT_PLAN_VERSION

    @property
    def task_def_id(self) -> str:
        return str(self.task_def.get("task_def_id") or "").strip()

    @property
    def device_id(self) -> str:
        return str(self.task_def.get("device_id") or "").strip()

    @property
    def owner(self) -> str:
        return f"collect:{self.run_task_id}"

    @property
    def runtime_limit(self) -> int:
        return max(0, int(self.task_def.get("max_runtime_seconds") or 0))

    def validate(self) -> None:
        if self.version != MOBILE_COLLECT_PLAN_VERSION:
            raise ValueError(f"不支持的手机采集计划版本: {self.version}")
        if not self.run_task_id:
            raise ValueError("手机采集缺少 run_task_id")
        if not self.task_def_id:
            raise ValueError("手机采集缺少 task_def_id")
        if not self.device_id:
            raise ValueError("手机采集缺少 device_id")
        extract_fields = list(self.task_def.get("extract_fields") or [])
        if (
            bool(self.task_def.get("deep_collect"))
            and str(self.task_def.get("source_link_strategy") or "none") != "none"
            and not extract_fields
        ):
            raise ValueError(
                "手机详情深采已启用，但 extract_fields 为空；"
                "无法生成相关性、主体匹配和点击坐标"
            )


@dataclass(slots=True)
class MobileSeedPlan:
    """Resolved keyword/Target seeds and their durable checkpoint identity."""

    target: dict[str, Any] | None
    keyword_resolution: dict[str, Any]
    definition_fingerprint: str
    seed_specs: list[dict[str, Any]] = field(default_factory=list)
    pending_seed_specs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def keywords(self) -> list[str]:
        return list(self.keyword_resolution.get("keywords") or [""])

    @property
    def completed_count(self) -> int:
        return len(self.seed_specs) - len(self.pending_seed_specs)


@dataclass(slots=True)
class MobileCollectExecution:
    """Mutable runtime state kept outside the public plan."""

    plan: MobileCollectPlan
    seeds: MobileSeedPlan
    state: dict[str, Any]
    metrics: dict[str, Any] = field(default_factory=dict)
    timed_out: bool = False
