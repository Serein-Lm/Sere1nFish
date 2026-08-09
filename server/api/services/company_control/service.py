"""公司全资关联单位分层发现、ICP 补全和项目 Target 持久化。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from crawler_tools.tianyancha_tools import (
    BALANCE_INSUFFICIENT_CODE,
    OUTBOUND_INVESTMENT_INTERFACE_ID,
    PERMISSION_DENIED_CODE,
    PROVIDER_DISABLED_CODE,
    TianyanchaApiError,
)
from core.logger import get_logger

from .contracts import ControlledEntity
from .factory import CompanyControlProviderFactory

logger = get_logger("company_control")


@dataclass(slots=True)
class _ControlParent:
    target_id: str
    name: str
    lineage_target_ids: list[str]
    lineage_target_names: list[str]


class CompanyControlService:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self.db = db

    async def discover_and_persist(
        self,
        *,
        project_id: str,
        task_id: str,
        parent_target: dict[str, Any],
        company_name: str,
        max_depth: int = 1,
        max_entities: int = 100,
        page_concurrency: int = 4,
        icp_concurrency: int = 6,
    ) -> dict[str, Any]:
        parent_target_id = str(parent_target.get("target_id") or "")
        parent_target_name = str(parent_target.get("canonical_name") or company_name).strip()
        safe_max_depth = max(1, min(int(max_depth or 1), 2))
        safe_max_entities = max(1, int(max_entities or 100))
        safe_lookup_concurrency = max(1, int(page_concurrency or 4))
        base_result: dict[str, Any] = {
            "enabled": True,
            "status": "running",
            "provider": "tianyancha_outbound_investment",
            "relation_type": "wholly_owned_direct_investment",
            "max_depth": safe_max_depth,
            "relation_depth": 0,
            "ownership_percent": 100.0,
            "total_reported": 0,
            "matched": 0,
            "persisted": 0,
            "pages_fetched": 0,
            "parents_queried": 0,
            "depth_counts": {},
            "truncated": False,
            "entities": [],
            "errors": [],
            "cycles_skipped": 0,
            "permission_required": False,
        }
        from api.services.tianyancha_runtime import get_tianyancha_runtime_policy

        policy = await get_tianyancha_runtime_policy(self.db)
        if not policy.enabled:
            base_result.update(
                {
                    "enabled": False,
                    "status": "disabled",
                    "disabled_reason": policy.disabled_reason or "runtime_disabled",
                }
            )
            return base_result
        try:
            provider = await CompanyControlProviderFactory.create("tianyancha")
        except TianyanchaApiError as exc:
            provider_disabled = exc.code in {
                BALANCE_INSUFFICIENT_CODE,
                PROVIDER_DISABLED_CODE,
            }
            base_result.update(
                {
                    "enabled": not provider_disabled,
                    "status": "disabled" if provider_disabled else "unavailable",
                    "error_code": exc.code,
                    "errors": [exc.reason],
                    "disabled_reason": (
                        "quota_insufficient"
                        if exc.code == BALANCE_INSUFFICIENT_CODE
                        else exc.reason
                        if provider_disabled
                        else ""
                    ),
                    "permission_required": exc.code == PERMISSION_DENIED_CODE,
                }
            )
            log = logger.info if provider_disabled else logger.warning
            log("全资关联单位发现不可用 company=%s code=%s reason=%s", company_name, exc.code, exc.reason)
            return base_result
        except Exception as exc:  # noqa: BLE001
            base_result.update({"status": "error", "errors": [str(exc)]})
            logger.exception("全资关联单位发现异常 company=%s", company_name)
            return base_result
        provider_name = str(
            getattr(provider, "name", "") or "tianyancha_outbound_investment"
        )

        from api.dao import company_meta as company_meta_dao
        from api.dao import targets as targets_dao
        from api.services.search_terms import build_target_channel_terms
        from api.services.target_scan_profile import build_target_scan_profile

        icp_semaphore = asyncio.Semaphore(max(1, int(icp_concurrency or 6)))

        async def _enrich(
            parent: _ControlParent,
            entity: ControlledEntity,
        ) -> tuple[_ControlParent, ControlledEntity, str]:
            async with icp_semaphore:
                try:
                    return parent, await provider.lookup_icp(entity), ""
                except TianyanchaApiError as exc:
                    logger.warning(
                        "全资关联单位 ICP 查询失败 company=%s code=%s",
                        entity.name,
                        exc.code,
                    )
                    return parent, entity, f"{entity.name}: ICP 查询失败({exc.code}) {exc.reason}"
                except Exception as exc:  # noqa: BLE001
                    logger.warning("全资关联单位 ICP 查询异常 company=%s: %s", entity.name, exc)
                    return parent, entity, f"{entity.name}: ICP 查询异常 {exc}"

        async def _persist(
            parent: _ControlParent,
            entity: ControlledEntity,
            icp_error: str,
            depth: int,
        ) -> tuple[dict[str, Any], _ControlParent] | None:
            aliases = list(dict.fromkeys([entity.name, *entity.aliases]))
            entity_name_key = targets_dao.normalize_target_name(entity.name)
            lineage_name_keys = {
                targets_dao.normalize_target_name(name)
                for name in parent.lineage_target_names
                if str(name or "").strip()
            }
            if entity_name_key and entity_name_key in lineage_name_keys:
                logger.warning(
                    "跳过全资关系名称循环 root=%s parent=%s entity=%s",
                    parent_target_id,
                    parent.target_id,
                    entity.name,
                )
                return None
            target = await targets_dao.upsert_target(
                self.db,
                name=entity.name,
                root_domain=entity.root_domain,
                aliases=aliases,
                source=provider_name,
                # Legal entities may share short brand aliases. Control-tree
                # identity must use the legal name/domain, not those aliases.
                match_aliases=False,
            )
            target_id = str(target.get("target_id") or "")
            if not target_id or target_id in parent.lineage_target_ids:
                logger.warning(
                    "跳过全资关系循环 root=%s parent=%s entity=%s target=%s",
                    parent_target_id,
                    parent.target_id,
                    entity.name,
                    target_id,
                )
                return None
            scan_profile = build_target_scan_profile(
                canonical_name=entity.name,
                identity_aliases=[entity.name],
                verified_aliases=list(entity.aliases or []),
                fallback_aliases=aliases,
                existing_profile=dict(target.get("scan_profile") or {}),
                source=provider_name,
            )
            target = await targets_dao.update_target_scan_profile(
                self.db,
                target_id=target_id,
                profile=scan_profile,
            ) or target
            aliases = list(scan_profile.get("search_aliases") or [entity.name])
            lineage_target_ids = [*parent.lineage_target_ids, target_id]
            lineage_target_names = [*parent.lineage_target_names, entity.name]
            relation = {
                "root_target_id": parent_target_id,
                "root_target_name": parent_target_name,
                "parent_target_id": parent.target_id,
                "parent_target_name": parent.name,
                "relation_type": "wholly_owned_direct_investment",
                "relation_depth": depth,
                "ownership_percent": 100.0,
                "relation_source": provider_name,
                "provider_company_id": entity.provider_id,
                "registration_status": entity.registration_status,
                "relation_paths": entity.relation_paths,
                "lineage_target_ids": lineage_target_ids,
                "lineage_target_names": lineage_target_names,
                "lineage": [
                    {
                        "target_id": lineage_id,
                        "target_name": lineage_name,
                        "relation_depth": lineage_depth,
                    }
                    for lineage_depth, (lineage_id, lineage_name) in enumerate(
                        zip(lineage_target_ids, lineage_target_names)
                    )
                ],
            }
            channel_terms = build_target_channel_terms(names=aliases)
            project_target = await targets_dao.link_project_target(
                self.db,
                project_id=project_id,
                target=target,
                search_terms=aliases,
                search_terms_by_channel=channel_terms,
                task_def_id=task_id,
                relation=relation,
            )
            await company_meta_dao.upsert_company_meta(
                self.db,
                project_id=project_id,
                input_name=entity.name,
                normalized_name=entity.name,
                root_domain=entity.root_domain,
                aliases=aliases,
                confidence=1.0,
                source=f"{provider_name}_icp",
                task_id=task_id,
                target_id=target_id,
                icp_domains=entity.icp_domains,
                relation=relation,
                provenance={
                    "investment_provider": provider_name,
                    "investment_interface_id": OUTBOUND_INVESTMENT_INTERFACE_ID,
                    "domain_provider": "tianyancha_icp",
                    "domain_interface_id": 1038,
                },
            )
            output = {
                "target_id": target_id,
                "project_target_id": project_target.get("project_target_id") or "",
                "name": entity.name,
                "aliases": aliases,
                "display_name": scan_profile.get("display_name") or entity.name,
                "short_names": list(scan_profile.get("short_names") or []),
                "scan_profile": scan_profile,
                "root_domain": entity.root_domain,
                "icp_domains": entity.icp_domains,
                "icp_records": entity.icp_records,
                "ownership_percent": 100.0,
                "root_target_id": parent_target_id,
                "root_target_name": parent_target_name,
                "parent_target_id": parent.target_id,
                "parent_target_name": parent.name,
                "relation_depth": depth,
                "lineage_target_ids": lineage_target_ids,
                "lineage_target_names": lineage_target_names,
                "registration_status": entity.registration_status,
                "provider_company_id": entity.provider_id,
                "icp_error": icp_error or None,
            }
            return output, _ControlParent(
                target_id=target_id,
                name=entity.name,
                lineage_target_ids=lineage_target_ids,
                lineage_target_names=lineage_target_names,
            )

        frontier = [
            _ControlParent(
                target_id=parent_target_id,
                name=parent_target_name,
                lineage_target_ids=[parent_target_id] if parent_target_id else [],
                lineage_target_names=[parent_target_name] if parent_target_name else [],
            )
        ]
        seen_entities = {
            f"id:{parent_target_id}" if parent_target_id else f"name:{parent_target_name.casefold()}"
        }
        deepest_persisted = 0

        for depth in range(1, safe_max_depth + 1):
            remaining = safe_max_entities - len(base_result["entities"])
            if not frontier or remaining <= 0:
                if frontier:
                    base_result["truncated"] = True
                break

            lookup_semaphore = asyncio.Semaphore(safe_lookup_concurrency)
            nested_page_concurrency = safe_lookup_concurrency if depth == 1 else 1

            async def _discover_parent(
                parent: _ControlParent,
            ) -> tuple[_ControlParent, Any, Exception | None]:
                async with lookup_semaphore:
                    try:
                        found = await provider.discover(
                            parent.name,
                            max_entities=remaining,
                            page_concurrency=nested_page_concurrency,
                        )
                        return parent, found, None
                    except Exception as exc:  # noqa: BLE001
                        return parent, None, exc

            discoveries = await asyncio.gather(
                *[_discover_parent(parent) for parent in frontier]
            )
            edges: list[tuple[_ControlParent, ControlledEntity]] = []
            for parent, discovery, discovery_error in discoveries:
                base_result["parents_queried"] += 1
                if discovery_error is not None:
                    if depth == 1:
                        if isinstance(discovery_error, TianyanchaApiError):
                            provider_disabled = discovery_error.code in {
                                BALANCE_INSUFFICIENT_CODE,
                                PROVIDER_DISABLED_CODE,
                            }
                            base_result.update(
                                {
                                    "enabled": not provider_disabled,
                                    "status": (
                                        "disabled" if provider_disabled else "unavailable"
                                    ),
                                    "error_code": discovery_error.code,
                                    "errors": [discovery_error.reason],
                                    "disabled_reason": (
                                        "quota_insufficient"
                                        if discovery_error.code == BALANCE_INSUFFICIENT_CODE
                                        else discovery_error.reason
                                        if provider_disabled
                                        else ""
                                    ),
                                    "permission_required": (
                                        discovery_error.code == PERMISSION_DENIED_CODE
                                    ),
                                }
                            )
                        else:
                            base_result.update(
                                {"status": "error", "errors": [str(discovery_error)]}
                            )
                        return base_result
                    base_result["errors"].append(
                        f"{parent.name}: 下级单位查询失败 {discovery_error}"
                    )
                    continue
                base_result["provider"] = discovery.provider
                base_result["total_reported"] += int(discovery.total_reported or 0)
                base_result["pages_fetched"] += int(discovery.pages_fetched or 0)
                base_result["truncated"] = bool(
                    base_result["truncated"] or discovery.truncated
                )
                for entity in discovery.entities:
                    entity_key = (
                        f"id:{entity.provider_id}"
                        if entity.provider_id
                        else f"name:{entity.name.casefold()}"
                    )
                    if (
                        not entity.name
                        or entity_key in seen_entities
                    ):
                        continue
                    if len(edges) >= remaining:
                        base_result["truncated"] = True
                        break
                    seen_entities.add(entity_key)
                    edges.append((parent, entity))

            base_result["matched"] += len(edges)
            if not edges:
                frontier = []
                continue
            enriched = await asyncio.gather(
                *[_enrich(parent, entity) for parent, entity in edges]
            )
            persisted = await asyncio.gather(
                *[
                    _persist(parent, entity, icp_error, depth)
                    for parent, entity, icp_error in enriched
                ],
                return_exceptions=True,
            )
            next_frontier: list[_ControlParent] = []
            depth_persisted = 0
            for item in persisted:
                if isinstance(item, Exception):
                    base_result["errors"].append(str(item))
                    continue
                if item is None:
                    base_result["cycles_skipped"] += 1
                    continue
                output, child_parent = item
                base_result["entities"].append(output)
                next_frontier.append(child_parent)
                depth_persisted += 1
            if depth_persisted:
                deepest_persisted = depth
                base_result["depth_counts"][str(depth)] = depth_persisted
            frontier = next_frontier

        base_result["persisted"] = len(base_result["entities"])
        base_result["relation_depth"] = deepest_persisted
        base_result["status"] = (
            "completed" if not base_result["errors"] else "partial"
        )
        return base_result
