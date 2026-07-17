"""统一资产发现编排：并发查询、跨源去重、探活、增量持久化。"""

from __future__ import annotations

import asyncio
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import fofa_assets as assets_dao
from core.logger import get_logger
from core.observability import obs_log

from .adapters import HttpAssetProbe
from .contracts import AssetCandidate, AssetIdentity, ProviderSearchResult
from .factory import AssetProviderFactory
from .triage import AssetTriageService

logger = get_logger("asset_intelligence")


class AssetIntelligenceService:
    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        *,
        app_config: Any | None = None,
        probe: HttpAssetProbe | None = None,
        triage: AssetTriageService | None = None,
    ) -> None:
        self.db = db
        self.probe = probe or HttpAssetProbe()
        self.triage = triage or (AssetTriageService(app_config) if app_config else None)

    async def discover(
        self,
        *,
        identity: AssetIdentity,
        project_id: str,
        task_id: str,
        provider_sizes: dict[str, int] | None = None,
        provider_names: tuple[str, ...] | None = None,
        probe_concurrency: int = 48,
        probe_timeout: float = 8.0,
    ) -> dict[str, Any]:
        names = provider_names or AssetProviderFactory.available()
        sizes = provider_sizes or {}
        obs_log(
            "外部资产发现开始",
            task_id=task_id,
            project_id=project_id,
            source="asset_intelligence",
            event="pipeline_start",
            data={"target_id": identity.target_id, "providers": list(names)},
        )
        searches = await asyncio.gather(
            *[
                self._search_provider(name, identity, max(1, int(sizes.get(name, 200))))
                for name in names
            ]
        )
        merged = self._merge_candidates(searches)
        urls = list(dict.fromkeys(item.canonical_url for item in merged if item.canonical_url))
        probe_by_url = await self.probe.probe(
            urls,
            concurrency=max(1, min(probe_concurrency, 128)),
            timeout=max(1.0, min(probe_timeout, 30.0)),
        ) if urls else {}
        for candidate in merged:
            probe = probe_by_url.get(candidate.canonical_url)
            if probe is not None:
                candidate.probe = probe
                candidate.is_alive = bool(probe.get("is_alive"))
                if not candidate.title and probe.get("title"):
                    candidate.title = str(probe["title"])

        alive_candidates = [candidate for candidate in merged if candidate.is_alive]
        if alive_candidates and self.triage:
            try:
                prioritized_alive = await self.triage.prioritize(
                    alive_candidates,
                    identity=identity,
                    project_id=project_id,
                    task_id=task_id,
                )
                kept_alive_ids = {id(candidate) for candidate in prioritized_alive}
                merged = [
                    candidate
                    for candidate in merged
                    if not candidate.is_alive or id(candidate) in kept_alive_ids
                ]
                alive_rank = {
                    id(candidate): index for index, candidate in enumerate(prioritized_alive)
                }
                merged.sort(
                    key=lambda candidate: (
                        0 if candidate.is_alive else 1,
                        alive_rank.get(id(candidate), len(alive_rank)),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("存活资产 LLM 分诊不可用，保留原始资产顺序: %s", exc)

        docs = [item.as_dict(target_id=identity.target_id) for item in merged]
        persisted = await assets_dao.upsert_assets_batch(
            self.db,
            project_id=project_id,
            root_domain=identity.root_domain,
            source_query=identity.root_domain or identity.normalized_name,
            search_type="multi_provider",
            assets=docs,
            task_id=task_id,
            target_id=identity.target_id,
        )
        changed_ids = set(persisted.get("changed_asset_ids") or [])
        scan_urls: list[str] = []
        alive_urls: list[str] = []
        for doc in docs:
            url = str(doc.get("canonical_url") or "")
            if not url or not doc.get("is_alive"):
                continue
            alive_urls.append(url)
            asset_id = assets_dao.fofa_asset_id(
                project_id,
                str(doc.get("host") or ""),
                str(doc.get("ip") or ""),
                str(doc.get("port") or ""),
            )
            if asset_id in changed_ids:
                scan_urls.append(url)

        provider_summary = {
            item.provider: {
                "count": len(item.candidates),
                "queries": item.queries,
                "errors": item.errors,
            }
            for item in searches
        }
        result = {
            **persisted,
            "providers": provider_summary,
            "discovered": len(merged),
            "alive": len(alive_urls),
            "alive_urls": list(dict.fromkeys(alive_urls)),
            "scan_urls": list(dict.fromkeys(scan_urls)),
            "target_id": identity.target_id,
            "root_domain": identity.root_domain,
        }
        obs_log(
            "外部资产发现完成",
            task_id=task_id,
            project_id=project_id,
            source="asset_intelligence",
            event="pipeline_done",
            data={
                "target_id": identity.target_id,
                "discovered": result["discovered"],
                "alive": result["alive"],
                "changed": len(changed_ids),
            },
        )
        return result

    @staticmethod
    async def _search_provider(
        name: str,
        identity: AssetIdentity,
        size: int,
    ) -> ProviderSearchResult:
        try:
            return await AssetProviderFactory.create(name).search(identity, size=size)
        except Exception as exc:  # noqa: BLE001
            logger.exception("资产 Provider 查询失败: %s", name)
            return ProviderSearchResult(provider=name, errors=[str(exc)])

    @staticmethod
    def _merge_candidates(results: list[ProviderSearchResult]) -> list[AssetCandidate]:
        merged: dict[str, AssetCandidate] = {}
        for result in results:
            for candidate in result.candidates:
                key = candidate.endpoint_key
                if not key:
                    continue
                existing = merged.get(key)
                if existing is None:
                    merged[key] = candidate
                else:
                    existing.merge(candidate)
        return list(merged.values())
