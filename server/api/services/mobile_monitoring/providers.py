"""Provider registry for monitor-specific mobile task profiles."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from api.models.mobile_collect import MobileMonitorCreate
from api.services.wechat_collection import build_company_wechat_task_profile


@dataclass(frozen=True)
class MonitorTargetContext:
    project_id: str
    target_id: str
    target_name: str


class MobileMonitorProvider(Protocol):
    channel: str

    def build_task_definition(
        self,
        request: MobileMonitorCreate,
        target: MonitorTargetContext,
    ) -> dict[str, Any]: ...


class MobileMonitorProviderRegistry:
    _providers: dict[str, MobileMonitorProvider] = {}

    @classmethod
    def register(cls, provider: MobileMonitorProvider) -> None:
        cls._providers[provider.channel] = provider

    @classmethod
    def resolve(cls, channel: str) -> MobileMonitorProvider:
        provider = cls._providers.get(str(channel or "").strip())
        if provider is None:
            raise ValueError(f"不支持的手机监控渠道: {channel}")
        return provider


class WeChatOfficialMonitorProvider:
    channel = "wechat_official"

    def build_task_definition(
        self,
        request: MobileMonitorCreate,
        target: MonitorTargetContext,
    ) -> dict[str, Any]:
        profile = build_company_wechat_task_profile(
            app_instance=request.app_instance
        )
        account_scope = request.scope == "official_account"
        accounts = list(request.official_accounts)
        scope_label = "、".join(accounts) if account_scope else target.target_name
        profile.update(
            {
                "name": request.name or f"增量监控 · {scope_label}",
                "project_id": target.project_id,
                "target_id": target.target_id,
                "target_name": target.target_name,
                "target_type": "company",
                "device_id": request.device_id,
                "app_instance": request.app_instance,
                "keywords": accounts if account_scope else [],
                "use_target_keyword_library": not account_scope,
                "include_direct_children": False,
                "max_relation_depth": 1,
                "max_related_targets": 1,
                "skip_completed_related_targets": True,
                "max_resolved_keywords": max(1, len(accounts)) if account_scope else 24,
                "notify_on": "both",
                "direct_launch_app": True,
                "swipe_times": 4,
                "swipe_interval": 1.8,
                "detail_max_items": 4,
                "detail_max_total_items": 16,
                "detail_review_max_items": 6,
                "detail_review_max_total_items": 24,
                "detail_max_swipes": 6,
                "skip_previously_collected": True,
                "prefer_recent_items": True,
                "max_item_age_days": 45,
                "max_runtime_seconds": 3600,
                "search_hint": (
                    "逐个搜索指定公众号，优先检查最近发布且与关联 Target 有关的文章"
                    if account_scope
                    else "搜索关联 Target 的公众号文章，优先检查最近发布的高相关结果"
                ),
            }
        )
        return profile


MobileMonitorProviderRegistry.register(WeChatOfficialMonitorProvider())
