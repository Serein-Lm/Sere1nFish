"""Runtime sync for AI skills and prompts.

The agent-facing loaders remain synchronous and progressively disclosed. This
module is the async bridge that refreshes their in-memory snapshots from MongoDB
on startup and after CRUD writes.
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase


CORE_SKILL_SLUGS = {
    "base-objection",
    "base-scenario",
    "customer-service",
    "email",
    "finalize",
    "intranet",
    "it-support",
    "payload",
    "phone",
    "real-cases",
    "recruitment",
    "sms",
    "vendor",
    "wechat",
    "wechat-keywords",
    "xhs-keywords",
}

CORE_SKILLS_WITH_REFERENCES = {
    "customer-service",
    "email",
    "intranet",
    "it-support",
    "phone",
    "real-cases",
    "recruitment",
    "vendor",
    "wechat",
}

CORE_PROMPT_SLUGS = {
    "asset_triage/asset_triage",
    "bid_collect/bid_collect",
    "browser_chat/browser_chat",
    "copywriting/copywriting",
    "company_normalize/company_normalize",
    "company_router/company_router",
    "douyin_profile/douyin_profile",
    "douyin_profile/douyin_tagging",
    "profile_copywriting/profile_copywriting",
    "hub/classify",
    "hub/content",
    "hub/data",
    "hub/payload",
    "hub/persona",
    "router/classify",
    "source_document/relevance_review",
    "source_document/source_document",
    "web_tagging/tagging_taxonomy",
    "web_tagging/web_tagging",
    "wechat_target_selection/wechat_target_selection",
    "weixin_search/weixin_search",
    "xhs_collect/xhs_collect",
    "xhs_detail_tagging/xhs_detail_tagging",
    "xhs_note_detail_vl/note_detail_analysis",
    "xhs_note_tagging/xhs_note_tagging",
    "xhs_profile/xhs_profile",
    "xhs_profile_vl/vision_analysis",
    "xhs_target_selection/xhs_target_selection",
}


async def seed_libraries_if_required(db: AsyncIOMotorDatabase) -> dict[str, int]:
    """Seed repository defaults when the runtime-critical DB documents are missing."""
    from api.db.collections import SKILLS_COLLECTION, PROMPTS_COLLECTION
    from scripts.sync_to_db import sync_prompts, sync_skills

    result = {"skills_seeded": 0, "prompts_seeded": 0}
    skill_filter = {"slug": {"$in": list(CORE_SKILL_SLUGS)}}
    prompt_filter = {"slug": {"$in": list(CORE_PROMPT_SLUGS)}}
    reference_filter = {
        "slug": {"$in": list(CORE_SKILLS_WITH_REFERENCES)},
        "meta.reference_contents": {"$exists": True, "$ne": {}},
    }
    if (
        await db[SKILLS_COLLECTION].count_documents(skill_filter) < len(CORE_SKILL_SLUGS)
        or await db[SKILLS_COLLECTION].count_documents(reference_filter) < len(CORE_SKILLS_WITH_REFERENCES)
    ):
        await sync_skills(db, overwrite=False)
        result["skills_seeded"] = await db[SKILLS_COLLECTION].count_documents(skill_filter)
    if await db[PROMPTS_COLLECTION].count_documents(prompt_filter) < len(CORE_PROMPT_SLUGS):
        await sync_prompts(db, overwrite=False)
        result["prompts_seeded"] = await db[PROMPTS_COLLECTION].count_documents(prompt_filter)
    return result


async def refresh_skill_runtime(db: AsyncIOMotorDatabase) -> int:
    """Load approved skills from MongoDB into the agent skill registry snapshot."""
    from api.dao import skills as skills_dao
    from Sere1nGraph.graph.skills.registry import get_skill_registry

    result = await skills_dao.list_skills(
        db,
        status="approved",
        page=1,
        page_size=10000,
        sort_by="priority",
        include_content=True,
    )
    docs: list[dict[str, Any]] = result.get("items", [])
    return get_skill_registry().load_from_documents(docs)


async def refresh_prompt_runtime(db: AsyncIOMotorDatabase) -> int:
    """Load approved prompts from MongoDB into the prompt loader snapshot."""
    from api.dao import prompts as prompts_dao
    from Sere1nGraph.graph.prompts.loader import load_prompts_from_documents

    result = await prompts_dao.list_prompts(
        db,
        status="approved",
        page=1,
        page_size=10000,
        sort_by="slug",
    )
    docs: list[dict[str, Any]] = result.get("items", [])
    return load_prompts_from_documents(docs)


async def refresh_ai_libraries(
    db: AsyncIOMotorDatabase, *, seed_if_empty: bool = False
) -> dict[str, int]:
    """Refresh both agent-facing library snapshots from MongoDB."""
    seeded = (
        await seed_libraries_if_required(db)
        if seed_if_empty
        else {"skills_seeded": 0, "prompts_seeded": 0}
    )
    skills = await refresh_skill_runtime(db)
    prompts = await refresh_prompt_runtime(db)
    return {**seeded, "skills_loaded": skills, "prompts_loaded": prompts}
