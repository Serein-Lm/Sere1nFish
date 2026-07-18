"""Resolve an optional company-scan scholar research direction."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field


ScholarDirectionSource = Literal[
    "manual",
    "company_router",
    "industry_default",
    "fallback",
]

_INDUSTRY_DEFAULTS = {
    "internet": "computer science information systems",
    "finance": "financial technology risk management",
    "airport": "aviation transportation airport operations",
    "government": "public administration digital government",
    "education": "education technology learning sciences",
    "healthcare": "clinical medicine healthcare technology",
    "manufacturing": "industrial engineering intelligent manufacturing",
    "retail": "consumer behavior retail management",
    "real_estate": "urban planning construction management",
    "energy": "energy systems engineering",
    "logistics": "transportation logistics engineering",
    "telecom": "communication networks information technology",
    "media": "broadcasting technology media convergence",
    "consulting": "management science organizational studies",
    "hotel": "hospitality management tourism",
    "food": "food science supply chain",
    "other": "information technology organizational management",
}
_NAME_INDUSTRY_HINTS = (
    (("广播电视", "电视台", "传媒", "报社", "通讯社"), "media"),
    (("交易所", "银行", "证券", "保险", "清算", "银联"), "finance"),
    (("大学", "教育", "考试院", "留学"), "education"),
    (("医院", "医疗", "卫生"), "healthcare"),
    (("机场", "航空"), "airport"),
    (("电信", "通信", "广电网络", "有线网络"), "telecom"),
    (("钢铁", "汽车", "制造"), "manufacturing"),
    (("能源", "电力", "石油", "煤炭"), "energy"),
)
_QUERY_NOISE_RE = re.compile(
    r"(?:论文|论著|学术|专家|作者|成果|课题|研究方向|研究领域|研究|招聘|"
    r"招标|采购|中标|校招|实习|公众号|联系方式)",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"\s+")


class ScholarDirectionResolution(BaseModel):
    direction: str = Field(min_length=1)
    source: ScholarDirectionSource
    terms: list[str] = Field(default_factory=list)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _clean_router_term(value: Any, names: list[str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for name in sorted(names, key=len, reverse=True):
        normalized_name = str(name or "").strip()
        if normalized_name:
            text = re.sub(re.escape(normalized_name), " ", text, flags=re.IGNORECASE)
    text = _QUERY_NOISE_RE.sub(" ", text)
    text = text.replace("/", " ").replace("|", " ").replace("，", " ")
    text = text.replace(",", " ").replace("；", " ").replace(";", " ")
    text = _SPACE_RE.sub(" ", text).strip(" -_:：")
    if len(text) < 2 or len(text) > 80:
        return ""
    return text


def _infer_industry_from_names(names: list[str]) -> str:
    combined = " ".join(names)
    for markers, industry in _NAME_INDUSTRY_HINTS:
        if any(marker in combined for marker in markers):
            return industry
    return "other"


def resolve_scholar_direction(
    manual_direction: str,
    router_output: Any,
    *,
    names: list[str] | None = None,
) -> ScholarDirectionResolution:
    """Prefer user input, then reuse CompanyRouter's paper/profile analysis."""
    manual = str(manual_direction or "").strip()
    if manual:
        return ScholarDirectionResolution(
            direction=manual,
            source="manual",
            terms=[manual],
        )

    search_names = [str(item).strip() for item in names or [] if str(item).strip()]
    profile = (
        getattr(router_output, "company_profile", None)
        if getattr(router_output, "success", False)
        else None
    )
    strategy = (
        getattr(router_output, "search_strategy", None)
        if getattr(router_output, "success", False)
        else None
    )
    paper = getattr(strategy, "paper", None) if strategy else None
    params = getattr(paper, "params", {}) if paper else {}

    raw_terms: list[Any] = []
    if isinstance(params, dict):
        for key in ("research_direction", "direction", "topic", "topics"):
            raw_terms.extend(_as_text_list(params.get(key)))
    if paper:
        raw_terms.extend(_as_text_list(getattr(paper, "focus_points", [])))
        raw_terms.extend(_as_text_list(getattr(paper, "keywords", [])))
    if profile:
        raw_terms.extend(_as_text_list(getattr(profile, "sub_industries", [])))
        raw_terms.extend(_as_text_list(getattr(profile, "main_business", [])))

    terms: list[str] = []
    for raw in raw_terms:
        cleaned = _clean_router_term(raw, search_names)
        if cleaned and cleaned.casefold() not in {
            item.casefold() for item in terms
        }:
            terms.append(cleaned)
        if len(terms) >= 3:
            break
    if terms:
        return ScholarDirectionResolution(
            direction=" ".join(terms)[:160].strip(),
            source="company_router",
            terms=terms,
        )

    industry = _enum_value(getattr(profile, "industry", "")) if profile else ""
    if not industry:
        industry = _infer_industry_from_names(search_names)
    default_direction = _INDUSTRY_DEFAULTS.get(industry)
    if default_direction:
        return ScholarDirectionResolution(
            direction=default_direction,
            source="industry_default",
            terms=[default_direction],
        )

    fallback = _INDUSTRY_DEFAULTS["other"]
    return ScholarDirectionResolution(
        direction=fallback,
        source="fallback",
        terms=[fallback],
    )
