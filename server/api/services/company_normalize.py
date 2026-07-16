"""
公司名规范化 Service

编排 AI 浏览器搜索（cn.bing.com）+ 天眼查 ICP 交叉验证，
输出规范化公司全称与官网根域名，并落库到 company_meta。

设计：复用 create_company_normalize_agent + chrome-devtools MCP，不另起浏览器；
AI 输出用 CompanyNormalization schema 约束（extract_with_retry 解析）。
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.logger import get_logger

logger = get_logger("company_normalize")


def normalize_root_domain(raw: str) -> str:
    """去掉协议、路径、www 前缀，只保留主域名。"""
    if not raw:
        return ""
    value = str(raw).strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("/")[0].split("?")[0].strip()
    if value.startswith("www."):
        value = value[4:]
    return value


def _extract_domain_from_icp_text(text: str) -> str:
    """从 tianyancha_get_domain 返回文本中提取域名。"""
    if not text:
        return ""
    # 形如：公司"X"的官网域名为：example.com
    m = re.search(r"域名为[：:]\s*([A-Za-z0-9.\-]+)", text)
    if m:
        return normalize_root_domain(m.group(1))
    return ""


async def _icp_lookup_domain(company_name: str) -> str:
    """调用天眼查 ICP 工具做交叉验证（在线程中执行，避免阻塞事件循环）。"""
    try:
        from Sere1nGraph.graph.tools.builtin import tianyancha_get_domain

        def _call() -> str:
            # langchain @tool：单参数用 .invoke 传入
            return tianyancha_get_domain.invoke(company_name)

        text = await asyncio.to_thread(_call)
        return _extract_domain_from_icp_text(str(text or ""))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"ICP 交叉验证失败: {exc}")
        return ""


async def normalize_company(
    db: AsyncIOMotorDatabase,
    app_config: Any,
    *,
    project_id: str,
    input_name: str,
    task_id: str = "",
) -> dict[str, Any]:
    """
    规范化单个公司名。

    流程：AI 浏览器搜索 + ICP 交叉验证 → 结构化输出 → 落库 company_meta。

    Returns:
        company_meta 文档（含 normalized_name/root_domain/aliases/confidence/source）。
    """
    from langchain_core.messages import HumanMessage

    from api.dao import company_meta as company_meta_dao
    from api.services.info_collection.url_tools import _build_worker_chrome_config
    from Sere1nGraph.graph.agents.factory import create_company_normalize_agent
    from Sere1nGraph.graph.agents.runtime import extract_with_retry
    from Sere1nGraph.graph.prompts.loader import load_prompt

    input_name = (input_name or "").strip()
    if not input_name:
        raise ValueError("公司名不能为空")

    # 1. 增量复用：已缓存则直接返回
    cached = await company_meta_dao.get_company_meta(db, project_id, input_name)
    if cached and cached.get("normalized_name"):
        if not cached.get("target_id"):
            from api.services.targets import attach_normalized_company

            target = await attach_normalized_company(
                db,
                project_id=project_id,
                input_name=input_name,
                normalized_name=str(cached.get("normalized_name") or input_name),
                root_domain=str(cached.get("root_domain") or ""),
                aliases=list(cached.get("aliases") or []),
                task_id=task_id,
            )
            cached = await company_meta_dao.upsert_company_meta(
                db,
                project_id=project_id,
                input_name=input_name,
                normalized_name=str(cached.get("normalized_name") or input_name),
                root_domain=str(cached.get("root_domain") or ""),
                aliases=list(cached.get("aliases") or []),
                confidence=cached.get("confidence"),
                source=str(cached.get("source") or "cached"),
                task_id=task_id,
                target_id=str(target.get("target_id") or ""),
            )
        logger.info(f"[normalize] 命中缓存: {input_name} → {cached.get('normalized_name')}")
        return cached

    # 2. 获取浏览器 CDP 端点
    from browser_manager.provider import get_browser_provider

    provider = get_browser_provider()
    cdp_task_id = f"company_normalize_{task_id or project_id}_{abs(hash(input_name)) % 100000}"
    cdp_url = await provider.get_cdp_endpoint(task_id=cdp_task_id, purpose="company_normalize")
    if not cdp_url:
        raise RuntimeError("无法获取 Chrome 容器用于公司名规范化")

    prompt = load_prompt("company_normalize/company_normalize")
    try:
        worker_config = _build_worker_chrome_config(app_config, cdp_url)
        agent = await create_company_normalize_agent(worker_config)
        raw = await agent(
            {"messages": [HumanMessage(content=f"请规范化以下公司名并给出官网根域名：{input_name}")]}
        )
        parsed = await extract_with_retry(raw, worker_config, system_prompt=prompt) or {}
    finally:
        try:
            await provider.release_cdp_endpoint(cdp_task_id)
        except Exception:
            pass

    normalized_name = str(parsed.get("normalized_name") or input_name).strip()
    ai_domain = normalize_root_domain(str(parsed.get("root_domain") or ""))
    aliases = parsed.get("aliases") or []
    confidence = parsed.get("confidence")
    source = str(parsed.get("source") or "bing_search")

    # 3. ICP 交叉验证：不一致以 ICP 为准
    icp_domain = await _icp_lookup_domain(normalized_name)
    root_domain = ai_domain
    if icp_domain:
        if ai_domain and icp_domain == ai_domain:
            source = "cross_validated"
        else:
            root_domain = icp_domain
            source = "tianyancha_icp"

    # 4. 落库
    from api.services.targets import attach_normalized_company

    target = await attach_normalized_company(
        db,
        project_id=project_id,
        input_name=input_name,
        normalized_name=normalized_name,
        root_domain=root_domain,
        aliases=[str(a) for a in aliases if a],
        task_id=task_id,
    )
    doc = await company_meta_dao.upsert_company_meta(
        db,
        project_id=project_id,
        input_name=input_name,
        normalized_name=normalized_name,
        root_domain=root_domain,
        aliases=[str(a) for a in aliases if a],
        confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
        source=source,
        task_id=task_id,
        target_id=str(target.get("target_id") or ""),
    )
    logger.info(
        f"[normalize] {input_name} → name='{normalized_name}' domain='{root_domain}' "
        f"source={source}"
    )
    return doc
