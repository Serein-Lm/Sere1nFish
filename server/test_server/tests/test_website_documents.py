from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

import aiohttp

from api.services.source_documents.resources import (
    extract_attachment_text,
    html_text_and_links,
)
from api.services.source_documents.web import (
    OfficialWebDocumentProvider,
    parse_official_html,
)
from api.services.website_documents import (
    classify_discovered_link,
    select_official_seed_urls,
)


def test_official_html_extracts_article_and_attachment() -> None:
    parsed = parse_official_html(
        b"""
        <html><head><title>site</title></head><body>
          <div class="left fl">
            <h5>Contact notice</h5><div class="articleCon content">
              <p>Call 010-12345678 before 2026-08-20.</p>
            </div>
            <div class="wzFooter"><a href="files/list.xlsx">Attachment</a></div>
          </div>
        </body></html>
        """,
        url="https://example.gov.cn/notices/1.html",
    )

    assert parsed["title"] == "Contact notice"
    assert "010-12345678" in parsed["text"]
    assert parsed["attachment_links"] == [
        {
            "url": "https://example.gov.cn/notices/files/list.xlsx",
            "label": "Attachment",
        }
    ]


def test_official_html_extracts_embedded_and_data_attachments() -> None:
    parsed = parse_official_html(
        b"""
        <html><body><article>
          <h1>Notice</h1><p>Evidence body with enough stable text for selection.</p>
          <iframe title="PDF evidence" src="files/evidence.pdf"></iframe>
          <a data-href="files/list.csv">Download list</a>
        </article></body></html>
        """,
        url="https://example.gov.cn/notices/1.html",
    )

    assert [item["url"] for item in parsed["attachment_links"]] == [
        "https://example.gov.cn/notices/files/evidence.pdf",
        "https://example.gov.cn/notices/files/list.csv",
    ]


def test_listing_link_extraction_reads_onclick_navigation() -> None:
    _text, links = html_text_and_links(
        b"""
        <html><body>
          <a onclick="window.open('index_2.shtml')">Next</a>
          <a data-url="202608/t20260812_1.html">Notice</a>
        </body></html>
        """,
        "https://example.gov.cn/notices/",
    )

    assert [item["url"] for item in links] == [
        "https://example.gov.cn/notices/index_2.shtml",
        "https://example.gov.cn/notices/202608/t20260812_1.html",
    ]


def test_crawl_link_classifier_ignores_navigation_but_keeps_notice_scope() -> None:
    navigation = classify_discovered_link(
        url="https://example.gov.cn/about/",
        label="About us",
        parent_url="https://example.gov.cn/notices/",
        parent_relevant=True,
        depth=2,
        max_depth=5,
    )
    pagination = classify_discovered_link(
        url="https://example.gov.cn/notices/index_2.html",
        label="3",
        parent_url="https://example.gov.cn/notices/",
        parent_relevant=True,
        depth=2,
        max_depth=5,
    )
    document = classify_discovered_link(
        url="https://example.gov.cn/notices/202608/t20260812_1.html",
        label="Notice detail",
        parent_url="https://example.gov.cn/notices/",
        parent_relevant=True,
        depth=2,
        max_depth=5,
    )
    cross_scope_document = classify_discovered_link(
        url="https://example.gov.cn/legal/202608/t20260812_2.html",
        label="Legal statement",
        parent_url="https://example.gov.cn/notices/",
        parent_relevant=True,
        depth=2,
        max_depth=5,
    )

    assert navigation is None
    assert pagination and pagination["kind"] == "index"
    assert document and document["kind"] == "document"
    assert cross_scope_document is None


def test_crawl_link_classifier_recognizes_query_and_path_pagination() -> None:
    query_page = classify_discovered_link(
        url="https://example.gov.cn/notices/list.shtml?pageIndex=2",
        label="2",
        parent_url="https://example.gov.cn/notices/list.shtml?pageIndex=1",
        parent_relevant=True,
        depth=2,
        max_depth=5,
    )
    path_page = classify_discovered_link(
        url="https://example.gov.cn/notices/page/3/",
        label="下一页",
        parent_url="https://example.gov.cn/notices/page/2/",
        parent_relevant=True,
        depth=3,
        max_depth=5,
    )
    article = classify_discovered_link(
        url="https://example.gov.cn/notices/detail.shtml?id=42",
        label="项目申报通知",
        parent_url="https://example.gov.cn/notices/list.shtml?page=2",
        parent_relevant=True,
        depth=3,
        max_depth=5,
    )

    assert query_page and query_page["kind"] == "index"
    assert path_page and path_page["kind"] == "index"
    assert article and article["kind"] == "document"


def test_official_seed_selection_prefers_probed_www_origin() -> None:
    seeds = select_official_seed_urls(
        fallback_urls=["https://example.gov.cn/"],
        known_alive_urls=[
            "http://portal.example.gov.cn:9999/",
            "https://www.example.gov.cn/",
            "https://mail.example.gov.cn/",
        ],
        root_domains=["example.gov.cn"],
    )

    assert seeds == ["https://www.example.gov.cn/"]


def test_official_seed_selection_falls_back_to_apex() -> None:
    seeds = select_official_seed_urls(
        fallback_urls=["example.gov.cn"],
        known_alive_urls=[],
        root_domains=["example.gov.cn"],
    )

    assert seeds == ["https://example.gov.cn/"]


def test_xlsx_attachment_text_is_extracted() -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["contact", "value"])
    sheet.append(["phone", "010-12345678"])
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()

    text, error, text_format = extract_attachment_text(
        buffer.getvalue(),
        filename="contacts.xlsx",
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )

    assert error == ""
    assert text_format == "xlsx"
    assert "010-12345678" in text


def test_scanned_pdf_uses_bounded_ocr_fallback(monkeypatch) -> None:
    from PIL import Image

    from api.services.source_documents import resources

    buffer = BytesIO()
    Image.new("RGB", (640, 360), "white").save(buffer, format="PDF")
    calls: list[list[int]] = []

    def _ocr(_data: bytes, pages: list[int]):
        calls.append(pages)
        return {1: "项目联系人 张三 010-12345678"}, []

    monkeypatch.setattr(resources, "_ocr_pdf_pages", _ocr)
    text, error, text_format = resources.extract_attachment_text(
        buffer.getvalue(),
        filename="scan.pdf",
        content_type="application/pdf",
    )

    assert calls == [[1]]
    assert error == ""
    assert text_format == "pdf"
    assert "010-12345678" in text


def test_pptx_and_plain_text_attachments_are_extracted() -> None:
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "项目联络"
    slide.placeholders[1].text = "邮箱 contact@example.gov.cn"
    buffer = BytesIO()
    presentation.save(buffer)

    pptx_text, pptx_error, pptx_format = extract_attachment_text(
        buffer.getvalue(),
        filename="contact.pptx",
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation"
        ),
    )
    csv_text, csv_error, csv_format = extract_attachment_text(
        "姓名,电话\n张三,010-12345678".encode(),
        filename="contact.csv",
        content_type="text/csv",
    )

    assert pptx_error == ""
    assert pptx_format == "pptx"
    assert "contact@example.gov.cn" in pptx_text
    assert csv_error == ""
    assert csv_format == "csv"
    assert "010-12345678" in csv_text


def test_permanent_missing_attachment_is_warning(monkeypatch) -> None:
    from api.services.source_documents import web

    async def _missing(*_args, **_kwargs):
        raise aiohttp.ClientResponseError(
            request_info=SimpleNamespace(
                real_url="https://example.gov.cn/gone.pdf"
            ),
            history=(),
            status=404,
            message="Not Found",
        )

    monkeypatch.setattr(web, "fetch_resource_with_retry", _missing)
    attachments, errors, warnings = asyncio.run(
        OfficialWebDocumentProvider._download_attachments(
            object(),
            [{"url": "https://example.gov.cn/gone.pdf", "label": "附件"}],
        )
    )

    assert attachments == []
    assert errors == []
    assert warnings and "HTTP 404" in warnings[0]


def test_permanent_missing_image_is_warning(monkeypatch) -> None:
    from api.services.source_documents import web

    async def _missing(*_args, **_kwargs):
        raise aiohttp.ClientResponseError(
            request_info=SimpleNamespace(
                real_url="https://example.gov.cn/gone.png"
            ),
            history=(),
            status=410,
            message="Gone",
        )

    monkeypatch.setattr(web, "fetch_resource_with_retry", _missing)
    images, errors, warnings = asyncio.run(
        OfficialWebDocumentProvider._download_images(
            object(), ["https://example.gov.cn/gone.png"]
        )
    )

    assert images == []
    assert errors == []
    assert warnings and "HTTP 410" in warnings[0]
