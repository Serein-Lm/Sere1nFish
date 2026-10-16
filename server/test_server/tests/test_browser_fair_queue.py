import asyncio

import pytest

from browser_manager.leases import BrowserLeaseRequest
from test_server.tests.test_browser_lease_contract import pool


@pytest.mark.asyncio
async def test_release_wakes_next_project_without_polling(monkeypatch):
    provider = pool(monkeypatch, 1)
    await provider.get_cdp_endpoint("busy", purpose="url_scan")
    request = BrowserLeaseRequest(lease_id="next", project_id="project", purpose="wechat_article")
    waiter = asyncio.create_task(provider.get_cdp_endpoint(request=request))
    await asyncio.sleep(0)
    await provider.release_cdp_endpoint("busy")
    assert await asyncio.wait_for(waiter, 0.2)
    await provider.release_cdp_endpoint("next", owner_token=request.owner_token)


@pytest.mark.asyncio
async def test_batch_projects_take_turns_and_cancelled_waiter_does_not_block(monkeypatch):
    provider = pool(monkeypatch, 1)
    await provider.get_cdp_endpoint("busy", purpose="url_scan")
    requests = [BrowserLeaseRequest(lease_id=f"a{i}", project_id="a", purpose="url_scan") for i in range(3)]
    requests += [BrowserLeaseRequest(lease_id="b", project_id="b", purpose="persona_research")]
    order = []

    async def acquire(request):
        await provider.get_cdp_endpoint(request=request)
        order.append(request.lease_id)

    tasks = [asyncio.create_task(acquire(request)) for request in requests]
    await asyncio.sleep(0)
    tasks[1].cancel()
    with pytest.raises(asyncio.CancelledError):
        await tasks[1]
    await provider.release_cdp_endpoint("busy")
    await asyncio.wait_for(tasks[0], 0.2)
    await provider.release_cdp_endpoint("a0", owner_token=requests[0].owner_token)
    await asyncio.wait_for(tasks[3], 0.2)
    await provider.release_cdp_endpoint("b", owner_token=requests[3].owner_token)
    await asyncio.wait_for(tasks[2], 0.2)
    await provider.release_cdp_endpoint("a2", owner_token=requests[2].owner_token)
    assert order == ["a0", "b", "a2"]
    assert provider.capacity_status()["waiting"] == 0
    assert provider._bulk_slots.in_use == 0


@pytest.mark.asyncio
async def test_shutdown_rejects_queued_requests_without_waiting_for_deadline(monkeypatch):
    provider = pool(monkeypatch, 1)
    await provider.get_cdp_endpoint("busy", purpose="url_scan")
    waiter = asyncio.create_task(provider.get_cdp_endpoint("waiting", purpose="url_scan"))
    await asyncio.sleep(0)

    async def remove(*args):
        return None

    monkeypatch.setattr(provider, "_remove_docker_container", remove)
    monkeypatch.setattr(provider, "_cleanup_orphan_containers", lambda: None)
    await provider.shutdown()
    with pytest.raises(RuntimeError, match="关闭"):
        await asyncio.wait_for(waiter, 0.2)
    assert provider.task_map == {}
