"""公司第一层全资子公司发现、ICP 补全和项目 Target 持久化。"""
from __future__ import annotations

import asyncio
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from crawler_tools.tianyancha_tools import (
    OUTBOUND_INVESTMENT_INTERFACE_ID,
    PERMISSION_DENIED_CODE,
    TianyanchaApiError,
)
from core.logger import get_logger

from .contracts import ControlledEntity
from .factory import CompanyControlProviderFactory

logger = get_logger("company_control")


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
        max_entities: int = 100,
        page_concurrency: int = 4,
        icp_concurrency: int = 6,
    ) -> dict[str, Any]:
        parent_target_id = str(parent_target.get("target_id") or "")
        parent_target_name = str(parent_target.get("canonical_name") or company_name).strip()
        base_result: dict[str, Any] = {
            "enabled": True,
            "status": "running",
            "provider": "tianyancha_outbound_investment",
            "relation_type": "wholly_owned_direct_investment",
            "relation_depth": 1,
            "ownership_percent": 100.0,
            "total_reported": 0,
            "matched": 0,
            "persisted": 0,
            "pages_fetched": 0,
            "truncated": False,
            "entities": [],
            "errors": [],
            "permission_required": False,
        }
        try:
            provider = await CompanyControlProviderFactory.create("tianyancha")
            discovery = await provider.discover(
                company_name,
                max_entities=max(1, max_entities),
                page_concurrency=max(1, page_concurrency),
            )
        except TianyanchaApiError as exc:
            base_result.update(
                {
                    "status": "unavailable",
                    "error_code": exc.code,
                    "errors": [exc.reason],
                    "permission_required": exc.code == PERMISSION_DENIED_CODE,
                }
            )
            logger.warning("全资子公司发现不可用 company=%s code=%s reason=%s", company_name, exc.code, exc.reason)
            return base_result
        except Exception as exc:  # noqa: BLE001
            base_result.update({"status": "error", "errors": [str(exc)]})
            logger.exception("全资子公司发现异常 company=%s", company_name)
            return base_result

        base_result.update(
            {
                "provider": discovery.provider,
                "total_reported": discovery.total_reported,
                "matched": len(discovery.entities),
                "pages_fetched": discovery.pages_fetched,
                "truncated": discovery.truncated,
            }
        )
        semaphore = asyncio.Semaphore(max(1, icp_concurrency))

        async def _enrich(entity: ControlledEntity) -> tuple[ControlledEntity, str]:
            async with semaphore:
                try:
                    return await provider.lookup_icp(entity), ""
                except TianyanchaApiError as exc:
                    logger.warning("全资子公司 ICP 查询失败 company=%s code=%s", entity.name, exc.code)
                    return entity, f"{entity.name}: ICP 查询失败({exc.code}) {exc.reason}"
                except Exception as exc:  # noqa: BLE001
                    logger.warning("全资子公司 ICP 查询异常 company=%s: %s", entity.name, exc)
                    return entity, f"{entity.name}: ICP 查询异常 {exc}"

        enriched = await asyncio.gather(*[_enrich(entity) for entity in discovery.entities])
        from api.dao import company_meta as company_meta_dao
        from api.dao import targets as targets_dao
        from api.services.search_terms import build_target_channel_terms

        async def _persist(entity: ControlledEntity, icp_error: str) -> dict[str, Any]:
            aliases = list(dict.fromkeys([entity.name, *entity.aliases]))
            target = await targets_dao.upsert_target(
                self.db,
                name=entity.name,
                root_domain=entity.root_domain,
                aliases=aliases,
                source=discovery.provider,
            )
            relation = {
                "parent_target_id": parent_target_id,
                "parent_target_name": parent_target_name,
                "relation_type": "wholly_owned_direct_investment",
                "relation_depth": 1,
                "ownership_percent": 100.0,
                "relation_source": discovery.provider,
                "provider_company_id": entity.provider_id,
                "registration_status": entity.registration_status,
                "relation_paths": entity.relation_paths,
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
                source=f"{discovery.provider}_icp",
                task_id=task_id,
                target_id=str(target.get("target_id") or ""),
                icp_domains=entity.icp_domains,
                relation=relation,
                provenance={
                    "investment_provider": discovery.provider,
                    "investment_interface_id": OUTBOUND_INVESTMENT_INTERFACE_ID,
                    "domain_provider": "tianyancha_icp",
                    "domain_interface_id": 1038,
                },
            )
            return {
                "target_id": target.get("target_id") or "",
                "project_target_id": project_target.get("project_target_id") or "",
                "name": entity.name,
                "aliases": aliases,
                "root_domain": entity.root_domain,
                "icp_domains": entity.icp_domains,
                "icp_records": entity.icp_records,
                "ownership_percent": 100.0,
                "relation_depth": 1,
                "registration_status": entity.registration_status,
                "provider_company_id": entity.provider_id,
                "icp_error": icp_error or None,
            }

        persisted = await asyncio.gather(
            *[_persist(entity, icp_error) for entity, icp_error in enriched],
            return_exceptions=True,
        )
        for item in persisted:
            if isinstance(item, Exception):
                base_result["errors"].append(str(item))
            else:
                base_result["entities"].append(item)
        base_result["persisted"] = len(base_result["entities"])
        base_result["status"] = "completed" if not base_result["errors"] else "partial"
        return base_result
