"""社交地点图片采集 API 契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


SocialPlatform = Literal["meituan", "douyin"]


class SocialCollectionRequest(BaseModel):
    """创建一个由手机执行、按平台串行的地点图片采集任务。"""

    project_id: str = Field(min_length=1, max_length=64)
    place_name: str = Field(min_length=1, max_length=200, description="地点或场所名称")
    device_id: str = Field(
        min_length=1,
        max_length=200,
        description="设备池中的稳定 device_id；任务会等待设备租约，不会抢占",
    )
    platforms: list[SocialPlatform] = Field(
        default_factory=lambda: ["meituan", "douyin"],
        min_length=1,
        max_length=2,
    )
    keywords: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="可选显式搜索词；为空时由平台 adapter 根据地点生成",
    )
    collection_goal: str = Field(
        default="收集能够辨认地点环境、设施和现场情况的公开用户图片",
        max_length=500,
    )
    target_id: str = Field(default="", max_length=64)
    max_results_per_platform: int = Field(default=6, ge=1, le=20)
    max_images_per_result: int = Field(default=8, ge=1, le=20)
    search_swipes: int = Field(default=5, ge=0, le=20)
    app_instance: Literal["primary", "clone"] = "primary"
    request_key: str = Field(
        default="",
        max_length=160,
        description="调用方幂等键；相同键存在未结束任务时复用",
    )

    @field_validator("platforms")
    @classmethod
    def _dedupe_platforms(cls, value: list[SocialPlatform]) -> list[SocialPlatform]:
        return list(dict.fromkeys(value))

    @field_validator("keywords")
    @classmethod
    def _normalize_keywords(cls, value: list[str]) -> list[str]:
        normalized = [" ".join(str(item or "").split()) for item in value]
        if any(len(item) > 120 for item in normalized):
            raise ValueError("单个搜索词不能超过 120 个字符")
        return list(dict.fromkeys(item for item in normalized if item))

    @field_validator(
        "project_id",
        "place_name",
        "device_id",
        "collection_goal",
        "target_id",
        "request_key",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: object) -> str:
        return str(value or "").strip()


class SocialCollectionPreviewRequest(SocialCollectionRequest):
    """仅编译计划，不创建数据库任务，也不访问手机。"""
