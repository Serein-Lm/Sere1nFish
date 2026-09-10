"""Target and keyword planning for mobile collection."""
from __future__ import annotations

import asyncio
from typing import Any

from api.dao import mobile_collect as collect_dao
from core.logger import get_logger
from core.mobile.collect.contracts import MobileCollectPlan, MobileSeedPlan


logger = get_logger("mobile_collect.planning")


def finalize_keyword_resolution(
    resolution: dict[str, Any],
    *,
    explicit_keywords: list[str],
    target: dict[str, Any] | None,
    task_def: dict[str, Any],
) -> dict[str, Any]:
    """Ensure Target-scoped collection never degrades to an empty query."""
    finalized = dict(resolution or {})
    keywords = list(
        dict.fromkeys(
            str(value or "").strip()
            for value in (finalized.get("keywords") or explicit_keywords)
            if str(value or "").strip()
        )
    )
    if keywords:
        finalized["keywords"] = keywords
        return finalized

    target_name = str(
        (target or {}).get("canonical_name") or task_def.get("target_name") or ""
    ).strip()
    target_id = str(
        (target or {}).get("target_id") or task_def.get("target_id") or ""
    ).strip()
    if not target_name:
        finalized["keywords"] = [""]
        return finalized

    finalized["keywords"] = [target_name]
    finalized["sources"] = list(
        dict.fromkeys(
            [*list(finalized.get("sources") or []), "target_name_fallback"]
        )
    )
    if target_id:
        finalized["target_ids"] = list(
            dict.fromkeys([*list(finalized.get("target_ids") or []), target_id])
        )
        keyword_targets = dict(finalized.get("keyword_targets") or {})
        keyword_targets[target_name] = {
            "target_id": target_id,
            "target_name": target_name,
        }
        finalized["keyword_targets"] = keyword_targets
    return finalized


async def resolve_collection_target(plan: MobileCollectPlan) -> dict[str, Any] | None:
    """Resolve the stable Target without making target failures fatal to collection."""
    task_def = plan.task_def
    try:
        if plan.dry_run:
            return await _resolve_dry_run_target(plan)
        if not bool(task_def.get("resolve_target_context", True)):
            return None
        from api.services.targets import resolve_collection_target as resolve_target

        target = await resolve_target(
            plan.db,
            task_def=task_def,
            project_id=plan.project_id or "",
        )
        if target and str(task_def.get("target_id") or "") != str(
            target.get("target_id") or ""
        ):
            task_def["target_id"] = target.get("target_id")
            await collect_dao.update_task_def(
                plan.db,
                plan.task_def_id,
                {"target_id": target.get("target_id")},
            )
        return target
    except Exception as exc:  # noqa: BLE001
        logger.warning("Target 解析失败，继续执行未聚类采集: %s", exc)
        return None


async def _resolve_dry_run_target(plan: MobileCollectPlan) -> dict[str, Any] | None:
    task_def = plan.task_def
    target_name = str(task_def.get("target_name") or "").strip()
    target_id = str(task_def.get("target_id") or "").strip()
    target = None
    if target_id:
        from api.dao import targets as targets_dao

        target = await targets_dao.get_target(plan.db, target_id)
    if target or not target_name:
        return target
    return {
        "target_id": target_id,
        "target_type": str(task_def.get("target_type") or "company"),
        "canonical_name": target_name,
    }


async def resolve_keyword_plan(
    plan: MobileCollectPlan,
    target: dict[str, Any] | None,
) -> MobileSeedPlan:
    explicit_keywords = list(plan.task_def.get("keywords") or [])
    resolution = await _resolve_keyword_library(plan, target, explicit_keywords)
    resolution = finalize_keyword_resolution(
        resolution,
        explicit_keywords=explicit_keywords,
        target=target,
        task_def=plan.task_def,
    )
    definition_fingerprint = collect_dao.definition_fingerprint(plan.task_def)
    seed_specs = await _build_seed_specs(
        plan,
        target,
        resolution,
        definition_fingerprint,
    )
    completed = (
        set()
        if plan.dry_run
        else await collect_dao.list_completed_checkpoint_keys(
            plan.db,
            run_task_id=plan.run_task_id,
            definition_fingerprint=definition_fingerprint,
        )
    )
    return MobileSeedPlan(
        target=target,
        keyword_resolution=resolution,
        definition_fingerprint=definition_fingerprint,
        seed_specs=seed_specs,
        pending_seed_specs=[
            spec for spec in seed_specs if spec["checkpoint_key"] not in completed
        ],
    )


async def _resolve_keyword_library(
    plan: MobileCollectPlan,
    target: dict[str, Any] | None,
    explicit_keywords: list[str],
) -> dict[str, Any]:
    resolution: dict[str, Any] = {
        "channel": "",
        "keywords": explicit_keywords,
        "target_ids": [],
        "sources": ["task_explicit"] if explicit_keywords else [],
    }
    if not bool(plan.task_def.get("use_target_keyword_library", True)) or not plan.project_id:
        return resolution
    from api.services.search_terms import (
        infer_collection_channel,
        resolve_project_target_terms,
    )

    channel = infer_collection_channel(
        app_name=str(plan.task_def.get("app_name") or ""),
        source_link_strategy=str(
            plan.task_def.get("source_link_strategy") or ""
        ),
    )
    if not channel:
        return resolution
    try:
        resolved = await resolve_project_target_terms(
            plan.db,
            project_id=plan.project_id,
            target_id=str(
                (target or {}).get("target_id")
                or plan.task_def.get("target_id")
                or ""
            ),
            target_name=str(
                (target or {}).get("canonical_name")
                or plan.task_def.get("target_name")
                or ""
            ),
            channel=channel,
            explicit_keywords=explicit_keywords,
            include_direct_children=bool(
                plan.task_def.get("include_direct_children", True)
            ),
            max_relation_depth=int(plan.task_def.get("max_relation_depth") or 2),
            max_related_targets=int(
                plan.task_def.get("max_related_targets") or 8
            ),
            skip_completed_descendants=bool(
                plan.task_def.get("skip_completed_related_targets", True)
            ),
            max_keywords=int(plan.task_def.get("max_resolved_keywords") or 60),
        )
        return resolved.as_dict()
    except Exception as exc:  # noqa: BLE001
        logger.warning("项目目标词解析失败，回退显式关键词: %s", exc)
        resolution["error"] = str(exc)[:500]
        return resolution


async def _build_seed_specs(
    plan: MobileCollectPlan,
    target: dict[str, Any] | None,
    resolution: dict[str, Any],
    definition_fingerprint: str,
) -> list[dict[str, Any]]:
    keyword_targets = resolution.get("keyword_targets") or {}
    targets_by_id = await _load_seed_targets(plan, target, keyword_targets)
    specs: list[dict[str, Any]] = []
    for keyword in list(resolution.get("keywords") or [""]):
        target_info = keyword_targets.get(keyword) if isinstance(keyword_targets, dict) else None
        resolved_target = target
        if isinstance(target_info, dict) and target_info.get("target_id"):
            target_id = str(target_info.get("target_id") or "")
            resolved_target = targets_by_id.get(target_id) or {
                "target_id": target_id,
                "target_type": "company",
                "canonical_name": str(target_info.get("target_name") or ""),
            }
        target_id = str((resolved_target or {}).get("target_id") or "")
        specs.append(
            {
                "keyword": keyword,
                "target": resolved_target,
                "target_id": target_id,
                "checkpoint_key": collect_dao.keyword_checkpoint_key(
                    definition_fingerprint=definition_fingerprint,
                    keyword=keyword,
                    target_id=target_id,
                ),
            }
        )
    return specs


async def _load_seed_targets(
    plan: MobileCollectPlan,
    target: dict[str, Any] | None,
    keyword_targets: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    targets_by_id: dict[str, dict[str, Any]] = {}
    if target and target.get("target_id"):
        targets_by_id[str(target["target_id"])] = target
    ids = list(
        dict.fromkeys(
            str(value.get("target_id") or "")
            for value in keyword_targets.values()
            if isinstance(value, dict) and value.get("target_id")
        )
    )
    missing = [target_id for target_id in ids if target_id not in targets_by_id]
    if not missing:
        return targets_by_id
    from api.services.targets import resolve_target

    resolved = await asyncio.gather(
        *(resolve_target(plan.db, target_id=target_id) for target_id in missing)
    )
    targets_by_id.update(
        {
            str(item["target_id"]): item
            for item in resolved
            if item and item.get("target_id")
        }
    )
    return targets_by_id
