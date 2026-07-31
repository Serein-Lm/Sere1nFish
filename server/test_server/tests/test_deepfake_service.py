import asyncio
from contextlib import asynccontextmanager

import pytest

from api.routers import deepfake as deepfake_router
from api.services.deepfake.contracts import (
    DeepfakeConfig,
    DeepfakeVoiceOptions,
    ImageSwapResult,
    SourceAudio,
    SourceImage,
)
from api.services.deepfake.service import (
    DeepfakeConfigurationError,
    DeepfakeService,
    _parse_config,
)
from deepfake_gateway.profiles import QUALITY_PROFILES


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.session_max_width = 0
        self.session_profile = ""
        self.session_source_count = 0
        self.session_transport = ""
        self.session_voice_options = None

    async def status(self):
        return {"ok": True}

    async def swap_image(self, **_kwargs):
        return ImageSwapResult(b"jpeg", "image/jpeg", 12.5)

    async def create_session(self, **kwargs):
        self.session_max_width = kwargs["max_width"]
        self.session_profile = kwargs["profile"]
        self.session_source_count = len(kwargs["sources"])
        self.session_transport = kwargs["transport"]
        self.session_voice_options = kwargs.get("voice_options")
        payload = {
            "session_id": "session-test-owner",
            "ticket": "must-not-leak",
            "websocket_path": "/v1/realtime/session-test-owner",
            "transport": kwargs["transport"],
        }
        if kwargs.get("voice_options"):
            payload["voice_conversion"] = {
                "enabled": True,
                "provider": "meanvc",
                "sample_rate": 16000,
                "chunk_ms": 200,
            }
            payload["voice_websocket_path"] = (
                "/v1/realtime/session-test-owner/voice"
            )
        if kwargs["transport"] == "obs_whip":
            payload["media"] = {
                "publish_url": "https://gpu.example.test/media/input/test/whip",
                "publish_token": "publish-once",
                "viewer_url": "https://gpu.example.test/media/output/test?token=read-once",
            }
        return payload

    async def session_status(self, session_id: str):
        return {"session_id": session_id, "frame_count": 3}

    async def delete_session(self, session_id: str):
        self.deleted.append(session_id)
        return {"deleted": True}

    @asynccontextmanager
    async def open_stream(self, _session_id: str):
        yield object()

    @asynccontextmanager
    async def open_voice_stream(self, _session_id: str):
        yield object()


def _config(**overrides) -> DeepfakeConfig:
    values = {
        "provider": "facefusion_gateway",
        "base_url": "https://gpu.example.test",
        "api_token": "x" * 48,
        "ca_certificate": "",
        "timeout_seconds": 15.0,
        "max_image_bytes": 1024 * 1024,
        "max_source_images": 4,
        "max_voice_reference_bytes": 20 * 1024 * 1024,
        "realtime_max_width": 960,
    }
    values.update(overrides)
    return DeepfakeConfig(**values)


def test_parse_config_requires_https_and_secret() -> None:
    with pytest.raises(DeepfakeConfigurationError, match="HTTPS"):
        _parse_config({"base_url": "http://gpu.example.test", "api_token": "x" * 48})
    with pytest.raises(DeepfakeConfigurationError, match="api_token"):
        _parse_config({"base_url": "https://gpu.example.test", "api_token": "short"})


def test_quality_profile_registry_keeps_effects_behind_named_policies() -> None:
    assert [profile.profile_id for profile in QUALITY_PROFILES.all()] == ["fast", "balanced", "quality"]
    assert QUALITY_PROFILES.get("fast").face_swapper_pixel_boost == "256x256"
    assert QUALITY_PROFILES.get("fast").face_landmarker_model == "peppa_wutz"
    assert QUALITY_PROFILES.get("fast").max_width == 640
    assert QUALITY_PROFILES.get("quality").processors == ("face_swapper",)
    assert QUALITY_PROFILES.get("quality").face_mask_types == ("box", "occlusion")
    assert QUALITY_PROFILES.get("quality").face_swapper_weight == 0.65
    assert QUALITY_PROFILES.get("quality").face_swapper_pixel_boost == "768x768"
    assert QUALITY_PROFILES.get("quality").max_width == 1280
    with pytest.raises(ValueError, match="Unknown quality profile"):
        QUALITY_PROFILES.get("unregistered")


def test_parse_config_clamps_runtime_limits() -> None:
    config = _parse_config(
        {
            "base_url": "https://gpu.example.test/",
            "api_token": "x" * 48,
            "timeout_seconds": 999,
            "max_image_bytes": 1,
            "max_source_images": 999,
            "realtime_max_width": 9999,
        }
    )
    assert config.base_url == "https://gpu.example.test"
    assert config.timeout_seconds == 120
    assert config.max_image_bytes == 1024 * 1024
    assert config.max_source_images == 8
    assert config.realtime_max_width == 1280


@pytest.mark.asyncio
async def test_session_ticket_is_hidden_and_owner_is_enforced() -> None:
    provider = FakeProvider()
    service = DeepfakeService(_config(), provider)
    created = await service.create_session(
        username="alice",
        sources=[SourceImage(b"source", "source.jpg")],
        max_width=640,
        profile="quality",
    )
    assert "ticket" not in created
    assert created["stream_path"].endswith("/session-test-owner/stream")
    assert provider.session_max_width == 640
    assert provider.session_profile == "quality"
    assert provider.session_source_count == 1
    assert provider.session_transport == "frame_ws"
    assert (await service.session_status("session-test-owner", "alice"))["frame_count"] == 3
    with pytest.raises(PermissionError):
        await service.session_status("session-test-owner", "bob")
    assert await service.delete_session("session-test-owner", "alice") == {"deleted": True}
    assert provider.deleted == ["session-test-owner"]


@pytest.mark.asyncio
async def test_obs_direct_session_preserves_short_lived_media_credentials() -> None:
    provider = FakeProvider()
    service = DeepfakeService(_config(), provider)
    created = await service.create_session(
        username="alice",
        sources=[SourceImage(b"source", "source.jpg")],
        max_width=640,
        profile="fast",
        transport="obs_whip",
    )
    assert created["transport"] == "obs_whip"
    assert created["media"]["publish_token"] == "publish-once"
    assert "ticket" not in created
    assert "stream_path" not in created
    assert provider.session_transport == "obs_whip"


@pytest.mark.asyncio
async def test_obs_direct_voice_conversion_is_forwarded_without_leaking_reference() -> None:
    provider = FakeProvider()
    service = DeepfakeService(_config(), provider)
    created = await service.create_session(
        username="alice",
        sources=[SourceImage(b"source", "source.jpg")],
        max_width=640,
        profile="fast",
        transport="obs_whip",
        voice_options=DeepfakeVoiceOptions(
            provider="meanvc",
            reference=SourceAudio(
                content=b"private-reference-audio",
                filename="voice.wav",
                content_type="audio/wav",
            ),
            steps=2,
        ),
    )
    assert "private-reference-audio" not in str(created)
    assert provider.session_voice_options.provider == "meanvc"
    assert provider.session_voice_options.reference.filename == "voice.wav"
    assert provider.session_voice_options.steps == 2
    await service.delete_session(created["session_id"], "alice")


@pytest.mark.asyncio
async def test_browser_voice_conversion_exposes_authenticated_proxy_path() -> None:
    provider = FakeProvider()
    service = DeepfakeService(_config(), provider)
    created = await service.create_session(
        username="alice",
        sources=[SourceImage(b"source", "source.jpg")],
        max_width=640,
        profile="fast",
        transport="frame_ws",
        voice_options=DeepfakeVoiceOptions(
            provider="meanvc",
            reference=SourceAudio(b"reference", "voice.wav", "audio/wav"),
        ),
    )

    assert created["voice_stream_path"].endswith("/session-test-owner/voice")
    assert "voice_websocket_path" not in created
    assert created["voice_conversion"]["sample_rate"] == 16000
    await service.delete_session(created["session_id"], "alice")


@pytest.mark.asyncio
async def test_voice_conversion_rejects_invalid_provider_and_size() -> None:
    service = DeepfakeService(_config(), FakeProvider())
    valid_reference = SourceAudio(b"reference", "voice.wav", "audio/wav")
    with pytest.raises(ValueError, match="provider"):
        await service.create_session(
            username="alice",
            sources=[SourceImage(b"source", "source.jpg")],
            max_width=640,
            profile="fast",
            transport="obs_whip",
            voice_options=DeepfakeVoiceOptions(
                provider="conversation",
                reference=valid_reference,
            ),
        )
    limited = DeepfakeService(
        _config(max_voice_reference_bytes=4),
        FakeProvider(),
    )
    with pytest.raises(ValueError, match="size limit"):
        await limited.create_session(
            username="alice",
            sources=[SourceImage(b"source", "source.jpg")],
            max_width=640,
            profile="fast",
            transport="obs_whip",
            voice_options=DeepfakeVoiceOptions(
                provider="meanvc",
                reference=SourceAudio(b"12345", "voice.wav", "audio/wav"),
            ),
        )


@pytest.mark.asyncio
async def test_upload_limit_is_checked_before_provider_call() -> None:
    service = DeepfakeService(_config(max_image_bytes=4), FakeProvider())
    with pytest.raises(ValueError, match="size limit"):
        await service.swap_image(
            sources=[SourceImage(b"12345", "source.jpg")],
            target=b"1",
            target_name="target.jpg",
            max_width=640,
            profile="quality",
        )


@pytest.mark.asyncio
async def test_source_count_and_profile_are_validated_before_provider_call() -> None:
    service = DeepfakeService(_config(max_source_images=2), FakeProvider())
    with pytest.raises(ValueError, match="source image count"):
        await service.create_session(
            username="alice",
            sources=[
                SourceImage(b"one", "one.jpg"),
                SourceImage(b"two", "two.jpg"),
                SourceImage(b"three", "three.jpg"),
            ],
            max_width=640,
            profile="quality",
        )
    with pytest.raises(ValueError, match="quality profile"):
        await service.create_session(
            username="alice",
            sources=[SourceImage(b"one", "one.jpg")],
            max_width=640,
            profile="../../quality",
        )
    with pytest.raises(ValueError, match="transport"):
        await service.create_session(
            username="alice",
            sources=[SourceImage(b"one", "one.jpg")],
            max_width=640,
            profile="fast",
            transport="rtmp",
        )


@pytest.mark.asyncio
async def test_realtime_frame_is_relayed_to_bound_media_output(monkeypatch) -> None:
    class FakeRemote:
        def __init__(self) -> None:
            self.responses = ['{"type":"ready"}', b"swapped-frame"]
            self.sent: list[bytes | str] = []

        async def recv(self):
            return self.responses.pop(0)

        async def send(self, payload):
            self.sent.append(payload)

    remote = FakeRemote()

    class FakeDeepfakeService:
        async def open_stream(self, session_id: str, username: str):
            assert session_id == "deepfake-session"
            assert username == "alice"

            @asynccontextmanager
            async def context():
                yield remote

            return context()

    class FakeMediaOutput:
        def __init__(self) -> None:
            self.frames: list[tuple[str, str, bytes]] = []

        async def require_owner(self, session_id: str, owner: str) -> None:
            assert (session_id, owner) == ("media-session", "alice")

        async def publish_video(self, session_id: str, owner: str, data: bytes) -> None:
            self.frames.append((session_id, owner, data))

    class FakeWebSocket:
        def __init__(self) -> None:
            self.messages = [
                {"type": "websocket.receive", "bytes": b"camera-frame"},
                {"type": "websocket.disconnect"},
            ]
            self.accepted_subprotocol = ""
            self.sent_text: list[str] = []
            self.sent_bytes: list[bytes] = []

        async def accept(self, *, subprotocol: str) -> None:
            self.accepted_subprotocol = subprotocol

        async def receive(self):
            return self.messages.pop(0)

        async def send_text(self, payload: str) -> None:
            self.sent_text.append(payload)

        async def send_bytes(self, payload: bytes) -> None:
            self.sent_bytes.append(payload)

        async def close(self, *, code: int) -> None:
            pass

    media = FakeMediaOutput()

    async def authenticated_username(_websocket) -> str:
        return "alice"

    async def deepfake_service():
        return FakeDeepfakeService()

    monkeypatch.setattr(
        deepfake_router,
        "authenticated_websocket_username",
        authenticated_username,
    )
    monkeypatch.setattr(deepfake_router, "get_deepfake_service", deepfake_service)
    monkeypatch.setattr(deepfake_router, "get_media_output_service", lambda: media)

    websocket = FakeWebSocket()
    await deepfake_router.stream_session(
        websocket,
        "deepfake-session",
        "media-session",
    )

    assert remote.sent == [b"camera-frame"]
    assert websocket.sent_bytes == [b"swapped-frame"]
    assert media.frames == [("media-session", "alice", b"swapped-frame")]


@pytest.mark.asyncio
async def test_browser_voice_is_relayed_to_bound_media_output(monkeypatch) -> None:
    published = asyncio.Event()

    class FakeRemote:
        def __init__(self) -> None:
            self.receive_count = 0
            self.sent: list[bytes | str] = []
            self.input_received = asyncio.Event()

        async def recv(self):
            self.receive_count += 1
            if self.receive_count == 1:
                return '{"type":"session.ready","sample_rate":16000}'
            if self.receive_count == 2:
                await self.input_received.wait()
                return b"\x01\x00\x02\x00"
            await asyncio.Future()

        async def send(self, payload):
            self.sent.append(payload)
            self.input_received.set()

    remote = FakeRemote()

    class FakeDeepfakeService:
        async def open_voice_stream(self, session_id: str, username: str):
            assert (session_id, username) == ("deepfake-session", "alice")

            @asynccontextmanager
            async def context():
                yield remote

            return context()

    class FakeMediaOutput:
        def __init__(self) -> None:
            self.audio: list[tuple[str, str, bytes, int]] = []

        async def require_owner(self, session_id: str, owner: str) -> None:
            assert (session_id, owner) == ("media-session", "alice")

        async def publish_audio(
            self,
            session_id: str,
            owner: str,
            data: bytes,
            *,
            sample_rate: int,
        ) -> None:
            self.audio.append((session_id, owner, data, sample_rate))
            published.set()

    class FakeWebSocket:
        def __init__(self) -> None:
            self.receive_count = 0
            self.accepted_subprotocol = ""
            self.sent_text: list[str] = []

        async def accept(self, *, subprotocol: str) -> None:
            self.accepted_subprotocol = subprotocol

        async def receive(self):
            self.receive_count += 1
            if self.receive_count == 1:
                return {"type": "websocket.receive", "bytes": b"\x03\x00\x04\x00"}
            await asyncio.wait_for(published.wait(), timeout=1)
            return {"type": "websocket.disconnect"}

        async def send_text(self, payload: str) -> None:
            self.sent_text.append(payload)

        async def close(self, *, code: int) -> None:
            pass

    media = FakeMediaOutput()

    async def authenticated_username(_websocket) -> str:
        return "alice"

    async def deepfake_service():
        return FakeDeepfakeService()

    monkeypatch.setattr(
        deepfake_router,
        "authenticated_websocket_username",
        authenticated_username,
    )
    monkeypatch.setattr(deepfake_router, "get_deepfake_service", deepfake_service)
    monkeypatch.setattr(deepfake_router, "get_media_output_service", lambda: media)

    websocket = FakeWebSocket()
    await deepfake_router.stream_voice_session(
        websocket,
        "deepfake-session",
        "media-session",
    )

    assert remote.sent == [b"\x03\x00\x04\x00"]
    assert media.audio == [
        ("media-session", "alice", b"\x01\x00\x02\x00", 16000)
    ]
    assert '"type":"session.ready"' in websocket.sent_text[0]
