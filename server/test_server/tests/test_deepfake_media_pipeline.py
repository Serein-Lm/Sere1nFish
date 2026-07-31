import asyncio
from types import SimpleNamespace

import pytest

from deepfake_gateway.media_pipeline import fit_even_dimensions, parse_frame_rate
from deepfake_gateway.media_diagnostics import WhipPublishDiagnostics
from deepfake_gateway.voice_bridge import (
    MediaVoiceBridge,
    MediaVoiceBridgeInputUnavailable,
    MediaVoiceBridgeStats,
    PcmOutputBuffer,
)


def test_fit_even_dimensions_preserves_aspect_ratio_and_encoder_constraints() -> None:
    assert fit_even_dimensions(1920, 1080, 640) == (640, 360)
    assert fit_even_dimensions(641, 481, 640) == (640, 480)
    assert fit_even_dimensions(320, 241, 640) == (320, 240)


def test_fit_even_dimensions_rejects_invalid_video() -> None:
    with pytest.raises(ValueError):
        fit_even_dimensions(0, 1080, 640)


def test_parse_frame_rate_handles_fractional_and_invalid_values() -> None:
    assert parse_frame_rate("30000/1001") == pytest.approx(29.970, rel=0.001)
    assert parse_frame_rate("15/1") == 15
    assert parse_frame_rate("0/0") == 0
    assert parse_frame_rate("not-a-rate") == 0


def test_whip_publish_diagnostics_exposes_outcome_without_credentials() -> None:
    diagnostics = WhipPublishDiagnostics()
    diagnostics.record(
        {
            "ip": "203.0.113.8",
            "protocol": "webrtc",
            "token": "must-not-leak",
            "password": "must-not-leak-either",
        },
        authorized=False,
    )
    diagnostics.record(
        {"ip": "203.0.113.8", "protocol": "webrtc", "token": "accepted-secret"},
        authorized=True,
    )

    payload = diagnostics.as_dict()
    assert payload["attempts"] == 2
    assert payload["authorized_attempts"] == 1
    assert payload["last_authorized"] is True
    assert payload["last_ip"] == "203.0.113.8"
    assert payload["last_protocol"] == "webrtc"
    assert "secret" not in str(payload)


def test_pcm_output_buffer_keeps_latest_aligned_audio_and_pads_silence() -> None:
    buffer = PcmOutputBuffer(sample_rate=10, max_seconds=0.2)
    buffer.append(b"\x01\x00\x02\x00\x03")
    assert buffer.buffered_ms == 200
    assert buffer.take_or_silence(6) == b"\x01\x00\x02\x00\x00\x00"
    assert buffer.buffered_ms == 0

    buffer.append(b"\x01\x00\x02\x00\x03\x00")
    assert buffer.take_or_silence(4) == b"\x02\x00\x03\x00"


def test_media_voice_bridge_treats_missing_initial_audio_as_waiting() -> None:
    async def run() -> None:
        stats = MediaVoiceBridgeStats()
        bridge = MediaVoiceBridge(
            input_url="rtsp://localhost/input",
            websocket_url="wss://localhost:18444/bridge",
            token="x" * 48,
            ca_file="/tmp/ca.crt",
            provider="meanvc",
            output_sample_rate=16000,
            output_buffer=PcmOutputBuffer(),
            stats=stats,
        )

        async def no_audio() -> None:
            raise MediaVoiceBridgeInputUnavailable

        async def stop_after_wait(_seconds: float) -> None:
            bridge._stopping.set()

        bridge._run_once = no_audio  # type: ignore[method-assign]
        bridge._sleep_or_stop = stop_after_wait  # type: ignore[method-assign]
        bridge._terminate_decoder = (  # type: ignore[method-assign]
            lambda: asyncio.sleep(0)
        )
        await bridge.run()
        assert stats.state == "stopped"
        assert stats.reconnects == 0
        assert stats.last_error == ""

    asyncio.run(run())


def test_media_voice_bridge_marks_empty_initial_decoder_as_unavailable() -> None:
    decoder = SimpleNamespace(stdout=SimpleNamespace(read=lambda _size: asyncio.sleep(0, result=b"")))
    with pytest.raises(MediaVoiceBridgeInputUnavailable):
        asyncio.run(MediaVoiceBridge._read_audio_chunk(decoder, waiting_for_input=True))
