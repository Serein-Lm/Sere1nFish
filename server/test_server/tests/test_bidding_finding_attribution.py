from __future__ import annotations

from api.models.web_tagging_schema import WebTaggingOutput
from api.services.url_scan_pipeline import UrlScanPipeline
from api.services.bidding_records import (
    _public_bidding_record,
    _record_urls,
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
