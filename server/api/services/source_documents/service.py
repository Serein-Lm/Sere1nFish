"""来源文档统一领域服务。

入口负责 Provider 选择、稳定版本判定、OSS 产物写入、结构化分析、Target/项目关联
与公开详情组装；调用侧不感知 Playwright、OSS SDK 或 Mongo 字段。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import source_documents as source_dao
from api.dao import targets as targets_dao
from api.models.mobile_collect import ExtractField
from api.storage import get_object_storage
from core.mobile.collect.contacts import extract_contacts, normalize_contact_candidate

from .analysis import (
    analyze_and_review_article,
    analyze_article_images,
    article_analysis_prompt_fingerprint,
    attribute_target_contacts,
    filter_target_contacts,
    stable_content_hash,
)
from .contracts import (
    CapturedDocument,
    CapturedImage,
    CapturedScreenshot,
    SourceDocumentAnalysisError,
)
from .factory import get_source_document_provider
from .urls import canonicalize_source_url


_document_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_document_lock_users: defaultdict[str, int] = defaultdict(int)
_CONTEXT_ANALYSIS_SCHEMA_VERSION = 7
_MEDIA_POLICY_VERSION = 3
_CONTACT_POLICY_VERSION = 2
_SOURCE_FIELD_KEYS = {
    "title",
    "account",
    "publish_time",
    "content",
    "contact",
    "image_context",
}


@asynccontextmanager
async def _hold_document_lock(document_id: str):
    lock = _document_locks[document_id]
    _document_lock_users[document_id] += 1
    acquired = False
    try:
        await lock.acquire()
        acquired = True
        yield
    finally:
        if acquired:
            lock.release()
        _document_lock_users[document_id] -= 1
        if _document_lock_users[document_id] <= 0:
            _document_lock_users.pop(document_id, None)
            if _document_locks.get(document_id) is lock:
                _document_locks.pop(document_id, None)


def _object_url(object_id: str) -> str:
    return f"/api/v1/storage/objects/{object_id}/content"


def _artifact_object_id(version_id: str, suffix: str, data: bytes) -> str:
    """产物内容参与 ID，失败重试时不与已上传的动态页面片段冲突。"""
    digest = hashlib.sha256(data).hexdigest()[:12]
    return f"obj_{version_id}_{suffix}_{digest}"


def _analysis_fingerprint(
    *,
    version_id: str,
    target_id: str,
    target_name: str,
    keyword: str,
    fields: list[ExtractField],
) -> str:
    payload = {
        "schema_version": _CONTEXT_ANALYSIS_SCHEMA_VERSION,
        "contact_policy_version": _CONTACT_POLICY_VERSION,
        "prompt_hash": article_analysis_prompt_fingerprint(),
        "version_id": version_id,
        "target_id": target_id,
        "target_name": target_name,
        "keyword": keyword,
        "fields": [field.model_dump(mode="json") for field in fields],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _compact_contextual_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    """关联层只保存任务特有字段；全文、联系方式和图片语义由版本层引用。"""
    fields = {
        key: value
        for key, value in dict(analysis.get("fields") or {}).items()
        if key not in _SOURCE_FIELD_KEYS
    }
    return {**analysis, "fields": fields}


async def _complete_contextual_analysis(
    analysis: dict[str, Any],
    *,
    capture: CapturedDocument,
    contacts: list[dict[str, Any]],
    image_analysis: list[dict[str, Any]],
    target_name: str,
    target_aliases: list[str],
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    validated_contacts: list[dict[str, Any]] = []
    for contact in contacts:
        if str(contact.get("source") or "text") != "image":
            validated_contacts.append(contact)
            continue
        normalized = normalize_contact_candidate(contact, source="image")
        if normalized:
            validated_contacts.append(
                {
                    **contact,
                    **normalized,
                    "contexts": list(
                        contact.get("contexts") or normalized["contexts"]
                    ),
                }
            )
    contacts = validated_contacts
    fields = dict(analysis.get("fields") or {})
    review_values = list(
        (analysis.get("relevance_review") or {}).get("target_contact_values") or []
    )
    declared_values = review_values or list(analysis.get("target_contact_values") or [])
    reviewed_contacts = filter_target_contacts(
        contacts,
        declared_values,
        text=capture.text,
    )
    attribution_error = ""
    target_contacts = reviewed_contacts
    if contacts:
        attributed, attribution_error = await attribute_target_contacts(
            target_name=target_name,
            target_aliases=target_aliases,
            title=capture.title,
            account=capture.account,
            summary=str(fields.get("summary") or ""),
            contacts=contacts,
            image_analysis=image_analysis,
            project_id=project_id,
            task_id=task_id,
        )
        if not attribution_error:
            target_contacts = attributed
    fields.update(
        {
            "title": capture.title,
            "account": capture.account,
            "publish_time": capture.publish_time,
            "content": capture.text,
            "contact": "、".join(
                str(item.get("label") or "") for item in target_contacts
            ),
            "image_context": [
                item.get("description")
                for item in image_analysis
                if item.get("description")
            ],
        }
    )
    return {
        **analysis,
        "fields": fields,
        "target_contacts": target_contacts,
        "contact_attribution_error": attribution_error,
    }


def _version_image_analysis(version: dict[str, Any]) -> list[dict[str, Any]]:
    if version.get("image_analysis"):
        return [dict(item) for item in version.get("image_analysis") or []]
    return [
        dict(image.get("analysis") or {})
        for image in version.get("images") or []
        if image.get("analysis")
    ]


def _usable_image_analysis(
    image_analysis: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        item
        for item in image_analysis
        if str(item.get("visible_text") or "").strip()
        or list(item.get("contacts") or [])
        or (
            item.get("is_key_evidence")
            and str(item.get("description") or "").strip()
        )
    ]


def _split_image_analysis_diagnostics(value: Any) -> tuple[str, str]:
    parts = [
        part.strip()
        for part in str(value or "").split("; ")
        if part.strip()
    ]
    warnings = [
        part
        for part in parts
        if "content_type=image/svg+xml" in part
        and "prepare_failed=UnidentifiedImageError" in part
    ]
    errors = [part for part in parts if part not in warnings]
    return "; ".join(errors)[:2000], "; ".join(warnings)[:2000]


def _archive_completeness(
    *,
    capture_metadata: dict[str, Any],
    image_analysis_error: str,
    image_analysis_warning: str,
) -> tuple[str, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    image_download_errors = list(
        capture_metadata.get("image_download_errors") or []
    )
    if image_download_errors:
        errors.append(f"原图下载失败 {len(image_download_errors)} 张")
    screenshot_error = str(
        capture_metadata.get("screenshot_capture_error") or ""
    ).strip()
    if screenshot_error:
        errors.append(f"页面截图不完整: {screenshot_error[:300]}")
    if image_analysis_error:
        errors.append(f"图片识别失败: {image_analysis_error[:500]}")
    if image_analysis_warning:
        warnings.append(
            "已跳过视觉模型不支持的 SVG 图片: "
            f"{image_analysis_warning[:500]}"
        )
    if errors:
        status = "partial"
    elif warnings:
        status = "complete_with_warnings"
    else:
        status = "complete"
    return status, [*errors, *warnings]


def _capture_with_image_evidence(
    capture: CapturedDocument,
    image_analysis: list[dict[str, Any]],
) -> CapturedDocument | None:
    """Expose OCR/key-image evidence to the same two-agent relevance review."""
    useful = sorted(
        _usable_image_analysis(image_analysis),
        key=lambda item: (
            not bool(item.get("contacts")),
            not bool(item.get("is_key_evidence")),
            -int(item.get("importance_score") or 0),
            int(item.get("index") or 0),
        ),
    )[:12]
    if not useful:
        return None
    evidence = [
        {
            "index": item.get("index"),
            "description": str(item.get("description") or "")[:1200],
            "visible_text": str(item.get("visible_text") or "")[:5000],
            "contacts": list(item.get("contacts") or [])[:20],
            "is_key_evidence": bool(item.get("is_key_evidence")),
            "importance_score": int(item.get("importance_score") or 0),
        }
        for item in useful
    ]
    evidence_text = json.dumps(evidence, ensure_ascii=False)[:20000]
    return replace(
        capture,
        text=(
            "【文章图片 OCR 与视觉证据】\n"
            f"{evidence_text}\n\n"
            "【网页正文】\n"
            f"{capture.text}"
        ),
    )


def _capture_has_more_complete_images(
    version: dict[str, Any],
    capture: CapturedDocument,
) -> bool:
    existing_metadata = dict(version.get("capture_metadata") or {})
    current_metadata = dict(capture.metadata or {})
    existing_urls = set(
        existing_metadata.get("analyzed_image_urls") or []
    ) or {
        str(image.get("source_url") or "")
        for image in version.get("images") or []
        if image.get("source_url")
    }
    captured_urls = {
        image.source_url for image in capture.images if image.source_url
    }
    if (
        current_metadata.get("image_download_errors")
        and existing_urls
        and not existing_urls.issubset(captured_urls)
    ):
        return False
    if int(version.get("media_policy_version") or 0) < _MEDIA_POLICY_VERSION:
        return True
    if existing_urls < captured_urls:
        return True
    if capture.images and not _usable_image_analysis(
        _version_image_analysis(version)
    ):
        return True
    if (
        existing_metadata.get("image_download_errors")
        and not current_metadata.get("image_download_errors")
        and capture.images
    ):
        return True
    if len(capture.screenshots) > len(version.get("screenshots") or []):
        return True
    if (
        existing_metadata.get("screenshot_capture_error")
        and not current_metadata.get("screenshot_capture_error")
        and len(capture.screenshots) >= len(version.get("screenshots") or [])
    ):
        return True
    return False


def _source_analysis(
    capture: CapturedDocument,
    *,
    contacts: list[dict[str, Any]],
    image_analysis: list[dict[str, Any]],
) -> dict[str, Any]:
    """版本层只保留来源自身事实，不混入项目、Target 或任务评分。"""
    return {
        "scope": "source",
        "fields": {
            "title": capture.title,
            "account": capture.account,
            "publish_time": capture.publish_time,
            "contact": "、".join(
                str(item.get("label") or "") for item in contacts
            ),
            "image_context": [
                item.get("description")
                for item in image_analysis
                if item.get("description")
            ],
        },
    }


def _extension(content_type: str, fallback: str) -> str:
    media_type = str(content_type or "").split(";", 1)[0].lower()
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
        "image/avif": "avif",
        "text/html": "html",
        "application/json": "json",
    }.get(media_type, fallback)


def _merge_contacts(
    text_contacts: list[dict[str, Any]],
    image_analysis: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}

    def _add(item: dict[str, Any]) -> None:
        channel = str(item.get("channel") or "").strip()
        value = str(item.get("value") or "").strip()
        if not channel or not value:
            return
        key = (channel, value.casefold())
        context = str(item.get("context") or "").strip()
        if key not in merged:
            merged[key] = {
                **item,
                "channel": channel,
                "value": value,
                "label": item.get("label") or f"{channel}: {value}",
                "contexts": [context] if context else list(item.get("contexts") or []),
            }
            return
        existing = merged[key]
        contexts = existing.setdefault("contexts", [])
        if context and context not in contexts and len(contexts) < 10:
            contexts.append(context)
        if not existing.get("context") and context:
            existing["context"] = context
        sources = set(existing.get("sources") or [existing.get("source") or "text"])
        sources.add(str(item.get("source") or "text"))
        existing["sources"] = sorted(sources)

    for contact in text_contacts:
        _add(contact)
    for image in image_analysis:
        try:
            index = int(image.get("index", -1))
        except (TypeError, ValueError):
            index = -1
        visible_text = str(image.get("visible_text") or "")
        for contact in extract_contacts(visible_text):
            _add({**contact, "source": "image", "image_index": index})
        for contact in image.get("contacts") or []:
            normalized = normalize_contact_candidate(contact, source="image")
            if normalized:
                _add({**normalized, "image_index": index})
    return list(merged.values())


def _select_archive_images(
    images: list[CapturedImage],
    analysis: list[dict[str, Any]],
    *,
    limit: int = 8,
) -> list[CapturedImage]:
    """Archive only contact-bearing or high-value images after analyzing all."""
    by_index = {int(item.get("index", -1)): item for item in analysis}
    ranked: list[tuple[int, int, CapturedImage]] = []
    for image in images:
        item = by_index.get(image.index, {})
        visible_contacts = extract_contacts(str(item.get("visible_text") or ""))
        model_contacts = [
            normalized
            for contact in item.get("contacts") or []
            if (normalized := normalize_contact_candidate(contact, source="image"))
        ]
        has_contacts = bool(visible_contacts or model_contacts)
        importance = int(item.get("importance_score") or 0)
        is_key = bool(item.get("is_key_evidence"))
        is_tiny = bool(
            image.width
            and image.height
            and image.width < 160
            and image.height < 160
        )
        if not has_contacts and (
            is_tiny or (not is_key and importance < 90) or importance < 80
        ):
            continue
        rank = (200 if has_contacts else 0) + importance
        ranked.append((-rank, image.index, image))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [image for _rank, _index, image in ranked[: max(1, limit)]]


def _subject_match(analysis: dict[str, Any], fallback: int | None) -> int:
    value = analysis.get("subject_match")
    if value is None:
        value = fallback
    try:
        return max(0, min(100, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _passes_target_review(analysis: dict[str, Any], min_subject_match: int) -> bool:
    return (
        analysis.get("review_decision") == "accept"
        and _subject_match(analysis, None) >= min_subject_match
    )


def _raise_on_analysis_failure(analysis: dict[str, Any]) -> None:
    """Keep infrastructure/model failures out of semantic rejection handling."""
    structure_error = str(analysis.get("analysis_error") or "").strip()
    review_error = str(
        (analysis.get("relevance_review") or {}).get("review_error") or ""
    ).strip()
    if structure_error:
        raise SourceDocumentAnalysisError(f"来源结构化分析失败: {structure_error}")
    if review_error:
        raise SourceDocumentAnalysisError(f"来源相关性审核失败: {review_error}")


async def _reanalyze_with_image_evidence(
    capture: CapturedDocument,
    analysis: dict[str, Any],
    image_analysis: list[dict[str, Any]],
    *,
    image_analysis_error: str,
    fields: list[ExtractField],
    target_name: str,
    keyword: str,
    required_subject_match: int,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    enriched_capture = _capture_with_image_evidence(capture, image_analysis)
    if enriched_capture is None:
        if image_analysis_error:
            raise SourceDocumentAnalysisError(
                f"来源图片证据识别失败: {image_analysis_error}"
            )
        return analysis
    reviewed = await analyze_and_review_article(
        enriched_capture,
        fields=fields,
        target_name=target_name,
        keyword=keyword,
        required_subject_match=required_subject_match,
        project_id=project_id,
        task_id=task_id,
    )
    _raise_on_analysis_failure(reviewed)
    return {
        **reviewed,
        "image_evidence_used": True,
        "image_evidence_indices": [
            int(item.get("index") or 0)
            for item in _usable_image_analysis(image_analysis)
        ],
    }


def _rejected_source_result(
    capture: CapturedDocument,
    analysis: dict[str, Any],
    *,
    document_id: str,
    version_id: str,
    target: dict[str, Any] | None,
    min_subject_match: int,
    fallback_subject_match: int | None,
) -> dict[str, Any]:
    subject_match = _subject_match(analysis, fallback_subject_match)
    return {
        "ok": False,
        "rejected": True,
        "reason": "文章未通过独立目标相关性审核",
        "source_type": capture.source_type,
        "source_url": capture.canonical_url,
        "document_id": document_id,
        "version_id": version_id,
        "target_id": str((target or {}).get("target_id") or ""),
        "target_name": str((target or {}).get("canonical_name") or ""),
        "fields": analysis.get("fields") or {},
        "score": analysis.get("score"),
        "subject_match": subject_match,
        "required_subject_match": min_subject_match,
        "score_reason": analysis.get("score_reason") or "",
        "review_decision": analysis.get("review_decision") or "reject",
        "article_scope": analysis.get("article_scope") or "uncertain",
        "image_evidence_used": bool(analysis.get("image_evidence_used")),
        "image_evidence_indices": list(
            analysis.get("image_evidence_indices") or []
        ),
        "contacts": [],
        "browser_screenshot_ids": [],
        "browser_screenshot_urls": [],
        "image_count": 0,
        "screenshot_count": 0,
    }


async def _store_capture_artifacts(
    capture: CapturedDocument,
    *,
    document_id: str,
    version_id: str,
    target_id: str,
    project_id: str,
    images_to_archive: list[CapturedImage] | None = None,
) -> tuple[dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
    storage = await get_object_storage()
    relative_path = f"{capture.source_type}/{document_id}/{version_id}"

    async def _store_html(kind: str, suffix: str, data: bytes) -> dict[str, Any]:
        object_id = _artifact_object_id(version_id, suffix, data)
        return await storage.store_bytes(
            data,
            kind=kind,
            filename=f"{suffix}.html",
            object_id=object_id,
            content_type="text/html; charset=utf-8",
            project_id=project_id,
            subject_id=target_id,
            source=capture.source_type,
            source_id=document_id,
            relative_path=relative_path,
            meta={
                "document_id": document_id,
                "version_id": version_id,
                "source_url": capture.canonical_url,
            },
        )

    raw_task = _store_html("source_document_raw", "raw", capture.raw_html)
    dom_task = _store_html("source_document_dom", "rendered", capture.rendered_html)

    async def _store_image(image: CapturedImage) -> dict[str, Any]:
        extension = _extension(image.content_type, "img")
        object_id = _artifact_object_id(
            version_id,
            f"image_{image.index:04d}",
            image.data,
        )
        stored = await storage.store_bytes(
            image.data,
            kind="source_document_image",
            filename=f"image-{image.index:04d}.{extension}",
            object_id=object_id,
            content_type=image.content_type,
            project_id=project_id,
            subject_id=target_id,
            source=capture.source_type,
            source_id=document_id,
            relative_path=relative_path,
            meta={
                "document_id": document_id,
                "version_id": version_id,
                "source_url": image.source_url,
                "index": image.index,
            },
        )
        return {
            "index": image.index,
            "source_url": image.source_url,
            "storage_object_id": stored["object_id"],
            "url": _object_url(stored["object_id"]),
            "content_type": image.content_type,
            "width": image.width,
            "height": image.height,
            "sha256": image.sha256,
            "size": len(image.data),
        }

    async def _store_screenshot(
        screenshot: CapturedScreenshot,
    ) -> dict[str, Any]:
        index = screenshot.index
        data = screenshot.data
        object_id = _artifact_object_id(
            version_id,
            f"screenshot_{index:04d}",
            data,
        )
        stored = await storage.store_bytes(
            data,
            kind="source_document_screenshot",
            filename=f"screenshot-{index:04d}.jpg",
            object_id=object_id,
            content_type="image/jpeg",
            project_id=project_id,
            subject_id=target_id,
            source=capture.source_type,
            source_id=document_id,
            relative_path=relative_path,
            meta={
                "document_id": document_id,
                "version_id": version_id,
                "index": index,
                "source_url": capture.canonical_url,
            },
        )
        return {
            "index": index,
            "source_url": capture.canonical_url,
            "storage_object_id": stored["object_id"],
            "url": _object_url(stored["object_id"]),
            "content_type": "image/jpeg",
            "width": screenshot.width,
            "height": screenshot.height,
            "size": len(data),
        }

    upload_semaphore = asyncio.Semaphore(12)

    async def _bounded_upload(awaitable):
        async with upload_semaphore:
            return await awaitable

    results = await asyncio.gather(
        *(
            _bounded_upload(awaitable)
            for awaitable in (
                raw_task,
                dom_task,
                *(_store_image(image) for image in (images_to_archive or [])),
                *(
                    _store_screenshot(screenshot)
                    for screenshot in capture.screenshots
                ),
            )
        )
    )
    raw = results[0]
    dom = results[1]
    image_count = len(images_to_archive or [])
    images = list(results[2 : 2 + image_count])
    screenshots = list(results[2 + image_count :])
    return (
        {
            "raw_html_object_id": raw["object_id"],
            "raw_html_url": _object_url(raw["object_id"]),
            "rendered_html_object_id": dom["object_id"],
            "rendered_html_url": _object_url(dom["object_id"]),
        },
        images,
        screenshots,
    )


async def _store_structured_json(
    structured: dict[str, Any],
    *,
    capture: CapturedDocument,
    document_id: str,
    version_id: str,
    target_id: str,
    project_id: str,
) -> dict[str, str]:
    storage = await get_object_storage()
    data = json.dumps(
        structured,
        ensure_ascii=False,
        default=str,
        indent=2,
    ).encode("utf-8")
    object_id = _artifact_object_id(version_id, "structured", data)
    stored = await storage.store_bytes(
        data,
        kind="source_document_structured",
        filename="article.json",
        object_id=object_id,
        content_type="application/json; charset=utf-8",
        project_id=project_id,
        subject_id=target_id,
        source=capture.source_type,
        source_id=document_id,
        relative_path=f"{capture.source_type}/{document_id}/{version_id}",
        meta={
            "document_id": document_id,
            "version_id": version_id,
            "source_url": capture.canonical_url,
        },
    )
    return {
        "structured_object_id": stored["object_id"],
        "structured_url": _object_url(stored["object_id"]),
    }


async def _refresh_cached_source_contacts(
    db: AsyncIOMotorDatabase,
    *,
    capture: CapturedDocument,
    document_id: str,
    version_id: str,
    version: dict[str, Any],
    contacts: list[dict[str, Any]],
    image_analysis: list[dict[str, Any]],
    target_id: str,
    project_id: str,
) -> dict[str, Any]:
    """Refresh derived contacts and structured JSON without replacing evidence."""
    image_analysis_error, migrated_warning = _split_image_analysis_diagnostics(
        version.get("image_analysis_error")
    )
    image_analysis_warning = "; ".join(
        value
        for value in [
            str(version.get("image_analysis_warning") or "").strip(),
            migrated_warning,
        ]
        if value
    )[:2000]
    archive_status, archive_warnings = _archive_completeness(
        capture_metadata=dict(version.get("capture_metadata") or {}),
        image_analysis_error=image_analysis_error,
        image_analysis_warning=image_analysis_warning,
    )
    source_analysis = _source_analysis(
        capture,
        contacts=contacts,
        image_analysis=image_analysis,
    )
    artifacts = dict(version.get("artifacts") or {})
    provenance_artifacts = {
        key: value
        for key, value in artifacts.items()
        if not key.startswith("structured_")
    }
    structured = {
        "schema_version": 2,
        "contact_policy_version": _CONTACT_POLICY_VERSION,
        "document_id": document_id,
        "version_id": version_id,
        "content_hash": version.get("content_hash") or "",
        "source_type": version.get("source_type") or capture.source_type,
        "source": {
            "identity": dict(version.get("identity") or {}),
            "content": dict(version.get("content") or {}),
            "analysis": source_analysis,
        },
        "evidence": {
            "contacts": contacts,
            "media": {
                "images": image_analysis,
                "archived_images": list(version.get("images") or []),
                "screenshots": list(version.get("screenshots") or []),
                "image_analysis_error": image_analysis_error,
                "image_analysis_warning": image_analysis_warning,
                "archive_status": archive_status,
                "archive_warnings": archive_warnings,
            },
        },
        "provenance": {
            "capture_metadata": dict(version.get("capture_metadata") or {}),
            "artifacts": provenance_artifacts,
        },
    }
    structured_artifact = await _store_structured_json(
        structured,
        capture=capture,
        document_id=document_id,
        version_id=version_id,
        target_id=target_id,
        project_id=project_id,
    )
    artifacts.update(structured_artifact)
    storage_object_ids = list(version.get("storage_object_ids") or [])
    structured_object_id = str(
        structured_artifact.get("structured_object_id") or ""
    )
    if structured_object_id and structured_object_id not in storage_object_ids:
        storage_object_ids.append(structured_object_id)
    return await source_dao.mark_version_ready(
        db,
        version_id=version_id,
        payload={
            "contacts": contacts,
            "analysis": source_analysis,
            "contact_policy_version": _CONTACT_POLICY_VERSION,
            "image_analysis_error": image_analysis_error,
            "image_analysis_warning": image_analysis_warning,
            "archive_status": archive_status,
            "archive_warnings": archive_warnings,
            "artifacts": artifacts,
            "storage_object_ids": storage_object_ids,
        },
    )


def _result_from_version(
    document: dict[str, Any],
    version: dict[str, Any],
    *,
    cached: bool,
    target: dict[str, Any] | None,
    analysis_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    analysis = analysis_override or version.get("analysis") or {}
    screenshots = version.get("screenshots") or []
    return {
        "ok": version.get("status") == "ready",
        "cached": cached,
        "source_type": document.get("source_type") or version.get("source_type"),
        "source_url": document.get("canonical_url") or version.get("canonical_url"),
        "document_id": document.get("document_id") or version.get("document_id"),
        "version_id": version.get("version_id"),
        "content_hash": version.get("content_hash"),
        "target_id": (target or {}).get("target_id") or "",
        "target_name": (target or {}).get("canonical_name") or "",
        "fields": analysis.get("fields") or {},
        "score": analysis.get("score"),
        "subject_match": analysis.get("subject_match"),
        "score_reason": analysis.get("score_reason") or "",
        "review_decision": analysis.get("review_decision") or "",
        "article_scope": analysis.get("article_scope") or "uncertain",
        "image_evidence_used": bool(analysis.get("image_evidence_used")),
        "image_evidence_indices": list(
            analysis.get("image_evidence_indices") or []
        ),
        "contacts": list(analysis.get("target_contacts") or []),
        "browser_screenshot_ids": [
            item.get("storage_object_id") for item in screenshots if item.get("storage_object_id")
        ],
        "browser_screenshot_urls": [item.get("url") for item in screenshots if item.get("url")],
        "image_count": len(version.get("images") or []),
        "screenshot_count": len(screenshots),
        "archive_status": version.get("archive_status") or "unknown",
        "archive_warnings": list(version.get("archive_warnings") or []),
    }


async def ingest_source_url(
    db: AsyncIOMotorDatabase,
    *,
    url: str,
    project_id: str = "",
    target: dict[str, Any] | None = None,
    task_def_id: str = "",
    run_task_id: str = "",
    keyword: str = "",
    extract_fields: list[ExtractField] | None = None,
    discovery_score: int | None = None,
    discovery_subject_match: int | None = None,
    discovery_context: dict[str, Any] | None = None,
    persist: bool = True,
    min_subject_match: int = 0,
) -> dict[str, Any]:
    """读取、结构化并永久保存一个来源 URL；同内容版本不会重复上传。"""
    canonical_url = canonicalize_source_url(url)
    document_id = source_dao.document_id_for_url(canonical_url)
    provider = get_source_document_provider(canonical_url)
    target_id = str((target or {}).get("target_id") or "")
    target_name = str((target or {}).get("canonical_name") or "")
    target_aliases = [
        str(value).strip()
        for value in (target or {}).get("aliases") or []
        if str(value).strip() and str(value).strip() != target_name
    ][:8]
    target_analysis_name = target_name
    if target_aliases:
        target_analysis_name += f"（可靠别名：{'、'.join(target_aliases)}）"
    task_fields = list(extract_fields or [])

    async with _hold_document_lock(document_id):
        capture = await provider.capture(
            canonical_url,
            task_id=run_task_id or task_def_id or document_id,
        )
        content_hash = stable_content_hash(capture)
        version_id = source_dao.version_id_for_content(document_id, content_hash)
        analysis_fingerprint = _analysis_fingerprint(
            version_id=version_id,
            target_id=target_id,
            target_name=target_name,
            keyword=keyword,
            fields=task_fields,
        )
        if persist:
            existing = await source_dao.get_version(db, version_id)
            if (
                existing
                and existing.get("status") == "ready"
                and not _capture_has_more_complete_images(existing, capture)
            ):
                required_subject_match = max(
                    0, min(100, int(min_subject_match or 0))
                )
                version_image_analysis = _version_image_analysis(existing)
                existing_image_error, migrated_image_warning = (
                    _split_image_analysis_diagnostics(
                        existing.get("image_analysis_error")
                    )
                )
                source_contacts = _merge_contacts(
                    extract_contacts(capture.text),
                    version_image_analysis,
                )
                if (
                    int(existing.get("contact_policy_version") or 0)
                    < _CONTACT_POLICY_VERSION
                    or source_contacts != list(existing.get("contacts") or [])
                    or not existing.get("archive_status")
                    or bool(migrated_image_warning)
                ):
                    existing = await _refresh_cached_source_contacts(
                        db,
                        capture=capture,
                        document_id=document_id,
                        version_id=version_id,
                        version=existing,
                        contacts=source_contacts,
                        image_analysis=version_image_analysis,
                        target_id=target_id,
                        project_id=project_id,
                    )
                    await source_dao.upsert_document(
                        db,
                        document_id=document_id,
                        canonical_url=canonical_url,
                        source_type=capture.source_type,
                        version=existing,
                    )
                contextual_analysis: dict[str, Any] | None = None
                existing_link = await source_dao.get_document_link(
                    db,
                    project_id=project_id,
                    target_id=target_id,
                    document_id=document_id,
                )
                if (
                    existing_link
                    and existing_link.get("analysis_fingerprint")
                    == analysis_fingerprint
                    and existing_link.get("latest_analysis")
                ):
                    contextual_analysis = dict(
                        existing_link.get("latest_analysis") or {}
                    )
                if contextual_analysis is None:
                    contextual_analysis = await analyze_and_review_article(
                        capture,
                        fields=task_fields,
                        target_name=target_analysis_name,
                        keyword=keyword,
                        required_subject_match=required_subject_match,
                        project_id=project_id,
                        task_id=run_task_id,
                    )
                _raise_on_analysis_failure(contextual_analysis)
                if not _passes_target_review(
                    contextual_analysis,
                    required_subject_match,
                ):
                    contextual_analysis = await _reanalyze_with_image_evidence(
                        capture,
                        contextual_analysis,
                        version_image_analysis,
                        image_analysis_error=existing_image_error,
                        fields=task_fields,
                        target_name=target_analysis_name,
                        keyword=keyword,
                        required_subject_match=required_subject_match,
                        project_id=project_id,
                        task_id=run_task_id,
                    )
                contextual_analysis = await _complete_contextual_analysis(
                    contextual_analysis,
                    capture=capture,
                    contacts=source_contacts,
                    image_analysis=version_image_analysis,
                    target_name=target_name,
                    target_aliases=target_aliases,
                    project_id=project_id,
                    task_id=run_task_id,
                )
                if not _passes_target_review(
                    contextual_analysis,
                    required_subject_match,
                ):
                    await _persist_existing_link_review(
                        db,
                        document_id=document_id,
                        version_id=version_id,
                        project_id=project_id,
                        target=target,
                        task_def_id=task_def_id,
                        run_task_id=run_task_id,
                        keyword=keyword,
                        analysis=contextual_analysis,
                        analysis_fingerprint=analysis_fingerprint,
                        discovery_score=discovery_score,
                        discovery_subject_match=discovery_subject_match,
                        discovery_context=discovery_context,
                    )
                    return _rejected_source_result(
                        capture,
                        contextual_analysis,
                        document_id=document_id,
                        version_id=version_id,
                        target=target,
                        min_subject_match=min_subject_match,
                        fallback_subject_match=discovery_subject_match,
                    )
                document = await source_dao.upsert_document(
                    db,
                    document_id=document_id,
                    canonical_url=canonical_url,
                    source_type=capture.source_type,
                    version=existing,
                    target_id=target_id,
                )
                await _link_discovery(
                    db,
                    document_id=document_id,
                    version_id=version_id,
                    project_id=project_id,
                    target=target,
                    task_def_id=task_def_id,
                    run_task_id=run_task_id,
                    keyword=keyword,
                    score=contextual_analysis.get("score")
                    if contextual_analysis.get("score") is not None
                    else discovery_score,
                    subject_match=contextual_analysis.get("subject_match")
                    if contextual_analysis.get("subject_match") is not None
                    else discovery_subject_match,
                    discovery_context=discovery_context,
                    contextual_analysis=_compact_contextual_analysis(
                        contextual_analysis
                    ),
                    analysis_fingerprint=analysis_fingerprint,
                )
                return _result_from_version(
                    document,
                    existing,
                    cached=True,
                    target=target,
                    analysis_override=contextual_analysis,
                )

        version_started = False
        try:
            required_subject_match = max(0, min(100, int(min_subject_match or 0)))
            image_analysis: list[dict[str, Any]] = []
            image_analysis_error = ""
            image_analysis_warning = ""
            images_analyzed = False
            analysis = await analyze_and_review_article(
                capture,
                fields=task_fields,
                target_name=target_analysis_name,
                keyword=keyword,
                required_subject_match=required_subject_match,
                project_id=project_id,
                task_id=run_task_id,
            )
            _raise_on_analysis_failure(analysis)
            if not _passes_target_review(analysis, required_subject_match):
                if capture.images:
                    image_analysis, raw_image_analysis_error = (
                        await analyze_article_images(
                            capture.images,
                            project_id=project_id,
                            task_id=run_task_id,
                        )
                    )
                    (
                        image_analysis_error,
                        image_analysis_warning,
                    ) = _split_image_analysis_diagnostics(
                        raw_image_analysis_error
                    )
                    images_analyzed = True
                    analysis = await _reanalyze_with_image_evidence(
                        capture,
                        analysis,
                        image_analysis,
                        image_analysis_error=image_analysis_error,
                        fields=task_fields,
                        target_name=target_analysis_name,
                        keyword=keyword,
                        required_subject_match=required_subject_match,
                        project_id=project_id,
                        task_id=run_task_id,
                    )
                elif (
                    (capture.metadata or {}).get("image_urls")
                    and (capture.metadata or {}).get("image_download_errors")
                ):
                    raise SourceDocumentAnalysisError(
                        "来源原图下载失败，无法完成图片证据相关性审核"
                    )
            if not _passes_target_review(analysis, required_subject_match):
                if persist:
                    await _persist_existing_link_review(
                        db,
                        document_id=document_id,
                        version_id=version_id,
                        project_id=project_id,
                        target=target,
                        task_def_id=task_def_id,
                        run_task_id=run_task_id,
                        keyword=keyword,
                        analysis=analysis,
                        analysis_fingerprint=analysis_fingerprint,
                        discovery_score=discovery_score,
                        discovery_subject_match=discovery_subject_match,
                        discovery_context=discovery_context,
                    )
                return _rejected_source_result(
                    capture,
                    analysis,
                    document_id=document_id,
                    version_id=version_id,
                    target=target,
                    min_subject_match=required_subject_match,
                    fallback_subject_match=discovery_subject_match,
                )
            if persist:
                await source_dao.begin_version(
                    db,
                    version_id=version_id,
                    document_id=document_id,
                    content_hash=content_hash,
                    source_type=capture.source_type,
                )
                version_started = True
            if not images_analyzed:
                image_analysis, raw_image_analysis_error = (
                    await analyze_article_images(
                        capture.images,
                        project_id=project_id,
                        task_id=run_task_id,
                    )
                )
                (
                    image_analysis_error,
                    image_analysis_warning,
                ) = _split_image_analysis_diagnostics(
                    raw_image_analysis_error
                )
            images_to_archive = _select_archive_images(
                capture.images,
                image_analysis,
            )
            if persist:
                artifacts, images, screenshots = await _store_capture_artifacts(
                    capture,
                    document_id=document_id,
                    version_id=version_id,
                    target_id=target_id,
                    project_id=project_id,
                    images_to_archive=images_to_archive,
                )
            else:
                artifacts, images, screenshots = {}, [], []

            analysis_by_index = {
                int(item.get("index", -1)): item for item in image_analysis
            }
            for image in images:
                image["analysis"] = analysis_by_index.get(image["index"], {})
            contacts = _merge_contacts(
                extract_contacts(capture.text), image_analysis
            )
            analysis = await _complete_contextual_analysis(
                analysis,
                capture=capture,
                contacts=contacts,
                image_analysis=image_analysis,
                target_name=target_name,
                target_aliases=target_aliases,
                project_id=project_id,
                task_id=run_task_id,
            )
            analysis_fields = dict(analysis.get("fields") or {})
            identity = {
                "title": capture.title,
                "account": capture.account,
                "publish_time": capture.publish_time,
                "canonical_url": capture.canonical_url,
            }
            content = {
                "summary": capture.text[:500],
                "text": capture.text,
                "text_length": len(capture.text),
            }
            source_analysis = _source_analysis(
                capture,
                contacts=contacts,
                image_analysis=image_analysis,
            )
            archive_status, archive_warnings = _archive_completeness(
                capture_metadata=dict(capture.metadata or {}),
                image_analysis_error=image_analysis_error,
                image_analysis_warning=image_analysis_warning,
            )
            structured = {
                "schema_version": 2,
                "contact_policy_version": _CONTACT_POLICY_VERSION,
                "document_id": document_id,
                "version_id": version_id,
                "content_hash": content_hash,
                "source_type": capture.source_type,
                "source": {
                    "identity": identity,
                    "content": content,
                    "analysis": source_analysis,
                },
                "evidence": {
                    "contacts": contacts,
                    "media": {
                        "images": image_analysis,
                        "archived_images": images,
                        "screenshots": screenshots,
                        "image_analysis_error": image_analysis_error,
                        "image_analysis_warning": image_analysis_warning,
                        "archive_status": archive_status,
                        "archive_warnings": archive_warnings,
                    },
                },
                "provenance": {
                    "capture_metadata": {
                        **capture.metadata,
                        "analyzed_image_urls": [
                            image.source_url for image in capture.images if image.source_url
                        ],
                    },
                    "artifacts": artifacts,
                },
            }

            if not persist:
                document = {
                    "document_id": document_id,
                    "canonical_url": canonical_url,
                    "source_type": capture.source_type,
                }
                version = {
                    "version_id": version_id,
                    "document_id": document_id,
                    "content_hash": content_hash,
                    "status": "ready",
                    "analysis": analysis,
                    "contacts": contacts,
                    "contact_policy_version": _CONTACT_POLICY_VERSION,
                    "images": image_analysis,
                    "screenshots": [],
                    "archive_status": archive_status,
                    "archive_warnings": archive_warnings,
                }
                return _result_from_version(
                    document, version, cached=False, target=target
                )

            structured_artifact = await _store_structured_json(
                structured,
                capture=capture,
                document_id=document_id,
                version_id=version_id,
                target_id=target_id,
                project_id=project_id,
            )
            artifacts.update(structured_artifact)
            storage_object_ids = [
                value
                for key, value in artifacts.items()
                if key.endswith("_object_id") and value
            ] + [
                item["storage_object_id"]
                for item in [*images, *screenshots]
                if item.get("storage_object_id")
            ]
            version_payload = {
                "version_id": version_id,
                "document_id": document_id,
                "content_hash": content_hash,
                "source_type": capture.source_type,
                "canonical_url": canonical_url,
                "identity": identity,
                "content": content,
                "contacts": contacts,
                "contact_policy_version": _CONTACT_POLICY_VERSION,
                "analysis": source_analysis,
                "images": images,
                "image_analysis": image_analysis,
                "media_policy_version": _MEDIA_POLICY_VERSION,
                "screenshots": screenshots,
                "artifacts": artifacts,
                "storage_object_ids": storage_object_ids,
                "capture_metadata": {
                    **capture.metadata,
                    "analyzed_image_urls": [
                        image.source_url for image in capture.images if image.source_url
                    ],
                },
                "image_analysis_error": image_analysis_error,
                "image_analysis_warning": image_analysis_warning,
                "archive_status": archive_status,
                "archive_warnings": archive_warnings,
            }
            version = await source_dao.mark_version_ready(
                db, version_id=version_id, payload=version_payload
            )
            document = await source_dao.upsert_document(
                db,
                document_id=document_id,
                canonical_url=canonical_url,
                source_type=capture.source_type,
                version=version,
                target_id=target_id,
            )
            await _link_discovery(
                db,
                document_id=document_id,
                version_id=version_id,
                project_id=project_id,
                target=target,
                task_def_id=task_def_id,
                run_task_id=run_task_id,
                keyword=keyword,
                score=analysis.get("score")
                if analysis.get("score") is not None
                else discovery_score,
                subject_match=analysis.get("subject_match")
                if analysis.get("subject_match") is not None
                else discovery_subject_match,
                discovery_context=discovery_context,
                contextual_analysis=_compact_contextual_analysis(analysis),
                analysis_fingerprint=analysis_fingerprint,
            )
            return _result_from_version(
                document,
                version,
                cached=False,
                target=target,
                analysis_override=analysis,
            )
        except Exception as exc:
            if persist and version_started:
                await source_dao.mark_version_error(db, version_id, str(exc))
            raise


async def _link_discovery(
    db: AsyncIOMotorDatabase,
    *,
    document_id: str,
    version_id: str,
    project_id: str,
    target: dict[str, Any] | None,
    task_def_id: str,
    run_task_id: str,
    keyword: str,
    score: int | None,
    subject_match: int | None,
    discovery_context: dict[str, Any] | None,
    contextual_analysis: dict[str, Any] | None = None,
    analysis_fingerprint: str = "",
) -> None:
    if not project_id:
        return
    target_id = str((target or {}).get("target_id") or "")
    await source_dao.link_document(
        db,
        document_id=document_id,
        version_id=version_id,
        project_id=project_id,
        target_id=target_id,
        target_name=str((target or {}).get("canonical_name") or ""),
        task_def_id=task_def_id,
        run_task_id=run_task_id,
        keyword=keyword,
        score=score,
        subject_match=subject_match,
        discovery_context=discovery_context,
        contextual_analysis=contextual_analysis,
        analysis_fingerprint=analysis_fingerprint,
    )
    if target_id:
        await targets_dao.touch_project_target_collection(
            db,
            project_id=project_id,
            target_id=target_id,
            run_task_id=run_task_id,
        )


async def _persist_existing_link_review(
    db: AsyncIOMotorDatabase,
    *,
    document_id: str,
    version_id: str,
    project_id: str,
    target: dict[str, Any] | None,
    task_def_id: str,
    run_task_id: str,
    keyword: str,
    analysis: dict[str, Any],
    analysis_fingerprint: str,
    discovery_score: int | None,
    discovery_subject_match: int | None,
    discovery_context: dict[str, Any] | None,
) -> None:
    """Refresh a prior discovery link without creating links for new rejects."""
    if not project_id:
        return
    version = await source_dao.get_version(db, version_id)
    if not version or version.get("status") != "ready":
        return
    target_id = str((target or {}).get("target_id") or "")
    existing_link = await source_dao.get_document_link(
        db,
        project_id=project_id,
        target_id=target_id,
        document_id=document_id,
    )
    if not existing_link:
        return
    await _link_discovery(
        db,
        document_id=document_id,
        version_id=version_id,
        project_id=project_id,
        target=target,
        task_def_id=task_def_id,
        run_task_id=run_task_id,
        keyword=keyword,
        score=(
            analysis.get("score")
            if analysis.get("score") is not None
            else discovery_score
        ),
        subject_match=(
            analysis.get("subject_match")
            if analysis.get("subject_match") is not None
            else discovery_subject_match
        ),
        discovery_context=discovery_context,
        contextual_analysis=_compact_contextual_analysis(analysis),
        analysis_fingerprint=analysis_fingerprint,
    )


async def get_source_document_detail(
    db: AsyncIOMotorDatabase,
    document_id: str,
    *,
    project_id: str = "",
    version_id: str = "",
) -> dict[str, Any] | None:
    document = await source_dao.get_document(db, document_id)
    if not document:
        return None
    if version_id:
        version = await source_dao.get_version(db, version_id)
        if not version or str(version.get("document_id") or "") != document_id:
            return None
    else:
        version = await source_dao.get_latest_version(db, document_id)
    if not version:
        return {**document, "version": None, "links": []}
    links = await source_dao.get_links_for_document(
        db, document_id, project_id=project_id
    )
    return {**document, "version": version, "links": links}
