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


@pytest.mark.asyncio
async def test_pool_capacity_reconfigures_without_replacing_active_leases() -> None:
    provider = DockerProvider(
        ChromeDockerConfig(max_containers=3, reserved_non_bulk_containers=1)
    )
    provider._bulk_slot_owners.add("url-1")
    await provider._bulk_slots.acquire()

    status = await provider.reconfigure(
        ChromeDockerConfig(
            max_containers=6,
            reserved_non_bulk_containers=2,
            warm_pool_size=3,
        )
    )

    assert provider.config.max_containers == 6
    assert provider._bulk_slots.limit == 4
    assert provider._bulk_slots.in_use == 1
    assert status["configured_max_containers"] == 6
    assert status["bulk_limit"] == 4
    provider._release_workload_slot("url-1")


def test_pool_config_hard_caps_chrome_and_applies_resource_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ChromeDockerConfig.from_dict(
        {
            "max_containers": 999,
            "reserved_non_bulk_containers": 8,
            "warm_pool_size": 99,
            "host_memory_floor_mb": 8192,
        }
    )
    assert config.max_containers == 64
    assert config.warm_pool_size == 64

    provider = DockerProvider(config)
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
    monkeypatch.setattr(
        provider,
        "_host_resource_snapshot",
        lambda: {
            "available_memory_mb": 4096,
            "memory_floor_mb": 8192,
            "cpu_count": 32,
            "load_1m": 2.0,
            "load_per_cpu": 0.063,
            "load_per_cpu_limit": 1.5,
        },
    )

    status = provider.capacity_status()

    assert status["effective_max_containers"] == 1
    assert status["resource_guard"]["restricted"] is True
    assert "available_memory_below_floor" in status["resource_guard"]["reason"]


def test_pool_config_accepts_legacy_resource_guard_fields() -> None:
    config = ChromeDockerConfig.from_dict(
        {
            "min_available_memory_mb": 6144,
            "max_host_load_ratio": 0.9,
            "max_recoveries_per_minute": 15,
        }
    )

    assert config.host_memory_floor_mb == 6144
    assert config.host_load_per_cpu_limit == 0.9
    assert config.recent_cdp_failure_limit == 15
    assert config.recent_cdp_failure_window_seconds == 60


def test_pool_config_prefers_current_fields_over_legacy_aliases() -> None:
    config = ChromeDockerConfig.from_dict(
        {
            "min_available_memory_mb": 6144,
            "host_memory_floor_mb": 10240,
            "max_host_load_ratio": 0.9,
            "host_load_per_cpu_limit": 1.25,
        }
    )

    assert config.host_memory_floor_mb == 10240
    assert config.host_load_per_cpu_limit == 1.25
