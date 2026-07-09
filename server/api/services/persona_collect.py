"""
人设收集编排 Service

流程：人物线索 → AI 浏览器搜索公开渠道 → 结构化人物档案（PersonaProfile 约束）
     → 增量归并入库 persons（人设库真源）。

设计原则：
- 复用 create_persona_collect_agent + chrome-devtools MCP，不另起浏览器；
- AI 输出严格用 PersonaProfile schema 约束（extract_with_retry 解析 + LLM 修复）；
- 结果按稳定 person_id 增量归并，重复采集只更新/补全字段，不重复插入。
"""
from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.logger import get_logger

logger = get_logger("persona_collect")


def _build_clue(name: str, company: str, position: str, extra: str) -> str:
    parts = [f"姓名：{name}"]
    if company:
        parts.append(f"公司：{company}")
    if position:
        parts.append(f"职位：{position}")
    if extra:
        parts.append(f"其他线索：{extra}")
    return "；".join(parts)


async def collect_persona(
    db: AsyncIOMotorDatabase,
    app_config: Any,
    *,
    name: str,
    project_id: str = "",
    company: str = "",
    position: str = "",
    extra: str = "",
    task_id: str = "",
    source: str = "web",
) -> dict[str, Any]:
    """
    收集单个人物的人设档案并入库（全局人设库，project_id 仅作可选溯源）。

    Returns:
        入库后的最新 person 文档（含 person_id）。
    """
    from langchain_core.messages import HumanMessage

    from core.observability import obs_log, observation_context

    from api.dao import persons as persons_dao
    from api.services.info_collection.url_tools import _build_worker_chrome_config
    from Sere1nGraph.graph.agents.factory import create_persona_collect_agent
    from Sere1nGraph.graph.agents.runtime import extract_with_retry
    from Sere1nGraph.graph.prompts.loader import load_prompt
    from Sere1nGraph.graph.skills.schemas import PersonaProfile

    name = (name or "").strip()
    if not name:
        raise ValueError("人物姓名不能为空")

    obs_log(
        "人设收集开始", task_id=task_id, project_id=project_id,
        source="persona_collect", level="notice", event="collect_start",
        data={"name": name, "company": company},
    )

    # 获取浏览器 CDP 端点
    from browser_manager.provider import get_browser_provider

    provider = get_browser_provider()
    cdp_task_id = f"persona_collect_{task_id or project_id}_{abs(hash(name + company)) % 100000}"
    cdp_url = await provider.get_cdp_endpoint(task_id=cdp_task_id, purpose="persona_collect")
    if not cdp_url:
        raise RuntimeError("无法获取 Chrome 容器用于人设收集")

    from api.services.notifications import notify_event_background

    prompt = load_prompt("persona_collect/persona_collect")
    clue = _build_clue(name, company, position, extra)
    try:
        with observation_context(
            project_id=project_id,
            task_id=task_id,
            phase="persona_collect",
            agent="persona_collect",
            task_type="persona_collect",
        ):
            worker_config = _build_worker_chrome_config(app_config, cdp_url)
            agent = await create_persona_collect_agent(worker_config)
            raw = await agent(
                {"messages": [HumanMessage(content=f"请收集以下人物的真实信息并输出人物档案：\n{clue}")]}
            )
            parsed = await extract_with_retry(raw, worker_config, system_prompt=prompt) or {}
    except Exception as exc:  # noqa: BLE001
        obs_log(
            f"人设收集失败: {exc}", task_id=task_id, project_id=project_id,
            source="persona_collect", level="error", event="collect_error",
            data={"name": name, "company": company, "error": str(exc)},
        )
        notify_event_background(
            event="persona_collect_failed",
            title="人设采集失败",
            content=f"人物「{name}」采集失败：{exc}",
            level="error",
            source="persona_collect",
            project_id=project_id or None,
            task_id=task_id or None,
            context={"name": name, "company": company},
        )
        logger.warning(f"[persona_collect] task={task_id} 采集失败 name='{name}': {exc}")
        raise
    finally:
        try:
            await provider.release_cdp_endpoint(cdp_task_id)
        except Exception:
            pass

    # 结构化校验：用 PersonaProfile 归一字段，过滤幻觉/非法字段
    if not parsed.get("name"):
        parsed["name"] = name
    try:
        profile = PersonaProfile(**parsed).model_dump()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[persona_collect] 结构化校验失败，回退最小档案: {exc}")
        profile = PersonaProfile(name=name, company=company, position=position).model_dump()

    # 关联公司元信息（若有根域名，人设库全局化：仅按 root_domain 查，不限定项目）
    root_domain = str(profile.get("company_root_domain") or "").strip()
    if root_domain:
        try:
            existing_meta = await db["company_meta"].find_one(
                {"root_domain": root_domain},
                {"_id": 0, "meta_id": 1},
            )
            if existing_meta and existing_meta.get("meta_id"):
                profile["company_meta_id"] = existing_meta["meta_id"]
        except Exception:
            pass

    doc = await persons_dao.upsert_person(
        db,
        profile=profile,
        project_id=project_id,
        source=source,
        task_id=task_id,
    )
    logger.info(
        f"[persona_collect] task={task_id} 完成 ✓ name='{profile.get('name')}' "
        f"company='{profile.get('company')}' confidence={profile.get('confidence')}"
    )
    obs_log(
        "人设收集完成", task_id=task_id, project_id=project_id,
        source="persona_collect", level="notice", event="collect_done",
        data={
            "person_id": doc.get("person_id"),
            "name": profile.get("name"),
            "company": profile.get("company"),
        },
    )
    return doc
