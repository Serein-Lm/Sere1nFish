"""公司控股结构领域协议。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ControlledEntity:
    name: str
    provider_id: str = ""
    aliases: list[str] = field(default_factory=list)
    ownership_percent: float = 100.0
    registration_status: str = ""
    legal_person_name: str = ""
    registered_capital: str = ""
    established_at: int | None = None
    relation_paths: list[list[dict[str, Any]]] = field(default_factory=list)
    root_domain: str = ""
    icp_domains: list[str] = field(default_factory=list)
    icp_records: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ControlDiscovery:
    provider: str
    entities: list[ControlledEntity] = field(default_factory=list)
    total_reported: int = 0
    pages_fetched: int = 0
    truncated: bool = False


class CompanyControlProvider(Protocol):
    name: str

    async def discover(
        self,
        company_name: str,
        *,
        max_entities: int,
        page_concurrency: int,
    ) -> ControlDiscovery: ...

    async def lookup_icp(self, entity: ControlledEntity) -> ControlledEntity: ...
