"""Target 扫描画像、可信检索名和跨渠道覆盖的统一领域服务。"""
from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase


SCAN_PROFILE_VERSION = 4
SCAN_CHANNELS = ("website", "wechat", "xhs", "bidding", "scholar", "control")

_LEGAL_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "集团有限公司",
    "控股有限公司",
    "有限公司",
)
_QUERY_MARKERS = (
    "联系方式",
    "手机号",
    "手机号码",
    "办公室电话",
    "联系人",
    "公众号",
    "招标",
    "投标",
    "采购",
    "招聘",
    "投稿",
)
_GENERIC_ALIASES = {
    "公司",
    "集团",
    "控股",
    "有限责任",
    "有限公司",
    "有限责任公司",
    "股份有限公司",
    "服务",
    "建设",
    "投资",
    "科技",
    "数字",
    "国际",
    "中心",
    "研究院",
}


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalized_name(value: Any) -> str:
    from api.dao.targets import normalize_target_name

    return normalize_target_name(_clean_text(value))


def _valid_alias(value: Any, *, canonical: bool = False) -> str:
    alias = _clean_text(value)
    if (
        not alias
        or len(alias) < 2
        or len(alias) > (128 if canonical else 64)
        or any(marker in alias for marker in ("://", "@"))
        or (not canonical and "/" in alias)
        or (
            not canonical
            and any(marker in alias for marker in _QUERY_MARKERS)
        )
        or (not canonical and _normalized_name(alias) in _GENERIC_ALIASES)
    ):
        return ""
    return alias


def _strip_legal_suffix(value: str) -> str:
    compact = re.sub(r"\s+", "", value)
    for suffix in _LEGAL_SUFFIXES:
        if compact.endswith(suffix):
            candidate = compact[: -len(suffix)]
            return candidate if len(candidate) >= 2 else ""
    return ""


def _structurally_related(canonical_name: str, alias: str) -> bool:
    canonical = _normalized_name(canonical_name)
    candidate = _normalized_name(alias)
    if not canonical or not candidate:
        return False
    if candidate == canonical or candidate in canonical:
        return True
    # 允许“主管机关前缀 + 机构名”，禁止“机构名 + 下属部门”污染根身份。
    if canonical in candidate:
        return candidate.endswith(canonical)
    canonical_core = _normalized_name(_strip_legal_suffix(canonical_name))
    if bool(
        canonical_core
        and (
            candidate in canonical_core
            or (
                canonical_core in candidate
                and candidate.endswith(canonical_core)
            )
        )
    ):
        return True

    # 允许省略名称中间的限定词，但要求首尾身份片段都一致且整体高度相似。
    # 这覆盖“南京禄口国际机场”/“南京禄口机场”，同时不会把追加的下属部门
    # 或只有宽泛前缀的机构名称纳入 Target 身份。
    matcher = SequenceMatcher(None, canonical, candidate)
    blocks = [block for block in matcher.get_matching_blocks() if block.size]
    return bool(
        len(blocks) >= 2
        and blocks[0].a == 0
        and blocks[0].b == 0
        and blocks[0].size >= 2
        and blocks[-1].a + blocks[-1].size == len(canonical)
        and blocks[-1].b + blocks[-1].size == len(candidate)
        and blocks[-1].size >= 2
        and matcher.ratio() >= 0.75
    )


def _profile_fingerprint(profile: dict[str, Any]) -> str:
    """只对会改变扫描输入的身份字段取指纹。"""
    stable = {
        key: profile.get(key)
        for key in (
            "canonical_name",
            "search_aliases",
        )
    }
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_target_scan_profile(
    *,
    canonical_name: str,
    identity_aliases: list[str] | None = None,
    verified_aliases: list[str] | None = None,
    ai_aliases: list[str] | None = None,
    fallback_aliases: list[str] | None = None,
    existing_profile: dict[str, Any] | None = None,
    ai_identity_verified: bool = False,
    source: str = "company_scan",
) -> dict[str, Any]:
    """构建可追溯扫描画像，未经身份确认的名称不得进入检索词。"""
    canonical = _valid_alias(canonical_name, canonical=True)
    if not canonical:
        raise ValueError("Target 规范名称不能为空")

    alias_details: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(
        values: list[str] | None,
        *,
        origin: str,
        confidence: float,
        canonical_value: bool = False,
    ) -> None:
        for value in values or []:
            alias = _valid_alias(value, canonical=canonical_value)
            key = _normalized_name(alias)
            if not alias or not key or key in seen:
                continue
            seen.add(key)
            alias_details.append(
                {
                    "value": alias,
                    "source": origin,
                    "confidence": round(max(0.0, min(float(confidence), 1.0)), 2),
                }
            )

    add(
        [canonical],
        origin="canonical",
        confidence=1.0,
        canonical_value=True,
    )
    from api.dao.targets import is_safe_identity_alias

    previous = dict(existing_profile or {})
    safe_previous_aliases = [
        alias
        for value in previous.get("search_aliases") or []
        if (alias := _valid_alias(value))
        and is_safe_identity_alias(canonical, alias)
    ]
    add(
        safe_previous_aliases,
        origin="previous_profile",
        confidence=0.95,
    )

    safe_verified_aliases = [
        alias
        for value in verified_aliases or []
        if (alias := _valid_alias(value))
        and is_safe_identity_alias(canonical, alias)
    ]
    add(
        safe_verified_aliases,
        origin="verified_normalization",
        confidence=0.95,
    )
    safe_ai_aliases: list[str] = []
    if ai_identity_verified:
        safe_ai_aliases = [
            alias
            for value in ai_aliases or []
            if (alias := _valid_alias(value))
            and is_safe_identity_alias(canonical, alias)
        ]
        add(safe_ai_aliases, origin="company_router_ai", confidence=0.9)

    derived = _strip_legal_suffix(canonical)
    if derived:
        add([derived], origin="legal_suffix", confidence=0.8)

    safe_identity_aliases = [
        alias
        for value in identity_aliases or []
        if (alias := _valid_alias(value))
        and (
            _structurally_related(canonical, alias)
            or is_safe_identity_alias(canonical, alias)
        )
    ]
    add(safe_identity_aliases, origin="identity", confidence=0.85)

    safe_fallbacks = [
        alias
        for value in fallback_aliases or []
        if (alias := _valid_alias(value)) and _structurally_related(canonical, alias)
    ]
    add(safe_fallbacks, origin="structural_fallback", confidence=0.7)
    search_aliases = [item["value"] for item in alias_details][:20]
    short_names = [
        value
        for value in search_aliases
        if _normalized_name(value) != _normalized_name(canonical)
        and 2 <= len(value) <= 32
    ][:8]
    display_name = short_names[0] if short_names else canonical
    profile: dict[str, Any] = {
        "version": SCAN_PROFILE_VERSION,
        "canonical_name": canonical,
        "display_name": display_name,
        "short_names": short_names,
        "search_aliases": search_aliases,
        "alias_details": alias_details[:20],
        "generated_by_ai": bool(safe_ai_aliases),
        "source": _clean_text(source) or "company_scan",
    }
    previous_aliases = [
        _clean_text(value)
        for value in previous.get("search_aliases") or []
        if _clean_text(value)
    ]
    if (
        _normalized_name(previous.get("canonical_name"))
        == _normalized_name(canonical)
        and previous_aliases == search_aliases
        and _clean_text(previous.get("fingerprint"))
    ):
        # 画像实现升级但扫描输入未变化时，沿用历史覆盖指纹。
        profile["fingerprint"] = _clean_text(previous.get("fingerprint"))
    else:
        profile["fingerprint"] = _profile_fingerprint(profile)
    return profile


def target_scan_names(
    *,
    target: dict[str, Any] | None = None,
    project_target: dict[str, Any] | None = None,
    fallback_name: str = "",
) -> list[str]:
    """读取当前权威检索名；旧 aliases 仅做保守结构化回退。"""
    target = target or {}
    project_target = project_target or {}
    profile = dict(target.get("scan_profile") or {})
    canonical = _clean_text(
        target.get("canonical_name")
        or project_target.get("target_name")
        or fallback_name
    )
    authoritative = [
        *(project_target.get("scan_aliases") or []),
        *(profile.get("search_aliases") or []),
        *(target.get("scan_aliases") or []),
    ]
    if authoritative:
        values = [canonical, *authoritative]
    else:
        values = [
            canonical,
            *(target.get("identity_aliases") or []),
            *[
                value
                for value in target.get("aliases") or []
                if _structurally_related(canonical, str(value or ""))
            ],
            *[
                value
                for value in project_target.get("search_terms") or []
                if _structurally_related(canonical, str(value or ""))
            ],
        ]
    result: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        alias = _valid_alias(value, canonical=index == 0 and value == canonical)
        key = _normalized_name(alias)
        if alias and key and key not in seen:
            seen.add(key)
            result.append(alias)
    return result[:20]


async def persist_target_scan_profile(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target: dict[str, Any],
    profile: dict[str, Any],
    routed_terms_by_channel: dict[str, list[str]] | None = None,
    additional_search_terms: list[str] | None = None,
) -> dict[str, Any]:
    """原子更新全局画像和项目快照，并统一重建分渠道检索词。"""
    from api.dao import targets as targets_dao
    from api.services.search_terms import build_target_channel_terms

    target_id = str(target.get("target_id") or "")
    if not target_id:
        raise ValueError("target_id 不能为空")
    updated_target = await targets_dao.update_target_scan_profile(
        db,
        target_id=target_id,
        profile=profile,
    )
    channel_terms = build_target_channel_terms(
        names=list(profile.get("search_aliases") or []),
        routed_terms_by_channel=routed_terms_by_channel,
    )
    await targets_dao.update_project_target_scan_profile(
        db,
        project_id=project_id,
        target_id=target_id,
        profile=profile,
        search_terms=list(
            dict.fromkeys(
                [
                    *list(profile.get("search_aliases") or []),
                    *[
                        _clean_text(value)
                        for value in additional_search_terms or []
                        if _clean_text(value)
                    ],
                ]
            )
        )[:100],
        search_terms_by_channel=channel_terms,
    )
    return updated_target or {**target, "scan_profile": profile}


async def record_target_scan_coverage(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str,
    channel: str,
    status: str,
    task_id: str,
    summary: dict[str, Any] | None = None,
    profile_fingerprint: str = "",
) -> None:
    from api.dao import targets as targets_dao

    if not profile_fingerprint:
        relation = await targets_dao.get_project_target(
            db,
            project_id=project_id,
            target_id=target_id,
        )
        profile_fingerprint = str(
            (relation or {}).get("scan_profile_fingerprint")
            or ((relation or {}).get("scan_profile") or {}).get("fingerprint")
            or ""
        )

    await targets_dao.record_project_target_scan_coverage(
        db,
        project_id=project_id,
        target_id=target_id,
        channel=channel,
        status=status,
        task_id=task_id,
        summary=summary,
        profile_fingerprint=profile_fingerprint,
    )


async def backfill_target_scan_profiles(
    db: AsyncIOMotorDatabase,
) -> dict[str, int]:
    """幂等补齐历史 Target 画像；仅使用可信身份和结构相关别名。"""
    from api.dao import targets as targets_dao
    from api.services.search_terms import build_target_channel_terms

    targets, project_targets = await targets_dao.get_scan_profile_backfill_candidates(
        db,
        version=SCAN_PROFILE_VERSION,
    )
    profiles: dict[str, dict[str, Any]] = {}
    target_updates: list[dict[str, Any]] = []
    for target in targets:
        target_id = str(target.get("target_id") or "")
        canonical_name = _clean_text(target.get("canonical_name"))
        if not target_id or not canonical_name:
            continue
        current_profile = dict(target.get("scan_profile") or {})
        if (
            int(target.get("scan_profile_version") or 0) == SCAN_PROFILE_VERSION
            and current_profile.get("search_aliases")
        ):
            profile = current_profile
        else:
            profile = build_target_scan_profile(
                canonical_name=canonical_name,
                identity_aliases=list(target.get("identity_aliases") or []),
                fallback_aliases=list(target.get("aliases") or []),
                existing_profile=current_profile,
                source="legacy_backfill",
            )
            target_updates.append({"target_id": target_id, "profile": profile})
        profiles[target_id] = profile

    project_updates: list[dict[str, Any]] = []
    for relation in project_targets:
        target_id = str(relation.get("target_id") or "")
        profile = profiles.get(target_id)
        if profile is None:
            canonical_name = _clean_text(relation.get("target_name"))
            if not canonical_name:
                continue
            profile = build_target_scan_profile(
                canonical_name=canonical_name,
                source="legacy_project_backfill",
            )
        project_updates.append(
            {
                "project_id": str(relation.get("project_id") or ""),
                "target_id": target_id,
                "profile": profile,
                "search_terms_by_channel": build_target_channel_terms(
                    names=list(profile.get("search_aliases") or [])
                ),
            }
        )

    result = await targets_dao.apply_scan_profile_backfill(
        db,
        target_profiles=target_updates,
        project_profiles=project_updates,
    )
    return {
        **result,
        "target_candidates": len(targets),
        "project_target_candidates": len(project_targets),
    }


def is_scan_coverage_current(
    relation: dict[str, Any],
    channel: str,
) -> bool:
    """判断渠道是否已由当前扫描画像完整覆盖。"""
    coverage = dict((relation.get("scan_coverage") or {}).get(channel) or {})
    if str(coverage.get("status") or "") != "completed":
        return False
    profile = dict(relation.get("scan_profile") or {})
    current_fingerprint = str(
        relation.get("scan_profile_fingerprint")
        or profile.get("fingerprint")
        or ""
    )
    coverage_fingerprint = str(coverage.get("profile_fingerprint") or "")
    return not current_fingerprint or coverage_fingerprint == current_fingerprint


def coverage_status_from_result(
    channel: str,
    outcome: dict[str, Any],
) -> str:
    """把各 runtime 的返回状态收敛成稳定的渠道覆盖状态。"""
    if channel == "website":
        url_scan = dict(outcome.get("url_scan") or {})
        website_documents = dict(outcome.get("website_documents") or {})
        if not url_scan or (
            url_scan.get("enabled") is not False and not website_documents
        ):
            return "partial"
        if int(url_scan.get("failed_urls") or 0) > 0 or int(
            url_scan.get("remaining_urls") or 0
        ) > 0:
            return "partial"
        if (
            int(website_documents.get("failed_pages") or 0) > 0
            or int(website_documents.get("documents_partial") or 0) > 0
            or int(website_documents.get("pending_pages") or 0) > 0
            or bool(website_documents.get("truncated"))
        ):
            return "partial"
        nested = [
            dict(outcome.get("assets") or {}),
            url_scan,
            website_documents,
        ]
        required = [
            item
            for item in (url_scan, website_documents)
            if item and item.get("enabled") is not False
        ]
        # A website run is complete only when every enabled content stage
        # explicitly reports a terminal status. Legacy summaries without this
        # evidence are partial, never implicitly complete.
        if any(not str(item.get("status") or "").strip() for item in required):
            return "partial"
        statuses = {
            str(item.get("status") or "").strip().lower()
            for item in nested
            if item and item.get("enabled") is not False
        }
        if "error" in statuses:
            return "partial" if len(statuses - {"error"}) else "error"
        if statuses.intersection(
            {
                "pending",
                "running",
                "probing",
                "scanning",
                "waiting_model",
                "partial",
                "timed_out",
                "stopped",
            }
        ):
            return "partial"
        if str(outcome.get("status") or "").lower() in {
            "partial",
            "timed_out",
            "stopped",
        }:
            return "partial"
        return "completed"
    raw_status = str(outcome.get("status") or "completed").strip().lower()
    if raw_status == "error":
        return "error"
    if raw_status in {"partial", "timed_out", "stopped"}:
        return "partial"
    if raw_status in {"disabled", "skipped", "unavailable"}:
        return "skipped"
    return "completed"


def has_current_mobile_keyword_coverage(
    outcome: dict[str, Any],
    *,
    target_id: str,
) -> bool:
    """Return whether a mobile result proves full use of resolved Target terms."""
    resolution = outcome.get("keyword_resolution") or {}
    if not isinstance(resolution, dict):
        return False
    keywords = [
        _clean_text(value)
        for value in resolution.get("keywords") or []
        if _clean_text(value)
    ]
    resolved_target_ids = {
        _clean_text(value)
        for value in resolution.get("target_ids") or []
        if _clean_text(value)
    }
    if not keywords or (target_id and target_id not in resolved_target_ids):
        return False
    try:
        keyword_total = int(outcome.get("keyword_total") or len(keywords))
        keywords_completed = int(outcome.get("keywords_completed") or 0)
        failed_keywords = int(outcome.get("failed_keywords") or 0)
        persist_failed = int(outcome.get("persist_failed") or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        keyword_total > 0
        and keywords_completed >= keyword_total
        and failed_keywords == 0
        and persist_failed == 0
        and not outcome.get("stopped")
        and not outcome.get("timed_out")
    )


async def backfill_target_scan_coverage(
    db: AsyncIOMotorDatabase,
) -> dict[str, int]:
    """从历史检查点恢复可信覆盖；旧手机词库仅记为部分覆盖。"""
    from api.dao import targets as targets_dao
    from api.dao import tasks as tasks_dao
    from api.dao.targets import is_safe_identity_alias
    from api.db.collections import (
        URL_SCAN_TASKS_COLLECTION,
        WEBSITE_CRAWL_TASKS_COLLECTION,
    )

    relations = await targets_dao.list_project_target_scan_state(db)
    relations_by_key = {
        (str(item.get("project_id") or ""), str(item.get("target_id") or "")): item
        for item in relations
    }
    tasks = await tasks_dao.list_completed_company_scans_for_coverage(db)
    tasks_by_id = {
        str(item.get("task_id") or ""): item
        for item in tasks
        if str(item.get("task_id") or "")
    }
    company_task_ids = [
        str(item.get("task_id") or "")
        for item in tasks
        if str(item.get("task_id") or "")
    ]
    url_task_rows = await db[URL_SCAN_TASKS_COLLECTION].find(
        {"task_id": {"$in": [f"{task_id}_url" for task_id in company_task_ids]}},
        {"_id": 0},
    ).to_list(None)
    url_tasks = {
        str(item.get("task_id") or "").removesuffix("_url"): item
        for item in url_task_rows
    }
    website_task_rows = await db[WEBSITE_CRAWL_TASKS_COLLECTION].find(
        {
            "crawl_task_id": {
                "$in": [f"{task_id}_webdocs" for task_id in company_task_ids]
            }
        },
        {"_id": 0},
    ).to_list(None)
    website_tasks = {
        str(item.get("crawl_task_id") or "").removesuffix("_webdocs"): item
        for item in website_task_rows
    }
    channel_map = {
        "asset_url": "website",
        "wechat": "wechat",
        "xhs": "xhs",
        "bidding": "bidding",
        "scholar": "scholar",
        "control_structure": "control",
    }
    fallback_fields = {
        "asset_url": None,
        "wechat": "wechat",
        "xhs": "xhs",
        "bidding": "bidding",
        "scholar": "scholar",
        "control_structure": "control_structure",
    }
    events: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    stale_tasks = 0

    def is_at_least_as_new(current: Any, candidate: Any) -> bool:
        if isinstance(current, datetime) and isinstance(candidate, datetime):
            current_value = (
                current.replace(tzinfo=timezone.utc)
                if current.tzinfo is None
                else current.astimezone(timezone.utc)
            )
            candidate_value = (
                candidate.replace(tzinfo=timezone.utc)
                if candidate.tzinfo is None
                else candidate.astimezone(timezone.utc)
            )
            return current_value >= candidate_value
        return bool(current) and str(current) >= str(candidate or "")

    for task in tasks:
        project_id = str(task.get("project_id") or "")
        identity = dict((task.get("result") or {}).get("identity") or {})
        target_id = str(identity.get("target_id") or "")
        relation = relations_by_key.get((project_id, target_id))
        if relation is None:
            continue
        canonical_name = _clean_text(relation.get("target_name"))
        identity_names = [
            identity.get("normalized_name"),
            identity.get("input_name"),
            *(identity.get("aliases") or []),
        ]
        unsafe_names = [
            _clean_text(value)
            for value in identity_names
            if _clean_text(value)
            and not is_safe_identity_alias(canonical_name, _clean_text(value))
        ]
        if unsafe_names:
            stale_tasks += 1
            continue
        profile = dict(relation.get("scan_profile") or {})
        fingerprint = str(
            relation.get("scan_profile_fingerprint")
            or profile.get("fingerprint")
            or ""
        )
        completed_at = task.get("completed_at") or task.get("updated_at")
        modules = dict((task.get("checkpoint") or {}).get("modules") or {})
        result = dict(task.get("result") or {})
        for module, channel in channel_map.items():
            checkpoint = dict(modules.get(module) or {})
            outcome = dict(checkpoint.get("result") or {})
            fallback_field = fallback_fields[module]
            if not outcome and fallback_field:
                outcome = dict(result.get(fallback_field) or {})
            if module == "asset_url" and not outcome:
                assets = dict(result.get("assets") or {})
                url_scan = dict(result.get("url_scan") or {})
                if assets or url_scan:
                    outcome = {
                        "kind": "asset_url",
                        "assets": assets,
                        "url_scan": url_scan,
                    }
            if module == "asset_url" and outcome:
                durable_url = dict(url_tasks.get(str(task.get("task_id") or "")) or {})
                url_scan = dict(outcome.get("url_scan") or {})
                if durable_url:
                    url_scan.update(
                        {
                            key: durable_url.get(key)
                            for key in (
                                "status",
                                "total_urls",
                                "alive_urls",
                                "eligible_urls",
                                "scanned_urls",
                                "failed_urls",
                                "remaining_urls",
                                "error",
                            )
                            if durable_url.get(key) is not None
                        }
                    )
                    url_scan["enabled"] = True
                outcome["url_scan"] = url_scan

                durable_website = dict(
                    website_tasks.get(str(task.get("task_id") or "")) or {}
                )
                website_documents = dict(outcome.get("website_documents") or {})
                if durable_website:
                    website_documents.update(
                        dict(durable_website.get("summary") or {})
                    )
                    website_documents.update(
                        {
                            "enabled": True,
                            "status": str(durable_website.get("status") or "pending"),
                            "error": str(durable_website.get("error") or ""),
                        }
                    )
                elif url_scan.get("enabled") is not False:
                    website_documents = {
                        "enabled": True,
                        "status": "pending",
                        "legacy_missing": True,
                    }
                outcome["website_documents"] = website_documents
                if (
                    dict(outcome.get("assets") or {}).get("enabled") is False
                    and url_scan.get("enabled") is False
                ):
                    continue
            if module == "control_structure" and outcome.get("result"):
                outcome = dict(outcome.get("result") or {})
            if not outcome or outcome.get("enabled") is False:
                continue
            status = coverage_status_from_result(channel, outcome)
            if status in {"disabled", "skipped"}:
                continue
            summary = {
                key: value
                for key, value in outcome.items()
                if isinstance(value, (str, int, float, bool)) and key != "error"
            }
            if (
                channel in {"wechat", "xhs"}
                and status == "completed"
                and not has_current_mobile_keyword_coverage(
                    outcome,
                    target_id=target_id,
                )
            ):
                status = "partial"
                summary["legacy_keyword_strategy"] = True
            coverage: dict[str, Any] = {
                "status": status,
                "task_id": str(task.get("task_id") or ""),
                "summary": summary,
                "profile_fingerprint": fingerprint,
                "updated_at": completed_at,
            }
            if status == "completed":
                coverage["completed_at"] = completed_at
            key = (project_id, target_id)
            persisted = dict(
                (relation.get("scan_coverage") or {}).get(channel) or {}
            )
            persisted_task = dict(
                tasks_by_id.get(str(persisted.get("task_id") or "")) or {}
            )
            persisted_params = dict(persisted_task.get("params") or {})
            persisted_channel_invalid = bool(
                channel == "website"
                and persisted_task
                and not persisted_params.get("enable_asset_discovery", True)
                and not persisted_params.get("enable_url_scan", True)
            )
            reclassifying_same_task = bool(
                str(persisted.get("task_id") or "")
                == str(task.get("task_id") or "")
                and str(persisted.get("status") or "") != status
            )
            if (
                is_at_least_as_new(persisted.get("updated_at"), completed_at)
                and not reclassifying_same_task
                and not persisted_channel_invalid
            ):
                continue
            existing = events.setdefault(key, {}).get(channel)
            if not existing or not is_at_least_as_new(
                existing.get("updated_at"), completed_at
            ):
                events[key][channel] = coverage

    rows = [
        {"project_id": key[0], "target_id": key[1], "channels": channels}
        for key, channels in events.items()
    ]
    result = await targets_dao.apply_scan_coverage_backfill(db, items=rows)
    return {
        **result,
        "tasks_reviewed": len(tasks),
        "stale_tasks": stale_tasks,
        "target_channels": sum(len(item["channels"]) for item in rows),
    }


async def load_project_descendant_scan_entities(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    root_target_id: str,
    max_depth: int = 2,
) -> list[dict[str, Any]]:
    """将已持久化的子孙 Target 转换为扫描实体，不依赖实时股权 API。"""
    from api.dao import targets as targets_dao

    relations = await targets_dao.list_project_target_descendants(
        db,
        project_id=project_id,
        root_target_id=root_target_id,
        max_depth=max_depth,
    )
    entities: list[dict[str, Any]] = []
    for relation in relations:
        target_id = _clean_text(relation.get("target_id"))
        name = _clean_text(relation.get("target_name"))
        if not target_id or not name:
            continue
        profile = dict(relation.get("scan_profile") or {})
        aliases = target_scan_names(
            project_target=relation,
            fallback_name=name,
        )
        root_domains = list(
            dict.fromkeys(
                _clean_text(value)
                for value in (
                    relation.get("root_domains")
                    or [relation.get("root_domain")]
                )
                if _clean_text(value)
            )
        )
        entities.append(
            {
                "target_id": target_id,
                "project_target_id": _clean_text(
                    relation.get("project_target_id")
                ),
                "name": name,
                "aliases": aliases,
                "display_name": _clean_text(
                    relation.get("display_name")
                    or profile.get("display_name")
                    or name
                ),
                "short_names": list(
                    relation.get("short_names")
                    or profile.get("short_names")
                    or []
                ),
                "scan_profile": profile,
                "root_domain": _clean_text(
                    relation.get("root_domain")
                    or (root_domains[0] if root_domains else "")
                ),
                "icp_domains": root_domains,
                "ownership_percent": relation.get("ownership_percent"),
                "root_target_id": _clean_text(
                    relation.get("root_target_id") or root_target_id
                ),
                "root_target_name": _clean_text(
                    relation.get("root_target_name")
                ),
                "parent_target_id": _clean_text(
                    relation.get("parent_target_id")
                ),
                "parent_target_name": _clean_text(
                    relation.get("parent_target_name")
                ),
                "relation_depth": max(
                    1, int(relation.get("relation_depth") or 1)
                ),
                "lineage_target_ids": list(
                    relation.get("lineage_target_ids") or []
                ),
                "lineage_target_names": list(
                    relation.get("lineage_target_names") or []
                ),
                "registration_status": _clean_text(
                    relation.get("registration_status")
                ),
                "entity_source": "project_target_relation",
            }
        )
    return entities


async def select_subsidiary_scan_scope(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    entities: list[dict[str, Any]],
    channels: list[str],
    max_entities: int = 12,
    skip_completed: bool = True,
) -> dict[str, Any]:
    """按存活线索与渠道缺口选择子单位，不重复扫描已完成覆盖。"""
    from api.dao import targets as targets_dao

    requested_channels = [
        channel
        for channel in dict.fromkeys(_clean_text(value).lower() for value in channels)
        if channel in SCAN_CHANNELS
    ]
    target_ids = [
        str(entity.get("target_id") or "")
        for entity in entities
        if str(entity.get("target_id") or "")
    ]
    relations = await targets_dao.get_project_targets_by_ids(
        db,
        project_id=project_id,
        target_ids=target_ids,
    )
    by_target = {str(item.get("target_id") or ""): item for item in relations}
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for index, entity in enumerate(entities):
        target_id = str(entity.get("target_id") or "")
        relation = by_target.get(target_id, {})
        coverage = dict(relation.get("scan_coverage") or {})
        pending_channels = [
            channel
            for channel in requested_channels
            if not (
                skip_completed
                and is_scan_coverage_current(relation, channel)
            )
        ]
        item = {
            **entity,
            "scan_channels": pending_channels,
            "scan_coverage": coverage,
            "scan_scope_index": index,
        }
        if requested_channels and not pending_channels:
            skipped.append({**item, "skip_reason": "requested_channels_completed"})
            continue
        candidates.append(item)

    def rank(item: dict[str, Any]) -> tuple[Any, ...]:
        registration = _clean_text(item.get("registration_status"))
        inactive = any(marker in registration for marker in ("注销", "吊销", "撤销"))
        has_domain = bool(item.get("root_domain") or item.get("icp_domains"))
        return (
            int(inactive),
            -int(has_domain),
            int(item.get("relation_depth") or 1),
            int(item.get("scan_scope_index") or 0),
            _clean_text(item.get("name")).casefold(),
        )

    candidates.sort(key=rank)
    safe_limit = max(1, min(int(max_entities or 12), 100))
    selected = candidates[:safe_limit]
    skipped.extend(
        {**item, "skip_reason": "scan_scope_limit"}
        for item in candidates[safe_limit:]
    )
    now = datetime.now(timezone.utc)
    return {
        "selected": selected,
        "skipped": skipped,
        "requested_channels": requested_channels,
        "selected_count": len(selected),
        "skipped_count": len(skipped),
        "planned_at": now,
    }
