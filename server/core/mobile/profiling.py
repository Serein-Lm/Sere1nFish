"""
系统3b — 人物画像识别与沉淀。

读屏(视觉) → default LLM 结构化提取画像 → 合并进现有画像 → 存 MongoDB。
聊天越多,画像越准。format_profile_for_prompt 把画像格式化给话术层用。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from Sere1nGraph.graph.agents.runtime import create_llm
from api.services.runtime_config import get_runtime_app_config

from api.db.mongodb import get_db
from api.dao import contact_profiles as cp_dao
from api.dao import findings as findings_dao
from api.dao import mobile_profile_observations as mpo_dao
from core.mobile.chat_assist import read_screen
from core.mobile.events import publish


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PersonaExtract(BaseModel):
    name: str | None = Field(default=None, description="联系人名称/昵称")
    background: str | None = Field(default=None, description="背景/职业/身份")
    personality: str | None = Field(default=None, description="性格特点")
    interests: list[str] = Field(default_factory=list, description="兴趣/关注点")
    communication_style: str | None = Field(
        default=None, description="沟通风格/语气偏好"
    )
    tone: str | None = Field(default=None, description="常见语气,如直接/委婉/热情/谨慎")
    reply_pattern: str | None = Field(default=None, description="回复节奏、长短、常见表达模式")
    common_phrases: list[str] = Field(default_factory=list, description="常用词、口头禅或固定句式")
    risk_signals: list[str] = Field(default_factory=list, description="可能影响沟通策略的风险或敏感点")
    summary: str | None = Field(
        default=None, description="综合画像摘要(用于指导针对性聊天)"
    )
    tags: list[str] = Field(default_factory=list, description="标签")
    confidence: float | None = Field(default=None, ge=0, le=1, description="本次提取置信度")


_EXTRACT_SYSTEM = (
    "你是人物画像分析师。基于聊天内容,提炼对方的画像,用于后续针对性沟通。"
    "重点识别身份背景、兴趣、沟通风格、常用说法、回复节奏、风险/敏感点。"
    "只输出有依据的信息;没有依据的字段请留空,不要编造。"
)


async def _extract_persona(
    chat_content: str, existing_persona: dict[str, Any] | None
) -> PersonaExtract:
    app_config = await get_runtime_app_config()
    llm = create_llm(
        app_config,
        model_name=app_config.runtime.models.default,
        streaming=False,
    )
    structured = llm.with_structured_output(PersonaExtract)
    existing_str = (
        json.dumps(existing_persona, ensure_ascii=False)
        if existing_persona
        else "无"
    )
    return await structured.ainvoke(
        [
            SystemMessage(content=_EXTRACT_SYSTEM),
            HumanMessage(
                content=(
                    f"已知画像:\n{existing_str}\n\n"
                    f"最新聊天内容:\n{chat_content}\n\n"
                    "请在已知画像基础上更新/补全。"
                )
            ),
        ]
    )


def _merge_persona(
    old: dict[str, Any] | None, new: PersonaExtract
) -> dict[str, Any]:
    old = old or {}
    merged = dict(old)
    for field in (
        "background",
        "personality",
        "communication_style",
        "tone",
        "reply_pattern",
        "summary",
        "confidence",
    ):
        value = getattr(new, field)
        if value is not None and value != "":
            merged[field] = value
    for field in ("interests", "tags", "common_phrases", "risk_signals"):
        values = getattr(new, field)
        merged[field] = sorted(set((old.get(field) or []) + list(values)))
    return merged


def _compact_extract(extract: PersonaExtract) -> dict[str, Any]:
    data = extract.model_dump(exclude_none=True)
    return {key: value for key, value in data.items() if value not in ("", [], {})}


async def analyze_and_update(
    device_id: str,
    contact_id: str,
    *,
    name: str | None = None,
    platform: str | None = None,
    screen_analysis: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
    source: str = "profile_analyze",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """读屏(或用传入的分析) → 提取画像 → 合并 → 存库,返回最新画像。"""
    evidence = dict(evidence or {})
    if screen_analysis is None:
        screen = await read_screen(
            device_id,
            project_id=project_id,
            task_id=task_id,
            contact_id=contact_id,
            source=source,
        )
        screen_analysis = screen["analysis"]
        for key in ("screenshot_id", "screenshot_url", "width", "height"):
            if screen.get(key) is not None:
                evidence[key] = screen.get(key)

    db = get_db()
    existing = await cp_dao.get_profile(db, contact_id)
    existing_persona = (existing or {}).get("persona", {})

    extract = await _extract_persona(screen_analysis, existing_persona)
    persona_patch = _compact_extract(extract)
    merged_persona = _merge_persona(existing_persona, extract)

    profile = await cp_dao.merge_persona(
        db,
        contact_id,
        merged_persona,
        name=name or extract.name,
        platform=platform,
        device_id=device_id,
        project_id=project_id,
    )

    finding_id: str | None = None
    if project_id:
        finding = await findings_dao.upsert_mobile_profile_finding(
            db,
            project_id=project_id,
            contact_id=contact_id,
            task_id=task_id,
            device_id=device_id,
            platform=platform,
            name=(profile or {}).get("name") or name or extract.name,
            persona=merged_persona,
            evidence=evidence,
        )
        finding_id = finding.get("finding_id")
        if finding_id:
            await cp_dao.link_project_finding(
                db, contact_id, project_id=project_id, finding_id=finding_id
            )
            await findings_dao.upsert_profile(
                db,
                finding_id,
                {
                    "project_id": project_id,
                    "task_id": task_id,
                    "contact_id": contact_id,
                    "user_id": f"mobile:{contact_id}",
                    "nickname": (profile or {}).get("name") or name or extract.name or contact_id,
                    "device_id": device_id,
                    "platform": platform,
                    "persona": merged_persona,
                    "profile_summary": merged_persona.get("summary"),
                    "communication_style": merged_persona.get("communication_style"),
                    "tone": merged_persona.get("tone"),
                    "reply_pattern": merged_persona.get("reply_pattern"),
                    "tags": merged_persona.get("tags") or [],
                    "common_phrases": merged_persona.get("common_phrases") or [],
                    "risk_signals": merged_persona.get("risk_signals") or [],
                    "attention_score": finding.get("attention_score", 0),
                    "evidence": evidence,
                },
            )

    await cp_dao.append_observation(
        db,
        contact_id,
        {
            "ts": _now(),
            "content": screen_analysis[:1000],
            "source": device_id,
            "finding_id": finding_id,
            "task_id": task_id,
        },
        project_id=project_id,
    )
    observation = await mpo_dao.insert_observation(
        db,
        contact_id=contact_id,
        project_id=project_id,
        finding_id=finding_id,
        task_id=task_id,
        device_id=device_id,
        platform=platform,
        contact_name=(profile or {}).get("name") or name or extract.name,
        source=source,
        screen_analysis=screen_analysis,
        persona_patch=persona_patch,
        persona_snapshot=merged_persona,
        evidence=evidence,
        metrics={
            "interests_count": len(merged_persona.get("interests") or []),
            "tags_count": len(merged_persona.get("tags") or []),
            "risk_signals_count": len(merged_persona.get("risk_signals") or []),
        },
    )

    # 画像更新推送(系统3:前端实时查看)
    publish(
        {
            "type": "profile_updated",
            "device_id": device_id,
            "contact_id": contact_id,
            "project_id": project_id,
            "finding_id": finding_id,
            "data": {
                "name": (profile or {}).get("name"),
                "summary": ((profile or {}).get("persona") or {}).get("summary"),
                "finding_id": finding_id,
                "observation_id": observation.get("observation_id"),
            },
        }
    )
    return profile or {}


def format_profile_for_prompt(profile: dict[str, Any] | None) -> str:
    """把画像格式化成话术层可用的文本(注入 chat_assist 的 contact_profile)。"""
    if not profile:
        return ""
    persona = profile.get("persona", {}) or {}
    lines: list[str] = []
    if profile.get("name"):
        lines.append(f"姓名/昵称: {profile['name']}")
    for label, key in (
        ("背景", "background"),
        ("性格", "personality"),
        ("沟通风格", "communication_style"),
        ("语气", "tone"),
        ("回复习惯", "reply_pattern"),
        ("综合画像", "summary"),
    ):
        if persona.get(key):
            lines.append(f"{label}: {persona[key]}")
    if persona.get("interests"):
        lines.append(f"兴趣/关注: {', '.join(persona['interests'])}")
    if persona.get("common_phrases"):
        lines.append(f"常用表达: {', '.join(persona['common_phrases'])}")
    if persona.get("risk_signals"):
        lines.append(f"注意点: {', '.join(persona['risk_signals'])}")
    return "\n".join(lines)
