"""Mobile LLM request parameter and pool merge tests."""

from __future__ import annotations

import asyncio
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


def test_autoglm_completion_usage_is_recorded_with_mobile_context(monkeypatch) -> None:
    from core.mobile import llm_usage

    captured: list[dict] = []

    class FakeCompletions:
        async def create(self, **kwargs):
            assert kwargs["model"] == "qwen3.7-plus"
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=123, completion_tokens=17)
            )

    class FakeAgent:
        openai_client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        )

    monkeypatch.setattr(
        llm_usage,
        "record_llm_usage",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    wrapped = llm_usage.instrument_agent(
        FakeAgent(),
        model="qwen3.7-plus",
        project_id="project-1",
        task_id="mobile-plan-1",
    )

    async def run() -> None:
        response = await wrapped.openai_client.chat.completions.create(
            model="qwen3.7-plus",
            messages=[],
        )
        assert response.usage.prompt_tokens == 123

    asyncio.run(run())

    assert captured and captured[0]["input_tokens"] == 123
    assert captured[0]["output_tokens"] == 17
    assert captured[0]["model"] == "qwen3.7-plus"


def test_token_tracker_public_usage_entrypoint_keeps_context() -> None:
    from Sere1nGraph.graph.observability.tracker import TokenTracker

    tracker = TokenTracker()
    tracker.push_context(
        project_id="project-1",
        task_id="mobile-plan-1",
        phase="mobile_executor",
        agent="mobile_executor",
        task_type="mobile",
    )
    try:
        assert tracker.record_usage(
            model="qwen3.7-plus",
            input_tokens=10,
            output_tokens=4,
            run_id="run-1",
        ) is True
    finally:
        tracker.pop_context()

    stats = tracker.get_stats(task_id="mobile-plan-1", phase="mobile_executor")
    assert stats["total_calls"] == 1
    assert stats["total_input_tokens"] == 10
    assert stats["total_output_tokens"] == 4


def test_shared_llm_factory_preserves_vision_parameters(monkeypatch) -> None:
    from Sere1nGraph.graph.agents import runtime as agent_runtime
    from Sere1nGraph.graph import observability

    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(agent_runtime, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setattr(
        observability,
        "get_global_tracker",
        lambda: SimpleNamespace(callback=object()),
    )
    app_config = SimpleNamespace(
        runtime=SimpleNamespace(
            base_url="https://example.test/v1",
            api_key="test-key",
            models=SimpleNamespace(default="qwen3.7-plus"),
        )
    )

    agent_runtime.create_llm(
        app_config,
        model_name="qwen3.7-plus",
        streaming=False,
        extra_body={"vl_high_resolution_images": True},
    )

    assert captured["extra_body"] == {
        "enable_thinking": False,
        "vl_high_resolution_images": True,
    }
    assert captured["stream_usage"] is False
    assert len(captured["callbacks"]) == 1


def test_xhs_vision_uses_observed_async_llm(monkeypatch) -> None:
    from api.services import xhs_vision_tools

    async def _run() -> None:
        calls = {}

        class FakeLlm:
            async def ainvoke(self, messages):
                calls["messages"] = messages
                return SimpleNamespace(content=[{"type": "text", "text": "画像结果"}])

        async def fake_llm(*, streaming: bool = False):
            calls["streaming"] = streaming
            return FakeLlm()

        monkeypatch.setattr(xhs_vision_tools, "_get_observed_vision_llm", fake_llm)
        monkeypatch.setattr(xhs_vision_tools, "_load_prompt", lambda _name: "分析提示")

        result = await xhs_vision_tools.analyze_screenshots_with_vision_async(
            [{"base64": "aGVsbG8=", "format": "png"}]
        )

        assert result == "画像结果"
        assert calls["streaming"] is False
        content = calls["messages"][0].content
        assert content[0]["type"] == "image_url"
        assert content[-1] == {"type": "text", "text": "分析提示"}

    asyncio.run(_run())


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


def test_background_task_can_take_over_initiators_device_lease() -> None:
    import threading

    import pytest

    from core.mobile.pool import DevicePool, PoolError

    pool = object.__new__(DevicePool)
    pool._lock = threading.RLock()
    pool._reservations = {}
    pool.acquire("device-key", "admin", device_id="device-a")

    reservation = pool.acquire_for_task(
        "device-key",
        "collect:run-1",
        initiated_by="admin",
        device_id="device-a",
    )

    assert reservation.owner == "collect:run-1"
    with pytest.raises(PoolError, match="collect:run-1"):
        pool.acquire_for_task(
            "device-key",
            "collect:run-2",
            initiated_by="another-user",
            device_id="device-a",
        )


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
