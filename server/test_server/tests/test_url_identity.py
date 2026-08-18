from api.services.site_relevance import classify_candidate_surface
from api.utils.url_identity import endpoint_identity, prefer_https_url


def test_http_and_https_share_endpoint_identity_and_prefer_https() -> None:
    http = "http://Example.com/path/"
    https = "https://example.com/path"

    assert endpoint_identity(http) == endpoint_identity(https) == "example.com/path"
    assert prefer_https_url(http, https) == https


def test_generic_open_source_surface_is_detected_from_url_or_title() -> None:
    assert classify_candidate_surface(url="https://example.com/kkFileView/index")
    assert classify_candidate_surface(title="kkFileView 在线文件预览")


def test_generic_tracking_endpoint_is_excluded_from_content_collection() -> None:
    assert classify_candidate_surface(
        url="https://example.com/statisticalQuantity/add.htm"
    ) == "generic_tracking_endpoint"
    assert not classify_candidate_surface(
        url="https://example.com/statistics/annual-report.html"
    )
