from __future__ import annotations

from typing import Any

import pytest

from api.services.target_scan_profile import (
    SCAN_PROFILE_VERSION,
    build_target_scan_profile,
    coverage_status_from_result,
    has_current_mobile_keyword_coverage,
    is_scan_coverage_current,
    load_project_descendant_scan_entities,
    select_subsidiary_scan_scope,
)


def test_profile_keeps_short_names_but_rejects_appended_departments() -> None:
    profile = build_target_scan_profile(
        canonical_name="交通运输部路网监测与应急处置中心",
        identity_aliases=[
            "路网中心",
            "交通运输部路网监测与应急处置中心联网结算服务部",
        ],
        verified_aliases=[
            "交通运输部路网监测与应急处置中心联网结算服务部",
        ],
        fallback_aliases=[
            "交通运输部路网监测与应急处置中心联网结算服务部",
        ],
    )

    assert profile["version"] == SCAN_PROFILE_VERSION
    assert "路网中心" in profile["search_aliases"]
    assert all("联网结算服务部" not in name for name in profile["search_aliases"])


def test_profile_accepts_slash_in_canonical_institution_name() -> None:
    profile = build_target_scan_profile(
        canonical_name="中国疾病预防控制中心儿少/学校卫生中心",
        identity_aliases=["中国疾控中心儿少学校卫生中心"],
    )

    assert profile["canonical_name"] == "中国疾病预防控制中心儿少/学校卫生中心"
    assert profile["search_aliases"][0] == "中国疾病预防控制中心儿少/学校卫生中心"


def test_profile_accepts_verified_internal_qualifier_omission() -> None:
    profile = build_target_scan_profile(
        canonical_name="南京禄口国际机场",
        fallback_aliases=["南京禄口机场", "南京机场"],
    )

    assert "南京禄口机场" in profile["search_aliases"]
    assert "南京机场" not in profile["search_aliases"]


def test_profile_rejects_unrelated_department_from_previous_profile() -> None:
    profile = build_target_scan_profile(
        canonical_name="交通运输部路网监测与应急处置中心",
        existing_profile={
            "version": SCAN_PROFILE_VERSION - 1,
            "search_aliases": ["路网中心", "联网结算服务部"],
        },
    )

    assert "路网中心" in profile["search_aliases"]
    assert "联网结算服务部" not in profile["search_aliases"]


def test_profile_accepts_verified_brand_only_after_identity_confirmation() -> None:
    unverified = build_target_scan_profile(
        canonical_name="上海宽娱数码科技有限公司",
        ai_aliases=["B站", "哔哩哔哩"],
        ai_identity_verified=False,
    )
    verified = build_target_scan_profile(
        canonical_name="上海宽娱数码科技有限公司",
        ai_aliases=["B站", "哔哩哔哩"],
        ai_identity_verified=True,
    )

    assert "B站" not in unverified["search_aliases"]
    assert {"B站", "哔哩哔哩"}.issubset(verified["search_aliases"])
    assert verified["display_name"] == "B站"


def test_profile_upgrade_reuses_fingerprint_when_scan_inputs_are_unchanged() -> None:
    previous = {
        "version": SCAN_PROFILE_VERSION - 1,
        "canonical_name": "示例科技有限公司",
        "search_aliases": ["示例科技有限公司", "示例科技"],
        "fingerprint": "existing-fingerprint",
    }

    profile = build_target_scan_profile(
        canonical_name="示例科技有限公司",
        existing_profile=previous,
    )

    assert profile["search_aliases"] == previous["search_aliases"]
    assert profile["fingerprint"] == "existing-fingerprint"


@pytest.mark.asyncio
async def test_collection_target_exposes_authoritative_scan_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import targets as targets_service

    async def resolve_target(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "target_id": "target-1",
            "canonical_name": "南京禄口国际机场",
            "aliases": ["南京禄口国际机场"],
            "scan_aliases": ["南京禄口国际机场", "南京禄口机场"],
        }

    monkeypatch.setattr(targets_service, "resolve_target", resolve_target)

    result = await targets_service.resolve_collection_target(
        object(),
        task_def={
            "target_id": "target-1",
            "target_name": "南京禄口国际机场",
            "target_type": "company",
        },
    )

    assert result is not None
    assert result["aliases"] == ["南京禄口国际机场", "南京禄口机场"]


def test_coverage_requires_completed_status_and_current_profile() -> None:
    relation = {
        "scan_profile_fingerprint": "current",
        "scan_coverage": {
            "website": {
                "status": "completed",
                "profile_fingerprint": "current",
            },
            "wechat": {
                "status": "partial",
                "profile_fingerprint": "current",
            },
            "scholar": {
                "status": "completed",
                "profile_fingerprint": "old",
            },
        },
    }

    assert is_scan_coverage_current(relation, "website") is True
    assert is_scan_coverage_current(relation, "wechat") is False
    assert is_scan_coverage_current(relation, "scholar") is False


def test_website_coverage_is_partial_when_one_runtime_stops() -> None:
    status = coverage_status_from_result(
        "website",
        {
            "assets": {"status": "completed"},
            "url_scan": {"status": "timed_out"},
        },
    )

    assert status == "partial"


def test_website_coverage_is_partial_when_any_url_failed() -> None:
    status = coverage_status_from_result(
        "website",
        {
            "assets": {"status": "completed"},
            "url_scan": {
                "status": "completed",
                "scanned_urls": 9,
                "failed_urls": 1,
                "remaining_urls": 0,
            },
        },
    )

    assert status == "partial"


def test_website_coverage_requires_completed_url_and_document_stages() -> None:
    legacy = coverage_status_from_result(
        "website",
        {
            "assets": {"status": "completed"},
            "url_scan": {"enabled": True, "status": "completed"},
        },
    )
    complete = coverage_status_from_result(
        "website",
        {
            "status": "completed",
            "assets": {"status": "completed"},
            "url_scan": {
                "enabled": True,
                "status": "completed",
                "remaining_urls": 0,
                "failed_urls": 0,
            },
            "website_documents": {
                "enabled": True,
                "status": "completed",
                "pending_pages": 0,
                "failed_pages": 0,
                "documents_partial": 0,
            },
        },
    )

    assert legacy == "partial"
    assert complete == "completed"


def test_current_mobile_keywords_keep_completed_coverage() -> None:
    outcome = {
        "status": "completed",
        "keyword_resolution": {
            "keywords": ["示例机场", "示例机场 联系方式"],
            "target_ids": ["target-airport"],
        },
        "keywords_completed": 2,
        "keyword_total": 2,
        "failed_keywords": 0,
        "persist_failed": 0,
        "stopped": False,
        "timed_out": False,
    }

    assert has_current_mobile_keyword_coverage(
        outcome,
        target_id="target-airport",
    ) is True
    assert has_current_mobile_keyword_coverage(
        outcome,
        target_id="another-target",
    ) is False
    assert has_current_mobile_keyword_coverage(
        {"status": "completed"},
        target_id="target-airport",
    ) is False


@pytest.mark.asyncio
async def test_loads_persisted_descendants_as_scan_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao

    async def descendants(*_args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        assert kwargs["max_depth"] == 2
        return [
            {
                "project_target_id": "pt-child",
                "target_id": "child",
                "target_name": "示例子公司有限公司",
                "display_name": "示例子公司",
                "scan_aliases": ["示例子公司有限公司", "示例子公司"],
                "root_domain": "child.example.com",
                "root_domains": ["child.example.com"],
                "root_target_id": "root",
                "parent_target_id": "root",
                "relation_depth": 1,
                "ownership_percent": 100,
            }
        ]

    monkeypatch.setattr(
        targets_dao,
        "list_project_target_descendants",
        descendants,
    )

    result = await load_project_descendant_scan_entities(
        object(),
        project_id="project-1",
        root_target_id="root",
        max_depth=2,
    )

    assert result[0]["name"] == "示例子公司有限公司"
    assert result[0]["aliases"] == ["示例子公司有限公司", "示例子公司"]
    assert result[0]["entity_source"] == "project_target_relation"


@pytest.mark.asyncio
async def test_subsidiary_scope_skips_only_current_completed_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import targets as targets_dao

    async def relations(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "target_id": "done",
                "scan_profile_fingerprint": "fp-done",
                "scan_coverage": {
                    "website": {
                        "status": "completed",
                        "profile_fingerprint": "fp-done",
                    }
                },
            },
            {
                "target_id": "stale",
                "root_domain": "stale.example.com",
                "scan_profile_fingerprint": "fp-new",
                "scan_coverage": {
                    "website": {
                        "status": "completed",
                        "profile_fingerprint": "fp-old",
                    }
                },
            },
        ]

    monkeypatch.setattr(targets_dao, "get_project_targets_by_ids", relations)

    result = await select_subsidiary_scan_scope(
        object(),
        project_id="project-1",
        entities=[
            {"target_id": "done", "name": "已完成单位"},
            {"target_id": "stale", "name": "待重扫单位"},
        ],
        channels=["website"],
        max_entities=10,
        skip_completed=True,
    )

    assert [item["target_id"] for item in result["selected"]] == ["stale"]
    assert result["skipped"][0]["skip_reason"] == "requested_channels_completed"
