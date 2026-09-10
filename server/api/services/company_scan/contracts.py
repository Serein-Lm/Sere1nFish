"""Versioned contracts for company scan orchestration."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from api.services.info_collection.tuning import (
    DEFAULT_ASSET_PROBE_CONCURRENCY,
    DEFAULT_COMPANY_SCAN_CONCURRENCY,
    DEFAULT_COPYWRITING_CONCURRENCY,
    DEFAULT_URL_PROBE_CONCURRENCY,
    DEFAULT_URL_SCAN_CONCURRENCY,
    DEFAULT_XHS_SEARCH_CONCURRENCY,
)


COMPANY_SCAN_PLAN_VERSION = 1


@dataclass(frozen=True, slots=True)
class CompanyScanPlan:
    task_id: str
    project_id: str
    company_name: str
    target_id: str = ""
    batch_id: str = ""
    target_batch_tags: list[str] | tuple[str, ...] | str | None = None
    url_text: str = ""
    urls: tuple[str, ...] = ()
    enable_url_scan: bool = True
    enable_asset_discovery: bool = True
    enable_xhs: bool = False
    enable_subsidiary_xhs: bool = False
    enable_subsidiary_bidding: bool = False
    xhs_target_selection_mode: str = "auto"
    xhs_manual_targets: list[str] | str | None = None
    enable_bidding: bool = False
    enable_bidding_visual_analysis: bool | None = None
    bidding_page_size: int = 20
    bidding_max_records: int = 20
    bidding_lookback_days: int = 30
    enable_wechat: bool = False
    wechat_device_id: str = ""
    wechat_app_instance: str = "primary"
    wechat_target_selection_mode: str = "auto"
    enable_scholar: bool = True
    scholar_direction: str = ""
    scholar_unit_en: str = ""
    scholar_limit: int = 10
    enable_copywriting: bool = True
    xhs_max_notes: int = 100
    xhs_attention_threshold: int = 60
    min_attention_score: int = 40
    profile_copywriting_threshold: int = 60
    fofa_size: int = 200
    hunter_size: int = 200
    asset_probe_concurrency: int = DEFAULT_ASSET_PROBE_CONCURRENCY
    incremental_scan: bool = False
    url_probe_concurrency: int = DEFAULT_URL_PROBE_CONCURRENCY
    url_scan_concurrency: int = DEFAULT_URL_SCAN_CONCURRENCY
    copywriting_concurrency: int = DEFAULT_COPYWRITING_CONCURRENCY
    xhs_search_concurrency: int = DEFAULT_XHS_SEARCH_CONCURRENCY
    enable_control_structure: bool = False
    control_max_depth: int = 1
    control_max_entities: int = 100
    control_lookup_concurrency: int = 4
    control_icp_concurrency: int = 6
    control_scan_concurrency: int = 1
    subsidiary_scan_limit: int = 12
    skip_completed_subsidiaries: bool = True
    company_core_concurrency: int = DEFAULT_COMPANY_SCAN_CONCURRENCY
    website_collection_mode: str = "deep"
    website_root_domains: tuple[str, ...] = ()
    website_required_path_segments: tuple[str, ...] = ()
    requested_by: str = ""
    refresh_target_identity: bool = False
    version: int = COMPANY_SCAN_PLAN_VERSION

    def validate(self) -> None:
        if self.version != COMPANY_SCAN_PLAN_VERSION:
            raise ValueError(f"不支持的公司扫描计划版本: {self.version}")
        if not self.task_id.strip():
            raise ValueError("公司扫描缺少 task_id")
        if not self.project_id.strip():
            raise ValueError("公司扫描缺少 project_id")
        if not self.company_name.strip():
            raise ValueError("公司扫描缺少 company_name")

    @property
    def subsidiary_xhs_enabled(self) -> bool:
        return bool(self.enable_xhs and self.enable_subsidiary_xhs)

    @property
    def subsidiary_bidding_enabled(self) -> bool:
        return bool(self.enable_bidding and self.enable_subsidiary_bidding)

    @property
    def bidding_visual_analysis_enabled(self) -> bool:
        if self.enable_bidding_visual_analysis is None:
            return bool(self.enable_url_scan)
        return bool(self.enable_bidding_visual_analysis)

    @property
    def enabled_core_modules(self) -> dict[str, bool]:
        return {
            "control_structure": self.enable_control_structure,
            "asset_url": self.enable_asset_discovery or self.enable_url_scan,
            "xhs": self.enable_xhs,
            "bidding": self.enable_bidding,
            "scholar": self.enable_scholar,
        }

    @classmethod
    def from_call(cls, **values: Any) -> "CompanyScanPlan":
        values["urls"] = tuple(values.get("urls") or ())
        values["website_root_domains"] = tuple(
            values.get("website_root_domains") or ()
        )
        values["website_required_path_segments"] = tuple(
            values.get("website_required_path_segments") or ()
        )
        return cls(**values)


@dataclass(slots=True)
class CompanyScanRecovery:
    checkpoint_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    retryable_core_modules: set[str] = field(default_factory=set)
    restored_primary_modules: set[str] = field(default_factory=set)
    previous_result: dict[str, Any] = field(default_factory=dict)
    restored_identity: dict[str, Any] | None = None
    restore_core_context: bool = False
    resume_core_completed: bool = False
    resume_mobile_completed: bool = False


@dataclass(slots=True)
class CompanyScanContext:
    """Mutable state shared by registered stages in one run only."""

    owner: Any
    plan: CompanyScanPlan
    result: dict[str, Any]
    recovery: CompanyScanRecovery = field(default_factory=CompanyScanRecovery)
    core_lease: Any = None
    target: dict[str, Any] = field(default_factory=dict)
    target_id: str = ""
    normalized_name: str = ""
    aliases: list[str] = field(default_factory=list)
    router_output: Any = None
    router_profile: Any = None
    scan_profile: dict[str, Any] = field(default_factory=dict)
    selection_context: dict[str, Any] = field(default_factory=dict)
    scholar_resolution: Any = None
    resolved_scholar_unit_en: str = ""
    xhs_selector: Any = None
    xhs_selection_result: Any = None
    root_xhs_enabled: bool = False
    root_wechat_enabled: bool = False
    xhs_succeeded: bool = False
    primary_job_count: int = 0
    failed_primary_jobs: set[str] = field(default_factory=set)
    mobile_jobs: list[tuple[str, Any]] = field(default_factory=list)
    mobile_task: asyncio.Task[list[Any]] | None = None
    mobile_started: asyncio.Event = field(default_factory=asyncio.Event)
    wholly_owned_entities: list[dict[str, Any]] = field(default_factory=list)
    subsidiary_scope: dict[str, Any] = field(default_factory=dict)
    child_xhs_decisions: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def db(self) -> Any:
        return self.owner.db

    @property
    def app_config(self) -> Any:
        return self.owner.app_config
