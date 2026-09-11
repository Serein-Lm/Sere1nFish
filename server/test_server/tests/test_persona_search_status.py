import pytest

from api.services import persona_research_browser as browser_module
from api.services.persona_research_search import PersonaSearchSource, SearchSourceBlocked


@pytest.mark.parametrize("url,title", [
    ("https://qcaptcha.so.com/?ret=example", "访问异常页面"),
    ("https://search.example/challenge/123", "Check"),
    ("https://search.example/", "安全验证"),
])
def test_challenge_is_explicitly_unavailable(url, title):
    source = PersonaSearchSource("test", "https://search.example/?q=", ())
    with pytest.raises(SearchSourceBlocked, match="人工验证"):
        source.validate_response({"url": url, "title": title})


def test_query_about_captcha_is_not_treated_as_a_challenge():
    source = PersonaSearchSource("test", "https://search.example/?q=", ())
    source.validate_response({"url": "https://search.example/?q=captcha", "title": "验证码开发 - 搜索结果"})


@pytest.mark.asyncio
async def test_blocked_source_is_skipped_for_remaining_queries(monkeypatch):
    sources = (PersonaSearchSource("blocked", "", ()), PersonaSearchSource("ready", "", ()))
    monkeypatch.setattr(browser_module, "persona_search_sources", lambda: sources)
    calls = []
    browser = browser_module.ChromeDevtoolsPersonaResearchBrowser()

    async def search(session, source, query):
        calls.append(source.name)
        if source.name == "blocked":
            raise SearchSourceBlocked("blocked 要求人工验证")
        return [browser_module.ResearchCandidate("https://source.example/" + query, query, "", query)]

    monkeypatch.setattr(browser, "_search_candidates", search)
    buckets = await browser._discover(object(), ["岗位1", "岗位2"])
    assert calls == ["blocked", "ready", "ready"]
    assert len(buckets) == 2


@pytest.mark.asyncio
async def test_no_results_preserve_human_verification_reason(monkeypatch):
    source = PersonaSearchSource("blocked", "", ())
    monkeypatch.setattr(browser_module, "persona_search_sources", lambda: (source,))
    browser = browser_module.ChromeDevtoolsPersonaResearchBrowser()

    async def search(*args):
        raise SearchSourceBlocked("blocked 要求人工验证")

    monkeypatch.setattr(browser, "_search_candidates", search)
    with pytest.raises(RuntimeError, match="人工验证"):
        await browser._discover(object(), ["岗位1", "岗位2"])
