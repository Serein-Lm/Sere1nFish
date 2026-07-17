from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from crawler_tools.tianyancha_tools import (
    BIDDING_PATH,
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

    first = parse_bidding_records([payload])[0]
    second = parse_bidding_records([dict(payload)])[0]

    assert first.record_id == second.record_id
    assert first.record_id.startswith("bid_")
    assert first.provider_record_id == "123"
    assert first.announcement_type == "中标公告"
    assert first.agency == "示例代理"
    assert first.published_on == "2026-07-15"
    assert first.content_html == "<p>公告正文</p>"
    assert first.raw_payload["bidList"] == [{"name": "供应商 A"}]


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
    monkeypatch.setattr(targets_dao, "list_project_target_children", _children)
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
    monkeypatch.setattr(targets_dao, "list_project_target_children", _children)
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
