from __future__ import annotations

from api.models.web_tagging_schema import WebTaggingOutput
from api.services.url_scan_pipeline import UrlScanPipeline


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
