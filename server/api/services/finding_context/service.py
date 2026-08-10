"""Finding 上下文解析、排队、Agent 执行与结果投影。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageOps
from motor.motor_asyncio import AsyncIOMotorDatabase

from Sere1nGraph.graph.prompts.loader import load_prompt
from api.dao import finding_contexts as context_dao
from api.dao import findings as findings_dao
from api.dao import source_documents as source_dao
from api.dao import targets as targets_dao
from api.dao.project_scope import project_scope_query
from api.db.collections import FINDINGS_COLLECTION, URL_SCAN_RESULTS_COLLECTION
from api.storage import get_object_storage
from api.utils.url_identity import endpoint_identity
from core.background import spawn_background
from core.logger import get_logger

from .agent import (
    PROMPT_SLUG,
    FindingContextAgentRequest,
    FindingContextImage,
    create_finding_context_agent,
)
from .schemas import FindingContextResult


logger = get_logger("finding_context")
_SCHEMA_VERSION = 2
_MAX_SOURCE_TEXT = 80_000
_MAX_IMAGES = 6
_WORKER_COUNT = 2
_worker_task: asyncio.Task[Any] | None = None
_worker_rerun_requested = False


@dataclass
class _ImageCandidate:
    object_id: str
    evidence_ref: str
    kind: str
    description: str
    visible_text: str
    relevance: str
    priority: int


@dataclass
class _ResolvedInput:
    finding: dict[str, Any]
    text: str
    allowed_refs: set[str]
    image_candidates: list[_ImageCandidate]
    source_document_ids: list[str]
    source_document_version_ids: list[str]
    input_fingerprint: str
    prompt_hash: str
    source_url: str


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _storage_object_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"/storage/objects/([^/?#]+)/(?:content|access)", text)
    if match:
        return match.group(1)
    if not any(marker in text for marker in ("/", "?", "#", "://")):
        return text
    return ""


def _source_url(finding: dict[str, Any]) -> str:
    latest = finding.get("latest_evidence_ref") or {}
    for value in (
        finding.get("source_url"),
        finding.get("url"),
        latest.get("source_url") if isinstance(latest, dict) else "",
    ):
        text = str(value or "").strip()
        if text.lower().startswith(("http://", "https://")):
            return text
    return ""


def _source_references(finding: dict[str, Any]) -> tuple[list[str], list[str]]:
    document_ids: list[str] = []
    version_ids: list[str] = []
    evidence_refs = [
        *(finding.get("evidence_refs") or []),
        finding.get("latest_evidence_ref") or {},
        finding.get("evidence") or {},
    ]
    document_ids.extend(
        str(value or "").strip()
        for value in [
            finding.get("source_document_id"),
            *(finding.get("source_document_ids") or []),
        ]
    )
    version_ids.extend(
        str(value or "").strip()
        for value in [finding.get("source_document_version_id")]
    )
    for evidence in evidence_refs:
        if not isinstance(evidence, dict):
            continue
        document_ids.append(str(evidence.get("source_document_id") or "").strip())
        version_ids.append(
            str(evidence.get("source_document_version_id") or "").strip()
        )
    return _unique(document_ids), _unique(version_ids)


def _finding_payload(finding: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "finding_id",
        "source",
        "type",
        "scope",
        "channel",
        "role",
        "subtype",
        "label",
        "value",
        "context",
        "article_context",
        "summary",
        "attention_score",
        "attention_reason",
        "party_name",
        "party_role",
        "target_relation",
        "target_relation_reason",
        "entity_name",
        "url",
        "source_url",
        "evidence",
        "evidence_refs",
        "latest_evidence_ref",
    )
    return {key: finding.get(key) for key in keys if finding.get(key) not in (None, "", [])}


def _append_image_candidate(
    candidates: list[_ImageCandidate],
    *,
    object_id: str,
    kind: str,
    analysis: dict[str, Any] | None = None,
    priority: int = 0,
) -> None:
    normalized = _storage_object_id(object_id)
    if not normalized or any(item.object_id == normalized for item in candidates):
        return
    details = dict(analysis or {})
    candidates.append(
        _ImageCandidate(
            object_id=normalized,
            evidence_ref=f"image:{normalized}",
            kind=kind,
            description=str(details.get("description") or "")[:2_000],
            visible_text=str(details.get("visible_text") or "")[:8_000],
            relevance=str(
                details.get("archive_reason")
                or details.get("relevance")
                or ""
            )[:1_000],
            priority=max(
                int(details.get("importance_score") or 0),
                priority,
                90 if details.get("is_key_evidence") else 0,
            ),
        )
    )


async def _resolve_source_versions(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str,
    document_ids: list[str],
    version_ids: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]]:
    versions: list[dict[str, Any]] = []
    resolved_documents = list(document_ids)
    resolved_versions = list(version_ids)
    for version_id in version_ids[:8]:
        version = await source_dao.get_version(db, version_id)
        if version:
            versions.append(version)
            resolved_documents.append(str(version.get("document_id") or ""))
    loaded_version_ids = {
        str(item.get("version_id") or "") for item in versions
    }
    for document_id in _unique(resolved_documents)[:8]:
        if any(str(item.get("document_id") or "") == document_id for item in versions):
            continue
        version = await source_dao.get_latest_version(db, document_id)
        if version and str(version.get("version_id") or "") not in loaded_version_ids:
            versions.append(version)
            loaded_version_ids.add(str(version.get("version_id") or ""))
            resolved_versions.append(str(version.get("version_id") or ""))
    links: list[dict[str, Any]] = []
    for document_id in _unique(resolved_documents)[:8]:
        link = await source_dao.get_document_link(
            db,
            project_id=project_id,
            target_id=target_id,
            document_id=document_id,
        )
        if link:
            links.append(link)
    return (
        versions,
        links,
        _unique(resolved_documents),
        _unique(resolved_versions),
    )


async def _browser_scan_snapshot(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str,
    source_url: str,
) -> dict[str, Any]:
    identity = endpoint_identity(source_url)
    if not project_id or not identity:
        return {}
    scope = project_scope_query(project_id, {})
    query: dict[str, Any] = {
        "$and": [
            scope,
            {"target_id": target_id} if target_id else {},
            {
                "$or": [
                    {"endpoint_key": identity},
                    {"url": source_url},
                ]
            },
        ]
    }
    return await db[URL_SCAN_RESULTS_COLLECTION].find_one(
        query,
        {
            "_id": 0,
            "result_id": 1,
            "task_id": 1,
            "url": 1,
            "intro": 1,
            "classification": 1,
            "screenshot_object_id": 1,
            "screenshot_url": 1,
            "updated_at": 1,
        },
        sort=[("updated_at", -1)],
    ) or {}


async def _sibling_findings(
    db: AsyncIOMotorDatabase,
    *,
    finding: dict[str, Any],
    source_url: str,
) -> list[dict[str, Any]]:
    if not source_url:
        return []
    query: dict[str, Any] = {
        "project_id": str(finding.get("project_id") or ""),
        "$or": [{"url": source_url}, {"source_url": source_url}],
    }
    target_id = str(finding.get("target_id") or "")
    if target_id:
        query["target_id"] = target_id
    return await db[FINDINGS_COLLECTION].find(
        query,
        {
            "_id": 0,
            "finding_id": 1,
            "type": 1,
            "channel": 1,
            "label": 1,
            "value": 1,
            "context": 1,
            "evidence": 1,
            "attention_score": 1,
            "party_name": 1,
        },
    ).sort("attention_score", -1).limit(50).to_list(50)


async def _resolve_input(
    db: AsyncIOMotorDatabase,
    finding_id: str,
) -> _ResolvedInput | None:
    finding = await findings_dao.get_finding(db, finding_id)
    if not finding:
        return None
    project_id = str(finding.get("project_id") or "")
    target_id = str(finding.get("target_id") or "")
    source_url = _source_url(finding)
    document_ids, version_ids = _source_references(finding)
    versions, links, document_ids, version_ids = await _resolve_source_versions(
        db,
        project_id=project_id,
        target_id=target_id,
        document_ids=document_ids,
        version_ids=version_ids,
    )
    target = await targets_dao.get_target(db, target_id) if target_id else None
    project_target = (
        await targets_dao.get_project_target(
            db,
            project_id=project_id,
            target_id=target_id,
        )
        if project_id and target_id
        else None
    )
    browser_scan, siblings = await asyncio.gather(
        _browser_scan_snapshot(
            db,
            project_id=project_id,
            target_id=target_id,
            source_url=source_url,
        ),
        _sibling_findings(db, finding=finding, source_url=source_url),
    )

    allowed_refs = {f"finding:{finding_id}"}
    image_candidates: list[_ImageCandidate] = []
    source_sections: list[dict[str, Any]] = []
    remaining_text = _MAX_SOURCE_TEXT
    for version in versions:
        version_id = str(version.get("version_id") or "")
        source_ref = f"source:{version_id}"
        allowed_refs.add(source_ref)
        content = dict(version.get("content") or {})
        raw_text = str(content.get("text") or "")
        bounded_text = raw_text[:remaining_text]
        remaining_text = max(0, remaining_text - len(bounded_text))
        source_sections.append(
            {
                "evidence_ref": source_ref,
                "identity": version.get("identity") or {},
                "content": {
                    "summary": content.get("summary") or "",
                    "text": bounded_text,
                    "text_length": content.get("text_length") or len(raw_text),
                },
                "contacts": version.get("contacts") or [],
                "source_analysis": version.get("analysis") or {},
                "image_analysis": version.get("image_analysis") or [],
                "archive_status": version.get("archive_status") or "",
            }
        )
        analysis_by_index = {
            int(item.get("index", -1)): item
            for item in version.get("image_analysis") or []
            if isinstance(item, dict)
        }
        for image in version.get("images") or []:
            if not isinstance(image, dict):
                continue
            analysis = dict(image.get("analysis") or {}) or analysis_by_index.get(
                int(image.get("index", -1)),
                {},
            )
            _append_image_candidate(
                image_candidates,
                object_id=str(image.get("storage_object_id") or ""),
                kind="source_image",
                analysis=analysis,
                priority=30,
            )
        for screenshot in version.get("screenshots") or []:
            if not isinstance(screenshot, dict):
                continue
            _append_image_candidate(
                image_candidates,
                object_id=str(screenshot.get("storage_object_id") or ""),
                kind="source_screenshot",
                priority=75,
            )

    link_sections: list[dict[str, Any]] = []
    for link in links:
        link_ref = f"analysis:{str(link.get('link_id') or '')}"
        allowed_refs.add(link_ref)
        link_sections.append(
            {
                "evidence_ref": link_ref,
                "keyword": link.get("keywords") or [],
                "discovery_context": link.get("latest_discovery_context") or {},
                "target_analysis": link.get("latest_analysis") or {},
                "score": link.get("latest_score"),
                "subject_match": link.get("latest_subject_match"),
            }
        )

    browser_section: dict[str, Any] = {}
    if browser_scan:
        browser_ref = f"source:url_scan:{str(browser_scan.get('result_id') or 'latest')}"
        allowed_refs.add(browser_ref)
        browser_section = {
            "evidence_ref": browser_ref,
            "url": browser_scan.get("url") or source_url,
            "intro": browser_scan.get("intro") or {},
            "classification": browser_scan.get("classification") or "",
            "sibling_findings": siblings,
        }
        _append_image_candidate(
            image_candidates,
            object_id=str(
                browser_scan.get("screenshot_object_id")
                or browser_scan.get("screenshot_url")
                or ""
            ),
            kind="browser_screenshot",
            priority=80,
        )
    _append_image_candidate(
        image_candidates,
        object_id=str(
            finding.get("screenshot_object_id")
            or finding.get("screenshot_url")
            or ""
        ),
        kind="finding_screenshot",
        priority=85,
    )
    for candidate in image_candidates:
        allowed_refs.add(candidate.evidence_ref)

    prompt = load_prompt(PROMPT_SLUG)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    input_payload = {
        "schema_version": _SCHEMA_VERSION,
        "finding": _finding_payload(finding),
        "target": {
            "target_id": target_id,
            "canonical_name": (target or {}).get("canonical_name") or "",
            "aliases": (target or {}).get("aliases") or [],
            "project_relation": project_target or {},
        },
        "source_documents": source_sections,
        "target_analyses": link_sections,
        "browser_capture": browser_section,
        "allowed_evidence_refs": sorted(allowed_refs),
    }
    fingerprint_payload = {
        "schema_version": _SCHEMA_VERSION,
        "prompt_hash": prompt_hash,
        "finding": _finding_payload(finding),
        "versions": [
            {
                "version_id": item.get("version_id"),
                "content_hash": item.get("content_hash"),
                "updated_at": item.get("updated_at"),
            }
            for item in versions
        ],
        "links": [
            {
                "link_id": item.get("link_id"),
                "analysis_fingerprint": item.get("analysis_fingerprint"),
                "updated_at": item.get("updated_at"),
            }
            for item in links
        ],
        "browser": browser_scan,
        "images": [item.object_id for item in image_candidates],
    }
    input_fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    text = (
        "请整理下面这个 Finding 的完整上下文。证据引用只能从 "
        "allowed_evidence_refs 中选择。\n\n"
        + json.dumps(input_payload, ensure_ascii=False, default=str)
    )
    return _ResolvedInput(
        finding=finding,
        text=text,
        allowed_refs=allowed_refs,
        image_candidates=sorted(
            image_candidates,
            key=lambda item: item.priority,
            reverse=True,
        )[:_MAX_IMAGES],
        source_document_ids=document_ids,
        source_document_version_ids=version_ids,
        input_fingerprint=input_fingerprint,
        prompt_hash=prompt_hash,
        source_url=source_url,
    )


def _encode_image(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as source:
        frame = ImageOps.exif_transpose(source)
        if frame.mode in {"RGBA", "LA"} or (
            frame.mode == "P" and "transparency" in frame.info
        ):
            rgba = frame.convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            background.alpha_composite(rgba)
            frame = background.convert("RGB")
        else:
            frame = frame.convert("RGB")
        frame.thumbnail((2_000, 2_000))
        output = io.BytesIO()
        frame.save(output, format="JPEG", quality=88, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode(
        "ascii"
    )


async def _load_images(
    candidates: list[_ImageCandidate],
) -> tuple[list[FindingContextImage], list[dict[str, Any]], list[str]]:
    if not candidates:
        return [], [], []
    storage = await get_object_storage()

    async def load(candidate: _ImageCandidate):
        try:
            data = await storage.get_bytes(candidate.object_id)
            data_url = await asyncio.to_thread(_encode_image, data)
            return candidate, data_url, ""
        except Exception as exc:  # noqa: BLE001
            return candidate, "", f"{candidate.object_id}: {exc}"

    loaded = await asyncio.gather(*(load(item) for item in candidates))
    images: list[FindingContextImage] = []
    manifest: list[dict[str, Any]] = []
    errors: list[str] = []
    for candidate, data_url, error in loaded:
        manifest.append(
            {
                "storage_object_id": candidate.object_id,
                "evidence_ref": candidate.evidence_ref,
                "kind": candidate.kind,
                "description": candidate.description,
                "visible_text": candidate.visible_text,
                "relevance": candidate.relevance,
                "loaded_for_agent": bool(data_url),
            }
        )
        if data_url:
            images.append(
                FindingContextImage(
                    evidence_ref=candidate.evidence_ref,
                    data_url=data_url,
                )
            )
        if error:
            errors.append(error[:1_000])
    return images, manifest, errors


def _sanitize_refs(values: list[str], allowed: set[str]) -> list[str]:
    return _unique(
        [
            str(value or "").strip()
            for value in values
            if str(value or "").strip() in allowed
        ]
    )


def sanitize_agent_result(
    result: FindingContextResult,
    *,
    allowed_refs: set[str],
    fallback_title: str,
    fallback_overview: str,
) -> dict[str, Any]:
    """拒绝 Agent 新造的引用，并为旧数据提供可读降级内容。"""
    payload = result.model_dump()
    payload["title"] = str(payload.get("title") or fallback_title or "Finding 上下文")
    narrative_keys = (
        "overview",
        "target_relationship",
        "source_overview",
        "business_background",
        "event_context",
        "contact_context",
        "finding_interpretation",
    )
    for key in narrative_keys:
        raw = payload.get(key) or {}
        row = dict(raw) if isinstance(raw, dict) else {"text": str(raw or "")}
        row["evidence_refs"] = _sanitize_refs(
            list(row.get("evidence_refs") or []),
            allowed_refs,
        )
        if not row["evidence_refs"] and row.get("text"):
            row["kind"] = "inference"
            row["confidence"] = min(int(row.get("confidence") or 0), 40)
        payload[key] = row
    if not payload["overview"].get("text"):
        payload["overview"] = {
            "text": fallback_overview or "暂无可确认的上下文摘要",
            "kind": "fact",
            "confidence": 60 if fallback_overview else 0,
            "evidence_refs": [
                value for value in sorted(allowed_refs) if value.startswith("finding:")
            ][:1],
        }
    for key in ("parties", "timeline", "key_facts"):
        cleaned = []
        for item in payload.get(key) or []:
            row = dict(item)
            row["evidence_refs"] = _sanitize_refs(
                list(row.get("evidence_refs") or []),
                allowed_refs,
            )
            if row["evidence_refs"]:
                cleaned.append(row)
        payload[key] = cleaned
    visual = []
    for item in payload.get("visual_findings") or []:
        row = dict(item)
        evidence_ref = str(row.get("evidence_ref") or "")
        if evidence_ref in allowed_refs and evidence_ref.startswith("image:"):
            visual.append(row)
    payload["visual_findings"] = visual
    payload["schema_version"] = _SCHEMA_VERSION
    return payload


async def _queue_one(
    db: AsyncIOMotorDatabase,
    finding_id: str,
    *,
    force: bool,
) -> dict[str, Any] | None:
    resolved = await _resolve_input(db, finding_id)
    if not resolved:
        return None
    finding = resolved.finding
    context = await context_dao.queue_context(
        db,
        finding_id=finding_id,
        project_id=str(finding.get("project_id") or ""),
        target_id=str(finding.get("target_id") or ""),
        source=str(finding.get("source") or ""),
        source_url=resolved.source_url,
        source_document_ids=resolved.source_document_ids,
        source_document_version_ids=resolved.source_document_version_ids,
        input_fingerprint=resolved.input_fingerprint,
        prompt_slug=PROMPT_SLUG,
        prompt_hash=resolved.prompt_hash,
        priority=int(finding.get("attention_score") or 0),
        force=force,
    )
    await db[FINDINGS_COLLECTION].update_one(
        {"finding_id": finding_id},
        {
            "$set": {
                "context_id": context.get("context_id"),
                "context_status": context.get("status"),
            }
        },
    )
    return context


async def queue_finding_contexts(
    db: AsyncIOMotorDatabase,
    finding_ids: list[str],
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    """统一排队入口；调用侧不直接接触 Agent 或模型实现。"""
    ids = _unique([str(value or "").strip() for value in finding_ids])
    semaphore = asyncio.Semaphore(8)

    async def queue(finding_id: str):
        async with semaphore:
            return await _queue_one(db, finding_id, force=force)

    rows = await asyncio.gather(*(queue(finding_id) for finding_id in ids))
    contexts = [row for row in rows if row]
    if contexts:
        kick_finding_context_worker(db)
    return contexts


def schedule_finding_contexts(
    db: AsyncIOMotorDatabase,
    finding_ids: list[str],
) -> asyncio.Task[Any] | None:
    """非阻塞排队入口，采集流水线只需表达“整理这些 Finding”。"""
    ids = _unique([str(value or "").strip() for value in finding_ids])
    if not ids:
        return None
    digest = hashlib.sha1("\x1f".join(ids).encode("utf-8")).hexdigest()[:10]
    return spawn_background(
        queue_finding_contexts(db, ids),
        name=f"finding-context-queue:{digest}",
    )


async def get_or_queue_finding_context(
    db: AsyncIOMotorDatabase,
    finding_id: str,
    *,
    force: bool = False,
) -> dict[str, Any] | None:
    existing = await context_dao.get_by_finding_id(db, finding_id)
    if (
        existing
        and existing.get("status") in {"pending", "running"}
        and not force
    ):
        kick_finding_context_worker(db)
        return existing
    contexts = await queue_finding_contexts(db, [finding_id], force=force)
    if contexts:
        return contexts[0]
    return await context_dao.get_by_finding_id(db, finding_id)


async def _process_claimed(
    db: AsyncIOMotorDatabase,
    context: dict[str, Any],
) -> None:
    context_id = str(context.get("context_id") or "")
    finding_id = str(context.get("finding_id") or "")
    try:
        resolved = await _resolve_input(db, finding_id)
        if not resolved:
            raise ValueError("Finding 已不存在")
        await db[FINDINGS_COLLECTION].update_one(
            {"finding_id": finding_id},
            {"$set": {"context_id": context_id, "context_status": "running"}},
        )
        images, image_manifest, image_errors = await _load_images(
            resolved.image_candidates
        )
        finding = resolved.finding
        task_id = str(
            finding.get("task_id")
            or finding.get("latest_task_id")
            or ""
        )
        response = await create_finding_context_agent().organize(
            FindingContextAgentRequest(
                finding_id=finding_id,
                project_id=str(finding.get("project_id") or ""),
                task_id=task_id,
                text=resolved.text,
                images=images,
            )
        )
        result = sanitize_agent_result(
            response.result,
            allowed_refs=resolved.allowed_refs,
            fallback_title=str(finding.get("label") or finding.get("value") or ""),
            fallback_overview=str(
                finding.get("context")
                or finding.get("summary")
                or finding.get("attention_reason")
                or ""
            ),
        )
        saved = await context_dao.mark_completed(
            db,
            context_id=context_id,
            input_fingerprint=resolved.input_fingerprint,
            result=result,
            model=response.model,
            evidence_manifest={
                "source_document_ids": resolved.source_document_ids,
                "source_document_version_ids": resolved.source_document_version_ids,
                "images": image_manifest,
                "image_errors": image_errors,
                "allowed_evidence_refs": sorted(resolved.allowed_refs),
            },
        )
        await db[FINDINGS_COLLECTION].update_one(
            {"finding_id": finding_id},
            {
                "$set": {
                    "context_id": context_id,
                    "context_status": (saved or {}).get("status") or "completed",
                }
            },
        )
    except Exception as exc:  # noqa: BLE001
        attempts = int(context.get("attempt_count") or 1)
        retry = attempts < 2
        saved = await context_dao.mark_error(
            db,
            context_id=context_id,
            error=str(exc),
            retry=retry,
        )
        await db[FINDINGS_COLLECTION].update_one(
            {"finding_id": finding_id},
            {
                "$set": {
                    "context_id": context_id,
                    "context_status": (saved or {}).get("status") or "error",
                }
            },
        )
        logger.warning(
            "Finding 上下文整理失败 finding_id=%s attempt=%s retry=%s error=%s",
            finding_id,
            attempts,
            retry,
            exc,
        )


async def _worker_loop(db: AsyncIOMotorDatabase) -> None:
    while True:
        context = await context_dao.claim_next_pending(db)
        if not context:
            return
        await _process_claimed(db, context)


async def _drain_pending(db: AsyncIOMotorDatabase) -> None:
    global _worker_rerun_requested
    while True:
        _worker_rerun_requested = False
        await asyncio.gather(*(_worker_loop(db) for _ in range(_WORKER_COUNT)))
        if not _worker_rerun_requested:
            return


def kick_finding_context_worker(db: AsyncIOMotorDatabase) -> asyncio.Task[Any]:
    global _worker_rerun_requested, _worker_task
    if _worker_task is not None and not _worker_task.done():
        _worker_rerun_requested = True
        return _worker_task

    _worker_rerun_requested = False
    task = spawn_background(
        _drain_pending(db),
        name="finding-context-worker",
    )
    _worker_task = task

    def restart_if_needed(done: asyncio.Task[Any]) -> None:
        global _worker_task
        if _worker_task is done:
            _worker_task = None
        if _worker_rerun_requested and not done.cancelled():
            kick_finding_context_worker(db)

    task.add_done_callback(restart_if_needed)
    return _worker_task
