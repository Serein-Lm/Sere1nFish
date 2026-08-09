"""采集分析 — 对截图做结构化视觉提取 + 相关性打分。

三种能力(均复用 Sere1nGraph create_llm + runtime 模型配置 mobile_screen_model,
用 observation_context 归因 token,不绑定单一供应商):
- triage_screenshot: 对**列表页**截图,识别所有可见条目,给出每条的结构化字段、
  相关性分(0-100)、打分理由,以及可点击中心点坐标(0-1000 归一化),用于「列表全收 + 详情选采」;
- analyze_detail: 对**详情页**(点进后可多张截图)做综合结构化,产出单条富记录 + 分数;
- analyze_screenshot: 不深采时的列表整屏结构化(每条带分数),或无字段时的整屏摘要。

统一返回记录形状:
  {"fields": dict, "score": int|None, "score_reason": str, "tap_x": int|None, "tap_y": int|None}
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Annotated, Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, BeforeValidator, Field, create_model, model_validator

from Sere1nGraph.graph.agents.runtime import create_llm
from api.services.runtime_config import get_runtime_app_config
from core.mobile.prompt_runtime import load_mobile_prompt
from core.mobile.vision_payload import prepare_vision_data_url
from core.observability import observation_context

from api.models.mobile_collect import ExtractField


def _coerce_text(value: Any) -> str | None:
    """Keep one malformed model field from discarding the whole screen."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, (list, tuple, set)):
        return "；".join(
            text for item in value if (text := _coerce_text(item)) not in (None, "")
        )
    return str(value)


def _coerce_required_text(value: Any) -> str:
    return _coerce_text(value) or ""


def _coerce_string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return [
        text
        for item in values
        if (text := _coerce_text(item)) not in (None, "")
    ]


def _coerce_number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def _coerce_boolean(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().casefold()
    if text in {"true", "yes", "y", "1", "是", "有"}:
        return True
    if text in {"false", "no", "n", "0", "否", "无", "没有"}:
        return False
    return None


def _coerce_required_boolean(value: Any) -> bool:
    return bool(_coerce_boolean(value))


def _coerce_required_int(value: Any) -> int:
    number = _coerce_number(value)
    return int(number) if number is not None else 0


def _coerce_optional_int(value: Any) -> int | None:
    number = _coerce_number(value)
    return int(number) if number is not None else None


def _coerce_content_kind(value: Any) -> str:
    text = (_coerce_text(value) or "").casefold()
    allowed = {
        "article",
        "account",
        "video",
        "live",
        "mini_program",
        "place",
        "review",
        "image",
        "ad",
        "other",
    }
    if text in allowed:
        return text
    aliases = {
        "文章": "article",
        "图文": "article",
        "公众号": "account",
        "账号": "account",
        "视频": "video",
        "直播": "live",
        "小程序": "mini_program",
        "地点": "place",
        "评价": "review",
        "评论": "review",
        "图片": "image",
        "广告": "ad",
    }
    return next((kind for label, kind in aliases.items() if label in text), "other")


_OptionalText = Annotated[str | None, BeforeValidator(_coerce_text)]
_RequiredText = Annotated[str, BeforeValidator(_coerce_required_text)]
_OptionalNumber = Annotated[float | None, BeforeValidator(_coerce_number)]
_OptionalBoolean = Annotated[bool | None, BeforeValidator(_coerce_boolean)]
_RequiredBoolean = Annotated[bool, BeforeValidator(_coerce_required_boolean)]
_StringList = Annotated[list[str], BeforeValidator(_coerce_string_list)]
_RequiredInt = Annotated[int, BeforeValidator(_coerce_required_int)]
_OptionalInt = Annotated[int | None, BeforeValidator(_coerce_optional_int)]
_ContentKind = Annotated[
    Literal[
        "article",
        "account",
        "video",
        "live",
        "mini_program",
        "place",
        "review",
        "image",
        "ad",
        "other",
    ],
    BeforeValidator(_coerce_content_kind),
]

_PY_TYPE = {
    "string": (_OptionalText, None),
    "number": (_OptionalNumber, None),
    "boolean": (_OptionalBoolean, None),
    "list": (_StringList, ...),
}

# schema 内固定追加的字段名(与业务字段区分,便于从结果里剥离)
_SCORE_KEYS = {
    "subject_match",
    "relevance_score",
    "score_reason",
    "tap_x",
    "tap_y",
    "tap_left",
    "tap_top",
    "tap_right",
    "tap_bottom",
    "source_url",
    "content_kind",
    "is_article_result",
    "target_evidence",
}
_DETAIL_VERIFICATION_PROMPT_SLUG = "mobile_collect/detail_verification"
_SOCIAL_MEDIA_FRAME_PROMPT_SLUG = "mobile_collect/social_media_frame"


class DetailEntryVerification(BaseModel):
    """Visual contract used to prove that a list tap opened the intended item."""

    page_kind: Literal[
        "article",
        "list",
        "video",
        "account",
        "place",
        "review",
        "gallery",
        "image",
        "loading",
        "other",
    ] = "other"
    visible_title: str = ""
    visible_account: str = ""
    candidate_match: int = Field(
        default=0,
        ge=0,
        le=100,
        description="当前详情与点击前候选为同一内容的置信分，必须使用 0-100 整数",
    )
    target_match: int = Field(
        default=0,
        ge=0,
        le=100,
        description="当前详情以目标主体为核心的对应分，必须使用 0-100 整数",
    )
    evidence: str = ""
    reason: str = ""


class SocialMediaFrameAnalysis(BaseModel):
    """Visual contract for one image opened from a public review/comment."""

    is_media_viewer: bool = False
    useful: bool = False
    subject_match: int = Field(default=0, ge=0, le=100)
    candidate_match: int = Field(default=0, ge=0, le=100)
    image_left: int = Field(default=0, ge=0, le=1000)
    image_top: int = Field(default=0, ge=0, le=1000)
    image_right: int = Field(default=0, ge=0, le=1000)
    image_bottom: int = Field(default=0, ge=0, le=1000)
    photo_description: str = ""
    visible_context: str = ""
    author: str = ""
    publish_time: str = ""
    evidence: str = ""
    reason: str = ""


def _build_item_model(
    fields: list[ExtractField], *, with_coords: bool
) -> type[BaseModel]:
    """构造单条目模型:业务字段 + relevance_score/score_reason(+可选 tap 坐标)。"""
    item_field_defs: dict[str, Any] = {}
    for f in fields:
        annotation, _default = _PY_TYPE.get(f.type, (str | None, None))
        item_field_defs[f.name] = (
            annotation,
            Field(
                default_factory=list if f.type == "list" else (lambda: None),
                description=f.description,
            ),
        )
    item_field_defs["subject_match"] = (
        _RequiredInt,
        Field(
            default=0,
            description=(
                "主体对应程度(0-100):该条目的主体是否就是搜索词所指的目标主体。"
                "90-100=完全就是目标主体本身;70-89=直接相关(目标主体的项目/公告/子事项);"
                "40-69=间接相关(同行业/关联方/提及);0-39=不同主体或无关。"
            ),
        ),
    )
    item_field_defs["relevance_score"] = (
        _RequiredInt,
        Field(default=0, description="相关性/价值分(0-100),依据搜索词与内容价值,越相关越高"),
    )
    item_field_defs["score_reason"] = (
        _RequiredText,
        Field(default="", description="简短打分理由"),
    )
    item_field_defs["source_url"] = (
        _OptionalText,
        Field(
            default=None,
            description="若画面中可见该条目的原文链接/URL(http/https)则填写,看不到就留空,不要臆造",
        ),
    )
    item_field_defs["content_kind"] = (
        _ContentKind,
        Field(default="other", description="列表条目的真实内容类型"),
    )
    item_field_defs["is_article_result"] = (
        _RequiredBoolean,
        Field(default=False, description="只有明确可点击进入图文文章详情时为 true"),
    )
    item_field_defs["target_evidence"] = (
        _RequiredText,
        Field(default="", description="画面中证明条目主体与目标一致的可见文本依据"),
    )
    if with_coords:
        item_field_defs["tap_x"] = (
            _OptionalInt,
            Field(default=None, description="该条目可点击中心点的横坐标(0-1000 归一化)"),
        )
        item_field_defs["tap_y"] = (
            _OptionalInt,
            Field(default=None, description="该条目可点击中心点的纵坐标(0-1000 归一化)"),
        )
        item_field_defs["tap_left"] = (
            _OptionalInt,
            Field(default=None, description="该条目可点击区域左边界(0-1000 归一化)"),
        )
        item_field_defs["tap_top"] = (
            _OptionalInt,
            Field(default=None, description="该条目可点击区域上边界(0-1000 归一化)"),
        )
        item_field_defs["tap_right"] = (
            _OptionalInt,
            Field(default=None, description="该条目可点击区域右边界(0-1000 归一化)"),
        )
        item_field_defs["tap_bottom"] = (
            _OptionalInt,
            Field(default=None, description="该条目可点击区域下边界(0-1000 归一化)"),
        )
    return create_model("CollectItem", **item_field_defs)  # type: ignore[call-overload]


def _build_records_model(
    fields: list[ExtractField], *, with_coords: bool = False
) -> type[BaseModel]:
    """由 extract_fields 动态构造 CollectRecords 结构化模型(条目带分数)。"""
    item_model = _build_item_model(fields, with_coords=with_coords)
    return create_model(
        "CollectRecords",
        __base__=_CollectRecordsEnvelope,
        items=(
            list[item_model],  # type: ignore[valid-type]
            Field(default_factory=list, description="从当前截图识别到的结构化条目列表"),
        ),
    )


class _CollectRecordsEnvelope(BaseModel):
    """Normalize provider output without coupling callers to one LLM shape."""

    @model_validator(mode="before")
    @classmethod
    def _wrap_direct_item_list(cls, value: Any) -> Any:
        if isinstance(value, list):
            return {"items": value}
        return value


def _clamp_score(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, n))


def _split_record(data: dict[str, Any]) -> dict[str, Any]:
    """从条目 dict 中剥离评分/坐标/链接,返回统一记录形状。"""
    score = _clamp_score(data.get("relevance_score"))
    subject_match = _clamp_score(data.get("subject_match"))
    reason = str(data.get("score_reason") or "")
    tap_x = data.get("tap_x")
    tap_y = data.get("tap_y")
    bounds_values = (
        data.get("tap_left"),
        data.get("tap_top"),
        data.get("tap_right"),
        data.get("tap_bottom"),
    )
    tap_bounds: list[int] | None = None
    if all(isinstance(value, int) for value in bounds_values):
        left, top, right, bottom = (int(value) for value in bounds_values)
        if 0 <= left < right <= 1000 and 0 <= top < bottom <= 1000:
            tap_bounds = [left, top, right, bottom]
            # A model-supplied point can drift onto the gap between list rows.
            # The center of the visible row bounds is stable across resolutions.
            tap_x = (left + right) // 2
            tap_y = (top + bottom) // 2
    raw_url = data.get("source_url")
    source_url = raw_url.strip() if isinstance(raw_url, str) and raw_url.strip() else None
    fields = {k: v for k, v in data.items() if k not in _SCORE_KEYS}
    return {
        "fields": fields,
        "score": score,
        "subject_match": subject_match,
        "score_reason": reason,
        "tap_x": tap_x if isinstance(tap_x, int) else None,
        "tap_y": tap_y if isinstance(tap_y, int) else None,
        "tap_bounds": tap_bounds,
        "source_url": source_url,
        "content_kind": str(data.get("content_kind") or "other"),
        "is_article_result": bool(data.get("is_article_result")),
        "target_evidence": str(data.get("target_evidence") or ""),
    }


def _has_content(fields: dict[str, Any]) -> bool:
    return any(v not in (None, "", [], {}) for v in fields.values())


def _get_vision_llm(app_config: Any):
    vision_model = app_config.runtime.models.mobile_screen_model
    return create_llm(app_config, model_name=vision_model, streaming=False)


async def _invoke_vision(runnable: Any, messages: list[Any], *, attempts: int = 2):
    """Retry a failed vision transport once before a screen is discarded."""
    last_error: Exception | None = None
    for index in range(max(1, attempts)):
        try:
            return await runnable.ainvoke(messages)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if index + 1 < max(1, attempts):
                await asyncio.sleep(0.45 * (index + 1))
    assert last_error is not None
    raise last_error


def _fields_desc(fields: list[ExtractField]) -> str:
    return "、".join(f"{f.name}({f.description})" for f in fields) or "关键条目"


async def triage_screenshot(
    image_base64: str,
    *,
    fields: list[ExtractField],
    app_name: str,
    keyword: str,
    target_name: str = "",
    target_aliases: list[str] | None = None,
    policy_instructions: str = "",
    project_id: str | None = None,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    """列表页分诊:识别所有条目 + 结构化字段 + 相关性分 + 可点击坐标。"""
    app_config = await get_runtime_app_config()
    llm = _get_vision_llm(app_config)
    records_model = _build_records_model(fields, with_coords=True)
    structured = llm.with_structured_output(records_model)

    system = load_mobile_prompt(
        "mobile_collect/list_triage",
        {
            "app_name": app_name,
            "keyword": keyword or "无",
            "target_name": target_name or keyword or "无",
            "target_aliases": "、".join(target_aliases or []) or "无",
            "fields_desc": _fields_desc(fields),
            "policy_instructions": policy_instructions or "无额外策略",
        },
    )
    message = HumanMessage(
        content=[
            {"type": "text", "text": "请分诊当前列表页并按 schema 输出。"},
            {
                "type": "image_url",
                "image_url": {"url": prepare_vision_data_url(image_base64)},
            },
        ]
    )
    with observation_context(
        project_id=project_id,
        task_id=task_id,
        phase="mobile_collect_triage",
        agent="collect",
    ):
        result = await _invoke_vision(
            structured, [SystemMessage(content=system), message]
        )

    items = getattr(result, "items", []) or []
    out: list[dict[str, Any]] = []
    for item in items:
        data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        rec = _split_record(data)
        if _has_content(rec["fields"]):
            out.append(rec)
    return out


async def verify_detail_entry(
    image_base64: str,
    *,
    app_name: str,
    keyword: str,
    candidate_fields: dict[str, Any],
    target_name: str = "",
    target_aliases: list[str] | None = None,
    policy_instructions: str = "",
    project_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Fail-closed visual check between a list candidate and the opened page."""
    app_config = await get_runtime_app_config()
    llm = _get_vision_llm(app_config)
    structured = llm.with_structured_output(DetailEntryVerification)
    expected = json.dumps(candidate_fields or {}, ensure_ascii=False, default=str)
    aliases = "、".join(target_aliases or []) or "无"
    system = load_mobile_prompt(
        _DETAIL_VERIFICATION_PROMPT_SLUG,
        {
            "app_name": app_name or "未知应用",
            "keyword": keyword or "无",
            "candidate_fields": expected,
            "target_name": target_name or keyword or "无",
            "target_aliases": aliases,
            "policy_instructions": policy_instructions or "无额外策略",
        },
    )
    message = HumanMessage(
        content=[
            {"type": "text", "text": "校验点击后页面并按 schema 输出。"},
            {
                "type": "image_url",
                "image_url": {"url": prepare_vision_data_url(image_base64)},
            },
        ]
    )
    with observation_context(
        project_id=project_id,
        task_id=task_id,
        phase="mobile_collect_detail_verify",
        agent="collect",
    ):
        result = await _invoke_vision(
            structured, [SystemMessage(content=system), message]
        )
    data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    data["candidate_match"] = _clamp_score(data.get("candidate_match"))
    data["target_match"] = _clamp_score(data.get("target_match"))
    return data


async def analyze_social_media_frame(
    image_base64: str,
    *,
    app_name: str,
    place_name: str,
    keyword: str,
    candidate_fields: dict[str, Any],
    collection_goal: str,
    project_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Validate and locate one public user image without inferring hidden data."""
    app_config = await get_runtime_app_config()
    llm = _get_vision_llm(app_config)
    structured = llm.with_structured_output(SocialMediaFrameAnalysis)
    system = load_mobile_prompt(
        _SOCIAL_MEDIA_FRAME_PROMPT_SLUG,
        {
            "app_name": app_name or "未知应用",
            "place_name": place_name or keyword or "未知地点",
            "keyword": keyword or "无",
            "candidate_fields": json.dumps(
            candidate_fields or {}, ensure_ascii=False, default=str
            ),
            "collection_goal": collection_goal or "收集地点公开图片",
        },
    )
    message = HumanMessage(
        content=[
            {"type": "text", "text": "审核当前公开图片并按 schema 输出。"},
            {
                "type": "image_url",
                "image_url": {"url": prepare_vision_data_url(image_base64)},
            },
        ]
    )
    with observation_context(
        project_id=project_id,
        task_id=task_id,
        phase="mobile_collect_social_media",
        agent="collect",
    ):
        result = await _invoke_vision(
            structured, [SystemMessage(content=system), message]
        )
    data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    bounds = [
        int(data.pop("image_left", 0) or 0),
        int(data.pop("image_top", 0) or 0),
        int(data.pop("image_right", 0) or 0),
        int(data.pop("image_bottom", 0) or 0),
    ]
    left, top, right, bottom = bounds
    valid_bounds = 0 <= left < right <= 1000 and 0 <= top < bottom <= 1000
    data["image_bounds"] = bounds if valid_bounds else []
    data["subject_match"] = _clamp_score(data.get("subject_match"))
    data["candidate_match"] = _clamp_score(data.get("candidate_match"))
    data["accepted"] = bool(
        data.get("is_media_viewer")
        and data.get("useful")
        and valid_bounds
        and data["subject_match"] >= 70
        and data["candidate_match"] >= 60
    )
    return data


async def analyze_detail(
    image_base64s: list[str],
    *,
    fields: list[ExtractField],
    app_name: str,
    keyword: str,
    project_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any] | None:
    """详情页深采:对点进后的一张或多张截图做综合结构化,产出单条富记录 + 分数。"""
    if not image_base64s:
        return None
    app_config = await get_runtime_app_config()
    llm = _get_vision_llm(app_config)
    item_model = _build_item_model(fields, with_coords=False)
    structured = llm.with_structured_output(item_model)

    system = load_mobile_prompt(
        "mobile_collect/detail_extract",
        {
            "app_name": app_name,
            "keyword": keyword or "无",
            "fields_desc": _fields_desc(fields),
        },
    )
    content: list[dict[str, Any]] = [
        {"type": "text", "text": "请综合以下详情页截图提取单条结构化记录并按 schema 输出。"}
    ]
    for b64 in image_base64s:
        content.append(
            {"type": "image_url", "image_url": {"url": prepare_vision_data_url(b64)}}
        )
    message = HumanMessage(content=content)
    with observation_context(
        project_id=project_id,
        task_id=task_id,
        phase="mobile_collect_detail",
        agent="collect",
    ):
        result = await _invoke_vision(
            structured, [SystemMessage(content=system), message]
        )

    data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    rec = _split_record(data)
    if not _has_content(rec["fields"]):
        return None
    return rec


async def analyze_screenshot(
    image_base64: str,
    *,
    fields: list[ExtractField],
    app_name: str,
    keyword: str,
    project_id: str | None = None,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    """不深采时的列表整屏结构化(每条带分数);无字段时退化为整屏摘要单条记录。"""
    app_config = await get_runtime_app_config()
    llm = _get_vision_llm(app_config)

    if not fields:
        prompt = load_mobile_prompt(
            "mobile_collect/screen_summary",
            {"app_name": app_name, "keyword": keyword or "无"},
        )
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": prepare_vision_data_url(image_base64)},
                },
            ]
        )
        with observation_context(
            project_id=project_id,
            task_id=task_id,
            phase="mobile_collect_analyze",
            agent="collect",
        ):
            resp = await _invoke_vision(llm, [message])
        summary = resp.content if isinstance(resp.content, str) else str(resp.content)
        summary = summary.strip()
        if not summary:
            return []
        return [
            {
                "fields": {"summary": summary},
                "score": None,
                "subject_match": 0,
                "score_reason": "",
                "tap_x": None,
                "tap_y": None,
                "source_url": None,
            }
        ]

    records_model = _build_records_model(fields, with_coords=False)
    structured = llm.with_structured_output(records_model)
    system = load_mobile_prompt(
        "mobile_collect/screen_extract",
        {
            "app_name": app_name,
            "keyword": keyword or "无",
            "fields_desc": _fields_desc(fields),
        },
    )
    message = HumanMessage(
        content=[
            {"type": "text", "text": "请提取截图中的条目并按 schema 输出。"},
            {
                "type": "image_url",
                "image_url": {"url": prepare_vision_data_url(image_base64)},
            },
        ]
    )
    with observation_context(
        project_id=project_id,
        task_id=task_id,
        phase="mobile_collect_analyze",
        agent="collect",
    ):
        result = await _invoke_vision(
            structured, [SystemMessage(content=system), message]
        )
    items = getattr(result, "items", []) or []
    out: list[dict[str, Any]] = []
    for item in items:
        data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        rec = _split_record(data)
        if _has_content(rec["fields"]):
            out.append(rec)
    return out
