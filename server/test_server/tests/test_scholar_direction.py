from __future__ import annotations

from types import SimpleNamespace

from api.services.scholar_direction import resolve_scholar_direction


def test_scholar_source_health_reports_partial_provider_failure() -> None:
    from api.services.scholar_contact_pipeline import _scholar_source_health

    health = _scholar_source_health(
        {
            "api_results": {"articles": []},
            "email_extraction": {
                "sources": {
                    "pubmed": {"articles": []},
                    "europepmc": {"error": "rate limited"},
                }
            },
        }
    )

    assert health["succeeded"] == ["openalex", "pubmed"]
    assert health["errors"] == ["europepmc: rate limited"]


def test_scholar_source_health_detects_total_provider_failure() -> None:
    from api.services.scholar_contact_pipeline import _scholar_source_health

    health = _scholar_source_health(
        {
            "api_results": {"error": "openalex unavailable"},
            "email_extraction": {"error": "extractors unavailable"},
        }
    )

    assert health["succeeded"] == []
    assert health["errors"] == [
        "openalex: openalex unavailable",
        "email_extractors: extractors unavailable",
    ]


def test_scholar_bulk_pages_preserve_full_text_failures(monkeypatch) -> None:
    from crawler_tools import scholar_tools

    monkeypatch.setattr(
        scholar_tools,
        "_get",
        lambda _url: {
            "hitCount": 1,
            "resultList": {
                "result": [
                    {
                        "pmcid": "PMC123",
                        "title": "公开论文",
                        "pubYear": "2026",
                    }
                ]
            },
            "nextCursorMark": "*",
        },
    )
    monkeypatch.setattr(
        scholar_tools,
        "_get_text",
        lambda _url: (_ for _ in ()).throw(RuntimeError("upstream timeout")),
    )

    pages = list(
        scholar_tools.europepmc_bulk_pages(
            "Example University",
            max_articles=1,
            page_size=1,
        )
    )

    assert pages[0]["errors"] == ["PMC123: upstream timeout"]
    assert pages[0]["articles"][0]["full_text_error"] == "upstream timeout"


def test_scholar_source_error_details_are_bounded() -> None:
    from api.services.scholar_contact_pipeline import _record_source_errors

    summary = {"source_errors": [], "source_error_count": 0}
    _record_source_errors(summary, [f"error-{index}" for index in range(150)])

    assert summary["source_error_count"] == 150
    assert len(summary["source_errors"]) == 100


def test_manual_scholar_direction_has_priority() -> None:
    result = resolve_scholar_direction(
        "  金融科技  ",
        SimpleNamespace(success=False),
        names=["某交易所"],
    )

    assert result.direction == "金融科技"
    assert result.source == "manual"


def test_scholar_direction_reuses_company_router_paper_strategy() -> None:
    router = SimpleNamespace(
        success=True,
        company_profile=SimpleNamespace(
            industry=SimpleNamespace(value="media"),
            sub_industries=["融媒体"],
            main_business=["广播电视节目制作"],
        ),
        search_strategy=SimpleNamespace(
            paper=SimpleNamespace(
                params={"research_direction": "媒体融合技术"},
                focus_points=["广播电视传输技术"],
                keywords=["安徽广播电视台 学术研究"],
            )
        ),
    )

    result = resolve_scholar_direction(
        "",
        router,
        names=["安徽广播电视台", "AHTV"],
    )

    assert result.source == "company_router"
    assert "媒体融合技术" in result.direction
    assert "广播电视传输技术" in result.direction
    assert "安徽广播电视台" not in result.direction


def test_scholar_direction_infers_industry_when_router_failed() -> None:
    result = resolve_scholar_direction(
        "",
        SimpleNamespace(success=False),
        names=["安徽广播电视台"],
    )

    assert result.source == "industry_default"
    assert result.direction == "broadcasting technology media convergence"
