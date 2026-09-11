"""Runtime adapters for each persisted project task type."""
from __future__ import annotations

from typing import Any

from api.db.mongodb import get_db


async def dispatch_url_scan(
    task_id: str, project_id: str, params: dict[str, Any]
) -> dict[str, Any] | None:
    from api.services.info_collection.tuning import get_collection_runtime_tuning
    from api.services.runtime_config import get_runtime_app_config
    from api.services.url_scan_pipeline import UrlScanPipeline

    url_content = params.get("url_text", "")
    urls = params.get("urls", [])
    if urls:
        url_content = "\n".join(urls)
    db = get_db()
    runtime_config = await get_runtime_app_config()
    tuning = (await get_collection_runtime_tuning()).with_overrides(
        url_probe_concurrency=params.get("url_probe_concurrency"),
        url_scan_concurrency=params.get("url_scan_concurrency"),
        copywriting_concurrency=params.get("copywriting_concurrency"),
    )
    result = await UrlScanPipeline(db, runtime_config).run_pipeline(
        task_id=task_id,
        project_id=project_id,
        url_content=url_content,
        min_attention_score=params.get("min_attention_score", 40),
        probe_concurrency=tuning.url_probe_concurrency,
        scan_concurrency=tuning.url_scan_concurrency,
        copywriting_concurrency=tuning.copywriting_concurrency,
        enable_copywriting=params.get("enable_copywriting", True),
        selected_skill_ids=params.get("selected_skill_ids", []),
    )
    if result.get("status") == "error":
        raise RuntimeError(str(result.get("error") or "URL 扫描失败"))
    return result


async def dispatch_xhs_search(
    task_id: str, project_id: str, params: dict[str, Any]
) -> Any:
    from api.services.runtime_config import get_runtime_app_config
    from api.services.xhs_pipeline import run_xhs_pipeline

    return await run_xhs_pipeline(
        db=get_db(),
        app_config=await get_runtime_app_config(),
        task_id=task_id,
        project_id=project_id,
        keyword=params.get("keyword", ""),
        max_notes=params.get("max_notes", 20),
        attention_threshold=params.get("attention_threshold", 60),
        target_id=str(params.get("target_id") or ""),
        target_name=str(params.get("target_name") or ""),
    )


async def dispatch_douyin_search(
    task_id: str, project_id: str, params: dict[str, Any]
) -> Any:
    from api.services.douyin_pipeline import run_douyin_pipeline
    from api.services.runtime_config import get_runtime_app_config

    return await run_douyin_pipeline(
        db=get_db(),
        app_config=await get_runtime_app_config(),
        project_id=project_id,
        keyword=params.get("keyword", ""),
        max_videos=params.get("max_videos", 20),
        publish_time=params.get("publish_time", 0),
        task_id=task_id,
    )


async def dispatch_web_tagging(
    task_id: str, project_id: str, params: dict[str, Any]
) -> Any:
    from api.services.runtime_config import get_runtime_app_config
    from api.services.web_tagging_pipeline import run_web_tagging_pipeline

    return await run_web_tagging_pipeline(
        db=get_db(),
        app_config=await get_runtime_app_config(),
        project_id=project_id,
        company_name=params.get("company_name", ""),
        max_urls=params.get("max_urls", 50),
        max_tagging_urls=params.get("max_tagging_urls", 10),
        task_id=task_id,
    )


async def dispatch_company_scan(
    task_id: str, project_id: str, params: dict[str, Any]
) -> dict[str, Any]:
    from api.services.company_scan_pipeline import CompanyScanPipeline
    from api.services.info_collection.tuning import get_collection_runtime_tuning
    from api.services.runtime_config import get_runtime_app_config

    db = get_db()
    runtime_config = await get_runtime_app_config()
    tuning = (await get_collection_runtime_tuning()).with_overrides(
        asset_probe_concurrency=params.get("asset_probe_concurrency"),
        url_probe_concurrency=params.get("url_probe_concurrency"),
        url_scan_concurrency=params.get("url_scan_concurrency"),
        copywriting_concurrency=params.get("copywriting_concurrency"),
        xhs_search_concurrency=params.get("xhs_search_concurrency"),
    )
    pipeline = CompanyScanPipeline(
        db,
        runtime_config,
        selected_skill_ids=params.get("selected_skill_ids", []),
    )
    result = await pipeline.run_pipeline(
        task_id=task_id,
        project_id=project_id,
        company_name=params.get("company_name", ""),
        target_id=str(params.get("target_id") or ""),
        refresh_target_identity=bool(params.get("refresh_target_identity", False)),
        batch_id=str(params.get("_batch_id") or ""),
        target_batch_tags=params.get("target_batch_tags", []),
        url_text=params.get("url_text", ""),
        urls=params.get("urls", []),
        enable_url_scan=params.get("enable_url_scan", True),
        enable_asset_discovery=params.get("enable_asset_discovery", True),
        enable_xhs=params.get("enable_xhs", False),
        enable_subsidiary_xhs=params.get("enable_subsidiary_xhs", False),
        enable_subsidiary_bidding=params.get("enable_subsidiary_bidding", False),
        xhs_target_selection_mode=params.get("xhs_target_selection_mode", "auto"),
        xhs_manual_targets=params.get("xhs_manual_targets", []),
        enable_bidding=params.get("enable_bidding", False),
        enable_bidding_visual_analysis=params.get("enable_bidding_visual_analysis"),
        bidding_page_size=max(1, min(int(params.get("bidding_page_size") or 20), 20)),
        bidding_max_records=max(
            1, min(int(params.get("bidding_max_records") or 20), 20)
        ),
        bidding_lookback_days=max(
            1, min(int(params.get("bidding_lookback_days") or 30), 30)
        ),
        enable_wechat=params.get("enable_wechat", False),
        wechat_device_id=params.get("wechat_device_id", ""),
        wechat_app_instance=params.get("wechat_app_instance", "primary"),
        wechat_target_selection_mode=params.get(
            "wechat_target_selection_mode", "auto"
        ),
        enable_scholar=params.get("enable_scholar", True),
        scholar_direction=params.get("scholar_direction", ""),
        scholar_unit_en=params.get("scholar_unit_en", ""),
        scholar_limit=max(1, min(int(params.get("scholar_limit") or 10), 50)),
        enable_copywriting=params.get("enable_copywriting", True),
        xhs_max_notes=params.get("xhs_max_notes") or params.get("max_notes", 20),
        xhs_attention_threshold=params.get("xhs_attention_threshold")
        or params.get("attention_threshold", 60),
        min_attention_score=params.get("min_attention_score", 40),
        profile_copywriting_threshold=params.get(
            "profile_copywriting_threshold", 60
        ),
        fofa_size=params.get("fofa_size", 200),
        hunter_size=params.get("hunter_size", 200),
        asset_probe_concurrency=tuning.asset_probe_concurrency,
        incremental_scan=params.get("incremental_scan", False),
        url_probe_concurrency=tuning.url_probe_concurrency,
        url_scan_concurrency=tuning.url_scan_concurrency,
        copywriting_concurrency=tuning.copywriting_concurrency,
        xhs_search_concurrency=tuning.xhs_search_concurrency,
        enable_control_structure=params.get("enable_control_structure", False),
        control_min_ownership_percent=float(
            params.get("control_min_ownership_percent") or 100.0
        ),
        control_max_depth=max(1, min(int(params.get("control_max_depth") or 1), 2)),
        control_max_entities=max(
            1, min(int(params.get("control_max_entities") or 100), 500)
        ),
        control_lookup_concurrency=max(
            1, min(int(params.get("control_lookup_concurrency") or 4), 12)
        ),
        control_icp_concurrency=max(
            1, min(int(params.get("control_icp_concurrency") or 6), 20)
        ),
        control_scan_concurrency=max(
            1, min(int(params.get("control_scan_concurrency") or 1), 12)
        ),
        subsidiary_scan_limit=max(
            1, min(int(params.get("subsidiary_scan_limit") or 12), 100)
        ),
        skip_completed_subsidiaries=params.get("skip_completed_subsidiaries", True),
        company_core_concurrency=tuning.company_scan_concurrency,
        website_collection_mode=params.get("website_collection_mode", "deep"),
        website_root_domains=params.get("website_root_domains", []),
        website_required_path_segments=params.get(
            "website_required_path_segments", []
        ),
        requested_by=str(params.get("_requested_by") or ""),
    )
    if result.get("status") == "error":
        raise RuntimeError(str(result.get("error") or "综合公司扫描失败"))
    return result


async def dispatch_fofa_collect(
    task_id: str, project_id: str, params: dict[str, Any]
) -> Any:
    from api.services.fofa_collect import run_fofa_collect
    from api.services.info_collection.tuning import get_collection_runtime_tuning
    from api.services.runtime_config import get_runtime_app_config

    tuning = (await get_collection_runtime_tuning()).with_overrides(
        asset_probe_concurrency=params.get("probe_concurrency"),
        url_probe_concurrency=params.get("url_probe_concurrency"),
        url_scan_concurrency=params.get("url_scan_concurrency"),
        copywriting_concurrency=params.get("copywriting_concurrency"),
    )
    return await run_fofa_collect(
        db=get_db(),
        app_config=await get_runtime_app_config(),
        task_id=task_id,
        project_id=project_id,
        company_name=params.get("company_name", ""),
        fofa_size=params.get("fofa_size", 200),
        hunter_size=params.get("hunter_size", 200),
        enable_scan=params.get("enable_scan", True),
        min_attention_score=params.get("min_attention_score", 40),
        probe_concurrency=tuning.asset_probe_concurrency,
        incremental_scan=params.get("incremental_scan", False),
        url_probe_concurrency=tuning.url_probe_concurrency,
        url_scan_concurrency=tuning.url_scan_concurrency,
        copywriting_concurrency=tuning.copywriting_concurrency,
        selected_skill_ids=params.get("selected_skill_ids", []),
    )


async def dispatch_scholar_contact(
    task_id: str, project_id: str, params: dict[str, Any]
) -> Any:
    from api.services.runtime_config import get_runtime_app_config
    from api.services.scholar_contact_pipeline import run_scholar_contact_collect

    return await run_scholar_contact_collect(
        get_db(),
        await get_runtime_app_config(),
        task_id=task_id,
        project_id=project_id,
        target_id=params.get("target_id", ""),
        unit=params.get("unit", ""),
        direction=params.get("direction", ""),
        unit_en=params.get("unit_en", ""),
        limit=params.get("limit", 10),
        enable_chrome_pmc=params.get("enable_chrome_pmc", False),
        dry_run=params.get("dry_run", False),
        bulk=params.get("bulk", False),
        max_articles=params.get("max_articles", 2000),
    )


async def dispatch_mobile_collect(
    task_id: str, project_id: str, params: dict[str, Any]
) -> Any:
    from api.services.mobile_collect_pipeline import _dispatch_mobile_collect

    return await _dispatch_mobile_collect(task_id, project_id, params)


async def dispatch_target_research(
    task_id: str, project_id: str, params: dict[str, Any]
) -> Any:
    from api.services.runtime_config import get_runtime_app_config
    from api.services.target_research import run_target_research

    return await run_target_research(
        get_db(),
        await get_runtime_app_config(),
        task_id=task_id,
        project_id=project_id,
        target_id=str(params.get("target_id") or ""),
        max_related_targets=int(params.get("max_related_targets", 8)),
        scan_discovered_targets=bool(params.get("scan_discovered_targets", True)),
        rescan_root=bool(params.get("rescan_root", False)),
        force_refresh=bool(params.get("force_refresh", True)),
        scan_params=dict(params.get("scan_params") or {}),
        requested_by=str(params.get("_requested_by") or ""),
    )


async def dispatch_social_media_collect(
    task_id: str, project_id: str, params: dict[str, Any]
) -> Any:
    from api.services.social_collection import execute_social_collection_job

    return await execute_social_collection_job(task_id, project_id, params)
