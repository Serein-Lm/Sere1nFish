"""EasyTier backend-peer self-healing tests."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from typing import Any


class _FakeContainer:
    def __init__(self, *, name: str, container_id: str, network_target: str) -> None:
        self.name = name
        self.id = container_id
        self.status = "exited"
        self.restarted = False
        self.removed = False
        self.attrs = {
            "Config": {
                "Image": "ghcr.io/easytier/easytier:v2.6.4",
                "Cmd": ["--no-listener"],
                "Env": ["TZ=Asia/Shanghai"],
                "Labels": {"com.docker.compose.service": "easytier-backend-peer"},
                "Entrypoint": ["easytier-core"],
                "User": "",
                "WorkingDir": "/app",
            },
            "HostConfig": {
                "NetworkMode": f"container:{network_target}",
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "CapAdd": ["NET_ADMIN"],
                "Devices": [
                    {
                        "PathOnHost": "/dev/net/tun",
                        "PathInContainer": "/dev/net/tun",
                        "CgroupPermissions": "rwm",
                    }
                ],
                "Ulimits": [{"Name": "nofile", "Soft": 1024, "Hard": 2048}],
            },
        }

    def restart(self, timeout: int) -> None:
        self.restarted = True

    def stop(self, timeout: int) -> None:
        self.status = "exited"

    def rename(self, name: str) -> None:
        self.name = name

    def remove(self, force: bool) -> None:
        self.removed = True


class _FakeContainers:
    def __init__(self, peer: _FakeContainer, backend: Any) -> None:
        self.peer = peer
        self.backend = backend
        self.list_all: bool | None = None
        self.run_kwargs: dict[str, Any] | None = None

    def list(self, all: bool = False) -> list[_FakeContainer]:
        self.list_all = all
        return [self.peer]

    def get(self, container_ref: str) -> Any:
        assert container_ref == "current-backend"
        return self.backend

    def run(self, **kwargs: Any) -> Any:
        self.run_kwargs = kwargs
        return SimpleNamespace(name=kwargs["name"], id="new-peer")


def test_stale_backend_peer_is_recreated_against_current_backend(monkeypatch) -> None:
    from core.mobile import easytier

    current_backend_id = "current-backend-full-id"
    peer = _FakeContainer(
        name="sere1nfish_easytier-backend-peer_1",
        container_id="old-peer",
        network_target="removed-backend-full-id",
    )
    containers = _FakeContainers(peer, SimpleNamespace(id=current_backend_id))
    fake_docker = SimpleNamespace(
        from_env=lambda: SimpleNamespace(containers=containers),
        types=SimpleNamespace(Ulimit=lambda **kwargs: kwargs),
    )
    health = iter([False, True])

    monkeypatch.setitem(sys.modules, "docker", fake_docker)
    monkeypatch.setenv("HOSTNAME", "current-backend")
    monkeypatch.setattr(easytier, "easytier_network_healthy", lambda: next(health, True))
    monkeypatch.setattr(easytier, "_LAST_HEAL_TS", 0.0)

    result = easytier.ensure_easytier_healthy()

    assert result["healthy"] is True
    assert result["healed"] is True
    assert result["action"] == "recreate"
    assert containers.list_all is True
    assert containers.run_kwargs is not None
    assert containers.run_kwargs["network_mode"] == f"container:{current_backend_id}"
    assert containers.run_kwargs["devices"] == ["/dev/net/tun:/dev/net/tun:rwm"]
    assert peer.removed is True
    assert peer.restarted is False


def test_current_backend_peer_uses_lightweight_restart(monkeypatch) -> None:
    from core.mobile import easytier

    current_backend_id = "current-backend-full-id"
    peer = _FakeContainer(
        name="sere1nfish_easytier-backend-peer_1",
        container_id="peer",
        network_target=current_backend_id,
    )
    containers = _FakeContainers(peer, SimpleNamespace(id=current_backend_id))
    fake_docker = SimpleNamespace(
        from_env=lambda: SimpleNamespace(containers=containers),
        types=SimpleNamespace(Ulimit=lambda **kwargs: kwargs),
    )
    health = iter([False, True])

    monkeypatch.setitem(sys.modules, "docker", fake_docker)
    monkeypatch.setenv("HOSTNAME", "current-backend")
    monkeypatch.setattr(easytier, "easytier_network_healthy", lambda: next(health, True))
    monkeypatch.setattr(easytier, "_LAST_HEAL_TS", 0.0)

    result = easytier.ensure_easytier_healthy()

    assert result["action"] == "restart"
    assert result["healthy"] is True
    assert peer.restarted is True
    assert containers.run_kwargs is None


def test_keepalive_reconnects_adb_immediately_after_network_heal(monkeypatch) -> None:
    from core.mobile import easytier
    from core.mobile.keepalive import MobileKeepAlive
    from core.mobile.pool import DevicePool

    calls: list[str] = []

    class _FakePool:
        def has_unconnected_easytier_peers(self) -> bool:
            calls.append("needs_auto_connect")
            return False

        def auto_connect_discovered(self) -> dict[str, Any]:
            calls.append("auto_connect")
            return {"count": 2}

        def keepalive_once(self, **kwargs: Any) -> dict[str, Any]:
            calls.append("keepalive")
            return {"checked": 2}

    pool = _FakePool()
    monkeypatch.setattr(
        easytier,
        "ensure_easytier_healthy",
        lambda: {"healthy": True, "healed": True, "action": "recreate"},
    )
    monkeypatch.setattr(DevicePool, "get_instance", classmethod(lambda cls: pool))

    keepalive = MobileKeepAlive()
    asyncio.run(keepalive._run_once())

    assert calls == ["auto_connect", "keepalive"]
    assert keepalive.status()["last_result"]["auto_connect"] == {
        "count": 2,
        "attempts": 1,
    }


def test_keepalive_connects_phone_that_joins_after_network_is_healthy(monkeypatch) -> None:
    from core.mobile import easytier
    from core.mobile.keepalive import MobileKeepAlive
    from core.mobile.pool import DevicePool

    calls: list[str] = []

    class _FakePool:
        def has_unconnected_easytier_peers(self) -> bool:
            calls.append("needs_auto_connect")
            return True

        def auto_connect_discovered(self) -> dict[str, Any]:
            calls.append("auto_connect")
            return {"count": 1}

        def keepalive_once(self, **kwargs: Any) -> dict[str, Any]:
            calls.append("keepalive")
            return {"checked": 1}

    monkeypatch.setattr(
        easytier,
        "ensure_easytier_healthy",
        lambda: {"healthy": True, "healed": False},
    )
    monkeypatch.setattr(
        DevicePool,
        "get_instance",
        classmethod(lambda cls: _FakePool()),
    )

    asyncio.run(MobileKeepAlive()._run_once())

    assert calls == ["needs_auto_connect", "auto_connect", "keepalive"]
