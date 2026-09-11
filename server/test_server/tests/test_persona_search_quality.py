import json
from urllib.parse import parse_qs, urlsplit

import pytest

from api.services import persona_research_browser as browser_module
from api.services.persona_research_search import candidate_matches_query, is_search_result_url, persona_search_sources


@pytest.mark.parametrize("query,title,snippet,expected", [
    ("中小学教师职业标准 工作职责 中国", "中（汉语汉字）", "中国简称，古今中外", False),
    ("全科医生 职业标准 工作流程 中国", "全的意思", "字典释义", False),
    ("软件工程师 岗位职责 工作流程", "虎牙直播", "游戏直播软件官方下载", False),
    ("软件工程师 岗位职责 工作流程", "软件工程师岗位职责", "研发团队工作规范", True),
    ("中小学教师职业标准 工作职责", "小学教师专业标准", "岗位职责与教学规范", True),
    ("IT项目经理 职责 技能要求", "项目经理岗位说明", "项目交付与风险管理", True),
    ("software engineer career requirements", "Software Download", "Download software", False),
    ("software engineer career requirements", "Software engineering careers", "Engineer skills and requirements", True),
])
def test_candidate_topic_guard(query, title, snippet, expected):
    assert candidate_matches_query(query, title, snippet) is expected


@pytest.mark.asyncio
async def test_unrelated_primary_results_use_registered_fallback(monkeypatch):
    visited = []
    async def call(session, name, arguments):
        if name == "navigate_page":
            visited.append(arguments["url"])
            return "OK"
        if "bing.com" in visited[-1]:
            items = [{"url": "https://dictionary.example/word", "title": "中字释义", "snippet": "中国简称"}]
        else:
            items = [{"url": f"https://school{i}.example/standard", "title": "小学教师专业标准", "snippet": "教学岗位规范"} for i in range(8)]
        return json.dumps({"items": items}, ensure_ascii=False)

    monkeypatch.setattr(browser_module, "_call_mcp", call)
    browser = browser_module.ChromeDevtoolsPersonaResearchBrowser()
    buckets = await browser._discover(object(), ["中小学教师职业标准 工作职责"])
    assert len(buckets[0]) == 8
    assert all("dictionary" not in item.url for item in buckets[0])
    assert len(visited) == 2
    assert "bing.com" in visited[0] and "so.com" in visited[1]


def test_all_sources_preserve_the_complete_unicode_query():
    query = "软件工程师 岗位职责 & 团队协作"
    for source in persona_search_sources():
        assert parse_qs(urlsplit(source.search_url(query)).query)["q"] == [query]


@pytest.mark.parametrize("url,expected", [
    ("https://cn.bing.com/search?q=role", True),
    ("https://www.so.com/s?q=role", True),
    ("https://wenku.so.com/s?q=role", True),
    ("https://image.so.com/i?q=role", True),
    ("https://wenku.so.com/d/document", False),
    ("https://www.so.com/link?m=result", False),
])
def test_search_result_pages_cannot_be_archived_as_sources(url, expected):
    assert is_search_result_url(url) is expected


@pytest.mark.asyncio
async def test_search_timeout_can_use_next_source(monkeypatch):
    browser = browser_module.ChromeDevtoolsPersonaResearchBrowser()
    called = []
    async def search(session, source, query):
        called.append(source.name)
        if len(called) == 1:
            raise TimeoutError("search timed out")
        return [browser_module.ResearchCandidate("https://school.example/standard", "小学教师标准", "", query)]
    monkeypatch.setattr(browser, "_search_candidates", search)
    buckets = await browser._discover(object(), ["中小学教师标准"])
    assert len(called) == 2 and len(buckets[0]) == 1
