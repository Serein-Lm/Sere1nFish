from __future__ import annotations

from typing import Any

import pytest

from crawler_tools.tianyancha_tools import (
    CONTROL_RIGHT_PATH,
    PERMISSION_DENIED_CODE,
    TianyanchaApiError,
    TianyanchaClient,
    parse_direct_wholly_controlled_items,
    parse_icp_records,
    parse_percent,
)


def _path(*companies: str, percent: str = "100%") -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = [{"type": "percent", "value": percent}]
    for index, company in enumerate(companies):
        nodes.append({"type": "company", "value": company, "cid": index + 1})
        if index < len(companies) - 1:
            nodes.append({"type": "percent", "value": percent})
    return nodes


def test_percent_parser_accepts_supplier_variants_but_keeps_exact_value() -> None:
    assert parse_percent("100%") == 100
    assert parse_percent("1") == 100
    assert parse_percent(100) == 100
    assert parse_percent("99.9%") != 100


def test_control_parser_keeps_only_direct_exact_wholly_owned_company() -> None:
    root = "根公司"
    items = [
        {
            "name": "直属全资公司",
            "cid": 2,
            "percent": "100%",
            "chainList": [_path(root, "直属全资公司")],
        },
        {
            "name": "间接全资公司",
            "cid": 3,
            "percent": "100%",
            "chainList": [_path(root, "中间公司", "间接全资公司")],
        },
        {
            "name": "直属非全资公司",
            "cid": 4,
            "percent": "99.9%",
            "chainList": [_path(root, "直属非全资公司", percent="99.9%")],
        },
    ]

    parsed = parse_direct_wholly_controlled_items(items, root_name=root)

    assert [item.name for item in parsed] == ["直属全资公司"]
    assert parsed[0].provider_id == "2"
    assert parsed[0].ownership_percent == 100.0


@pytest.mark.asyncio
async def test_control_query_keeps_paging_until_direct_match_is_found() -> None:
    root = "根公司"

    class _Client(TianyanchaClient):
        def __init__(self) -> None:
            super().__init__("test-key")

        async def _request(self, _endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            page = int(params["pageNum"])
            items_by_page = {
                1: [
                    {
                        "name": "间接公司",
                        "cid": 2,
                        "percent": "100%",
                        "chainList": [_path(root, "中间公司", "间接公司")],
                    }
                ],
                2: [
                    {
                        "name": "非全资公司",
                        "cid": 3,
                        "percent": "99%",
                        "chainList": [_path(root, "非全资公司", percent="99%")],
                    }
                ],
                3: [
                    {
                        "name": "直属全资公司",
                        "cid": 4,
                        "percent": "100%",
                        "chainList": [_path(root, "直属全资公司")],
                    }
                ],
            }
            return {
                "error_code": 0,
                "result": {"total": 60, "items": items_by_page[page]},
            }

    result = await _Client().list_direct_wholly_controlled(
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


@pytest.mark.asyncio
async def test_provider_does_not_substitute_outbound_investment_for_control_rights() -> None:
    from api.services.company_control.adapters import TianyanchaControlProvider

    class _Client:
        async def list_direct_wholly_controlled(self, *_args: Any, **_kwargs: Any) -> Any:
            raise TianyanchaApiError(
                code=PERMISSION_DENIED_CODE,
                reason="无权限访问此api",
                endpoint=CONTROL_RIGHT_PATH,
            )

    with pytest.raises(TianyanchaApiError) as raised:
        await TianyanchaControlProvider(_Client()).discover(
            "根公司",
            max_entities=10,
            page_concurrency=2,
        )
    assert raised.value.code == PERMISSION_DENIED_CODE


@pytest.mark.asyncio
async def test_control_service_marks_missing_interface_permission_without_failing_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services.company_control.factory import CompanyControlProviderFactory
    from api.services.company_control.service import CompanyControlService

    class _DeniedProvider:
        name = "tianyancha_control_right"

        async def discover(self, *_args: Any, **_kwargs: Any) -> Any:
            raise TianyanchaApiError(
                code=PERMISSION_DENIED_CODE,
                reason="无权限访问此api",
                endpoint=CONTROL_RIGHT_PATH,
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
