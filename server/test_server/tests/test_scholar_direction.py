from __future__ import annotations

from types import SimpleNamespace

from api.services.scholar_direction import resolve_scholar_direction


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
