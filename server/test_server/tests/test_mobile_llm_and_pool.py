"""Mobile LLM request parameter and pool merge tests."""

from __future__ import annotations

import threading
from types import SimpleNamespace


def test_disable_thinking_extra_body_preserves_existing_params() -> None:
    from core.llm_params import disable_thinking_extra_body

    body = disable_thinking_extra_body({"vl_high_resolution_images": True})

    assert body == {
        "enable_thinking": False,
        "vl_high_resolution_images": True,
    }


def test_executor_model_config_disables_thinking() -> None:
    from core.mobile.executor import _build_model_config

    app_config = SimpleNamespace(
        runtime=SimpleNamespace(
            base_url="https://example.test/v1",
            api_key="test-key",
            max_tokens=3000,
            temperature=0,
            top_p=0.85,
            frequency_penalty=0.2,
            models=SimpleNamespace(mobile_executor_model="qwen3.7-plus"),
        ),
        mobile=SimpleNamespace(executor_max_tokens=None),
    )

    model_config = _build_model_config(app_config)

    assert model_config.extra_body["enable_thinking"] is False
    assert model_config.extra_body["vl_high_resolution_images"] is True


def test_pairing_candidate_survives_stale_offline_adb_record(monkeypatch) -> None:
    from core.mobile.pool import DevicePool

    class FakeManager:
        def refresh(self) -> None:
            raise AssertionError("list_pool must not synchronously refresh ADB")

        def list_devices(self):
            return [
                SimpleNamespace(
                    device_id="10.144.144.23:5555",
                    status="offline",
                    model="stale-phone",
                    connection_type="wifi",
                )
            ]

        def resolve_adb_device_id(self, device_id: str) -> str:
            return device_id

    pool = object.__new__(DevicePool)
    pool._mgr = FakeManager()
    pool._reservations = {}
    pool._lock = threading.Lock()

    monkeypatch.setattr(DevicePool, "_connected_adb_ips", lambda _self: set())
    monkeypatch.setattr(
        DevicePool,
        "_easytier_peers",
        staticmethod(
            lambda: [{"ipv4": "10.144.144.23", "hostname": "vivo-phone"}]
        ),
    )

    items = pool.list_pool()

    assert [item["status"] for item in items] == ["offline", "pairing_required"]
    pairing = items[1]
    assert pairing["device_id"] == "easytier:10.144.144.23"
    assert pairing["pairing_available"] is True


def test_connected_adb_device_includes_easytier_link_metrics(monkeypatch) -> None:
    from core.mobile.pool import DevicePool

    class FakeManager:
        def refresh(self) -> None:
            return None

        def list_devices(self):
            return [
                SimpleNamespace(
                    device_id="10AEBJ43JU002MZ",
                    status="device",
                    model="V2353A",
                    connection_type="wifi",
                )
            ]

        def resolve_adb_device_id(self, _device_id: str) -> str:
            return "10.144.144.2:5555"

    peer = {
        "ipv4": "10.144.144.2",
        "hostname": "test1",
        "lat_ms": "43.2",
        "rx_bytes": "1.20 MB",
        "tx_bytes": "800.00 kB",
        "rx_bytes_total": 1_200_000,
        "tx_bytes_total": 800_000,
        "sampled_at": 100.0,
    }
    pool = object.__new__(DevicePool)
    pool._mgr = FakeManager()
    pool._reservations = {}
    pool._lock = threading.Lock()

    monkeypatch.setattr(DevicePool, "_connected_adb_ips", lambda _self: {"10.144.144.2"})
    monkeypatch.setattr(DevicePool, "_easytier_peers", staticmethod(lambda: [peer]))

    items = pool.list_pool()

    assert len(items) == 1
    assert items[0]["network_ip"] == "10.144.144.2"
    assert items[0]["easytier_peer"] == peer


def test_unconnected_easytier_peer_requests_auto_connect(monkeypatch) -> None:
    from core.mobile.pool import DevicePool

    pool = object.__new__(DevicePool)
    monkeypatch.setattr(pool, "_connected_adb_ips", lambda: {"10.144.144.2"})
    monkeypatch.setattr(
        pool,
        "_easytier_peers",
        lambda: [
            {"ipv4": "10.144.144.2"},
            {"ipv4": "10.144.144.3"},
        ],
    )

    assert pool.has_unconnected_easytier_peers() is True

    monkeypatch.setattr(
        pool,
        "_connected_adb_ips",
        lambda: {"10.144.144.2", "10.144.144.3"},
    )
    assert pool.has_unconnected_easytier_peers() is False


def test_easytier_byte_counter_normalization() -> None:
    from core.mobile.easytier import _parse_byte_count

    assert _parse_byte_count("518.15 kB") == 518_150
    assert _parse_byte_count("1.01 MB") == 1_010_000
    assert _parse_byte_count("42 B") == 42
    assert _parse_byte_count("-") is None


def test_wake_unlock_does_not_swipe_an_unlocked_device(monkeypatch) -> None:
    from core.mobile.pool import DevicePool

    commands: list[list[str]] = []

    def fake_adb_shell(_device_id: str, args: list[str], timeout: int = 10):
        commands.append(args)
        stdout = ""
        if args == ["dumpsys", "window", "policy"]:
            stdout = "KeyguardServiceDelegate\n  showing=false\n"
        return SimpleNamespace(returncode=0, stderr="", stdout=stdout)

    pool = object.__new__(DevicePool)
    pool._mgr = SimpleNamespace(resolve_adb_device_id=lambda device_id: device_id)
    monkeypatch.setattr(pool, "_adb_shell", fake_adb_shell)
    monkeypatch.setattr("core.mobile.pool.time.sleep", lambda _seconds: None)

    result = pool.wake_and_unlock("10.144.144.2:5555", stay_on=True)

    assert result["ok"] is True
    assert result["unlocked"] is True
    assert result["unlock"] == []
    assert ["input", "swipe", "500", "1800", "500", "300", "250"] not in commands
