"""Registry-driven list candidate selection for mobile collection sources."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


_WECHAT_SAFE_TAP_TOP = 140
_WECHAT_SAFE_TAP_BOTTOM = 780


class CandidatePolicy(Protocol):
    name: str
    persist_list_candidates: bool
    allow_mobile_detail_fallback: bool
    max_details_per_screen: int
    requires_detail_verification: bool
    retry_detail_verification_at_top: bool

    def analysis_instructions(self, *, target_name: str, aliases: list[str]) -> str: ...

    def navigation_instructions(self) -> str: ...

    def detail_verification_instructions(self) -> str: ...

    def review_detail(
        self,
        candidate: dict,
        *,
        min_score: int,
        min_subject_match: int,
        target_name: str = "",
        aliases: list[str] | None = None,
    ) -> CandidateDecision: ...

    def accepts_detail(
        self,
        candidate: dict,
        *,
        min_score: int,
        min_subject_match: int,
        target_name: str = "",
        aliases: list[str] | None = None,
    ) -> bool: ...

    def review_opened_detail(
        self,
        verification: dict,
        *,
        candidate: dict,
        target_name: str,
        aliases: list[str],
        min_subject_match: int,
    ) -> CandidateDecision: ...


def candidate_tap_bounds(candidate: dict) -> tuple[int, int, int, int] | None:
    bounds = candidate.get("tap_bounds")
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        return None
    if not all(isinstance(value, int) for value in bounds):
        return None
    left, top, right, bottom = bounds
    if 0 <= left < right <= 1000 and 0 <= top < bottom <= 1000:
        return left, top, right, bottom
    return None


def candidate_tap_point(candidate: dict) -> tuple[int, int] | None:
    """Resolve a safe row-center tap while retaining legacy point support."""
    if bounds := candidate_tap_bounds(candidate):
        left, top, right, bottom = bounds
        return (left + right) // 2, (top + bottom) // 2
    x = candidate.get("tap_x")
    y = candidate.get("tap_y")
    if isinstance(x, int) and isinstance(y, int) and 0 <= x <= 1000 and 0 <= y <= 1000:
        return x, y
    return None


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    accepted: bool
    reason: str


@dataclass(frozen=True, slots=True)
class DefaultCandidatePolicy:
    name: str = "default"
    persist_list_candidates: bool = True
    allow_mobile_detail_fallback: bool = True
    max_details_per_screen: int = 0
    requires_detail_verification: bool = False
    retry_detail_verification_at_top: bool = False

    def analysis_instructions(self, *, target_name: str, aliases: list[str]) -> str:
        return ""

    def navigation_instructions(self) -> str:
        return ""

    def detail_verification_instructions(self) -> str:
        return ""

    def review_detail(
        self,
        candidate: dict,
        *,
        min_score: int,
        min_subject_match: int,
        target_name: str = "",
        aliases: list[str] | None = None,
    ) -> CandidateDecision:
        failures: list[str] = []
        if int(candidate.get("subject_match") or 0) < min_subject_match:
            failures.append("主体对应度不足")
        if int(candidate.get("score") or 0) < min_score:
            failures.append("内容价值分不足")
        if candidate_tap_point(candidate) is None:
            failures.append("缺少可靠点击坐标")
        return CandidateDecision(
            accepted=not failures,
            reason="；".join(failures) if failures else "通过点击前审核",
        )

    def accepts_detail(
        self,
        candidate: dict,
        *,
        min_score: int,
        min_subject_match: int,
        target_name: str = "",
        aliases: list[str] | None = None,
    ) -> bool:
        return self.review_detail(
            candidate,
            min_score=min_score,
            min_subject_match=min_subject_match,
            target_name=target_name,
            aliases=aliases,
        ).accepted

    def review_opened_detail(
        self,
        verification: dict,
        *,
        candidate: dict,
        target_name: str,
        aliases: list[str],
        min_subject_match: int,
    ) -> CandidateDecision:
        return CandidateDecision(True, "当前来源无需详情页二次校验")


@dataclass(frozen=True, slots=True)
class WechatArticleCandidatePolicy(DefaultCandidatePolicy):
    name: str = "wechat_article"
    persist_list_candidates: bool = False
    allow_mobile_detail_fallback: bool = False
    max_details_per_screen: int = 1
    requires_detail_verification: bool = True
    retry_detail_verification_at_top: bool = True

    def analysis_instructions(self, *, target_name: str, aliases: list[str]) -> str:
        aliases_text = "、".join(value for value in aliases if value) or "无"
        return (
            "当前只允许选择微信公众号文章结果。必须区分文章、公众号账号、视频、直播、"
            "小程序、广告和功能入口；后六类一律不得点击。候选必须有可见的文章标题，且"
            f"标题或账号中存在与目标“{target_name}”的直接身份对应。摘要仅提及目标不能"
            "作为点击依据。可靠别名："
            f"{aliases_text}。仅行业相近、正文可能提及或搜索词命中不足以证明主体一致。"
            "content_kind 必须按画面真实类型填写；只有文章填写 is_article_result=true，"
            "并在 target_evidence 中写出画面上可见的主体对应依据。只返回整条卡片完整"
            "可见的候选；屏幕顶部或底部被裁切的卡片不得作为可点击候选。"
        )

    def navigation_instructions(self) -> str:
        return (
            "搜索后必须停留在“全部”结果页，看到与关键词对应的文章列表后立即完成；"
            "不得切换到账号、视频、直播、商品或其他分类"
        )

    def detail_verification_instructions(self) -> str:
        return (
            "微信公众号文章必须显示图文详情页标题；列表页、视频、公众号主页、搜索页或"
            "加载空白都不通过。当前标题/账号必须与点击前候选一致，且文章核心主体必须"
            "就是目标主体；行业汇总、招标周报和多主体合集即使包含目标也要降低主体分。"
        )

    def review_detail(
        self,
        candidate: dict,
        *,
        min_score: int,
        min_subject_match: int,
        target_name: str = "",
        aliases: list[str] | None = None,
    ) -> CandidateDecision:
        base = DefaultCandidatePolicy.review_detail(
            self,
            candidate,
            min_score=min_score,
            min_subject_match=max(80, min_subject_match),
            target_name=target_name,
            aliases=aliases,
        )
        failures = [] if base.accepted else [base.reason]
        if candidate.get("content_kind") != "article":
            failures.append("不是图文文章结果")
        if candidate.get("is_article_result") is not True:
            failures.append("文章类型未确认")
        if not str(candidate.get("target_evidence") or "").strip():
            failures.append("缺少可见主体证据")
        candidate_fields = candidate.get("fields") or {}
        if target_name and not _has_direct_target_identity(
            title=candidate_fields.get("title"),
            account=candidate_fields.get("account"),
            target_name=target_name,
            aliases=aliases or [],
        ):
            failures.append("候选标题或账号未直接证明目标主体")
        bounds = candidate_tap_bounds(candidate)
        if bounds is None:
            failures.append("缺少完整可点击区域")
        else:
            _left, top, _right, bottom = bounds
            if top < _WECHAT_SAFE_TAP_TOP or bottom > _WECHAT_SAFE_TAP_BOTTOM:
                failures.append("条目位于屏幕边缘或未完整显示")
        return CandidateDecision(
            accepted=not failures,
            reason="；".join(failures) if failures else "文章类型和主体证据审核通过",
        )

    def review_opened_detail(
        self,
        verification: dict,
        *,
        candidate: dict,
        target_name: str,
        aliases: list[str],
        min_subject_match: int,
    ) -> CandidateDecision:
        failures: list[str] = []
        if verification.get("page_kind") != "article":
            failures.append("点击后不是图文文章详情页")
        if int(verification.get("candidate_match") or 0) < 75:
            failures.append("详情与点击前候选不一致")
        if int(verification.get("target_match") or 0) < max(80, min_subject_match):
            failures.append("详情核心主体与目标不一致")
        if not str(verification.get("visible_title") or "").strip():
            failures.append("详情页未识别到可见标题")
        candidate_title = (candidate.get("fields") or {}).get("title")
        if not _titles_correspond(
            candidate_title,
            verification.get("visible_title"),
        ):
            failures.append("详情标题与点击前候选标题不一致")
        if target_name and not _has_direct_target_identity(
            title=verification.get("visible_title"),
            account=verification.get("visible_account"),
            target_name=target_name,
            aliases=aliases,
        ):
            failures.append("详情标题或账号未直接出现目标主体")
        return CandidateDecision(
            accepted=not failures,
            reason="；".join(failures) if failures else "详情页、候选和目标主体一致",
        )


def _normalized_identity_text(value: object) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").casefold())


def _titles_correspond(candidate_title: object, visible_title: object) -> bool:
    candidate = _normalized_identity_text(candidate_title)
    visible = _normalized_identity_text(visible_title)
    if not candidate or not visible:
        return False
    return candidate in visible or visible in candidate


def _has_direct_target_identity(
    *,
    title: object,
    account: object,
    target_name: str,
    aliases: list[str],
) -> bool:
    normalized_title = _normalized_identity_text(title)
    normalized_account = _normalized_identity_text(account)
    canonical = _normalized_identity_text(target_name)
    if canonical and (
        canonical in normalized_title or canonical in normalized_account
    ):
        return True
    normalized_aliases = {
        normalized
        for alias in aliases
        if len(normalized := _normalized_identity_text(alias)) >= 3
    }
    return (
        normalized_title in normalized_aliases
        or normalized_account in normalized_aliases
    )


class CandidatePolicyRegistry:
    _policies: dict[str, CandidatePolicy] = {
        "default": DefaultCandidatePolicy(),
        "wechat_copy_link": WechatArticleCandidatePolicy(),
    }

    @classmethod
    def resolve(cls, strategy: str) -> CandidatePolicy:
        return cls._policies.get(str(strategy or "").strip(), cls._policies["default"])
