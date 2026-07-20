from __future__ import annotations

from typing import Any

import pytest

from api.dao.bidding import _merge_archive_evidence
from api.services import bidding_pipeline as bidding_module
from api.services.company_url import normalize_url
from api.services.bidding_pipeline import (
    BiddingPipeline,
    _filename_from_response,
    _html_text_and_links,
)
from crawler_tools.tianyancha_tools import BiddingRecord, BiddingSearchResult


def test_archive_merge_does_not_replace_ready_evidence_with_transient_errors() -> None:
    previous = {
        "raw_content_object_id": "obj_raw",
        "raw_content_url": "/objects/obj_raw",
        "detail_html_object_id": "obj_detail",
        "attachment_urls": ["https://example.com/spec.pdf"],
        "attachments": [
            {
                "index": 0,
                "source_url": "https://example.com/spec.pdf",
                "status": "ready",
                "storage_object_id": "obj_pdf",
                "url": "/objects/obj_pdf",
            }
        ],
    }
    current = {
        "raw_content_object_id": "",
        "detail_html_object_id": "",
        "attachment_urls": ["https://example.com/new.docx"],
        "attachments": [
            {
                "index": 0,
                "source_url": "https://example.com/spec.pdf",
                "status": "error",
                "error": "timeout",
            },
            {
                "index": 1,
                "source_url": "https://example.com/new.docx",
                "status": "ready",
                "storage_object_id": "obj_docx",
            },
        ],
    }

    merged = _merge_archive_evidence(current, previous)

    assert merged["raw_content_object_id"] == "obj_raw"
    assert merged["detail_html_object_id"] == "obj_detail"
    assert merged["attachment_urls"] == [
        "https://example.com/spec.pdf",
        "https://example.com/new.docx",
    ]
    assert merged["attachments"][0]["status"] == "ready"
    assert merged["attachments"][0]["storage_object_id"] == "obj_pdf"
    assert merged["attachments"][0]["latest_archive_error"] == "timeout"
    assert merged["attachments"][1]["storage_object_id"] == "obj_docx"


def test_html_reader_extracts_text_and_attachment_links() -> None:
    text, links = _html_text_and_links(
        """
        <html><body>
          <script>ignore()</script>
          <h1>采购公告</h1>
          <a href="/files/spec.pdf">下载采购文件</a>
        </body></html>
        """,
        "https://example.com/bids/1",
    )

    assert text == "采购公告 下载采购文件"
    assert links == [
        {"url": "https://example.com/files/spec.pdf", "label": "下载采购文件"}
    ]


def test_remote_filename_recovers_gb18030_header_bytes() -> None:
    expected = "招标文件正文.pdf"
    surrogate_name = expected.encode("gb18030").decode("utf-8", errors="surrogateescape")

    filename = _filename_from_response(
        "https://example.com/download?id=1",
        {"Content-Disposition": f'attachment; filename="{surrogate_name}"'},
        "application/pdf",
    )

    assert filename == expected


def test_url_normalization_rejects_relative_or_hostless_values() -> None:
    assert normalize_url("/html/1336/content.html") == ""
    assert normalize_url("https:///html/1336/content.html") == ""
    assert normalize_url("example.com/bids/1") == "https://example.com/bids/1"


def test_scan_context_keeps_query_target_and_announcement_parties_distinct() -> None:
    record = BiddingRecord(
        record_id="bid_party_roles",
        title="采购结果公告",
        enterprise_identity="被提及",
        purchaser="目标单位下属公司",
        agency="采购代理有限公司",
        winner="中标供应商有限公司",
        detail_url="https://example.com/bids/roles",
    )

    context = BiddingPipeline._scan_context(
        record,
        {"_context_text": "采购人联系人张老师；代理机构联系人李老师"},
        target_name="目标单位",
    )

    assert "本次查询目标主体：目标单位" in context
    assert "供应商对查询目标的命中身份标注：被提及" in context
    assert "公告采购方/招标人：目标单位下属公司" in context
    assert "公告代理机构：采购代理有限公司" in context
    assert "公告供应商/中标方：中标供应商有限公司" in context
    assert "查询命中不代表目标主体就是采购方" in context
    assert "代理机构、关联公司或第三方平台的联系人不得标成目标单位联系人" in context


@pytest.mark.asyncio
async def test_pipeline_archives_then_reuses_visual_and_copywriting_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = BiddingRecord(
        record_id="bid_one",
        provider_uuid="one",
        title="演播室设备采购公告",
        purchaser="安徽广播电视台",
        detail_url="https://example.com/bids/one",
        content_html="<p>设备采购联系人：张老师</p>",
        raw_payload={"uuid": "one", "bidList": [{"name": "供应商 A"}]},
    )

    class _Client:
        async def search_bids(self, company_name: str, **kwargs: Any) -> BiddingSearchResult:
            assert company_name == "安徽广播电视台"
            assert kwargs["page_size"] == 20
            return BiddingSearchResult(
                records=[record],
                total_reported=1,
                publish_start="2026-01-18",
                publish_end="2026-07-17",
            )

    async def _configured_client() -> _Client:
        return _Client()

    async def _archive_records(
        _self: Any,
        records: list[BiddingRecord],
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        assert records == [record]
        return [
            {
                "record_id": "bid_one",
                "content_text": "设备采购联系人：张老师",
                "content_length": 12,
                "content_preview": "设备采购联系人：张老师",
                "provider_payload_object_id": "obj_provider",
                "provider_payload_url": "/api/v1/storage/objects/obj_provider/content",
                "raw_content_object_id": "obj_raw",
                "raw_content_url": "/api/v1/storage/objects/obj_raw/content",
                "detail_html_object_id": "obj_detail",
                "detail_html_url": "/api/v1/storage/objects/obj_detail/content",
                "detail_text_preview": "设备采购联系人：张老师",
                "attachment_urls": ["https://example.com/files/spec.pdf"],
                "attachments": [],
                "archive_errors": [],
                "_context_text": "设备采购联系人：张老师",
            }
        ]

    persisted: dict[str, Any] = {}

    async def _upsert(_db: Any, **kwargs: Any) -> dict[str, int]:
        persisted.update(kwargs)
        return {"inserted": 1, "updated": 0, "unchanged": 0, "total": 1}

    scan_call: dict[str, Any] = {}

    async def _run_visual(_self: Any, **kwargs: Any) -> dict[str, Any]:
        scan_call.update(kwargs)
        return {
            "status": "completed",
            "scanned_urls": 1,
            "total_findings": 2,
            "total_copywritings": 1,
        }

    monkeypatch.setattr(
        bidding_module.TianyanchaClient,
        "from_runtime_config",
        _configured_client,
    )
    monkeypatch.setattr(
        bidding_module.BiddingArchiveService,
        "archive_records",
        _archive_records,
    )
    monkeypatch.setattr(bidding_module.bidding_dao, "upsert_records_batch", _upsert)

    from api.services.url_scan_pipeline import UrlScanPipeline

    monkeypatch.setattr(UrlScanPipeline, "run_pipeline", _run_visual)

    result = await BiddingPipeline(object(), object()).run_pipeline(
        task_id="task-1",
        project_id="project-1",
        target_id="target-1",
        parent_task_id="company-task-1",
        company_name="安徽广播电视台",
        page_size=20,
    )

    stored_record = persisted["records"][0]
    assert "content_html" not in stored_record
    assert "content_text" not in stored_record
    assert stored_record["raw_content_object_id"] == "obj_raw"
    assert stored_record["provider_payload_object_id"] == "obj_provider"
    assert scan_call["source"] == "bidding"
    assert scan_call["parent_task_id"] == "company-task-1"
    assert scan_call["progress_source"] == "bidding_url_scan"
    assert scan_call["copywriting_score_threshold"] == 70
    assert scan_call["max_copywritings_per_url"] == 1
    assert scan_call["known_alive_urls"] == ["https://example.com/bids/one"]
    evidence = scan_call["source_context_by_url"]["https://example.com/bids/one"]
    assert "本次查询目标主体：安徽广播电视台" in evidence
    assert "公告采购方/招标人：安徽广播电视台" in evidence
    assert "演播室设备采购公告" in evidence
    assert "设备采购联系人：张老师" in evidence
    assert result["visual_analysis"]["findings_count"] == 2
    assert result["visual_analysis"]["copywritings_count"] == 1


@pytest.mark.asyncio
async def test_pipeline_falls_back_from_relative_detail_to_provider_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = BiddingRecord(
        record_id="bid_relative",
        title="采购公告",
        detail_url="/html/1336/content.html",
        provider_url="https://m.tianyancha.com/app/h5/bid/relative",
        content_html="<p>公告正文</p>",
    )

    class _Client:
        async def search_bids(self, *_args: Any, **_kwargs: Any) -> BiddingSearchResult:
            return BiddingSearchResult(records=[record], total_reported=1)

    async def _configured_client() -> _Client:
        return _Client()

    async def _archive_records(
        _self: Any,
        _records: list[BiddingRecord],
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        return [
            {
                "record_id": record.record_id,
                "resolved_detail_url": record.provider_url,
                "detail_html_object_id": "",
                "archive_errors": ["供应商页面暂时不可读"],
                "attachments": [],
                "attachment_urls": [],
                "_context_text": "公告正文",
            }
        ]

    async def _upsert(_db: Any, **_kwargs: Any) -> dict[str, int]:
        return {"inserted": 1, "updated": 0, "unchanged": 0, "total": 1}

    scan_call: dict[str, Any] = {}

    async def _run_visual(_self: Any, **kwargs: Any) -> dict[str, Any]:
        scan_call.update(kwargs)
        return {
            "status": "completed",
            "scanned_urls": 1,
            "total_findings": 0,
            "total_copywritings": 0,
        }

    monkeypatch.setattr(
        bidding_module.TianyanchaClient,
        "from_runtime_config",
        _configured_client,
    )
    monkeypatch.setattr(
        bidding_module.BiddingArchiveService,
        "archive_records",
        _archive_records,
    )
    monkeypatch.setattr(bidding_module.bidding_dao, "upsert_records_batch", _upsert)

    from api.services.url_scan_pipeline import UrlScanPipeline

    monkeypatch.setattr(UrlScanPipeline, "run_pipeline", _run_visual)

    await BiddingPipeline(object(), object()).run_pipeline(
        task_id="task-relative",
        project_id="project-1",
        target_id="target-1",
        company_name="目标单位",
    )

    assert scan_call["url_content"] == record.provider_url
    assert scan_call["known_alive_urls"] == []
    assert "https:///html" not in scan_call["url_content"]


@pytest.mark.asyncio
async def test_pipeline_rejects_unassigned_records() -> None:
    with pytest.raises(ValueError, match="Target"):
        await BiddingPipeline(object(), object()).run_pipeline(
            task_id="task-1",
            project_id="project-1",
            target_id="",
            company_name="安徽广播电视台",
        )
