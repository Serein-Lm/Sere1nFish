"""Resumable discovery and archival of official website documents."""
from __future__ import annotations

import asyncio
import itertools
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

import aiohttp
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import findings as findings_dao
from api.dao import website_crawl as crawl_dao
from api.services.company_url import normalize_url
from api.services.site_relevance import classify_candidate_surface
from api.services.source_documents.resources import (
    create_public_fetch_ssl_context,
    fetch_resource_with_retry,
    html_text_and_links,
    is_attachment_link,
)
from api.services.source_documents.service import ingest_source_url
from api.services.source_documents.urls import canonicalize_source_url
from api.services.task_progress import update_source_progress
from core.logger import get_logger
from core.mobile.collect.contacts import build_contact_findings


logger = get_logger("website_documents")

_MAX_HTML_BYTES = 8 * 1024 * 1024
_DISCOVERY_POLICY_VERSION = 5
_DETAIL_DOCUMENT_PATH_RE = re.compile(
    r"(?:^|[/_.-])detail(?:[/_.-]|$)",
    re.I,
)
_DOCUMENT_PATH_RE = re.compile(
    r"/(?:19|20)\d{2}(?:\d{2})?/t(?:19|20)\d{6}_[^/]+\.s?html?$",
    re.I,
)
_DATE_DOCUMENT_RE = re.compile(
    r"/(?:19|20)\d{2}[/_-]\d{1,2}[/_-]\d{1,2}/[^/]+\.s?html?$",
    re.I,
)
_PAGINATION_PATH_PATTERNS = (
    re.compile(r"(?:^|/)(?:index|list)[_-]?\d+\.s?html?$", re.I),
    re.compile(r"(?:^|/)(?:page|list|index)[/_-]\d+(?:\.s?html?)?/?$", re.I),
)
_PAGINATION_QUERY_KEYS = {
    "current",
    "currentpage",
    "offset",
    "p",
    "page",
    "pageindex",
    "pageno",
    "pagenum",
    "start",
}
_PAGINATION_LABELS = {
    "下一页",
    "下页",
    "后页",
    "末页",
    "尾页",
    "next",
    "nextpage",
    ">",
    ">>",
    "»",
}
_DOCUMENT_HINTS = (
    "公告",
    "通知",
    "公示",
    "招聘",
    "采购",
    "招标",
    "中标",
    "成交",
    "报名",
    "申报",
    "下载",
    "附件",
    "联系",
    "人才",
    "人事",
    "征集",
    "遴选",
    "评审",
    "名单",
    "项目",
    "课题",
    "研究",
    "学术",
    "专家",
    "导师",
    "政策",
    "科研",
    "合作",
    "会议",
    "培训",
    "标准",
    "指南",
    "成果",
    "动态",
    "要闻",
    "工作",
    "竞价",
    "比选",
    "咨询",
    "信息公开",
)
_SKIP_EXTENSIONS = {
    ".css",
    ".js",
    ".map",
    ".ico",
    ".svg",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
}


@dataclass(slots=True)
class WebsiteCollectionPolicy:
    max_pages: int = 1_200
    max_documents: int = 400
    max_depth: int = 5
    discovery_concurrency: int = 12
    archive_concurrency: int = 4
    request_timeout_seconds: int = 120
    max_page_attempts: int = 3
    broad_official_discovery: bool = False

    def bounded(self) -> "WebsiteCollectionPolicy":
        return WebsiteCollectionPolicy(
            max_pages=max(20, min(int(self.max_pages), 10_000)),
            max_documents=max(10, min(int(self.max_documents), 3_000)),
            max_depth=max(1, min(int(self.max_depth), 10)),
            discovery_concurrency=max(1, min(int(self.discovery_concurrency), 48)),
            archive_concurrency=max(1, min(int(self.archive_concurrency), 16)),
            request_timeout_seconds=max(
                30,
                min(int(self.request_timeout_seconds), 600),
            ),
            max_page_attempts=max(1, min(int(self.max_page_attempts), 6)),
            broad_official_discovery=bool(self.broad_official_discovery),
        )


def resolve_website_collection_policy(
    tuning: Any,
    *,
    mode: str = "standard",
) -> WebsiteCollectionPolicy:
    """Map the task-level collection intent to one bounded crawl policy."""
    normalized_mode = str(mode or "standard").strip().casefold()
    if normalized_mode not in {"standard", "deep"}:
        raise ValueError("官网归档模式必须为 standard 或 deep")
    policy = WebsiteCollectionPolicy(
        max_pages=int(tuning.website_crawl_max_pages),
        max_documents=int(tuning.website_crawl_max_documents),
        max_depth=int(tuning.website_crawl_max_depth),
        discovery_concurrency=int(tuning.website_crawl_concurrency),
        archive_concurrency=int(tuning.website_archive_concurrency),
    )
    if normalized_mode == "deep":
        policy = WebsiteCollectionPolicy(
            max_pages=max(policy.max_pages, 6_000),
            max_documents=max(policy.max_documents, 3_000),
            max_depth=max(policy.max_depth, 8),
            discovery_concurrency=max(policy.discovery_concurrency, 24),
            archive_concurrency=max(policy.archive_concurrency, 8),
            request_timeout_seconds=policy.request_timeout_seconds,
            max_page_attempts=policy.max_page_attempts,
            broad_official_discovery=True,
        )
    return policy.bounded()


def _host_in_roots(host: str, root_domains: list[str]) -> bool:
    normalized = str(host or "").casefold().strip(".")
    return any(
        normalized == root or normalized.endswith("." + root)
        for root in root_domains
    )


def normalize_required_path_segments(values: Any) -> list[str]:
    """Normalize exact URL path segments used to isolate shared official sites."""
    if values is None:
        return []
    if isinstance(values, str):
        raw_values = [values]
    elif isinstance(values, (list, tuple, set)):
        raw_values = list(values)
    else:
        raise ValueError("官网路径范围必须为字符串列表")

    normalized: list[str] = []
    for raw in raw_values:
        segment = unquote(str(raw or "")).strip().strip("/").casefold()
        if not segment:
            continue
        if (
            len(segment) > 128
            or "/" in segment
            or "?" in segment
            or "#" in segment
            or segment in {".", ".."}
        ):
            raise ValueError(f"无效的官网路径段: {raw}")
        if segment not in normalized:
            normalized.append(segment)
    if len(normalized) > 32:
        raise ValueError("官网路径范围最多允许 32 个路径段")
    return normalized


def url_matches_required_path_segments(
    url: str,
    required_path_segments: list[str] | tuple[str, ...] | set[str] | str | None,
) -> bool:
    """Match complete path segments so similar province codes cannot overlap."""
    required = normalize_required_path_segments(required_path_segments)
    if not required:
        return True
    try:
        path_segments = {
            unquote(segment).strip().casefold()
            for segment in urlsplit(str(url or "")).path.split("/")
            if unquote(segment).strip()
        }
    except (TypeError, ValueError):
        return False
    return any(segment in path_segments for segment in required)


def select_official_seed_urls(
    *,
    fallback_urls: list[str],
    known_alive_urls: list[str],
    root_domains: list[str],
    required_path_segments: list[str] | None = None,
) -> list[str]:
    """Prefer already-probed primary website origins over guessed apex URLs."""
    roots = {
        str(value or "").casefold().strip(".").removeprefix("www.")
        for value in root_domains
        if str(value or "").strip()
    }
    required_segments = normalize_required_path_segments(required_path_segments)

    def _normalized_scoped(values: list[str]) -> list[str]:
        candidates: list[tuple[tuple[int, int, str], str]] = []
        seen: set[str] = set()
        for raw in values:
            normalized = normalize_url(raw)
            if not normalized:
                continue
            try:
                canonical = canonicalize_source_url(normalized)
                parsed = urlsplit(canonical)
                host = str(parsed.hostname or "").casefold().strip(".")
                port = parsed.port
            except (TypeError, ValueError):
                continue
            if (
                not _host_in_roots(host, list(roots))
                or port not in {None, 80, 443}
                or parsed.query
                or not url_matches_required_path_segments(
                    canonical,
                    required_segments,
                )
                or canonical in seen
            ):
                continue
            seen.add(canonical)
            rank = (
                0 if parsed.scheme == "https" else 1,
                0 if host.startswith("www.") else 1,
                canonical,
            )
            candidates.append((rank, canonical))
        return [url for _rank, url in sorted(candidates)]

    def _normalized_primary(values: list[str]) -> list[str]:
        candidates: list[tuple[tuple[int, int, str], str]] = []
        seen: set[str] = set()
        for raw in values:
            normalized = normalize_url(raw)
            if not normalized:
                continue
            try:
                canonical = canonicalize_source_url(normalized)
                parsed = urlsplit(canonical)
                host = str(parsed.hostname or "").casefold().strip(".")
                port = parsed.port
            except (TypeError, ValueError):
                continue
            primary_root = host.removeprefix("www.")
            if (
                primary_root not in roots
                or host not in {primary_root, f"www.{primary_root}"}
                or port not in {None, 80, 443}
                or (parsed.path or "/") != "/"
                or parsed.query
                or canonical in seen
            ):
                continue
            seen.add(canonical)
            rank = (
                0 if parsed.scheme == "https" else 1,
                0 if host.startswith("www.") else 1,
                canonical,
            )
            candidates.append((rank, canonical))
        return [url for _rank, url in sorted(candidates)]

    if required_segments:
        scoped_alive = _normalized_scoped(known_alive_urls)
        scoped_fallback = _normalized_scoped(fallback_urls)
        return list(dict.fromkeys([*scoped_alive, *scoped_fallback]))

    alive_primary = _normalized_primary(known_alive_urls)
    if alive_primary:
        return alive_primary
    return _normalized_primary(fallback_urls)


def _is_pagination_link(url: str, label: str = "") -> bool:
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if any(pattern.search(path) for pattern in _PAGINATION_PATH_PATTERNS):
        return True
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        normalized_key = re.sub(r"[^a-z]", "", key.casefold())
        if normalized_key in _PAGINATION_QUERY_KEYS and re.fullmatch(
            r"\d+", value.strip()
        ):
            return True
    normalized_label = re.sub(r"\s+", "", str(label or "")).casefold()
    return normalized_label in _PAGINATION_LABELS


def _is_document_url(url: str, label: str = "") -> bool:
    path = urlsplit(url).path
    if _DETAIL_DOCUMENT_PATH_RE.search(path):
        return True
    if _is_pagination_link(url, label):
        return False
    if _DOCUMENT_PATH_RE.search(path) or _DATE_DOCUMENT_RE.search(path):
        return True
    filename = PurePosixPath(path).name.casefold()
    return bool(
        filename.endswith((".html", ".htm", ".shtml"))
        and filename not in {"index.html", "index.htm", "index.shtml"}
        and not filename.startswith("index_")
    )


def _link_kind(url: str, label: str = "") -> str:
    if is_attachment_link({"url": url, "label": label}):
        return "attachment"
    suffix = PurePosixPath(urlsplit(url).path).suffix.casefold()
    if suffix in _SKIP_EXTENSIONS:
        return "asset"
    return "document" if _is_document_url(url, label) else "index"


def _has_hint(*values: str) -> bool:
    combined = " ".join(str(value or "") for value in values).casefold()
    return any(hint.casefold() in combined for hint in _DOCUMENT_HINTS)


def classify_discovered_link(
    *,
    url: str,
    label: str,
    parent_url: str,
    parent_relevant: bool,
    depth: int,
    max_depth: int,
    broad_official_discovery: bool = False,
) -> dict[str, Any] | None:
    """Return a crawl candidate only when it belongs to a document-rich scope."""
    if depth > max_depth:
        return None
    if classify_candidate_surface(url=url, title=label):
        return None
    kind = _link_kind(url, label)
    if kind == "asset":
        return None
    path = urlsplit(url).path
    parent_parts = urlsplit(parent_url)
    parent_path = parent_parts.path or "/"
    parent_scope = (
        parent_path
        if parent_path.endswith("/")
        else str(PurePosixPath(parent_path).parent).rstrip("/") + "/"
    )
    same_scope = bool(
        urlsplit(url).hostname == parent_parts.hostname
        and path.startswith(parent_scope)
    )
    pagination = _is_pagination_link(url, label)
    hinted = _has_hint(url, label)
    if kind == "attachment":
        relevant = broad_official_discovery or hinted or (
            parent_relevant and same_scope
        )
        kind = "document"
    elif kind == "document":
        relevant = broad_official_discovery or hinted or (
            parent_relevant and same_scope
        )
    else:
        relevant = broad_official_discovery or hinted or (
            parent_relevant and pagination
        )
    if not relevant:
        return None
    priority = 100 if kind == "document" else 80 if pagination else 70
    if hinted:
        priority += 20
    return {
        "canonical_url": canonicalize_source_url(url),
        "parent_url": parent_url,
        "anchor_text": label,
        "kind": kind,
        "scope_relevant": relevant,
        "depth": depth,
        "priority": priority,
    }


def archive_page_status(source_result: dict[str, Any]) -> str:
    if source_result.get("rejected"):
        return "rejected"
    if (
        source_result.get("archive_status") == "partial"
        or source_result.get("contact_attribution_error")
    ):
        return "partial"
    return "archived"


class WebsiteDocumentCollectionService:
    """Discover listing pagination and archive official documents as evidence."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        *,
        policy: WebsiteCollectionPolicy | None = None,
    ) -> None:
        self.db = db
        self.policy = (policy or WebsiteCollectionPolicy()).bounded()

    async def run(
        self,
        *,
        parent_task_id: str,
        project_id: str,
        target: dict[str, Any],
        seed_urls: list[str],
        known_alive_urls: list[str] | None = None,
        required_path_segments: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        target_id = str(target.get("target_id") or "").strip()
        target_name = str(target.get("canonical_name") or "").strip()
        root_domains = list(
            dict.fromkeys(
                str(value or "").casefold().strip().removeprefix("www.")
                for value in target.get("root_domains") or []
                if str(value or "").strip()
            )
        )
        if not root_domains:
            root = str(target.get("root_domain") or "").casefold().strip()
            if root:
                root_domains = [root.removeprefix("www.")]
        required_segments = normalize_required_path_segments(
            required_path_segments
        )
        seeds = select_official_seed_urls(
            fallback_urls=seed_urls,
            known_alive_urls=list(known_alive_urls or []),
            root_domains=root_domains,
            required_path_segments=required_segments,
        )
        if not target_id or not project_id or not root_domains or not seeds:
            return {
                "enabled": False,
                "status": "skipped",
                "error": "官网文档采集缺少 project_id、target_id、根域名或入口 URL",
            }

        crawl_task_id = f"{parent_task_id}_webdocs"
        config = {
            "discovery_policy_version": _DISCOVERY_POLICY_VERSION,
            "max_pages": self.policy.max_pages,
            "max_documents": self.policy.max_documents,
            "max_depth": self.policy.max_depth,
            "discovery_concurrency": self.policy.discovery_concurrency,
            "archive_concurrency": self.policy.archive_concurrency,
            "request_timeout_seconds": self.policy.request_timeout_seconds,
            "max_page_attempts": self.policy.max_page_attempts,
            "broad_official_discovery": self.policy.broad_official_discovery,
            "required_path_segments": required_segments,
        }
        if not dry_run:
            await crawl_dao.begin_task(
                self.db,
                crawl_task_id=crawl_task_id,
                parent_task_id=parent_task_id,
                project_id=project_id,
                target_id=target_id,
                target_name=target_name,
                seeds=seeds,
                root_domains=root_domains,
                config=config,
            )

        queue: asyncio.PriorityQueue[tuple[int, int, dict[str, Any]]] = (
            asyncio.PriorityQueue()
        )
        sequence = itertools.count()
        seen: set[str] = set()
        reservations: set[str] = set()
        reserved_documents = 0
        state_lock = asyncio.Lock()
        counters: dict[str, int] = {
            "total_pages": 0,
            "processed_pages": 0,
            "listing_pages": 0,
            "documents_scheduled": 0,
            "documents_archived": 0,
            "documents_partial": 0,
            "documents_rejected": 0,
            "failed_pages": 0,
            "attachments_archived": 0,
            "contacts_found": 0,
            "findings_upserted": 0,
            "rendered_discovery_pages": 0,
            "rendered_discovery_failures": 0,
        }
        preview: list[dict[str, Any]] = []
        truncated = False

        async def _queue_items(items: list[dict[str, Any]]) -> None:
            nonlocal reserved_documents, truncated
            accepted: list[dict[str, Any]] = []
            async with state_lock:
                pending_urls: set[str] = set()
                pending_documents = 0
                reached_limit = False
                for item in items:
                    url = str(item.get("canonical_url") or "")
                    if (
                        not url
                        or url in seen
                        or url in reservations
                        or url in pending_urls
                    ):
                        continue
                    if len(seen) + len(reservations) >= self.policy.max_pages:
                        reached_limit = True
                        break
                    if (
                        item.get("kind") == "document"
                        and counters["documents_scheduled"]
                        + reserved_documents
                        + pending_documents
                        >= self.policy.max_documents
                    ):
                        reached_limit = True
                        continue
                    pending_urls.add(url)
                    if item.get("kind") == "document":
                        pending_documents += 1
                    accepted.append(item)
                if not accepted:
                    truncated = truncated or reached_limit
                    return
                reservations.update(pending_urls)
                reserved_documents += pending_documents
            try:
                if dry_run:
                    inserted = accepted
                else:
                    inserted_ids = set(
                        await crawl_dao.enqueue_pages(
                            self.db,
                            crawl_task_id=crawl_task_id,
                            project_id=project_id,
                            target_id=target_id,
                            pages=accepted,
                        )
                    )
                    inserted = [
                        item
                        for item in accepted
                        if crawl_dao.page_id_for_url(
                            crawl_task_id,
                            item["canonical_url"],
                        )
                        in inserted_ids
                    ]
            except BaseException:
                async with state_lock:
                    reservations.difference_update(pending_urls)
                    reserved_documents -= pending_documents
                raise
            async with state_lock:
                reservations.difference_update(pending_urls)
                reserved_documents -= pending_documents
                seen.update(pending_urls)
                counters["documents_scheduled"] += pending_documents
                counters["total_pages"] = len(seen)
                truncated = truncated or reached_limit
            for item in inserted:
                item = dict(item)
                item["page_id"] = crawl_dao.page_id_for_url(
                    crawl_task_id,
                    item["canonical_url"],
                )
                await queue.put((-int(item.get("priority") or 0), next(sequence), item))

        if dry_run:
            await _queue_items(
                [
                    {
                        "canonical_url": seed,
                        "parent_url": "",
                        "anchor_text": target_name,
                        "kind": "index",
                        "scope_relevant": False,
                        "depth": 0,
                        "priority": 60,
                    }
                    for seed in seeds
                ]
            )
        else:
            existing = await crawl_dao.list_pages(
                self.db,
                crawl_task_id=crawl_task_id,
            )
            seen.update(str(item.get("canonical_url") or "") for item in existing)
            counters["documents_scheduled"] = sum(
                str(item.get("kind") or "") == "document" for item in existing
            )
            counters["total_pages"] = len(seen)
            counters["processed_pages"] = sum(
                str(item.get("status") or "")
                in {
                    "discovered",
                    "archived",
                    "partial",
                    "rejected",
                    "error",
                    "superseded",
                }
                for item in existing
            )
            counters["listing_pages"] = sum(
                str(item.get("status") or "") == "discovered"
                for item in existing
            )
            counters["documents_archived"] = sum(
                str(item.get("status") or "") in {"archived", "partial"}
                for item in existing
            )
            counters["documents_partial"] = sum(
                str(item.get("status") or "") == "partial"
                for item in existing
            )
            counters["documents_rejected"] = sum(
                str(item.get("status") or "") == "rejected"
                for item in existing
            )
            counters["failed_pages"] = sum(
                str(item.get("status") or "") == "error"
                for item in existing
            )
            counters["attachments_archived"] = sum(
                int(item.get("attachment_count") or 0) for item in existing
            )
            counters["contacts_found"] = sum(
                int(item.get("contact_count") or 0) for item in existing
            )
            counters["findings_upserted"] = sum(
                int(item.get("finding_count") or 0) for item in existing
            )
            pending = [item for item in existing if item.get("status") == "pending"]
            for item in pending:
                await queue.put(
                    (-int(item.get("priority") or 0), next(sequence), item)
                )
            await _queue_items(
                [
                    {
                        "canonical_url": seed,
                        "parent_url": "",
                        "anchor_text": target_name,
                        "kind": "index",
                        "scope_relevant": False,
                        "depth": 0,
                        "priority": 60,
                    }
                    for seed in seeds
                ]
            )

        timeout = aiohttp.ClientTimeout(
            total=self.policy.request_timeout_seconds,
            connect=min(30, self.policy.request_timeout_seconds),
            sock_read=max(30, self.policy.request_timeout_seconds - 10),
        )
        connector = aiohttp.TCPConnector(
            limit=self.policy.discovery_concurrency * 2,
            ttl_dns_cache=180,
            ssl=create_public_fetch_ssl_context(),
        )
        archive_semaphore = asyncio.Semaphore(self.policy.archive_concurrency)
        rendered_discovery_semaphore = asyncio.Semaphore(
            max(1, min(self.policy.archive_concurrency, 4))
        )

        async def _record_progress() -> None:
            if dry_run:
                return
            await crawl_dao.heartbeat_task(
                self.db,
                crawl_task_id=crawl_task_id,
                counters=dict(counters),
            )
            await update_source_progress(
                self.db,
                task_id=parent_task_id,
                source="website_documents",
                status="running",
                processed=counters["processed_pages"],
                total=counters["total_pages"],
                succeeded=counters["documents_archived"],
                failed=counters["failed_pages"],
                message=(
                    "官网文档已归档 "
                    f"{counters['documents_archived']}/{counters['documents_scheduled']}，"
                    f"附件 {counters['attachments_archived']}"
                ),
            )

        async def _archive(item: dict[str, Any]) -> None:
            page_id = str(item["page_id"])
            if not dry_run:
                await crawl_dao.mark_page_started(
                    self.db,
                    page_id=page_id,
                    status="archiving",
                )
            async with archive_semaphore:
                source_result = await ingest_source_url(
                    self.db,
                    url=item["canonical_url"],
                    project_id=project_id,
                    target=target,
                    task_def_id=parent_task_id,
                    run_task_id=crawl_task_id,
                    keyword=str(item.get("anchor_text") or "官网公告"),
                    discovery_context={
                        "source": "official_website_crawl",
                        "parent_url": item.get("parent_url") or "",
                        "anchor_text": item.get("anchor_text") or "",
                        "depth": int(item.get("depth") or 0),
                    },
                    persist=not dry_run,
                    min_subject_match=80,
                    analysis_mode="trusted_official",
                )
            status = archive_page_status(source_result)
            attribution_error = str(
                source_result.get("contact_attribution_error") or ""
            )
            if status == "rejected":
                counters["documents_rejected"] += 1
            else:
                counters["documents_archived"] += 1
                if status == "partial":
                    counters["documents_partial"] += 1
            attachments = int(source_result.get("attachment_count") or 0)
            contacts = list(source_result.get("contacts") or [])
            counters["attachments_archived"] += attachments
            counters["contacts_found"] += len(contacts)
            if dry_run:
                preview.append(source_result)
                return
            findings = build_contact_findings(
                project_id=project_id,
                task_id=crawl_task_id,
                record={
                    "record_id": f"website:{source_result.get('document_id') or page_id}",
                    "fields": source_result.get("fields") or {},
                    "score": int(source_result.get("score") or 60),
                    "source_url": source_result.get("source_url") or item["canonical_url"],
                    "keyword": item.get("anchor_text") or "官网公告",
                    "target_id": target_id,
                    "target_name": target_name,
                    "source_document_id": source_result.get("document_id") or "",
                    "source_document_version_id": source_result.get("version_id") or "",
                    "source_type": "official_website_document",
                },
                contacts=contacts,
            )
            await findings_dao.upsert_contact_findings_batch(self.db, findings)
            if not attribution_error:
                await findings_dao.reconcile_contact_findings_for_record(
                    self.db,
                    project_id=project_id,
                    record_id=(
                        f"website:{source_result.get('document_id') or page_id}"
                    ),
                    keep_finding_ids=[
                        str(finding.get("finding_id") or "")
                        for finding in findings
                    ],
                )
            counters["findings_upserted"] += len(findings)
            await crawl_dao.mark_page_terminal(
                self.db,
                page_id=page_id,
                status=status,
                fields={
                    "document_id": source_result.get("document_id") or "",
                    "version_id": source_result.get("version_id") or "",
                    "archive_status": source_result.get("archive_status") or "unknown",
                    "attachment_count": attachments,
                    "contact_count": len(contacts),
                    "finding_count": len(findings),
                    "kind": "document",
                    "contact_attribution_error": attribution_error,
                },
            )

        async def _discover(
            session: aiohttp.ClientSession,
            item: dict[str, Any],
        ) -> None:
            page_id = str(item["page_id"])
            if not dry_run:
                await crawl_dao.mark_page_started(
                    self.db,
                    page_id=page_id,
                    status="fetching",
                )
            fetched = await fetch_resource_with_retry(
                session,
                item["canonical_url"],
                max_bytes=_MAX_HTML_BYTES,
            )
            content_type = fetched.content_type.casefold()
            if "html" not in content_type and not fetched.data.lstrip().startswith(
                (b"<!DOCTYPE", b"<html", b"<HTML")
            ):
                raise RuntimeError(f"目录入口不是 HTML: {fetched.content_type}")
            text, links = html_text_and_links(
                fetched.data,
                fetched.url,
                content_type=fetched.content_type,
            )
            content_length = len(text)
            discovery_mode = "static"

            def _classify_links(
                discovered_links: list[dict[str, Any]],
                *,
                parent_url: str,
            ) -> list[dict[str, Any]]:
                classified: list[dict[str, Any]] = []
                for link in discovered_links:
                    try:
                        canonical = canonicalize_source_url(link["url"])
                        host = str(urlsplit(canonical).hostname or "")
                    except (KeyError, TypeError, ValueError):
                        continue
                    if not _host_in_roots(host, root_domains):
                        continue
                    if not url_matches_required_path_segments(
                        canonical,
                        required_segments,
                    ):
                        continue
                    candidate = classify_discovered_link(
                        url=canonical,
                        label=str(link.get("label") or ""),
                        parent_url=parent_url,
                        parent_relevant=bool(item.get("scope_relevant")),
                        depth=int(item.get("depth") or 0) + 1,
                        max_depth=self.policy.max_depth,
                        broad_official_discovery=(
                            self.policy.broad_official_discovery
                        ),
                    )
                    if candidate:
                        classified.append(candidate)
                return classified

            candidates = _classify_links(
                links,
                parent_url=item["canonical_url"],
            )
            if not candidates and self.policy.broad_official_discovery:
                try:
                    from api.services.web_capture import (
                        discover_managed_rendered_links,
                    )

                    async with rendered_discovery_semaphore:
                        rendered = await discover_managed_rendered_links(
                            item["canonical_url"],
                            task_id=f"{crawl_task_id}_render",
                            timeout_seconds=min(
                                self.policy.request_timeout_seconds,
                                60,
                            ),
                        )
                    rendered_links = list(rendered.get("links") or [])
                    candidates = _classify_links(
                        rendered_links,
                        parent_url=str(
                            rendered.get("final_url")
                            or item["canonical_url"]
                        ),
                    )
                    links = rendered_links
                    content_length = max(
                        content_length,
                        int(rendered.get("content_length") or 0),
                    )
                    discovery_mode = "rendered"
                    counters["rendered_discovery_pages"] += 1
                except Exception as exc:  # noqa: BLE001
                    counters["rendered_discovery_failures"] += 1
                    logger.warning(
                        "官网动态目录渲染失败 task=%s url=%s error=%s",
                        crawl_task_id,
                        item.get("canonical_url"),
                        exc,
                    )
                    raise RuntimeError(
                        f"动态目录渲染失败: {exc}"
                    ) from exc
            await _queue_items(candidates)
            counters["listing_pages"] += 1
            if not dry_run:
                await crawl_dao.mark_page_terminal(
                    self.db,
                    page_id=page_id,
                    status="discovered",
                    fields={
                        "final_url": fetched.url,
                        "content_length": content_length,
                        "links_discovered": len(links),
                        "links_enqueued": len(candidates),
                        "discovery_mode": discovery_mode,
                    },
                )

        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            trust_env=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/138.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            },
        ) as session:

            async def _worker(worker_id: int) -> None:
                while True:
                    queued = await queue.get()
                    item = queued[2]
                    try:
                        if item.get("kind") == "document" or _is_document_url(
                            str(item.get("canonical_url") or ""),
                            str(item.get("anchor_text") or ""),
                        ):
                            item["kind"] = "document"
                            await _archive(item)
                        else:
                            await _discover(session, item)
                    except Exception as exc:  # noqa: BLE001
                        attempt = int(item.get("attempts_in_run") or 0) + 1
                        item["attempts_in_run"] = attempt
                        logger.warning(
                            "官网文档页面失败 task=%s worker=%s attempt=%s/%s "
                            "url=%s error=%s",
                            crawl_task_id,
                            worker_id,
                            attempt,
                            self.policy.max_page_attempts,
                            item.get("canonical_url"),
                            exc,
                        )
                        if attempt < self.policy.max_page_attempts:
                            if not dry_run:
                                await crawl_dao.mark_page_retry(
                                    self.db,
                                    page_id=str(item["page_id"]),
                                    error=str(exc),
                                )
                            await asyncio.sleep(min(4.0, 0.5 * (2 ** (attempt - 1))))
                            await queue.put(
                                (
                                    -int(item.get("priority") or 0),
                                    next(sequence),
                                    item,
                                )
                            )
                        else:
                            counters["failed_pages"] += 1
                            if not dry_run:
                                await crawl_dao.mark_page_terminal(
                                    self.db,
                                    page_id=str(item["page_id"]),
                                    status="error",
                                    fields={"error": str(exc)[:2_000]},
                                )
                            counters["processed_pages"] += 1
                    else:
                        counters["processed_pages"] += 1
                    finally:
                        queue.task_done()
                        if (
                            counters["processed_pages"] > 0
                            and counters["processed_pages"] % 10 == 0
                        ):
                            try:
                                await _record_progress()
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(
                                    "官网文档进度写入失败 task=%s: %s",
                                    crawl_task_id,
                                    exc,
                                )

            workers = [
                asyncio.create_task(_worker(index))
                for index in range(self.policy.discovery_concurrency)
            ]
            try:
                await queue.join()
            finally:
                for worker in workers:
                    worker.cancel()
                await asyncio.gather(*workers, return_exceptions=True)

        durable = (
            await crawl_dao.summarize_task(
                self.db,
                crawl_task_id=crawl_task_id,
            )
            if not dry_run
            else {}
        )
        if durable:
            counters.update(
                {
                    "total_pages": int(durable.get("total_pages") or 0),
                    "processed_pages": max(
                        0,
                        int(durable.get("total_pages") or 0)
                        - int(durable.get("pending_pages") or 0),
                    ),
                    "listing_pages": int(
                        (durable.get("by_status") or {}).get("discovered") or 0
                    ),
                    "documents_scheduled": int(
                        (durable.get("by_kind") or {}).get("document") or 0
                    ),
                    "documents_archived": int(
                        durable.get("archived_documents") or 0
                    ),
                    "documents_partial": int(
                        durable.get("partial_documents") or 0
                    ),
                    "documents_rejected": int(
                        durable.get("rejected_documents") or 0
                    ),
                    "failed_pages": int(durable.get("failed_pages") or 0),
                    "attachments_archived": int(
                        durable.get("attachments_archived") or 0
                    ),
                    "contacts_found": int(durable.get("contacts_found") or 0),
                    "findings_upserted": int(
                        durable.get("findings_upserted") or 0
                    ),
                }
            )
        status = "partial" if (
            counters["failed_pages"]
            or counters["documents_partial"]
            or int(durable.get("pending_pages") or 0)
            or truncated
        ) else "completed"
        summary = {
            "enabled": True,
            "status": status,
            "crawl_task_id": crawl_task_id,
            **counters,
            "truncated": truncated,
            "dry_run": dry_run,
            "required_path_segments": required_segments,
        }
        if dry_run:
            summary["preview"] = preview
            return summary
        await crawl_dao.finish_task(
            self.db,
            crawl_task_id=crawl_task_id,
            status=status,
            summary=summary,
            error=(
                f"{counters['failed_pages']} 个页面失败"
                if counters["failed_pages"]
                else f"{counters['documents_partial']} 篇附件证据不完整"
                if counters["documents_partial"]
                else "达到采集上限，保留剩余范围供后续增量"
                if truncated
                else ""
            ),
        )
        await update_source_progress(
            self.db,
            task_id=parent_task_id,
            source="website_documents",
            status=status,
            processed=counters["processed_pages"],
            total=counters["total_pages"],
            succeeded=counters["documents_archived"],
            failed=counters["failed_pages"] + counters["documents_partial"],
            message=(
                f"官网文档归档完成 {counters['documents_archived']} 篇，"
                f"附件 {counters['attachments_archived']} 个"
            ),
        )
        try:
            from api.services.company_scan_recovery import (
                reconcile_terminal_company_website_result,
            )

            await reconcile_terminal_company_website_result(
                self.db,
                parent_task_id=parent_task_id,
                website_summary=summary,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "官网文档终态投影同步失败 task=%s: %s",
                crawl_task_id,
                exc,
            )
        return summary

    async def run_until_stable(
        self,
        *,
        max_passes: int = 2,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Retry durable partial/error pages without repeating completed pages."""
        result: dict[str, Any] = {}
        passes = max(1, min(int(max_passes or 1), 3))
        for pass_index in range(passes):
            result = await self.run(**kwargs)
            result["retry_passes"] = pass_index
            if (
                result.get("status") != "partial"
                or result.get("truncated")
                or kwargs.get("dry_run")
            ):
                break
        return result
