import asyncio

from api.services.web_capture import (
    _ignore_certificate_errors,
    _rendered_page_expression,
    _select_page_target,
    extract_rendered_contact_evidence,
)


def test_page_target_prefers_exact_url_over_other_tabs() -> None:
    selected = _select_page_target(
        [
            {"targetId": "blank", "type": "page", "url": "about:blank"},
            {"targetId": "other", "type": "page", "url": "https://example.com/other"},
            {"targetId": "wanted", "type": "page", "url": "https://example.com/bids/1"},
        ],
        "https://example.com/bids/1",
    )

    assert selected is not None
    assert selected["targetId"] == "wanted"


def test_page_target_accepts_redirect_on_same_host() -> None:
    selected = _select_page_target(
        [
            {"targetId": "other", "type": "page", "url": "https://other.example/page"},
            {"targetId": "redirect", "type": "page", "url": "https://example.com/login"},
        ],
        "https://example.com/bids/1",
    )

    assert selected is not None
    assert selected["targetId"] == "redirect"


def test_page_target_rejects_unrelated_open_tab() -> None:
    selected = _select_page_target(
        [
            {"targetId": "other", "type": "page", "url": "https://other.example/bids/1"},
        ],
        "https://example.com/bids/1",
    )

    assert selected is None


def test_configure_cdp_security_ignores_certificate_errors() -> None:
    calls = []

    async def command(method, *, params=None):
        calls.append((method, params))

    asyncio.run(_ignore_certificate_errors(command))

    assert calls == [
        ("Security.setIgnoreCertificateErrors", {"ignore": True})
    ]


def test_rendered_contact_evidence_keeps_phone_and_css_service_button() -> None:
    evidence = extract_rendered_contact_evidence(
        {
            "url": "https://zwfw.cscse.edu.cn",
            "final_url": "https://zwfw.cscse.edu.cn/",
            "title": "教育部留学服务中心网上服务大厅",
            "content_length": 1129,
            "visible_text": "联系我们\n咨询电话：010-62677800（客服中心）",
            "controls": [
                {
                    "tag": "BUTTON",
                    "text": "",
                    "aria_label": "",
                    "title": "",
                    "background_image": (
                        "https://zwfw.cscse.edu.cn/eportal/fileDir/cscse/js/"
                        "aiccBtnBg.png"
                    ),
                    "position": "fixed",
                    "visible": True,
                    "interactive": True,
                    "rect": {"x": 1290, "y": 730, "width": 90, "height": 30},
                },
                {
                    "tag": "A",
                    "text": "在线查验",
                    "href": "https://example.com/contactUs?type=2",
                    "position": "static",
                    "visible": True,
                    "interactive": True,
                    "rect": {"x": 20, "y": 40, "width": 80, "height": 30},
                },
                {
                    "tag": "A",
                    "text": "Service catalog",
                    "href": "https://example.com/services",
                    "position": "static",
                    "visible": True,
                    "interactive": True,
                    "rect": {"x": 20, "y": 80, "width": 80, "height": 30},
                }
            ],
            "service_resources": [
                "https://zwfw.cscse.edu.cn/eportal/fileDir/cscse/js/kf121801.js"
            ],
        }
    )

    assert [item["value"] for item in evidence["contacts"]] == ["010-62677800"]
    assert len(evidence["service_entries"]) == 1
    assert evidence["service_entries"][0]["label"] == "页面客服入口"
    assert evidence["service_entries"][0]["value"] is None
    assert "aiccBtnBg.png" in evidence["service_entries"][0]["evidence"]


def test_rendered_page_expression_collects_visible_control_semantics() -> None:
    expression = _rendered_page_expression(include_html=False)

    assert "visibleText" in expression
    assert "backgroundImage" in expression
    assert "serviceResources" in expression
    assert "html: false ?" in expression
