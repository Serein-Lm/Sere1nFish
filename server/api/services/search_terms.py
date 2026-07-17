"""跨采集渠道的目标搜索词统一服务。

词库正文来自数据库同步后的 Skill Registry。调用侧只声明渠道、目标和显式词，
本服务负责渐进式加载对应 Skill、展开模板、持久化和项目关系聚合。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase


CHANNEL_SKILLS = {
    "xhs": "xhs-keywords",
    "weixin": "wechat-keywords",
}

FALLBACK_TEMPLATES = {
    "xhs": [
        "{company} 实习",
        "{company} 内推",
        "{company} 招聘",
        "{company} 工作体验",
    ],
    "weixin": [
        "{company} 招标",
        "{company} 采购",
        "{company} 招商",
        "{company} 合作",
        "{company} 联系方式",
        "{company} 公众号",
    ],
}

_CODE_TEMPLATE_RE = re.compile(r"`([^`]*\{company\}[^`]*)`")


def _dedupe(values: list[str], *, limit: int | None = None) -> list[str]:
    result = list(
        dict.fromkeys(
            re.sub(r"\s+", " ", str(value or "")).strip()
            for value in values
            if str(value or "").strip()
        )
    )
    return result[:limit] if limit else result


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
    clean_names = _dedupe(names, limit=4)
    generated = [
        template.replace("{company}", name)
        for name in clean_names
        for template in get_keyword_templates(channel)
    ]
    return _dedupe([*(routed_terms or []), *generated], limit=limit)


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
    max_keywords: int = 60,
) -> ResolvedSearchTerms:
    """解析根 Target 及其第一层控股单位的渠道词，供手机/浏览器任务复用。"""
    from api.dao import targets as targets_dao

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
        documents.extend(
            await targets_dao.list_project_target_children(
                db,
                project_id=project_id,
                parent_target_id=target_id,
                relation_depth=1,
            )
        )

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
        if not channel_terms and doc_name:
            channel_terms = build_channel_terms(channel=channel, names=[doc_name])
        term_groups.append(
            (
                _dedupe(channel_terms),
                {"target_id": doc_target_id, "target_name": doc_name},
            )
        )
        if doc_target_id:
            target_ids.append(doc_target_id)
        sources.append(
            "project_target_child" if doc.get("parent_target_id") else "project_target"
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

    # 在根公司和所有子单位之间轮询取词，避免根公司词库先占满上限。
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
