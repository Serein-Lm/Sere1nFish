from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from crawler_tools.tianyancha_tools import (
    BIDDING_PATH,
    BIDDING_TYPES,
    OUTBOUND_INVESTMENT_INTERFACE_ID,
    OUTBOUND_INVESTMENT_PATH,
    PERMISSION_DENIED_CODE,
    TianyanchaApiError,
    TianyanchaClient,
    parse_bidding_records,
    parse_direct_wholly_owned_investments,
    parse_icp_records,
    parse_percent,
)


def test_percent_parser_accepts_supplier_variants_but_keeps_exact_value() -> None:
    assert parse_percent("100%") == 100
    assert parse_percent("1") == 100
    assert parse_percent(100) == 100
    assert parse_percent("99.9%") != 100


def test_investment_parser_keeps_only_exact_wholly_owned_company() -> None:
    root = "根公司"
    items = [
        {
            "name": "直属全资公司",
            "id": 2,
            "percent": "100%",
            "regStatus": "存续",
        },
        {
            "name": "已注销全资公司",
            "id": 5,
            "percent": "100%",
            "regStatus": "注销",
        },
        {
            "name": root,
            "id": 3,
            "percent": "100%",
        },
        {
            "name": "直属非全资公司",
            "id": 4,
            "percent": "99.9%",
        },
    ]

    parsed = parse_direct_wholly_owned_investments(items, root_name=root)

    assert [item.name for item in parsed] == ["直属全资公司"]
    assert parsed[0].provider_id == "2"
    assert parsed[0].ownership_percent == 100.0
    assert [node["value"] for node in parsed[0].relation_paths[0]] == [
        root,
        "100%",
        "直属全资公司",
    ]


@pytest.mark.asyncio
async def test_investment_query_keeps_paging_until_wholly_owned_match_is_found() -> None:
    root = "根公司"

    class _Client(TianyanchaClient):
        def __init__(self) -> None:
            super().__init__("test-key")

        async def _request(self, _endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            page = int(params["pageNum"])
            items_by_page = {
                1: [
                    {
                        "name": "非全资公司A",
                        "id": 2,
                        "percent": "80%",
                    }
                ],
                2: [
                    {
                        "name": "非全资公司",
                        "id": 3,
                        "percent": "99%",
                    }
                ],
                3: [
                    {
                        "name": "直属全资公司",
                        "id": 4,
                        "percent": "100%",
                    }
                ],
            }
            return {
                "error_code": 0,
                "result": {"total": 60, "items": items_by_page[page]},
            }

    result = await _Client().list_direct_wholly_owned_investments(
        root,
        max_entities=1,
        page_concurrency=2,
    )

    assert [company.name for company in result.companies] == ["直属全资公司"]
    assert result.pages_fetched == 3
    assert result.truncated is False


def test_icp_parser_uses_official_ym_and_website_fields() -> None:
    records = parse_icp_records(
        [
            {
                "ym": "Example.COM",
                "webSite": ["https://www.example.com/index.html"],
                "webName": "示例官网",
                "liscense": "京ICP备案号",
                "companyName": "示例公司",
            }
        ]
    )

    assert len(records) == 1
    assert records[0].domain == "example.com"
    assert records[0].websites == ["example.com"]
    assert records[0].license_no == "京ICP备案号"


def test_bidding_parser_maps_supplier_fields_and_builds_stable_id() -> None:
    payload = {
        "id": 123,
        "uuid": "bid-uuid",
        "title": "采购结果公告",
        "type": "中标公告",
        "stage": "结果",
        "publishTime": "1784044800000",
        "purchaser": "示例公司",
        "proxy": "示例代理",
        "link": "https://example.com/bids/123",
        "bidList": [{"name": "供应商 A"}],
        "content": "<p>公告正文</p>",
    }

    first = parse_bidding_records([payload], bid_type="4")[0]
    second = parse_bidding_records([dict(payload)], bid_type="4")[0]

    assert first.record_id == second.record_id
    assert first.record_id.startswith("bid_")
    assert first.provider_record_id == "123"
    assert first.announcement_type == "中标公告"
    assert first.agency == "示例代理"
    assert first.published_on == "2026-07-15"
    assert first.content_html == "<p>公告正文</p>"
    assert first.raw_payload["bidList"] == [{"name": "供应商 A"}]
    assert first.bid_type_codes == ["4"]
    assert first.procurement_id.startswith("proc_")
    assert first.procurement_title == "采购"


def test_bidding_parser_normalizes_supplier_collection_fields() -> None:
    record = parse_bidding_records(
        [
            {
                "uuid": "collection-fields",
                "proxy": "[[]]",
                "purchaser": [{"name": "采购单位 A"}, {"name": "采购单位 B"}],
                "bidWinner": '[{"name":"中标单位"}]',
            }
        ]
    )[0]

    assert record.agency == ""
    assert record.purchaser == "采购单位 A、采购单位 B"
    assert record.winner == "中标单位"


@pytest.mark.asyncio
async def test_bidding_query_uses_legal_name_date_window_and_supplier_limit() -> None:
    captured: dict[str, Any] = {}

    class _Client(TianyanchaClient):
        def __init__(self) -> None:
            super().__init__("test-key")

        async def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            captured.update(endpoint=endpoint, params=params)
            return {
                "error_code": 0,
                "result": {
                    "total": 1,
                    "items": [{"uuid": "one", "title": "公告"}],
                },
            }

    result = await _Client().search_bids(
        "安徽广播电视台",
        page_size=100,
        lookback_days=180,
        end_date=date(2026, 7, 17),
    )

    assert captured["endpoint"] == BIDDING_PATH
    assert captured["params"] == {
        "keyword": "安徽广播电视台",
        "type": "2",
        "publishStartTime": "2026-01-18",
        "publishEndTime": "2026-07-17",
        "pageNum": 1,
        "pageSize": 20,
    }
    assert result.total_reported == 1
    assert result.page_size == 20


@pytest.mark.asyncio
async def test_bidding_query_reads_all_pages_with_bounded_concurrency() -> None:
    requested_pages: list[int] = []

    class _Client(TianyanchaClient):
        def __init__(self) -> None:
            super().__init__("test-key")

        async def _request(self, _endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            page = int(params["pageNum"])
            requested_pages.append(page)
            start = (page - 1) * 20
            items = [
                {"uuid": f"bid-{index}", "title": f"公告 {index}"}
                for index in range(start, min(start + 20, 45))
            ]
            return {"error_code": 0, "result": {"total": 45, "items": items}}

    result = await _Client().search_all_bids(
        "安徽广播电视台",
        page_size=20,
        max_records=100,
        page_concurrency=2,
        end_date=date(2026, 7, 17),
    )

    assert requested_pages == [1, 2, 3]
    assert result.total_reported == 45
    assert len(result.records) == 45
    assert result.pages_fetched == 3
    assert result.raw_records_fetched == 45
    assert result.duplicates_discarded == 0
    assert result.truncated is False
    assert result.coverage_expected == 45
    assert result.coverage_gap == 0
    assert result.coverage_complete is True
    assert result.retry_passes == 0


@pytest.mark.asyncio
async def test_bidding_query_retries_and_reports_persistent_cross_page_gap() -> None:
    class _Client(TianyanchaClient):
        def __init__(self) -> None:
            super().__init__("test-key")

        async def _request(self, _endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            page = int(params["pageNum"])
            start = (page - 1) * 20
            indexes = list(range(start, min(start + 20, 41)))
            if page == 3:
                indexes = [39, 40]
            return {
                "error_code": 0,
                "result": {
                    "total": 42,
                    "items": [
                        {"uuid": f"bid-{index}", "title": f"公告 {index}"}
                        for index in indexes
                    ],
                },
            }

    result = await _Client().search_all_bids(
        "安徽广播电视台",
        page_size=20,
        max_records=100,
        end_date=date(2026, 7, 17),
    )

    assert result.pages_fetched == 9
    assert result.raw_records_fetched == 126
    assert len(result.records) == 41
    assert result.duplicates_discarded == 85
    assert result.truncated is True
    assert result.coverage_expected == 42
    assert result.coverage_gap == 1
    assert result.coverage_complete is False
    assert result.retry_passes == 2


@pytest.mark.asyncio
async def test_recent_bidding_query_collects_forecast_notice_and_award_serially() -> None:
    requested_types: list[str] = []

    class _Client(TianyanchaClient):
        def __init__(self) -> None:
            super().__init__("test-key")

        async def _request(self, _endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            bid_type = str(params["type"])
            requested_types.append(bid_type)
            payloads = {
                "1": [{"uuid": "forecast", "title": "设备采购意向", "purchaser": "目标单位"}],
                "2": [{"uuid": "shared", "title": "设备采购公告", "purchaser": "目标单位"}],
                "4": [
                    {"uuid": "shared", "title": "设备采购公告", "purchaser": "目标单位"},
                    {"uuid": "award", "title": "设备采购中标结果公告", "purchaser": "目标单位"},
                ],
            }
            return {
                "error_code": 0,
                "result": {"total": len(payloads[bid_type]), "items": payloads[bid_type]},
            }

    result = await _Client().search_all_bid_types(
        "目标单位",
        end_date=date(2026, 7, 17),
    )

    assert requested_types == list(BIDDING_TYPES)
    assert result.bid_types == ["1", "2", "4"]
    assert result.total_reported == 4
    assert result.raw_records_fetched == 4
    assert len(result.records) == 3
    assert result.duplicates_discarded == 1
    shared = next(item for item in result.records if item.provider_uuid == "shared")
    assert shared.bid_type_codes == ["2", "4"]
    assert result.type_stats["4"]["records_fetched"] == 2
    assert result.type_stats["4"]["coverage_complete"] is True
    assert result.coverage_expected == 4
    assert result.coverage_gap == 0
    assert result.coverage_complete is True
    assert result.publish_start == "2026-01-18"


def test_keyword_skill_ignores_standalone_company_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import search_terms

    monkeypatch.setattr(
        search_terms,
        "load_keyword_skill",
        lambda _channel: (
            "wechat-keywords",
            "将 `{company}` 替换为目标名称，再使用 `{company} 招标`。",
        ),
    )

    assert search_terms.get_keyword_templates("weixin") == ["{company} 招标"]


def test_channel_terms_interleave_aliases_before_applying_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import search_terms

    monkeypatch.setattr(
        search_terms,
        "get_keyword_templates",
        lambda _channel: ["{company} 实习", "{company} 招聘"],
    )

    assert search_terms.build_channel_terms(
        channel="xhs",
        names=["法定名", "品牌名"],
        routed_terms=["动态行业词"],
        limit=4,
    ) == ["法定名 实习", "品牌名 实习", "法定名 招聘", "动态行业词"]


@pytest.mark.asyncio
async def test_provider_uses_outbound_investment_endpoint() -> None:
    from api.services.company_control.adapters import TianyanchaInvestmentProvider

    class _Client:
        async def list_direct_wholly_owned_investments(
            self,
            *_args: Any,
            **_kwargs: Any,
        ) -> Any:
            raise TianyanchaApiError(
                code=PERMISSION_DENIED_CODE,
                reason="无权限访问此api",
                endpoint=OUTBOUND_INVESTMENT_PATH,
            )

    with pytest.raises(TianyanchaApiError) as raised:
        await TianyanchaInvestmentProvider(_Client()).discover(
            "根公司",
            max_entities=10,
            page_concurrency=2,
        )
    assert raised.value.code == PERMISSION_DENIED_CODE
    assert raised.value.endpoint == OUTBOUND_INVESTMENT_PATH


@pytest.mark.asyncio
async def test_subsidiary_service_marks_missing_interface_permission_without_failing_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services.company_control.factory import CompanyControlProviderFactory
    from api.services.company_control.service import CompanyControlService

    class _DeniedProvider:
        name = "tianyancha_outbound_investment"

        async def discover(self, *_args: Any, **_kwargs: Any) -> Any:
            raise TianyanchaApiError(
                code=PERMISSION_DENIED_CODE,
                reason="无权限访问此api",
                endpoint=OUTBOUND_INVESTMENT_PATH,
            )

    async def _create(_provider: str = "tianyancha") -> Any:
        return _DeniedProvider()

    monkeypatch.setattr(CompanyControlProviderFactory, "create", _create)
    result = await CompanyControlService(object()).discover_and_persist(
        project_id="project-1",
        task_id="task-1",
        parent_target={"target_id": "root", "canonical_name": "根公司"},
        company_name="根公司",
    )

    assert result["status"] == "unavailable"
    assert result["permission_required"] is True
    assert result["error_code"] == PERMISSION_DENIED_CODE
    assert result["entities"] == []
    assert result["provider"] == "tianyancha_outbound_investment"
    assert result["relation_type"] == "wholly_owned_direct_investment"


@pytest.mark.asyncio
async def test_subsidiary_service_persists_outbound_investment_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import company_meta as company_meta_dao
    from api.dao import targets as targets_dao
    from api.services.company_control.contracts import ControlDiscovery, ControlledEntity
    from api.services.company_control.factory import CompanyControlProviderFactory
    from api.services.company_control.service import CompanyControlService

    class _Provider:
        name = "tianyancha_outbound_investment"

        async def discover(self, *_args: Any, **_kwargs: Any) -> ControlDiscovery:
            return ControlDiscovery(
                provider=self.name,
                entities=[
                    ControlledEntity(
                        name="全资子公司",
                        provider_id="company-2",
                        ownership_percent=100.0,
                    )
                ],
                total_reported=2,
                pages_fetched=1,
            )

        async def lookup_icp(self, entity: ControlledEntity) -> ControlledEntity:
            entity.root_domain = "child.example.com"
            entity.icp_domains = [entity.root_domain]
            return entity

    async def _create(_provider: str = "tianyancha") -> _Provider:
        return _Provider()

    captured: dict[str, Any] = {}

    async def _upsert_target(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "target_id": "child",
            "canonical_name": "全资子公司",
            "root_domain": "child.example.com",
        }

    async def _link_target(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        captured["relation"] = kwargs["relation"]
        return {"project_target_id": "project-child"}

    async def _upsert_meta(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        captured["provenance"] = kwargs["provenance"]
        return {}

    monkeypatch.setattr(CompanyControlProviderFactory, "create", _create)
    monkeypatch.setattr(targets_dao, "upsert_target", _upsert_target)
    monkeypatch.setattr(targets_dao, "link_project_target", _link_target)
    monkeypatch.setattr(company_meta_dao, "upsert_company_meta", _upsert_meta)

    result = await CompanyControlService(object()).discover_and_persist(
        project_id="project-1",
        task_id="task-1",
        parent_target={"target_id": "root", "canonical_name": "根公司"},
        company_name="根公司",
    )

    assert result["status"] == "completed"
    assert result["persisted"] == 1
    assert captured["relation"]["relation_type"] == "wholly_owned_direct_investment"
    assert captured["relation"]["relation_source"] == "tianyancha_outbound_investment"
    assert captured["provenance"]["investment_interface_id"] == OUTBOUND_INVESTMENT_INTERFACE_ID
    assert "control_interface_id" not in captured["provenance"]


@pytest.mark.asyncio
async def test_subsidiary_service_persists_child_and_grandchild_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import company_meta as company_meta_dao
    from api.dao import targets as targets_dao
    from api.services.company_control.contracts import ControlDiscovery, ControlledEntity
    from api.services.company_control.factory import CompanyControlProviderFactory
    from api.services.company_control.service import CompanyControlService

    class _Provider:
        name = "tianyancha_outbound_investment"

        def __init__(self) -> None:
            self.calls: list[tuple[str, int]] = []

        async def discover(
            self,
            company_name: str,
            *,
            max_entities: int,
            page_concurrency: int,
        ) -> ControlDiscovery:
            self.calls.append((company_name, page_concurrency))
            names = {
                "根公司": [("直属子单位", "provider-child")],
                "直属子单位": [("孙单位", "provider-grandchild")],
            }.get(company_name, [])
            return ControlDiscovery(
                provider=self.name,
                entities=[
                    ControlledEntity(name=name, provider_id=provider_id)
                    for name, provider_id in names[:max_entities]
                ],
                total_reported=len(names),
                pages_fetched=1,
            )

        async def lookup_icp(self, entity: ControlledEntity) -> ControlledEntity:
            return entity

    provider = _Provider()

    async def _create(_provider: str = "tianyancha") -> _Provider:
        return provider

    target_ids = {"直属子单位": "child", "孙单位": "grandchild"}
    relations: dict[str, dict[str, Any]] = {}
    target_upserts: list[dict[str, Any]] = []

    async def _upsert_target(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        target_upserts.append(dict(kwargs))
        name = str(kwargs["name"])
        return {"target_id": target_ids[name], "canonical_name": name}

    async def _link_target(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        target_id = str(kwargs["target"]["target_id"])
        relations[target_id] = dict(kwargs["relation"])
        return {"project_target_id": f"project-{target_id}"}

    async def _upsert_meta(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(CompanyControlProviderFactory, "create", _create)
    monkeypatch.setattr(targets_dao, "upsert_target", _upsert_target)
    monkeypatch.setattr(targets_dao, "link_project_target", _link_target)
    monkeypatch.setattr(company_meta_dao, "upsert_company_meta", _upsert_meta)

    result = await CompanyControlService(object()).discover_and_persist(
        project_id="project-1",
        task_id="task-1",
        parent_target={"target_id": "root", "canonical_name": "根公司"},
        company_name="根公司",
        max_depth=2,
        page_concurrency=4,
    )

    assert result["status"] == "completed"
    assert result["persisted"] == 2
    assert result["relation_depth"] == 2
    assert result["depth_counts"] == {"1": 1, "2": 1}
    assert provider.calls == [("根公司", 4), ("直属子单位", 1)]
    assert relations["child"]["parent_target_id"] == "root"
    assert relations["child"]["lineage_target_ids"] == ["root", "child"]
    assert relations["grandchild"]["root_target_id"] == "root"
    assert relations["grandchild"]["parent_target_id"] == "child"
    assert relations["grandchild"]["relation_depth"] == 2
    assert relations["grandchild"]["lineage_target_ids"] == [
        "root",
        "child",
        "grandchild",
    ]
    assert all(item.get("match_aliases") is False for item in target_upserts)


@pytest.mark.asyncio
async def test_subsidiary_service_skips_repeated_legal_name_cycles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao
    from api.services.company_control.contracts import ControlDiscovery, ControlledEntity
    from api.services.company_control.factory import CompanyControlProviderFactory
    from api.services.company_control.service import CompanyControlService

    class _Provider:
        name = "tianyancha_outbound_investment"

        async def discover(self, *_args: Any, **_kwargs: Any) -> ControlDiscovery:
            return ControlDiscovery(
                provider=self.name,
                entities=[
                    ControlledEntity(
                        name="根公司",
                        provider_id="cycle-provider",
                        root_domain="root.example",
                    )
                ],
                total_reported=1,
                pages_fetched=1,
            )

        async def lookup_icp(self, entity: ControlledEntity) -> ControlledEntity:
            return entity

    async def _create(_provider: str = "tianyancha") -> _Provider:
        return _Provider()

    async def _unexpected(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("循环 Target 不应继续写入")

    monkeypatch.setattr(CompanyControlProviderFactory, "create", _create)
    monkeypatch.setattr(targets_dao, "upsert_target", _unexpected)
    monkeypatch.setattr(targets_dao, "link_project_target", _unexpected)

    result = await CompanyControlService(object()).discover_and_persist(
        project_id="project-1",
        task_id="task-1",
        parent_target={"target_id": "root", "canonical_name": "根公司"},
        company_name="根公司",
    )

    assert result["status"] == "completed"
    assert result["persisted"] == 0
    assert result["cycles_skipped"] == 1
    assert result["entities"] == []


@pytest.mark.asyncio
async def test_project_terms_merge_root_and_direct_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao
    from api.services.search_terms import resolve_project_target_terms

    async def _root(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "target_id": "root",
            "target_name": "根公司",
            "search_terms_by_channel": {"weixin": ["根公司 招标"]},
        }

    async def _children(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "target_id": "child",
                "target_name": "全资子公司",
                "parent_target_id": "root",
                "search_terms_by_channel": {"weixin": ["全资子公司 采购"]},
            }
        ]

    monkeypatch.setattr(targets_dao, "get_project_target", _root)
    monkeypatch.setattr(targets_dao, "list_project_target_descendants", _children)
    result = await resolve_project_target_terms(
        object(),
        project_id="project-1",
        target_id="root",
        target_name="根公司",
        channel="weixin",
        explicit_keywords=["根公司 公告"],
    )

    assert result.keywords == ["根公司 公告", "根公司 招标", "全资子公司 采购"]
    assert result.target_ids == ["root", "child"]
    assert result.sources == ["task_explicit", "project_target", "project_target_child"]
    assert result.keyword_targets == {
        "根公司 公告": {"target_id": "root", "target_name": "根公司"},
        "根公司 招标": {"target_id": "root", "target_name": "根公司"},
        "全资子公司 采购": {"target_id": "child", "target_name": "全资子公司"},
    }


@pytest.mark.asyncio
async def test_project_terms_include_grandchild_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao
    from api.services.search_terms import resolve_project_target_terms

    async def _root(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "target_id": "root",
            "target_name": "根公司",
            "search_terms_by_channel": {"weixin": ["根公司 公告"]},
        }

    async def _descendants(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "target_id": "child",
                "target_name": "直属子单位",
                "parent_target_id": "root",
                "relation_depth": 1,
                "search_terms_by_channel": {"weixin": ["直属子单位 采购"]},
            },
            {
                "target_id": "grandchild",
                "target_name": "孙单位",
                "parent_target_id": "child",
                "relation_depth": 2,
                "search_terms_by_channel": {"weixin": ["孙单位 招标"]},
            },
        ]

    monkeypatch.setattr(targets_dao, "get_project_target", _root)
    monkeypatch.setattr(
        targets_dao,
        "list_project_target_descendants",
        _descendants,
    )
    result = await resolve_project_target_terms(
        object(),
        project_id="project-1",
        target_id="root",
        target_name="根公司",
        channel="weixin",
    )

    assert result.target_ids == ["root", "child", "grandchild"]
    assert result.keywords == ["根公司 公告", "直属子单位 采购", "孙单位 招标"]
    assert result.sources == [
        "project_target",
        "project_target_child",
        "project_target_grandchild",
    ]
    assert result.keyword_targets["孙单位 招标"] == {
        "target_id": "grandchild",
        "target_name": "孙单位",
    }


@pytest.mark.asyncio
async def test_project_terms_round_robin_children_before_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao
    from api.services.search_terms import resolve_project_target_terms

    async def _root(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "target_id": "root",
            "target_name": "根公司",
            "search_terms_by_channel": {"weixin": ["根1", "根2", "根3"]},
        }

    async def _children(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "target_id": "child-a",
                "target_name": "子公司A",
                "parent_target_id": "root",
                "search_terms_by_channel": {"weixin": ["子A1", "子A2"]},
            },
            {
                "target_id": "child-b",
                "target_name": "子公司B",
                "parent_target_id": "root",
                "search_terms_by_channel": {"weixin": ["子B1", "子B2"]},
            },
        ]

    monkeypatch.setattr(targets_dao, "get_project_target", _root)
    monkeypatch.setattr(targets_dao, "list_project_target_descendants", _children)
    result = await resolve_project_target_terms(
        object(),
        project_id="project-1",
        target_id="root",
        target_name="根公司",
        channel="weixin",
        max_keywords=3,
    )

    assert result.keywords == ["根1", "子A1", "子B1"]
    assert [result.keyword_targets[item]["target_id"] for item in result.keywords] == [
        "root",
        "child-a",
        "child-b",
    ]
