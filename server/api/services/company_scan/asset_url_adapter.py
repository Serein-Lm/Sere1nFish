"""Asset discovery and official-site collection adapter."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from api.services.company_url import normalize_url
from core.logger import get_logger


logger = get_logger("company_scan.asset_url")


def resolve_official_website_roots(
    asset_root_domains: list[str],
    configured_roots: list[str] | None = None,
) -> list[str]:
    """Resolve the official-site boundary independently from domain history."""
    from api.services.website_documents import normalize_website_root_domains

    explicit = normalize_website_root_domains(configured_roots)
    if explicit:
        return explicit
    return normalize_website_root_domains(asset_root_domains)


@dataclass(frozen=True, slots=True)
class AssetUrlCollectionRequest:
    task_id: str
    project_id: str
    identity: dict[str, Any]
    url_text: str
    urls: tuple[str, ...]
    enable_asset_discovery: bool
    enable_url_scan: bool
    enable_copywriting: bool
    min_attention_score: int
    fofa_size: int
    hunter_size: int
    probe_concurrency: int
    incremental_scan: bool
    url_probe_concurrency: int
    url_scan_concurrency: int
    copywriting_concurrency: int
    progress_task_id: str
    progress_source: str
    website_collection_mode: str
    website_root_domains: tuple[str, ...]
    website_required_path_segments: tuple[str, ...]


@dataclass(slots=True)
class AssetDiscoveryState:
    result: dict[str, Any]
    root_domains: list[str]
    discovered_urls: list[str]
    alive_metadata: dict[str, dict[str, Any]]


class AssetUrlCollectionAdapter:
    """Run asset discovery and two independent website consumers."""

    def __init__(self, owner: Any, request: AssetUrlCollectionRequest) -> None:
        self.owner = owner
        self.request = request

    @property
    def db(self) -> Any:
        return self.owner.db

    async def run(self) -> dict[str, Any]:
        discovery = await self._discover_assets()
        url_result = self._initial_result("url_scan")
        document_result = self._initial_result("website_documents")
        if self.request.enable_url_scan:
            operations = await self._build_operations(
                discovery,
                url_result,
                document_result,
            )
            await self._run_operations(operations, url_result, document_result)
        return self._project_result(discovery.result, url_result, document_result)

    def _asset_root_domains(self) -> tuple[list[str], list[str]]:
        from api.services.website_documents import normalize_website_root_domains

        identity = self.request.identity
        verified = normalize_website_root_domains(identity.get("asset_root_domains"))
        roots = self.owner._dedupe_text(
            verified
            or [
                str(identity.get("root_domain") or ""),
                *list(identity.get("root_domains") or []),
            ]
        )[:6]
        return verified, roots

    async def _discover_assets(self) -> AssetDiscoveryState:
        verified, roots = self._asset_root_domains()
        result = self._initial_asset_result()
        if not self.request.enable_asset_discovery:
            return AssetDiscoveryState(result, roots, [], {})

        from api.services.asset_intelligence import (
            AssetIdentity,
            AssetIntelligenceService,
        )

        identity = self.request.identity
        result = await AssetIntelligenceService(
            self.db,
            app_config=self.owner.app_config,
        ).discover(
            identity=AssetIdentity(
                input_name=str(identity.get("input_name") or ""),
                normalized_name=str(identity.get("normalized_name") or ""),
                root_domain=roots[0] if roots else "",
                target_id=str(identity.get("target_id") or ""),
                aliases=list(identity.get("aliases") or []),
                root_domains=roots,
                strict_domain_scope=bool(verified),
            ),
            project_id=self.request.project_id,
            task_id=self.request.task_id,
            provider_sizes={
                "fofa": self.request.fofa_size,
                "hunter": self.request.hunter_size,
            },
            probe_concurrency=self.request.probe_concurrency,
        )
        return await self._resolve_discovered_urls(result, roots)

    async def _resolve_discovered_urls(
        self,
        result: dict[str, Any],
        roots: list[str],
    ) -> AssetDiscoveryState:
        from api.dao import url_scan as url_scan_dao

        recovery_full_scan = bool(
            self.request.incremental_scan
            and await url_scan_dao.task_requires_full_scan(
                self.db,
                task_id=f"{self.request.task_id}_url",
            )
        )
        effective_incremental = self.request.incremental_scan and not recovery_full_scan
        candidate_key = "scan_urls" if effective_incremental else "alive_urls"
        urls = [
            str(value)
            for value in result.get(candidate_key) or []
            if str(value).strip()
        ]
        result.update(
            scan_mode="incremental" if effective_incremental else "full",
            recovery_full_scan=recovery_full_scan,
            scan_candidates=len(urls),
        )
        metadata = dict(result.get("probe_metadata_by_url") or {})
        return AssetDiscoveryState(result, roots, urls, metadata)

    def _initial_asset_result(self) -> dict[str, Any]:
        return {
            "enabled": self.request.enable_asset_discovery,
            "discovered": 0,
            "alive": 0,
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "scan_mode": "incremental" if self.request.incremental_scan else "full",
            "scan_candidates": 0,
            "providers": {},
        }

    def _initial_result(self, kind: str) -> dict[str, Any]:
        base = {
            "enabled": self.request.enable_url_scan,
            "status": "pending" if self.request.enable_url_scan else "disabled",
        }
        if kind == "url_scan":
            base.update(findings_count=0, copywritings_count=0)
        else:
            base.update(documents_archived=0, attachments_archived=0, failed_pages=0)
        return base

    async def _build_operations(
        self,
        discovery: AssetDiscoveryState,
        url_result: dict[str, Any],
        document_result: dict[str, Any],
    ) -> list[tuple[str, Any]]:
        official_roots = resolve_official_website_roots(
            discovery.root_domains,
            list(self.request.website_root_domains),
        )
        root_urls = [normalize_url(domain) for domain in discovery.root_domains if domain]
        official_urls = [normalize_url(domain) for domain in official_roots if domain]
        direct_roots = [] if self.request.website_required_path_segments else root_urls
        merged_urls = self.owner._dedupe_text(
            [*direct_roots, *self.request.urls, *discovery.discovered_urls]
        )
        seed_urls = self.owner._dedupe_text([*self.request.urls, *official_urls])
        run_url_scan = bool(merged_urls or self.request.url_text.strip())
        if not run_url_scan:
            url_result.update(
                status="skipped",
                reason="未发现可供 URL 深扫的存活资产或手工 URL",
            )
        document_operation = None
        if seed_urls and self.request.identity.get("target_id"):
            document_operation = await self._website_documents_operation(
                discovery,
                official_roots,
                seed_urls,
            )
        else:
            document_result.update(
                enabled=False,
                status="skipped",
                reason="目标缺少已核验根域名，官网文档阶段未启动",
            )
        operations: list[tuple[str, Any]] = []
        if run_url_scan:
            operations.append(("url_scan", self._url_scan(discovery, merged_urls)))
        if document_operation is not None:
            operations.append(("website_documents", document_operation))
        return operations

    def _url_scan(
        self,
        discovery: AssetDiscoveryState,
        merged_urls: list[str],
    ) -> Any:
        identity = self.request.identity
        return self.owner._run_url_scan(
            self.request.task_id,
            self.request.project_id,
            self.request.url_text,
            merged_urls,
            self.request.min_attention_score,
            self.request.enable_copywriting,
            target_id=str(identity.get("target_id") or ""),
            known_alive_urls=discovery.discovered_urls,
            known_alive_metadata=discovery.alive_metadata,
            probe_concurrency=self.request.url_probe_concurrency,
            scan_concurrency=self.request.url_scan_concurrency,
            copywriting_concurrency=self.request.copywriting_concurrency,
            progress_task_id=self.request.progress_task_id,
            progress_source=self.request.progress_source,
            target_context={
                "target_id": str(identity.get("target_id") or ""),
                "canonical_name": str(identity.get("normalized_name") or ""),
                "aliases": list(identity.get("aliases") or [])[:12],
                "root_domains": discovery.root_domains,
            },
        )

    async def _website_documents_operation(
        self,
        discovery: AssetDiscoveryState,
        official_roots: list[str],
        seed_urls: list[str],
    ) -> Any:
        from api.services.info_collection.tuning import get_collection_runtime_tuning
        from api.services.website_documents import (
            WebsiteDocumentCollectionService,
            resolve_website_collection_policy,
        )

        tuning = await get_collection_runtime_tuning(self.db)
        identity = self.request.identity
        return WebsiteDocumentCollectionService(
            self.db,
            policy=resolve_website_collection_policy(
                tuning,
                mode=self.request.website_collection_mode,
            ),
        ).run_until_stable(
            parent_task_id=self.request.task_id,
            project_id=self.request.project_id,
            target={
                "target_id": str(identity.get("target_id") or ""),
                "canonical_name": str(identity.get("normalized_name") or ""),
                "aliases": list(identity.get("aliases") or [])[:12],
                "root_domain": official_roots[0] if official_roots else "",
                "root_domains": official_roots,
            },
            seed_urls=seed_urls,
            known_alive_urls=discovery.discovered_urls,
            required_path_segments=list(self.request.website_required_path_segments),
        )

    async def _run_operations(
        self,
        operations: list[tuple[str, Any]],
        url_result: dict[str, Any],
        document_result: dict[str, Any],
    ) -> None:
        if not operations:
            return
        outcomes = await asyncio.gather(
            *(operation for _kind, operation in operations),
            return_exceptions=True,
        )
        for (kind, _operation), outcome in zip(operations, outcomes):
            if isinstance(outcome, BaseException):
                from core.llm_capacity import find_llm_capacity_error

                capacity_error = find_llm_capacity_error(outcome)
                if capacity_error is not None:
                    raise capacity_error
                target = url_result if kind == "url_scan" else document_result
                target.update(enabled=True, status="error", error=str(outcome))
                logger.error(
                    "官网子流水线失败 | task=%s kind=%s error=%s",
                    self.request.task_id,
                    kind,
                    outcome,
                )
            elif kind == "url_scan":
                url_result.update(dict(outcome))
            else:
                document_result.update(dict(outcome))

    @staticmethod
    def _project_result(
        asset_result: dict[str, Any],
        url_result: dict[str, Any],
        document_result: dict[str, Any],
    ) -> dict[str, Any]:
        durable_assets = {
            key: value
            for key, value in asset_result.items()
            if key not in {"alive_urls", "scan_urls", "probe_metadata_by_url"}
        }
        statuses = [
            str(item.get("status") or "pending")
            for item in (url_result, document_result)
            if item.get("enabled") is not False
        ]
        status = (
            "completed"
            if statuses and all(value in {"completed", "skipped"} for value in statuses)
            else "error"
            if statuses and all(value == "error" for value in statuses)
            else "partial"
            if statuses
            else "skipped"
        )
        return {
            "kind": "asset_url",
            "status": status,
            "assets": durable_assets,
            "url_scan": url_result,
            "website_documents": document_result,
        }
