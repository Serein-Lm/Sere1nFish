"""Bailian realtime voice routing tests without external API calls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def test_voice_config_prefers_cosyvoice_and_builds_workspace_endpoint(
    monkeypatch,
) -> None:
    from api.services import voice_runtime

    async def fake_app_config():
        return SimpleNamespace(runtime=SimpleNamespace(api_key="runtime-key"))

    async def fake_section(category: str) -> dict:
        if category == "cosyvoice":
            return {
                "api_key": "cosy-key",
                "region": "singapore",
                "workspace_id": "ws-123",
                "model": "qwen-audio-3.0-tts-flash",
                "prefix": "tester",
                "language_hints": ["zh", "en"],
                "max_prompt_audio_length": 20,
                "enable_preprocess": True,
                "pool_size": 3,
                "stream_sample_rate": 24000,
            }
        if category == "bailian":
            return {"api_key": "bailian-key", "region": "beijing"}
        return {}

    monkeypatch.setattr(voice_runtime, "get_runtime_app_config", fake_app_config)
    monkeypatch.setattr(voice_runtime, "get_runtime_config_section", fake_section)

    config = asyncio.run(voice_runtime.load_voice_runtime_config())
    assert config.api_key == "cosy-key"
    assert config.model == "qwen-audio-3.0-tts-flash"
    assert config.base_http == (
        "https://ws-123.ap-southeast-1.maas.aliyuncs.com/api/v1"
    )
    assert config.base_ws == (
        "wss://ws-123.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/inference"
    )
    assert config.language_hints == ["zh", "en"]
    assert config.enable_preprocess is True
    assert config.pool_size == 3


def test_voice_config_defaults_to_latest_realtime_clone_model(monkeypatch) -> None:
    from api.services import voice_runtime

    async def fake_app_config():
        return SimpleNamespace(runtime=SimpleNamespace(api_key="runtime-key"))

    async def fake_section(category: str) -> dict:
        if category == "cosyvoice":
            return {"workspace_id": "llm-example"}
        return {}

    monkeypatch.setattr(voice_runtime, "get_runtime_app_config", fake_app_config)
    monkeypatch.setattr(voice_runtime, "get_runtime_config_section", fake_section)

    config = asyncio.run(voice_runtime.load_voice_runtime_config())
    assert config.model == voice_runtime.LATEST_REALTIME_VOICE_MODEL
    assert config.model == "qwen-audio-3.0-tts-flash"


def test_voice_source_url_drops_temporary_query_credentials() -> None:
    from api.services.voice_runtime import _stored_source_url

    assert _stored_source_url(
        "https://bucket.example.com/sample.wav?Expires=123&Signature=secret"
    ) == "https://bucket.example.com/sample.wav"
    assert _stored_source_url("builtin://voice-1") == "builtin://voice-1"


def test_voice_upload_public_url_uses_forwarded_headers() -> None:
    from starlette.requests import Request

    from api.routers.voice import _public_url

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/voice/upload",
            "headers": [
                (b"host", b"backend:8000"),
                (b"x-forwarded-host", b"voice.example.com"),
                (b"x-forwarded-proto", b"https"),
            ],
            "scheme": "http",
            "server": ("backend", 8000),
        }
    )
    assert (
        _public_url(request, "/api/v1/voice/files/sample.mp3")
        == "https://voice.example.com/api/v1/voice/files/sample.mp3"
    )


def test_voice_file_route_rejects_invalid_filename() -> None:
    from fastapi import HTTPException

    from api.routers import voice as voice_router

    with pytest.raises(HTTPException) as exc:
        asyncio.run(voice_router.get_uploaded_file("../sample.wav"))

    assert exc.value.status_code == 400


def test_voice_config_normalizes_pasted_workspace_hostnames(monkeypatch) -> None:
    from api.services import voice_runtime

    async def fake_app_config():
        return SimpleNamespace(runtime=SimpleNamespace(api_key="runtime-key"))

    async def fake_section(category: str) -> dict:
        if category == "cosyvoice":
            return {
                "workspace_id": "llm-example",
                "base_http": "llm-example.cn-beijing.maas.aliyuncs.com",
                "base_ws": "llm-example.cn-beijing.maas.aliyuncs.com",
            }
        return {}

    monkeypatch.setattr(voice_runtime, "get_runtime_app_config", fake_app_config)
    monkeypatch.setattr(voice_runtime, "get_runtime_config_section", fake_section)

    config = asyncio.run(voice_runtime.load_voice_runtime_config())
    assert config.base_http == (
        "https://llm-example.cn-beijing.maas.aliyuncs.com/api/v1"
    )
    assert config.base_ws == (
        "wss://llm-example.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
    )


def test_voice_synthesize_returns_audio_and_provider_metadata(monkeypatch) -> None:
    from api.auth import User, UserRole
    from api.routers import voice as voice_router
    from api.services.voice_runtime import SynthesisResult

    calls: dict[str, object] = {}

    class FakeService:
        async def synthesize(self, db, **kwargs):
            calls["synthesize"] = {"db": db, **kwargs}
            return (
                "syn-test",
                SynthesisResult(
                    audio=b"fake-mp3",
                    model="qwen-audio-3.0-tts-flash",
                    request_id="req-test",
                    first_package_delay_ms=117,
                ),
            )

    monkeypatch.setattr(
        voice_router,
        "get_voice_runtime_service",
        lambda: FakeService(),
    )
    monkeypatch.setattr(voice_router, "get_db", lambda: "db")

    body = voice_router.SynthesizeReq(text="你好", voice_id="voice-1")
    user = User(username="tester", role=UserRole.USER)
    response = asyncio.run(voice_router.synthesize(body, user))

    assert response.media_type == "audio/mpeg"
    assert response.body == b"fake-mp3"
    assert response.headers["X-Request-Id"] == "req-test"
    assert response.headers["X-Record-Id"] == "syn-test"
    assert response.headers["X-First-Package-Delay-Ms"] == "117"
    assert response.headers["X-Voice-Model"] == "qwen-audio-3.0-tts-flash"
    assert response.headers["X-Synthetic-Media"] == "true"
    assert calls["synthesize"] == {
        "db": "db",
        "text": "你好",
        "voice_id": "voice-1",
        "requested_model": None,
        "instruction": None,
    }


def test_voice_stream_returns_pcm_chunks_and_headers(monkeypatch) -> None:
    from api.auth import User, UserRole
    from api.routers import voice as voice_router
    from api.services.voice_runtime import VoiceStreamHandle

    async def chunks():
        yield b"\x01\x00"
        yield b"\x02\x00"

    class FakeService:
        async def stream_synthesis(self, db, **kwargs):
            return VoiceStreamHandle(
                record_id="syn-stream",
                model="qwen-audio-3.0-tts-flash",
                sample_rate=24000,
                chunks=chunks(),
            )

    monkeypatch.setattr(
        voice_router,
        "get_voice_runtime_service",
        lambda: FakeService(),
    )
    monkeypatch.setattr(voice_router, "get_db", lambda: "db")

    body = voice_router.SynthesizeReq(text="你好", voice_id="voice-1")
    user = User(username="tester", role=UserRole.USER)
    response = asyncio.run(voice_router.synthesize_stream(body, user))

    async def read_body() -> bytes:
        return b"".join([chunk async for chunk in response.body_iterator])

    assert asyncio.run(read_body()) == b"\x01\x00\x02\x00"
    assert response.headers["X-Record-Id"] == "syn-stream"
    assert response.headers["X-Audio-Encoding"] == "pcm_s16le"
    assert response.headers["X-Audio-Sample-Rate"] == "24000"
    assert response.headers["X-Synthetic-Media"] == "true"


def test_voice_runtime_rejects_clone_model_mismatch(monkeypatch) -> None:
    from api.services import voice_runtime

    service = voice_runtime.VoiceRuntimeService()

    async def fake_runtime():
        return (
            SimpleNamespace(model="qwen-audio-3.0-tts-flash"),
            object(),
        )

    async def fake_clone(db, voice_id: str):
        assert db == "db"
        assert voice_id == "voice-legacy"
        return {"model": "cosyvoice-v3.5-plus"}

    monkeypatch.setattr(service, "_get_runtime", fake_runtime)
    monkeypatch.setattr(voice_runtime.voice_dao, "get_clone", fake_clone)

    with pytest.raises(voice_runtime.VoiceModelMismatchError):
        asyncio.run(
            service.resolve_model(
                "db",
                voice_id="voice-legacy",
                requested_model="qwen-audio-3.0-tts-flash",
            )
        )


def test_voice_runtime_persists_stream_metrics(monkeypatch) -> None:
    from api.services import voice_runtime

    calls: dict[str, object] = {}

    class FakeStream:
        audio_bytes = 96000
        first_package_delay_ms = 145
        total_elapsed_ms = 480
        audio_duration_ms = 2000
        rtf = 0.24
        request_id = "req-stream"

        async def iter_chunks(self):
            yield b"\x01\x00"
            yield b"\x02\x00"

        async def wait_closed(self):
            calls["waited"] = True

        async def cancel(self):
            calls["cancelled"] = True

    class FakeAdapter:
        async def stream(self, **kwargs):
            calls["stream"] = kwargs
            return FakeStream()

    service = voice_runtime.VoiceRuntimeService()

    async def fake_runtime():
        return (
            SimpleNamespace(
                model="qwen-audio-3.0-tts-flash",
                stream_sample_rate=24000,
            ),
            FakeAdapter(),
        )

    async def fake_resolve_model(db, **kwargs):
        return "qwen-audio-3.0-tts-flash"

    async def fake_create_record(db, **kwargs):
        calls["create"] = kwargs
        return "syn-stream"

    async def fake_complete_record(db, record_id: str, **kwargs):
        calls["complete"] = {"record_id": record_id, **kwargs}

    monkeypatch.setattr(service, "_get_runtime", fake_runtime)
    monkeypatch.setattr(service, "resolve_model", fake_resolve_model)
    monkeypatch.setattr(
        voice_runtime.voice_dao,
        "create_synthesis_record",
        fake_create_record,
    )
    monkeypatch.setattr(
        voice_runtime.voice_dao,
        "complete_synthesis_record",
        fake_complete_record,
    )

    async def run_stream() -> bytes:
        handle = await service.stream_synthesis(
            "db",
            text="实时测试",
            voice_id="voice-1",
            requested_model=None,
            instruction=None,
        )
        return b"".join([chunk async for chunk in handle.chunks])

    assert asyncio.run(run_stream()) == b"\x01\x00\x02\x00"
    assert calls["create"] == {
        "voice_id": "voice-1",
        "text": "实时测试",
        "model": "qwen-audio-3.0-tts-flash",
        "streaming": True,
        "audio_format": "pcm_s16le",
        "sample_rate": 24000,
    }
    assert calls["complete"] == {
        "record_id": "syn-stream",
        "audio_bytes": 96000,
        "first_pkg_delay_ms": 145,
        "total_latency_ms": 480,
        "audio_duration_ms": 2000,
        "rtf": 0.24,
        "request_id": "req-stream",
    }
    assert calls["waited"] is True


def test_voice_runtime_marks_early_stream_close_cancelled(monkeypatch) -> None:
    from api.services import voice_runtime

    calls: dict[str, int] = {"cancel_stream": 0, "cancel_record": 0}

    class FakeStream:
        audio_bytes = 2
        first_package_delay_ms = 100
        total_elapsed_ms = 100
        audio_duration_ms = 0
        rtf = 0.0
        request_id = None

        async def iter_chunks(self):
            yield b"\x01\x00"
            await asyncio.sleep(60)

        async def wait_closed(self):
            return None

        async def cancel(self):
            calls["cancel_stream"] += 1

    class FakeAdapter:
        async def stream(self, **kwargs):
            return FakeStream()

    service = voice_runtime.VoiceRuntimeService()

    async def fake_runtime():
        return (
            SimpleNamespace(
                model="qwen-audio-3.0-tts-flash",
                stream_sample_rate=24000,
            ),
            FakeAdapter(),
        )

    async def fake_resolve_model(db, **kwargs):
        return "qwen-audio-3.0-tts-flash"

    async def fake_create_record(db, **kwargs):
        return "syn-cancel"

    async def fake_cancel_record(db, record_id: str, reason: str):
        assert record_id == "syn-cancel"
        assert reason
        calls["cancel_record"] += 1

    monkeypatch.setattr(service, "_get_runtime", fake_runtime)
    monkeypatch.setattr(service, "resolve_model", fake_resolve_model)
    monkeypatch.setattr(
        voice_runtime.voice_dao,
        "create_synthesis_record",
        fake_create_record,
    )
    monkeypatch.setattr(
        voice_runtime.voice_dao,
        "cancel_synthesis_record",
        fake_cancel_record,
    )

    async def close_early() -> None:
        handle = await service.stream_synthesis(
            "db",
            text="取消测试",
            voice_id="voice-1",
            requested_model=None,
            instruction=None,
        )
        iterator = handle.chunks
        assert await anext(iterator) == b"\x01\x00"
        await iterator.aclose()

    asyncio.run(close_early())
    assert calls["cancel_stream"] >= 1
    assert calls["cancel_record"] == 1
