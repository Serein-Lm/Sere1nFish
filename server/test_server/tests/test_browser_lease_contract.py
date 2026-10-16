import asyncio
import time
from datetime import datetime, timedelta

import pytest

from browser_manager.provider import ChromeDockerConfig, ContainerInfo, DockerProvider


def pool(monkeypatch, size=2):
    provider = DockerProvider(ChromeDockerConfig(max_containers=size, reserved_non_bulk_containers=0))
    provider.containers = {str(i): ContainerInfo(str(i), f"chrome-{i}", "chrome", 9222, 8250, 5900, 6080,
                                               status="idle", last_memory_check_at=datetime.now()) for i in range(size)}

    async def endpoint(info):
        return "ws://" + info.container_name

    monkeypatch.setattr(provider, "_get_ws_url", endpoint)
    return provider


@pytest.mark.asyncio
async def test_duplicate_legacy_identity_is_explicitly_rejected_without_orphan(monkeypatch):
    provider = pool(monkeypatch)
    await provider.get_cdp_endpoint("same", purpose="url_scan")
    with pytest.raises(RuntimeError, match="租约"):
        await provider.get_cdp_endpoint("same", purpose="url_scan")
    await provider.release_cdp_endpoint("same")
    await provider.release_cdp_endpoint("same")
    assert provider.task_map == {}
    assert all(info.status == "idle" for info in provider.containers.values())
    assert provider._bulk_slots.in_use == 0


@pytest.mark.asyncio
async def test_explicit_owner_reuses_one_lease_and_stale_release_cannot_free_successor(monkeypatch):
    from browser_manager.leases import BrowserLeaseRequest

    provider = pool(monkeypatch)
    request = BrowserLeaseRequest(lease_id="same", purpose="url_scan")
    first = await provider.get_cdp_endpoint(request=request)
    assert await provider.get_cdp_endpoint(request=request) == first
    assert sum(info.status == "busy" for info in provider.containers.values()) == 1
    await provider.release_cdp_endpoint("same", owner_token=request.owner_token)
    successor = BrowserLeaseRequest(lease_id="same", purpose="url_scan")
    await provider.get_cdp_endpoint(request=successor)
    await provider.release_cdp_endpoint("same", owner_token=request.owner_token)
    assert "same" in provider.task_map
    assert provider._bulk_slots.in_use == 1
    await provider.release_cdp_endpoint("same", owner_token=successor.owner_token)


@pytest.mark.asyncio
async def test_concurrent_duplicate_does_not_disturb_pending_owner(monkeypatch):
    provider = pool(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def endpoint(info):
        entered.set()
        await release.wait()
        return "ws://chrome"

    monkeypatch.setattr(provider, "_get_ws_url", endpoint)
    first = asyncio.create_task(provider.get_cdp_endpoint("same", purpose="url_scan"))
    await entered.wait()
    try:
        with pytest.raises(RuntimeError, match="租约"):
            await asyncio.wait_for(provider.get_cdp_endpoint("same", purpose="url_scan"), timeout=0.2)
        assert provider._bulk_slots.in_use == 1
    finally:
        release.set()
        await first
        await provider.release_cdp_endpoint("same")
    assert sum(info.status == "busy" for info in provider.containers.values()) == 0


@pytest.mark.asyncio
async def test_healthy_long_research_obeys_declared_deadline(monkeypatch):
    from browser_manager.leases import BrowserLeaseRequest

    provider = pool(monkeypatch, 1)
    request = BrowserLeaseRequest(lease_id="research", purpose="target_research", execution_deadline=time.monotonic()+3600)
    await provider.get_cdp_endpoint(request=request)
    info = next(iter(provider.containers.values()))
    info.last_used_at = datetime.now() - timedelta(seconds=1810)
    restarts = []

    async def healthy(info):
        return True, ""

    async def restart(info, **kwargs):
        restarts.append(kwargs)

    monkeypatch.setattr(provider, "_query_cdp_health", healthy)
    monkeypatch.setattr(provider, "_restart_chrome_for_recovery", restart)
    await provider._inspect_container_health(info.container_id, info)
    assert restarts == []
    assert provider.task_map["research"] == info.container_id
    await provider.release_cdp_endpoint("research")


@pytest.mark.asyncio
async def test_departed_explicit_owner_releases_its_lease(monkeypatch):
    from browser_manager.leases import BrowserLeaseRequest

    provider = pool(monkeypatch, 1)
    owner = asyncio.create_task(asyncio.sleep(0))
    request = BrowserLeaseRequest(lease_id="research", owner_task=owner)
    await provider.get_cdp_endpoint(request=request)
    await owner
    info = next(iter(provider.containers.values()))

    async def healthy(info):
        return True, ""

    monkeypatch.setattr(provider, "_query_cdp_health", healthy)
    await provider._inspect_container_health(info.container_id, info)
    assert provider.task_map == {}
    assert info.status == "idle"


@pytest.mark.asyncio
async def test_unified_acquisition_deadline_cancels_wait_and_frees_quota(monkeypatch):
    from browser_manager.leases import BrowserLeaseRequest

    provider = pool(monkeypatch, 1)
    await provider.get_cdp_endpoint("occupied", purpose="url_scan")
    request = BrowserLeaseRequest(lease_id="waiter", purpose="url_scan", wait_timeout_seconds=0.02)
    with pytest.raises(TimeoutError):
        await provider.get_cdp_endpoint(request=request)
    assert provider._bulk_slots.in_use == 1
    assert "waiter" not in provider.task_map
    await provider.release_cdp_endpoint("occupied")


@pytest.mark.asyncio
async def test_execution_budget_cancels_explicit_owner_then_reclaims(monkeypatch):
    from browser_manager.leases import BrowserLeaseRequest

    provider = pool(monkeypatch, 1)
    owner = asyncio.create_task(asyncio.Event().wait())
    request = BrowserLeaseRequest(lease_id="research", owner_task=owner, execution_deadline=time.monotonic()+3600)
    await provider.get_cdp_endpoint(request=request)
    provider._leases.get("research").deadline = time.monotonic()-1
    info = next(iter(provider.containers.values()))

    async def healthy(info):
        return True, ""

    monkeypatch.setattr(provider, "_query_cdp_health", healthy)
    await provider._inspect_container_health(info.container_id, info)
    with pytest.raises(asyncio.CancelledError):
        await owner
    await provider._inspect_container_health(info.container_id, info)
    assert provider.task_map == {}
    assert info.status == "idle"


@pytest.mark.asyncio
async def test_long_lease_still_recovers_confirmed_cdp_failure(monkeypatch):
    from browser_manager.leases import BrowserLeaseRequest

    provider = pool(monkeypatch, 1)
    request = BrowserLeaseRequest(lease_id="research", execution_deadline=time.monotonic()+3600)
    await provider.get_cdp_endpoint(request=request)
    info = next(iter(provider.containers.values()))
    restarts = []

    async def unhealthy(info):
        return False, "disconnected"

    async def restart(info, **kwargs):
        restarts.append(kwargs)

    monkeypatch.setattr(provider, "_query_cdp_health", unhealthy)
    monkeypatch.setattr(provider, "_restart_chrome_for_recovery", restart)
    await provider._inspect_container_health(info.container_id, info)
    await provider._inspect_container_health(info.container_id, info)
    assert len(restarts) == 1
    await provider.release_cdp_endpoint("research")


@pytest.mark.asyncio
async def test_hot_swap_preserves_execution_deadline_and_owner(monkeypatch):
    from browser_manager.leases import BrowserLeaseRequest

    provider = pool(monkeypatch, 2)
    request = BrowserLeaseRequest(lease_id="research", execution_deadline=time.monotonic()+3600)
    first = await provider.get_cdp_endpoint(request=request)
    removed = asyncio.Event()

    async def destroy(container_id):
        provider.containers.pop(container_id, None)
        removed.set()

    monkeypatch.setattr(provider, "_destroy_container", destroy)
    second = await provider.hot_swap_container("research")
    await asyncio.wait_for(removed.wait(), 1)
    assert second != first
    assert provider._leases.get("research").request == request
    assert provider._leases.get("research").deadline == request.execution_deadline
    await provider.release_cdp_endpoint("research", owner_token=request.owner_token)
