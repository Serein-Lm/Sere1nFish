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
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import source_documents as source_dao
from api.dao import targets as targets_dao
from api.models.mobile_collect import ExtractField
from api.storage import get_object_storage
from core.mobile.collect.contacts import extract_contacts

from .analysis import (
    analyze_article_fields,
    analyze_article_images,
    stable_content_hash,
)
from .contracts import CapturedDocument, CapturedImage, CapturedScreenshot
from .factory import get_source_document_provider
from .urls import canonicalize_source_url


_document_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_document_lock_users: defaultdict[str, int] = defaultdict(int)
_CONTEXT_ANALYSIS_SCHEMA_VERSION = 2
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


def _complete_contextual_analysis(
    analysis: dict[str, Any],
    *,
    capture: CapturedDocument,
    contacts: list[dict[str, Any]],
    image_analysis: list[dict[str, Any]],
) -> dict[str, Any]:
    fields = dict(analysis.get("fields") or {})
    fields.update(
        {
            "title": capture.title,
            "account": capture.account,
            "publish_time": capture.publish_time,
            "content": capture.text,
            "contact": "、".join(
                str(item.get("label") or "") for item in contacts
            ),
            "image_context": [
                item.get("description")
                for item in image_analysis
                if item.get("description")
            ],
        }
    )
    return {**analysis, "fields": fields}


def _version_image_analysis(version: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(image.get("analysis") or {})
        for image in version.get("images") or []
        if image.get("analysis")
    ]


def _capture_has_more_complete_images(
    version: dict[str, Any],
    capture: CapturedDocument,
) -> bool:
    existing_urls = {
        str(image.get("source_url") or "")
        for image in version.get("images") or []
        if image.get("source_url")
    }
    captured_urls = {image.source_url for image in capture.images if image.source_url}
    return bool(existing_urls < captured_urls)


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
        index = int(image.get("index", -1))
        visible_text = str(image.get("visible_text") or "")
        for contact in extract_contacts(visible_text):
            _add({**contact, "source": "image", "image_index": index})
        for contact in image.get("contacts") or []:
            _add({**contact, "source": "image", "image_index": index})
    return list(merged.values())


async def _store_capture_artifacts(
    capture: CapturedDocument,
    *,
    document_id: str,
    version_id: str,
    target_id: str,
    project_id: str,
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

    results = await asyncio.gather(
        raw_task,
        dom_task,
        *(_store_image(image) for image in capture.images),
        *(
            _store_screenshot(screenshot)
            for screenshot in capture.screenshots
        ),
    )
    raw = results[0]
    dom = results[1]
    image_count = len(capture.images)
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
        "contacts": version.get("contacts") or [],
        "browser_screenshot_ids": [
            item.get("storage_object_id") for item in screenshots if item.get("storage_object_id")
        ],
        "browser_screenshot_urls": [item.get("url") for item in screenshots if item.get("url")],
        "image_count": len(version.get("images") or []),
        "screenshot_count": len(screenshots),
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
) -> dict[str, Any]:
    """读取、结构化并永久保存一个来源 URL；同内容版本不会重复上传。"""
    canonical_url = canonicalize_source_url(url)
    document_id = source_dao.document_id_for_url(canonical_url)
    provider = get_source_document_provider(canonical_url)
    target_id = str((target or {}).get("target_id") or "")
    target_name = str((target or {}).get("canonical_name") or "")
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
                    contextual_analysis = await analyze_article_fields(
                        capture,
                        fields=task_fields,
                        target_name=target_name,
                        keyword=keyword,
                        project_id=project_id,
                        task_id=run_task_id,
                    )
                contextual_analysis = _complete_contextual_analysis(
                    contextual_analysis,
                    capture=capture,
                    contacts=list(existing.get("contacts") or []),
                    image_analysis=_version_image_analysis(existing),
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

        if persist:
            await source_dao.begin_version(
                db,
                version_id=version_id,
                document_id=document_id,
                content_hash=content_hash,
                source_type=capture.source_type,
            )
        try:
            structure_task = analyze_article_fields(
                capture,
                fields=task_fields,
                target_name=target_name,
                keyword=keyword,
                project_id=project_id,
                task_id=run_task_id,
            )
            images_task = analyze_article_images(
                capture.images,
                project_id=project_id,
                task_id=run_task_id,
            )
            if persist:
                artifacts_task = _store_capture_artifacts(
                    capture,
                    document_id=document_id,
                    version_id=version_id,
                    target_id=target_id,
                    project_id=project_id,
                )
                analysis, image_result, artifact_result = await asyncio.gather(
                    structure_task, images_task, artifacts_task
                )
                artifacts, images, screenshots = artifact_result
            else:
                analysis, image_result = await asyncio.gather(
                    structure_task, images_task
                )
                artifacts, images, screenshots = {}, [], []

            image_analysis, image_analysis_error = image_result
            analysis_by_index = {
                int(item.get("index", -1)): item for item in image_analysis
            }
            for image in images:
                image["analysis"] = analysis_by_index.get(image["index"], {})
            contacts = _merge_contacts(
                extract_contacts(capture.text), image_analysis
            )
            analysis = _complete_contextual_analysis(
                analysis,
                capture=capture,
                contacts=contacts,
                image_analysis=image_analysis,
            )
            analysis_fields = dict(analysis.get("fields") or {})
            identity = {
                "title": capture.title,
                "account": capture.account,
                "publish_time": capture.publish_time,
                "canonical_url": capture.canonical_url,
            }
            content = {
                "summary": analysis_fields.get("summary") or capture.text[:500],
                "text": capture.text,
                "text_length": len(capture.text),
            }
            source_analysis = _source_analysis(
                capture,
                contacts=contacts,
                image_analysis=image_analysis,
            )
            structured = {
                "schema_version": 2,
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
                        "images": images if persist else image_analysis,
                        "screenshots": screenshots,
                        "image_analysis_error": image_analysis_error,
                    },
                },
                "provenance": {
                    "capture_metadata": capture.metadata,
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
                    "images": image_analysis,
                    "screenshots": [],
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
                "analysis": source_analysis,
                "images": images,
                "screenshots": screenshots,
                "artifacts": artifacts,
                "storage_object_ids": storage_object_ids,
                "capture_metadata": capture.metadata,
                "image_analysis_error": image_analysis_error,
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
            if persist:
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
