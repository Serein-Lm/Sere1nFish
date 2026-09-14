import pytest
from api.services.persona_coverage.catalog import catalog, ordered_divisions, default_background
from api.services.persona_coverage.organizations import supported_fact
from api.services.persona_coverage.service import coverage_gaps, profile_ready


def test_catalog_is_complete_and_distributed_across_sectors():
    assert len(catalog()["sectors"]) == 20
    rows = ordered_divisions()
    assert len({item["code"] for item in rows}) == 97
    assert len({item["sector_code"] for item in rows[:20]}) == 20
    assert "无需" in default_background() or "不需要用户提供资料" in default_background()


def test_phone_requires_archived_context_and_cannot_be_a_personal_mobile():
    fact = {"organization_name": "示例机构", "excerpt": "示例机构 联系电话：010-12345678", "office_phone": "010-12345678", "website": "https://invented.invalid", "address": "猜测地址"}
    result = supported_fact(fact, "欢迎。示例机构 联系电话：010-12345678。")
    assert result["office_phone"] == "010-12345678"
    assert result["website"] == result["address"] == ""
    assert supported_fact(fact, "这个来源没有电话") is None
    mobile = {**fact, "excerpt": "示例机构 联系电话：13812345678", "office_phone": "13812345678"}
    assert supported_fact(mobile, mobile["excerpt"])["office_phone"] == ""


def test_coverage_defaults_to_complete_context_and_supports_optional_research():
    assert coverage_gaps(4, 0, 0, 4) == []
    assert coverage_gaps(4, 0, 0, 4, generation_mode="researched") == ["缺少已核验机构背景", "缺少已核验公开办公电话"]
    assert coverage_gaps(4, 1, 1, 4) == []
    assert not profile_ready({"summary": "完整" * 80, "source_urls": []})
    assert profile_ready({"summary": "完整" * 80, "company": "虚构公司", "position": "经理", "context_complete": True})


@pytest.mark.asyncio
async def test_retry_claim_is_fenced_and_reclaims_expired_lease():
    from unittest.mock import AsyncMock
    from api.dao import persona_coverage as dao
    coll = AsyncMock()
    coll.find_one_and_update.return_value = {"job_id": "industry_84"}
    result = await dao.claim({dao.JOBS: coll}, "owner")
    assert result["job_id"] == "industry_84"
    query, update = coll.find_one_and_update.call_args.args[:2]
    assert query["$or"][1]["status"] == "running"
    assert "lease_until" in query["$or"][1]
    assert update["$set"]["lease_owner"] == "owner"
    await dao.update_job({dao.JOBS: coll}, "industry_84", "owner", stage="researching")
    assert coll.update_one.call_args.args[0] == {"job_id": "industry_84", "lease_owner": "owner", "status": "running"}
