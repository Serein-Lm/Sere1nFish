"""Coverage planning and completeness, independent of UI and execution runtime."""
from __future__ import annotations

from collections import Counter
from api.dao import persona_coverage as dao
from api.dao import config as config_dao
from .catalog import catalog, ordered_divisions


def profile_ready(profile: dict) -> bool:
    return bool(len(str(profile.get("summary") or "")) >= 80 and profile.get("source_urls") and profile.get("research_evidence"))


def coverage_gaps(person_count: int, organization_count: int, phone_count: int, minimum: int) -> list[str]:
    return ([f"缺少 {minimum - person_count} 条完整人设"] if person_count < minimum else []) + (["缺少已核验机构背景"] if organization_count < 1 else []) + (["缺少已核验公开办公电话"] if phone_count < 1 else [])


async def coverage(db) -> dict:
    jobs, people, facts = await dao.snapshots(db)
    jobs_by_code = {item["industry_code"]: item for item in jobs}
    facts_by_code = {item["_id"]: item for item in facts}
    counts = Counter(item.get("industry_code") for item in people if profile_ready(item))
    names = {item["name"]: item["code"] for item in catalog()["sectors"]}
    sectors = Counter(item.get("industry_sector_code") or names.get(item.get("industry")) for item in people)
    rows = []
    for division in ordered_divisions():
        code = division["code"]
        job, fact = jobs_by_code.get(code, {}), facts_by_code.get(code, {})
        organization_count, phone_count = len(fact.get("organizations") or []), fact.get("phone_count", 0)
        minimum = job.get("minimum_personas", 4)
        gaps = coverage_gaps(counts[code], organization_count, phone_count, minimum)
        rows.append({**division, "person_count": counts[code], "minimum_personas": minimum, "organization_count": organization_count, "phone_count": phone_count, "source_count": len(fact.get("sources") or []), "gaps": gaps, "complete": not gaps, "job": job or None})
    return {"standard": catalog()["standard"], "source_url": catalog()["source_url"], "sectors": [{**item, "person_count": sectors[item["code"]]} for item in catalog()["sectors"]], "items": rows,
            "summary": {"person_count": len(people), "sector_count": sum(sectors[item["code"]] > 0 for item in catalog()["sectors"]), "division_count": sum(row["person_count"] > 0 for row in rows), "complete_count": sum(row["complete"] for row in rows), "unclassified_count": sum(not item.get("industry_code") for item in people), "organization_count": len({target for fact in facts for target in fact.get("organizations", [])}), "phone_count": sum(row["phone_count"] for row in rows), "running": sum(item["status"] == "running" for item in jobs), "queued": sum(item["status"] in {"queued", "retry", "needs_sources"} for item in jobs)}}


async def start_coverage(db, *, industry_codes: list[str], minimum_personas: int) -> dict:
    selected = set(industry_codes)
    divisions = [item for item in ordered_divisions() if not selected or item["code"] in selected]
    if selected - {item["code"] for item in divisions}:
        raise ValueError("存在无效的行业大类代码")
    result = await dao.enqueue(db, divisions, minimum_personas)
    current = await config_dao.get_config(db, "persona_coverage")
    await config_dao.set_config(db, "persona_coverage", {**((current or {}).get("config") or {}), "enabled": True})
    return result
