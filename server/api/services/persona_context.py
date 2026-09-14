"""Bounded concurrent fictional context generation, with durable profile writes."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone

from api.dao import persons as persons_dao
from api.dao import persona_research_tasks as task_dao
from api.schemas.persona_context import ContextPlan, ContextReview, ContextSlot, FictionalContextProfile
from api.services.persona_quality import _profile_quality_issues

_model_slots = asyncio.Semaphore(4)


async def _invoke(app_config, schema, prompt: str, payload: dict, *, task_id: str, project_id: str, phase: str):
    from langchain_core.messages import HumanMessage, SystemMessage
    from Sere1nGraph.graph.agents.runtime import create_llm
    from core.observability import observation_context
    async with _model_slots:
        with observation_context(task_id=task_id, project_id=project_id, phase=phase, agent="persona_context", task_type="persona_generate"):
            model = create_llm(app_config, workload="collection", streaming=False).with_structured_output(schema)
            return await asyncio.wait_for(model.ainvoke([
                SystemMessage(content=prompt), HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
            ]), timeout=180)


def materialize(candidate: dict, *, existing: dict | None, generation_key: str, background: str, scope: dict) -> dict:
    profile = FictionalContextProfile.model_validate(candidate).model_dump()
    if existing:
        # These are identity constraints, not generated replacements of an old person.
        for key in ("name", "age", "age_range", "gender", "company", "education", "generation_key", "industry", "industry_code", "industry_sector_code"):
            if existing.get(key) not in (None, "", {}):
                profile[key] = existing[key]
    profile.update({"is_fictional": True, "generation_mode": "context", "information_origin": "fictional_context",
        "generation_key": existing.get("generation_key", "") if existing else generation_key,
        "generation_brief": background, "context_complete": True,
        "contact": {"phone": "", "email": "", "wechat": "", "other_social": []}, "company_root_domain": ""})
    # Only supplied historical references may be retained. A model cannot create evidence.
    if existing and existing.get("scenario_contact"):
        profile["scenario_contact"] = existing["scenario_contact"]
    profile["sources"] = list((existing or {}).get("source_urls") or [])
    profile["evidence"] = list((existing or {}).get("evidence") or [])
    profile["research_evidence"] = list((existing or {}).get("research_evidence") or [])
    profile.update({key: value for key, value in scope.items() if value})
    return profile


async def _generate_one(db, app_config, *, slot: dict, index: int, request: dict, prompt: str, existing: dict | None) -> dict:
    task_id, project_id = request.get("task_id", ""), request.get("project_id", "")
    key = hashlib.sha256(json.dumps([task_id, request["background"], slot, index], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    scope = {key: request.get(key, "") for key in ("industry_code", "industry_sector_code")}
    payload = {"request": request, "slot": slot, "current_date": datetime.now(timezone.utc).date().isoformat(), "existing_profile": existing, "feedback": []}
    for attempt in range(3):
        generated = await _invoke(app_config, FictionalContextProfile, prompt, payload, task_id=task_id, project_id=project_id, phase="persona_context_generate")
        review = await _invoke(app_config, ContextReview, prompt, {**payload, "candidate": generated.model_dump(),
            "action": "独立校对并实际修订上下文。以 current_date 为准，逐项核算毕业年份、入职年份和累计工作年限、子女出生年份与年龄、当前职位与职业路径。稳定身份沿用 existing_profile；用具体修订消除矛盾，返回修订后完整档案。"},
            task_id=task_id, project_id=project_id, phase="persona_context_consistency")
        profile = materialize(review.profile.model_dump(), existing=existing, generation_key=key, background=request["background"], scope=scope)
        issues = _profile_quality_issues(profile, require_references=False)
        if not review.consistent:
            issues.extend(review.issues_found or ["上下文逻辑仍不一致"])
        if profile["name"] != slot["name"] or profile["industry"] != slot["industry"]:
            issues.append("姓名和 industry 必须与本轮 slot 精确一致")
        if not issues:
            profile["context_review"] = {"passed": True, "checked_at": datetime.now(timezone.utc).isoformat(), "issues_found": review.issues_found, "corrections_made": review.corrections_made}
            if request.get("dry_run"):
                return profile
            return await persons_dao.upsert_person(db, profile=profile, project_id=project_id,
                source="synthetic_context", ref_id=f"context:{index}", task_id=task_id)
        payload.update({"candidate": profile, "feedback": issues})
    raise RuntimeError("人设上下文校验未通过：" + "；".join(issues[:8]))


async def _plan(app_config, request: dict, prompt: str, existing: list[dict]) -> list[dict]:
    if existing:
        return [ContextSlot(name=item["name"], industry=item["industry"], position=item["position"],
            direction="保持已有身份与时间线，完整补齐公司、联系方式、工作和生活情境，不覆盖原有可追溯背景资料。").model_dump() for item in existing]
    result = await _invoke(app_config, ContextPlan, prompt, {"action": "只规划恰好 count 个不同人物的具体方向，不填写完整档案", **request},
        task_id=request.get("task_id", ""), project_id=request.get("project_id", ""), phase="persona_context_plan")
    slots = [item.model_dump() for item in result.slots]
    if len(slots) != request["count"] or len({item["name"] for item in slots}) != len(slots):
        raise ValueError("人设规划数量或姓名唯一性未通过校验")
    industries = [value for value in request.get("industries") or [] if value]
    if industries and any(item["industry"] not in industries for item in slots):
        raise ValueError("人设规划必须使用指定行业的完整名称")
    return slots


async def generate_contexts(db, app_config, *, background: str = "", count: int = 36, existing_profiles=None, **kwargs) -> dict:
    from api.services.persona_coverage.catalog import default_background
    from Sere1nGraph.graph.prompts.loader import load_prompt
    from core.observability import obs_log
    count = max(1, min(int(count or 1), 60))
    existing = list(existing_profiles or [])
    if existing and len(existing) != count:
        raise ValueError("持续完善的数量必须与已有档案一致")
    request = {**kwargs, "background": background or default_background(kwargs.get("industries")), "count": count}
    request.pop("source", None)
    task_id = request.get("task_id", "")
    if task_id and not request.get("dry_run"):
        await task_dao.update_task(db, task_id, status="running", stage="generating_context", message=f"正在自动补齐 {count} 条完整虚构人设，无需来源核验", details={"generation_mode": "context"})
    prompt = load_prompt("persona_context/persona_context")
    slots = await _plan(app_config, request, prompt, existing)
    results = await asyncio.gather(*(_generate_one(db, app_config, slot=slot, index=index, request=request,
        prompt=prompt, existing=existing[index] if existing else None) for index, slot in enumerate(slots)), return_exceptions=True)
    items = [item for item in results if isinstance(item, dict)]
    errors = [str(item)[:600] for item in results if isinstance(item, Exception)]
    obs_log("虚构人设上下文生成完成", task_id=task_id, project_id=request.get("project_id", ""), source="persona_context",
        event="generate_done", level="warning" if errors else "notice", data={"requested": count, "generated": len(items), "failed": len(errors)})
    if not items:
        raise RuntimeError("上下文生成未完成：" + "；".join(errors[:3]))
    return {"items": [] if request.get("dry_run") else items, "preview": items if request.get("dry_run") else [], "requested": count, "generated": len(items), "errors": errors, "source_urls": [], "generation_mode": "context"}
