"""Full-duplex voice protocol tests without external API calls."""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest


def test_realtime_endpoint_rewrites_inference_path() -> None:
    from api.services.voice_realtime.config import normalise_realtime_endpoint

    assert normalise_realtime_endpoint(
        "https://llm-example.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference",
        workspace_id=None,
        region="beijing",
    ) == "wss://llm-example.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"


def test_realtime_config_uses_latest_plus_model(monkeypatch) -> None:
    from api.services.voice_realtime import config as voice_config

    async def fake_app_config():
        return SimpleNamespace(runtime=SimpleNamespace(api_key="runtime-key"))

    async def fake_section(category: str) -> dict:
        if category == "cosyvoice":
            return {
                "workspace_id": "llm-example",
                "base_ws": "llm-example.cn-beijing.maas.aliyuncs.com",
            }
        return {}

    monkeypatch.setattr(voice_config, "get_runtime_app_config", fake_app_config)
    monkeypatch.setattr(voice_config, "get_runtime_config_section", fake_section)

    loaded = asyncio.run(voice_config.load_realtime_voice_config())
    assert loaded.model == "qwen-audio-3.0-realtime-plus"
    assert loaded.endpoint.endswith("/api-ws/v1/realtime")
    assert loaded.default_mode == "smart_turn"
    assert loaded.max_history_turns == 20


class _ProviderSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.messages: asyncio.Queue[str] = asyncio.Queue()
        self.closed = False

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> str:
        return await self.messages.get()

    async def close(self) -> None:
        self.closed = True


def test_realtime_adapter_encodes_and_decodes_pcm() -> None:
    from api.services.voice_realtime.adapters import BailianRealtimeConnection

    async def run() -> None:
        socket = _ProviderSocket()
        connection = BailianRealtimeConnection(socket)
        audio = b"\x01\x00\xff\x7f"
        await connection.send_audio(audio)
        sent = json.loads(socket.sent[0])
        assert sent["type"] == "input_audio_buffer.append"
        assert base64.b64decode(sent["audio"]) == audio

        await socket.messages.put(
            json.dumps(
                {
                    "type": "response.audio.delta",
                    "delta": base64.b64encode(audio).decode(),
                }
            )
        )
        event = await connection.receive()
        assert event.type == "response.audio.delta"
        assert event.audio == audio

    asyncio.run(run())


def test_realtime_provider_builds_smart_turn_session(monkeypatch) -> None:
    from api.services.voice_realtime import adapters
    from api.services.voice_realtime.contracts import (
        RealtimeSessionOptions,
        RealtimeVoiceConfig,
    )

    socket = _ProviderSocket()

    async def fake_connect(url: str, **kwargs):
        assert url.endswith("?model=qwen-audio-3.0-realtime-plus")
        assert kwargs["additional_headers"]["Authorization"] == "Bearer secret"
        return socket

    monkeypatch.setattr(adapters.websockets, "connect", fake_connect)
    config = RealtimeVoiceConfig(
        provider="bailian_qwen_audio",
        api_key="secret",
        model="qwen-audio-3.0-realtime-plus",
        endpoint="wss://example.com/api-ws/v1/realtime",
        workspace_id="llm-example",
        region="beijing",
        default_voice="longanqian",
        default_mode="smart_turn",
        default_instructions="",
        max_history_turns=20,
        vad_threshold=0.5,
        vad_silence_ms=800,
        connect_timeout_seconds=15,
        session_timeout_seconds=1800,
        max_audio_chunk_bytes=65536,
        max_provider_message_bytes=4194304,
    )
    options = RealtimeSessionOptions(
        voice="longanqian",
        mode="smart_turn",
        instructions="简洁回答",
        max_history_turns=12,
        vad_threshold=0.5,
        vad_silence_ms=800,
    )

    asyncio.run(adapters.BailianQwenAudioRealtimeProvider(config).connect(options))
    update = json.loads(socket.sent[0])
    assert update["type"] == "session.update"
    assert update["session"]["turn_detection"] == {"type": "smart_turn"}
    assert update["session"]["max_history_turns"] == 12
    assert update["session"]["input_audio_format"] == "pcm"


def test_realtime_session_rejects_incompatible_clone(monkeypatch) -> None:
    from api.services.voice_realtime.contracts import RealtimeVoiceConfig
    from api.services.voice_realtime import service as voice_service

    async def fake_clone(db, voice_id: str):
        return {
            "voice_id": voice_id,
            "status": "active",
            "model": "qwen-audio-3.0-tts-flash",
        }

    monkeypatch.setattr(voice_service.voice_dao, "get_clone", fake_clone)
    config = RealtimeVoiceConfig(
        provider="bailian_qwen_audio",
        api_key="secret",
        model="qwen-audio-3.0-realtime-plus",
        endpoint="wss://example.com/api-ws/v1/realtime",
        workspace_id="llm-example",
        region="beijing",
        default_voice="longanqian",
        default_mode="smart_turn",
        default_instructions="",
        max_history_turns=20,
        vad_threshold=0.5,
        vad_silence_ms=800,
        connect_timeout_seconds=15,
        session_timeout_seconds=1800,
        max_audio_chunk_bytes=65536,
        max_provider_message_bytes=4194304,
    )

    with pytest.raises(voice_service.RealtimeVoiceError, match="不能用于"):
        asyncio.run(
            voice_service.RealtimeVoiceSessionService()._session_options(
                "db",
                {"type": "session.start", "voice": "tts-clone"},
                config,
            )
        )


def test_websocket_auth_token_uses_shared_subprotocol() -> None:
    from api.services.websocket_auth import websocket_bearer_token

    websocket = SimpleNamespace(
        headers={
            "sec-websocket-protocol": "sere1nfish, sere1nfish.auth.token-value"
        }
    )
    assert websocket_bearer_token(websocket) == "token-value"


def test_realtime_audio_is_published_to_bound_media_output(monkeypatch) -> None:
    from api.services.voice_realtime import service as voice_service
    from api.services.voice_realtime.adapters import RealtimeVoiceProviderError
    from api.services.voice_realtime.contracts import RealtimeProviderEvent

    class Connection:
        calls = 0

        async def receive(self):
            self.calls += 1
            if self.calls == 1:
                return RealtimeProviderEvent(
                    type="response.audio.delta",
                    payload={},
                    audio=b"\x01\x00\x02\x00",
                )
            raise RealtimeVoiceProviderError("finished")

        async def send_control(self, _event_type: str):
            return None

    class Browser:
        def __init__(self) -> None:
            self.audio: list[bytes] = []

        async def send_bytes(self, payload: bytes):
            self.audio.append(payload)

        async def send_text(self, _payload: str):
            return None

    class MediaOutput:
        def __init__(self) -> None:
            self.published: list[tuple[str, str, bytes]] = []

        async def publish_audio(self, session_id: str, owner: str, data: bytes):
            self.published.append((session_id, owner, data))

    async def run() -> None:
        browser = Browser()
        media = MediaOutput()
        monkeypatch.setattr(voice_service, "get_media_output_service", lambda: media)
        with pytest.raises(RealtimeVoiceProviderError, match="finished"):
            await voice_service.RealtimeVoiceSessionService()._provider_to_browser(
                browser,
                Connection(),
                {"provider_events": 0, "output_bytes": 0},
                output_session_id="media-test",
                username="alice",
            )
        assert media.published == [("media-test", "alice", b"\x01\x00\x02\x00")]
        assert browser.audio == [b"\x01\x00\x02\x00"]

    asyncio.run(run())
