"""Candidate retries must spend browser work on distinct, usable sources."""
from contextlib import asynccontextmanager

import pytest

from api.services import persona_research_browser as browser_module
from api.services.persona_research_browser import ResearchCandidate, ResearchPage


def candidate(url):
    return ResearchCandidate(url=url, title="岗位研究", snippet="公开行业资料", query="行业 岗位")


def setup_browser(monkeypatch, candidates, failures=()):
    visited, released = [], []

    class Provider:
        async def get_cdp_endpoint(self, **kwargs):
            return "ws://browser.test/cdp"

        async def release_cdp_endpoint(self, task_id):
            released.append(task_id)

    class Client:
        def __init__(self, connections):
            pass

        @asynccontextmanager
        async def session(self, name):
            yield object()

    async def discover(session, queries, **kwargs):
        return [candidates]

    async def read(session, item):
        visited.append(item.url)
        if item.url in failures:
            raise ValueError("页面正文不足")
        return ResearchPage(url=item.url, title=item.title, publisher="行业资料", description="岗位研究", text="正文" * 400, query=item.query)

    monkeypatch.setattr(browser_module, "get_browser_provider", Provider)
    monkeypatch.setattr(browser_module, "MultiServerMCPClient", Client)
    monkeypatch.setattr(browser_module, "_build_worker_chrome_config", lambda *a: {})
    monkeypatch.setattr(browser_module, "build_mcp_connections", lambda *a, **k: {})
    browser = browser_module.ChromeDevtoolsPersonaResearchBrowser()
    monkeypatch.setattr(browser, "_discover", discover)
    monkeypatch.setattr(browser, "_read_page", read)
    return browser, visited, released


@pytest.mark.asyncio
async def test_failed_duplicate_urls_do_not_exhaust_distinct_candidate_budget(monkeypatch):
    broken = "https://broken.example/article"
    rows = [candidate(broken)] * 24 + [candidate(f"https://source{i}.example/article") for i in range(12)]
    browser, visited, released = setup_browser(monkeypatch, rows, failures={broken})
    pages = await browser.collect(object(), search_queries=["行业 岗位"], task_id="research-1", research_key="research-1")
    assert len(pages) == 12
    assert visited.count(broken) == 1
    assert released == ["persona_evidence_research-1"]


@pytest.mark.asyncio
async def test_full_host_budget_is_skipped_before_browser_navigation(monkeypatch):
    rows = [candidate(f"https://same.example/article/{i}") for i in range(20)]
    rows += [candidate(f"https://source{i}.example/article") for i in range(12)]
    browser, visited, _ = setup_browser(monkeypatch, rows)
    pages = await browser.collect(object(), search_queries=["行业 岗位"], task_id="research-2", research_key="research-2")
    assert len(pages) == 12
    assert sum("same.example" in url for url in visited) == 2


@pytest.mark.asyncio
async def test_bounded_extra_candidates_can_complete_required_evidence(monkeypatch):
    broken = {f"https://broken{i}.example/article" for i in range(24)}
    rows = [candidate(url) for url in sorted(broken)]
    rows += [candidate(f"https://source{i}.example/article") for i in range(12)]
    browser, visited, _ = setup_browser(monkeypatch, rows, failures=broken)
    pages = await browser.collect(object(), search_queries=["行业 岗位"], task_id="research-3", research_key="research-3")
    assert len(pages) == 12
    assert len(visited) == 36


@pytest.mark.asyncio
async def test_source_shortage_still_fails_and_releases_browser(monkeypatch):
    rows = [candidate(f"https://source{i}.example/article") for i in range(7)]
    browser, _, released = setup_browser(monkeypatch, rows)
    with pytest.raises(RuntimeError, match="少于要求的 8"):
        await browser.collect(object(), search_queries=["行业 岗位"], task_id="research-4", research_key="research-4")
    assert released == ["persona_evidence_research-4"]
