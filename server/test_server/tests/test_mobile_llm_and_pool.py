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
            return None

        def list_devices(self):
            return [
                SimpleNamespace(
                    device_id="10.144.144.23:5555",
                    status="offline",
                    model="stale-phone",
                    connection_type="wifi",
                )
            ]

    pool = object.__new__(DevicePool)
    pool._mgr = FakeManager()
    pool._reservations = {}
    pool._lock = threading.Lock()

    monkeypatch.setattr(DevicePool, "_connected_adb_ips", lambda _self: set())
    monkeypatch.setattr(
        DevicePool,
        "_easytier_pairing_candidates",
        staticmethod(
            lambda exclude_ips=None: [
                {"ipv4": "10.144.144.23", "hostname": "vivo-phone"}
            ]
        ),
    )

    items = pool.list_pool()

    assert [item["status"] for item in items] == ["offline", "pairing_required"]
    pairing = items[1]
    assert pairing["device_id"] == "easytier:10.144.144.23"
    assert pairing["pairing_available"] is True
