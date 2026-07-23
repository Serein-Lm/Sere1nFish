from contextlib import asynccontextmanager

import pytest

from api.services.deepfake.contracts import DeepfakeConfig, ImageSwapResult, SourceImage
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

    async def status(self):
        return {"ok": True}

    async def swap_image(self, **_kwargs):
        return ImageSwapResult(b"jpeg", "image/jpeg", 12.5)

    async def create_session(self, **kwargs):
        self.session_max_width = kwargs["max_width"]
        self.session_profile = kwargs["profile"]
        self.session_source_count = len(kwargs["sources"])
        return {
            "session_id": "session-test-owner",
            "ticket": "must-not-leak",
            "websocket_path": "/v1/realtime/session-test-owner",
        }

    async def session_status(self, session_id: str):
        return {"session_id": session_id, "frame_count": 3}

    async def delete_session(self, session_id: str):
        self.deleted.append(session_id)
        return {"deleted": True}

    @asynccontextmanager
    async def open_stream(self, _session_id: str):
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
    assert QUALITY_PROFILES.get("quality").processors == ("face_swapper", "face_enhancer")
    assert QUALITY_PROFILES.get("quality").face_mask_types == ("box", "occlusion")
    assert QUALITY_PROFILES.get("quality").face_swapper_weight == 0.65
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
    assert (await service.session_status("session-test-owner", "alice"))["frame_count"] == 3
    with pytest.raises(PermissionError):
        await service.session_status("session-test-owner", "bob")
    assert await service.delete_session("session-test-owner", "alice") == {"deleted": True}
    assert provider.deleted == ["session-test-owner"]


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
