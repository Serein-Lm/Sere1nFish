"""CosyVoice/TTS routing tests without real Aliyun calls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_voice_config_prefers_cosyvoice_and_builds_workspace_endpoint(monkeypatch) -> None:
    from api.routers import voice as voice_router

    async def fake_app_config():
        return SimpleNamespace(runtime=SimpleNamespace(api_key="runtime-key"))

    async def fake_section(category: str) -> dict:
        if category == "cosyvoice":
            return {
                "api_key": "cosy-key",
                "region": "singapore",
                "workspace_id": "ws-123",
                "model": "cosyvoice-v3.5-plus",
                "prefix": "tester",
                "language_hints": ["zh", "en"],
                "max_prompt_audio_length": 12,
                "enable_preprocess": True,
            }
        if category == "bailian":
            return {"api_key": "bailian-key", "region": "beijing"}
        return {}

    monkeypatch.setattr(voice_router, "get_runtime_app_config", fake_app_config)
    monkeypatch.setattr(voice_router, "get_runtime_config_section", fake_section)

    cfg = asyncio.run(voice_router._cfg())
    assert cfg["api_key"] == "cosy-key"
    assert cfg["base_http"] == "https://ws-123.ap-southeast-1.maas.aliyuncs.com/api/v1"
    assert cfg["base_ws"] == "wss://ws-123.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/inference"
    assert cfg["language_hints"] == ["zh", "en"]
    assert cfg["enable_preprocess"] is True


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


def test_voice_config_normalizes_pasted_workspace_hostnames(monkeypatch) -> None:
    from api.routers import voice as voice_router

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

    monkeypatch.setattr(voice_router, "get_runtime_app_config", fake_app_config)
    monkeypatch.setattr(voice_router, "get_runtime_config_section", fake_section)

    cfg = asyncio.run(voice_router._cfg())

    assert cfg["base_http"] == "https://llm-example.cn-beijing.maas.aliyuncs.com/api/v1"
    assert cfg["base_ws"] == (
        "wss://llm-example.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
    )

def test_voice_synthesize_returns_audio_and_records_metadata(monkeypatch) -> None:
    from api.auth import User, UserRole
    from api.routers import voice as voice_router

    calls: dict[str, object] = {}

    async def fake_init_sdk() -> dict:
        return {"model": "cosyvoice-v3.5-plus"}

    async def fake_create_record(db, *, voice_id: str, text: str, model: str) -> str:
        calls["create"] = {"db": db, "voice_id": voice_id, "text": text, "model": model}
        return "syn-test"

    async def fake_complete_record(db, record_id: str, **kwargs) -> None:
        calls["complete"] = {"db": db, "record_id": record_id, **kwargs}

    async def fake_fail_record(db, record_id: str, error: str) -> None:
        calls["fail"] = {"db": db, "record_id": record_id, "error": error}

    class FakeSynthesizer:
        def __init__(self, *, model: str, voice: str):
            calls["synth_init"] = {"model": model, "voice": voice}

        def call(self, text: str) -> bytes:
            calls["synth_text"] = text
            return b"fake-mp3"

        def get_last_request_id(self) -> str:
            return "req-test"

        def get_first_package_delay(self) -> int:
            return 17

    monkeypatch.setattr(voice_router, "_init_sdk", fake_init_sdk)
    monkeypatch.setattr(voice_router, "get_db", lambda: "db")
    monkeypatch.setattr(voice_router.voice_dao, "create_synthesis_record", fake_create_record)
    monkeypatch.setattr(voice_router.voice_dao, "complete_synthesis_record", fake_complete_record)
    monkeypatch.setattr(voice_router.voice_dao, "fail_synthesis_record", fake_fail_record)
    monkeypatch.setattr(voice_router, "SpeechSynthesizer", FakeSynthesizer)

    body = voice_router.SynthesizeReq(text="你好", voice_id="voice-1")
    user = User(username="tester", role=UserRole.USER)
    resp = asyncio.run(voice_router.synthesize(body, user))

    assert resp.media_type == "audio/mpeg"
    assert resp.body == b"fake-mp3"
    assert resp.headers["X-Request-Id"] == "req-test"
    assert resp.headers["X-Record-Id"] == "syn-test"
    assert resp.headers["X-First-Package-Delay-Ms"] == "17"
    assert calls["create"] == {
        "db": "db",
        "voice_id": "voice-1",
        "text": "你好",
        "model": "cosyvoice-v3.5-plus",
    }
    assert calls["synth_init"] == {"model": "cosyvoice-v3.5-plus", "voice": "voice-1"}
    assert calls["synth_text"] == "你好"
    assert calls["complete"] == {
        "db": "db",
        "record_id": "syn-test",
        "audio_bytes": 8,
        "first_pkg_delay_ms": 17,
        "request_id": "req-test",
    }
    assert "fail" not in calls
