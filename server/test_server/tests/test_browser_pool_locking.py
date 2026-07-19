from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from browser_manager.provider import ChromeDockerConfig, ContainerInfo, DockerProvider


@pytest.mark.asyncio
async def test_capacity_wait_does_not_block_release(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = DockerProvider(ChromeDockerConfig(max_containers=1))
    provider._lock = asyncio.Lock()
    provider._pending_creates = 0
    provider.task_map = {"old-task": "container-1"}
    provider.containers = {
        "container-1": ContainerInfo(
            container_id="container-1",
            container_name="chrome-test",
            cdp_host="127.0.0.1",
            cdp_port=9222,
            api_port=8250,
            vnc_port=5900,
            novnc_port=6080,
            status="busy",
            task_id="old-task",
            last_used_at=datetime.now(),
        )
    }

    async def endpoint(_info: ContainerInfo) -> str:
        return "ws://127.0.0.1:8250/cdp-proxy"

    monkeypatch.setattr(provider, "_get_ws_url", endpoint)
    waiter = asyncio.create_task(
        provider.get_cdp_endpoint(task_id="new-task", purpose="url_scan")
    )
    await asyncio.sleep(0.05)

    await asyncio.wait_for(provider.release_cdp_endpoint("old-task"), timeout=0.2)
    result = await asyncio.wait_for(waiter, timeout=2.0)

    assert result == "ws://127.0.0.1:8250/cdp-proxy"
    assert provider.task_map["new-task"] == "container-1"
    assert provider.containers["container-1"].purpose == "url_scan"


@pytest.mark.asyncio
async def test_container_startup_does_not_block_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DockerProvider(ChromeDockerConfig(max_containers=2))
    provider._lock = asyncio.Lock()
    provider._pending_creates = 0
    provider.task_map = {"old-task": "container-1"}
    provider.containers = {
        "container-1": ContainerInfo(
            container_id="container-1",
            container_name="chrome-old",
            cdp_host="127.0.0.1",
            cdp_port=9222,
            api_port=8250,
            vnc_port=5900,
            novnc_port=6080,
            status="busy",
            task_id="old-task",
        )
    }
    creation_started = asyncio.Event()
    allow_creation = asyncio.Event()

    async def create_container() -> ContainerInfo:
        creation_started.set()
        await allow_creation.wait()
        return ContainerInfo(
            container_id="container-2",
            container_name="chrome-new",
            cdp_host="127.0.0.1",
            cdp_port=9223,
            api_port=8251,
            vnc_port=5901,
            novnc_port=6081,
        )

    async def endpoint(info: ContainerInfo) -> str:
        return f"ws://{info.cdp_host}:{info.api_port}/cdp-proxy"

    monkeypatch.setattr(provider, "_create_container", create_container)
    monkeypatch.setattr(provider, "_get_ws_url", endpoint)
    monkeypatch.setattr(provider, "_ensure_background_tasks", lambda: None)

    creator = asyncio.create_task(
        provider.get_cdp_endpoint(task_id="new-task", purpose="url_scan")
    )
    await asyncio.wait_for(creation_started.wait(), timeout=0.2)
    await asyncio.wait_for(provider.release_cdp_endpoint("old-task"), timeout=0.2)
    allow_creation.set()

    assert await asyncio.wait_for(creator, timeout=0.5) == (
        "ws://127.0.0.1:8251/cdp-proxy"
    )
    assert provider._pending_creates == 0


@pytest.mark.asyncio
async def test_url_scan_capacity_leaves_room_for_wechat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DockerProvider(
        ChromeDockerConfig(
            max_containers=3,
            reserved_non_bulk_containers=1,
        )
    )
    provider.containers = {
        f"container-{index}": ContainerInfo(
            container_id=f"container-{index}",
            container_name=f"chrome-{index}",
            cdp_host="127.0.0.1",
            cdp_port=9221 + index,
            api_port=8249 + index,
            vnc_port=5899 + index,
            novnc_port=6079 + index,
            status="idle",
        )
        for index in range(1, 4)
    }

    async def endpoint(info: ContainerInfo) -> str:
        return f"ws://{info.cdp_host}:{info.api_port}/cdp-proxy"

    monkeypatch.setattr(provider, "_get_ws_url", endpoint)

    assert await provider.get_cdp_endpoint("url-1", purpose="url_scan")
    assert await provider.get_cdp_endpoint("url-2", purpose="url_scan")

    waiting_url = asyncio.create_task(
        provider.get_cdp_endpoint("url-3", purpose="url_scan")
    )
    await asyncio.sleep(0.05)
    assert not waiting_url.done()

    assert await asyncio.wait_for(
        provider.get_cdp_endpoint("wechat-1", purpose="wechat_article"),
        timeout=0.2,
    )

    await provider.release_cdp_endpoint("wechat-1")
    assert not waiting_url.done()
    await provider.release_cdp_endpoint("url-1")
    assert await asyncio.wait_for(waiting_url, timeout=0.2)

    await provider.release_cdp_endpoint("url-2")
    await provider.release_cdp_endpoint("url-3")


@pytest.mark.asyncio
async def test_cancelled_url_scan_waiter_does_not_leak_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DockerProvider(
        ChromeDockerConfig(
            max_containers=2,
            reserved_non_bulk_containers=1,
        )
    )
    provider.containers = {
        "container-1": ContainerInfo(
            container_id="container-1",
            container_name="chrome-1",
            cdp_host="127.0.0.1",
            cdp_port=9222,
            api_port=8250,
            vnc_port=5900,
            novnc_port=6080,
            status="idle",
        )
    }

    async def endpoint(_info: ContainerInfo) -> str:
        return "ws://127.0.0.1:8250/cdp-proxy"

    monkeypatch.setattr(provider, "_get_ws_url", endpoint)

    assert await provider.get_cdp_endpoint("url-1", purpose="url_scan")
    cancelled = asyncio.create_task(
        provider.get_cdp_endpoint("url-cancelled", purpose="url_scan")
    )
    await asyncio.sleep(0.05)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    await provider.release_cdp_endpoint("url-1")
    assert await asyncio.wait_for(
        provider.get_cdp_endpoint("url-2", purpose="url_scan"),
        timeout=0.2,
    )
    await provider.release_cdp_endpoint("url-2")


@pytest.mark.asyncio
async def test_destroy_removes_stale_task_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DockerProvider(ChromeDockerConfig(max_containers=1))
    provider._lock = asyncio.Lock()
    provider.task_map = {"stale-task": "container-1"}
    provider.containers = {
        "container-1": ContainerInfo(
            container_id="container-1",
            container_name="chrome-stale",
            cdp_host="127.0.0.1",
            cdp_port=9222,
            api_port=8250,
            vnc_port=5900,
            novnc_port=6080,
            status="stopping",
            task_id="stale-task",
        )
    }

    async def remove_container(_container_id: str, _container_name: str) -> None:
        return None

    monkeypatch.setattr(provider, "_remove_docker_container", remove_container)
    await provider._destroy_container("container-1")

    assert provider.containers == {}
    assert provider.task_map == {}


@pytest.mark.asyncio
async def test_cdp_endpoint_failure_recovers_before_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DockerProvider(ChromeDockerConfig(max_containers=1))
    info = ContainerInfo(
        container_id="container-1",
        container_name="chrome-broken",
        cdp_host="127.0.0.1",
        cdp_port=9222,
        api_port=8250,
        vnc_port=5900,
        novnc_port=6080,
        status="idle",
    )
    provider.containers = {info.container_id: info}
    events: list[str] = []

    async def endpoint(_info: ContainerInfo) -> str:
        raise RuntimeError("CDP unavailable")

    async def recover(*, task_id: str | None, reason: str) -> bool:
        events.append(f"recover:{task_id}:{reason}")
        return False

    monkeypatch.setattr(provider, "_get_ws_url", endpoint)
    monkeypatch.setattr(provider, "recover_task_container", recover)

    with pytest.raises(RuntimeError, match="CDP unavailable"):
        await provider.get_cdp_endpoint("task-1", purpose="wechat_article")

    assert events == ["recover:task-1:Chrome CDP 端点连续获取失败"]
    assert provider.task_map == {}
    assert info.status == "idle"
    assert info.cdp_healthy is False
