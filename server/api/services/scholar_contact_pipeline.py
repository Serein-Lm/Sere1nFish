"""
学者学术联系发现 — 采集流水线 Service

流程：单位名+方向 → scholar_tools.discover(多源聚合) → normalize_to_docs(去噪/去重/姓名绑定)
     → (可选) chrome 池打开 PMC 全文补抽通讯邮箱 → 按稳定 id 增量入库 → 观测/通知。

设计原则(契合 AGENTS.md)：
- 数据源差异收敛在 crawler_tools.scholar_tools 适配层；
- 本 service 只表达领域动作(收集/入库/通知)，不感知具体 HTTP；
- 同步网络调用用 to_thread 卸载，避免阻塞事件循环；
- chrome 走项目统一 provider 资源池，用完归还，失败降级为 API 结果。

合规边界：仅按文章绑定的公开学术通讯邮箱；不导出整单位联系方式名单；
         不取个人电话；知网等反爬源不做绕过。
"""
from __future__ import annotations

import asyncio
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.logger import get_logger

logger = get_logger("scholar_contact")


async def _chrome_pmc_enrich(
    *, task_id: str, pmcids: list[str], articles_by_pmcid: dict[str, str],
) -> list[dict[str, Any]]:
    """用项目 chrome 池打开 PMC 全文，补抽通讯邮箱。返回 contact dict 列表。"""
    import re

    from browser_manager.provider import get_browser_provider

    email_re = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
    provider = get_browser_provider()
    cdp_task_id = f"scholar_{task_id}"
    contacts: list[dict[str, Any]] = []

    cdp_url = await provider.get_cdp_endpoint(task_id=cdp_task_id, purpose="scholar_contact")
    if not cdp_url:
        logger.warning(f"[scholar_contact] task={task_id} 无可用 chrome，跳过 PMC 增强")
        return contacts
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(cdp_url)
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await ctx.new_page()
            for pmcid in pmcids:
                url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(4000)
                    html = await page.content()
                    emails = {
                        e for e in email_re.findall(html)
                        if not e.lower().endswith(
                            (".png", ".jpg", ".gif", ".svg", ".css", ".js"))
                    }
                    aid = articles_by_pmcid.get(pmcid, pmcid)
                    for em in sorted(emails):
                        contacts.append({
                            "email": em, "article_id": aid,
                            "source_key": "pmc_web", "author_name": None,
                            "is_corresponding": False,
                        })
                except Exception as e:  # noqa: BLE001
                    logger.info(f"[scholar_contact] PMC {pmcid} 抽取失败: {e}")
            await page.close()
    finally:
        await provider.release_cdp_endpoint(task_id=cdp_task_id)
    return contacts


async def run_scholar_contact_collect(
    db: AsyncIOMotorDatabase,
    app_config: Any,
    *,
    task_id: str,
    project_id: str,
    unit: str,
    direction: str,
    unit_en: str = "",
    limit: int = 10,
    enable_chrome_pmc: bool = False,
    dry_run: bool = False,
    bulk: bool = False,
    max_articles: int = 2000,
    notify_completion: bool = True,
) -> dict[str, Any]:
    """
    按单位+方向收集学者学术联系，结构化入库。

    Returns:
        采集摘要（matched_institution / articles_inserted / contacts_inserted /
        corresponding_count / status / error），dry_run 时不入库。
    """
    from core.observability import obs_log

    from api.dao import scholar_contact as scholar_dao
    from api.services.notifications import notify_event_background
    from crawler_tools import scholar_tools

    summary: dict[str, Any] = {
        "task_id": task_id, "project_id": project_id,
        "unit": unit, "direction": direction, "unit_en": unit_en or unit,
        "matched_institution": "", "articles_total": 0, "contacts_total": 0,
        "institution_verified": False,
        "verified_articles_total": 0,
        "unverified_articles_total": 0,
        "articles_inserted": 0, "articles_updated": 0,
        "contacts_inserted": 0, "contacts_updated": 0,
        "corresponding_count": 0, "dry_run": dry_run,
        "status": "running", "error": None,
    }
    obs_log(
        "学者联系采集开始", task_id=task_id, project_id=project_id,
        source="scholar_contact", level="notice", event="pipeline_start",
        data={"unit": unit, "direction": direction, "dry_run": dry_run},
    )

    try:
        if bulk:
            if not unit:
                raise ValueError("bulk 模式下 unit 为必填")
        elif not unit or not direction:
            raise ValueError("unit 与 direction 均为必填")

        if bulk:
            # 机构级全量：流式分页，逐批增量入库（前端可实时看到数据增长）
            ue = unit_en or unit
            try:
                inst_cands = await asyncio.to_thread(scholar_tools._resolve_institution, unit)
                if inst_cands:
                    summary["matched_institution"] = inst_cands[0].get("name", "")
            except Exception:  # noqa: BLE001
                pass

            pages = scholar_tools.europepmc_bulk_pages(ue, max_articles=max_articles, page_size=100)
            it = iter(pages)
            hit_total = None
            while True:
                page = await asyncio.to_thread(lambda: next(it, None))
                if page is None:
                    break
                hit_total = page.get("hit_count")
                art_docs, con_docs = scholar_tools.normalize_bulk_batch(unit, page["articles"])
                summary["articles_total"] += len(art_docs)
                verified_in_batch = sum(
                    1 for article in art_docs if article.get("unit_verified")
                )
                summary["verified_articles_total"] += verified_in_batch
                summary["unverified_articles_total"] += (
                    len(art_docs) - verified_in_batch
                )
                summary["contacts_total"] += len(con_docs)
                summary["corresponding_count"] += sum(
                    1 for c in con_docs if c.get("is_corresponding"))
                if not dry_run:
                    if art_docs:
                        ar = await scholar_dao.upsert_articles_batch(
                            db, project_id=project_id, unit=unit, direction="",
                            articles=art_docs, task_id=task_id)
                        summary["articles_inserted"] += ar["inserted"]
                        summary["articles_updated"] += ar["updated"]
                    if con_docs:
                        cr = await scholar_dao.upsert_contacts_batch(
                            db, project_id=project_id, unit=unit, direction="",
                            contacts=con_docs, task_id=task_id)
                        summary["contacts_inserted"] += cr["inserted"]
                        summary["contacts_updated"] += cr["updated"]
                await db["tasks"].update_one(
                    {"task_id": task_id},
                    {"$set": {"progress": {
                        "fetched": page["fetched"], "total": hit_total,
                        "articles": summary["articles_total"],
                        "contacts": summary["contacts_total"],
                    }}},
                )
                obs_log(
                    f"机构级抓取 {page['fetched']}/{hit_total or '?'} "
                    f"(累计文章 {summary['articles_total']} 联系 {summary['contacts_total']})",
                    task_id=task_id, project_id=project_id, source="scholar_contact",
                    level="info", event="bulk_progress",
                    data={"fetched": page["fetched"], "total": hit_total},
                )
        else:
            # 单位+方向：多源聚合（同步网络调用卸载到线程池）
            discover_out = await asyncio.to_thread(
                scholar_tools.discover, unit, direction, unit_en, limit,
            )
            api = discover_out.get("api_results", {}) or {}
            inst = api.get("unit") or {}
            summary["matched_institution"] = inst.get("name", "")
            summary["institution_verified"] = bool(
                api.get("institution_verified", False)
            )

            # 归一化
            sources, articles, contacts = scholar_tools.normalize_to_docs(discover_out)
            docs = scholar_tools.docs_as_dicts(sources, articles, contacts)
            article_docs = docs["articles"]
            contact_docs = docs["contacts"]

            # 可选 chrome PMC 增强
            if enable_chrome_pmc:
                ep = (discover_out.get("email_extraction") or {}).get("sources", {}).get("europepmc", {})
                articles_by_pmcid: dict[str, str] = {}
                pmcids: list[str] = []
                for a in ep.get("articles", []):
                    pmcid = a.get("pmcid")
                    if pmcid:
                        pmcids.append(pmcid)
                        articles_by_pmcid[pmcid] = a.get("doi") or pmcid
                if pmcids:
                    obs_log(
                        f"chrome 打开 {len(pmcids)} 篇 PMC 全文补抽", task_id=task_id,
                        project_id=project_id, source="scholar_contact",
                        level="info", event="chrome_enrich",
                    )
                    extra = await _chrome_pmc_enrich(
                        task_id=task_id, pmcids=pmcids, articles_by_pmcid=articles_by_pmcid,
                    )
                    for c in extra:
                        em = scholar_tools.normalize_email(c["email"])
                        if em and not scholar_tools.is_noise_email(em):
                            c["email"] = em
                            c["unit"] = unit
                            contact_docs.append(c)

            summary["articles_total"] = len(article_docs)
            summary["verified_articles_total"] = sum(
                1 for article in article_docs if article.get("unit_verified")
            )
            summary["unverified_articles_total"] = (
                summary["articles_total"] - summary["verified_articles_total"]
            )
            summary["contacts_total"] = len(contact_docs)
            summary["corresponding_count"] = sum(
                1 for c in contact_docs if c.get("is_corresponding"))

            # 入库（dry_run 跳过）
            if not dry_run:
                art_res = await scholar_dao.upsert_articles_batch(
                    db, project_id=project_id, unit=unit, direction=direction,
                    articles=article_docs, task_id=task_id,
                )
                con_res = await scholar_dao.upsert_contacts_batch(
                    db, project_id=project_id, unit=unit, direction=direction,
                    contacts=contact_docs, task_id=task_id,
                )
                summary["articles_inserted"] = art_res["inserted"]
                summary["articles_updated"] = art_res["updated"]
                summary["contacts_inserted"] = con_res["inserted"]
                summary["contacts_updated"] = con_res["updated"]

        summary["status"] = "completed"
        obs_log(
            "学者联系采集完成", task_id=task_id, project_id=project_id,
            source="scholar_contact", level="notice", event="pipeline_done",
            data={
                "matched_institution": summary["matched_institution"],
                "articles_total": summary["articles_total"],
                "verified_articles_total": summary["verified_articles_total"],
                "contacts_total": summary["contacts_total"],
                "corresponding_count": summary["corresponding_count"],
                "dry_run": dry_run,
            },
        )
        logger.info(
            f"[scholar_contact] task={task_id} 完成 ✓ unit={unit} "
            f"articles={summary['articles_total']} contacts={summary['contacts_total']} "
            f"corr={summary['corresponding_count']} dry_run={dry_run}"
        )
        if (
            not dry_run
            and notify_completion
            and summary["verified_articles_total"] > 0
        ):
            notify_event_background(
                event="scholar_contact_done", level="info",
                title="发现目标单位已验证学者文章",
                content=(f"单位『{unit}』方向『{direction}』: "
                         f"已验证文章 {summary['verified_articles_total']} 篇"),
                source="scholar_contact", project_id=project_id, task_id=task_id,
                context={"unit": unit, "direction": direction},
            )
    except Exception as e:  # noqa: BLE001
        summary["status"] = "error"
        summary["error"] = str(e)
        logger.error(f"[scholar_contact] task={task_id} 失败: {e}")
        obs_log(
            f"学者联系采集失败: {e}", task_id=task_id, project_id=project_id,
            source="scholar_contact", level="error", event="pipeline_error",
            data={"error": str(e)},
        )
        raise

    return summary
