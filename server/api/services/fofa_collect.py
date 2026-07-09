"""
FOFA 资产采集编排 Service

流程：公司名规范化 → FOFA(domain+cert 两路检索) → 合并去重 → 增量入库
     → 抽取存活站点 URL → 复用 UrlScanPipeline 探活+去重+Chrome Agent 深扫。

设计原则：
- FOFA 负责高效批量资产发现，替代慢速浏览器全量爬取；
- 浏览器 Agent 仅用于对 FOFA 发现的资产做存活站点深度扫描；
- 资产按稳定 asset_id upsert 增量入库，同公司再次采集只处理新增/变更资产。
"""
from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.logger import get_logger

logger = get_logger("fofa_collect")


def _asset_to_url(asset: dict[str, Any]) -> str:
    """从 FOFA 资产提取可探活的 URL：优先 link，其次按 host/protocol 拼接。"""
    link = str(asset.get("link") or "").strip()
    if link:
        return link
    host = str(asset.get("host") or "").strip()
    if not host:
        return ""
    if host.startswith(("http://", "https://")):
        return host
    protocol = str(asset.get("protocol") or "").strip().lower()
    scheme = "https" if protocol in ("https", "ssl") else "http"
    return f"{scheme}://{host}"


async def run_fofa_collect(
    db: AsyncIOMotorDatabase,
    app_config: Any,
    *,
    task_id: str,
    project_id: str,
    company_name: str,
    fofa_size: int = 200,
    enable_scan: bool = True,
    min_attention_score: int = 40,
) -> dict[str, Any]:
    """
    公司名 → 规范化 → FOFA 采集 → 增量入库 → URL 扫描。

    Returns:
        采集结果摘要（含 normalized_name/root_domain/资产计数/扫描结果）。
    """
    from core.observability import obs_log

    from api.dao import fofa_assets as fofa_dao
    from api.services.company_normalize import normalize_company
    from crawler_tools import fofa_tools

    summary: dict[str, Any] = {
        "task_id": task_id,
        "project_id": project_id,
        "input_name": company_name,
        "normalized_name": "",
        "root_domain": "",
        "fofa_total": 0,
        "assets_inserted": 0,
        "assets_updated": 0,
        "new_assets": 0,
        "scanned": False,
        "status": "running",
        "error": None,
    }
    obs_log(
        "FOFA 采集流水线开始", task_id=task_id, project_id=project_id,
        source="fofa_collect", level="notice", event="pipeline_start",
        data={"company_name": company_name},
    )

    try:
        # 1. 公司名规范化
        meta = await normalize_company(
            db, app_config, project_id=project_id, input_name=company_name, task_id=task_id,
        )
        normalized_name = meta.get("normalized_name") or company_name
        root_domain = meta.get("root_domain") or ""
        summary["normalized_name"] = normalized_name
        summary["root_domain"] = root_domain

        if not root_domain:
            summary["status"] = "completed"
            summary["error"] = "未能确定根域名，跳过 FOFA 采集"
            logger.warning(f"[fofa_collect] task={task_id} 无根域名，跳过")
            obs_log(
                "FOFA 采集跳过（无根域名）", task_id=task_id, project_id=project_id,
                source="fofa_collect", level="warning", event="pipeline_skip",
            )
            return summary

        # 2. FOFA 两路检索（domain + cert）
        assets: list[Any] = []
        for search_type in ("domain", "cert"):
            found = await fofa_tools.search_fofa(
                query=root_domain, search_type=search_type, size=fofa_size,
            )
            logger.info(f"[fofa_collect] task={task_id} {search_type} 检索 {len(found)} 条")
            assets.extend(found)

        # 3. 合并去重（按 host/ip/port）
        seen: set[tuple[str, str, str]] = set()
        merged: list[dict[str, Any]] = []
        for a in assets:
            d = a.as_dict() if hasattr(a, "as_dict") else dict(a)
            key = (d.get("host", ""), d.get("ip", ""), d.get("port", ""))
            if key in seen or not (d.get("host") or d.get("ip")):
                continue
            seen.add(key)
            merged.append(d)
        summary["fofa_total"] = len(merged)

        # 4. 增量入库（记录入库前已有 asset_id，识别新增资产）
        pre_ids = await fofa_dao.list_asset_ids(db, project_id)
        upsert_result = await fofa_dao.upsert_assets_batch(
            db,
            project_id=project_id,
            root_domain=root_domain,
            source_query=root_domain,
            search_type="domain+cert",
            assets=merged,
            task_id=task_id,
        )
        summary["assets_inserted"] = upsert_result.get("inserted", 0)
        summary["assets_updated"] = upsert_result.get("updated", 0)

        # 5. 抽取新增资产 URL，复用 UrlScanPipeline 深扫（增量：只扫新增/变更）
        new_assets = [
            d for d in merged
            if fofa_dao.fofa_asset_id(
                project_id, d.get("host", ""), d.get("ip", ""), d.get("port", "")
            ) not in pre_ids
        ]
        summary["new_assets"] = len(new_assets)

        if enable_scan and new_assets:
            urls = [u for u in (_asset_to_url(d) for d in new_assets) if u]
            url_content = "\n".join(dict.fromkeys(urls))  # 去重保序
            if url_content.strip():
                from api.services.url_scan_pipeline import UrlScanPipeline

                logger.info(
                    f"[fofa_collect] task={task_id} 新增资产 {len(new_assets)} 个，"
                    f"提交 {len(urls)} 个 URL 深扫"
                )
                pipeline = UrlScanPipeline(db, app_config)
                await pipeline.run_pipeline(
                    task_id=task_id, project_id=project_id, url_content=url_content,
                    min_attention_score=min_attention_score,
                )
                summary["scanned"] = True

        summary["status"] = "completed"
        obs_log(
            "FOFA 采集流水线完成", task_id=task_id, project_id=project_id,
            source="fofa_collect", level="notice", event="pipeline_done",
            data={
                "normalized_name": normalized_name, "root_domain": root_domain,
                "fofa_total": summary["fofa_total"], "new_assets": summary["new_assets"],
                "inserted": summary["assets_inserted"], "updated": summary["assets_updated"],
            },
        )
        logger.info(
            f"[fofa_collect] task={task_id} 完成 ✓ domain={root_domain} "
            f"total={summary['fofa_total']} inserted={summary['assets_inserted']} "
            f"updated={summary['assets_updated']} new={summary['new_assets']}"
        )
    except Exception as e:  # noqa: BLE001
        summary["status"] = "error"
        summary["error"] = str(e)
        logger.error(f"[fofa_collect] task={task_id} 失败: {e}")
        obs_log(
            f"FOFA 采集流水线失败: {e}", task_id=task_id, project_id=project_id,
            source="fofa_collect", level="error", event="pipeline_error",
            data={"error": str(e)},
        )
        raise

    return summary
