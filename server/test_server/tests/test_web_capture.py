from api.services.web_capture import _select_page_target


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
