"""扫描任务模板：admin 预配置下发参数，前端选模板 + 勾渠道 + 输入目标即可下发。

模板保存在 system_config 的 `scan_templates` 段（加密存储，与其他配置段同机制），
结构：{ templates: [ { id, name, description, is_default, params } ] }。
`params` 内的字段与前端任务表单值同构（company_names 等每次下发的目标类字段
不属于模板），下发时由前端展开进表单。
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import config as config_dao

SECTION = "scan_templates"
_TEMPLATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_TEMPLATES = 50

# 模板允许携带的参数白名单：下发表单里「非目标类」的可选字段。
# 目标类字段（company_names/urls/target_batch_tags 等）和任务类型不进模板，
# 始终由用户本次填写。
_ALLOWED_PARAM_KEYS: frozenset[str] = frozenset({
    # 渠道开关
    "enable_asset_discovery", "enable_control_structure", "enable_url_scan",
    "enable_xhs", "enable_subsidiary_xhs", "xhs_target_selection_mode",
    "xhs_manual_targets", "enable_bidding", "enable_subsidiary_bidding",
    "enable_bidding_visual_analysis", "bidding_page_size",
    "bidding_max_records", "bidding_lookback_days",
    "enable_wechat", "wechat_device_id", "wechat_app_instance",
    "wechat_target_selection_mode", "wechat_collection_priority",
    "wechat_append_keywords", "enable_scholar", "scholar_direction",
    "scholar_unit_en", "scholar_limit", "enable_copywriting",
    "profile_copywriting_threshold",
    # 深度与预算
    "control_min_ownership_percent", "control_max_depth",
    "subsidiary_scan_limit", "skip_completed_subsidiaries",
    "asset_scan_mode", "website_collection_mode", "website_root_domains",
    "website_required_path_segments", "refresh_target_identity",
    "incremental_scan", "xhs_max_notes", "xhs_attention_threshold",
    "min_attention_score", "fofa_size", "hunter_size",
    # 并发调优
    "asset_probe_concurrency", "url_probe_concurrency",
    "url_scan_concurrency", "copywriting_concurrency",
    "xhs_search_concurrency", "company_scan_concurrency",
    "control_max_entities", "control_lookup_concurrency",
    "control_icp_concurrency", "control_scan_concurrency",
})


class ScanTemplateError(ValueError):
    """模板数据不合法。"""


def _clean_params(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ScanTemplateError("params 必须是对象")
    unknown = [key for key in raw if key not in _ALLOWED_PARAM_KEYS]
    if unknown:
        raise ScanTemplateError(f"不支持的模板参数: {', '.join(sorted(unknown)[:5])}")
    return dict(raw)


def _clean_template(raw: Any, *, partial: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ScanTemplateError("模板必须是对象")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ScanTemplateError("模板名称不能为空")
    if len(name) > 60:
        raise ScanTemplateError("模板名称过长（最多 60 字）")
    description = str(raw.get("description") or "").strip()[:200]
    template_id = str(raw.get("id") or "").strip()
    if template_id and not _TEMPLATE_ID_RE.fullmatch(template_id):
        raise ScanTemplateError("模板 id 格式无效")
    params = _clean_params(raw.get("params"))
    cleaned: dict[str, Any] = {
        "name": name,
        "description": description,
        "is_default": bool(raw.get("is_default")),
        "params": params,
    }
    if not partial and template_id:
        cleaned["id"] = template_id
    return cleaned


async def _load_section(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    doc = await config_dao.get_config(db, SECTION)
    config = doc.get("config", {}) if doc else {}
    templates = config.get("templates")
    return {
        "templates": [
            item for item in (templates if isinstance(templates, list) else [])
            if isinstance(item, dict)
        ]
    }


async def _save_section(db: AsyncIOMotorDatabase, templates: list[dict[str, Any]]) -> None:
    await config_dao.set_config(db, SECTION, {"templates": templates})


async def list_scan_templates(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    """列出全部模板；默认模板排在最前，其余按名称排序。"""
    section = await _load_section(db)
    templates = list(section["templates"])
    templates.sort(key=lambda item: (not item.get("is_default"), str(item.get("name") or "")))
    return templates


async def get_default_scan_template(db: AsyncIOMotorDatabase) -> dict[str, Any] | None:
    for template in await list_scan_templates(db):
        if template.get("is_default"):
            return template
    return None


async def upsert_scan_template(
    db: AsyncIOMotorDatabase,
    raw: dict[str, Any],
    *,
    template_id: str = "",
) -> dict[str, Any]:
    """新增或更新模板。指定 template_id 时更新，否则创建新 id。"""
    cleaned = _clean_template(raw)
    section = await _load_section(db)
    templates = section["templates"]
    if len(templates) >= MAX_TEMPLATES and not template_id:
        raise ScanTemplateError(f"模板数量已达上限（{MAX_TEMPLATES}）")
    normalized_id = str(template_id or cleaned.get("id") or "").strip()
    if cleaned.get("is_default"):
        # 默认模板唯一：其余模板取消默认标记
        for item in templates:
            item["is_default"] = False
    if normalized_id:
        for index, item in enumerate(templates):
            if str(item.get("id") or "") == normalized_id:
                merged = {**item, **cleaned, "id": normalized_id}
                templates[index] = merged
                await _save_section(db, templates)
                return merged
        cleaned["id"] = normalized_id
        templates.append(cleaned)
        await _save_section(db, templates)
        return cleaned
    new_id = "tpl_" + uuid.uuid4().hex[:12]
    cleaned["id"] = new_id
    templates.append(cleaned)
    await _save_section(db, templates)
    return cleaned


async def delete_scan_template(db: AsyncIOMotorDatabase, template_id: str) -> bool:
    section = await _load_section(db)
    templates = section["templates"]
    remaining = [item for item in templates if str(item.get("id") or "") != template_id]
    if len(remaining) == len(templates):
        return False
    await _save_section(db, remaining)
    return True
