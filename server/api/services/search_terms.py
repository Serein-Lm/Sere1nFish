"""跨采集渠道的目标搜索词统一服务。

词库正文来自数据库同步后的 Skill Registry。调用侧只声明渠道、目标和显式词，
本服务负责渐进式加载对应 Skill、展开模板、持久化和项目关系聚合。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase


CHANNEL_SKILLS = {
    "xhs": "xhs-keywords",
    "weixin": "wechat-keywords",
}

CHANNEL_TERM_POLICIES = {
    "xhs": {"generated_weight": 3},
    "weixin": {
        "generated_weight": 10,
        "max_names": 3,
        "alias_template_count": 2,
    },
}

FALLBACK_TEMPLATES = {
    "xhs": [
        "{company} 实习",
        "{company} 内推",
        "{company} 招聘",
        "{company} 工作体验",
    ],
    "weixin": [
        "{company}",
        "{company} 公众号",
        "{company} 联系方式",
        "{company} 手机号码",
        "{company} 座机",
        "{company} 邮箱",
        "{company} 联系人",
        "{company} 办公室 电话",
        "{company} 招标 联系人",
        "{company} 采购 联系方式",
        "{company} 招聘 邮箱",
        "{company} 投稿 邮箱",
        "{company} 新闻",
        "{company} 公告",
        "{company} 合作 联系方式",
    ],
}

_CODE_TEMPLATE_RE = re.compile(r"`([^`]*\{company\}[^`]*)`")
_QUERY_MARKERS = (
    "公众号",
    "官方",
    "新闻",
    "公告",
    "招标",
    "中标",
    "采购",
    "招商",
    "合作",
    "联系",
    "电话",
    "手机",
    "邮箱",
    "招聘",
    "投稿",
    "任命",
    "负责人",
    "通讯录",
)
_LEGAL_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "集团有限公司",
    "有限公司",
)


def _extract_table_templates(body: str) -> list[str]:
    """只从 Markdown 表格提取显式模板，避免把说明文字当成关键词。"""
    templates: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "{company}" not in stripped:
            continue
        templates.extend(_CODE_TEMPLATE_RE.findall(stripped))
    return _dedupe(templates)


def _dedupe(values: list[str], *, limit: int | None = None) -> list[str]:
    result = list(
        dict.fromkeys(
            re.sub(r"\s+", " ", str(value or "")).strip()
            for value in values
            if str(value or "").strip()
        )
    )
    return result[:limit] if limit else result


def _name_core(value: str) -> str:
    core = re.sub(r"\s+", "", value).strip().lower()
    for suffix in _LEGAL_SUFFIXES:
        if core.endswith(suffix):
            return core[: -len(suffix)]
    return core


def _is_plausible_search_alias(canonical_name: str, candidate: str) -> bool:
    """Conservatively reuse stored names without amplifying legacy query pollution."""
    canonical = re.sub(r"\s+", " ", canonical_name).strip()
    alias = re.sub(r"\s+", " ", candidate).strip()
    if not canonical or not alias:
        return False
    if alias == canonical:
        return True
    if len(alias) > 48 or any(token in alias for token in ("://", "/", "@")):
        return False
    if any(marker in alias for marker in _QUERY_MARKERS):
        return False
    if alias.isascii():
        return bool(
            2 <= len(alias) <= 32
            and "." not in alias
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9&._ -]*", alias)
        )

    canonical_core = _name_core(canonical)
    alias_core = _name_core(alias)
    if alias_core in canonical_core or canonical_core in alias_core:
        return True
    match = SequenceMatcher(None, canonical_core, alias_core).find_longest_match()
    ratio = SequenceMatcher(None, canonical_core, alias_core).ratio()
    return match.size >= 4 and ratio >= 0.65


def _target_search_names(doc: dict[str, Any], canonical_name: str) -> list[str]:
    from api.services.target_scan_profile import target_scan_names

    candidates = target_scan_names(
        project_target=doc,
        fallback_name=canonical_name,
    )
    profile = dict(doc.get("scan_profile") or {})
    has_authoritative_profile = bool(
        doc.get("scan_aliases") or profile.get("search_aliases")
    )
    return _dedupe(
        [
            value
            for value in candidates
            if has_authoritative_profile
            or _is_plausible_search_alias(canonical_name, str(value or ""))
        ],
        limit=int(CHANNEL_TERM_POLICIES["weixin"]["max_names"]),
    )


def _merge_generated_and_routed(
    generated: list[str],
    routed: list[str],
    *,
    limit: int,
    generated_weight: int = 3,
) -> list[str]:
    """按渠道权重合并稳定 Skill 词和 Agent 路由词。"""
    merged: list[str] = []
    seen: set[str] = set()
    generated_index = 0
    routed_index = 0

    def _append(value: str) -> None:
        normalized = re.sub(r"\s+", " ", str(value or "")).strip()
        if normalized and normalized not in seen and len(merged) < limit:
            seen.add(normalized)
            merged.append(normalized)

    while len(merged) < limit and (
        generated_index < len(generated) or routed_index < len(routed)
    ):
        for _ in range(max(1, generated_weight)):
            if generated_index >= len(generated):
                break
            _append(generated[generated_index])
            generated_index += 1
        if routed_index < len(routed):
            _append(routed[routed_index])
            routed_index += 1
    return merged


def load_keyword_skill(channel: str) -> tuple[str, str]:
    """按渠道只加载一个 Layer 2 Skill，未同步时返回空正文。"""
    slug = CHANNEL_SKILLS.get(str(channel or "").strip().lower(), "")
    if not slug:
        return "", ""
    from Sere1nGraph.graph.skills.registry import get_skill_registry

    skill = get_skill_registry().load_skill(slug)
    return (slug, skill.body) if skill else (slug, "")


def get_keyword_skill_context(channels: list[str]) -> str:
    """供 Agent prompt 渐进式披露当前场景所需的词库正文。"""
    sections: list[str] = []
    for channel in _dedupe(channels):
        slug, body = load_keyword_skill(channel)
        if body.strip():
            sections.append(f"## 已加载 Skill: {slug}\n\n{body.strip()}")
    return "\n\n".join(sections)


def get_keyword_templates(channel: str) -> list[str]:
    channel = str(channel or "").strip().lower()
    _, body = load_keyword_skill(channel)
    table_templates = _extract_table_templates(body)
    if table_templates:
        return table_templates
    templates = [
        template
        for template in _CODE_TEMPLATE_RE.findall(body)
        if template.replace("{company}", "").strip()
    ]
    return _dedupe(templates or FALLBACK_TEMPLATES.get(channel, []))


def build_channel_terms(
    *,
    channel: str,
    names: list[str],
    routed_terms: list[str] | None = None,
    limit: int = 30,
) -> list[str]:
    """合并 Agent 路由结果与 DB 词库模板，并用真实目标别名展开。"""
    policy = CHANNEL_TERM_POLICIES.get(channel, {})
    clean_names = _dedupe(names, limit=int(policy.get("max_names", 4)))
    alias_template_count = int(policy.get("alias_template_count", 10_000))
    # 微信仅在主体发现词中展开别名；联系方式词使用规范名，减少噪声。
    generated = [
        template.replace("{company}", name)
        for template_index, template in enumerate(get_keyword_templates(channel))
        for name in (
            clean_names
            if template_index < alias_template_count
            else clean_names[:1]
        )
    ]
    return _merge_generated_and_routed(
        _dedupe(generated),
        _dedupe(list(routed_terms or [])),
        limit=max(1, limit),
        generated_weight=int(policy.get("generated_weight", 3)),
    )


def build_target_channel_terms(
    *,
    names: list[str],
    routed_terms_by_channel: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    routed = routed_terms_by_channel or {}
    return {
        channel: build_channel_terms(
            channel=channel,
            names=names,
            routed_terms=routed.get(channel) or [],
        )
        for channel in CHANNEL_SKILLS
    }


def infer_collection_channel(*, app_name: str, source_link_strategy: str = "") -> str:
    app = str(app_name or "").strip().lower()
    strategy = str(source_link_strategy or "").strip().lower()
    if strategy == "wechat_copy_link" or "微信" in app or "wechat" in app:
        return "weixin"
    if "小红书" in app or "xhs" in app:
        return "xhs"
    return ""


@dataclass(slots=True)
class ResolvedSearchTerms:
    channel: str
    keywords: list[str] = field(default_factory=list)
    target_ids: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    keyword_targets: dict[str, dict[str, str]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "keywords": self.keywords,
            "target_ids": self.target_ids,
            "sources": self.sources,
            "keyword_targets": self.keyword_targets,
        }


async def resolve_project_target_terms(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str,
    target_name: str,
    channel: str,
    explicit_keywords: list[str] | None = None,
    include_direct_children: bool = True,
    max_relation_depth: int = 2,
    max_related_targets: int = 8,
    skip_completed_descendants: bool = True,
    max_keywords: int = 60,
) -> ResolvedSearchTerms:
    """解析根 Target 及其全资关联单位渠道词，供手机/浏览器任务复用。"""
    from api.dao import targets as targets_dao
    from api.services.target_scan_profile import is_scan_coverage_current

    channel = str(channel or "").strip().lower()
    keyword_limit = max(1, min(int(max_keywords or 60), 200))
    root = (
        await targets_dao.get_project_target(
            db,
            project_id=project_id,
            target_id=target_id,
        )
        if target_id
        else None
    )
    if root is None and target_name:
        global_target = await targets_dao.find_target(db, name=target_name)
        if global_target:
            target_id = str(global_target.get("target_id") or "")
            root = await targets_dao.get_project_target(
                db,
                project_id=project_id,
                target_id=target_id,
            )

    documents = [root] if root else []
    if include_direct_children and target_id:
        descendants = await targets_dao.list_project_target_descendants(
            db,
            project_id=project_id,
            root_target_id=target_id,
            max_depth=max_relation_depth,
        )
        coverage_channel = "wechat" if channel == "weixin" else channel

        def descendant_rank(item: dict[str, Any]) -> tuple[Any, ...]:
            completed = is_scan_coverage_current(item, coverage_channel)
            return (
                int(skip_completed_descendants and completed),
                -int(bool(item.get("root_domain") or item.get("root_domains"))),
                int(item.get("relation_depth") or 1),
                str(item.get("target_name") or "").casefold(),
            )

        descendants.sort(key=descendant_rank)
        if skip_completed_descendants:
            descendants = [
                item
                for item in descendants
                if not is_scan_coverage_current(item, coverage_channel)
            ]
        safe_related_limit = max(0, min(int(max_related_targets or 0), 50))
        documents.extend(descendants[:safe_related_limit])

    root_target = {
        "target_id": str((root or {}).get("target_id") or target_id or ""),
        "target_name": str((root or {}).get("target_name") or target_name or ""),
    }
    explicit = _dedupe(list(explicit_keywords or []))
    sources = ["task_explicit"] if explicit else []
    target_ids: list[str] = [root_target["target_id"]] if root_target["target_id"] else []
    term_groups: list[tuple[list[str], dict[str, str]]] = []
    for doc in documents:
        if not doc:
            continue
        doc_target_id = str(doc.get("target_id") or "")
        doc_name = str(doc.get("target_name") or "").strip()
        by_channel = doc.get("search_terms_by_channel") or {}
        stored = by_channel.get(channel) if isinstance(by_channel, dict) else []
        channel_terms = [str(term) for term in (stored or []) if str(term).strip()]
        if doc_name:
            # 历史关系中的词可能由旧 Prompt 生成。每次执行都与当前 DB Skill
            # 合并，确保词库升级立即生效，同时保留原有场景化词。
            channel_terms = build_channel_terms(
                channel=channel,
                names=_target_search_names(doc, doc_name),
                routed_terms=channel_terms,
                limit=max(keyword_limit, 30),
            )
        term_groups.append(
            (
                _dedupe(channel_terms),
                {"target_id": doc_target_id, "target_name": doc_name},
            )
        )
        if doc_target_id:
            target_ids.append(doc_target_id)
        relation_depth = int(doc.get("relation_depth") or 0)
        sources.append(
            "project_target_grandchild"
            if relation_depth >= 2
            else "project_target_child"
            if doc.get("parent_target_id")
            else "project_target"
        )

    if not documents and target_name:
        term_groups.append(
            (
                build_channel_terms(channel=channel, names=[target_name]),
                root_target,
            )
        )
        sources.append("runtime_skill")

    selected: list[str] = []
    keyword_targets: dict[str, dict[str, str]] = {}
    seen: set[str] = set()

    def _append(term: str, term_target: dict[str, str]) -> None:
        normalized = re.sub(r"\s+", " ", str(term or "")).strip()
        if not normalized or normalized in seen or len(selected) >= keyword_limit:
            return
        seen.add(normalized)
        selected.append(normalized)
        keyword_targets[normalized] = term_target

    for term in explicit:
        _append(term, root_target)

    # 在根公司和所有关联单位之间轮询取词，避免根公司词库先占满上限。
    max_group_size = max((len(group) for group, _target in term_groups), default=0)
    for term_index in range(max_group_size):
        for group, term_target in term_groups:
            if term_index < len(group):
                _append(group[term_index], term_target)
            if len(selected) >= keyword_limit:
                break
        if len(selected) >= keyword_limit:
            break

    return ResolvedSearchTerms(
        channel=channel,
        keywords=selected,
        target_ids=_dedupe(target_ids),
        sources=_dedupe(sources),
        keyword_targets=keyword_targets,
    )
