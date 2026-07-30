"""Bailian Qwen-Audio realtime WebSocket adapter."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import uuid
from typing import Any
from urllib.parse import urlencode

import websockets

from .contracts import (
    RealtimeProviderEvent,
    RealtimeSessionOptions,
    RealtimeVoiceConfig,
    RealtimeVoiceConnection,
)


class RealtimeVoiceProviderError(RuntimeError):
    pass


def _event(event_type: str, **payload: Any) -> str:
    return json.dumps(
        {
            "event_id": f"event_{uuid.uuid4().hex}",
            "type": event_type,
            **payload,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


class BailianRealtimeConnection:
    def __init__(self, websocket: Any) -> None:
        self._websocket = websocket
        self._send_lock = asyncio.Lock()

    async def _send(self, payload: str) -> None:
        async with self._send_lock:
            await self._websocket.send(payload)

    async def send_audio(self, audio: bytes) -> None:
        await self._send(
            _event(
                "input_audio_buffer.append",
                audio=base64.b64encode(audio).decode("ascii"),
            )
        )

    async def send_control(self, event_type: str) -> None:
        await self._send(_event(event_type))

    async def receive(self) -> RealtimeProviderEvent:
        message = await self._websocket.recv()
        if not isinstance(message, str):
            raise RealtimeVoiceProviderError("百炼全双工语音返回了非 JSON 消息")
        try:
            payload = json.loads(message)
        except (TypeError, ValueError) as exc:
            raise RealtimeVoiceProviderError("百炼全双工语音返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise RealtimeVoiceProviderError("百炼全双工语音返回结构无效")
        event_type = str(payload.get("type") or "")
        if not event_type:
            raise RealtimeVoiceProviderError("百炼全双工语音事件缺少 type")
        if event_type != "response.audio.delta":
            return RealtimeProviderEvent(type=event_type, payload=payload)
        encoded = payload.get("delta")
        if not isinstance(encoded, str):
            raise RealtimeVoiceProviderError("百炼全双工语音音频分片无效")
        try:
            audio = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise RealtimeVoiceProviderError("百炼全双工语音音频分片解码失败") from exc
        return RealtimeProviderEvent(type=event_type, payload=payload, audio=audio)

    async def close(self) -> None:
        await self._websocket.close()


class BailianQwenAudioRealtimeProvider:
    name = "bailian_qwen_audio"

    def __init__(self, config: RealtimeVoiceConfig) -> None:
        self.config = config

    def _session_payload(self, options: RealtimeSessionOptions) -> dict[str, Any]:
        turn_detection: dict[str, Any] | None
        if options.mode == "manual":
            turn_detection = None
        elif options.mode == "server_vad":
            turn_detection = {
                "type": "server_vad",
                "threshold": options.vad_threshold,
                "silence_duration_ms": options.vad_silence_ms,
            }
        else:
            turn_detection = {"type": "smart_turn"}
        return {
            "modalities": ["text", "audio"],
            "voice": options.voice,
            "instructions": options.instructions,
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "turn_detection": turn_detection,
            "max_history_turns": options.max_history_turns,
        }

    async def connect(
        self,
        options: RealtimeSessionOptions,
    ) -> RealtimeVoiceConnection:
        url = f"{self.config.endpoint}?{urlencode({'model': self.config.model})}"
        try:
            websocket = await websockets.connect(
                url,
                additional_headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                },
                open_timeout=self.config.connect_timeout_seconds,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_size=self.config.max_provider_message_bytes,
                compression=None,
            )
            connection = BailianRealtimeConnection(websocket)
            await connection._send(
                _event("session.update", session=self._session_payload(options))
            )
            return connection
        except Exception as exc:  # provider/library errors share one domain error
            raise RealtimeVoiceProviderError(
                f"百炼全双工语音连接失败: {exc}"
            ) from exc
