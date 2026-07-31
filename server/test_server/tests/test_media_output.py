"""Tests for the transient remote OBS media bus."""

from __future__ import annotations

import pytest

from api.services.media_output import (
    MEDIA_PACKET_AUDIO,
    MEDIA_PACKET_AUDIO_16K,
    MEDIA_PACKET_VIDEO,
    MediaOutputError,
    MediaOutputNotFound,
    MediaOutputPermissionError,
    MediaOutputService,
)


@pytest.mark.asyncio
async def test_output_session_enforces_owner_and_keeps_viewer_token_private() -> None:
    service = MediaOutputService()
    created = await service.create("alice", ttl_seconds=900)

    metadata = await service.get(created["session_id"], "alice")
    assert "viewer_token" not in metadata
    assert metadata["viewer_path"] == "/api/v1/media-output/view"

    with pytest.raises(MediaOutputPermissionError):
        await service.get(created["session_id"], "bob")
    with pytest.raises(MediaOutputNotFound):
        await service.subscribe("invalid-token")


@pytest.mark.asyncio
async def test_output_session_fans_video_and_audio_to_viewer() -> None:
    service = MediaOutputService()
    created = await service.create("alice", ttl_seconds=900)
    subscription = await service.subscribe(created["viewer_token"])

    await service.publish_video(created["session_id"], "alice", b"jpeg-frame")
    await service.publish_audio(created["session_id"], "alice", b"\x01\x00\x02\x00")
    await service.publish_audio(
        created["session_id"],
        "alice",
        b"\x03\x00\x04\x00",
        sample_rate=16000,
    )

    assert await subscription.queue.get() == bytes((MEDIA_PACKET_VIDEO,)) + b"jpeg-frame"
    assert await subscription.queue.get() == bytes((MEDIA_PACKET_AUDIO,)) + b"\x01\x00\x02\x00"
    assert await subscription.queue.get() == bytes((MEDIA_PACKET_AUDIO_16K,)) + b"\x03\x00\x04\x00"
    metadata = await service.get(created["session_id"], "alice")
    assert metadata["video_frames"] == 1
    assert metadata["audio_chunks"] == 2
    assert metadata["viewer_count"] == 1

    await service.delete(created["session_id"], "alice")
    assert await subscription.queue.get() is None


@pytest.mark.asyncio
async def test_slow_viewer_is_moved_to_latest_packet() -> None:
    service = MediaOutputService(subscriber_queue_size=2)
    created = await service.create("alice", ttl_seconds=900)
    subscription = await service.subscribe(created["viewer_token"])

    await service.publish_video(created["session_id"], "alice", b"frame-1")
    await service.publish_video(created["session_id"], "alice", b"frame-2")
    await service.publish_video(created["session_id"], "alice", b"frame-3")

    assert await subscription.queue.get() == bytes((MEDIA_PACKET_VIDEO,)) + b"frame-3"
    assert subscription.queue.empty()


@pytest.mark.asyncio
async def test_output_session_rejects_unknown_audio_sample_rate() -> None:
    service = MediaOutputService()
    created = await service.create("alice", ttl_seconds=900)

    with pytest.raises(MediaOutputError, match="采样率"):
        await service.publish_audio(
            created["session_id"],
            "alice",
            b"\x01\x00",
            sample_rate=22050,
        )


def test_viewer_token_uses_fragment_safe_websocket_subprotocol() -> None:
    from types import SimpleNamespace

    from api.routers.media_output import _viewer_token

    websocket = SimpleNamespace(
        headers={
            "sec-websocket-protocol": (
                "sere1nfish-output, sere1nfish.output.viewer-token"
            )
        }
    )
    assert _viewer_token(websocket) == "viewer-token"
