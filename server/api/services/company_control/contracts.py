"""公司控股关联单位领域协议。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol


MIN_CONTROL_OWNERSHIP_PERCENT = 50.0
MAX_CONTROL_OWNERSHIP_PERCENT = 100.0


def normalize_control_ownership_threshold(value: Any = 100.0) -> float:
    try:
        threshold = float(value if value is not None else 100.0)
    except (TypeError, ValueError) as exc:
        raise ValueError("控股持股比例阈值必须为 50 到 100") from exc
    if not math.isfinite(threshold) or not (
        MIN_CONTROL_OWNERSHIP_PERCENT
        <= threshold
        <= MAX_CONTROL_OWNERSHIP_PERCENT
    ):
        raise ValueError("控股持股比例阈值必须为 50 到 100")
    return threshold


def investment_relation_type(ownership_percent: float) -> str:
    if math.isclose(float(ownership_percent), 100.0, rel_tol=0, abs_tol=0.0001):
        return "wholly_owned_direct_investment"
    return "controlled_direct_investment"


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
        min_ownership_percent: float,
        max_entities: int,
        page_concurrency: int,
    ) -> ControlDiscovery: ...

    async def lookup_icp(self, entity: ControlledEntity) -> ControlledEntity: ...
