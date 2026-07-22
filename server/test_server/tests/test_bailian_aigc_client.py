"""Aliyun Bailian AIGC client payload tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _make_client(
    region: str = "beijing",
    workspace_id: str | None = "ws1",
    base_url: str | None = None,
):
    from api.services.bailian_aigc import BailianAIGCClient, BailianRuntimeConfig

    class FakeBailianClient(BailianAIGCClient):
        def __init__(self, config: BailianRuntimeConfig):
            super().__init__(config)
            self.calls: list[dict] = []

        async def _post_json(self, url: str, payload: dict, *, async_task: bool = False) -> dict:
            self.calls.append({"url": url, "payload": payload, "async_task": async_task})
            return {"output": {"task_id": "task-1", "task_status": "PENDING"}}

    return FakeBailianClient(
        BailianRuntimeConfig(
            api_key="sk-test",
            workspace_id=workspace_id,
            region=region,
            base_url=base_url,
            legacy_base_url=None,
            timeout_seconds=30,
            qwen_image_edit_model="qwen-image-3.0-pro",
            wanx_image_edit_model="wanx2.1-imageedit",
            text_to_video_model="wan2.7-t2v-2026-06-12",
            image_to_video_model="wan2.7-i2v-2026-04-25",
        )
    )


def test_workspace_hostname_is_normalized_to_https_api_endpoint() -> None:
    client = _make_client(
        workspace_id="llm-example",
        base_url="llm-example.cn-beijing.maas.aliyuncs.com",
    )

    assert (
        client.workspace_base_url()
        == "https://llm-example.cn-beijing.maas.aliyuncs.com/api/v1"
    )


def test_bailian_config_defaults_to_qwen_image_30_pro(monkeypatch) -> None:
    from api.services import bailian_aigc

    async def fake_app_config():
        return SimpleNamespace(runtime=SimpleNamespace(api_key="sk-test"))

    async def fake_bailian_section(_category: str):
        return {"workspace_id": "ws1", "region": "beijing"}

    monkeypatch.setattr(bailian_aigc, "get_runtime_app_config", fake_app_config)
    monkeypatch.setattr(
        bailian_aigc,
        "get_runtime_config_section",
        fake_bailian_section,
    )

    config = asyncio.run(bailian_aigc.load_bailian_config())

    assert config.qwen_image_edit_model == "qwen-image-3.0-pro"


def test_image_to_video_uses_wan27_media_payload() -> None:
    async def run() -> None:
        client = _make_client(region="beijing")
        result = await client.image_to_video(
            img_url="https://example.com/first.png",
            last_frame_url="https://example.com/last.png",
            audio_url="https://example.com/audio.mp3",
            prompt="make it move",
        )
        call = client.calls[0]

        assert result["payload_protocol"] == "wan2.7"
        assert result["task_protocol"] == "legacy"
        assert call["async_task"] is True
        assert call["url"] == "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
        assert call["payload"]["model"] == "wan2.7-i2v-2026-04-25"
        assert call["payload"]["input"]["media"] == [
            {"type": "first_frame", "url": "https://example.com/first.png"},
            {"type": "last_frame", "url": "https://example.com/last.png"},
            {"type": "driving_audio", "url": "https://example.com/audio.mp3"},
        ]
        assert "img_url" not in call["payload"]["input"]

    asyncio.run(run())


def test_image_to_video_legacy_payload_still_supported() -> None:
    async def run() -> None:
        client = _make_client(region="beijing", workspace_id=None)
        result = await client.image_to_video(
            img_url="https://example.com/first.png",
            prompt="make it move",
            model="wan2.6-i2v-flash",
            protocol="legacy",
        )
        call = client.calls[0]

        assert result["payload_protocol"] == "legacy"
        assert result["task_protocol"] == "legacy"
        assert call["payload"]["model"] == "wan2.6-i2v-flash"
        assert call["payload"]["input"]["img_url"] == "https://example.com/first.png"
        assert "media" not in call["payload"]["input"]

    asyncio.run(run())


def test_aigc_routes_delegate_to_runtime_client(monkeypatch) -> None:
    from api.routers import aigc as aigc_router

    calls: list[tuple[str, dict]] = []

    class FakeClient:
        config = SimpleNamespace(
            api_key="sk-test",
            workspace_id="ws1",
            region="beijing",
            qwen_image_edit_model="qwen-image-3.0-pro",
            wanx_image_edit_model="wanx2.1-imageedit",
            text_to_video_model="wan2.7-t2v-2026-06-12",
            image_to_video_model="wan2.7-i2v-2026-04-25",
        )

        async def text_to_video(self, **kwargs):
            calls.append(("text_to_video", kwargs))
            return {"ok": True, "task_id": "t2v-task", "task_protocol": "workspace"}

        async def image_to_video(self, **kwargs):
            calls.append(("image_to_video", kwargs))
            return {
                "ok": True,
                "task_id": "i2v-task",
                "task_protocol": "legacy",
                "payload_protocol": "wan2.7",
            }

        async def query_task(self, task_id: str, *, protocol: str = "workspace"):
            calls.append(("query_task", {"task_id": task_id, "protocol": protocol}))
            return {"ok": True, "task_id": task_id, "task_protocol": protocol}

    async def fake_client():
        return FakeClient()

    monkeypatch.setattr(aigc_router, "get_bailian_client", fake_client)

    async def run() -> None:
        cfg = await aigc_router.aigc_config()
        assert cfg["configured"] is True
        assert cfg["has_api_key"] is True
        assert cfg["has_workspace_id"] is True
        assert cfg["text_to_video_model"] == "wan2.7-t2v-2026-06-12"

        t2v = await aigc_router.text_to_video(
            aigc_router.TextToVideoReq(
                prompt="生成一段产品演示视频",
                negative_prompt="低清晰度",
                audio_url="https://example.com/audio.mp3",
                parameters={"duration": 5},
            )
        )
        assert t2v["task_id"] == "t2v-task"
        assert calls[-1] == (
            "text_to_video",
            {
                "prompt": "生成一段产品演示视频",
                "negative_prompt": "低清晰度",
                "audio_url": "https://example.com/audio.mp3",
                "model": None,
                "parameters": {"duration": 5},
            },
        )

        i2v = await aigc_router.image_to_video(
            aigc_router.ImageToVideoReq(
                image_url="https://example.com/first.png",
                last_frame_url="https://example.com/last.png",
                prompt="让画面自然动起来",
                protocol="auto",
            )
        )
        assert i2v["payload_protocol"] == "wan2.7"
        assert calls[-1][0] == "image_to_video"
        assert calls[-1][1]["img_url"] == "https://example.com/first.png"
        assert calls[-1][1]["last_frame_url"] == "https://example.com/last.png"
        assert calls[-1][1]["protocol"] == "auto"

        task = await aigc_router.query_task("task-123", protocol="legacy")
        assert task["task_protocol"] == "legacy"
        assert calls[-1] == ("query_task", {"task_id": "task-123", "protocol": "legacy"})

    asyncio.run(run())


def test_aigc_route_validation_and_error_mapping(monkeypatch) -> None:
    from api.routers import aigc as aigc_router
    from api.services.bailian_aigc import BailianAPIError

    async def fake_client_error():
        raise BailianAPIError(
            "Bailian api_key is not configured",
            status_code=500,
            payload={"category": "bailian"},
        )

    async def run() -> None:
        with pytest.raises(HTTPException) as missing_input:
            await aigc_router.image_to_video(aigc_router.ImageToVideoReq(prompt="缺少图片"))
        assert missing_input.value.status_code == 422
        assert "img_url" in str(missing_input.value.detail)

        monkeypatch.setattr(aigc_router, "get_bailian_client", fake_client_error)
        with pytest.raises(HTTPException) as mapped_error:
            await aigc_router.qwen_image_edit(
                aigc_router.QwenImageEditReq(
                    images=["https://example.com/input.png"],
                    prompt="换背景",
                )
            )
        assert mapped_error.value.status_code == 500
        assert mapped_error.value.detail == {
            "message": "Bailian api_key is not configured",
            "payload": {"category": "bailian"},
        }

    asyncio.run(run())
