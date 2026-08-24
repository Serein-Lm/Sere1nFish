from __future__ import annotations

from typing import Any

import pytest

from api.models.web_tagging_schema import WebTaggingOutput
from api.services.url_scan_pipeline import UrlScanPipeline
from api.services.bidding_records import (
    _public_bidding_record,
    _record_recency_key,
    _record_urls,
    archived_bidding_contacts,
    is_actionable_bidding_contact,
)


def _tagging_payload() -> dict:
    return {
        "intro": {
            "url": "https://example.com/bid/1",
            "final_url": "https://example.com/bid/1",
            "domain": "example.com",
            "site_name": "采购公告",
            "entity_name": "目标采购单位",
            "summary": "目标采购单位发布的采购公告",
        },
        "has_findings": True,
        "no_findings_reason": None,
        "findings": [
            {
                "type": "business_contact",
                "scope": "official",
                "channel": "phone",
                "role": "business",
                "label": "代理机构联系电话",
                "value": "0551-12345678",
                "context": "代理机构联系信息段",
                "source_url": "https://example.com/bid/1",
                "evidence": "代理机构：示例代理公司，电话：0551-12345678",
                "attention_score": 60,
                "attention_reason": "项目咨询电话",
                "party_name": "示例代理公司",
                "party_role": "agency",
                "target_relation": "not_target",
                "target_relation_reason": "公告明确将该单位列为代理机构，而非查询目标",
            }
        ],
    }


def test_web_tagging_allows_explicit_no_finding_result() -> None:
    output = WebTaggingOutput.model_validate({
        "intro": {"url": "https://third-party.example"},
        "site_category": "third_party",
        "target_relation": "not_target",
        "excluded": True,
        "no_findings_reason": "第三方通用系统",
    })

    assert output.has_findings is False
    assert output.findings == []


def test_web_tagging_schema_validates_bidding_party_attribution() -> None:
    output = WebTaggingOutput.model_validate(_tagging_payload())
    finding = output.findings[0]

    assert finding.party_name == "示例代理公司"
    assert finding.party_role == "agency"
    assert finding.target_relation == "not_target"


def test_url_scan_preserves_bidding_party_attribution_in_findings() -> None:
    findings = UrlScanPipeline.extract_findings(
        [
            {
                "success": True,
                "url": "https://example.com/bid/1",
                "data": _tagging_payload(),
            }
        ]
    )

    assert findings[0]["party_name"] == "示例代理公司"
    assert findings[0]["party_role"] == "agency"
    assert findings[0]["target_relation"] == "not_target"
    assert "代理机构" in findings[0]["target_relation_reason"]


def test_bidding_contact_view_rejects_platform_links_and_support_contacts() -> None:
    assert is_actionable_bidding_contact(
        {"channel": "phone", "value": "0551-12345678", "party_role": "agency"}
    )
    assert not is_actionable_bidding_contact(
        {"channel": "link", "value": "https://example.com/download"}
    )
    assert not is_actionable_bidding_contact(
        {"channel": "phone", "value": "400-000-0000", "role": "support"}
    )
    assert not is_actionable_bidding_contact(
        {"channel": "phone", "value": "400-000-0000", "party_role": "publisher"}
    )


def test_bidding_contact_fallback_keeps_article_query_identity() -> None:
    first = _record_urls(
        {
            "detail_url": "https://example.com/site/detail?articleId=first",
        }
    )
    second = _record_urls(
        {
            "detail_url": "http://example.com/site/detail?articleId=second",
        }
    )

    assert first == {"example.com/site/detail?articleId=first"}
    assert second == {"example.com/site/detail?articleId=second"}
    assert first.isdisjoint(second)


def test_bidding_archive_contacts_keep_participants_and_drop_platform_support() -> None:
    contacts = archived_bidding_contacts(
        {
            "purchaser": "重庆江北国际机场有限公司",
            "contact_candidates": [
                {
                    "channel": "phone",
                    "value": "023-67156296",
                    "context": (
                        "联系方式 招标人：重庆江北国际机场有限公司 "
                        "代理机构：重庆国际投资咨询集团有限公司 "
                        "项目负责人：凌女士 电话：023-67156296"
                    ),
                },
                {
                    "channel": "phone",
                    "value": "023-63626470",
                    "context": "技术支持与联系电话 网站操作：023-63626470",
                },
            ],
        }
    )

    assert [item["value"] for item in contacts] == ["023-67156296"]
    assert contacts[0]["party_role"] == "participant"
    assert contacts[0]["target_relation"] == "uncertain"
    assert contacts[0]["review_source"] == "archived_context"


def test_bidding_archive_contact_attributes_unambiguous_purchaser() -> None:
    contacts = archived_bidding_contacts(
        {
            "purchaser": "示例采购单位",
            "contact_candidates": [
                {
                    "channel": "email",
                    "value": "buyer@example.com",
                    "context": "采购人：示例采购单位 联系邮箱 buyer@example.com",
                }
            ],
        }
    )

    assert contacts[0]["party_name"] == "示例采购单位"
    assert contacts[0]["party_role"] == "purchaser"
    assert contacts[0]["target_relation"] == "confirmed"


class _BiddingCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def to_list(self, _length: int | None) -> list[dict[str, Any]]:
        return list(self.rows)


class _BiddingCollection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, *_args: Any, **_kwargs: Any) -> _BiddingCursor:
        return _BiddingCursor(self.rows)


class _BiddingDb:
    def __init__(self, findings: list[dict[str, Any]]) -> None:
        self.findings = _BiddingCollection(findings)

    def __getitem__(self, _name: str) -> _BiddingCollection:
        return self.findings


@pytest.mark.asyncio
async def test_bidding_read_model_uses_archived_contacts_without_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import bidding_records

    records = [
        {
            "record_id": "bid-participant",
            "title": "采购公告",
            "published_on": "2026-08-24",
            "purchaser": "示例采购单位",
            "contact_candidates": [
                {
                    "channel": "phone",
                    "value": "010-12345678",
                    "context": "采购人：示例采购单位 联系电话：010-12345678",
                }
            ],
        },
        {
            "record_id": "bid-support",
            "title": "交易平台说明",
            "published_on": "2026-08-23",
            "contact_candidates": [
                {
                    "channel": "phone",
                    "value": "010-87654321",
                    "context": "技术支持与联系电话 网站操作：010-87654321",
                }
            ],
        },
    ]

    async def query_records(*_args: Any, **_kwargs: Any):
        return records, len(records)

    monkeypatch.setattr(bidding_records.bidding_dao, "query_records", query_records)

    items, total = await bidding_records.list_project_bidding_records(
        _BiddingDb([]),
        project_id="project-1",
    )

    assert total == 1
    assert items[0]["record_id"] == "bid-participant"
    assert items[0]["contacts"][0]["value"] == "010-12345678"
    assert items[0]["contacts"][0]["review_source"] == "archived_context"


def test_bidding_read_model_excludes_heavy_archived_evidence() -> None:
    record = {
        "record_id": "bid-1",
        "title": "采购公告",
        "content_preview": "正文" * 2_000,
        "detail_text_preview": "重复正文" * 2_000,
        "provider_payload": {"large": "payload"},
        "attachments": [
            {
                "index": 0,
                "status": "ready",
                "filename": "公告.pdf",
                "url": "/api/v1/storage/objects/attachment/content",
                "text_preview": "附件全文" * 2_000,
            }
        ],
        "resolved_detail_url": "https://example.com/bid/1",
    }
    contacts = [
        {
            "finding_id": "finding-1",
            "channel": "phone",
            "value": "0551-12345678",
            "context": "采购人联系方式",
            "attention_score": 80,
            "raw_result": {"large": "payload"},
        }
    ]

    public = _public_bidding_record(record, contacts=contacts)

    assert public["original_url"] == "https://example.com/bid/1"
    assert public["contact_count"] == 1
    assert public["contacts"][0]["value"] == "0551-12345678"
    assert "raw_result" not in public["contacts"][0]
    assert "provider_payload" not in public
    assert "detail_text_preview" not in public
    assert len(public["content_preview"]) <= 2_003
    assert "text_preview" not in public["attachments"][0]


def test_bidding_project_order_prioritizes_publish_date_over_contact_score() -> None:
    records = [
        {"published_on": "2026-06-01", "updated_at": "2026-07-01", "max_contact_score": 99},
        {"published_on": "2026-08-01", "updated_at": "2026-08-02", "max_contact_score": 40},
    ]

    assert sorted(records, key=_record_recency_key, reverse=True) == [
        records[1],
        records[0],
    ]
