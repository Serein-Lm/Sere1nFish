"""Fictional persona completeness and evidence checks, without inventing facts."""
from __future__ import annotations

from typing import Any, Sequence


_PROFILE_PLACEHOLDER_MARKERS = (
    "信息缺失",
    "内容缺失",
    "模式缺失",
    "偏好缺失",
    "动机缺失",
    "待补充",
    "待验证",
    "待定",
    "未知",
    "无法确认",
    "无法支撑",
    "未获取",
    "无实证",
    "空壳",
    "占位",
    "假设性描述",
)
_RESEARCH_GAP_MARKERS = (
    "未覆盖",
    "无法支撑",
    "未获取",
    "未提供",
    "无实证",
    "缺乏实证",
    "缺乏企业层面",
    "缺乏一线",
    "空壳",
    "留白",
    "研究缺口",
)


def _contains_marker(value: Any, markers: Sequence[str]) -> bool:
    text = str(value or "").strip()
    return bool(text) and any(marker in text for marker in markers)


def _is_concrete_profile_value(value: Any) -> bool:
    return value not in (None, "", [], {}) and not _contains_marker(
        value,
        _PROFILE_PLACEHOLDER_MARKERS,
    )


def _research_evidence_has_gap(value: Any) -> bool:
    payload = (
        value.model_dump()
        if hasattr(value, "model_dump")
        else dict(value or {})
        if isinstance(value, dict)
        else {}
    )
    return any(
        _contains_marker(payload.get(field), _RESEARCH_GAP_MARKERS)
        for field in ("dimension", "finding", "applicability")
    )


def _profile_quality_issues(profile: dict[str, Any]) -> list[str]:
    """Validate richness without constructing or rewriting any persona facts."""
    from Sere1nGraph.graph.skills.schemas import RichFictionalPersonaProfile

    issues: list[str] = []
    for key, definition in RichFictionalPersonaProfile.model_fields.items():
        if not definition.is_required():
            continue
        value = profile.get(key)
        if isinstance(value, str):
            value = value.strip()
        if value in (None, "", [], {}):
            issues.append(f"{key} 不能为空")
        elif isinstance(value, list) and any(isinstance(item, str) and not item.strip() for item in value):
            issues.append(f"{key} 包含空白条目")
    for key in ("school", "degree", "major", "graduation_year"):
        if not str((profile.get("education") or {}).get(key) or "").strip():
            issues.append(f"education.{key} 不能为空")
    scalar_fields = (
        "background",
        "career_path",
        "collaboration_style",
        "communication_style",
        "decision_style",
        "learning_style",
        "life_stage",
        "organization_context",
        "personality",
        "stress_response",
        "summary",
        "technology_attitude",
        "work_context",
        "work_rhythm",
    )
    list_fields = (
        "behavior_patterns",
        "content_preferences",
        "digital_habits",
        "goals",
        "information_preferences",
        "interests",
        "motivations",
        "pain_points",
        "purchase_considerations",
        "risk_signals",
        "tags",
        "values",
    )
    for field in scalar_fields:
        value = str(profile.get(field) or "").strip()
        if _contains_marker(value, _PROFILE_PLACEHOLDER_MARKERS):
            issues.append(f"{field} 包含缺失或占位描述")
    for field in list_fields:
        if any(
            _contains_marker(item, _PROFILE_PLACEHOLDER_MARKERS)
            for item in profile.get(field) or []
        ):
            issues.append(f"{field} 包含缺失或占位条目")
    if any(
        _research_evidence_has_gap(item)
        for item in profile.get("research_evidence") or []
    ):
        issues.append("research_evidence 包含研究缺口或待补充证据")
    if len(str(profile.get("summary") or "").strip()) < 80:
        issues.append("summary 未形成可独立检索的具体首层摘要")
    if len(str(profile.get("background") or "").strip()) < 80:
        issues.append("background 缺少完整职业与生活时间线")
    return issues
