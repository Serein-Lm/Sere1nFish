"""保存由 Agent 基于公开背景主动研究生成的虚构人设。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import persons as persons_dao
from api.services.source_documents.urls import canonicalize_source_url
from Sere1nGraph.graph.skills.schemas import RichFictionalPersonaProfile


def _generation_key(profile: dict[str, Any]) -> str:
    identity = {
        key: profile.get(key)
        for key in (
            "generation_brief",
            "industry",
            "position",
            "position_level",
            "age_range",
            "region_type",
            "organization_context",
        )
    }
    raw = json.dumps(identity, ensure_ascii=False, sort_keys=True, default=str)
    return "agent_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def validate_researched_persona(payload: dict[str, Any]) -> dict[str, Any]:
    profile = RichFictionalPersonaProfile.model_validate(payload).model_dump()
    if profile.get("is_fictional") is not True:
        raise ValueError("主动研究人设必须明确 is_fictional=true")
    if not str(profile.get("generation_brief") or "").strip():
        raise ValueError("主动研究人设必须说明 generation_brief")

    source_urls = list(
        dict.fromkeys(
            canonicalize_source_url(str(url or "").strip())
            for url in profile.get("sources") or []
        )
    )
    if not source_urls:
        raise ValueError("主动研究人设至少需要一个公开背景来源")
    known_urls = set(source_urls)
    for item in profile.get("research_evidence") or []:
        evidence_urls = list(
            dict.fromkeys(
                canonicalize_source_url(str(url or "").strip())
                for url in item.get("source_urls") or []
            )
        )
        if not evidence_urls or any(url not in known_urls for url in evidence_urls):
            raise ValueError("人设研究证据必须引用 sources 中已核验的 URL")
        item["source_urls"] = evidence_urls

    profile["sources"] = source_urls
    profile["generation_key"] = str(profile.get("generation_key") or "").strip() or _generation_key(profile)
    profile["contact"] = {
        "phone": "",
        "email": "",
        "wechat": "",
        "other_social": [],
    }
    profile["company_root_domain"] = ""
    return profile


async def save_researched_persona(
    db: AsyncIOMotorDatabase,
    *,
    profile: dict[str, Any],
    project_id: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    validated = validate_researched_persona(profile)
    existing = await persons_dao.get_person(
        db,
        persons_dao.fictional_person_id(validated["generation_key"]),
    )
    if existing:
        from api.services.persona_collect import merge_existing_persona_profile

        validated = validate_researched_persona(
            merge_existing_persona_profile(existing, validated)
        )
    return await persons_dao.upsert_person(
        db,
        profile=validated,
        project_id=project_id.strip(),
        source="synthetic_research:osint",
        task_id=task_id.strip(),
    )
