import asyncio
from contextlib import asynccontextmanager

import pytest

from Sere1nGraph.graph.tools.mcp_session import (
    McpRecoveryExhausted,
    RecoveringMcpSession,
)


class Client:
    def __init__(self, *, blocked=(), blocked_url=""):
        self.blocked = set(blocked)
        self.blocked_url = blocked_url
        self.opened = []
        self.closed = []
        self.calls = []

    @asynccontextmanager
    async def session(self, name):
        number = len(self.opened)
        self.opened.append(number)
        owner = asyncio.current_task()
        client = self

        class Session:
            async def call_tool(self, tool, arguments):
                client.calls.append((number, tool, arguments))
                if number in client.blocked and (
                    not client.blocked_url or arguments.get("url") == client.blocked_url
                ):
                    await asyncio.Future()
                return {"session": number, "arguments": arguments}

        try:
            yield Session()
        finally:
            assert asyncio.current_task() is owner
            self.closed.append(number)


@pytest.mark.asyncio
async def test_timeout_replaces_session_before_next_call_without_replaying_action():
    client = Client(blocked={0})
    async with RecoveringMcpSession(client, "chrome", call_timeout=0.01) as session:
        with pytest.raises(TimeoutError, match="navigate_page"):
            await session.call_tool("navigate_page", {"url": "https://broken.example"})
        assert client.closed == [0]
        result = await session.call_tool("navigate_page", {"url": "https://good.example"})
        assert result["session"] == 1
        assert len(client.calls) == 2
    assert client.closed == [0, 1]


@pytest.mark.asyncio
async def test_successful_calls_reuse_one_session():
    client = Client()
    async with RecoveringMcpSession(client, "chrome") as session:
        await session.call_tool("navigate_page", {})
        await session.call_tool("evaluate_script", {})
    assert client.opened == [0]
    assert client.closed == [0]


@pytest.mark.asyncio
async def test_persona_keeps_saved_pages_when_a_later_source_needs_new_session(monkeypatch):
    from api.services import persona_research_browser as browser_module

    client = Client(blocked={0}, blocked_url="https://broken.example")
    browser = browser_module.ChromeDevtoolsPersonaResearchBrowser()

    async def read(session, candidate):
        await session.call_tool("navigate_page", {"url": candidate.url})
        return browser_module.ResearchPage(
            url=candidate.url, title="公开职业资料", publisher="行业协会",
            description="背景", text="正文" * 400, query="行业 岗位",
        )

    monkeypatch.setattr(browser, "_read_page", read)
    urls = [f"https://source{i}.example" for i in range(12)]
    urls.insert(1, "https://broken.example")
    candidates = [browser_module.ResearchCandidate(url, "岗位", "职责", "行业") for url in urls]
    async with RecoveringMcpSession(client, "chrome", call_timeout=0.01) as session:
        pages = await browser._read_candidates(session, candidates, excluded=set(), task_id="research")
    assert len(pages) == 12
    assert pages[0].url == "https://source0.example"
    assert client.opened == [0, 1]
    assert client.closed == [0, 1]


@pytest.mark.asyncio
async def test_reconnection_budget_is_finite():
    client = Client(blocked={0, 1, 2})
    async with RecoveringMcpSession(client, "chrome", call_timeout=0.01, max_reconnects=1) as session:
        for _ in range(2):
            with pytest.raises(TimeoutError):
                await session.call_tool("navigate_page", {})
        with pytest.raises(McpRecoveryExhausted, match="1"):
            await session.call_tool("navigate_page", {})
    assert client.opened == [0, 1]
    assert client.closed == [0, 1]


@pytest.mark.asyncio
async def test_cancellation_releases_session_and_does_not_reconnect():
    client = Client(blocked={0})

    async def run():
        async with RecoveringMcpSession(client, "chrome") as session:
            await session.call_tool("navigate_page", {})

    task = asyncio.create_task(run())
    while not client.calls:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.opened == [0]
    assert client.closed == [0]
