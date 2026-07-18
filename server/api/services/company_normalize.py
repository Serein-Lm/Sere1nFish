"""
公司名规范化 Service

编排 AI 浏览器搜索（cn.bing.com）+ 天眼查 ICP 交叉验证，
输出规范化公司全称与官网根域名，并落库到 company_meta。

设计：复用 create_company_normalize_agent + chrome-devtools MCP，不另起浏览器；
AI 输出用 CompanyNormalization schema 约束（extract_with_retry 解析）。
"""
from __future__ import annotations

import re
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.logger import get_logger

logger = get_logger("company_normalize")
NORMALIZATION_VERSION = 2


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


def _company_name_candidates(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    values = [text]
    values.extend(re.findall(r"[【\[]([^】\]]+)[】\]]", text))
    without_annotations = re.sub(r"[【\[].*?[】\]]", "", text).strip()
    if without_annotations:
        values.append(without_annotations)
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


async def _icp_lookup_records(company_names: list[str]) -> list[Any]:
    """Read and deduplicate all ICP candidates for plausible legal names."""
    try:
        from crawler_tools.tianyancha_tools import TianyanchaClient

        client = await TianyanchaClient.from_runtime_config()
        records: list[Any] = []
        seen: set[str] = set()
        for company_name in company_names:
            for record in await client.get_icp_records(company_name):
                domain = normalize_root_domain(record.domain)
                if not domain or domain in seen:
                    continue
                seen.add(domain)
                records.append(record)
        return records
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"ICP 交叉验证失败: {exc}")
        return []


def _icp_domains(records: list[Any]) -> list[str]:
    values: list[str] = []
    for record in records:
        values.append(normalize_root_domain(getattr(record, "domain", "")))
        values.extend(
            normalize_root_domain(value)
            for value in (getattr(record, "websites", None) or [])
        )
    return list(dict.fromkeys(value for value in values if value))[:6]


def _icp_record_rank(record: Any) -> tuple[int, int, str]:
    domain = normalize_root_domain(getattr(record, "domain", ""))
    license_no = str(getattr(record, "license_no", "") or "")
    suffix_match = re.search(r"-(\d+)$", license_no)
    suffix = int(suffix_match.group(1)) if suffix_match else 999
    websites = {
        normalize_root_domain(value)
        for value in (getattr(record, "websites", None) or [])
    }
    return (0 if domain in websites else 1, suffix, domain)


def _select_primary_domain(
    *,
    ai_domain: str,
    icp_records: list[Any],
) -> tuple[str, str]:
    """Choose a primary domain without treating the first ICP row as a homepage."""
    normalized_ai = normalize_root_domain(ai_domain)
    candidates = _icp_domains(icp_records)
    if normalized_ai:
        return (
            normalized_ai,
            "cross_validated" if normalized_ai in candidates else "bing_search",
        )
    if not icp_records:
        return "", "fallback"
    selected = min(icp_records, key=_icp_record_rank)
    return normalize_root_domain(getattr(selected, "domain", "")), "tianyancha_icp"


def _clean_aliases(values: list[Any], *, limit: int = 20) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if not text or len(text) > 64 or key in seen:
            continue
        seen.add(key)
        aliases.append(text)
        if len(aliases) >= limit:
            break
    return aliases


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
    from core.observability import observation_context
    from Sere1nGraph.graph.agents.factory import create_company_normalize_agent
    from Sere1nGraph.graph.agents.runtime import extract_with_retry
    from Sere1nGraph.graph.prompts.loader import load_prompt

    input_name = (input_name or "").strip()
    if not input_name:
        raise ValueError("公司名不能为空")

    # 1. 增量复用：已缓存则直接返回
    cached = await company_meta_dao.get_company_meta(db, project_id, input_name)
    # 降级结果不能成为永久缓存，否则一次浏览器故障会让后续资产发现永远缺失根域名。
    cached_provenance = dict((cached or {}).get("provenance") or {})
    if (
        cached
        and cached.get("normalized_name")
        and str(cached.get("source") or "") not in {"fallback", "company_scan"}
        and int(cached_provenance.get("normalization_version") or 0)
        >= NORMALIZATION_VERSION
    ):
        if not cached.get("target_id"):
            from api.services.targets import attach_normalized_company

            target = await attach_normalized_company(
                db,
                project_id=project_id,
                input_name=input_name,
                normalized_name=str(cached.get("normalized_name") or input_name),
                root_domain=str(cached.get("root_domain") or ""),
                root_domains=list(cached.get("icp_domains") or []),
                aliases=list(cached.get("aliases") or []),
                task_id=task_id,
                normalization_version=NORMALIZATION_VERSION,
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
                icp_domains=list(cached.get("icp_domains") or []),
                provenance=cached_provenance,
            )
        logger.info(f"[normalize] 命中缓存: {input_name} → {cached.get('normalized_name')}")
        return cached

    # 2. 跨项目复用全局 Target 别名。品牌名（如“B站”）已聚类过时，不再重复启动浏览器。
    from api.dao import targets as targets_dao

    existing_target = await targets_dao.find_target(db, name=input_name)
    if (
        existing_target
        and existing_target.get("canonical_name")
        and existing_target.get("root_domain")
        and int(existing_target.get("normalization_version") or 0)
        >= NORMALIZATION_VERSION
    ):
        normalized_name = str(existing_target.get("canonical_name") or input_name)
        root_domain = str(existing_target.get("root_domain") or "")
        aliases = list(
            dict.fromkeys(
                [
                    input_name,
                    *[
                        str(item).strip()
                        for item in existing_target.get("aliases") or []
                        if str(item).strip()
                    ],
                ]
            )
        )
        await targets_dao.link_project_target(
            db,
            project_id=project_id,
            target=existing_target,
            search_terms=aliases,
            task_def_id=task_id,
        )
        doc = await company_meta_dao.upsert_company_meta(
            db,
            project_id=project_id,
            input_name=input_name,
            normalized_name=normalized_name,
            root_domain=root_domain,
            aliases=aliases,
            confidence=None,
            source="target_cache",
            task_id=task_id,
            target_id=str(existing_target.get("target_id") or ""),
            icp_domains=list(existing_target.get("root_domains") or []),
            provenance={"normalization_version": NORMALIZATION_VERSION},
        )
        logger.info(f"[normalize] 命中全局 Target: {input_name} → {normalized_name}")
        return doc

    # 3. 获取浏览器 CDP 端点
    from browser_manager.provider import get_browser_provider

    provider = get_browser_provider()
    cdp_task_id = f"company_normalize_{task_id or project_id}_{abs(hash(input_name)) % 100000}"
    cdp_url = ""
    parsed: dict[str, Any] = {}
    browser_error = ""
    prompt = load_prompt("company_normalize/company_normalize")
    try:
        cdp_url = await provider.get_cdp_endpoint(
            task_id=cdp_task_id,
            purpose="company_normalize",
        )
        if not cdp_url:
            raise RuntimeError("无法获取 Chrome 容器用于公司名规范化")
        worker_config = _build_worker_chrome_config(app_config, cdp_url)
        agent = await create_company_normalize_agent(worker_config)
        with observation_context(
            project_id=project_id,
            task_id=task_id,
            phase="company_normalize",
            agent="company_normalize",
        ):
            raw = await agent(
                {"messages": [HumanMessage(content=f"请规范化以下公司名并给出官网根域名：{input_name}")]}
            )
            parsed = await extract_with_retry(raw, worker_config, system_prompt=prompt) or {}
    except Exception as exc:  # noqa: BLE001
        browser_error = str(exc) or type(exc).__name__
        logger.warning("公司浏览器规范化失败，降级 ICP: %s", browser_error)
    finally:
        if cdp_url:
            try:
                await provider.release_cdp_endpoint(cdp_task_id)
            except Exception:
                pass

    normalized_name = str(parsed.get("normalized_name") or input_name).strip()
    ai_domain = normalize_root_domain(str(parsed.get("root_domain") or ""))
    parsed_aliases = parsed.get("aliases") or []
    if not isinstance(parsed_aliases, list):
        parsed_aliases = [parsed_aliases]
    aliases = _clean_aliases(
        [
            *_company_name_candidates(input_name),
            normalized_name,
            *parsed_aliases,
        ]
    )
    confidence = parsed.get("confidence")
    source = str(parsed.get("source") or "bing_search")

    # 4. ICP supplies ownership candidates, while browser evidence selects the
    # primary homepage. All trusted candidates continue into asset discovery.
    icp_records = await _icp_lookup_records(
        _clean_aliases(
            [
                normalized_name,
                *_company_name_candidates(input_name),
            ],
            limit=6,
        )
    )
    root_domain, resolved_source = _select_primary_domain(
        ai_domain=ai_domain,
        icp_records=icp_records,
    )
    source = resolved_source or source
    icp_domains = _icp_domains(icp_records)
    root_domains = list(
        dict.fromkeys(value for value in [root_domain, *icp_domains] if value)
    )[:6]
    if not isinstance(confidence, (int, float)):
        confidence = 0.5 if root_domain else 0.0
    elif resolved_source == "bing_search" and icp_domains:
        # A search-only domain that conflicts with ICP evidence is retained
        # for discovery, but must not look as trustworthy as a cross-validated
        # homepage in downstream routing and review.
        confidence = min(float(confidence), 0.55)
    provenance = {
        "normalization_version": NORMALIZATION_VERSION,
        "browser_error": browser_error or None,
        "ai_domain": ai_domain,
        "icp_domains": icp_domains,
    }

    # 5. 落库
    from api.services.targets import attach_normalized_company

    target = await attach_normalized_company(
        db,
        project_id=project_id,
        input_name=input_name,
        normalized_name=normalized_name,
        root_domain=root_domain,
        root_domains=root_domains,
        aliases=aliases,
        task_id=task_id,
        normalization_version=NORMALIZATION_VERSION,
    )
    doc = await company_meta_dao.upsert_company_meta(
        db,
        project_id=project_id,
        input_name=input_name,
        normalized_name=normalized_name,
        root_domain=root_domain,
        aliases=aliases,
        confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
        source=source,
        task_id=task_id,
        target_id=str(target.get("target_id") or ""),
        icp_domains=root_domains,
        provenance=provenance,
    )
    logger.info(
        f"[normalize] {input_name} → name='{normalized_name}' domain='{root_domain}' "
        f"source={source}"
    )
    return doc
