"""AI-planned, source-grounded fictional persona generation.

Models plan the research space, run independent Chrome research missions,
synthesize concrete archetypes, generate detailed fictional profiles, and
review consistency before persistence. Code enforces provenance, privacy,
idempotency, concurrency, and output contracts; it does not construct persona
facts or dimension matrices.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Sequence

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.logger import get_logger

logger = get_logger("persona_generate")

DEFAULT_PERSONA_COUNT = 36
MAX_PERSONAS_PER_BATCH = 60
RESEARCH_BATCH_SIZE = 6
RESEARCH_CONCURRENCY = 3
RESEARCH_ATTEMPTS = 2
MIN_VERIFIED_SOURCES_PER_RESEARCH_BATCH = 8
MIN_VERIFIED_INSIGHTS_PER_RESEARCH_BATCH = 12
MIN_DISTINCT_SOURCES_PER_RESEARCH_BATCH = 4
MIN_VERIFIED_SOURCES_PER_ARCHETYPE = 4
GENERATION_CONCURRENCY = 6
MODEL_REQUEST_TIMEOUT_SECONDS = 180

_PROFILE_PLACEHOLDER_MARKERS = (
    "信息缺失",
    "内容缺失",
    "模式缺失",
    "偏好缺失",
    "动机缺失",
    "待补充",
    "待验证",
    "待定",
    "未知",
    "无法确认",
    "无法支撑",
    "未获取",
    "无实证",
    "空壳",
    "占位",
    "假设性描述",
)
_RESEARCH_GAP_MARKERS = (
    "未覆盖",
    "无法支撑",
    "未获取",
    "未提供",
    "无实证",
    "缺乏实证",
    "缺乏企业层面",
    "缺乏一线",
    "空壳",
    "留白",
    "研究缺口",
)


async def _invoke_model(
    call: Awaitable[Any] | Callable[[], Awaitable[Any]],
    *,
    phase: str,
    attempts: int = 2,
) -> Any:
    """Bound direct calls and retry timeouts only when the request is rebuildable."""
    is_factory = callable(call)
    max_attempts = max(1, min(int(attempts or 1), 3)) if is_factory else 1
    for attempt in range(1, max_attempts + 1):
        awaitable = call() if is_factory else call
        try:
            return await asyncio.wait_for(
                awaitable,
                timeout=MODEL_REQUEST_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            if attempt >= max_attempts:
                raise TimeoutError(
                    f"{phase}模型调用超过 {MODEL_REQUEST_TIMEOUT_SECONDS} 秒"
                ) from exc
            logger.warning(
                "[persona_research] %s模型调用超时，准备有限重试 %s/%s",
                phase,
                attempt + 1,
                max_attempts,
            )
            await asyncio.sleep(1)
    raise RuntimeError(f"{phase}模型调用未返回结果")


async def _update_research_task(
    db: AsyncIOMotorDatabase,
    task_id: str,
    *,
    stage: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    if not task_id:
        return
    try:
        from api.dao import persona_research_tasks as research_tasks_dao

        await research_tasks_dao.update_task(
            db,
            task_id,
            status="running",
            stage=stage,
            message=message,
            details=details,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[persona_research] 更新任务进度失败 task=%s: %s", task_id, exc)


def _clean_hints(values: Sequence[str] | None) -> list[str]:
    return list(
        dict.fromkeys(
            str(value).strip()
            for value in values or []
            if str(value).strip()
        )
    )


def _contains_marker(value: Any, markers: Sequence[str]) -> bool:
    text = str(value or "").strip()
    return bool(text) and any(marker in text for marker in markers)


def _is_concrete_profile_value(value: Any) -> bool:
    return value not in (None, "", [], {}) and not _contains_marker(
        value,
        _PROFILE_PLACEHOLDER_MARKERS,
    )


def _research_evidence_has_gap(value: Any) -> bool:
    payload = (
        value.model_dump()
        if hasattr(value, "model_dump")
        else dict(value or {})
        if isinstance(value, dict)
        else {}
    )
    return any(
        _contains_marker(payload.get(field), _RESEARCH_GAP_MARKERS)
        for field in ("dimension", "finding", "applicability")
    )


def _profile_quality_issues(profile: dict[str, Any]) -> list[str]:
    """Validate richness without constructing or rewriting any persona facts."""
    issues: list[str] = []
    scalar_fields = (
        "background",
        "career_path",
        "collaboration_style",
        "communication_style",
        "decision_style",
        "learning_style",
        "life_stage",
        "organization_context",
        "personality",
        "stress_response",
        "summary",
        "technology_attitude",
        "work_context",
        "work_rhythm",
    )
    list_fields = (
        "behavior_patterns",
        "content_preferences",
        "digital_habits",
        "goals",
        "information_preferences",
        "interests",
        "motivations",
        "pain_points",
        "purchase_considerations",
        "risk_signals",
        "tags",
        "values",
    )
    for field in scalar_fields:
        value = str(profile.get(field) or "").strip()
        if _contains_marker(value, _PROFILE_PLACEHOLDER_MARKERS):
            issues.append(f"{field} 包含缺失或占位描述")
    for field in list_fields:
        if any(
            _contains_marker(item, _PROFILE_PLACEHOLDER_MARKERS)
            for item in profile.get(field) or []
        ):
            issues.append(f"{field} 包含缺失或占位条目")
    if any(
        _research_evidence_has_gap(item)
        for item in profile.get("research_evidence") or []
    ):
        issues.append("research_evidence 包含研究缺口或待补充证据")
    if len(str(profile.get("summary") or "").strip()) < 80:
        issues.append("summary 未形成可独立检索的具体首层摘要")
    if len(str(profile.get("background") or "").strip()) < 80:
        issues.append("background 缺少完整职业与生活时间线")
    return issues


def _archetype_quality_issues(archetype: Any) -> list[str]:
    payload = (
        archetype.model_dump()
        if hasattr(archetype, "model_dump")
        else dict(archetype or {})
    )
    issues: list[str] = []
    narrative_fields = (
        "background_focus",
        "career_stage",
        "communication_style",
        "context_summary",
        "decision_style",
        "life_stage",
        "organization_context",
        "technology_attitude",
        "work_context",
        "work_rhythm",
    )
    list_fields = (
        "behavior_patterns",
        "content_preferences",
        "context_evidence",
        "digital_habits",
        "goals",
        "information_channels",
        "motivations",
        "pain_points",
        "personality_traits",
        "values",
    )
    for field in narrative_fields:
        if _contains_marker(payload.get(field), _RESEARCH_GAP_MARKERS):
            issues.append(f"{field} 使用了研究缺口代替人物原型信息")
    for field in list_fields:
        if any(
            _contains_marker(item, _RESEARCH_GAP_MARKERS)
            for item in payload.get(field) or []
        ):
            issues.append(f"{field} 包含研究缺口条目")
    for evidence in payload.get("research_evidence") or []:
        evidence_payload = (
            evidence.model_dump()
            if hasattr(evidence, "model_dump")
            else dict(evidence or {})
        )
        if _contains_marker(
            evidence_payload.get("finding"),
            _RESEARCH_GAP_MARKERS,
        ):
            issues.append("research_evidence 选择了研究缺口而非正向事实")
            break
    return issues


def _stable_key(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _research_mission_request(
    *,
    background: str,
    mission: Any,
    industries: list[str],
    age_ranges: list[str],
    personalities: list[str],
    company: str,
    position: str,
    extra: str,
) -> str:
    mission_data = mission.model_dump() if hasattr(mission, "model_dump") else dict(mission)
    return "\n".join(
        [
            f"总体背景：{background}",
            f"用户行业提示：{'、'.join(industries) or '无，由 AI 自主探索'}",
            f"用户年龄提示：{'、'.join(age_ranges) or '无，由 AI 自主探索'}",
            f"用户性格提示：{'、'.join(personalities) or '无，由 AI 自主探索'}",
            f"组织提示：{company or '无，由 AI 自主探索'}",
            f"职位提示：{position or '无，由 AI 自主探索'}",
            f"补充约束：{extra or '无'}",
            "本分片研究任务（由 AI 规划器生成）：",
            json.dumps(mission_data, ensure_ascii=False, indent=2),
            "只研究通用行业与岗位背景，禁止采集或输出任何真实自然人身份。",
        ]
    )


def _persona_brief(background: str, archetype: Any, index: int) -> str:
    payload = archetype.model_dump() if hasattr(archetype, "model_dump") else dict(archetype)
    return (
        f"总体背景：{background}；原型序号：{index + 1}；"
        f"虚构姓名：{payload.get('fictional_name', '')}；"
        f"行业：{payload.get('industry', '')}；年龄段：{payload.get('age_range', '')}；"
        f"岗位：{payload.get('role', '')}；职级：{payload.get('position_level', '')}；"
        f"地区：{payload.get('region', '')}；"
        f"组织环境：{payload.get('organization_context', '')}；"
        f"职业阶段：{payload.get('career_stage', '')}；"
        f"生活阶段：{payload.get('life_stage', '')}；"
        f"性格：{'、'.join(payload.get('personality_traits') or [])}；"
        f"决策方式：{payload.get('decision_style', '')}；"
        f"沟通方式：{payload.get('communication_style', '')}；"
        f"数字态度：{payload.get('technology_attitude', '')}；"
        f"背景重点：{payload.get('background_focus', '')}"
    )


_INCREMENTAL_LIST_FIELDS = (
    "interests",
    "information_preferences",
    "digital_habits",
    "motivations",
    "goals",
    "pain_points",
    "values",
    "behavior_patterns",
    "content_preferences",
    "purchase_considerations",
    "tags",
    "risk_signals",
    "sources",
    "evidence",
    "research_evidence",
)
_STABLE_IDENTITY_FIELDS = (
    "name",
    "gender",
    "age",
    "age_range",
    "company",
    "industry",
    "position",
    "position_level",
    "department",
    "education",
    "location",
    "region_type",
    "generation_key",
)


def _profile_for_ai(existing: dict[str, Any] | None) -> dict[str, Any]:
    """Remove persistence metadata while retaining facts for incremental review."""
    if not existing:
        return {}
    ignored = {
        "_id",
        "person_id",
        "project_ids",
        "created_at",
        "updated_at",
        "last_researched_at",
        "profile_version",
        "research_rounds",
    }
    profile = {key: value for key, value in existing.items() if key not in ignored}
    profile["sources"] = list(existing.get("source_urls") or [])
    return profile


def _merge_existing_profile(
    existing: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Preserve collected facts while accepting AI-authored incremental details."""
    base = _profile_for_ai(existing)
    if not base:
        return dict(candidate or {})
    merged = dict(base)
    for key, value in dict(candidate or {}).items():
        if key in _INCREMENTAL_LIST_FIELDS:
            combined = [*list(base.get(key) or []), *list(value or [])]
            if key == "research_evidence":
                normalized: dict[str, Any] = {}
                for item in combined:
                    if hasattr(item, "model_dump"):
                        item = item.model_dump()
                    if isinstance(item, dict) and not _research_evidence_has_gap(item):
                        normalized[json.dumps(item, ensure_ascii=False, sort_keys=True)] = item
                merged[key] = list(normalized.values())
            else:
                if key not in {"sources", "evidence"}:
                    combined = [
                        item
                        for item in combined
                        if not _contains_marker(item, _PROFILE_PLACEHOLDER_MARKERS)
                    ]
                merged[key] = list(
                    dict.fromkeys(str(item).strip() for item in combined if str(item).strip())
                )
        elif value not in (None, "", [], {}):
            merged[key] = value
    for key in _STABLE_IDENTITY_FIELDS:
        if _is_concrete_profile_value(base.get(key)):
            merged[key] = base[key]
    return merged


def merge_existing_persona_profile(
    existing: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """统一归并主动研究结果，供批量研究和 Agent 写入复用。"""
    return _merge_existing_profile(existing, candidate)


def _enforce_fictional_profile(
    parsed: dict[str, Any],
    *,
    brief: str,
    index: int,
    archetype: Any,
    generation_key: str = "",
    global_sources: list[str],
    company: str,
    position: str,
    existing_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply identity, privacy, and provenance invariants to AI output."""
    archetype_data = (
        archetype.model_dump() if hasattr(archetype, "model_dump") else dict(archetype)
    )
    source_urls = list(
        dict.fromkeys(
            [
                *global_sources,
                *list(archetype_data.get("source_urls") or []),
            ]
        )
    )
    profile = dict(parsed or {})
    fictional_name = str(archetype_data.get("fictional_name") or "").strip()
    if fictional_name:
        profile["name"] = fictional_name
    profile["is_fictional"] = True
    profile["generation_brief"] = brief
    profile["generation_key"] = generation_key or _stable_key(
        {"brief": brief, "index": index}
    )
    profile["industry"] = (
        archetype_data.get("industry") or profile.get("industry") or ""
    )
    profile["age_range"] = (
        archetype_data.get("age_range") or profile.get("age_range") or ""
    )
    profile["position"] = position.strip() or archetype_data.get("role") or profile.get("position") or ""
    profile["position_level"] = (
        archetype_data.get("position_level") or profile.get("position_level") or ""
    )
    profile["region_type"] = (
        archetype_data.get("region") or profile.get("region_type") or ""
    )
    profile["location"] = archetype_data.get("region") or profile.get("location") or ""
    scalar_fallbacks = {
        "organization_context": "organization_context",
        "career_stage": "career_stage",
        "life_stage": "life_stage",
        "work_context": "work_context",
        "work_rhythm": "work_rhythm",
        "communication_style": "communication_style",
    }
    for profile_field, archetype_field in scalar_fallbacks.items():
        profile[profile_field] = (
            archetype_data.get(archetype_field) or profile.get(profile_field) or ""
        )
    profile["decision_style"] = (
        archetype_data.get("decision_style")
        or profile.get("decision_style")
        or ""
    )
    profile["technology_attitude"] = (
        archetype_data.get("technology_attitude")
        or profile.get("technology_attitude")
        or ""
    )
    list_fallbacks = {
        "information_preferences": "information_channels",
        "digital_habits": "digital_habits",
        "motivations": "motivations",
        "goals": "goals",
        "pain_points": "pain_points",
        "values": "values",
        "behavior_patterns": "behavior_patterns",
        "content_preferences": "content_preferences",
    }
    for profile_field, archetype_field in list_fallbacks.items():
        profile[profile_field] = list(
            dict.fromkeys(
                str(value).strip()
                for value in [
                    *list(archetype_data.get(archetype_field) or []),
                    *list(profile.get(profile_field) or []),
                ]
                if str(value).strip()
            )
        )
    profile["company_root_domain"] = ""
    profile["contact"] = {
        "phone": "",
        "email": "",
        "wechat": "",
        "other_social": [],
    }
    profile["sources"] = source_urls
    profile["evidence"] = list(
        dict.fromkeys(
            str(value).strip()
            for value in archetype_data.get("context_evidence") or []
            if str(value).strip()
        )
    )
    profile["research_evidence"] = list(
        archetype_data.get("research_evidence") or []
    )
    if company.strip():
        profile["company"] = company.strip()
    for field in _STABLE_IDENTITY_FIELDS:
        existing_value = (existing_profile or {}).get(field)
        if _is_concrete_profile_value(existing_value):
            profile[field] = existing_value
    summary = str(profile.get("summary") or "").strip()
    if summary and "虚构" not in summary:
        profile["summary"] = f"【虚构人设】{summary}"
    return profile


async def _verify_research_sources(
    report: Any,
    *,
    project_id: str,
    task_id: str,
) -> Any:
    """Keep only reachable public sources in one AI research report."""
    from api.services.company_url import normalize_url
    from api.services.info_collection.contracts import ProbeRequest
    from api.services.info_collection.url_tools import UrlProbeTool

    candidates = [source.url for source in report.sources]
    normalized = list(
        dict.fromkeys(
            url
            for value in candidates
            if (url := normalize_url(str(value or "")))
        )
    )
    if not normalized:
        raise RuntimeError("背景研究没有返回可校验的公网来源")

    result = await UrlProbeTool().probe(
        ProbeRequest(
            source="persona_research",
            urls=normalized,
            project_id=project_id,
            task_id=task_id,
            concurrency=min(12, len(normalized)),
            timeout=12.0,
            only_alive=False,
        )
    )
    reachable: list[str] = []
    for item in result.items:
        try:
            status_code = int(item.get("status_code") or 0)
        except (TypeError, ValueError):
            status_code = 0
        if 200 <= status_code < 400 or status_code in {401, 403, 429}:
            url = normalize_url(str(item.get("url") or ""))
            if url:
                reachable.append(url)
    reachable = list(dict.fromkeys(reachable))
    if len(reachable) < MIN_VERIFIED_SOURCES_PER_RESEARCH_BATCH:
        raise RuntimeError(
            f"背景研究只有 {len(reachable)} 个可探活公网来源，"
            f"少于要求的 {MIN_VERIFIED_SOURCES_PER_RESEARCH_BATCH} 个"
        )

    reachable_set = set(reachable)
    verified_sources = []
    seen_sources: set[str] = set()
    for source in report.sources:
        normalized_url = normalize_url(str(source.url or ""))
        if normalized_url in reachable_set and normalized_url not in seen_sources:
            source.url = normalized_url
            verified_sources.append(source)
            seen_sources.add(normalized_url)
    report.sources = verified_sources
    verified_insights = []
    seen_insights: set[tuple[str, str]] = set()
    for insight in report.insights:
        insight.source_urls = list(
            dict.fromkeys(
                url
                for value in insight.source_urls
                if (url := normalize_url(str(value or ""))) in reachable_set
            )
        )
        insight_key = (insight.dimension.strip(), insight.finding.strip())
        if insight.source_urls and insight_key not in seen_insights:
            verified_insights.append(insight)
            seen_insights.add(insight_key)
    if len(verified_insights) < MIN_VERIFIED_INSIGHTS_PER_RESEARCH_BATCH:
        raise RuntimeError(
            f"背景研究只有 {len(verified_insights)} 条具备有效来源的洞察，"
            f"少于要求的 {MIN_VERIFIED_INSIGHTS_PER_RESEARCH_BATCH} 条"
        )
    report.insights = verified_insights
    return report


async def _research_mission(
    app_config: Any,
    *,
    request_text: str,
    mission_id: str,
    search_queries: Sequence[str],
    project_id: str,
    task_id: str,
    research_key: str = "",
    excluded_urls: Sequence[str] | None = None,
    candidate_offset: int = 0,
) -> Any:
    from langchain_core.messages import HumanMessage, SystemMessage

    from api.services.persona_research_browser import (
        create_persona_research_browser,
        research_url_identity,
    )
    from core.observability import observation_context
    from Sere1nGraph.graph.agents.runtime import create_llm
    from Sere1nGraph.graph.prompts.loader import load_prompt
    from Sere1nGraph.graph.skills.schemas import PersonaResearchReport

    browser = create_persona_research_browser()
    pages = await browser.collect(
        app_config,
        search_queries=search_queries,
        task_id=task_id,
        research_key=research_key or task_id or _stable_key(request_text),
        excluded_urls=excluded_urls,
        candidate_offset=candidate_offset,
    )
    prompt = load_prompt("persona_research/persona_research")
    structured = create_llm(app_config, streaming=False).with_structured_output(
        PersonaResearchReport
    )
    payload = {
        "mission_id": mission_id,
        "research_task": request_text,
        "visited_pages": [page.model_payload() for page in pages],
        "hard_requirements": {
            "sources": "从 visited_pages 中选择至少 8 个实际来源",
            "insights": "输出至少 12 条具体洞察，每条仅引用 visited_pages URL",
            "privacy": "只提炼群体与岗位背景，不输出真实自然人身份",
        },
    }
    with observation_context(
        project_id=project_id,
        task_id=task_id,
        phase="persona_research",
        agent="persona_research",
        task_type="persona_research",
    ):
        report = await _invoke_model(
            lambda: structured.ainvoke(
                [
                    SystemMessage(content=prompt),
                    HumanMessage(
                        content=json.dumps(payload, ensure_ascii=False, indent=2)
                    ),
                ]
            ),
            phase="人设证据综合",
        )
    if report.mission_id.strip() != mission_id.strip():
        raise RuntimeError(
            f"背景研究分片标识不一致：期望 {mission_id}，实际 {report.mission_id}"
        )

    page_by_identity = {
        research_url_identity(page.url): page
        for page in pages
        if research_url_identity(page.url)
    }
    aligned_sources = []
    for source in report.sources:
        page = page_by_identity.get(research_url_identity(source.url))
        if page is None:
            continue
        source.url = page.url
        source.title = page.title
        source.publisher = page.publisher
        aligned_sources.append(source)
    report.sources = aligned_sources
    allowed_urls = {source.url for source in report.sources}
    aligned_insights = []
    for insight in report.insights:
        insight.source_urls = list(
            dict.fromkeys(
                page.url
                for value in insight.source_urls
                if (
                    page := page_by_identity.get(research_url_identity(value))
                ) is not None
                and page.url in allowed_urls
            )
        )
        if insight.source_urls:
            aligned_insights.append(insight)
    report.insights = aligned_insights
    if len(report.sources) < MIN_VERIFIED_SOURCES_PER_RESEARCH_BATCH:
        raise RuntimeError("模型引用的实际浏览来源少于 8 个")
    if len(report.insights) < MIN_VERIFIED_INSIGHTS_PER_RESEARCH_BATCH:
        raise RuntimeError("模型输出的来源关联洞察少于 12 条")
    return await _verify_research_sources(
        report,
        project_id=project_id,
        task_id=task_id,
    )


async def _plan_research_program(
    app_config: Any,
    *,
    background: str,
    count: int,
    industries: list[str],
    age_ranges: list[str],
    personalities: list[str],
    company: str,
    position: str,
    extra: str,
    project_id: str,
    task_id: str,
) -> Any:
    """Let the model discover and divide the research space; code sets no dimensions."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from core.observability import observation_context
    from Sere1nGraph.graph.agents.runtime import create_llm
    from Sere1nGraph.graph.prompts.loader import load_prompt
    from Sere1nGraph.graph.skills.schemas import PersonaResearchProgram

    expected_missions = (count + RESEARCH_BATCH_SIZE - 1) // RESEARCH_BATCH_SIZE
    prompt = load_prompt("persona_research_plan/persona_research_plan")
    structured = create_llm(app_config, streaming=False).with_structured_output(
        PersonaResearchProgram
    )
    request = {
        "background": background,
        "persona_count": count,
        "research_mission_count": expected_missions,
        "optional_user_hints": {
            "industries": industries,
            "age_ranges": age_ranges,
            "personalities": personalities,
            "company": company,
            "position": position,
            "extra": extra,
        },
    }
    correction = ""
    for _attempt in range(2):
        with observation_context(
            project_id=project_id,
            task_id=task_id,
            phase="persona_research_plan",
            agent="persona_research_plan",
            task_type="persona_research_plan",
        ):
            program = await _invoke_model(
                lambda: structured.ainvoke(
                    [
                        SystemMessage(content=prompt),
                        HumanMessage(
                            content=json.dumps(request, ensure_ascii=False, indent=2)
                            + correction
                        ),
                    ]
                ),
                phase="人设研究规划",
            )
        mission_ids = [mission.mission_id.strip() for mission in program.missions]
        for mission in program.missions:
            mission.search_queries = _clean_hints(mission.search_queries)
            mission.discovery_dimensions = _clean_hints(
                mission.discovery_dimensions
            )
            mission.diversity_focus = _clean_hints(mission.diversity_focus)
            mission.source_priorities = _clean_hints(mission.source_priorities)
        total = sum(mission.persona_count for mission in program.missions)
        mission_semantics_valid = all(
            len(mission.search_queries) >= 4
            and len(mission.discovery_dimensions) >= 5
            and len(mission.diversity_focus) >= 3
            and len(mission.source_priorities) >= 3
            and bool(mission.objective.strip())
            and bool(mission.overlap_avoidance.strip())
            for mission in program.missions
        )
        if (
            len(program.missions) == expected_missions
            and total == count
            and all(mission_ids)
            and len(set(mission_ids)) == len(mission_ids)
            and mission_semantics_valid
        ):
            return program
        correction = (
            "\n上一次计划不符合硬性 Schema 语义：必须恰好输出 "
            f"{expected_missions} 个 mission，persona_count 总和必须为 {count}，"
            "mission_id 必须非空且互不重复；每个 mission 必须提供至少 4 个非空且"
            "互不重复的检索词、5 个探索维度、3 个差异化重点和 3 类来源优先级，"
            "objective 与 overlap_avoidance 也不能为空。请完全重做。"
        )
    raise RuntimeError("AI 研究计划未满足分片数量、人物总数或唯一标识约束")


async def _research_program_batches(
    app_config: Any,
    *,
    program: Any,
    background: str,
    industries: list[str],
    age_ranges: list[str],
    personalities: list[str],
    company: str,
    position: str,
    extra: str,
    project_id: str,
    task_id: str,
    existing_source_urls: Sequence[str] | None = None,
) -> list[Any]:
    """Execute AI-planned missions with independent Chrome leases."""
    semaphore = asyncio.Semaphore(RESEARCH_CONCURRENCY)

    async def _run_mission(mission_index: int, mission: Any) -> Any:
        request_text = _research_mission_request(
            background=background,
            mission=mission,
            industries=industries,
            age_ranges=age_ranges,
            personalities=personalities,
            company=company,
            position=position,
            extra=extra,
        )
        last_error: Exception | None = None
        async with semaphore:
            for attempt in range(1, RESEARCH_ATTEMPTS + 1):
                try:
                    return await _research_mission(
                        app_config,
                        request_text=request_text,
                        mission_id=mission.mission_id,
                        search_queries=mission.search_queries,
                        project_id=project_id,
                        task_id=task_id,
                        research_key=(
                            f"{task_id or _stable_key(background)}_"
                            f"mission{mission_index + 1}_attempt{attempt}"
                        ),
                        excluded_urls=existing_source_urls,
                        candidate_offset=(attempt - 1) * 4,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.warning(
                        "[persona_research] mission=%s attempt=%s/%s failed: %s",
                        mission.mission_id,
                        attempt,
                        RESEARCH_ATTEMPTS,
                        exc,
                    )
            raise RuntimeError(
                f"研究任务 {mission.mission_id} 失败：{last_error}"
            )

    reports = await asyncio.gather(
        *(
            _run_mission(index, mission)
            for index, mission in enumerate(program.missions)
        )
    )

    # Repeated sources do not add information. If a later mission mostly
    # overlaps previous reports, let the browser Agent research it again with
    # an explicit exclusion list. The implementation still does not invent any
    # profile dimensions or source facts.
    seen_urls = {
        str(url).strip() for url in existing_source_urls or [] if str(url).strip()
    }
    for index, (mission, report) in enumerate(zip(program.missions, reports, strict=True)):
        report_urls = {source.url for source in report.sources}
        if seen_urls and len(report_urls - seen_urls) < MIN_DISTINCT_SOURCES_PER_RESEARCH_BATCH:
            request_text = _research_mission_request(
                background=background,
                mission=mission,
                industries=industries,
                age_ranges=age_ranges,
                personalities=personalities,
                company=company,
                position=position,
                extra=extra,
            )
            request_text += (
                "\n\n跨分片来源去重要求：以下 URL 已被前序任务采用，本分片不得再次"
                "写入这些 URL；请更换检索词和来源类型，补充不同的信息。\n"
                + "\n".join(sorted(seen_urls))
            )
            replacement = await _research_mission(
                app_config,
                request_text=request_text,
                mission_id=mission.mission_id,
                search_queries=mission.search_queries,
                project_id=project_id,
                task_id=task_id,
                research_key=(
                    f"{task_id or _stable_key(background)}_"
                    f"mission{index + 1}_dedupe"
                ),
                excluded_urls=seen_urls,
                candidate_offset=4,
            )
            replacement_urls = {source.url for source in replacement.sources}
            if len(replacement_urls - seen_urls) < MIN_DISTINCT_SOURCES_PER_RESEARCH_BATCH:
                raise RuntimeError(
                    f"研究任务 {mission.mission_id} 与前序来源重复过多，"
                    f"未获得至少 {MIN_DISTINCT_SOURCES_PER_RESEARCH_BATCH} 个新来源"
                )
            reports[index] = replacement
            report_urls = replacement_urls
        seen_urls.update(report_urls)
    return reports


async def _synthesize_archetypes(
    app_config: Any,
    *,
    background: str,
    count: int,
    program: Any,
    reports: list[Any],
    name: str,
    company: str,
    position: str,
    extra: str,
    project_id: str,
    task_id: str,
) -> Any:
    """Synthesize information-rich archetypes per mission to avoid truncation."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from api.services.company_url import normalize_url
    from core.observability import observation_context
    from Sere1nGraph.graph.agents.runtime import create_llm
    from Sere1nGraph.graph.prompts.loader import load_prompt
    from Sere1nGraph.graph.skills.schemas import PersonaGenerationPlan

    prompt = load_prompt("persona_archetype/persona_archetype")
    structured = create_llm(app_config, streaming=False).with_structured_output(
        PersonaGenerationPlan
    )
    mission_by_id = {
        mission.mission_id.strip(): mission for mission in program.missions
    }
    if {report.mission_id.strip() for report in reports} != set(mission_by_id):
        raise RuntimeError("AI 研究计划与已验证研究报告无法一一对应")

    synthesis_semaphore = asyncio.Semaphore(RESEARCH_CONCURRENCY)

    async def _synthesize_report(report: Any, global_correction: str) -> Any:
        mission = mission_by_id[report.mission_id.strip()]
        verified_urls = list(dict.fromkeys(source.url for source in report.sources))
        normalized_sources = {
            normalize_url(str(url or "")): url
            for url in verified_urls
            if normalize_url(str(url or ""))
        }
        verified_insights = {
            (
                insight.dimension.strip(),
                insight.finding.strip(),
                insight.applicability.strip(),
            ): insight
            for insight in report.insights
        }
        payload = {
            "background": background,
            "persona_count": mission.persona_count,
            "name_preference": name,
            "organization_hint": company,
            "position_hint": position,
            "extra_constraints": extra,
            "research_mission": mission.model_dump(),
            "verified_research_report": report.model_dump(),
        }
        correction = global_correction
        async with synthesis_semaphore:
            for _attempt in range(2):
                with observation_context(
                    project_id=project_id,
                    task_id=task_id,
                    phase="persona_archetype",
                    agent="persona_archetype",
                    task_type="persona_archetype",
                ):
                    plan = await _invoke_model(
                        lambda: structured.ainvoke(
                            [
                                SystemMessage(content=prompt),
                                HumanMessage(
                                    content=json.dumps(payload, ensure_ascii=False)
                                    + correction
                                ),
                            ]
                        ),
                        phase=f"人设原型综合 {mission.mission_id}",
                    )
                names = [item.fictional_name.strip() for item in plan.archetypes]
                validation_errors: list[str] = []
                if _contains_marker(plan.research_summary, _RESEARCH_GAP_MARKERS):
                    validation_errors.append(
                        "research_summary 使用了研究缺口代替已获得的正向背景结论"
                    )
                for item_index, archetype in enumerate(plan.archetypes):
                    mapped_sources = [
                        normalized_sources[url]
                        for value in archetype.source_urls
                        if (url := normalize_url(str(value or ""))) in normalized_sources
                    ]
                    archetype.source_urls = list(dict.fromkeys(mapped_sources))
                    mapped_evidence = []
                    for evidence in archetype.research_evidence:
                        evidence_key = (
                            evidence.dimension.strip(),
                            evidence.finding.strip(),
                            evidence.applicability.strip(),
                        )
                        verified = verified_insights.get(evidence_key)
                        if verified is not None:
                            mapped_evidence.append(verified)
                    archetype.research_evidence = list(
                        {
                            (
                                evidence.dimension,
                                evidence.finding,
                                evidence.applicability,
                            ): evidence
                            for evidence in mapped_evidence
                        }.values()
                    )
                    required_source_count = min(
                        MIN_VERIFIED_SOURCES_PER_ARCHETYPE,
                        len(verified_urls),
                    )
                    if len(archetype.source_urls) < required_source_count:
                        validation_errors.append(
                            f"原型 {item_index + 1} 未使用至少 "
                            f"{required_source_count} 个已验证来源"
                        )
                    if len(archetype.research_evidence) < 4:
                        validation_errors.append(
                            f"原型 {item_index + 1} 未精确引用至少 4 条研究洞察"
                        )
                    validation_errors.extend(
                        f"原型 {item_index + 1}：{issue}"
                        for issue in _archetype_quality_issues(archetype)
                    )
                if (
                    len(plan.archetypes) == mission.persona_count
                    and all(names)
                    and len(set(names)) == len(names)
                    and not validation_errors
                ):
                    plan.source_urls = verified_urls
                    return plan
                correction = (
                    "\n上一次原型计划不合格：必须恰好输出 "
                    f"{mission.persona_count} 个具体原型，分片内姓名必须唯一；"
                    f"每个原型必须引用至少 {MIN_VERIFIED_SOURCES_PER_ARCHETYPE} 个"
                    "已验证 URL，并从输入 insights 中逐字复制至少 4 条正向事实的"
                    " dimension、finding、applicability 组成 research_evidence；不得"
                    "把研究缺口、未覆盖维度、空壳或待补充内容写成人物原型。"
                    + (
                        "问题：" + "；".join(validation_errors[:8])
                        if validation_errors
                        else ""
                    )
                    + global_correction
                )
        raise RuntimeError(f"AI 原型综合分片 {mission.mission_id} 未通过信息来源约束")

    global_correction = ""
    for _global_attempt in range(2):
        partial_plans = await asyncio.gather(
            *(_synthesize_report(report, global_correction) for report in reports)
        )
        archetypes = [
            archetype for partial in partial_plans for archetype in partial.archetypes
        ]
        names = [archetype.fictional_name.strip() for archetype in archetypes]
        if len(archetypes) == count and len(set(names)) == count:
            verified_urls = list(
                dict.fromkeys(
                    source.url for report in reports for source in report.sources
                )
            )
            return PersonaGenerationPlan(
                research_summary="\n\n".join(
                    partial.research_summary.strip()
                    for partial in partial_plans
                    if partial.research_summary.strip()
                ),
                source_urls=verified_urls,
                archetypes=archetypes,
            )
        duplicate_names = sorted(
            {value for value in names if names.count(value) > 1}
        )
        global_correction = (
            "\n跨分片虚构姓名发生重复。请重新生成本分片全部姓名，且不得使用以下姓名："
            + "、".join(duplicate_names)
        )
    raise RuntimeError("AI 原型综合未满足总数或跨分片唯一姓名约束")


async def generate_personas(
    db: AsyncIOMotorDatabase,
    app_config: Any,
    *,
    background: str,
    count: int = DEFAULT_PERSONA_COUNT,
    industries: Sequence[str] | None = None,
    age_ranges: Sequence[str] | None = None,
    personalities: Sequence[str] | None = None,
    name: str = "",
    project_id: str = "",
    company: str = "",
    position: str = "",
    extra: str = "",
    task_id: str = "",
    source: str = "synthetic_research",
    existing_profiles: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Research context and generate a diverse batch of fictional personas."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from api.dao import persons as persons_dao
    from api.services.notifications import notify_event_background
    from core.observability import obs_log, observation_context
    from Sere1nGraph.graph.agents.runtime import create_llm
    from Sere1nGraph.graph.prompts.loader import load_prompt
    from Sere1nGraph.graph.skills.schemas import (
        PersonaConsistencyReview,
        PersonaProfile,
        RichFictionalPersonaProfile,
    )

    background = str(background or "").strip()
    if not background:
        raise ValueError("背景设定不能为空")
    count = max(1, min(int(count or DEFAULT_PERSONA_COUNT), MAX_PERSONAS_PER_BATCH))
    existing_profile_values = list(existing_profiles or [])
    if existing_profile_values and len(existing_profile_values) != count:
        raise ValueError("增量人设数量必须与本轮生成数量一致")
    industry_values = _clean_hints(industries)
    age_values = _clean_hints(age_ranges)
    personality_values = _clean_hints(personalities)
    batch_key = _stable_key(
        {
            "background": background,
            "industries": industry_values,
            "age_ranges": age_values,
            "personalities": personality_values,
            "name": name.strip(),
            "company": company.strip(),
            "position": position.strip(),
            "extra": extra.strip(),
        }
    )
    obs_log(
        "虚构人设背景研究开始",
        task_id=task_id,
        project_id=project_id,
        source="persona_research",
        level="notice",
        event="research_start",
        data={
            "count": count,
            "research_missions": (count + RESEARCH_BATCH_SIZE - 1) // RESEARCH_BATCH_SIZE,
            "user_dimension_hints": bool(
                industry_values or age_values or personality_values
            ),
        },
    )
    try:
        await _update_research_task(
            db,
            task_id,
            stage="planning",
            message="AI 正在规划差异化公网研究分片",
            details={"requested_count": count},
        )
        program = await _plan_research_program(
            app_config,
            background=background,
            count=count,
            industries=industry_values,
            age_ranges=age_values,
            personalities=personality_values,
            company=company,
            position=position,
            extra=extra,
            project_id=project_id,
            task_id=task_id,
        )
        await _update_research_task(
            db,
            task_id,
            stage="researching",
            message=f"正在并行执行 {len(program.missions)} 个公网研究分片",
            details={"research_missions": len(program.missions)},
        )
        reports = await _research_program_batches(
            app_config,
            program=program,
            background=background,
            industries=industry_values,
            age_ranges=age_values,
            personalities=personality_values,
            company=company,
            position=position,
            extra=extra,
            project_id=project_id,
            task_id=task_id,
            existing_source_urls=list(
                dict.fromkeys(
                    str(url).strip()
                    for profile in existing_profile_values
                    for url in profile.get("source_urls") or []
                    if str(url).strip()
                )
            ),
        )
        await _update_research_task(
            db,
            task_id,
            stage="synthesizing",
            message="正在将已验证来源综合为具体人物原型",
            details={
                "research_missions": len(reports),
                "verified_sources": len(
                    {
                        str(getattr(source, "url", "") or "").strip()
                        for report in reports
                        for source in getattr(report, "sources", [])
                        if str(getattr(source, "url", "") or "").strip()
                    }
                ),
                "verified_insights": sum(
                    len(getattr(report, "insights", []) or []) for report in reports
                ),
            },
        )
        plan = await _synthesize_archetypes(
            app_config,
            background=background,
            count=count,
            program=program,
            reports=reports,
            name=name,
            company=company,
            position=position,
            extra=extra,
            project_id=project_id,
            task_id=task_id,
        )
    except Exception as exc:  # noqa: BLE001
        obs_log(
            f"虚构人设背景研究失败: {exc}",
            task_id=task_id,
            project_id=project_id,
            source="persona_research",
            level="error",
            event="research_error",
            data={"error": str(exc)},
        )
        notify_event_background(
            event="persona_research_failed",
            title="虚构人设背景研究失败",
            content=f"人设背景研究失败：{exc}",
            level="error",
            source="persona_research",
            project_id=project_id or None,
            task_id=task_id or None,
            context={"count": count},
        )
        raise

    obs_log(
        "虚构人设背景研究完成",
        task_id=task_id,
        project_id=project_id,
        source="persona_research",
        level="notice",
        event="research_done",
        data={
            "archetypes": len(plan.archetypes),
            "sources": len(plan.source_urls),
            "research_missions": len(program.missions),
            "research_insights": sum(len(report.insights) for report in reports),
        },
    )

    await _update_research_task(
        db,
        task_id,
        stage="generating",
        message=f"正在生成并审校 {len(plan.archetypes)} 条具体虚构人设",
        details={
            "archetypes": len(plan.archetypes),
            "verified_sources": len(plan.source_urls),
        },
    )

    prompt = load_prompt("persona_collect/persona_collect")
    consistency_prompt = load_prompt("persona_consistency/persona_consistency")
    llm = create_llm(app_config, streaming=False)
    structured = llm.with_structured_output(RichFictionalPersonaProfile)
    consistency_structured = llm.with_structured_output(PersonaConsistencyReview)
    semaphore = asyncio.Semaphore(GENERATION_CONCURRENCY)

    async def _generate(index: int, archetype: Any) -> dict[str, Any]:
        async with semaphore:
            existing_profile = (
                existing_profile_values[index] if existing_profile_values else None
            )
            brief = _persona_brief(background, archetype, index)
            archetype_json = json.dumps(
                archetype.model_dump(),
                ensure_ascii=False,
                indent=2,
            )
            with observation_context(
                project_id=project_id,
                task_id=task_id,
                phase="persona_generate",
                agent="persona_generate",
                task_type="persona_generate",
            ):
                generated = await _invoke_model(
                    lambda: structured.ainvoke(
                        [
                            SystemMessage(content=prompt),
                            HumanMessage(
                                content=(
                                    "根据以下已研究的通用背景原型生成一名全新虚构人物。"
                                    "不得复用来源中的真实姓名或联系方式。\n"
                                    f"姓名偏好：{name or '自动生成普通虚构姓名'}\n"
                                    f"原型：\n{archetype_json}"
                                    + (
                                        "\n这是同一虚构人设的持续升级。先读取已有 summary，再阅读"
                                        "完整档案；保持身份与既有事实，只使用新研究补充和细化信息。"
                                        "\n已有 summary："
                                        + str(existing_profile.get("summary") or "")
                                        + "\n已有完整档案："
                                        + json.dumps(
                                            _profile_for_ai(existing_profile),
                                            ensure_ascii=False,
                                        )
                                        if existing_profile
                                        else ""
                                    )
                                )
                            ),
                        ]
                    ),
                    phase=f"第 {index + 1} 条人设生成",
                )
            parsed = (
                generated.model_dump()
                if hasattr(generated, "model_dump")
                else dict(generated or {})
            )
            generation_key = str(
                (existing_profile or {}).get("generation_key") or ""
            ).strip() or _stable_key({"batch_key": batch_key, "slot": index})

            def _materialize(candidate: dict[str, Any]) -> dict[str, Any]:
                merged_candidate = _merge_existing_profile(
                    existing_profile,
                    candidate,
                )
                enforced = _enforce_fictional_profile(
                    merged_candidate,
                    brief=brief,
                    index=index,
                    archetype=archetype,
                    generation_key=generation_key,
                    global_sources=[],
                    company=company,
                    position=position,
                    existing_profile=existing_profile,
                )
                return PersonaProfile(**enforced).model_dump()

            profile = _materialize(parsed)
            review_feedback = ""
            for review_round in range(2):
                with observation_context(
                    project_id=project_id,
                    task_id=task_id,
                    phase="persona_consistency",
                    agent="persona_consistency",
                    task_type="persona_consistency",
                ):
                    review = await _invoke_model(
                        lambda: consistency_structured.ainvoke(
                            [
                                SystemMessage(content=consistency_prompt),
                                HumanMessage(
                                    content=(
                                        "请依据研究原型审校候选虚构人物，并按 PersonaConsistencyReview "
                                        "Schema 输出完整修订结果。\n"
                                        f"研究原型：{archetype_json}\n"
                                        "候选档案："
                                        + json.dumps(profile, ensure_ascii=False)
                                        + review_feedback
                                    )
                                ),
                            ]
                        ),
                        phase=f"第 {index + 1} 条人设一致性审校",
                    )
                reviewed_dict = review.profile.model_dump()
                profile = _materialize(reviewed_dict)
                quality_issues = _profile_quality_issues(profile)
                if review.consistent and not quality_issues:
                    break
                unresolved = [*list(review.issues_found or []), *quality_issues]
                review_feedback = (
                    "\n上一次审校仍未通过，请修复以下全部问题后再输出完整档案："
                    + "；".join(unresolved)
                )
                if review_round == 1:
                    raise RuntimeError(
                        f"第 {index + 1} 个人设 AI 一致性审校未通过："
                        + "；".join(unresolved[:8])
                    )
            if not str(profile.get("name") or "").strip():
                raise RuntimeError(f"第 {index + 1} 个人设缺少虚构姓名")
            return await persons_dao.upsert_person(
                db,
                profile=profile,
                project_id=project_id,
                source=source,
                ref_id=f"archetype:{index + 1}",
                task_id=task_id,
            )

    obs_log(
        "虚构人设批量生成开始",
        task_id=task_id,
        project_id=project_id,
        source="persona_generate",
        level="notice",
        event="generate_start",
        data={"count": count, "concurrency": GENERATION_CONCURRENCY},
    )
    results = await asyncio.gather(
        *(_generate(index, archetype) for index, archetype in enumerate(plan.archetypes)),
        return_exceptions=True,
    )
    items = [result for result in results if isinstance(result, dict)]
    errors = [str(result) for result in results if isinstance(result, Exception)]
    if not items:
        raise RuntimeError("虚构人设批量生成全部失败：" + "；".join(errors[:3]))

    obs_log(
        "虚构人设批量生成完成",
        task_id=task_id,
        project_id=project_id,
        source="persona_generate",
        level="notice" if not errors else "warning",
        event="generate_done",
        data={"requested": count, "generated": len(items), "failed": len(errors)},
    )
    logger.info(
        "[persona_generate] task=%s requested=%s generated=%s failed=%s",
        task_id,
        count,
        len(items),
        len(errors),
    )
    return {
        "items": items,
        "requested": count,
        "generated": len(items),
        "errors": errors,
        "research_summary": plan.research_summary,
        "source_urls": list(plan.source_urls or []),
    }


async def enrich_persona(
    db: AsyncIOMotorDatabase,
    app_config: Any,
    *,
    person_id: str,
    project_id: str = "",
    extra: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    """Research fresh sources and incrementally upgrade one fictional persona."""
    from api.dao import persons as persons_dao

    existing = await persons_dao.get_person(db, person_id)
    if not existing:
        raise ValueError("人设不存在")
    if existing.get("is_fictional") is not True:
        raise ValueError("持续研究仅用于不对应真实自然人的虚构人设")

    profile_fields = (
        "organization_context",
        "career_stage",
        "career_path",
        "life_stage",
        "work_context",
        "work_rhythm",
        "decision_style",
        "communication_style",
        "collaboration_style",
        "technology_attitude",
        "learning_style",
        "stress_response",
        "information_preferences",
        "digital_habits",
        "motivations",
        "goals",
        "pain_points",
        "values",
        "behavior_patterns",
        "content_preferences",
        "purchase_considerations",
    )
    missing_fields = [field for field in profile_fields if not existing.get(field)]
    evidence_dimensions = list(
        dict.fromkeys(
            str(item.get("dimension") or "").strip()
            for item in existing.get("research_evidence") or []
            if isinstance(item, dict) and str(item.get("dimension") or "").strip()
        )
    )
    enrichment_context = "\n".join(
        [
            "这是同一虚构人设的持续研究任务，不创建新身份。",
            f"已有 summary：{str(existing.get('summary') or '').strip()}",
            f"当前资料版本：{int(existing.get('profile_version') or 1)}",
            f"已有证据维度：{'、'.join(evidence_dimensions) or '尚无结构化证据'}",
            f"优先补充字段：{'、'.join(missing_fields) or '继续细化现有维度'}",
            "必须研究新的公开来源，补充具体信息，并保持姓名、年龄、教育、职业和生活时间线不变。",
            str(extra or "").strip(),
        ]
    )
    background = str(
        existing.get("generation_brief")
        or existing.get("summary")
        or existing.get("background")
        or "持续完善一名完全虚构人物的职业与生活背景"
    ).strip()
    result = await generate_personas(
        db,
        app_config,
        background=background,
        count=1,
        industries=[str(existing.get("industry") or "").strip()],
        age_ranges=[str(existing.get("age_range") or "").strip()],
        personalities=[str(existing.get("personality") or "").strip()],
        name=str(existing.get("name") or "").strip(),
        project_id=project_id,
        company=str(existing.get("company") or "").strip(),
        position=str(existing.get("position") or "").strip(),
        extra=enrichment_context,
        task_id=task_id,
        source="synthetic_research_enrichment",
        existing_profiles=[existing],
    )
    return result["items"][0]


async def collect_persona(
    db: AsyncIOMotorDatabase,
    app_config: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Backward-compatible single-person wrapper around the batch pipeline."""
    if not str(kwargs.get("background") or "").strip():
        parts = ["生成一名完全虚构、但职业与生活背景内部一致的人物"]
        if str(kwargs.get("company") or "").strip():
            parts.append(f"组织背景为 {str(kwargs['company']).strip()}")
        if str(kwargs.get("position") or "").strip():
            parts.append(f"岗位背景为 {str(kwargs['position']).strip()}")
        if str(kwargs.get("extra") or "").strip():
            parts.append(str(kwargs["extra"]).strip())
        kwargs["background"] = "；".join(parts)
    kwargs.pop("count", None)
    result = await generate_personas(db, app_config, count=1, **kwargs)
    return result["items"][0]
