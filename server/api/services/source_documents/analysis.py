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
from typing import Any, Literal

from PIL import Image
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, create_model

from Sere1nGraph.graph.agents.runtime import create_llm
from api.models.mobile_collect import ExtractField
from api.services.runtime_config import get_runtime_app_config
from core.mobile.collect.contacts import extract_contacts
from core.observability import observation_context

from .contracts import CapturedDocument, CapturedImage


_PY_TYPE = {
    "string": (str | None, None),
    "number": (float | None, None),
    "boolean": (bool | None, None),
    "list": (list[str], ...),
}
_RESERVED = {"summary", "subject_match", "relevance_score", "score_reason"}


class VisualContact(BaseModel):
    channel: Literal["phone", "telephone", "email", "wechat", "qq"]
    value: str
    context: str = ""


class ImageUnderstanding(BaseModel):
    index: int
    description: str = ""
    visible_text: str = ""
    contacts: list[VisualContact] = Field(default_factory=list)


class ImageUnderstandingBatch(BaseModel):
    items: list[ImageUnderstanding] = Field(default_factory=list)


def stable_content_hash(capture: CapturedDocument) -> str:
    """只对稳定文章内容取哈希，忽略页面动态脚本、风控 token 和会话差异。"""
    image_urls = list(capture.metadata.get("image_urls") or []) or [
        image.source_url for image in capture.images
    ]
    payload = {
        "source_type": capture.source_type,
        "canonical_url": capture.canonical_url,
        "title": capture.title,
        "account": capture.account,
        "publish_time": capture.publish_time,
        "text": capture.text,
        "images": image_urls,
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
            "summary": (str, Field(default="", description="完整文章的事实性摘要")),
            "subject_match": (
                float,
                Field(default=0, ge=0, le=100, description="文章主体与目标实体对应程度"),
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


def _deterministic_fields(capture: CapturedDocument) -> dict[str, Any]:
    contacts = extract_contacts(capture.text)
    return {
        "title": capture.title,
        "account": capture.account,
        "publish_time": capture.publish_time,
        "summary": capture.text[:500],
        "contact": "、".join(str(item.get("label") or "") for item in contacts),
        "content": capture.text,
    }


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
    try:
        app_config = await get_runtime_app_config()
        model_name = app_config.runtime.models.default
        llm = create_llm(app_config, model_name=model_name, streaming=False)
        structured = llm.with_structured_output(_article_output_model(fields))
        field_desc = "、".join(
            f"{item.name}({item.description or item.type})" for item in fields
        ) or "文章关键信息"
        system = (
            "你是公开文章结构化分析器。只能依据给出的完整正文归纳，不得补充文章外事实。"
            f"本次目标实体：{target_name or '未指定'}；搜索词：{keyword or '未指定'}。"
            f"需要提取：{field_desc}。subject_match 判断文章主体是否真的是目标实体，"
            "不能因为同属一个行业或只提到目标就给高分；relevance_score 同时考虑目标对应和信息价值。"
            "两个分数必须输出0到100的百分制数字（例如95），禁止输出0到1的小数比例。"
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
            result = await structured.ainvoke(
                [SystemMessage(content=system), message]
            )
        parsed = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        output_fields = {
            key: value
            for key, value in parsed.items()
            if key not in {"subject_match", "relevance_score", "score_reason"}
            and value not in (None, "", [], {})
        }
        relevance_score, subject_match = normalize_scores(
            parsed.get("relevance_score"),
            parsed.get("subject_match"),
        )
        return {
            "fields": {**fallback, **output_fields, "content": capture.text},
            "score": relevance_score,
            "subject_match": subject_match,
            "score_reason": str(parsed.get("score_reason") or ""),
            "analysis_model": model_name,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "fields": fallback,
            "score": 0,
            "subject_match": 0,
            "score_reason": "全文结构化模型不可用，已保留确定性提取结果",
            "analysis_error": str(exc),
        }


def _vision_payload(image: CapturedImage) -> tuple[str, str]:
    """把任意原图（含 GIF）转为适合视觉模型的受限尺寸 JPEG。"""
    with Image.open(io.BytesIO(image.data)) as source:
        frame = source.convert("RGB")
        frame.thumbnail((1600, 1600))
        output = io.BytesIO()
        frame.save(output, format="JPEG", quality=86, optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return "image/jpeg", encoded


async def _analyze_image_batch(
    images: list[CapturedImage],
    *,
    project_id: str,
    task_id: str,
) -> list[dict[str, Any]]:
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
                "不得猜测。每张输入图片必须返回一个同 index 的 item。"
            ),
        }
    ]
    for image in images:
        media_type, encoded = _vision_payload(image)
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
        result = await structured.ainvoke([HumanMessage(content=content)])
    items = getattr(result, "items", []) or []
    return [item.model_dump() if hasattr(item, "model_dump") else dict(item) for item in items]


async def analyze_article_images(
    images: list[CapturedImage],
    *,
    project_id: str = "",
    task_id: str = "",
    batch_size: int = 4,
) -> tuple[list[dict[str, Any]], str]:
    """识别全部文章原图；单批失败不影响原图永久保存。"""
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
            merged.extend(result)
    by_index = {int(item.get("index", -1)): item for item in merged}
    ordered = [
        {
            "index": image.index,
            "description": str(by_index.get(image.index, {}).get("description") or ""),
            "visible_text": str(by_index.get(image.index, {}).get("visible_text") or ""),
            "contacts": list(by_index.get(image.index, {}).get("contacts") or []),
        }
        for image in images
    ]
    return ordered, "; ".join(errors)[:2000]
