"""Registry-driven list candidate selection for mobile collection sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class CandidatePolicy(Protocol):
    name: str
    persist_list_candidates: bool
    allow_mobile_detail_fallback: bool

    def analysis_instructions(self, *, target_name: str, aliases: list[str]) -> str: ...

    def navigation_instructions(self) -> str: ...

    def accepts_detail(
        self,
        candidate: dict,
        *,
        min_score: int,
        min_subject_match: int,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class DefaultCandidatePolicy:
    name: str = "default"
    persist_list_candidates: bool = True
    allow_mobile_detail_fallback: bool = True

    def analysis_instructions(self, *, target_name: str, aliases: list[str]) -> str:
        return ""

    def navigation_instructions(self) -> str:
        return ""

    def accepts_detail(
        self,
        candidate: dict,
        *,
        min_score: int,
        min_subject_match: int,
    ) -> bool:
        return (
            int(candidate.get("subject_match") or 0) >= min_subject_match
            and int(candidate.get("score") or 0) >= min_score
            and isinstance(candidate.get("tap_x"), int)
            and isinstance(candidate.get("tap_y"), int)
        )


@dataclass(frozen=True, slots=True)
class WechatArticleCandidatePolicy(DefaultCandidatePolicy):
    name: str = "wechat_article"
    persist_list_candidates: bool = False
    allow_mobile_detail_fallback: bool = False

    def analysis_instructions(self, *, target_name: str, aliases: list[str]) -> str:
        aliases_text = "、".join(value for value in aliases if value) or "无"
        return (
            "当前只允许选择微信公众号文章结果。必须区分文章、公众号账号、视频、直播、"
            "小程序、广告和功能入口；后六类一律不得点击。候选必须有可见的文章标题，且"
            f"标题、摘要或账号中存在与目标“{target_name}”的直接对应证据。可靠别名："
            f"{aliases_text}。仅行业相近、正文可能提及或搜索词命中不足以证明主体一致。"
            "content_kind 必须按画面真实类型填写；只有文章填写 is_article_result=true，"
            "并在 target_evidence 中写出画面上可见的主体对应依据。"
        )

    def navigation_instructions(self) -> str:
        return (
            "搜索后必须停留在“全部”结果页，看到与关键词对应的文章列表后立即完成；"
            "不得切换到账号、视频、直播、商品或其他分类"
        )

    def accepts_detail(
        self,
        candidate: dict,
        *,
        min_score: int,
        min_subject_match: int,
    ) -> bool:
        return (
            DefaultCandidatePolicy.accepts_detail(
                self,
                candidate,
                min_score=min_score,
                min_subject_match=max(80, min_subject_match),
            )
            and candidate.get("content_kind") == "article"
            and candidate.get("is_article_result") is True
            and bool(str(candidate.get("target_evidence") or "").strip())
        )


class CandidatePolicyRegistry:
    _policies: dict[str, CandidatePolicy] = {
        "default": DefaultCandidatePolicy(),
        "wechat_copy_link": WechatArticleCandidatePolicy(),
    }

    @classmethod
    def resolve(cls, strategy: str) -> CandidatePolicy:
        return cls._policies.get(str(strategy or "").strip(), cls._policies["default"])
