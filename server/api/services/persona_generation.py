"""Unified generation entry: complete contexts by default, research by choice."""
from __future__ import annotations

from api.dao import persons as persons_dao


async def _context(db, app_config, **kwargs):
    from api.services.persona_context import generate_contexts
    return await generate_contexts(db, app_config, **kwargs)


async def _researched(db, app_config, **kwargs):
    from api.services.persona_collect import generate_personas
    for key in ("industry_code", "industry_sector_code"):
        kwargs.pop(key, None)
    return await generate_personas(db, app_config, **kwargs)


GENERATORS = {"context": _context, "researched": _researched}


async def generate_personas(db, app_config, *, generation_mode: str = "context", **kwargs):
    try:
        generator = GENERATORS[generation_mode]
    except KeyError as exc:
        raise ValueError("未知的人设生成方式") from exc
    return await generator(db, app_config, **kwargs)


async def enrich_persona(db, app_config, *, person_id: str, project_id: str = "", extra: str = "", task_id: str = "", generation_mode: str = "context"):
    existing = await persons_dao.get_person(db, person_id)
    if not existing or existing.get("is_fictional") is not True:
        raise ValueError("仅支持完善已有虚构人设")
    result = await generate_personas(db, app_config, generation_mode=generation_mode,
        background=existing.get("generation_brief") or existing.get("summary") or "完善既有人设的完整上下文",
        count=1, name=existing["name"], company=existing.get("company", ""), position=existing.get("position", ""),
        industries=[existing.get("industry", "")], existing_profiles=[existing], project_id=project_id,
        task_id=task_id, extra=extra, source="synthetic_research_enrichment")
    return result["items"][0]
