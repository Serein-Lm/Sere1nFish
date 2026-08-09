"""社交平台采集 adapter registry。

平台差异只在这里声明；任务编排和 mobile pipeline 只消费统一 CollectTaskDef。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from api.models.social_collection import SocialCollectionRequest


class SocialPlatformAdapter(Protocol):
    platform: str
    label: str
    app_name: str

    def build_keywords(self, request: SocialCollectionRequest) -> list[str]: ...

    def build_task(self, request: SocialCollectionRequest) -> dict[str, Any]: ...


_COMMON_FIELDS = [
    {"name": "content_title", "description": "地点、视频或评价条目标题", "type": "string"},
    {"name": "place_name", "description": "画面明确对应的地点名称", "type": "string"},
    {"name": "author", "description": "发布者或评价者昵称", "type": "string"},
    {"name": "review_text", "description": "图片附近的评价或评论上下文", "type": "string"},
    {"name": "location", "description": "可见的地址、商圈或定位信息", "type": "string"},
    {"name": "publish_time", "description": "可见发布时间或评价时间", "type": "string"},
    {"name": "media_count", "description": "条目可见图片数量", "type": "string"},
    {"name": "content_type", "description": "地点、评价、图文或视频", "type": "string"},
]


@dataclass(frozen=True, slots=True)
class BaseSocialPlatformAdapter:
    platform: str
    label: str
    app_name: str
    search_suffixes: tuple[str, ...]
    search_hint: str
    gallery_hint: str

    def build_keywords(self, request: SocialCollectionRequest) -> list[str]:
        if request.keywords:
            return list(request.keywords)
        values = [
            f"{request.place_name}{suffix}".strip()
            for suffix in self.search_suffixes
        ]
        return list(dict.fromkeys(value for value in values if value))

    def build_task(self, request: SocialCollectionRequest) -> dict[str, Any]:
        keywords = self.build_keywords(request)
        return {
            "name": f"{request.place_name} - {self.label}公开图片采集",
            "project_id": request.project_id,
            "target_id": request.target_id,
            "target_name": "",
            "target_type": "location",
            "device_id": request.device_id,
            "app_name": self.app_name,
            "app_instance": request.app_instance,
            "keywords": keywords,
            "use_target_keyword_library": False,
            "include_direct_children": False,
            "swipe_times": request.search_swipes,
            "swipe_interval": 1.2,
            "extract_fields": list(_COMMON_FIELDS),
            "dedup_key_fields": ["content_title", "author", "place_name"],
            "notify_on": "none",
            "search_hint": self.search_hint,
            "deep_collect": True,
            "source_link_strategy": "none",
            "search_navigation_strategy": "none",
            "candidate_policy": "social_place_media",
            "detail_capture_strategy": "social_place_gallery",
            "score_policy": "raw",
            "extract_contact_findings": False,
            "resolve_target_context": bool(request.target_id),
            "require_persist_success": True,
            "direct_launch_app": True,
            "detail_max_items": request.max_results_per_platform,
            "detail_max_total_items": request.max_results_per_platform,
            "detail_review_max_items": min(20, request.max_results_per_platform * 2),
            "detail_review_max_total_items": min(40, request.max_results_per_platform * 2),
            "detail_max_swipes": 0,
            "min_score_to_detail": 55,
            "min_subject_match": 70,
            "min_score_to_persist": 50,
            "max_runtime_seconds": 3600,
            "collection_subject": request.place_name,
            "collection_goal": request.collection_goal,
            "platform": self.platform,
            "media_max_items": request.max_images_per_result,
            "media_navigation_hint": self.gallery_hint,
            "record_source_type": "social_place_media",
            "progress_source": f"social_photos_{self.platform}",
            "progress_label": f"{self.label}图片采集",
        }


class SocialPlatformRegistry:
    _adapters: dict[str, SocialPlatformAdapter] = {}

    @classmethod
    def register(cls, adapter: SocialPlatformAdapter) -> None:
        key = str(adapter.platform or "").strip().lower()
        if not key:
            raise ValueError("社交平台 adapter 缺少 platform")
        cls._adapters[key] = adapter

    @classmethod
    def resolve(cls, platform: str) -> SocialPlatformAdapter:
        key = str(platform or "").strip().lower()
        try:
            return cls._adapters[key]
        except KeyError as exc:
            raise ValueError(f"不支持的社交采集平台: {platform}") from exc

    @classmethod
    def catalog(cls) -> list[dict[str, str]]:
        return [
            {
                "platform": adapter.platform,
                "label": adapter.label,
                "app_name": adapter.app_name,
            }
            for adapter in cls._adapters.values()
        ]


SocialPlatformRegistry.register(
    BaseSocialPlatformAdapter(
        platform="meituan",
        label="美团",
        app_name="美团",
        search_suffixes=("",),
        search_hint=(
            "搜索后停留在地点或商户结果列表；优先保留名称和地址明确对应搜索地点的结果，"
            "不要下单、收藏、点赞、关注或发布内容"
        ),
        gallery_hint=(
            "进入地点详情的评价区域，优先选择有图评价或全部图片；打开第一张公开用户图片"
        ),
    )
)
SocialPlatformRegistry.register(
    BaseSocialPlatformAdapter(
        platform="douyin",
        label="抖音",
        app_name="抖音",
        search_suffixes=("", " 实拍"),
        search_hint=(
            "搜索后停留在综合或视频结果列表；只选择标题、定位或画面明确对应搜索地点的公开内容，"
            "不要点赞、评论、关注、私信或发布内容"
        ),
        gallery_hint=(
            "进入公开内容后查看评论区中的图片评论；有图片时打开第一张图片。"
            "若没有任何图片评论，不要互动，也不要用无关视频帧冒充评论图片"
        ),
    )
)
