from __future__ import annotations

from api.services.source_documents.resources import extract_attachment_text
from api.services.source_documents.web import parse_official_html
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
    from io import BytesIO

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
