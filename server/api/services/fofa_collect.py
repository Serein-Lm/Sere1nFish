"""兼容任务入口：公司规范化后统一执行 FOFA + Hunter 资产发现。"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.services.asset_intelligence import AssetIdentity, AssetIntelligenceService
from core.logger import get_logger
from core.observability import obs_log

logger = get_logger("asset_collect")


async def run_fofa_collect(
    db: AsyncIOMotorDatabase,
    app_config: Any,
    *,
    task_id: str,
    project_id: str,
    company_name: str,
    fofa_size: int = 200,
    hunter_size: int = 200,
    enable_scan: bool = True,
    min_attention_score: int = 40,
    probe_concurrency: int = 48,
) -> dict[str, Any]:
    """保留旧 task_type/API，内部改走统一多 Provider 资产情报服务。"""
    from api.dao import targets as targets_dao
    from api.services.company_normalize import normalize_company

    summary: dict[str, Any] = {
        "task_id": task_id,
        "project_id": project_id,
        "input_name": company_name,
        "normalized_name": "",
        "root_domain": "",
        "target_id": "",
        "fofa_total": 0,
        "hunter_total": 0,
        "asset_total": 0,
        "assets_inserted": 0,
        "assets_updated": 0,
        "assets_unchanged": 0,
        "alive_assets": 0,
        "new_assets": 0,
        "scanned": False,
        "providers": {},
        "status": "running",
        "error": None,
    }
    obs_log(
        "多源资产采集开始",
        task_id=task_id,
        project_id=project_id,
        source="asset_collect",
        level="notice",
        event="pipeline_start",
        data={"company_name": company_name},
    )
    try:
        meta = await normalize_company(
            db,
            app_config,
            project_id=project_id,
            input_name=company_name,
            task_id=task_id,
        )
        identity = AssetIdentity(
            input_name=company_name,
            normalized_name=str(meta.get("normalized_name") or company_name),
            root_domain=str(meta.get("root_domain") or ""),
            target_id=str(meta.get("target_id") or ""),
            aliases=[str(item) for item in meta.get("aliases") or [] if str(item).strip()],
        )
        summary.update(
            normalized_name=identity.normalized_name,
            root_domain=identity.root_domain,
            target_id=identity.target_id,
        )
        assets = await AssetIntelligenceService(db).discover(
            identity=identity,
            project_id=project_id,
            task_id=task_id,
            provider_sizes={"fofa": fofa_size, "hunter": hunter_size},
            probe_concurrency=probe_concurrency,
        )
        providers = assets.get("providers") or {}
        summary.update(
            fofa_total=int((providers.get("fofa") or {}).get("count") or 0),
            hunter_total=int((providers.get("hunter") or {}).get("count") or 0),
            asset_total=int(assets.get("discovered") or 0),
            assets_inserted=int(assets.get("inserted") or 0),
            assets_updated=int(assets.get("updated") or 0),
            assets_unchanged=int(assets.get("unchanged") or 0),
            alive_assets=int(assets.get("alive") or 0),
            new_assets=len(assets.get("scan_urls") or []),
            providers=providers,
        )

        scan_urls = [str(url) for url in assets.get("scan_urls") or [] if str(url).strip()]
        if enable_scan and scan_urls:
            from api.services.url_scan_pipeline import UrlScanPipeline

            scan_result = await UrlScanPipeline(db, app_config).run_pipeline(
                task_id=f"{task_id}_assets",
                project_id=project_id,
                url_content="\n".join(scan_urls),
                min_attention_score=min_attention_score,
                target_id=identity.target_id,
            )
            if scan_result.get("status") == "error":
                raise RuntimeError(str(scan_result.get("error") or "资产深度扫描失败"))
            summary["scanned"] = True
        if identity.target_id:
            await targets_dao.touch_project_target_collection(
                db,
                project_id=project_id,
                target_id=identity.target_id,
                run_task_id=task_id,
            )
        summary["status"] = "completed"
        obs_log(
            "多源资产采集完成",
            task_id=task_id,
            project_id=project_id,
            source="asset_collect",
            level="notice",
            event="pipeline_done",
            data={
                "target_id": identity.target_id,
                "asset_total": summary["asset_total"],
                "alive_assets": summary["alive_assets"],
                "changed_assets": summary["new_assets"],
            },
        )
    except Exception as exc:  # noqa: BLE001
        summary["status"] = "error"
        summary["error"] = str(exc)
        logger.exception("多源资产采集失败 task=%s", task_id)
        obs_log(
            f"多源资产采集失败: {exc}",
            task_id=task_id,
            project_id=project_id,
            source="asset_collect",
            level="error",
            event="pipeline_error",
            data={"error": str(exc)},
        )
        raise
    return summary
