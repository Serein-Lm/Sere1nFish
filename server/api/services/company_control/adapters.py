"""公司控股结构供应商适配。"""
from __future__ import annotations

from crawler_tools.tianyancha_tools import TianyanchaClient

from .contracts import ControlDiscovery, ControlledEntity


class TianyanchaControlProvider:
    name = "tianyancha_control_right"

    def __init__(self, client: TianyanchaClient) -> None:
        self.client = client

    @classmethod
    async def create(cls) -> "TianyanchaControlProvider":
        return cls(await TianyanchaClient.from_runtime_config())

    async def discover(
        self,
        company_name: str,
        *,
        max_entities: int,
        page_concurrency: int,
    ) -> ControlDiscovery:
        result = await self.client.list_direct_wholly_controlled(
            company_name,
            max_entities=max_entities,
            page_concurrency=page_concurrency,
        )
        return ControlDiscovery(
            provider=self.name,
            entities=[
                ControlledEntity(
                    name=item.name,
                    provider_id=item.provider_id,
                    aliases=[item.alias] if item.alias else [],
                    ownership_percent=item.ownership_percent,
                    registration_status=item.registration_status,
                    legal_person_name=item.legal_person_name,
                    registered_capital=item.registered_capital,
                    established_at=item.established_at,
                    relation_paths=item.relation_paths,
                )
                for item in result.companies
            ],
            total_reported=result.total_reported,
            pages_fetched=result.pages_fetched,
            truncated=result.truncated,
        )

    async def lookup_icp(self, entity: ControlledEntity) -> ControlledEntity:
        records = await self.client.get_icp_records(entity.provider_id or entity.name)
        entity.icp_records = [record.as_dict() for record in records]
        entity.icp_domains = list(dict.fromkeys(record.domain for record in records if record.domain))
        entity.root_domain = entity.icp_domains[0] if entity.icp_domains else ""
        return entity
