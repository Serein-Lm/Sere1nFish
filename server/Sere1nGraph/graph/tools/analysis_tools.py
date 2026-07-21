"""
只读数据分析工具 — 供 AI 中枢 ReAct Agent 调用。

Agent 在分析/生成话术前，可用它们拉取平台内的统计与发现数据：
项目综合看板、findings 总览与明细、全局观测概览，并支持多项目 batch 并发分析。

设计原则：
- 全部只读，不写库、不触发任务；底层聚合收敛在 api.services.analytics 与既有 DAO。
- 本文件仅做同步 tool 封装（通过 _run_coro_sync 调用 async service/DAO）。
- 返回结构化摘要文本 + 稳定领域标识（project_id/finding_id），并对可跳转实体内嵌引用标记。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain.tools import tool

from . import _refs
from .builtin import _run_coro_sync


def _parse_ids(raw: str) -> list[str]:
    """解析逗号分隔或 JSON 数组形式的 id 列表，去重保序。"""
    raw = (raw or "").strip()
    if not raw:
        return []
    ids: list[str] = []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                ids = [str(x).strip() for x in parsed]
        except (ValueError, TypeError):
            ids = []
    if not ids:
        ids = [seg.strip() for seg in raw.split(",")]
    seen: set[str] = set()
    ordered: list[str] = []
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            ordered.append(i)
    return ordered


def _format_dashboard(project_id: str, d: dict[str, Any]) -> list[str]:
    findings = d.get("findings") or {}
    dist = findings.get("score_distribution") or {}
    tasks = d.get("tasks") or {}
    counts = d.get("data_counts") or {}
    token = d.get("token_usage") or {}

    lines = [f"项目 {project_id}："]
    lines.append(
        f"- 发现：共 {findings.get('total', 0)} 条"
        f"（高 {dist.get('high', 0)} / 中 {dist.get('medium', 0)} / 低 {dist.get('low', 0)}）"
    )
    by_source = findings.get("by_source") or {}
    if by_source:
        lines.append("- 来源分布：" + "、".join(f"{k}:{v}" for k, v in by_source.items()))
    lines.append(
        f"- 任务：共 {tasks.get('total', 0)} 个，状态 "
        + "、".join(f"{k}:{v}" for k, v in (tasks.get('by_status') or {}).items())
    )
    data_seg = "、".join(f"{k}:{v}" for k, v in counts.items() if v)
    if data_seg:
        lines.append("- 数据量：" + data_seg)
    if token:
        lines.append(
            f"- Token：调用 {token.get('total_calls', 0)} 次，"
            f"输入 {token.get('total_input_tokens', 0)}，输出 {token.get('total_output_tokens', 0)}，"
            f"成本 ¥{token.get('total_cost_yuan', 0)}"
        )
    top = d.get("top_findings") or []
    if top:
        lines.append("- 高价值目标 Top：")
        for f in top[:10]:
            label = f.get("label") or f.get("value") or f.get("finding_id") or ""
            score = f.get("attention_score")
            ref = _refs.finding_ref(f.get("finding_id", ""), label)
            seg = f"  · {label}"
            if score is not None:
                seg += f"（关注度 {score}）"
            if ref:
                seg += f" {ref}"
            lines.append(seg)
    return lines


@tool(
    "get_project_dashboard",
    description=(
        "获取单个项目的综合看板数据（只读）：发现统计与评分分布、任务状态、"
        "各数据源采集量、Token 消耗、高价值目标 Top10。用于对项目做整体态势分析。"
        "参数：project_id（必填）。"
    ),
)
def get_project_dashboard(project_id: str) -> str:
    """拉取项目综合看板并返回结构化摘要。"""
    project_id = (project_id or "").strip()
    if not project_id:
        return "请提供 project_id。"

    async def _load() -> dict[str, Any]:
        from api.db.mongodb import get_db
        from api.services import analytics

        return await analytics.resolve_project_dashboard(get_db(), project_id)

    try:
        d = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"获取项目看板失败：{exc}"
    return "\n".join(_format_dashboard(project_id, d))


@tool(
    "batch_get_project_dashboards",
    description=(
        "批量并发获取多个项目的综合看板数据（只读），用于跨项目对比分析。"
        "参数：project_ids（必填，逗号分隔或 JSON 数组字符串，最多 10 个）。"
    ),
)
def batch_get_project_dashboards(project_ids: str) -> str:
    """并发拉取多个项目看板并返回对比摘要。"""
    ids = _parse_ids(project_ids)[:10]
    if not ids:
        return "请提供 project_ids（逗号分隔或 JSON 数组）。"

    async def _load() -> list[tuple[str, Any]]:
        from api.db.mongodb import get_db
        from api.services import analytics

        db = get_db()
        results = await asyncio.gather(
            *[analytics.resolve_project_dashboard(db, pid) for pid in ids],
            return_exceptions=True,
        )
        return list(zip(ids, results))

    try:
        pairs = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"批量获取项目看板失败：{exc}"

    blocks: list[str] = [f"共分析 {len(pairs)} 个项目："]
    for pid, res in pairs:
        if isinstance(res, Exception):
            blocks.append(f"项目 {pid}：解析失败（{res}）")
        else:
            blocks.append("\n".join(_format_dashboard(pid, res)))
    return "\n\n".join(blocks)


@tool(
    "get_findings_summary",
    description=(
        "获取单个项目的 findings 总览统计（只读）：总数、按来源/类型分布、评分高中低分布。"
        "参数：project_id（必填）。"
    ),
)
def get_findings_summary(project_id: str) -> str:
    """拉取项目 findings 总览统计。"""
    project_id = (project_id or "").strip()
    if not project_id:
        return "请提供 project_id。"

    async def _load() -> dict[str, Any]:
        from api.dao import findings as findings_dao
        from api.db.mongodb import get_db

        return await findings_dao.get_findings_summary(get_db(), project_id)

    try:
        s = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"获取 findings 总览失败：{exc}"

    dist = s.get("score_distribution") or {}
    lines = [
        f"项目 {project_id} findings 总览：",
        f"- 总数：{s.get('total', 0)}",
        f"- 评分分布：高 {dist.get('high', 0)} / 中 {dist.get('medium', 0)} / 低 {dist.get('low', 0)}",
    ]
    by_source = s.get("by_source") or {}
    if by_source:
        lines.append("- 来源：" + "、".join(f"{k}:{v}" for k, v in by_source.items()))
    by_type = s.get("by_type") or {}
    if by_type:
        lines.append("- 类型：" + "、".join(f"{k}:{v}" for k, v in by_type.items()))
    return "\n".join(lines)


@tool(
    "query_findings",
    description=(
        "分页查询某项目的 findings 明细（只读），可按 Target、来源、类型、最低关注度筛选并排序。"
        "用于定位高价值目标或特定来源的发现。参数：project_id（必填）；"
        "target_id（可选，按项目 Target 精确过滤）；"
        "source（可选，如 web_tagging/xhs/douyin/mobile）；finding_type（可选）；"
        "min_score（可选，0-100）；sort（score_desc/score_asc/time_desc，默认 score_desc）；"
        "limit（默认 10，上限 50）；offset（默认 0，用于分页）。"
    ),
)
def query_findings(
    project_id: str,
    target_id: str = "",
    source: str = "",
    finding_type: str = "",
    min_score: int = 0,
    sort: str = "score_desc",
    limit: int = 10,
    offset: int = 0,
) -> str:
    """分页查询 findings 明细并返回带跳转引用的摘要。"""
    project_id = (project_id or "").strip()
    if not project_id:
        return "请提供 project_id。"

    async def _load() -> tuple[list[dict[str, Any]], int]:
        from api.dao import findings as findings_dao
        from api.db.mongodb import get_db

        return await findings_dao.query_findings(
            get_db(),
            project_id=project_id,
            target_id=(target_id or "").strip(),
            source=source,
            finding_type=finding_type,
            min_score=max(0, min(int(min_score or 0), 100)),
            sort=sort or "score_desc",
            limit=max(1, min(int(limit or 10), 50)),
            skip=max(0, min(int(offset or 0), 10_000)),
        )

    try:
        items, total = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"查询 findings 失败：{exc}"

    if not items:
        return f"项目 {project_id} 未查询到符合条件的 findings。"

    bounded_offset = max(0, min(int(offset or 0), 10_000))
    lines = [
        f"项目 {project_id} 命中 {total} 条，"
        f"返回第 {bounded_offset + 1}-{bounded_offset + len(items)} 条："
    ]
    for idx, f in enumerate(items, 1):
        label = f.get("label") or f.get("value") or f.get("finding_id") or ""
        score = f.get("attention_score")
        src = f.get("source") or ""
        ftype = f.get("type") or ""
        ref = _refs.finding_ref(f.get("finding_id", ""), label)
        seg = f"{idx}. {label}"
        meta = "／".join(x for x in (src, ftype) if x)
        if meta:
            seg += f"（{meta}）"
        if score is not None:
            seg += f" 关注度 {score}"
        if ref:
            seg += f" {ref}"
        lines.append(seg)
    next_offset = bounded_offset + len(items)
    if next_offset < total:
        lines.append(f"- 仍有更多结果；下一页 offset={next_offset}。")
    return "\n".join(lines)


@tool(
    "query_target_intelligence",
    description=(
        "按全局 target_id 查询该公司/机构关联的泄漏、Finding、来源证据和已有话术。"
        "适合回答‘某个 Target 有哪些泄漏/联系方式/风险、已经有哪些话术’。"
        "会跨项目聚合；如只看一个项目可传 project_id。参数：target_id（必填）；"
        "project_id（可选）；min_score（0-100）；limit（默认10，上限30）；"
        "offset（分页偏移）。返回 finding_id，需更完整证据时继续调用 get_finding_detail，"
        "需要单条完整话术时继续调用 get_finding_copywriting。"
    ),
)
def query_target_intelligence(
    target_id: str,
    project_id: str = "",
    min_score: int = 0,
    limit: int = 10,
    offset: int = 0,
) -> str:
    """Query one Target's findings and existing copywriting across projects."""
    target_id = str(target_id or "").strip()
    if not target_id:
        return "请提供 target_id。"

    async def _load() -> tuple[dict[str, Any] | None, list[dict[str, Any]], int]:
        from api.dao import findings as findings_dao
        from api.dao import targets as targets_dao
        from api.db.mongodb import get_db

        db = get_db()
        target, result = await asyncio.gather(
            targets_dao.get_target(db, target_id),
            findings_dao.query_target_findings_with_copywriting(
                db,
                target_id,
                project_id=str(project_id or "").strip(),
                min_score=max(0, min(int(min_score or 0), 100)),
                limit=max(1, min(int(limit or 10), 30)),
                skip=max(0, min(int(offset or 0), 10_000)),
            ),
        )
        items, total = result
        return target, items, total

    try:
        target, items, total = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"查询 Target 情报失败：{exc}"

    target_name = str(
        (target or {}).get("canonical_name")
        or (target or {}).get("display_name")
        or (target or {}).get("name")
        or target_id
    )
    if not items:
        return f"Target {target_name}（{target_id}）暂无符合条件的 Finding。"

    bounded_offset = max(0, min(int(offset or 0), 10_000))
    lines = [
        f"Target：{target_name}（target_id={target_id}）",
        f"命中 {total} 条，返回第 {bounded_offset + 1}-{bounded_offset + len(items)} 条：",
    ]
    for index, finding in enumerate(items, start=1):
        finding_id = str(finding.get("finding_id") or "")
        label = str(finding.get("label") or finding.get("value") or finding_id or "未命名 Finding")
        source_type = "／".join(
            str(value) for value in (finding.get("source"), finding.get("type")) if value
        )
        score = finding.get("attention_score")
        line = f"{index}. {label}"
        if source_type:
            line += f"（{source_type}）"
        if score is not None:
            line += f" 关注度 {score}"
        ref = _refs.finding_ref(finding_id, label)
        if ref:
            line += f" {ref}"
        lines.append(line)
        if finding.get("attention_reason"):
            lines.append(f"   - 关注原因：{str(finding['attention_reason'])[:300]}")
        context = finding.get("context") or finding.get("evidence")
        if context:
            context_text = (
                json.dumps(context, ensure_ascii=False, default=str)
                if isinstance(context, (dict, list))
                else str(context)
            )
            lines.append(f"   - 证据上下文：{context_text[:600]}")
        if finding.get("url"):
            lines.append(f"   - 来源：{finding['url']}")
        copywriting = finding.get("copywriting")
        if copywriting:
            payload = {
                key: copywriting.get(key)
                for key in (
                    "scenario",
                    "scripts",
                    "objections",
                    "payload",
                    "psychology_strategy",
                )
                if copywriting.get(key)
            }
            preview = json.dumps(payload or copywriting, ensure_ascii=False, default=str)
            lines.append(f"   - 已有话术：{preview[:1_600]}")
        else:
            lines.append("   - 已有话术：无")

    next_offset = bounded_offset + len(items)
    if next_offset < total:
        lines.append(f"仍有更多结果；下一页 offset={next_offset}。")
    return "\n".join(lines)


@tool(
    "get_global_stats",
    description=(
        "获取全局观测概览（只读）：全平台 Token 消耗汇总与已归因项目列表。"
        "用于了解整体运行态势与项目清单。无参数。"
    ),
)
def get_global_stats() -> str:
    """拉取全局 token 统计与项目列表。"""

    async def _load() -> dict[str, Any]:
        from Sere1nGraph.graph.observability import get_global_tracker
        from api.db.mongodb import get_db

        tracker = get_global_tracker()
        if getattr(tracker, "_db", None) is None:
            tracker.set_db(get_db())
        stats = await tracker.get_stats_async()
        projects = await tracker.list_projects_async()
        return {"global": stats, "projects": projects}

    try:
        d = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"获取全局统计失败：{exc}"

    g = d.get("global") or {}
    projects = d.get("projects") or []
    lines = [
        "全局观测概览：",
        f"- Token 调用 {g.get('total_calls', 0)} 次，输入 {g.get('total_input_tokens', 0)}，"
        f"输出 {g.get('total_output_tokens', 0)}，成本 ¥{g.get('total_cost_yuan', 0)}",
        f"- 已归因项目 {len(projects)} 个。",
    ]
    for p in projects[:10]:
        pid = p.get("project_id") or p.get("_id") or ""
        calls = p.get("total_calls") or p.get("calls") or 0
        if pid:
            lines.append(f"  · {pid}（调用 {calls} 次）")
    return "\n".join(lines)


# 供 Agent 复用的只读数据分析工具集
ANALYSIS_TOOLS = [
    get_project_dashboard,
    batch_get_project_dashboards,
    get_findings_summary,
    query_findings,
    query_target_intelligence,
    get_global_stats,
]
