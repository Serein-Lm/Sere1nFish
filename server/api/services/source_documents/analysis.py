"""来源文档结构化与图片识别。

HTML 正文、链接和联系方式由确定性代码提取；LLM 只负责任务字段归纳、目标相关性
判断和图片语义识别。模型与观测均通过项目统一运行时接入。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import unicodedata
from typing import Any, Literal

from PIL import Image
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, create_model

from Sere1nGraph.graph.agents.runtime import create_llm
from Sere1nGraph.graph.prompts.loader import load_prompt
from api.models.mobile_collect import ExtractField
from api.services.runtime_config import get_runtime_app_config
from core.mobile.collect.contacts import extract_contacts
from core.observability import observation_context
from .contracts import CapturedDocument, CapturedImage


_STRUCTURE_TIMEOUT_SECONDS = 120
_RELEVANCE_REVIEW_TIMEOUT_SECONDS = 120
_IMAGE_BATCH_TIMEOUT_SECONDS = 120
ARTICLE_ANALYSIS_PROMPT_SLUG = "source_document/source_document"
RELEVANCE_REVIEW_PROMPT_SLUG = "source_document/relevance_review"
CONTACT_ATTRIBUTION_PROMPT_SLUG = "source_document/contact_attribution"


_PY_TYPE = {
    "string": (str | None, None),
    "number": (float | None, None),
    "boolean": (bool | None, None),
    "list": (list[str], ...),
}
_RESERVED = {
    "summary",
    "article_scope",
    "target_contact_values",
    "subject_match",
    "relevance_score",
    "score_reason",
}
_ARTICLE_SCOPE_CAPS = {
    "target_focused": 100,
    "multi_entity_roundup": 39,
    "incidental": 20,
    "uncertain": 69,
}
_CONTACT_CHANNEL_LABELS = {
    "phone": "手机号",
    "telephone": "座机",
    "email": "邮箱",
    "wechat": "微信号",
    "qq": "QQ",
}


def _safe_index(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


class VisualContact(BaseModel):
    channel: Literal["phone", "telephone", "email", "wechat", "qq"]
    value: str
    context: str = ""


class ImageUnderstanding(BaseModel):
    index: int
    description: str = ""
    visible_text: str = ""
    contacts: list[VisualContact] = Field(default_factory=list)
    is_key_evidence: bool = False
    importance_score: int = Field(default=0, ge=0, le=100)
    archive_reason: str = ""


class ImageUnderstandingBatch(BaseModel):
    items: list[ImageUnderstanding] = Field(default_factory=list)


class ArticleRelevanceReview(BaseModel):
    decision: Literal["accept", "reject"] = "reject"
    article_scope: Literal[
        "target_focused",
        "multi_entity_roundup",
        "incidental",
        "uncertain",
    ] = "uncertain"
    subject_match: float = Field(default=0, ge=0, le=100)
    relevance_score: float = Field(default=0, ge=0, le=100)
    summary: str = ""
    target_contact_values: list[str] = Field(default_factory=list)
    reason: str = ""


class ContactAttributionDecision(BaseModel):
    candidate_id: str
    belongs_to_target: bool = False
    confidence: int = Field(default=0, ge=0, le=100)
    reason: str = ""


class ContactAttributionBatch(BaseModel):
    items: list[ContactAttributionDecision] = Field(default_factory=list)


def stable_content_hash(capture: CapturedDocument) -> str:
    """只对稳定文章内容取哈希，忽略页面动态脚本、风控 token 和会话差异。"""
    image_urls = list(capture.metadata.get("image_urls") or []) or [
        image.source_url for image in capture.images
    ]
    attachment_urls = list(capture.metadata.get("attachment_urls") or []) or [
        item.source_url for item in capture.attachments
    ]
    payload = {
        "source_type": capture.source_type,
        "canonical_url": capture.canonical_url,
        "title": capture.title,
        "account": capture.account,
        "publish_time": capture.publish_time,
        "text": capture.metadata.get("article_text") or capture.text,
        "images": image_urls,
        # Declared attachment identity belongs to the page version; download
        # success and extracted text are repairable evidence, not version identity.
        "attachments": sorted(str(value) for value in attachment_urls if value),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _article_output_model(fields: list[ExtractField]) -> type[BaseModel]:
    definitions: dict[str, Any] = {}
    for item in fields:
        if item.name in _RESERVED:
            continue
        annotation, _default = _PY_TYPE.get(item.type, (str | None, None))
        definitions[item.name] = (
            annotation,
            Field(
                default_factory=list if item.type == "list" else (lambda: None),
                description=item.description,
            ),
        )
    definitions.update(
        {
            "summary": (
                str,
                Field(
                    default="",
                    description="只概括可直接归属于目标实体的事实，不概括无关正文",
                ),
            ),
            "article_scope": (
                Literal[
                    "target_focused",
                    "multi_entity_roundup",
                    "incidental",
                    "uncertain",
                ],
                Field(
                    default="uncertain",
                    description="目标在整篇文章中的主体范围分类",
                ),
            ),
            "target_contact_values": (
                list[str],
                Field(
                    default_factory=list,
                    description="正文明确归属于目标实体的联系方式原值",
                ),
            ),
            "subject_match": (
                float,
                Field(
                    default=0,
                    ge=0,
                    le=100,
                    description="目标实体在整篇文章主题与篇幅中的占比和聚焦程度",
                ),
            ),
            "relevance_score": (
                float,
                Field(default=0, ge=0, le=100, description="文章对本次搜索目标的价值分"),
            ),
            "score_reason": (str, Field(default="", description="简短评分依据")),
        }
    )
    return create_model("SourceArticleAnalysis", **definitions)  # type: ignore[call-overload]


def normalize_scores(
    relevance_score: Any,
    subject_match: Any,
) -> tuple[int, int]:
    """兼容部分模型把百分制误写成 0-1 比例，统一输出 0-100 整数。"""
    try:
        relevance = float(relevance_score or 0)
    except (TypeError, ValueError):
        relevance = 0.0
    try:
        subject = float(subject_match or 0)
    except (TypeError, ValueError):
        subject = 0.0
    if 0 <= relevance <= 1 and 0 <= subject <= 1 and max(relevance, subject) > 0:
        relevance *= 100
        subject *= 100
    return (
        max(0, min(100, round(relevance))),
        max(0, min(100, round(subject))),
    )


def clamp_score(value: Any) -> int:
    """把单个模型评分稳定收敛为 0-100 整数。"""
    try:
        score = float(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, round(score)))


def normalize_article_scope(value: Any) -> str:
    scope = str(value or "uncertain").strip()
    return scope if scope in _ARTICLE_SCOPE_CAPS else "uncertain"


def apply_article_scope_cap(subject_match: Any, article_scope: Any) -> int:
    """用整篇文章范围分类约束主体分，避免单条命中冒充整篇高相关。"""
    scope = normalize_article_scope(article_scope)
    return min(clamp_score(subject_match), _ARTICLE_SCOPE_CAPS[scope])


def _contact_value_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[\s\-‐‑‒–—()（）]", "", normalized).casefold()


def _declared_contact_values(values: list[Any]) -> list[str]:
    """Turn model labels such as ``联系电话：010-...`` into contact atoms."""
    normalized: list[str] = []
    for raw in values:
        declared = unicodedata.normalize("NFKC", str(raw or "")).strip()
        if not declared:
            continue
        parsed = extract_contacts(declared)
        if parsed:
            normalized.extend(str(item.get("value") or "") for item in parsed)
        else:
            normalized.append(declared)
    return list(dict.fromkeys(value for value in normalized if value))


def filter_target_contacts(
    contacts: list[dict[str, Any]],
    target_contact_values: list[Any],
    *,
    text: str = "",
) -> list[dict[str, Any]]:
    """只保留结构化分析明确归属于当前 Target 的联系方式。"""
    declared = _declared_contact_values(target_contact_values)
    allowed = {_contact_value_key(value) for value in declared}
    if not allowed:
        return []
    matched = [
        dict(contact)
        for contact in contacts
        if _contact_value_key(contact.get("value")) in allowed
    ]
    matched_keys = {_contact_value_key(item.get("value")) for item in matched}
    for value in declared:
        key = _contact_value_key(value)
        if key in matched_keys:
            continue
        supplemental = _declared_contact_from_text(text, value)
        if supplemental:
            matched.append(supplemental)
            matched_keys.add(key)
    return matched


def _declared_contact_from_text(text: str, value: str) -> dict[str, Any] | None:
    """补齐正则未覆盖、但两个 Agent 均声明且正文精确出现的联系方式。"""
    if not text or not value:
        return None
    original_text = text
    normalized_text = unicodedata.normalize("NFKC", text)
    value = unicodedata.normalize("NFKC", value).strip()
    index = normalized_text.casefold().find(value.casefold())
    if index < 0:
        return None
    left = max(0, index - 80)
    right = min(len(original_text), index + len(value) + 120)
    context = re.sub(r"\s+", " ", original_text[left:right]).strip()
    anchor = normalized_text[left:index].casefold()
    compact = _contact_value_key(value)
    if "@" in value:
        channel = "email"
    elif re.fullmatch(r"1[3-9]\d{9}", compact):
        channel = "phone"
    elif re.fullmatch(r"0\d{9,11}", compact):
        channel = "telephone"
    elif re.search(r"(?:微信|weixin|wechat|\bvx\b|\bwx\b)", anchor):
        channel = "wechat"
    elif re.search(r"\bqq\b", anchor):
        channel = "qq"
    else:
        return None
    return {
        "channel": channel,
        "value": value,
        "label": f"{_CONTACT_CHANNEL_LABELS[channel]}: {value}",
        "context": context,
        "contexts": [context] if context else [],
        "source": "text",
        "attribution": "dual_agent_declared",
    }


def _deterministic_fields(capture: CapturedDocument) -> dict[str, Any]:
    return {
        "title": capture.title,
        "account": capture.account,
        "publish_time": capture.publish_time,
        "summary": capture.text[:500],
        "contact": "",
        "content": capture.text,
    }


def article_analysis_prompt_fingerprint() -> str:
    """返回结构化与独立审核 Prompt 的组合指纹。"""
    payload = {
        ARTICLE_ANALYSIS_PROMPT_SLUG: load_prompt(ARTICLE_ANALYSIS_PROMPT_SLUG),
        RELEVANCE_REVIEW_PROMPT_SLUG: load_prompt(RELEVANCE_REVIEW_PROMPT_SLUG),
        CONTACT_ATTRIBUTION_PROMPT_SLUG: load_prompt(CONTACT_ATTRIBUTION_PROMPT_SLUG),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _render_article_analysis_prompt(
    *,
    target_name: str,
    keyword: str,
    field_desc: str,
) -> tuple[str, str]:
    template = load_prompt(ARTICLE_ANALYSIS_PROMPT_SLUG)
    prompt_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()
    rendered = template
    for placeholder, value in {
        "{{target_name}}": target_name or "未指定",
        "{{keyword}}": keyword or "未指定",
        "{{field_desc}}": field_desc or "文章关键信息",
    }.items():
        rendered = rendered.replace(placeholder, value)
    return rendered, prompt_hash


async def analyze_article_fields(
    capture: CapturedDocument,
    *,
    fields: list[ExtractField],
    target_name: str,
    keyword: str,
    project_id: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    """按采集任务 schema 归纳全文；失败时返回完整的确定性基础字段。"""
    fallback = _deterministic_fields(capture)
    prompt_hash = ""
    try:
        app_config = await get_runtime_app_config()
        model_name = app_config.runtime.models.default
        llm = create_llm(app_config, model_name=model_name, streaming=False)
        structured = llm.with_structured_output(_article_output_model(fields))
        field_desc = "、".join(
            f"{item.name}({item.description or item.type})" for item in fields
        ) or "文章关键信息"
        system, prompt_hash = _render_article_analysis_prompt(
            target_name=target_name,
            keyword=keyword,
            field_desc=field_desc,
        )
        message = HumanMessage(
            content=(
                f"文章标题：{capture.title}\n"
                f"公众号：{capture.account}\n"
                f"发布时间：{capture.publish_time}\n"
                f"原文链接：{capture.canonical_url}\n\n"
                f"完整正文：\n{capture.text[:60000]}"
            )
        )
        with observation_context(
            project_id=project_id or None,
            task_id=task_id or None,
            phase="source_document_structure",
            agent="source_document",
            task_type="wechat_article",
        ):
            result = await asyncio.wait_for(
                structured.ainvoke([SystemMessage(content=system), message]),
                timeout=_STRUCTURE_TIMEOUT_SECONDS,
            )
        parsed = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        output_fields = {
            key: value
            for key, value in parsed.items()
            if key
            not in {
                "summary",
                "article_scope",
                "target_contact_values",
                "subject_match",
                "relevance_score",
                "score_reason",
            }
            and value not in (None, "", [], {})
        }
        relevance_score, subject_match = normalize_scores(
            parsed.get("relevance_score"),
            parsed.get("subject_match"),
        )
        article_scope = normalize_article_scope(parsed.get("article_scope"))
        subject_match = apply_article_scope_cap(subject_match, article_scope)
        target_contact_values = [
            str(value).strip()
            for value in parsed.get("target_contact_values") or []
            if str(value).strip()
        ]
        target_contacts = filter_target_contacts(
            extract_contacts(capture.text),
            target_contact_values,
            text=capture.text,
        )
        contextual_fields = {
            **fallback,
            **output_fields,
            "summary": str(parsed.get("summary") or ""),
            "contact": "、".join(
                str(item.get("label") or "") for item in target_contacts
            ),
            "content": capture.text,
        }
        return {
            "fields": contextual_fields,
            "score": relevance_score,
            "subject_match": subject_match,
            "score_reason": str(parsed.get("score_reason") or ""),
            "article_scope": article_scope,
            "target_contact_values": target_contact_values,
            "target_contacts": target_contacts,
            "analysis_model": model_name,
            "analysis_prompt_slug": ARTICLE_ANALYSIS_PROMPT_SLUG,
            "analysis_prompt_hash": prompt_hash,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "fields": fallback,
            "score": 0,
            "subject_match": 0,
            "score_reason": "全文结构化模型不可用，已保留确定性提取结果",
            "article_scope": "uncertain",
            "target_contact_values": [],
            "target_contacts": [],
            "analysis_error": str(exc),
            "analysis_prompt_slug": ARTICLE_ANALYSIS_PROMPT_SLUG,
            "analysis_prompt_hash": prompt_hash,
        }


def _render_relevance_review_prompt(
    *,
    target_name: str,
    keyword: str,
    required_subject_match: int,
) -> tuple[str, str]:
    template = load_prompt(RELEVANCE_REVIEW_PROMPT_SLUG)
    prompt_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()
    rendered = template
    for placeholder, value in {
        "{{target_name}}": target_name or "未指定",
        "{{keyword}}": keyword or "未指定",
        "{{required_subject_match}}": str(required_subject_match),
    }.items():
        rendered = rendered.replace(placeholder, value)
    return rendered, prompt_hash


async def review_article_relevance(
    capture: CapturedDocument,
    *,
    draft_analysis: dict[str, Any],
    target_name: str,
    keyword: str,
    required_subject_match: int,
    project_id: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    """用独立模型调用复核整篇主体相关性与 Target 归属。"""
    prompt_hash = ""
    try:
        app_config = await get_runtime_app_config()
        model_name = app_config.runtime.models.default
        llm = create_llm(app_config, model_name=model_name, streaming=False)
        structured = llm.with_structured_output(ArticleRelevanceReview)
        system, prompt_hash = _render_relevance_review_prompt(
            target_name=target_name,
            keyword=keyword,
            required_subject_match=required_subject_match,
        )
        draft = {
            "summary": (draft_analysis.get("fields") or {}).get("summary") or "",
            "article_scope": draft_analysis.get("article_scope") or "uncertain",
            "subject_match": draft_analysis.get("subject_match") or 0,
            "relevance_score": draft_analysis.get("score") or 0,
            "score_reason": draft_analysis.get("score_reason") or "",
            "target_contact_values": draft_analysis.get("target_contact_values") or [],
        }
        message = HumanMessage(
            content=(
                f"文章标题：{capture.title}\n"
                f"公众号：{capture.account}\n"
                f"发布时间：{capture.publish_time}\n"
                f"原文链接：{capture.canonical_url}\n\n"
                f"结构化 Agent 初稿（仅供复核，不得直接照抄）：\n"
                f"{json.dumps(draft, ensure_ascii=False)}\n\n"
                f"完整正文：\n{capture.text[:60000]}"
            )
        )
        with observation_context(
            project_id=project_id or None,
            task_id=task_id or None,
            phase="source_document_relevance_review",
            agent="source_document_reviewer",
            task_type="wechat_article",
        ):
            result = await asyncio.wait_for(
                structured.ainvoke([SystemMessage(content=system), message]),
                timeout=_RELEVANCE_REVIEW_TIMEOUT_SECONDS,
            )
        parsed = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        score, subject_match = normalize_scores(
            parsed.get("relevance_score"),
            parsed.get("subject_match"),
        )
        article_scope = normalize_article_scope(parsed.get("article_scope"))
        return {
            "decision": str(parsed.get("decision") or "reject"),
            "article_scope": article_scope,
            "subject_match": apply_article_scope_cap(subject_match, article_scope),
            "score": score,
            "summary": str(parsed.get("summary") or ""),
            "target_contact_values": [
                str(value).strip()
                for value in parsed.get("target_contact_values") or []
                if str(value).strip()
            ],
            "reason": str(parsed.get("reason") or ""),
            "review_model": model_name,
            "review_prompt_slug": RELEVANCE_REVIEW_PROMPT_SLUG,
            "review_prompt_hash": prompt_hash,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "decision": "reject",
            "article_scope": "uncertain",
            "subject_match": 0,
            "score": 0,
            "summary": "",
            "target_contact_values": [],
            "reason": "独立相关性审核不可用，按保守策略拒绝",
            "review_error": str(exc),
            "review_prompt_slug": RELEVANCE_REVIEW_PROMPT_SLUG,
            "review_prompt_hash": prompt_hash,
        }


def apply_relevance_review(
    capture: CapturedDocument,
    draft_analysis: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    """保守合并两个 Agent 的结论；任何一侧低判都不能被另一侧抬高。"""
    draft_values = {
        _contact_value_key(value)
        for value in draft_analysis.get("target_contact_values") or []
    }
    agreed_values = [
        str(value).strip()
        for value in review.get("target_contact_values") or []
        if str(value).strip() and _contact_value_key(value) in draft_values
    ]
    target_contacts = filter_target_contacts(
        extract_contacts(capture.text),
        agreed_values,
        text=capture.text,
    )
    fields = dict(draft_analysis.get("fields") or {})
    fields.update(
        {
            "summary": str(review.get("summary") or ""),
            "contact": "、".join(
                str(item.get("label") or "") for item in target_contacts
            ),
        }
    )
    decision = "accept" if review.get("decision") == "accept" else "reject"
    return {
        **draft_analysis,
        "fields": fields,
        "score": min(
            clamp_score(draft_analysis.get("score")),
            clamp_score(review.get("score")),
        ),
        "subject_match": min(
            clamp_score(draft_analysis.get("subject_match")),
            clamp_score(review.get("subject_match")),
        ),
        "score_reason": str(review.get("reason") or "独立相关性审核未给出依据"),
        "article_scope": normalize_article_scope(review.get("article_scope")),
        "target_contact_values": agreed_values,
        "target_contacts": target_contacts,
        "review_decision": decision,
        "relevance_review": dict(review),
    }


async def analyze_and_review_article(
    capture: CapturedDocument,
    *,
    fields: list[ExtractField],
    target_name: str,
    keyword: str,
    required_subject_match: int,
    project_id: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    """统一入口：结构化提取后交给独立相关性审核 Agent。"""
    draft = await analyze_article_fields(
        capture,
        fields=fields,
        target_name=target_name,
        keyword=keyword,
        project_id=project_id,
        task_id=task_id,
    )
    if draft.get("analysis_error"):
        return {
            **draft,
            "review_decision": "reject",
            "relevance_review": {
                "decision": "reject",
                "reason": "结构化分析失败，未进入独立审核",
            },
        }
    review = await review_article_relevance(
        capture,
        draft_analysis=draft,
        target_name=target_name,
        keyword=keyword,
        required_subject_match=required_subject_match,
        project_id=project_id,
        task_id=task_id,
    )
    return apply_relevance_review(capture, draft, review)


async def attribute_target_contacts(
    *,
    target_name: str,
    target_aliases: list[str],
    title: str,
    account: str,
    summary: str,
    contacts: list[dict[str, Any]],
    image_analysis: list[dict[str, Any]],
    project_id: str = "",
    task_id: str = "",
) -> tuple[list[dict[str, Any]], str]:
    """Audit source-level contact candidates before exposing Target findings."""
    if not contacts:
        return [], ""

    images = {_safe_index(item.get("index")): item for item in image_analysis}
    candidates: list[dict[str, Any]] = []
    candidate_by_id: dict[str, dict[str, Any]] = {}
    for index, contact in enumerate(contacts):
        candidate_id = f"contact_{index}"
        image = images.get(_safe_index(contact.get("image_index")), {})
        candidate = {
            "candidate_id": candidate_id,
            "channel": str(contact.get("channel") or ""),
            "value": str(contact.get("value") or ""),
            "source": str(contact.get("source") or "text"),
            "context": str(contact.get("context") or "")[:1200],
            "contexts": [str(value)[:800] for value in contact.get("contexts") or []][:5],
            "image_description": str(image.get("description") or "")[:1200],
            "image_visible_text": str(image.get("visible_text") or "")[:5000],
        }
        candidates.append(candidate)
        candidate_by_id[candidate_id] = contact

    try:
        app_config = await get_runtime_app_config()
        model_name = app_config.runtime.models.default
        llm = create_llm(app_config, model_name=model_name, streaming=False)
        structured = llm.with_structured_output(ContactAttributionBatch)
        system = load_prompt(CONTACT_ATTRIBUTION_PROMPT_SLUG)
        for placeholder, value in {
            "{{target_name}}": target_name or "未指定",
            "{{target_aliases}}": "、".join(target_aliases) or "无",
        }.items():
            system = system.replace(placeholder, value)
        message = HumanMessage(
            content=(
                f"文章标题：{title}\n"
                f"公众号：{account}\n"
                f"目标摘要：{summary}\n\n"
                "候选联系方式（只能按 candidate_id 审核，不得新增）：\n"
                f"{json.dumps(candidates, ensure_ascii=False)}"
            )
        )
        with observation_context(
            project_id=project_id or None,
            task_id=task_id or None,
            phase="source_document_contact_attribution",
            agent="source_document_contact_reviewer",
            task_type="wechat_article",
        ):
            result = await asyncio.wait_for(
                structured.ainvoke([SystemMessage(content=system), message]),
                timeout=_RELEVANCE_REVIEW_TIMEOUT_SECONDS,
            )
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in getattr(result, "items", []) or []:
            decision = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            candidate_id = str(decision.get("candidate_id") or "")
            candidate = candidate_by_id.get(candidate_id)
            if (
                candidate is None
                or candidate_id in seen
                or not decision.get("belongs_to_target")
                or clamp_score(decision.get("confidence")) < 80
            ):
                continue
            seen.add(candidate_id)
            selected.append(
                {
                    **candidate,
                    "attribution": "contact_review_agent",
                    "attribution_confidence": clamp_score(decision.get("confidence")),
                    "attribution_reason": str(decision.get("reason") or ""),
                }
            )
        return selected, ""
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)[:2000]


def _vision_payload(image: CapturedImage) -> tuple[str, str]:
    """把任意原图（含 GIF）转为适合视觉模型的受限尺寸 JPEG。"""
    with Image.open(io.BytesIO(image.data)) as source:
        source.seek(0)
        source.load()
        if source.mode in {"RGBA", "LA"} or (
            source.mode == "P" and "transparency" in source.info
        ):
            rgba = source.convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            background.alpha_composite(rgba)
            frame = background.convert("RGB")
        else:
            frame = source.convert("RGB")
        frame.thumbnail((1600, 1600))
        output = io.BytesIO()
        frame.save(output, format="JPEG", quality=86, optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return "image/jpeg", encoded


def _prepare_image_inputs(
    images: list[CapturedImage],
) -> tuple[list[tuple[CapturedImage, str, str]], list[str]]:
    """Isolate malformed/unsupported images instead of failing their batch."""
    prepared: list[tuple[CapturedImage, str, str]] = []
    errors: list[str] = []
    for image in images:
        try:
            media_type, encoded = _vision_payload(image)
            prepared.append((image, media_type, encoded))
        except Exception as exc:  # noqa: BLE001
            errors.append(
                f"image index={image.index} content_type={image.content_type or 'unknown'} "
                f"url={image.source_url[:300]} prepare_failed={type(exc).__name__}"
            )
    return prepared, errors


async def _analyze_image_batch(
    images: list[CapturedImage],
    *,
    project_id: str,
    task_id: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    prepared, errors = _prepare_image_inputs(images)
    if not prepared:
        return [], errors
    app_config = await get_runtime_app_config()
    model_name = app_config.runtime.models.vision
    llm = create_llm(app_config, model_name=model_name, streaming=False)
    structured = llm.with_structured_output(ImageUnderstandingBatch)
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "按图片前的 index 逐张识别：给出主要内容、所有清晰可见文字；如果图片中明确出现"
                "手机号、座机、邮箱、微信号或 QQ，提取联系方式及其邻近上下文。看不清就留空，"
                "不得猜测。还要判断图片是否包含联系方式、关键人物/单位、业务流程、公告数据、"
                "核心图表或其它不可由正文替代的关键证据；普通场景照、品牌页脚、作者头像、关注二维码、"
                "平台导流图、分隔图和无信息配图不得标为关键。只有二维码本身承载明确的目标联系方式或"
                "业务入口时才可标为关键。输出 is_key_evidence、0-100 importance_score 和简短 archive_reason。"
                "每张输入图片必须返回一个同 index 的 item。"
            ),
        }
    ]
    for image, media_type, encoded in prepared:
        content.append({"type": "text", "text": f"index={image.index}"})
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{encoded}"},
            }
        )
    with observation_context(
        project_id=project_id or None,
        task_id=task_id or None,
        phase="source_document_image_recognition",
        agent="source_document",
        task_type="wechat_article",
    ):
        result = await asyncio.wait_for(
            structured.ainvoke([HumanMessage(content=content)]),
            timeout=_IMAGE_BATCH_TIMEOUT_SECONDS,
        )
    items = getattr(result, "items", []) or []
    return (
        [
            item.model_dump() if hasattr(item, "model_dump") else dict(item)
            for item in items
        ],
        errors,
    )


async def analyze_article_images(
    images: list[CapturedImage],
    *,
    project_id: str = "",
    task_id: str = "",
    batch_size: int = 4,
) -> tuple[list[dict[str, Any]], str]:
    """识别全部文章原图；调用侧据识别结果只归档关键证据图片。"""
    if not images:
        return [], ""
    batches = [images[index : index + batch_size] for index in range(0, len(images), batch_size)]
    results = await asyncio.gather(
        *(
            _analyze_image_batch(
                batch,
                project_id=project_id,
                task_id=f"{task_id}:image:{index}",
            )
            for index, batch in enumerate(batches)
        ),
        return_exceptions=True,
    )
    merged: list[dict[str, Any]] = []
    errors: list[str] = []
    for result in results:
        if isinstance(result, BaseException):
            errors.append(str(result))
        else:
            items, batch_errors = result
            merged.extend(items)
            errors.extend(batch_errors)
    by_index = {int(item.get("index", -1)): item for item in merged}
    ordered = [
        {
            "index": image.index,
            "description": str(by_index.get(image.index, {}).get("description") or ""),
            "visible_text": str(by_index.get(image.index, {}).get("visible_text") or ""),
            "contacts": list(by_index.get(image.index, {}).get("contacts") or []),
            "is_key_evidence": bool(
                by_index.get(image.index, {}).get("is_key_evidence")
            ),
            "importance_score": clamp_score(
                by_index.get(image.index, {}).get("importance_score")
            ),
            "archive_reason": str(
                by_index.get(image.index, {}).get("archive_reason") or ""
            ),
        }
        for image in images
    ]
    return ordered, "; ".join(errors)[:2000]
