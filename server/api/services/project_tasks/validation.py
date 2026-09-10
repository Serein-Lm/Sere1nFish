"""Task-parameter policies shared by JSON and file submission APIs."""
from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase


def normalize_selected_skill_params(params: dict[str, Any]) -> None:
    from api.services.skill_library.selection import validate_selected_skill_ids

    raw_selection = params.get("selected_skill_ids")
    if raw_selection is None:
        raw_selection = params.get("selected_skills")
    if raw_selection is not None:
        params["selected_skill_ids"] = validate_selected_skill_ids(raw_selection)
    params.pop("selected_skills", None)


def _bounded_integer(
    params: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
    label: str,
) -> None:
    if key not in params:
        return
    try:
        value = int(params.get(key) if params.get(key) is not None else default)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须为 {minimum} 到 {maximum}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{label}必须为 {minimum} 到 {maximum}")
    params[key] = value


async def normalize_xhs_target_params(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    params: dict[str, Any],
) -> None:
    target_id = str(params.get("target_id") or "").strip()
    if not target_id:
        params.pop("target_name", None)
        return
    from api.services.targets import require_project_target

    params.update(
        await require_project_target(
            db,
            project_id=project_id,
            target_id=target_id,
        )
    )


def normalize_company_scan_params(params: dict[str, Any]) -> None:
    from api.dao.targets import normalize_batch_tags

    if "target_batch_tags" in params:
        params["target_batch_tags"] = normalize_batch_tags(
            params.get("target_batch_tags")
        )

    website_collection_mode = str(
        params.get("website_collection_mode") or "deep"
    ).strip().casefold()
    if website_collection_mode not in {"standard", "deep"}:
        raise ValueError("官网归档模式必须为 standard 或 deep")
    params["website_collection_mode"] = website_collection_mode

    _bounded_integer(
        params,
        "bidding_lookback_days",
        default=30,
        minimum=1,
        maximum=30,
        label="招投标回溯天数",
    )
    _bounded_integer(
        params,
        "xhs_attention_threshold",
        default=60,
        minimum=0,
        maximum=100,
        label="小红书关注度阈值",
    )
    _bounded_integer(
        params,
        "profile_copywriting_threshold",
        default=60,
        minimum=0,
        maximum=100,
        label="画像话术阈值",
    )

    if "website_required_path_segments" in params:
        from api.services.website_documents import normalize_required_path_segments

        params["website_required_path_segments"] = normalize_required_path_segments(
            params.get("website_required_path_segments")
        )
    if "website_root_domains" in params:
        from api.services.website_documents import normalize_website_root_domains

        params["website_root_domains"] = normalize_website_root_domains(
            params.get("website_root_domains")
        )

    if params.get("enable_control_structure", False) or any(
        key in params
        for key in (
            "control_max_depth",
            "subsidiary_scan_limit",
            "skip_completed_subsidiaries",
        )
    ):
        try:
            control_max_depth = int(params.get("control_max_depth") or 1)
        except (TypeError, ValueError) as exc:
            raise ValueError("全资单位层级必须为 1 或 2") from exc
        if control_max_depth not in {1, 2}:
            raise ValueError("全资单位层级必须为 1 或 2")
        params["control_max_depth"] = control_max_depth
        _bounded_integer(
            params,
            "subsidiary_scan_limit",
            default=12,
            minimum=1,
            maximum=100,
            label="关联单位补扫数量",
        )
        params["skip_completed_subsidiaries"] = bool(
            params.get("skip_completed_subsidiaries", True)
        )

    for key in (
        "refresh_target_identity",
        "enable_bidding_visual_analysis",
        "enable_subsidiary_bidding",
    ):
        if key in params:
            params[key] = bool(params.get(key))
    if not params.get("enable_bidding", False):
        params["enable_subsidiary_bidding"] = False

    if params.get("enable_wechat", False):
        from api.services.wechat_collection import normalize_wechat_app_instance
        from api.services.wechat_target_selection import (
            normalize_wechat_selection_mode,
        )

        params["wechat_app_instance"] = normalize_wechat_app_instance(
            params.get("wechat_app_instance", "primary")
        )
        params["wechat_target_selection_mode"] = normalize_wechat_selection_mode(
            params.get("wechat_target_selection_mode", "auto")
        )

    if params.get("enable_scholar", True):
        for key in ("scholar_direction", "scholar_unit_en"):
            value = str(params.get(key) or "").strip()
            if value:
                params[key] = value
            else:
                params.pop(key, None)


def _validate_required_text(
    params: dict[str, Any], key: str, label: str
) -> None:
    if not str(params.get(key) or "").strip():
        raise ValueError(f"{label}不能为空")


async def prepare_project_task_params(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    task_type: str,
    params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return an isolated, normalized parameter object for one task type."""
    normalized = dict(params or {})
    normalize_selected_skill_params(normalized)

    if task_type == "company_scan":
        _validate_required_text(normalized, "company_name", "公司名称")
        normalize_company_scan_params(normalized)
        if normalized.get("enable_wechat", False):
            from api.services.wechat_collection import ensure_wechat_task_definition

            await ensure_wechat_task_definition(
                db,
                project_id=project_id,
                device_id=str(normalized.get("wechat_device_id") or ""),
                app_instance=str(normalized.get("wechat_app_instance") or "primary"),
            )
    elif task_type == "xhs_search":
        _validate_required_text(normalized, "keyword", "搜索关键词")
        await normalize_xhs_target_params(
            db,
            project_id=project_id,
            params=normalized,
        )
    elif task_type == "url_scan":
        urls = normalized.get("urls") or []
        if not urls and not str(normalized.get("url_text") or "").strip():
            raise ValueError("URL 列表不能为空")
    elif task_type in {"web_tagging", "fofa_collect"}:
        _validate_required_text(normalized, "company_name", "公司名称")
    elif task_type == "scholar_contact":
        _validate_required_text(normalized, "unit", "单位名称")
        _validate_required_text(normalized, "direction", "研究方向")
    elif task_type == "douyin_search":
        _validate_required_text(normalized, "keyword", "搜索关键词")
    return normalized
