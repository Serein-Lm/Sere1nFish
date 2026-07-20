"""Registry-backed, read-only access to every project data surface.

The project page and AI Hub consume the same DAO/service layer.  This module
keeps source selection, bounded serialization, and secret redaction behind one
stable interface so new project datasets are added by registration instead of
business-flow branches.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from motor.motor_asyncio import AsyncIOMotorDatabase


@dataclass(frozen=True)
class ProjectDatasetResult:
    items: list[dict[str, Any]]
    total: int
    total_exact: bool = True


@dataclass(frozen=True)
class ProjectDataAccess:
    owner: str = ""
    is_admin: bool = False


@dataclass(frozen=True)
class ProjectDatasetQuery:
    """Shared pagination and filtering contract for every Hub dataset."""

    limit: int = 20
    offset: int = 0
    target_id: str = ""
    min_score: int = 0

    @classmethod
    def build(
        cls,
        *,
        limit: int = 20,
        offset: int = 0,
        target_id: str = "",
        min_score: int = 0,
    ) -> "ProjectDatasetQuery":
        return cls(
            limit=max(1, min(int(limit or 20), 50)),
            offset=max(0, min(int(offset or 0), 10_000)),
            target_id=str(target_id or "").strip(),
            min_score=max(0, min(int(min_score or 0), 100)),
        )


DatasetLoader = Callable[
    [AsyncIOMotorDatabase, str, int, ProjectDataAccess],
    Awaitable[ProjectDatasetResult],
]
DatasetQueryLoader = Callable[
    [AsyncIOMotorDatabase, str, ProjectDatasetQuery, ProjectDataAccess],
    Awaitable[ProjectDatasetResult],
]


@dataclass(frozen=True)
class ProjectDatasetAdapter:
    key: str
    label: str
    description: str
    loader: DatasetLoader
    query_loader: DatasetQueryLoader | None = None
    filters: tuple[str, ...] = ()

    async def load(
        self,
        db: AsyncIOMotorDatabase,
        project_id: str,
        query: ProjectDatasetQuery,
        access: ProjectDataAccess,
    ) -> ProjectDatasetResult:
        requested_filters = {
            "target_id" for value in [query.target_id] if value
        } | ({"min_score"} if query.min_score else set())
        unsupported = requested_filters - set(self.filters)
        if unsupported:
            raise ValueError(
                f"数据源 {self.key!r} 不支持过滤条件: {', '.join(sorted(unsupported))}"
            )
        if self.query_loader is not None:
            return await self.query_loader(db, project_id, query, access)

        fetch_limit = min(10_050, query.offset + query.limit)
        result = await self.loader(db, project_id, fetch_limit, access)
        return ProjectDatasetResult(
            items=result.items[query.offset : query.offset + query.limit],
            total=result.total,
            total_exact=result.total_exact,
        )


def _list_result(items: list[dict[str, Any]], limit: int) -> ProjectDatasetResult:
    return ProjectDatasetResult(
        items=items,
        total=len(items),
        total_exact=len(items) < limit,
    )


async def _web_tagging(db, project_id, limit, _access):
    from api.dao import web_tagging

    items, total = await web_tagging.list_web_tagging_results(
        db, project_id=project_id, limit=limit
    )
    return ProjectDatasetResult(items, total)


def _project_record_loader(source: str) -> DatasetLoader:
    async def _load(db, project_id, limit, _access):
        from api.dao import project_records

        items, total = await project_records.list_project_records(
            db, source, project_id, limit=limit
        )
        return ProjectDatasetResult(items, total)

    return _load


async def _assets(db, project_id, limit, _access):
    from api.dao import fofa_assets

    items, total = await asyncio.gather(
        fofa_assets.query_assets(db, project_id, limit=limit),
        fofa_assets.count_assets(db, project_id),
    )
    return ProjectDatasetResult(items, total)


async def _xhs_notes(db, project_id, limit, _access):
    from api.dao import xhs

    items, total = await xhs.list_notes(
        db, project_id=project_id, limit=limit, sort_by="relevance"
    )
    return ProjectDatasetResult(items, total)


async def _xhs_search_tasks(db, project_id, limit, _access):
    from api.dao import xhs

    items, total = await xhs.list_search_tasks(
        db, project_id=project_id, limit=limit
    )
    return ProjectDatasetResult(items, total)


async def _xhs_note_details(db, project_id, limit, _access):
    from api.dao import xhs

    items = await xhs.list_note_details(db, project_id, limit=limit)
    return _list_result(items, limit)


async def _xhs_profiles(db, project_id, limit, _access):
    from api.dao import xhs

    items, total = await xhs.list_profiles(db, project_id, limit=limit)
    return ProjectDatasetResult(items, total)


async def _douyin_search(db, project_id, limit, _access):
    from api.dao import douyin

    items, total = await douyin.list_search_results(db, project_id, limit=limit)
    return ProjectDatasetResult(items, total)


async def _douyin_tagged(db, project_id, limit, _access):
    from api.dao import douyin

    items, total = await douyin.list_tagged_results(db, project_id, limit=limit)
    return ProjectDatasetResult(items, total)


async def _douyin_profiles(db, project_id, limit, _access):
    from api.dao import douyin

    items, total = await douyin.list_profiles(db, project_id, limit=limit)
    return ProjectDatasetResult(items, total)


async def _wechat_records(db, project_id, limit, _access):
    from api.dao import mobile_collect

    items, total = await mobile_collect.list_records(
        db, project_id=project_id, limit=limit
    )
    return ProjectDatasetResult(items, total)


async def _mobile_collect_tasks(db, project_id, limit, _access):
    from api.dao import mobile_collect

    items = await mobile_collect.list_task_defs(
        db, project_id=project_id, limit=limit
    )
    return _list_result(items, limit)


async def _source_documents(db, project_id, limit, _access):
    from api.dao import source_documents

    items, total = await source_documents.list_project_documents(
        db, project_id, limit=limit
    )
    return ProjectDatasetResult(items, total)


async def _targets(db, project_id, limit, _access):
    from api.dao import targets

    items = (await targets.list_project_targets(db, project_id))[:limit]
    return _list_result(items, limit)


async def _mobile_profiles(db, project_id, limit, _access):
    from api.dao import contact_profiles

    items = await contact_profiles.list_profiles(
        db, project_id=project_id, limit=limit
    )
    return _list_result(items, limit)


async def _mobile_observations(db, project_id, limit, _access):
    from api.dao import mobile_profile_observations

    items = await mobile_profile_observations.list_observations(
        db, project_id=project_id, limit=limit
    )
    return _list_result(items, limit)


async def _mobile_screenshots(db, project_id, limit, _access):
    from api.dao import mobile_artifacts

    items = await mobile_artifacts.list_screenshots(
        db, project_id=project_id, limit=limit
    )
    return _list_result(items, limit)


async def _mobile_operations(db, project_id, limit, _access):
    from api.dao import mobile_artifacts

    items = await mobile_artifacts.list_operations(
        db, project_id=project_id, limit=limit
    )
    return _list_result(items, limit)


async def _mobile_sessions(db, project_id, limit, _access):
    from api.dao import auto_chat_sessions

    items = await auto_chat_sessions.list_sessions(
        db, project_id=project_id, limit=limit
    )
    return _list_result(items, limit)


async def _scholar_contacts(db, project_id, limit, _access):
    from api.dao import scholar_contact

    items, total = await scholar_contact.query_contacts(
        db, project_id, limit=limit
    )
    return ProjectDatasetResult(items, total)


async def _scholar_articles(db, project_id, limit, _access):
    from api.dao import scholar_contact

    items, total = await scholar_contact.query_articles(
        db, project_id, limit=limit
    )
    return ProjectDatasetResult(items, total)


async def _bidding_records(db, project_id, limit, _access):
    from api.dao import bidding

    items, total = await bidding.query_records(
        db,
        project_id=project_id,
        limit=limit,
    )
    return ProjectDatasetResult(items, total)


async def _tasks(db, project_id, limit, _access):
    from api.dao import tasks

    items, total = await tasks.list_tasks(db, project_id, limit=limit)
    return ProjectDatasetResult(items, total)


async def _task_logs(db, project_id, limit, _access):
    from api.dao import task_logs

    items, total = await task_logs.query_logs(
        db, project_id=project_id, limit=limit
    )
    return ProjectDatasetResult(items, total)


async def _findings(db, project_id, limit, _access):
    from api.dao import findings

    items, total = await findings.query_findings(
        db, project_id, limit=limit, sort="score_desc"
    )
    return ProjectDatasetResult(items, total)


async def _artifacts(db, project_id, limit, access: ProjectDataAccess):
    from api.dao import artifacts

    if not access.owner:
        return ProjectDatasetResult([], 0)

    items = await artifacts.list_artifacts(
        db,
        owner="" if access.is_admin else access.owner,
        project_id=project_id,
        limit=limit,
    )
    return _list_result(items, limit)


async def _query_website_records(db, project_id, query, _access):
    from api.services.website_records import list_website_records

    items, total = await list_website_records(
        db,
        project_id=project_id,
        target_id=query.target_id,
        skip=query.offset,
        limit=query.limit,
    )
    return ProjectDatasetResult(items, total)


async def _query_assets(db, project_id, query, _access):
    from api.dao import fofa_assets

    items, total = await asyncio.gather(
        fofa_assets.query_assets(
            db,
            project_id,
            target_id=query.target_id,
            limit=query.limit,
            skip=query.offset,
        ),
        fofa_assets.count_assets(
            db,
            project_id,
            target_id=query.target_id,
        ),
    )
    return ProjectDatasetResult(items, total)


async def _query_xhs_notes(db, project_id, query, _access):
    from api.dao import xhs

    items, total = await xhs.list_notes(
        db,
        project_id=project_id,
        target_id=query.target_id or None,
        limit=query.limit,
        skip=query.offset,
        sort_by="relevance",
    )
    return ProjectDatasetResult(items, total)


async def _query_xhs_profiles(db, project_id, query, _access):
    from api.dao import xhs

    items, total = await xhs.list_profiles(
        db,
        project_id,
        target_id=query.target_id or None,
        limit=query.limit,
        skip=query.offset,
    )
    return ProjectDatasetResult(items, total)


async def _query_wechat_records(db, project_id, query, _access):
    from api.dao import mobile_collect

    items, total = await mobile_collect.list_records(
        db,
        project_id=project_id,
        target_id=query.target_id or None,
        archived_only=True,
        min_score=query.min_score if query.min_score > 0 else None,
        skip=query.offset,
        limit=query.limit,
    )
    return ProjectDatasetResult(items, total)


async def _query_source_documents(db, project_id, query, _access):
    from api.dao import source_documents

    if query.target_id:
        items, total = await source_documents.list_target_documents(
            db,
            query.target_id,
            project_id=project_id,
            skip=query.offset,
            limit=query.limit,
        )
    else:
        items, total = await source_documents.list_project_documents(
            db,
            project_id,
            skip=query.offset,
            limit=query.limit,
        )
    return ProjectDatasetResult(items, total)


async def _query_targets(db, project_id, query, _access):
    from api.services.targets import list_project_target_summaries

    items = await list_project_target_summaries(db, project_id, compact=True)
    if query.target_id:
        items = [item for item in items if item.get("target_id") == query.target_id]
    total = len(items)
    return ProjectDatasetResult(
        items[query.offset : query.offset + query.limit],
        total,
    )


async def _query_scholar_contacts(db, project_id, query, _access):
    from api.dao import scholar_contact

    items, total = await scholar_contact.query_contacts(
        db,
        project_id,
        target_id=query.target_id,
        limit=query.limit,
        skip=query.offset,
    )
    return ProjectDatasetResult(items, total)


async def _query_bidding_records(db, project_id, query, _access):
    from api.services.bidding_records import list_project_bidding_records

    items, total = await list_project_bidding_records(
        db,
        project_id=project_id,
        target_id=query.target_id,
        limit=query.limit,
        skip=query.offset,
    )
    return ProjectDatasetResult(items, total)


async def _query_findings(db, project_id, query, _access):
    from api.dao import findings

    items, total = await findings.query_findings(
        db,
        project_id,
        target_id=query.target_id,
        min_score=query.min_score,
        limit=query.limit,
        skip=query.offset,
        sort="score_desc",
    )
    return ProjectDatasetResult(items, total)


def _record_adapter(key: str, label: str, description: str) -> ProjectDatasetAdapter:
    return ProjectDatasetAdapter(key, label, description, _project_record_loader(key))


PROJECT_DATASETS: dict[str, ProjectDatasetAdapter] = {
    adapter.key: adapter
    for adapter in (
        ProjectDatasetAdapter(
            "web_tagging",
            "网站",
            "已排除第三方/通用开源页面并合并 HTTP/HTTPS 的网站分析结果",
            _web_tagging,
            query_loader=_query_website_records,
            filters=("target_id",),
        ),
        _record_adapter("url_scan_tasks", "旧版 URL 任务", "兼容保留的 URL 扫描任务"),
        _record_adapter("url_scan_results", "旧版 URL 结果", "兼容保留的 URL 扫描结果"),
        _record_adapter("url_scan_findings", "旧版 URL 发现", "兼容保留的 URL 扫描发现"),
        _record_adapter(
            "url_scan_copywritings", "旧版 URL 话术", "兼容保留的 URL 扫描话术"
        ),
        ProjectDatasetAdapter(
            "assets",
            "资产情报",
            "FOFA/Hunter 资产与存活状态",
            _assets,
            query_loader=_query_assets,
            filters=("target_id",),
        ),
        _record_adapter("company_meta", "公司元信息", "规范化公司名、别名和根域名"),
        _record_adapter("company_scans", "综合公司扫描", "公司全流程扫描的阶段结果"),
        ProjectDatasetAdapter(
            "xhs_search_tasks",
            "小红书搜索任务",
            "搜索词、状态和采集统计",
            _xhs_search_tasks,
        ),
        ProjectDatasetAdapter(
            "xhs_notes",
            "小红书笔记",
            "笔记命中与关注度",
            _xhs_notes,
            query_loader=_query_xhs_notes,
            filters=("target_id",),
        ),
        ProjectDatasetAdapter(
            "xhs_note_details", "小红书正文", "笔记正文和结构化研判", _xhs_note_details
        ),
        ProjectDatasetAdapter(
            "xhs_profiles",
            "小红书画像",
            "用户身份与公司研判",
            _xhs_profiles,
            query_loader=_query_xhs_profiles,
            filters=("target_id",),
        ),
        ProjectDatasetAdapter(
            "douyin_search", "抖音搜索", "视频搜索命中", _douyin_search
        ),
        ProjectDatasetAdapter(
            "douyin_tagged", "抖音打标", "潜在员工等打标结果", _douyin_tagged
        ),
        ProjectDatasetAdapter(
            "douyin_profiles", "抖音画像", "用户画像与优先级", _douyin_profiles
        ),
        ProjectDatasetAdapter(
            "wechat_records",
            "公众号文章",
            "已通过主体审核并完成浏览器原文归档的公众号文章和联系方式上下文",
            _wechat_records,
            query_loader=_query_wechat_records,
            filters=("target_id", "min_score"),
        ),
        ProjectDatasetAdapter(
            "mobile_collect_tasks",
            "手机采集定义",
            "公众号等手机采集任务配置与状态",
            _mobile_collect_tasks,
        ),
        ProjectDatasetAdapter(
            "source_documents",
            "来源原文",
            "永久保存的文章原文、图片分析、稳定文档 ID 和版本证据",
            _source_documents,
            query_loader=_query_source_documents,
            filters=("target_id",),
        ),
        ProjectDatasetAdapter(
            "bidding_records",
            "招投标公告",
            "仅含有效参与方联系方式、公告简介、原文和附件引用的招投标记录",
            _bidding_records,
            query_loader=_query_bidding_records,
            filters=("target_id",),
        ),
        ProjectDatasetAdapter(
            "targets",
            "Target 看板",
            "项目 Target、任务完整度、高分 Finding 和各模块数据量",
            _targets,
            query_loader=_query_targets,
            filters=("target_id",),
        ),
        ProjectDatasetAdapter(
            "mobile_profiles", "手机画像", "联系人画像快照", _mobile_profiles
        ),
        ProjectDatasetAdapter(
            "mobile_observations",
            "画像观察",
            "画像证据和增量观察",
            _mobile_observations,
        ),
        ProjectDatasetAdapter(
            "mobile_screenshots",
            "手机截图",
            "截图元数据和 OSS 对象引用",
            _mobile_screenshots,
        ),
        ProjectDatasetAdapter(
            "mobile_operations", "手机操作", "设备操作审计日志", _mobile_operations
        ),
        ProjectDatasetAdapter(
            "mobile_sessions", "自动聊天", "自动聊天会话快照", _mobile_sessions
        ),
        ProjectDatasetAdapter(
            "scholar_contacts",
            "学者联系",
            "仅含公开邮箱与可访问原文的学者、单位和对应作者信息",
            _scholar_contacts,
            query_loader=_query_scholar_contacts,
            filters=("target_id",),
        ),
        ProjectDatasetAdapter(
            "scholar_articles",
            "学术文章原始索引",
            "采集过程文章索引；有效联系分析应优先读取 scholar_contacts",
            _scholar_articles,
        ),
        ProjectDatasetAdapter("tasks", "任务", "项目任务状态与进度", _tasks),
        ProjectDatasetAdapter(
            "task_logs", "任务日志", "任务运行日志与异常", _task_logs
        ),
        ProjectDatasetAdapter(
            "findings",
            "Findings",
            "统一发现与关注度",
            _findings,
            query_loader=_query_findings,
            filters=("target_id", "min_score"),
        ),
        _record_adapter("copywritings", "统一话术", "Finding 关联话术、载荷和异议处理"),
        _record_adapter("profiles", "统一画像", "Finding 关联的结构化目标画像"),
        _record_adapter(
            "profile_copywritings", "画像话术", "高分画像直接生成的话术记录"
        ),
        _record_adapter(
            "token_usage", "Token 明细", "项目 AI 调用的模型、阶段、耗时和费用"
        ),
        ProjectDatasetAdapter(
            "artifacts", "AI 产物", "项目关联的可下载 AI 产物", _artifacts
        ),
    )
}


_SENSITIVE_KEYS = {
    "password",
    "secret",
    "client_secret",
    "access_token",
    "refresh_token",
    "authorization",
    "api_key",
    "access_key_id",
    "access_key_secret",
    "private_key",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "file_path",
    "local_path",
    "object_key",
    "bucket",
}

_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "ossaccesskeyid",
    "security-token",
    "signature",
    "token",
    "x-amz-credential",
    "x-amz-security-token",
    "x-amz-signature",
    "x-oss-credential",
    "x-oss-security-token",
    "x-oss-signature",
}


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key or "").strip().lower()
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith(
            ("_password", "_secret", "_token", "_api_key", "_private_key")
        )
    )


def _redact_signed_url(value: str) -> str:
    if not value.lower().startswith(("http://", "https://")) or "?" not in value:
        return value
    try:
        parsed = urlsplit(value)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        return value
    changed = False
    redacted: list[tuple[str, str]] = []
    for key, item in pairs:
        normalized = key.strip().lower()
        sensitive = (
            normalized in _SENSITIVE_QUERY_KEYS
            or "signature" in normalized
            or normalized.endswith("credential")
            or normalized.endswith("token")
        )
        changed = changed or sensitive
        redacted.append((key, "[redacted]" if sensitive else item))
    if not changed:
        return value
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(redacted), parsed.fragment)
    )


def _bounded_value(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = 6,
    max_fields: int = 60,
    max_items: int = 30,
    max_string: int = 5_000,
) -> Any:
    if depth >= max_depth:
        return "<depth-limited>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_fields:
                result["_truncated_fields"] = len(value) - index
                break
            text_key = str(key)
            result[text_key] = (
                "<redacted>"
                if _is_sensitive_key(text_key)
                else _bounded_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_fields=max_fields,
                    max_items=max_items,
                    max_string=max_string,
                )
            )
        return result
    if isinstance(value, (list, tuple, set)):
        values = list(value)
        bounded = [
            _bounded_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_fields=max_fields,
                max_items=max_items,
                max_string=max_string,
            )
            for item in values[:max_items]
        ]
        if len(values) > len(bounded):
            bounded.append(f"<truncated {len(values) - len(bounded)} items>")
        return bounded
    if isinstance(value, bytes):
        return f"<bytes {len(value)}>"
    if isinstance(value, str):
        value = _redact_signed_url(value)
        return value if len(value) <= max_string else value[:max_string] + "...<truncated>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _bounded_items(
    items: list[dict[str, Any]], *, max_chars: int = 48_000
) -> tuple[list[Any], bool]:
    output: list[Any] = []
    used = 0
    truncated = False
    for raw in items:
        item: Any = {}
        encoded = ""
        for profile in (
            {"max_depth": 6, "max_fields": 60, "max_items": 30, "max_string": 5_000},
            {"max_depth": 6, "max_fields": 40, "max_items": 12, "max_string": 1_200},
            {"max_depth": 5, "max_fields": 28, "max_items": 6, "max_string": 400},
            {"max_depth": 4, "max_fields": 18, "max_items": 3, "max_string": 160},
        ):
            item = _bounded_value(raw, **profile)
            encoded = json.dumps(item, ensure_ascii=False, default=str)
            if len(encoded) <= 12_000:
                break
        raw_size = len(json.dumps(raw, ensure_ascii=False, default=str))
        if isinstance(item, dict) and len(encoded) < raw_size:
            item = {**item, "_truncated": True}
            encoded = json.dumps(item, ensure_ascii=False, default=str)
        if output and used + len(encoded) > max_chars:
            truncated = True
            break
        output.append(item)
        used += len(encoded)
    return output, truncated or len(output) < len(items)


def dataset_catalog() -> list[dict[str, Any]]:
    return [
        {
            "source": adapter.key,
            "label": adapter.label,
            "description": adapter.description,
            "filters": ["offset", *adapter.filters],
        }
        for adapter in PROJECT_DATASETS.values()
    ]


async def inspect_project_datasets(
    db: AsyncIOMotorDatabase,
    project_id: str,
    *,
    owner: str = "",
    is_admin: bool = False,
) -> dict[str, Any]:
    from api.dao import projects

    project = await projects.get_project(db, project_id)
    if not project:
        raise LookupError(f"项目不存在: {project_id}")
    adapters = list(PROJECT_DATASETS.values())
    access = ProjectDataAccess(owner=owner, is_admin=is_admin)
    query = ProjectDatasetQuery.build(limit=1)
    results = await asyncio.gather(
        *(adapter.load(db, project_id, query, access) for adapter in adapters),
        return_exceptions=True,
    )
    sources: list[dict[str, Any]] = []
    for adapter, result in zip(adapters, results):
        item: dict[str, Any] = {
            "source": adapter.key,
            "label": adapter.label,
            "description": adapter.description,
            "filters": ["offset", *adapter.filters],
        }
        if isinstance(result, Exception):
            item.update(ok=False, count=0, error=str(result)[:300])
        else:
            item.update(
                ok=True,
                count=result.total,
                count_exact=result.total_exact,
                available=bool(result.items),
            )
        sources.append(item)
    return {
        "project_id": project_id,
        "project_name": project.get("name") or "",
        "sources": sources,
    }


async def read_project_dataset(
    db: AsyncIOMotorDatabase,
    project_id: str,
    source: str,
    *,
    limit: int = 20,
    offset: int = 0,
    target_id: str = "",
    min_score: int = 0,
    owner: str = "",
    is_admin: bool = False,
) -> dict[str, Any]:
    from api.dao import projects

    project = await projects.get_project(db, project_id)
    if not project:
        raise LookupError(f"项目不存在: {project_id}")
    source_key = str(source or "").strip().lower()
    adapter = PROJECT_DATASETS.get(source_key)
    if adapter is None:
        raise ValueError(
            f"未知项目数据源 {source_key!r}；可用值：{', '.join(PROJECT_DATASETS)}"
        )
    query = ProjectDatasetQuery.build(
        limit=limit,
        offset=offset,
        target_id=target_id,
        min_score=min_score,
    )
    access = ProjectDataAccess(owner=owner, is_admin=is_admin)
    result = await adapter.load(db, project_id, query, access)
    items, truncated = _bounded_items(result.items)
    consumed = len(items)
    if result.total_exact:
        has_more = query.offset + consumed < result.total
    else:
        has_more = consumed >= query.limit or len(result.items) > consumed
    return {
        "project_id": project_id,
        "project_name": project.get("name") or "",
        "source": adapter.key,
        "label": adapter.label,
        "description": adapter.description,
        "total": result.total,
        "total_exact": result.total_exact,
        "offset": query.offset,
        "limit": query.limit,
        "returned": len(items),
        "has_more": has_more,
        "next_offset": query.offset + consumed if has_more else None,
        "filters": {
            "target_id": query.target_id or None,
            "min_score": query.min_score or None,
        },
        "truncated": truncated,
        "items": items,
    }
