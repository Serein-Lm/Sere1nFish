"""Researched, background-driven fictional persona generation.

Pipeline:
1. A Chrome-backed Agent researches generic industry/role/life-stage context.
2. A text-model batch generates fictional ``PersonaProfile`` documents from a
   diverse archetype matrix.
3. Profiles are persisted by stable background fingerprints.

No stage collects or reuses real-person identity data.  Research and generation
use separate observation phases so token usage remains attributable.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any, Sequence

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.logger import get_logger

logger = get_logger("persona_generate")

DEFAULT_INDUSTRIES = (
    "互联网与软件",
    "制造业",
    "金融服务",
    "医疗健康",
    "教育与科研",
    "零售与消费",
    "媒体与文化",
    "物流与供应链",
    "能源与工程",
    "公共服务",
)
DEFAULT_AGE_RANGES = ("22-29", "30-39", "40-49", "50-59")
DEFAULT_PERSONALITIES = (
    "谨慎理性、重流程",
    "外向主动、结果导向",
    "稳定协作、低冲突",
    "独立创新、好奇心强",
    "规则导向、风险敏感",
    "共情细致、关系导向",
)
MAX_PERSONAS_PER_BATCH = 40
GENERATION_CONCURRENCY = 4


def _clean_dimensions(values: Sequence[str] | None, defaults: Sequence[str]) -> list[str]:
    cleaned = list(dict.fromkeys(str(value).strip() for value in (values or []) if str(value).strip()))
    return cleaned or list(defaults)


def _stable_key(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _dimension_matrix(
    count: int,
    industries: Sequence[str],
    age_ranges: Sequence[str],
    personalities: Sequence[str],
) -> list[dict[str, str]]:
    """Build deterministic, balanced slots that the research Agent must fill."""
    industry_count = len(industries)
    return [
        {
            "industry": industries[index % industry_count],
            "age_range": age_ranges[(index + index // industry_count) % len(age_ranges)],
            "personality": personalities[
                (index + 2 * (index // industry_count)) % len(personalities)
            ],
        }
        for index in range(count)
    ]


def _age_in_range(value: Any, age_range: str, index: int) -> int | None:
    bounds = [int(part) for part in re.findall(r"\d+", str(age_range or ""))[:2]]
    if not bounds:
        return None
    low = max(18, bounds[0])
    high = min(75, bounds[-1])
    if high < low:
        low, high = high, low
    try:
        age = int(value)
    except (TypeError, ValueError):
        age = low + index % (high - low + 1)
    return age if low <= age <= high else low + index % (high - low + 1)


def _normalize_work_years(value: Any) -> str:
    """Keep only a plausible complete year-count scalar from model output."""
    text = str(value or "").strip()
    match = re.fullmatch(r"[,，\s]*(\d{1,2})\s*年?", text)
    if not match:
        return ""
    years = int(match.group(1))
    return f"{years}年" if 0 <= years <= 60 else ""


def _research_request(
    *,
    background: str,
    count: int,
    industries: list[str],
    age_ranges: list[str],
    personalities: list[str],
    dimension_matrix: list[dict[str, str]],
    company: str,
    position: str,
    extra: str,
) -> str:
    required_slots = "\n".join(
        f"- 原型 {index + 1}: 行业={slot['industry']}；年龄段={slot['age_range']}；"
        f"核心性格={slot['personality']}"
        for index, slot in enumerate(dimension_matrix)
    )
    return "\n".join(
        [
            f"生成数量：{count}",
            f"总体背景：{background}",
            f"行业维度：{'、'.join(industries)}",
            f"年龄维度：{'、'.join(age_ranges)}",
            f"性格维度：{'、'.join(personalities)}",
            f"组织设定：{company or '不限，可使用虚构组织'}",
            f"职位设定：{position or '不限，按行业合理分布'}",
            f"补充约束：{extra or '无'}",
            "必须按以下顺序逐项填充原型，不得更改每项的行业、年龄段和核心性格：",
            required_slots,
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
        f"性格：{'、'.join(payload.get('personality_traits') or [])}；"
        f"背景重点：{payload.get('background_focus', '')}"
    )


def _enforce_fictional_profile(
    parsed: dict[str, Any],
    *,
    brief: str,
    index: int,
    archetype: Any,
    dimension_slot: dict[str, str] | None = None,
    generation_key: str = "",
    global_sources: list[str],
    company: str,
    position: str,
) -> dict[str, Any]:
    """Apply identity, privacy, dimension, and provenance invariants."""
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
    slot = dimension_slot or {}
    profile["industry"] = (
        slot.get("industry") or archetype_data.get("industry") or profile.get("industry") or ""
    )
    profile["age_range"] = (
        slot.get("age_range")
        or archetype_data.get("age_range")
        or profile.get("age_range")
        or ""
    )
    profile["age"] = _age_in_range(profile.get("age"), profile["age_range"], index)
    profile["work_years"] = _normalize_work_years(profile.get("work_years"))
    profile["position"] = position.strip() or archetype_data.get("role") or profile.get("position") or ""
    profile["position_level"] = (
        archetype_data.get("position_level") or profile.get("position_level") or ""
    )
    profile["location"] = archetype_data.get("region") or profile.get("location") or ""
    profile["company_root_domain"] = ""
    profile["contact"] = {
        "phone": "",
        "email": "",
        "wechat": "",
        "other_social": [],
    }
    required_personality = str(slot.get("personality") or "").strip()
    personality = str(profile.get("personality") or "").strip()
    if required_personality and required_personality not in personality:
        profile["personality"] = "；".join(
            value for value in (required_personality, personality) if value
        )
    profile["sources"] = source_urls
    profile["evidence"] = list(archetype_data.get("context_evidence") or [])
    if company.strip():
        profile["company"] = company.strip()
    summary = str(profile.get("summary") or "").strip()
    if summary and "虚构" not in summary:
        profile["summary"] = f"【虚构人设】{summary}"
    return profile


async def _research_archetypes(
    app_config: Any,
    *,
    request_text: str,
    count: int,
    project_id: str,
    task_id: str,
) -> Any:
    from langchain_core.messages import HumanMessage

    from api.services.info_collection.url_tools import _build_worker_chrome_config
    from browser_manager.provider import get_browser_provider
    from core.observability import observation_context
    from Sere1nGraph.graph.agents.factory import create_persona_research_agent
    from Sere1nGraph.graph.agents.runtime import extract_with_retry
    from Sere1nGraph.graph.prompts.loader import load_prompt
    from Sere1nGraph.graph.skills.schemas import PersonaGenerationPlan

    provider = get_browser_provider()
    cdp_task_id = f"persona_research_{task_id or _stable_key(request_text)}"
    cdp_url = await provider.get_cdp_endpoint(
        task_id=cdp_task_id,
        purpose="persona_research",
    )
    if not cdp_url:
        raise RuntimeError("无法获取 Chrome 容器进行人设背景研究")

    prompt = load_prompt("persona_research/persona_research")
    try:
        with observation_context(
            project_id=project_id,
            task_id=task_id,
            phase="persona_research",
            agent="persona_research",
            task_type="persona_research",
        ):
            worker_config = _build_worker_chrome_config(app_config, cdp_url)
            agent = await create_persona_research_agent(worker_config)
            raw = await agent(
                {
                    "messages": [
                        HumanMessage(
                            content=(
                                "请爬取公开行业与岗位背景，规划虚构人物原型矩阵。\n"
                                f"{request_text}"
                            )
                        )
                    ]
                }
            )
            parsed = await extract_with_retry(raw, worker_config, system_prompt=prompt) or {}
    finally:
        try:
            await provider.release_cdp_endpoint(cdp_task_id)
        except Exception:
            pass

    plan = PersonaGenerationPlan(**parsed)
    if len(plan.archetypes) < count:
        raise RuntimeError(
            f"背景研究只返回 {len(plan.archetypes)} 个原型，少于要求的 {count} 个"
        )
    plan.archetypes = plan.archetypes[:count]
    names = [item.fictional_name.strip() for item in plan.archetypes]
    if any(not value for value in names) or len(set(names)) != len(names):
        raise RuntimeError("背景研究返回的虚构姓名为空或重复")
    return plan


async def generate_personas(
    db: AsyncIOMotorDatabase,
    app_config: Any,
    *,
    background: str,
    count: int = 12,
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
) -> dict[str, Any]:
    """Research context and generate a diverse batch of fictional personas."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from api.dao import persons as persons_dao
    from api.services.notifications import notify_event_background
    from core.observability import obs_log, observation_context
    from Sere1nGraph.graph.agents.runtime import create_llm
    from Sere1nGraph.graph.prompts.loader import load_prompt
    from Sere1nGraph.graph.skills.schemas import PersonaProfile

    background = str(background or "").strip()
    if not background:
        raise ValueError("背景设定不能为空")
    count = max(1, min(int(count or 12), MAX_PERSONAS_PER_BATCH))
    industry_values = _clean_dimensions(industries, DEFAULT_INDUSTRIES)
    age_values = _clean_dimensions(age_ranges, DEFAULT_AGE_RANGES)
    personality_values = _clean_dimensions(personalities, DEFAULT_PERSONALITIES)
    dimension_matrix = _dimension_matrix(
        count,
        industry_values,
        age_values,
        personality_values,
    )
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
    request_text = _research_request(
        background=background,
        count=count,
        industries=industry_values,
        age_ranges=age_values,
        personalities=personality_values,
        dimension_matrix=dimension_matrix,
        company=company,
        position=position,
        extra=extra,
    )

    obs_log(
        "虚构人设背景研究开始",
        task_id=task_id,
        project_id=project_id,
        source="persona_research",
        level="notice",
        event="research_start",
        data={"count": count, "industries": industry_values, "age_ranges": age_values},
    )
    try:
        plan = await _research_archetypes(
            app_config,
            request_text=request_text,
            count=count,
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
        data={"archetypes": len(plan.archetypes), "sources": len(plan.source_urls)},
    )

    prompt = load_prompt("persona_collect/persona_collect")
    llm = create_llm(app_config, streaming=False)
    structured = llm.with_structured_output(PersonaProfile)
    semaphore = asyncio.Semaphore(GENERATION_CONCURRENCY)

    async def _generate(index: int, archetype: Any) -> dict[str, Any]:
        async with semaphore:
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
                generated = await structured.ainvoke(
                    [
                        SystemMessage(content=prompt),
                        HumanMessage(
                            content=(
                                "根据以下已研究的通用背景原型生成一名全新虚构人物。"
                                "不得复用来源中的真实姓名或联系方式。\n"
                                f"姓名偏好：{name or '自动生成普通虚构姓名'}\n"
                                f"原型：\n{archetype_json}"
                            )
                        ),
                    ]
                )
            parsed = (
                generated.model_dump()
                if hasattr(generated, "model_dump")
                else dict(generated or {})
            )
            profile = _enforce_fictional_profile(
                parsed,
                brief=brief,
                index=index,
                archetype=archetype,
                dimension_slot=dimension_matrix[index],
                generation_key=_stable_key({"batch_key": batch_key, "slot": index}),
                global_sources=list(plan.source_urls or []),
                company=company,
                position=position,
            )
            profile = PersonaProfile(**profile).model_dump()
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
    result = await generate_personas(db, app_config, count=1, **kwargs)
    return result["items"][0]
