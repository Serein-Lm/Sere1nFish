"""人物 OSINT 情报读写工具。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from langchain.tools import tool
from api.models.person_intelligence import (
    ContextSignal,
    EngagementScenario,
    IntelligenceEvidence,
    PersonaMatch,
    PublicContact,
    PublicSource,
    SampleCopywriting,
)

from . import _refs
from .builtin import _run_coro_sync


def _dump_items(values: list[Any] | None) -> list[Any]:
    return [item.model_dump() if hasattr(item, "model_dump") else item for item in values or []]


def _coerce_list(value: list[Any] | str | None, *, field: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        parsed = json.loads(value or "[]")
        if not isinstance(parsed, list):
            raise ValueError(f"{field} 必须是 JSON 数组")
        return parsed
    return _dump_items(value)


def _coerce_mapping(value: dict[str, Any] | str | None, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        parsed = json.loads(value or "{}")
        if not isinstance(parsed, dict):
            raise ValueError(f"{field} 必须是 JSON 对象")
        return parsed
    return dict(value)


def _summary_line(item: dict[str, Any]) -> str:
    identity = " · ".join(
        str(item.get(key) or "").strip()
        for key in ("name", "organization", "position")
        if str(item.get(key) or "").strip()
    )
    freshness = _research_freshness(item)
    return (
        f"{identity or '未命名人物'}；置信度={float(item.get('confidence') or 0):.2f}；"
        f"来源={int(item.get('source_count') or len(item.get('sources') or []))}；"
        f"证据={int(item.get('evidence_count') or len(item.get('evidence') or []))}；"
        f"版本=v{int(item.get('profile_version') or 1)}；"
        f"新鲜度={freshness['status']}({freshness['age_days']}天)；"
        f"intel_id={item.get('intel_id') or ''}"
    )


def _research_freshness(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("last_researched_at") or item.get("updated_at")
    researched_at: datetime | None = None
    if isinstance(value, datetime):
        researched_at = value
    elif value:
        try:
            researched_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            researched_at = None
    if researched_at is None:
        return {"status": "unknown", "age_days": -1, "fresh": False}
    if researched_at.tzinfo is None:
        researched_at = researched_at.replace(tzinfo=timezone.utc)
    age_days = max(0, (datetime.now(timezone.utc) - researched_at).days)
    return {
        "status": "fresh" if age_days <= 30 else "stale",
        "age_days": age_days,
        "fresh": age_days <= 30,
    }


@tool(
    "search_person_intelligence",
    description=(
        "检索真实人物的公开 OSINT 情报摘要。支持 keyword、organization、target_id、"
        "project_id、min_confidence 和 limit。先用本工具定位，再调用 get_person_intelligence；"
        "不要用虚构人设工具查询真实人物。"
    ),
)
def search_person_intelligence(
    keyword: str = "",
    organization: str = "",
    target_id: str = "",
    project_id: str = "",
    min_confidence: float = 0.0,
    limit: int = 5,
) -> str:
    async def _load():
        from api.db.mongodb import get_db
        from api.services.person_intelligence import list_person_intelligence

        return await list_person_intelligence(
            get_db(),
            keyword=keyword,
            organization=organization,
            target_id=target_id,
            project_id=project_id,
            min_confidence=min_confidence,
            sort="confidence_desc",
            skip=0,
            limit=max(1, min(int(limit), 20)),
            summary_only=True,
        )

    try:
        items, total = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"检索人物情报失败：{exc}"
    if not items:
        return "人物情报库中未找到匹配记录，需要启动公网 OSINT 研究。"
    lines = [f"共匹配 {total} 条人物情报，返回前 {len(items)} 条："]
    for index, item in enumerate(items, 1):
        ref = _refs.person_intelligence_ref(
            str(item.get("intel_id") or ""), str(item.get("name") or "")
        )
        lines.append(f"{index}. {_summary_line(item)} {ref}".strip())
    return "\n".join(lines)


@tool(
    "get_person_intelligence",
    description=(
        "按 intel_id 读取真实人物的完整公开情报，包括核验来源、事实与推断、公开联系方式、"
        "画像、匹配人设、沟通方案和历史话术。"
    ),
)
def get_person_intelligence(intel_id: str) -> str:
    async def _load():
        from api.db.mongodb import get_db
        from api.services.person_intelligence import get_person_intelligence as load

        return await load(get_db(), intel_id.strip())

    try:
        item = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"读取人物情报失败：{exc}"
    if not item:
        return f"未找到 intel_id={intel_id} 的人物情报。"
    result = dict(item)
    result["research_freshness"] = _research_freshness(result)
    result["sources"] = list(result.get("sources") or [])[:40]
    result["evidence"] = list(result.get("evidence") or [])[:80]
    result["sample_copywritings"] = list(result.get("sample_copywritings") or [])[-20:]
    ref = _refs.person_intelligence_ref(
        str(result.get("intel_id") or ""), str(result.get("name") or "")
    )
    return json.dumps(result, ensure_ascii=False, default=str, indent=2) + f"\n{ref}"


@tool(
    "save_person_intelligence",
    description=(
        "保存或升级一名真实人物的公开 OSINT 情报。仅在浏览器已核验公开网页后调用；"
        "sources 至少一项，证据和公开联系方式必须引用 sources 中的 URL。"
        "嵌套字段优先按工具 Schema 直接传结构化对象；兼容合法 JSON 字符串。"
        "场景使用稳定 scenario_id，"
        "话术通过 scenario_ids 关联；当前信号不得使用已经过期的 expires_at。"
    ),
)
def save_person_intelligence(
    name: str,
    organization: str,
    position: str = "",
    department: str = "",
    location: str = "",
    summary: str = "",
    background: str = "",
    aliases: list[str] | str | None = None,
    affiliations: list[dict[str, Any]] | str | None = None,
    career_history: list[dict[str, Any]] | str | None = None,
    research_areas: list[str] | str | None = None,
    public_contacts: list[PublicContact] | str | None = None,
    profile: dict[str, Any] | str | None = None,
    sources: list[PublicSource] | str | None = None,
    evidence: list[IntelligenceEvidence] | str | None = None,
    context_signals: list[ContextSignal] | str | None = None,
    recommended_personas: list[PersonaMatch] | str | None = None,
    scenarios: list[EngagementScenario] | str | None = None,
    engagement_plan: dict[str, Any] | str | None = None,
    sample_copywritings: list[SampleCopywriting] | str | None = None,
    confidence: float = 0.0,
    target_id: str = "",
    project_id: str = "",
    task_id: str = "",
) -> str:
    try:
        payload = {
            "name": name,
            "organization": organization,
            "position": position,
            "department": department,
            "location": location,
            "summary": summary,
            "background": background,
            "aliases": _coerce_list(aliases, field="aliases"),
            "affiliations": _coerce_list(affiliations, field="affiliations"),
            "career_history": _coerce_list(career_history, field="career_history"),
            "research_areas": _coerce_list(research_areas, field="research_areas"),
            "public_contacts": _coerce_list(public_contacts, field="public_contacts"),
            "profile": _coerce_mapping(profile, field="profile"),
            "sources": _coerce_list(sources, field="sources"),
            "evidence": _coerce_list(evidence, field="evidence"),
            "context_signals": _coerce_list(context_signals, field="context_signals"),
            "recommended_personas": _coerce_list(
                recommended_personas, field="recommended_personas"
            ),
            "scenarios": _coerce_list(scenarios, field="scenarios"),
            "engagement_plan": _coerce_mapping(
                engagement_plan, field="engagement_plan"
            ),
            "sample_copywritings": _coerce_list(
                sample_copywritings, field="sample_copywritings"
            ),
            "confidence": confidence,
            "target_id": target_id,
            "project_id": project_id,
            "task_id": task_id,
        }

        async def _save():
            from api.db.mongodb import get_db
            from api.services.person_intelligence import save_person_intelligence as save

            return await save(get_db(), payload)

        item = _run_coro_sync(_save())
    except Exception as exc:  # noqa: BLE001
        return f"保存人物情报失败：{exc}"
    ref = _refs.person_intelligence_ref(
        str(item.get("intel_id") or ""), str(item.get("name") or "")
    )
    return f"人物情报已保存：{_summary_line(item)} {ref}".strip()


@tool(
    "link_person_intelligence_artifact",
    description=(
        "把已生成的 Artifact 关联到人物情报溯源链。先生成文档获得 artifact_id，"
        "再传 intel_id 和 artifact_id 调用。"
    ),
)
def link_person_intelligence_artifact(intel_id: str, artifact_id: str) -> str:
    async def _link():
        from api.db.mongodb import get_db
        from api.services.person_intelligence import attach_person_intelligence_artifact

        return await attach_person_intelligence_artifact(
            get_db(), intel_id=intel_id.strip(), artifact_id=artifact_id.strip()
        )

    try:
        item = _run_coro_sync(_link())
    except Exception as exc:  # noqa: BLE001
        return f"关联人物情报产物失败：{exc}"
    return (
        f"产物 {artifact_id} 已关联人物情报 {intel_id}；"
        f"当前共 {len(item.get('artifact_ids') or [])} 个产物。"
    )


OSINT_READ_TOOLS = [search_person_intelligence, get_person_intelligence]
OSINT_TOOLS = [
    *OSINT_READ_TOOLS,
    save_person_intelligence,
    link_person_intelligence_artifact,
]
