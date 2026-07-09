"""EasyTier mobile access profile tests."""

from __future__ import annotations

import tomllib


def test_easytier_access_profile_exports_phone_toml(monkeypatch) -> None:
    from core.mobile.easytier import build_easytier_access_profile

    monkeypatch.setenv("EASYTIER_PUBLIC_HOST", "203.0.113.10")
    monkeypatch.setenv("EASYTIER_NETWORK_NAME", "sere1nfish/mobile test")
    monkeypatch.setenv("EASYTIER_NETWORK_SECRET", 'secret"with\\escaping')
    monkeypatch.setenv("EASYTIER_VIRTUAL_CIDR", "10.144.144.0/24")
    monkeypatch.setenv("EASYTIER_BACKEND_IPV4", "10.144.144.1")
    monkeypatch.setenv("MOBILE_AGENT_ADB_PORT", "5555")

    profile = build_easytier_access_profile("ignored.example:443")
    config = tomllib.loads(profile.config_toml)

    assert profile.config_filename == "sere1nfish-mobile-test-android.toml"
    assert config["instance_name"] == "sere1nfish-mobile"
    assert config["hostname"] == "sere1nfish-android"
    assert config["ipv4"] == "10.144.144.0/24"
    assert config["network_identity"]["network_name"] == "sere1nfish/mobile test"
    assert config["network_identity"]["network_secret"] == 'secret"with\\escaping'
    assert config["dhcp"] is True
    assert config["listeners"] == []
    assert [peer["uri"] for peer in config["peer"]] == [
        "tcp://203.0.113.10:11010",
        "udp://203.0.113.10:11010",
        "ws://203.0.113.10:11011",
        "wss://203.0.113.10:11012",
        "wg://203.0.113.10:11013",
    ]
    assert "flags" in config
    assert profile.phone_ipv4_cidr == "10.144.144.0/24"
    assert profile.phone_command.startswith("easytier-core -d -i 10.144.144.0/24 ")
    assert profile.config_payload["network"]["phone_ipv4_cidr"] == "10.144.144.0/24"
    assert profile.config_payload["network"]["peers"] == [peer["uri"] for peer in config["peer"]]
    assert profile.qr_payload is profile.config_payload
