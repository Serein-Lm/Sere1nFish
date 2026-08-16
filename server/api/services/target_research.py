"""Target 机构公开情报深研与扩展扫描编排。"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from langchain_core.messages import HumanMessage, ToolMessage
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import projects as projects_dao
from api.dao import target_research as research_dao
from api.dao import target_relationships as relationships_dao
from api.dao import targets as targets_dao
from api.dao import tasks as tasks_dao
from api.models.target_research import TargetResearchPayload
from api.services.company_normalize import NORMALIZATION_VERSION, normalize_root_domain
from api.services.source_documents.urls import canonicalize_source_url
from api.services.target_scan_profile import (
    build_target_scan_profile,
    persist_target_scan_profile,
)
from api.services.task_progress import update_task_stage
from core.background import spawn_background
from core.logger import get_logger
from core.observability import observation_context, obs_log
from Sere1nGraph.graph.agents.runtime import REQUIRE_EVIDENCE_TOOL_MARKER


logger = get_logger("target_research")
MAX_TARGET_RESEARCH_BATCH_SIZE = 100
MAX_TARGET_RESEARCH_CONCURRENCY = 8
TARGET_RESEARCH_BROWSER_ATTEMPTS = 3
EXPANDED_TARGET_BATCH_TAG = "拓展目标"
SUPERVISING_TARGET_BATCH_TAG = "上级单位"
RELATED_TARGET_BATCH_TAG = "关联单位"
_DOWNSTREAM_EXPAND_RELATIONS = {
    "subsidiary",
    "controlled_entity",
    "service_unit",
    "operating_entity",
    "platform_owner",
}
_UPSTREAM_EXPAND_RELATIONS = {"parent_organization"}
_LATERAL_EXPAND_RELATIONS = {"affiliated_unit"}
_AUTO_EXPAND_RELATIONS = (
    _DOWNSTREAM_EXPAND_RELATIONS
    | _UPSTREAM_EXPAND_RELATIONS
    | _LATERAL_EXPAND_RELATIONS
)
_TRUSTED_SOURCE_TYPES = {
    "official",
    "government",
    "regulator",
    "first_party",
    "institution",
}
_SEARCH_RESULT_PATHS = {
    "bing.com": ("/search",),
    "baidu.com": ("/s",),
    "google.com": ("/search",),
    "sogou.com": ("/web",),
    "so.com": ("/s",),
}
_ERROR_PAGE_TITLE_RE = re.compile(
    r"(?:^|\b)(?:401|403|404|500|502|503|504)(?:\b|$)|"
    r"not found|forbidden|bad gateway|service unavailable|gateway timeout|"
    r"页面不存在|页面未找到|访问出错|访问异常|系统错误",
    re.I,
)
_GENERIC_SHARED_PATH_SEGMENTS = {
    "about",
    "article",
    "column",
    "content",
    "default",
    "detail",
    "index",
    "info",
    "jgsz",
    "list",
    "news",
    "notice",
    "notices",
    "page",
    "pages",
    "public",
    "wjw",
    "wsjkw",
    "xxgk",
    "zwgk",
}
_OWNED_DOMAIN_SOURCE_TYPES = {
    "first_party",
    "institution",
    "official",
    "regulator",
}


class TargetResearchTargetNotFoundError(LookupError):
    pass


def _clean_strings(values: list[Any] | None, *, limit: int) -> list[str]:
    return list(
        dict.fromkeys(str(value).strip() for value in values or [] if str(value).strip())
    )[:limit]


def _browser_tool_text(value: Any) -> str:
    if isinstance(value, tuple) and value:
        value = value[0]
    if isinstance(value, list):
        return "\n".join(
            text
            for item in value
            if (text := _browser_tool_text(item))
        )
    if isinstance(value, dict):
        return str(value.get("text") or value.get("content") or "")
    return str(value or "")


def _canonical_browser_url(value: Any) -> str:
    raw = str(value or "").strip().rstrip(".,;，。；")
    url = canonicalize_source_url(raw)
    return url if url.startswith(("http://", "https://")) else ""


def _is_search_result_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("cn.")
    return any(
        parts.path.startswith(prefix)
        for prefix in _SEARCH_RESULT_PATHS.get(host, ())
    )


def _snapshot_is_error_page(text: str) -> bool:
    root_line = next(
        (line for line in str(text or "").splitlines() if "RootWebArea" in line),
        "",
    )
    title_match = re.search(r'RootWebArea\s+"([^"]*)"', root_line)
    title = title_match.group(1).strip() if title_match else ""
    return bool(title and _ERROR_PAGE_TITLE_RE.search(title))


def _is_origin_homepage(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except (TypeError, ValueError):
        return False
    return parts.path in {"", "/"} and not parts.query


def _source_title_matches_identity(
    source: dict[str, Any],
    *,
    canonical_name: str,
    aliases: list[Any] | None,
) -> bool:
    title = targets_dao.normalize_target_name(str(source.get("title") or ""))
    if not title:
        return False
    labels = {
        targets_dao.normalize_target_name(str(value or ""))
        for value in [canonical_name, *(aliases or [])]
        if targets_dao.normalize_target_name(str(value or ""))
    }
    return any(label in title or title in label for label in labels)


def _identity_key_matches(candidate: str, known: str) -> bool:
    """Match an existing identity with an official name carrying a qualifier."""
    if not candidate or not known:
        return False
    if candidate == known:
        return True
    return min(len(candidate), len(known)) >= 8 and (
        candidate.startswith(known) or known.startswith(candidate)
    )


def _with_target_identity_default(
    value: dict[str, Any],
    *,
    canonical_name: str,
) -> dict[str, Any]:
    """Keep the stable Target identity when an otherwise valid Agent payload omits it."""
    if str(value.get("canonical_name") or "").strip():
        return value
    return {**value, "canonical_name": str(canonical_name or "").strip()}


def _shared_government_path_segments(
    urls: list[str] | None,
    root_domains: list[str] | None,
) -> list[str]:
    """Derive bounded path scopes for units hosted on shared gov portals."""
    roots = [
        normalize_root_domain(value)
        for value in root_domains or []
        if normalize_root_domain(value).endswith(".gov.cn")
    ]
    if not roots:
        return []
    result: list[str] = []
    for root in roots:
        scoped_urls = []
        for value in urls or []:
            url = _canonical_browser_url(value)
            host = (urlsplit(url).hostname or "").casefold().rstrip(".") if url else ""
            if host == root or host.endswith(f".{root}"):
                scoped_urls.append(url)
        if not scoped_urls or any(_is_origin_homepage(url) for url in scoped_urls):
            continue
        for url in scoped_urls:
            candidates: list[tuple[int, str]] = []
            for raw_segment in urlsplit(url).path.split("/"):
                segment = unquote(raw_segment).strip().casefold()
                if (
                    len(segment) < 3
                    or len(segment) > 64
                    or segment in _GENERIC_SHARED_PATH_SEGMENTS
                    or "." in segment
                    or re.fullmatch(r"(?:19|20)\d{2}(?:\d{2}){0,2}", segment)
                ):
                    continue
                if segment.isdigit():
                    score = 100 + len(segment)
                elif any(character.isdigit() for character in segment):
                    score = 80 + len(segment)
                else:
                    score = 40 + len(segment)
                candidates.append((score, segment))
            if candidates:
                selected = max(candidates)[1]
                if selected not in result:
                    result.append(selected)
    return result[:16]


def _validated_scan_scope(
    *,
    urls: list[str],
    root_domains: list[Any] | None,
    sources_by_url: dict[str, dict[str, Any]],
    canonical_name: str,
    aliases: list[Any] | None,
) -> tuple[list[str], list[str]]:
    """Keep only owned origins or bounded target-specific gov portal paths."""
    candidate_domains = _clean_strings(
        [normalize_root_domain(value) for value in root_domains or []],
        limit=12,
    )
    relevant_urls = [
        url
        for url in urls
        if url in sources_by_url
        and (
            not _is_origin_homepage(url)
            or _source_title_matches_identity(
                sources_by_url[url],
                canonical_name=canonical_name,
                aliases=aliases,
            )
            or (
                bool(candidate_domains)
                and not normalize_root_domain(url).endswith(".gov.cn")
                and str(
                    sources_by_url[url].get("source_type") or ""
                ).strip().casefold()
                in _OWNED_DOMAIN_SOURCE_TYPES
                and bool(_filter_scan_urls_by_domains([url], candidate_domains))
            )
        )
    ]
    if not candidate_domains:
        candidate_domains = _clean_strings(
            [normalize_root_domain(url) for url in relevant_urls],
            limit=12,
        )
    verified_domains = [
        domain
        for domain in candidate_domains
        if _filter_scan_urls_by_domains(relevant_urls, [domain])
        and (
            not domain.endswith(".gov.cn")
            or any(_is_origin_homepage(url) for url in relevant_urls)
            or _shared_government_path_segments(relevant_urls, [domain])
        )
    ]
    return verified_domains, _filter_scan_urls_by_domains(
        relevant_urls,
        verified_domains,
    )


def _filter_scan_urls_by_domains(
    values: list[Any] | None,
    domains: list[Any] | None,
    *,
    limit: int = 60,
) -> list[str]:
    """Keep verified first-party URLs under one of the Target root domains."""
    normalized_domains = _clean_strings(
        [normalize_root_domain(value) for value in domains or []],
        limit=20,
    )
    if not normalized_domains:
        return []
    result: list[str] = []
    for value in values or []:
        url = _canonical_browser_url(value)
        host = (urlsplit(url).hostname or "").strip().lower().rstrip(".")
        if not url or not host or not any(
            host == domain or host.endswith(f".{domain}")
            for domain in normalized_domains
        ):
            continue
        if url not in result:
            result.append(url)
        if len(result) >= max(1, int(limit or 1)):
            break
    return result


def _selected_browser_urls(text: str) -> list[str]:
    return list(
        dict.fromkeys(
            url
            for value in re.findall(
                r"\((https?://[^\s)]+)\)\s*\[selected\]",
                text,
            )
            if (url := _canonical_browser_url(value))
        )
    )


def _unverified_navigation_domains(
    attempted_urls: list[str],
    verified_urls: set[str],
) -> list[str]:
    """Return failed page hosts in navigation order for browser failover."""
    verified_hosts = {
        (urlsplit(url).hostname or "").lower().rstrip(".")
        for url in verified_urls
    }
    domains: list[str] = []
    for value in attempted_urls:
        url = _canonical_browser_url(value)
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        if host and host not in verified_hosts and host not in domains:
            domains.append(host)
    return domains


def _build_navigation_evidence_observer(
    urls: set[str],
    attempted_urls: list[str] | None = None,
) -> Callable[[str, Any], None]:
    """Record browser evidence before Agent summarization can discard messages."""
    pending_selected_url = ""

    def observe(tool_name: str, result: Any) -> None:
        nonlocal pending_selected_url
        text = _browser_tool_text(result)
        if tool_name in {"evaluate_script", "evaluate", "take_snapshot"}:
            if tool_name == "take_snapshot":
                evaluation_succeeded = "## Latest page snapshot" in text
                evaluated_values = re.findall(
                    r'RootWebArea[^\n]*url="(https?://[^"\\]+)',
                    text,
                )
            else:
                evaluation_succeeded = (
                    "Script ran on page and returned:" in text
                    if tool_name == "evaluate_script"
                    else '"url"' in text and '"text"' in text
                )
                evaluated_values = re.findall(
                    r'"url"\s*:\s*"(https?://[^"\\]+)',
                    text,
                )
            evaluated_url = (
                _canonical_browser_url(evaluated_values[-1])
                if evaluated_values
                else ""
            )
            if tool_name == "take_snapshot" and _snapshot_is_error_page(text):
                pending_selected_url = ""
                return
            if evaluation_succeeded and (evaluated_url or pending_selected_url):
                urls.add(evaluated_url or pending_selected_url)
                pending_selected_url = ""
            return
        if tool_name not in {"navigate_page", "navigate"}:
            return

        if tool_name == "navigate":
            values = re.findall(r"Navigated to\s+(https?://[^\s]+)", text)
            pending_selected_url = (
                _canonical_browser_url(values[-1]) if values else ""
            )
            if (
                pending_selected_url
                and attempted_urls is not None
                and pending_selected_url not in attempted_urls
            ):
                attempted_urls.append(pending_selected_url)
            return

        selected_urls = _selected_browser_urls(text)
        if "Successfully navigated to " not in text:
            pending_selected_url = selected_urls[-1] if selected_urls else ""
            return

        successful_urls = [
            url
            for value in re.findall(
                r"Successfully navigated to\s+(https?://[^\s]+)",
                text,
            )
            if (url := _canonical_browser_url(value))
        ]
        pending_selected_url = (
            selected_urls[-1]
            if selected_urls
            else (successful_urls[-1] if successful_urls else "")
        )
        if (
            pending_selected_url
            and attempted_urls is not None
            and pending_selected_url not in attempted_urls
        ):
            attempted_urls.append(pending_selected_url)

    return observe


def _extract_navigated_urls(raw: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    observe = _build_navigation_evidence_observer(urls)
    for message in raw.get("messages") or []:
        if not isinstance(message, ToolMessage):
            continue
        tool_name = str(getattr(message, "name", "") or "")
        observe(tool_name, getattr(message, "content", ""))
    return urls


def _normalize_payload(
    payload: TargetResearchPayload,
    *,
    navigated_urls: set[str] | None = None,
) -> dict[str, Any]:
    data = payload.model_dump()
    sources_by_url: dict[str, dict[str, Any]] = {}
    dropped_source_urls: list[str] = []
    for source in data.get("sources") or []:
        url = canonicalize_source_url(str(source.get("url") or ""))
        if not url.startswith(("http://", "https://")):
            dropped_source_urls.append(str(source.get("url") or ""))
            continue
        if _is_search_result_url(url):
            dropped_source_urls.append(url)
            continue
        if navigated_urls is not None and url not in navigated_urls:
            dropped_source_urls.append(url)
            continue
        sources_by_url[url] = {
            **source,
            "url": url,
            "published_at": str(source.get("published_at") or ""),
        }
    if dropped_source_urls:
        logger.warning(
            "机构深研已丢弃未核验来源 | count=%s urls=%s",
            len(dropped_source_urls),
            dropped_source_urls,
        )
    if len(sources_by_url) < 2:
        raise ValueError("机构深研至少需要两个本轮浏览器实际导航并读取的正文来源")
    if not any(
        str(source.get("source_type") or "").strip().lower() in _TRUSTED_SOURCE_TYPES
        for source in sources_by_url.values()
    ):
        raise ValueError("机构深研至少需要一个官方、政府、监管或机构一手来源")
    known_urls = set(sources_by_url)

    def normalize_urls(values: list[Any]) -> list[str]:
        urls = [
            canonicalize_source_url(str(value or ""))
            for value in values
        ]
        return list(dict.fromkeys(url for url in urls if url in known_urls))

    dropped_derived = 0

    def normalize_many(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        nonlocal dropped_derived
        normalized: list[dict[str, Any]] = []
        for item in items:
            urls = normalize_urls(item.get("source_urls") or [])
            if not urls:
                dropped_derived += 1
                continue
            normalized.append({**item, "source_urls": urls})
        return normalized

    evidence = normalize_many(list(data.get("evidence") or []))
    key_people = normalize_many(list(data.get("key_people") or []))
    related: list[dict[str, Any]] = []
    for item in normalize_many(list(data.get("related_targets") or [])):
        related_name = str(item.get("name") or "").strip()
        related_aliases = _clean_strings(item.get("aliases"), limit=20)
        related_domains, related_urls = _validated_scan_scope(
            urls=normalize_urls(item.get("web_scan_urls") or []),
            root_domains=item.get("root_domains") or [],
            sources_by_url=sources_by_url,
            canonical_name=related_name,
            aliases=related_aliases,
        )
        related.append(
            {
                **item,
                "aliases": related_aliases,
                "root_domains": related_domains,
                "web_scan_urls": related_urls,
            }
        )
    contacts: list[dict[str, Any]] = []
    for item in data.get("public_contacts") or []:
        urls = normalize_urls([item.get("source_url")])
        if not urls:
            dropped_derived += 1
            continue
        contacts.append({**item, "source_url": urls[0]})
    if dropped_derived:
        logger.warning(
            "机构深研已丢弃无有效来源的派生事实 | count=%s",
            dropped_derived,
        )
    candidate_root_domains = _clean_strings(
        [normalize_root_domain(value) for value in data.get("root_domains") or []],
        limit=12,
    )
    canonical_name = str(data.get("canonical_name") or "").strip()
    aliases = _clean_strings(data.get("aliases"), limit=30)
    root_domains, web_scan_urls = _validated_scan_scope(
        urls=normalize_urls(data.get("web_scan_urls") or []),
        root_domains=candidate_root_domains,
        sources_by_url=sources_by_url,
        canonical_name=canonical_name,
        aliases=aliases,
    )
    if candidate_root_domains != root_domains:
        logger.warning(
            "机构深研已丢弃无自营入口或无专属路径的根域名 | target=%s domains=%s",
            canonical_name,
            sorted(set(candidate_root_domains) - set(root_domains)),
        )
    channel_terms = {
        str(channel).strip().lower(): _clean_strings(terms, limit=30)
        for channel, terms in (data.get("search_terms_by_channel") or {}).items()
        if str(channel).strip() and _clean_strings(terms, limit=30)
    }
    return {
        **data,
        "canonical_name": canonical_name,
        "aliases": aliases,
        "root_domains": root_domains,
        "web_scan_urls": web_scan_urls,
        "business_keywords": _clean_strings(data.get("business_keywords"), limit=80),
        "search_terms_by_channel": channel_terms,
        "sources": list(sources_by_url.values()),
        "evidence": evidence,
        "public_contacts": contacts,
        "key_people": key_people,
        "related_targets": related,
    }


def _prepare_payload_for_validation(
    value: dict[str, Any],
    *,
    navigated_urls: set[str] | None = None,
) -> dict[str, Any]:
    """Drop derived facts whose evidence cannot survive source validation."""
    data = dict(value)
    known_urls: set[str] = set()
    for source in data.get("sources") or []:
        if not isinstance(source, dict):
            continue
        url = _canonical_browser_url(source.get("url"))
        if (
            url
            and not _is_search_result_url(url)
            and (navigated_urls is None or url in navigated_urls)
        ):
            known_urls.add(url)

    def valid_urls(raw: Any) -> list[str]:
        values = raw if isinstance(raw, list) else [raw]
        return list(
            dict.fromkeys(
                url
                for item in values
                if (url := _canonical_browser_url(item)) in known_urls
            )
        )

    dropped = 0

    def prepare_many(field: str) -> None:
        nonlocal dropped
        prepared: list[dict[str, Any]] = []
        for raw_item in data.get(field) or []:
            if not isinstance(raw_item, dict):
                dropped += 1
                continue
            urls = valid_urls(raw_item.get("source_urls"))
            if not urls:
                dropped += 1
                continue
            item = {**raw_item, "source_urls": urls}
            if field == "related_targets":
                item["web_scan_urls"] = valid_urls(
                    raw_item.get("web_scan_urls") or []
                )
            prepared.append(item)
        data[field] = prepared

    for field in ("evidence", "key_people", "related_targets"):
        prepare_many(field)

    contacts: list[dict[str, Any]] = []
    for raw_item in data.get("public_contacts") or []:
        if not isinstance(raw_item, dict):
            dropped += 1
            continue
        urls = valid_urls(raw_item.get("source_url"))
        if not urls:
            dropped += 1
            continue
        contacts.append({**raw_item, "source_url": urls[0]})
    data["public_contacts"] = contacts
    data["web_scan_urls"] = valid_urls(data.get("web_scan_urls") or [])
    if dropped:
        logger.warning(
            "机构深研预校验已丢弃无有效来源的派生事实 | count=%s",
            dropped,
        )
    return data


def _validate_research_payload(
    value: dict[str, Any],
    *,
    navigated_urls: set[str] | None = None,
) -> dict[str, Any]:
    prepared = _prepare_payload_for_validation(
        value,
        navigated_urls=navigated_urls,
    )
    return _normalize_payload(
        TargetResearchPayload.model_validate(prepared),
        navigated_urls=navigated_urls,
    )


def _eligible_related_targets(
    data: dict[str, Any],
    *,
    current_name: str,
    limit: int,
) -> list[dict[str, Any]]:
    source_types = {
        str(source.get("url") or ""): str(source.get("source_type") or "").strip().lower()
        for source in data.get("sources") or []
    }
    eligible: list[dict[str, Any]] = []
    current_key = current_name.strip().casefold()
    for item in data.get("related_targets") or []:
        name = str(item.get("name") or "").strip()
        urls = list(item.get("source_urls") or [])
        trusted = any(source_types.get(url) in _TRUSTED_SOURCE_TYPES for url in urls)
        if (
            not name
            or name.casefold() == current_key
            or not item.get("should_scan")
            or str(item.get("relation_type") or "") not in _AUTO_EXPAND_RELATIONS
            or float(item.get("confidence") or 0) < 0.85
            or not trusted
        ):
            continue
        eligible.append(item)
    eligible.sort(
        key=lambda item: (
            -int(item.get("scan_priority") or 0),
            -float(item.get("confidence") or 0),
            str(item.get("name") or "").casefold(),
        )
    )
    return eligible[: max(1, min(int(limit or 1), 12))]


def _relationship_direction(relation_type: str) -> str:
    normalized = str(relation_type or "").strip()
    if normalized in _UPSTREAM_EXPAND_RELATIONS:
        return relationships_dao.UPSTREAM_DIRECTION
    if normalized in _LATERAL_EXPAND_RELATIONS:
        return relationships_dao.LATERAL_DIRECTION
    return relationships_dao.DOWNSTREAM_DIRECTION


def _expanded_batch_tags(
    values: list[str] | None,
    *required_tags: str,
) -> list[str]:
    required = targets_dao.normalize_batch_tags(list(required_tags))
    existing = [
        tag
        for tag in targets_dao.normalize_batch_tags(values or [])
        if tag not in required
    ]
    available = max(0, targets_dao.MAX_TARGET_BATCH_TAGS - len(required))
    return [*existing[:available], *required]


def _preserved_relation(relation: dict[str, Any] | None) -> dict[str, Any] | None:
    if not relation or int(relation.get("relation_depth") or 0) <= 0:
        return None
    fields = (
        "root_target_id",
        "root_target_name",
        "parent_target_id",
        "parent_target_name",
        "relation_type",
        "relation_depth",
        "ownership_percent",
        "relation_source",
        "lineage_target_ids",
        "lineage_target_names",
    )
    return {field: relation.get(field) for field in fields if relation.get(field) is not None}


async def _latest_scan_params(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target: dict[str, Any],
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    aliases = _clean_strings(
        [target.get("canonical_name"), *(target.get("aliases") or [])], limit=40
    )
    latest = await tasks_dao.find_latest_matching_task(
        db,
        project_id=project_id,
        task_type="company_scan",
        param_filters={"company_name": {"$in": aliases}},
        projection={"params": 1},
    )
    params = dict((latest or {}).get("params") or {})
    for key in ("company_name", "urls", "url_text", "company_scan_concurrency"):
        params.pop(key, None)
    if not params:
        params = {
            "enable_asset_discovery": True,
            "enable_url_scan": True,
            "enable_xhs": False,
            "enable_wechat": False,
            "enable_bidding": False,
            "enable_scholar": True,
            "enable_copywriting": True,
            "enable_control_structure": False,
            "incremental_scan": False,
        }
    params.update(dict(overrides or {}))
    return params


def _candidate_scan_params(
    base_params: dict[str, Any],
    *,
    name: str,
    target_id: str = "",
    is_root: bool,
    seed_urls: list[str] | None = None,
    root_domains: list[Any] | None = None,
) -> dict[str, Any]:
    """Apply reusable root/related-Target source policies to one scan."""
    params = {**base_params, "company_name": name}
    if target_id:
        params["target_id"] = target_id
    normalized_seed_urls = _clean_strings(seed_urls, limit=60)
    if normalized_seed_urls:
        params["urls"] = normalized_seed_urls
    shared_path_segments = _shared_government_path_segments(
        normalized_seed_urls,
        [normalize_root_domain(value) for value in root_domains or []],
    )
    if shared_path_segments and not params.get("website_required_path_segments"):
        params["website_required_path_segments"] = shared_path_segments
        if not bool(params.get("allow_shared_portal_asset_discovery", False)):
            params["enable_asset_discovery"] = False
    if not is_root and not bool(params.get("enable_subsidiary_bidding", False)):
        params["enable_bidding"] = False
    if not is_root and not bool(params.get("enable_subsidiary_wechat", False)):
        params["enable_wechat"] = False
    return params


async def _schedule_company_scans(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    root_target: dict[str, Any],
    expanded_targets: list[dict[str, Any]],
    task_id: str,
    requested_by: str,
    scan_params: dict[str, Any] | None,
    rescan_root: bool,
    root_seed_urls: list[str] | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    from api.services.project_task_runtime import execute_project_task

    base_params = await _latest_scan_params(
        db,
        project_id=project_id,
        target=root_target,
        overrides=scan_params,
    )
    candidates = list(expanded_targets)
    if rescan_root:
        candidates.insert(0, root_target)
    documents: list[dict[str, Any]] = []
    runtime_jobs: list[tuple[str, dict[str, Any]]] = []
    skipped: list[dict[str, str]] = []
    seen_candidates: set[str] = set()
    now = datetime.now(timezone.utc)
    root_target_id = str(root_target.get("target_id") or "")
    for candidate in candidates:
        name = str(candidate.get("canonical_name") or candidate.get("name") or "").strip()
        target_id = str(candidate.get("target_id") or "")
        if not name:
            continue
        identity = f"target:{target_id}" if target_id else f"name:{name.casefold()}"
        if identity in seen_candidates:
            skipped.append({
                "target_id": target_id,
                "reason": "与本轮其他扫描归并为同一 Target",
            })
            continue
        seen_candidates.add(identity)
        active = await tasks_dao.find_latest_matching_task(
            db,
            project_id=project_id,
            task_type="company_scan",
            param_filters={"company_name": name},
            statuses=["pending", "running", "pausing", "paused"],
            projection={"task_id": 1, "status": 1},
        )
        if active:
            skipped.append({"target_id": target_id, "reason": f"已有 {active.get('status')} 扫描"})
            continue
        if not rescan_root or target_id != str(root_target.get("target_id") or ""):
            completed = await tasks_dao.find_latest_matching_task(
                db,
                project_id=project_id,
                task_type="company_scan",
                param_filters={"company_name": name},
                statuses=["completed"],
                projection={"task_id": 1},
            )
            if completed:
                skipped.append({"target_id": target_id, "reason": "当前项目已有完成扫描"})
                continue
        child_task_id = uuid.uuid4().hex[:12]
        is_root = target_id == root_target_id
        relation = dict(candidate.get("research_relation") or {})
        seed_urls = (
            list(root_seed_urls or [])
            if is_root
            else _filter_scan_urls_by_domains(
                list(relation.get("web_scan_urls") or []),
                [
                    candidate.get("root_domain"),
                    *(candidate.get("root_domains") or []),
                ],
                limit=30,
            )
        )
        params = _candidate_scan_params(
            base_params,
            name=name,
            target_id=target_id,
            is_root=is_root,
            seed_urls=seed_urls,
            root_domains=[
                candidate.get("root_domain"),
                *(candidate.get("root_domains") or []),
            ],
        )
        documents.append(
            {
                "task_id": child_task_id,
                "project_id": project_id,
                "task_type": "company_scan",
                "params": params,
                "requested_by": requested_by,
                "parent_task_id": task_id,
                "target_research_id": str(root_target.get("latest_research_id") or ""),
                "status": "pending",
                "progress": {},
                "created_at": now,
                "updated_at": now,
            }
        )
        runtime_jobs.append((child_task_id, params))
    await tasks_dao.insert_tasks(db, documents)
    for child_task_id, params in runtime_jobs:
        spawn_background(
            execute_project_task(
                child_task_id,
                project_id,
                "company_scan",
                {**params, "_requested_by": requested_by},
            ),
            name=f"target-research-scan:{child_task_id}",
        )
    return [task_id for task_id, _params in runtime_jobs], skipped


async def enqueue_target_research(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str,
    requested_by: str,
    scan_discovered_targets: bool = True,
    rescan_root: bool = True,
    max_related_targets: int = 8,
    force_refresh: bool = True,
    scan_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """校验并下发一项持久化机构深研任务。"""
    target = await targets_dao.get_target(db, target_id)
    relation = await targets_dao.get_project_target(
        db, project_id=project_id, target_id=target_id
    )
    if not target or not relation:
        raise TargetResearchTargetNotFoundError("Target 不存在或不属于当前项目")
    existing = await tasks_dao.find_latest_matching_task(
        db,
        project_id=project_id,
        task_type="target_research",
        param_filters={"target_id": target_id},
        statuses=["pending", "running", "pausing", "paused"],
        projection={"_id": 0, "task_id": 1, "status": 1},
    )
    if existing:
        return {**existing, "target_id": target_id, "deduplicated": True}

    from api.services.project_task_runtime import execute_project_task

    task_id = uuid.uuid4().hex[:12]
    params = {
        "target_id": target_id,
        "scan_discovered_targets": bool(scan_discovered_targets),
        "rescan_root": bool(rescan_root),
        "max_related_targets": max(1, min(int(max_related_targets or 8), 12)),
        "force_refresh": bool(force_refresh),
        "scan_params": dict(scan_params or {}),
    }
    now = datetime.now(timezone.utc)
    await tasks_dao.insert_tasks(
        db,
        [{
            "task_id": task_id,
            "project_id": project_id,
            "task_type": "target_research",
            "params": params,
            "requested_by": requested_by,
            "status": "pending",
            "progress": {},
            "created_at": now,
            "updated_at": now,
        }],
    )
    spawn_background(
        execute_project_task(
            task_id,
            project_id,
            "target_research",
            {**params, "_requested_by": requested_by},
        ),
        name=f"target-research:{task_id}",
    )
    return {
        "task_id": task_id,
        "target_id": target_id,
        "task_type": "target_research",
        "status": "pending",
        "deduplicated": False,
    }


async def enqueue_target_research_batch(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_names: list[str],
    requested_by: str,
    concurrency: int = 4,
    scan_discovered_targets: bool = True,
    rescan_root: bool = True,
    max_related_targets: int = 4,
    force_refresh: bool = True,
    scan_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a Target list and enqueue bounded, recoverable deep research."""
    from api.services.project_task_batch import (
        ProjectTaskJob,
        parse_company_names,
        run_project_task_batch,
    )
    from api.services.project_task_runtime import execute_project_task
    from api.services.targets import resolve_target

    if not await projects_dao.get_project(db, project_id):
        raise TargetResearchTargetNotFoundError("项目不存在")
    names = parse_company_names(target_names)
    if not names:
        raise ValueError("Target 列表不能为空")
    if len(names) > MAX_TARGET_RESEARCH_BATCH_SIZE:
        raise ValueError(
            f"一次最多下发 {MAX_TARGET_RESEARCH_BATCH_SIZE} 个 Target"
        )

    safe_concurrency = max(
        1,
        min(int(concurrency or 4), MAX_TARGET_RESEARCH_CONCURRENCY, len(names)),
    )
    batch_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    documents: list[dict[str, Any]] = []
    jobs: list[ProjectTaskJob] = []
    linked_targets: list[dict[str, str]] = []
    deduplicated: list[dict[str, str]] = []
    seen_target_ids: set[str] = set()
    shared_scan_params = dict(scan_params or {})

    for name in names:
        target = await resolve_target(
            db,
            target_name=name,
            target_type="company",
            aliases=[name],
            source="target_research_batch",
        )
        if not target:
            continue
        await targets_dao.link_project_target(
            db,
            project_id=project_id,
            target=target,
            search_terms=[name],
            objectives=["批量机构公开情报深研与扩展扫描"],
        )
        target_id = str(target.get("target_id") or "")
        target_name = str(target.get("canonical_name") or name)
        linked_targets.append({"target_id": target_id, "target_name": target_name})
        if target_id in seen_target_ids:
            deduplicated.append(
                {"target_id": target_id, "target_name": target_name, "reason": "批次内重复"}
            )
            continue
        seen_target_ids.add(target_id)
        active = await tasks_dao.find_latest_matching_task(
            db,
            project_id=project_id,
            task_type="target_research",
            param_filters={"target_id": target_id},
            statuses=["pending", "running", "pausing", "paused"],
            projection={"_id": 0, "task_id": 1, "status": 1},
        )
        if active:
            deduplicated.append({
                "target_id": target_id,
                "target_name": target_name,
                "task_id": str(active.get("task_id") or ""),
                "reason": f"已有 {active.get('status')} 深研",
            })
            continue

        task_id = uuid.uuid4().hex[:12]
        params = {
            "target_id": target_id,
            "scan_discovered_targets": bool(scan_discovered_targets),
            "rescan_root": bool(rescan_root),
            "max_related_targets": max(1, min(int(max_related_targets or 4), 12)),
            "force_refresh": bool(force_refresh),
            "scan_params": shared_scan_params,
        }
        documents.append({
            "task_id": task_id,
            "project_id": project_id,
            "task_type": "target_research",
            "params": params,
            "requested_by": requested_by,
            "batch_id": batch_id,
            "batch_index": len(documents) + 1,
            "batch_concurrency": safe_concurrency,
            "status": "pending",
            "progress": {},
            "created_at": now,
            "updated_at": now,
        })
        jobs.append(ProjectTaskJob(
            task_id=task_id,
            project_id=project_id,
            task_type="target_research",
            params={**params, "_requested_by": requested_by},
        ))

    total = len(jobs)
    for document in documents:
        document["batch_total"] = total
    if documents:
        await tasks_dao.insert_tasks(db, documents)
        spawn_background(
            run_project_task_batch(
                batch_id=batch_id,
                project_id=project_id,
                jobs=jobs,
                executor=execute_project_task,
                concurrency=safe_concurrency,
                dispatch_concurrency=safe_concurrency,
                aggregate_notification=False,
            ),
            name=f"target-research-batch:{batch_id}",
        )
    return {
        "batch_id": batch_id,
        "task_type": "target_research",
        "task_count": total,
        "task_ids": [job.task_id for job in jobs],
        "linked_target_count": len({item["target_id"] for item in linked_targets}),
        "targets": linked_targets,
        "deduplicated": deduplicated,
        "concurrency": safe_concurrency,
        "status": "pending" if jobs else "deduplicated",
    }


async def run_target_research(
    db: AsyncIOMotorDatabase,
    app_config: Any,
    *,
    task_id: str,
    project_id: str,
    target_id: str,
    max_related_targets: int = 8,
    scan_discovered_targets: bool = True,
    rescan_root: bool = False,
    force_refresh: bool = True,
    scan_params: dict[str, Any] | None = None,
    requested_by: str = "",
) -> dict[str, Any]:
    target = await targets_dao.get_target(db, target_id)
    relation = await targets_dao.get_project_target(
        db, project_id=project_id, target_id=target_id
    )
    if not target or not relation:
        raise ValueError("Target 不存在或不属于当前项目")

    if not force_refresh:
        cached = await research_dao.get_latest_research(
            db, target_id=target_id, project_id=project_id
        )
        researched_at = (cached or {}).get("researched_at")
        if isinstance(researched_at, datetime):
            age = datetime.now(timezone.utc) - researched_at.astimezone(timezone.utc)
            if age.days <= 30:
                return {**cached, "cached": True, "scan_task_ids": []}

    await update_task_stage(
        db, task_id=task_id, stage="target_research", message="正在深研机构身份、业务与关联 Target..."
    )
    from api.services.info_collection.url_tools import _build_worker_chrome_config
    from browser_manager.provider import (
        get_browser_provider,
        is_browser_infrastructure_error,
    )
    from Sere1nGraph.graph.agents.factory import create_target_research_agent
    from Sere1nGraph.graph.agents.runtime import extract_with_retry
    from Sere1nGraph.graph.prompts.loader import load_prompt

    provider = get_browser_provider()
    browser_task_id = f"target_research_{task_id}"
    query = (
        "请对以下公司/机构执行公开互联网深度研究，并严格输出 Prompt 规定的 JSON。\n"
        f"project_id: {project_id}\n"
        f"target_id: {target_id}\n"
        f"机构名称: {target.get('canonical_name') or ''}\n"
        f"可信身份别名: {target.get('identity_aliases') or []}\n"
        f"已有根域名: {target.get('root_domains') or []}\n"
        "目标：使用 Bing 结合机构全称、可信简称和已知根域名补全机构信息，"
        "实际打开并核验可进入网站深扫的自营业务页面；识别可继续扫描的明确隶属、"
        "控制、直属服务或平台运营单位；必须额外尝试通过官网机构设置、直属单位名录"
        "或权威政府页面核验一层直接主管单位。直接主管单位使用 parent_organization，"
        "仅有行业监管、业务指导、地域归属或名称包含关系时不得认定为上级；"
        "合作方、供应商、媒体转载主体和同名第三方不得标记为自动扫描。"
        f"\n{REQUIRE_EVIDENCE_TOOL_MARKER}"
    )
    prompt = load_prompt("target_research/target_research")
    parsed: dict[str, Any] | None = None
    navigated_urls: set[str] = set()
    failed_domains: list[str] = []
    try:
        cdp_url = ""
        for browser_attempt in range(1, TARGET_RESEARCH_BROWSER_ATTEMPTS + 1):
            attempt_urls: set[str] = set()
            attempted_urls: list[str] = []
            try:
                if browser_attempt == 1:
                    cdp_url = await provider.get_cdp_endpoint(
                        task_id=browser_task_id,
                        purpose="target_research",
                    )
                else:
                    cdp_url = await provider.hot_swap_container(
                        task_id=browser_task_id,
                        purpose="target_research",
                    )
                    if not cdp_url:
                        await provider.release_cdp_endpoint(browser_task_id)
                        cdp_url = await provider.get_cdp_endpoint(
                            task_id=browser_task_id,
                            purpose="target_research",
                        )
                if not cdp_url:
                    raise RuntimeError("无法获取 Chrome 容器执行机构深研")

                if browser_attempt > 1:
                    await update_task_stage(
                        db,
                        task_id=task_id,
                        stage="target_research",
                        message=(
                            "已切换浏览器，正在重新执行机构深研 "
                            f"{browser_attempt}/{TARGET_RESEARCH_BROWSER_ATTEMPTS}..."
                        ),
                    )

                worker_config = _build_worker_chrome_config(app_config, cdp_url)
                agent = await create_target_research_agent(
                    worker_config,
                    mcp_result_observer=_build_navigation_evidence_observer(
                        attempt_urls,
                        attempted_urls,
                    ),
                )

                async def run_research_pass(
                    pass_query: str,
                    *,
                    phase: str,
                ) -> dict[str, Any] | None:
                    with observation_context(
                        project_id=project_id,
                        task_id=task_id,
                        phase=phase,
                        agent="target_research",
                        task_type="target_research",
                    ):
                        raw = await agent(
                            {"messages": [HumanMessage(content=pass_query)]}
                        )
                    attempt_urls.update(_extract_navigated_urls(raw))
                    content_urls = sorted(
                        url
                        for url in attempt_urls
                        if not _is_search_result_url(url)
                    )

                    def validate_research_payload(value: dict[str, Any]) -> None:
                        _validate_research_payload(
                            _with_target_identity_default(
                                value,
                                canonical_name=str(
                                    target.get("canonical_name") or ""
                                ),
                            ),
                            navigated_urls=attempt_urls,
                        )

                    return await extract_with_retry(
                        raw,
                        worker_config,
                        max_retries=1,
                        system_prompt=prompt,
                        validator=validate_research_payload,
                        repair_context=(
                            "以下 URL 由本轮浏览器实际打开并读取。sources、evidence、联系方式、"
                            "关键人物和关联 Target 只能引用其中的正文 URL；Bing 等搜索结果页只用于"
                            "发现候选，不得作为来源。至少选择两个正文来源：\n"
                            + "\n".join(content_urls)
                        ),
                    )

                attempt_query = query
                if failed_domains:
                    attempt_query += (
                        "\n\n以下域名在本任务此前的浏览器中未能成功读取，"
                        "本轮禁止再次访问，请改用其他独立权威来源："
                        + "、".join(failed_domains)
                    )
                parsed = await run_research_pass(
                    attempt_query,
                    phase="target_research",
                )
                if not parsed:
                    logger.warning(
                        "机构深研首次浏览证据不足，启动一次补充检索 | target=%s navigated=%s",
                        target_id,
                        len(attempt_urls),
                    )
                    retry_failed_domains = _unverified_navigation_domains(
                        attempted_urls,
                        attempt_urls,
                    )
                    retry_query = (
                        attempt_query
                        + "\n\n上一次浏览没有形成可校验结果。请重新检索并实际打开、读取至少两个正文页面，"
                        "其中至少一个必须是官网、政府、监管或机构一手来源。不要输出搜索结果页，"
                        "不要引用未打开的 URL；先完成补证，再输出完整 JSON。"
                    )
                    if retry_failed_domains:
                        retry_query += (
                            "\n本轮未成功读取的以下域名不要再次访问："
                            + "、".join(retry_failed_domains)
                        )
                    parsed = await run_research_pass(
                        retry_query,
                        phase="target_research_evidence_retry",
                    )
                navigated_urls = attempt_urls
                break
            except Exception as exc:
                for domain in _unverified_navigation_domains(
                    attempted_urls,
                    attempt_urls,
                ):
                    if domain not in failed_domains:
                        failed_domains.append(domain)
                if (
                    browser_attempt >= TARGET_RESEARCH_BROWSER_ATTEMPTS
                    or not is_browser_infrastructure_error(exc)
                ):
                    raise
                logger.warning(
                    "机构深研浏览器故障，准备热切换重试 | target=%s attempt=%s/%s avoid=%s error=%s",
                    target_id,
                    browser_attempt,
                    TARGET_RESEARCH_BROWSER_ATTEMPTS,
                    failed_domains,
                    exc,
                )
                await update_task_stage(
                    db,
                    task_id=task_id,
                    stage="target_research_browser_retry",
                    message=(
                        "浏览器连接异常，正在切换容器重试 "
                        f"{browser_attempt + 1}/{TARGET_RESEARCH_BROWSER_ATTEMPTS}..."
                    ),
                )
    finally:
        await provider.release_cdp_endpoint(browser_task_id)
    if not parsed:
        raise ValueError("机构深研 Agent 未返回可解析的结构化结果")

    data = _validate_research_payload(
        _with_target_identity_default(
            parsed,
            canonical_name=str(target.get("canonical_name") or ""),
        ),
        navigated_urls=navigated_urls,
    )
    await update_task_stage(
        db, task_id=task_id, stage="target_expand", message="正在校验证据并扩展 Target 关系..."
    )
    root_domains = list(data.get("root_domains") or [])
    known_identity_keys = {
        targets_dao.normalize_target_name(value)
        for value in [
            target.get("canonical_name"),
            *(target.get("identity_aliases") or []),
        ]
        if targets_dao.normalize_target_name(str(value or ""))
    }
    research_identity_key = targets_dao.normalize_target_name(
        str(data.get("canonical_name") or "")
    )
    known_domains = {
        normalize_root_domain(value)
        for value in [
            target.get("root_domain"),
            *(target.get("root_domains") or []),
        ]
        if normalize_root_domain(value)
    }
    research_identity_verified = bool(
        any(
            _identity_key_matches(research_identity_key, known_key)
            for known_key in known_identity_keys
        )
        or known_domains.intersection(root_domains)
    )
    if not research_identity_verified:
        logger.warning(
            "机构深研身份与 Target 不一致，忽略本轮身份别名和域名扩展 | "
            "target=%s candidate=%s",
            target_id,
            data.get("canonical_name"),
        )
    enriched_target = await targets_dao.merge_target_research_identity(
        db,
        target_id=target_id,
        root_domains=root_domains if research_identity_verified else [],
        aliases=[
            *(target.get("aliases") or []),
            *(
                [
                    str(data.get("canonical_name") or ""),
                    *(data.get("aliases") or []),
                ]
                if research_identity_verified
                else []
            ),
        ],
    ) or target
    root_scan_urls = _filter_scan_urls_by_domains(
        list(data.get("web_scan_urls") or []),
        [
            enriched_target.get("root_domain"),
            *(enriched_target.get("root_domains") or []),
        ],
    )
    scan_profile = build_target_scan_profile(
        canonical_name=str(enriched_target.get("canonical_name") or ""),
        identity_aliases=list(enriched_target.get("identity_aliases") or []),
        verified_aliases=(
            list(data.get("aliases") or []) if research_identity_verified else []
        ),
        ai_aliases=list(data.get("aliases") or []),
        fallback_aliases=list(enriched_target.get("aliases") or []),
        existing_profile=dict(enriched_target.get("scan_profile") or {}),
        ai_identity_verified=research_identity_verified,
        source="target_research",
    )
    enriched_target = await persist_target_scan_profile(
        db,
        project_id=project_id,
        target=enriched_target,
        profile=scan_profile,
        routed_terms_by_channel=dict(data.get("search_terms_by_channel") or {}),
        additional_search_terms=list(data.get("business_keywords") or []),
    )
    await targets_dao.link_project_target(
        db,
        project_id=project_id,
        target=enriched_target,
        objectives=["机构公开情报深研与高置信关联 Target 扩展"],
        task_def_id=task_id,
        relation=_preserved_relation(relation),
    )

    current_depth = max(0, int(relation.get("relation_depth") or 0))
    candidates = _eligible_related_targets(
        data,
        current_name=str(enriched_target.get("canonical_name") or ""),
        limit=max_related_targets,
    )
    expanded: list[dict[str, Any]] = []
    relationship_edges: list[dict[str, Any]] = []
    expanded_target_ids: set[str] = set()
    root_target_id = str(relation.get("root_target_id") or target_id)
    root_target_name = str(
        relation.get("root_target_name") or enriched_target.get("canonical_name") or ""
    )
    for candidate in candidates:
        relation_type = str(
            candidate.get("relation_type") or "affiliated_unit"
        ).strip()
        direction = _relationship_direction(relation_type)
        if (
            direction == relationships_dao.DOWNSTREAM_DIRECTION
            and current_depth >= 2
        ):
            continue
        domains = _clean_strings(
            [normalize_root_domain(value) for value in candidate.get("root_domains") or []],
            limit=12,
        )
        candidate_name = str(candidate.get("name") or "").strip()
        candidate_aliases = _clean_strings(
            [candidate_name, *(candidate.get("aliases") or [])],
            limit=30,
        )
        existing_related = await targets_dao.find_target_exact_name(
            db,
            name=candidate_name,
        )
        if (
            existing_related
            and str(existing_related.get("target_id") or "") == target_id
        ):
            if direction != relationships_dao.DOWNSTREAM_DIRECTION:
                logger.warning(
                    "机构深研非下级关系与当前 Target 归并为同一身份，已拒绝关系 | "
                    "target=%s candidate=%s",
                    target_id,
                    candidate_name,
                )
                continue
            enriched_target = await targets_dao.merge_target_research_identity(
                db,
                target_id=target_id,
                root_domains=domains,
                aliases=candidate_aliases,
            ) or enriched_target
            await targets_dao.link_project_target(
                db,
                project_id=project_id,
                target=enriched_target,
                search_terms=candidate_aliases,
                objectives=["机构深研识别的自营平台或同主体能力"],
                task_def_id=task_id,
                relation=_preserved_relation(relation),
            )
            continue
        related = await targets_dao.upsert_target(
            db,
            name=(
                str(existing_related.get("canonical_name") or candidate_name)
                if existing_related
                else candidate_name
            ),
            root_domain=(
                str(existing_related.get("root_domain") or "")
                if existing_related
                else domains[0] if domains else ""
            ),
            root_domains=domains,
            aliases=candidate_aliases,
            source="target_research",
            normalization_version=NORMALIZATION_VERSION if domains else None,
            match_aliases=False,
            preferred_target_id=str((existing_related or {}).get("target_id") or ""),
            identity_aliases=[candidate_name],
        )
        related_target_id = str(related.get("target_id") or "")
        if related_target_id == target_id:
            if direction != relationships_dao.DOWNSTREAM_DIRECTION:
                logger.warning(
                    "机构深研非下级关系被错误归并到当前 Target，已拒绝关系 | "
                    "target=%s candidate=%s",
                    target_id,
                    candidate_name,
                )
                continue
            enriched_target = await targets_dao.merge_target_research_identity(
                db,
                target_id=target_id,
                root_domains=domains,
                aliases=candidate_aliases,
            ) or enriched_target
            continue
        if not related_target_id or related_target_id in expanded_target_ids:
            continue
        expanded_target_ids.add(related_target_id)
        if direction in {
            relationships_dao.UPSTREAM_DIRECTION,
            relationships_dao.LATERAL_DIRECTION,
        }:
            existing_project_relation = await targets_dao.get_project_target(
                db,
                project_id=project_id,
                target_id=related_target_id,
            )
            project_relation = _preserved_relation(existing_project_relation)
            if direction == relationships_dao.UPSTREAM_DIRECTION:
                objectives = ["机构深研发现并核验的直接主管单位"]
                batch_tags = _expanded_batch_tags(
                    list(relation.get("batch_tags") or []),
                    EXPANDED_TARGET_BATCH_TAG,
                    SUPERVISING_TARGET_BATCH_TAG,
                )
            else:
                objectives = ["机构深研发现并核验的横向关联单位"]
                batch_tags = _expanded_batch_tags(
                    list(relation.get("batch_tags") or []),
                    EXPANDED_TARGET_BATCH_TAG,
                    RELATED_TARGET_BATCH_TAG,
                )
        else:
            relation_depth = current_depth + 1
            project_relation = {
                "root_target_id": root_target_id,
                "root_target_name": root_target_name,
                "parent_target_id": target_id,
                "parent_target_name": enriched_target.get("canonical_name") or "",
                "relation_type": relation_type,
                "relation_depth": relation_depth,
                "relation_source": "target_research",
                "relation_summary": candidate.get("relationship_summary") or "",
                "relation_source_urls": list(candidate.get("source_urls") or []),
                "lineage_target_ids": list(
                    dict.fromkeys(
                        [*(relation.get("lineage_target_ids") or []), target_id]
                    )
                ),
                "lineage_target_names": list(
                    dict.fromkeys(
                        [
                            *(relation.get("lineage_target_names") or []),
                            str(enriched_target.get("canonical_name") or ""),
                        ]
                    )
                ),
            }
            objectives = ["机构深研发现的高置信关联单位"]
            batch_tags = _expanded_batch_tags(
                list(relation.get("batch_tags") or []),
                EXPANDED_TARGET_BATCH_TAG,
            )
        await targets_dao.link_project_target(
            db,
            project_id=project_id,
            target=related,
            search_terms=[str(candidate.get("name") or ""), *(candidate.get("aliases") or [])],
            objectives=objectives,
            task_def_id=task_id,
            relation=project_relation,
            batch_tags=batch_tags,
        )
        related_profile = build_target_scan_profile(
            canonical_name=str(related.get("canonical_name") or candidate_name),
            identity_aliases=list(related.get("identity_aliases") or [candidate_name]),
            verified_aliases=list(candidate.get("aliases") or []),
            ai_aliases=list(candidate.get("aliases") or []),
            fallback_aliases=list(related.get("aliases") or []),
            existing_profile=dict(related.get("scan_profile") or {}),
            ai_identity_verified=True,
            source="target_research_related",
        )
        related = await persist_target_scan_profile(
            db,
            project_id=project_id,
            target=related,
            profile=related_profile,
        )
        research_relation = {
            **candidate,
            "relationship_direction": direction,
        }
        expanded.append({**related, "research_relation": research_relation})
        relationship_edges.append(
            {
                "related_target_id": related_target_id,
                "related_target_name": str(
                    related.get("canonical_name") or candidate_name
                ),
                "relation_type": relation_type,
                "direction": direction,
                "summary": str(candidate.get("relationship_summary") or ""),
                "confidence": float(candidate.get("confidence") or 0),
                "source_urls": list(candidate.get("source_urls") or []),
            }
        )

    research = await research_dao.save_research(
        db,
        project_id=project_id,
        target_id=target_id,
        task_id=task_id,
        document={
            **data,
            "expanded_targets": [
                {
                    "target_id": item.get("target_id"),
                    "target_name": item.get("canonical_name"),
                    "root_domain": item.get("root_domain"),
                    **dict(item.get("research_relation") or {}),
                }
                for item in expanded
            ],
            "expanded_target_count": len(expanded),
        },
    )
    persisted_relationships = await relationships_dao.sync_research_relationships(
        db,
        project_id=project_id,
        subject_target_id=target_id,
        subject_target_name=str(enriched_target.get("canonical_name") or ""),
        task_id=task_id,
        research_id=str(research.get("research_id") or ""),
        relationships=relationship_edges,
    )
    enriched_target = await targets_dao.enrich_target_from_research(
        db,
        target_id=target_id,
        summary=str(data.get("summary") or ""),
        industry=str(data.get("industry") or ""),
        organization_type=str(data.get("organization_type") or ""),
        responsibilities=list(data.get("responsibilities") or []),
        services=list(data.get("services") or []),
        business_keywords=list(data.get("business_keywords") or []),
        key_people=list(data.get("key_people") or []),
        research_id=str(research.get("research_id") or ""),
    ) or enriched_target

    scan_task_ids: list[str] = []
    skipped_scans: list[dict[str, str]] = []
    if scan_discovered_targets or rescan_root:
        await update_task_stage(
            db, task_id=task_id, stage="target_scan_dispatch", message="正在下发扩展 Target 扫描..."
        )
        scan_task_ids, skipped_scans = await _schedule_company_scans(
            db,
            project_id=project_id,
            root_target=enriched_target,
            expanded_targets=expanded if scan_discovered_targets else [],
            task_id=task_id,
            requested_by=requested_by,
            scan_params=scan_params,
            rescan_root=rescan_root,
            root_seed_urls=root_scan_urls,
        )
    result = {
        "research_id": research.get("research_id"),
        "target_id": target_id,
        "target_name": enriched_target.get("canonical_name"),
        "summary": data.get("summary"),
        "source_count": len(data.get("sources") or []),
        "evidence_count": len(data.get("evidence") or []),
        "web_scan_url_count": len(root_scan_urls),
        "expanded_target_count": len(expanded),
        "relationship_count": len(persisted_relationships),
        "supervising_unit_count": sum(
            item.get("direction") == relationships_dao.UPSTREAM_DIRECTION
            for item in persisted_relationships
        ),
        "related_unit_count": sum(
            item.get("direction") == relationships_dao.LATERAL_DIRECTION
            for item in persisted_relationships
        ),
        "expanded_targets": research.get("expanded_targets") or [],
        "scan_task_ids": scan_task_ids,
        "skipped_scans": skipped_scans,
    }
    obs_log(
        "Target 机构深研完成",
        project_id=project_id,
        task_id=task_id,
        source="target_research",
        level="notice",
        event="target_research_completed",
        data={
            "target_id": target_id,
            "sources": result["source_count"],
            "expanded_targets": len(expanded),
            "scan_tasks": len(scan_task_ids),
        },
    )
    logger.notice(
        "Target 深研完成 | target=%s sources=%s expanded=%s scans=%s",
        target_id,
        result["source_count"],
        len(expanded),
        len(scan_task_ids),
    )
    return result
