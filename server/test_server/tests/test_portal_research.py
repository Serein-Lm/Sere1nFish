import asyncio
import json

import pytest
from pydantic import ValidationError

from api.models.portal_research import PortalResearchOptions
from api.services.portal_research import PortalResearchSession
from api.services.source_documents.resources import html_text_and_links
from Sere1nGraph.graph.agents.portal_research import PAGE_READ, PortalBrowserPolicy


def policy(**options):
    return PortalBrowserPolicy(['example.cn'], PortalResearchOptions(**options))


def test_friend_links_are_excluded_from_static_html_and_browser_navigation():
    markup = '<main><section><h2>友情链接</h2><a href="https://friend.cn/">友好机构</a></section><section><h2>直属单位</h2><a href="https://child.cn/">直属服务中心</a></section><a href="/jobs">招聘</a></main>'
    _, links = html_text_and_links(markup, 'https://example.cn/')
    assert {link['url'] for link in links} == {'https://child.cn/', 'https://example.cn/jobs'}
    browser = policy()
    browser.observe_page({'url': 'https://example.cn/', 'links': [
        {'url': 'https://friend.cn/', 'title': '友好机构', 'context': '友情链接'},
        {'url': 'https://child.cn/', 'title': '直属服务中心', 'context': '直属单位'},
        {'url': 'https://vendor.cn/', 'title': '供应商', 'context': ''},
    ]})
    assert '友情链接' in browser.guard('navigate_page', (), {'url': 'https://friend.cn/#x'})
    assert '友情链接' in browser.guard('navigate_page', (), {'url': 'http://friend.cn/rewritten'})
    assert browser.guard('navigate_page', (), {'url': 'https://child.cn/'}) is None
    assert browser.guard('navigate_page', (), {'url': 'https://vendor.cn/'})


def test_disabled_relationships_block_even_same_root_subdomains():
    browser = policy(include_subordinates=False, include_parent=False)
    browser.observe_page({'url': 'https://example.cn/', 'links': [
        {'url': 'https://child.example.cn/', 'title': '直属服务中心', 'context': '直属单位'},
        {'url': 'https://parent.gov.cn/', 'title': '主管机构', 'context': '主管单位'},
    ]})
    assert browser.guard('navigate_page', (), {'url': 'https://child.example.cn/'})
    assert browser.guard('navigate_page', (), {'url': 'http://child.example.cn/about'})
    assert browser.guard('navigate_page', (), {'url': 'https://parent.gov.cn/'})
    assert browser.guard('navigate_page', (), {'url': 'https://example.cn/jobs'}) is None


def test_read_script_is_fixed_and_navigation_is_bounded():
    browser = policy(max_pages=10)
    args, kwargs = browser.transform('evaluate_script', (), {'function': "() => fetch('/send')"})
    assert not args and kwargs['function'] == PAGE_READ
    for i in range(10):
        assert browser.guard('navigate_page', (), {'url': f'https://example.cn/{i}'}) is None
    assert '预算' in browser.guard('navigate_page', (), {'url': 'https://example.cn/next'})
    assert browser.guard('navigate_page', (), {'url': 'file:///etc/passwd'})
    with pytest.raises(ValidationError):
        PortalResearchOptions(max_runtime_seconds=999999)


def test_relation_projection_requires_nearby_direct_evidence_and_excludes_friends():
    session = PortalResearchSession(None, 't', 'p', {'target_id': 'a', 'root_domains': ['example.cn']}, {})
    session.browser.blocked_names.add('友好机构')
    session.pages = {'https://example.cn/units': {'text': '直属单位：直属服务中心。友情链接：友好机构。'}}
    candidates = [{ 'name': name, 'relation_type': relation, 'should_scan': True, 'source_urls': ['https://example.cn/units']} for name, relation in [('直属服务中心', 'service_unit'), ('友好机构', 'service_unit'), ('主管机构', 'parent_organization')]]
    value = session.restrict({'sources': [{'url': 'https://example.cn/units'}], 'related_targets': candidates,
                              'portal_sections': [{'category': 'business', 'status': 'covered', 'source_urls': ['https://unknown.cn/']}]})
    assert [item['should_scan'] for item in value['related_targets']] == [True, False, False]
    assert len(value['portal_sections']) == 6
    assert value['portal_sections'][0]['status'] == 'partial'


def test_read_checkpoint_is_awaited_and_dry_run_does_not_archive(monkeypatch):
    from api.dao import portal_research as dao
    from api.services import task_progress
    stored = []

    async def save(_db, _task, page):
        await asyncio.sleep(0)
        stored.append(page)

    async def noop(*_args, **_kwargs):
        pass

    monkeypatch.setattr(dao, 'save_page', save)
    monkeypatch.setattr(task_progress, 'update_task_stage', noop)

    async def run():
        target = {'target_id': 'a', 'root_domains': ['example.cn']}
        session = PortalResearchSession(None, 't', 'p', target, {})
        page = {'url': 'https://example.cn/about', 'title': '机构介绍', 'text': '公开业务资料', 'links': []}
        await session.observer(lambda *_args: None)('evaluate_script', json.dumps(page))
        assert len(stored) == 1 and session.saved == {page['url']}
        preview = PortalResearchSession(None, 't', 'p', target, {'dry_run': True})
        await preview.observer(lambda *_args: None)('evaluate_script', json.dumps(page))
        await preview.flush()
        data = {'sources': [{'url': page['url']}]}
        assert await preview.archive(data) is data
        assert len(stored) == 1
    asyncio.run(run())


def test_portal_agent_uses_extended_budget_without_changing_standard_mode(monkeypatch):
    import Sere1nGraph.graph.agents.factory as factory
    captured = {}
    monkeypatch.setattr(factory, 'create_agent_node', lambda **kwargs: captured.update(kwargs) or 'agent')
    class Model:
        _llm_type = 'fake-chat'

        def with_retry(self):
            return self

    monkeypatch.setattr(factory, 'create_llm', lambda *_args, **_kwargs: Model())
    session = PortalResearchSession(None, 't', 'p', {'root_domains': ['example.cn']}, {})
    result = asyncio.run(factory.create_target_research_agent(object(), research_session=session))
    assert result == 'agent'
    assert 3500 < captured['timeout'] <= 3600
    assert captured['mcp_tool_limit'] == 120
    assert captured['mcp_tool_names'] == ('navigate_page', 'evaluate_script')
    assert '不能读取两三个来源后提前停止' in captured['system_prompt']


@pytest.mark.asyncio
@pytest.mark.parametrize('sync_tool', [False, True])
async def test_runtime_waits_for_async_checkpoint_before_returning_tool_result(sync_tool):
    from langchain_core.tools import StructuredTool
    from Sere1nGraph.graph.agents.runtime import _wrap_tools_with_error_handling

    observed = []

    async def read_async():
        return 'page'

    async def observer(name, result):
        await asyncio.sleep(0)
        observed.append((name, result))

    tool = StructuredTool.from_function(
        name='read', description='read evidence',
        **({'func': lambda: 'page'} if sync_tool else {'coroutine': read_async}),
    )
    wrapped = _wrap_tools_with_error_handling([tool], result_observer=observer)[0]
    assert await wrapped.ainvoke({}) == 'page'
    assert observed == [('read', 'page')]


@pytest.mark.asyncio
async def test_resume_restores_read_pages_and_relationship_navigation(monkeypatch):
    from api.dao import portal_research as dao

    page = {'url': 'https://example.cn/units', 'title': '直属单位', 'text': '直属单位：服务中心', 'links': [
        {'url': 'https://child.cn/', 'title': '服务中心', 'context': '直属单位'},
        {'url': 'https://friend.cn/', 'title': '友好机构', 'context': '友情链接'},
    ]}

    async def checkpoint(*_args):
        return {'pages': {'hash': page}}

    async def candidates(*_args):
        return [{'canonical_url': 'https://example.cn/about', 'title': '机构简介'}]

    monkeypatch.setattr(dao, 'load_checkpoint', checkpoint)
    monkeypatch.setattr(dao, 'source_candidates', candidates)
    session = PortalResearchSession(None, 't', 'p', {'target_id': 'a', 'root_domains': ['example.cn']}, {})
    await session.prepare()
    assert session.saved == {page['url']}
    assert session.browser.guard('navigate_page', (), {'url': 'https://child.cn/'}) is None
    assert '友情链接' in session.browser.guard('navigate_page', (), {'url': 'https://friend.cn/'})
    assert page['text'] in session.query('目标：旧简短研究要求')


@pytest.mark.asyncio
async def test_batch_preserves_portal_scope_and_budget(monkeypatch):
    from api.services import target_research as research, targets

    documents = []

    async def project(*_args, **_kwargs):
        return {'project_id': 'p'}

    async def resolve(*_args, **_kwargs):
        return {'target_id': 'a', 'canonical_name': '示例单位'}

    async def noop(*_args, **_kwargs):
        return None

    async def insert(_db, items):
        documents.extend(items)

    def background(coro, **_kwargs):
        coro.close()

    monkeypatch.setattr(research.projects_dao, 'get_project', project)
    monkeypatch.setattr(targets, 'resolve_target', resolve)
    monkeypatch.setattr(research.targets_dao, 'link_project_target', noop)
    monkeypatch.setattr(research.tasks_dao, 'find_latest_matching_task', noop)
    monkeypatch.setattr(research.tasks_dao, 'insert_tasks', insert)
    monkeypatch.setattr(research, 'spawn_background', background)
    await research.enqueue_target_research_batch(None, project_id='p', target_names=['示例单位'], requested_by='tester', portal_options={'include_parent': True, 'max_runtime_seconds': 7200})
    assert len(documents) == 1
    assert documents[0]['params']['portal_options']['include_parent'] is True
    assert documents[0]['params']['portal_options']['max_runtime_seconds'] == 7200


@pytest.mark.asyncio
async def test_rendered_discovery_filters_friend_section_and_releases_page(monkeypatch):
    from contextlib import asynccontextmanager
    import websockets
    from api.services import web_capture

    closed = []

    @asynccontextmanager
    async def connection(*_args, **_kwargs):
        yield object()

    async def command(_websocket, _id, method, **kwargs):
        if method == 'Target.createTarget': return {'targetId': 'owned-page'}
        if method == 'Target.attachToTarget': return {'sessionId': 'session'}
        if method == 'Target.closeTarget': closed.append(kwargs['params']['targetId'])
        if method == 'Runtime.evaluate': return {'result': {'value': {
            'href': 'https://example.cn/', 'readyState': 'complete',
            'html': '<main><section><h2>友情链接</h2><a href="https://friend.cn/">友好机构</a></section><a href="/jobs">招聘信息</a></main>',
            'links': [{'url': 'https://friend.cn/'}, {'url': 'https://example.cn/jobs'}],
        }}}
        return {}

    async def sleep(_seconds):
        pass

    monkeypatch.setattr(websockets, 'connect', connection)
    monkeypatch.setattr(web_capture, '_cdp_command', command)
    monkeypatch.setattr(web_capture.asyncio, 'sleep', sleep)
    result = await web_capture.capture_cdp_rendered_links('ws://owned-browser', 'https://example.cn/')
    assert [item['url'] for item in result['links']] == ['https://example.cn/jobs']
    assert closed == ['owned-page']


@pytest.mark.asyncio
async def test_browser_retries_share_remaining_time_budget_and_cancel(monkeypatch):
    import time
    session = PortalResearchSession(None, 't', 'p', {'root_domains': ['example.cn']}, {})
    called = []

    async def agent(_state):
        called.append(True)
        return 'report'

    session.deadline = time.monotonic() - 1
    with pytest.raises(TimeoutError):
        await session.run_agent(agent, {})
    assert not called

    released = []

    async def slow_agent(_state):
        try:
            await asyncio.Future()
        finally:
            released.append(True)

    monkeypatch.setattr(session, 'remaining_seconds', lambda: 0.01)
    with pytest.raises(TimeoutError):
        await session.run_agent(slow_agent, {})
    assert released == [True]


@pytest.mark.asyncio
async def test_archive_keeps_successful_versions_and_records_partial_failures(monkeypatch):
    from api.services import portal_research as service
    saved = []

    async def ingest(_db, *, url, **_kwargs):
        if url.endswith('/failed'): raise TimeoutError('source unavailable')
        return {'document_id': 'doc-1', 'version_id': 'version-1', 'archive_status': 'complete'}

    async def save(_db, _task_id, data):
        saved.append(data)

    monkeypatch.setattr(service, 'ingest_source_url', ingest)
    monkeypatch.setattr(service.dao, 'save_result', save)
    session = PortalResearchSession(None, 't', 'p', {'root_domains': ['example.cn']}, {})
    result = await session.archive({'sources': [{'url': 'https://example.cn/about'}, {'url': 'https://example.cn/failed'}]})
    assert result['portal_archive_pending'] == 1
    assert result['sources'][0]['source_document_version_id'] == 'version-1'
    assert result['sources'][1]['archive_status'] == 'pending'
    assert saved == [result]
