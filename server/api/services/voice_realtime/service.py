"""Session orchestration and browser protocol for full-duplex voice."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import suppress
from typing import Any

from fastapi import WebSocket
from motor.motor_asyncio import AsyncIOMotorDatabase
from starlette.websockets import WebSocketDisconnect, WebSocketState

from api.dao import voice as voice_dao
from api.services.media_output import MediaOutputError, get_media_output_service
from core.logger import get_logger
from core.observability import obs_log

from .adapters import RealtimeVoiceProviderError
from .config import RealtimeVoiceConfigurationError, load_realtime_voice_config
from .contracts import (
    RealtimeProviderEvent,
    RealtimeSessionOptions,
    RealtimeVoiceConfig,
    RealtimeVoiceConnection,
    SYSTEM_VOICES,
)
from .factory import RealtimeVoiceProviderFactory


logger = get_logger("api.services.voice_realtime")


class RealtimeVoiceError(RuntimeError):
    pass


_CLIENT_CONTROLS = {
    "input_audio_buffer.clear",
    "input_audio_buffer.commit",
    "response.cancel",
    "response.create",
}

_PUBLIC_EVENT_TYPES = {
    "session.created",
    "session.updated",
    "input_audio_buffer.cleared",
    "input_audio_buffer.committed",
    "input_audio_buffer.speech_started",
    "input_audio_buffer.speech_stopped",
    "conversation.item.input_audio_transcription.delta",
    "conversation.item.input_audio_transcription.completed",
    "response.created",
    "response.audio_transcript.delta",
    "response.audio_transcript.done",
    "response.text.delta",
    "response.text.done",
    "response.done",
    "response.cancelled",
    "error",
}


def _number(
    value: object,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        return min(maximum, max(minimum, float(value)))
    except (TypeError, ValueError):
        return default


def _integer(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        return min(maximum, max(minimum, int(value)))
    except (TypeError, ValueError):
        return default


def _event_text(payload: dict[str, Any], *, max_bytes: int = 128 * 1024) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > max_bytes:
        raise RealtimeVoiceError("全双工语音事件超过安全大小限制")
    return encoded


def _public_provider_event(event: RealtimeProviderEvent) -> dict[str, Any] | None:
    if event.type not in _PUBLIC_EVENT_TYPES:
        return None
    payload = event.payload
    public: dict[str, Any] = {"type": event.type}
    for key in (
        "event_id",
        "item_id",
        "response_id",
        "content_index",
        "output_index",
        "transcript",
        "delta",
    ):
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if value is not None:
                public[key] = value

    if event.type.startswith("session."):
        session = payload.get("session")
        if isinstance(session, dict):
            public["session"] = {
                key: session.get(key)
                for key in (
                    "id",
                    "model",
                    "voice",
                    "modalities",
                    "turn_detection",
                    "max_history_turns",
                )
                if session.get(key) is not None
            }
    elif event.type.startswith("response.") and isinstance(payload.get("response"), dict):
        response = payload["response"]
        public["response"] = {
            key: response.get(key)
            for key in ("id", "status", "status_details")
            if response.get(key) is not None
        }
    elif event.type == "error":
        error = payload.get("error")
        if isinstance(error, dict):
            public["error"] = {
                key: error.get(key)
                for key in ("type", "code", "message")
                if error.get(key) is not None
            }
        else:
            public["error"] = {"message": "百炼全双工语音返回错误"}
    return public


class RealtimeVoiceSessionService:
    def __init__(self) -> None:
        self._connections: set[RealtimeVoiceConnection] = set()
        self._connections_lock = asyncio.Lock()

    async def metadata(self, db: AsyncIOMotorDatabase) -> dict[str, Any]:
        config = await load_realtime_voice_config()
        async with self._connections_lock:
            active_sessions = len(self._connections)
        clones, _ = await voice_dao.list_clones(
            db,
            status="active",
            model=config.model,
            limit=100,
        )
        return {
            "available": True,
            "provider": config.provider,
            "model": config.model,
            "active_sessions": active_sessions,
            "remote_output_supported": True,
            "input_audio": {
                "encoding": "pcm_s16le",
                "sample_rate": 16000,
                "channels": 1,
            },
            "output_audio": {
                "encoding": "pcm_s16le",
                "sample_rate": 24000,
                "channels": 1,
            },
            "default_voice": config.default_voice,
            "default_mode": config.default_mode,
            "default_instructions": config.default_instructions,
            "max_history_turns": config.max_history_turns,
            "system_voices": [
                {"voice_id": voice_id, "label": label, "kind": "system"}
                for voice_id, label in SYSTEM_VOICES
            ],
            "cloned_voices": [
                {
                    "voice_id": row["voice_id"],
                    "label": row.get("prefix") or row["voice_id"],
                    "kind": "clone",
                    "model": row.get("model"),
                }
                for row in clones
            ],
            "turn_modes": ["smart_turn", "server_vad", "manual"],
        }

    async def _session_options(
        self,
        db: AsyncIOMotorDatabase,
        payload: dict[str, Any],
        config: RealtimeVoiceConfig,
    ) -> RealtimeSessionOptions:
        if payload.get("type") != "session.start":
            raise RealtimeVoiceError("首个消息必须是 session.start")
        requested_model = str(payload.get("model") or config.model).strip()
        if requested_model != config.model:
            raise RealtimeVoiceError(
                f"当前全双工语音模型固定为 {config.model}"
            )

        voice = str(payload.get("voice") or config.default_voice).strip()
        system_voice_ids = {voice_id for voice_id, _ in SYSTEM_VOICES}
        if voice not in system_voice_ids:
            clone = await voice_dao.get_clone(db, voice)
            if not clone or clone.get("status") != "active":
                raise RealtimeVoiceError("所选复刻音色不存在或不可用")
            if clone.get("model") != config.model:
                raise RealtimeVoiceError(
                    f"所选音色绑定 {clone.get('model')}，不能用于 {config.model}"
                )

        mode = str(payload.get("mode") or config.default_mode).strip()
        if mode not in {"smart_turn", "server_vad", "manual"}:
            raise RealtimeVoiceError("不支持的语音轮次检测模式")
        instructions = str(
            payload.get("instructions")
            if payload.get("instructions") is not None
            else config.default_instructions
        ).strip()
        if len(instructions) > 4000:
            raise RealtimeVoiceError("会话指令不能超过 4000 个字符")

        return RealtimeSessionOptions(
            voice=voice,
            mode=mode,  # type: ignore[arg-type]
            instructions=instructions,
            max_history_turns=_integer(
                payload.get("max_history_turns"),
                default=config.max_history_turns,
                minimum=1,
                maximum=50,
            ),
            vad_threshold=_number(
                payload.get("vad_threshold"),
                default=config.vad_threshold,
                minimum=-1.0,
                maximum=1.0,
            ),
            vad_silence_ms=_integer(
                payload.get("vad_silence_ms"),
                default=config.vad_silence_ms,
                minimum=200,
                maximum=6000,
            ),
        )

    async def _receive_start(self, websocket: WebSocket) -> dict[str, Any]:
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=15)
            payload = json.loads(raw)
        except TimeoutError as exc:
            raise RealtimeVoiceError("等待 session.start 超时") from exc
        except (ValueError, TypeError) as exc:
            raise RealtimeVoiceError("session.start 不是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise RealtimeVoiceError("session.start 结构无效")
        return payload

    async def _browser_to_provider(
        self,
        websocket: WebSocket,
        connection: RealtimeVoiceConnection,
        config: RealtimeVoiceConfig,
        counters: dict[str, int],
    ) -> None:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            audio = message.get("bytes")
            if audio is not None:
                if not isinstance(audio, bytes):
                    raise RealtimeVoiceError("麦克风音频分片类型无效")
                if not audio or len(audio) > config.max_audio_chunk_bytes:
                    raise RealtimeVoiceError("麦克风音频分片大小无效")
                if len(audio) % 2:
                    raise RealtimeVoiceError("麦克风 PCM 分片必须按 16-bit 对齐")
                await connection.send_audio(audio)
                counters["input_bytes"] += len(audio)
                continue

            raw = message.get("text")
            if raw is None:
                continue
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise RealtimeVoiceError("全双工语音控制消息不是有效 JSON") from exc
            if not isinstance(payload, dict):
                raise RealtimeVoiceError("全双工语音控制消息结构无效")
            event_type = str(payload.get("type") or "")
            if event_type == "session.stop":
                return
            if event_type == "session.ping":
                await websocket.send_text(_event_text({"type": "session.pong"}))
                continue
            if event_type not in _CLIENT_CONTROLS:
                raise RealtimeVoiceError(f"不允许的全双工语音控制事件: {event_type}")
            await connection.send_control(event_type)

    async def _provider_to_browser(
        self,
        websocket: WebSocket,
        connection: RealtimeVoiceConnection,
        counters: dict[str, int],
        *,
        output_session_id: str,
        username: str,
    ) -> None:
        is_responding = False
        suppress_audio = False
        while True:
            event = await connection.receive()
            counters["provider_events"] += 1
            if event.type == "response.created":
                is_responding = True
                suppress_audio = False
            elif event.type in {"response.done", "response.cancelled"}:
                is_responding = False
            elif event.type == "input_audio_buffer.speech_started":
                suppress_audio = is_responding
                if is_responding:
                    await connection.send_control("response.cancel")
                    is_responding = False

            if event.audio is not None:
                if suppress_audio:
                    continue
                counters["output_bytes"] += len(event.audio)
                if output_session_id:
                    try:
                        await get_media_output_service().publish_audio(
                            output_session_id,
                            username,
                            event.audio,
                        )
                    except MediaOutputError as exc:
                        logger.warning(
                            "远端 OBS 音频输出已停止: session=%s error=%s",
                            output_session_id,
                            exc,
                        )
                        output_session_id = ""
                await websocket.send_bytes(event.audio)
                continue

            public = _public_provider_event(event)
            if public is not None:
                await websocket.send_text(_event_text(public))

    async def run(
        self,
        websocket: WebSocket,
        db: AsyncIOMotorDatabase,
        *,
        username: str,
    ) -> None:
        session_id = f"rvoice-{uuid.uuid4().hex[:16]}"
        started = time.perf_counter()
        counters = {"input_bytes": 0, "output_bytes": 0, "provider_events": 0}
        connection: RealtimeVoiceConnection | None = None
        config: RealtimeVoiceConfig | None = None
        status = "completed"
        try:
            config = await load_realtime_voice_config()
            start_payload = await self._receive_start(websocket)
            options = await self._session_options(db, start_payload, config)
            output_session_id = str(
                start_payload.get("output_session_id") or ""
            ).strip()
            if len(output_session_id) > 96:
                raise RealtimeVoiceError("远端 OBS 输出会话 ID 无效")
            if output_session_id:
                await get_media_output_service().require_owner(
                    output_session_id,
                    username,
                )
            provider = RealtimeVoiceProviderFactory.create(config)
            connection = await provider.connect(options)
            async with self._connections_lock:
                self._connections.add(connection)
            await websocket.send_text(
                _event_text(
                    {
                        "type": "session.ready",
                        "session_id": session_id,
                        "provider": provider.name,
                        "model": config.model,
                        "voice": options.voice,
                        "mode": options.mode,
                        "output_session_id": output_session_id,
                        "input_sample_rate": 16000,
                        "output_sample_rate": 24000,
                    }
                )
            )
            obs_log(
                "百炼全双工语音会话开始",
                task_id=session_id,
                source="voice_realtime",
                level="info",
                event="voice_realtime_start",
                data={
                    "username": username,
                    "model": config.model,
                    "mode": options.mode,
                    "output_session_id": output_session_id,
                },
            )

            tasks: set[asyncio.Task[None]] = set()
            try:
                async with asyncio.timeout(config.session_timeout_seconds):
                    browser_task = asyncio.create_task(
                        self._browser_to_provider(websocket, connection, config, counters),
                        name=f"{session_id}-browser",
                    )
                    provider_task = asyncio.create_task(
                        self._provider_to_browser(
                            websocket,
                            connection,
                            counters,
                            output_session_id=output_session_id,
                            username=username,
                        ),
                        name=f"{session_id}-provider",
                    )
                    tasks = {browser_task, provider_task}
                    done, pending = await asyncio.wait(
                        tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    for task in done:
                        task.result()
            finally:
                remaining = [task for task in tasks if not task.done()]
                for task in remaining:
                    task.cancel()
                await asyncio.gather(*remaining, return_exceptions=True)
        except WebSocketDisconnect:
            status = "cancelled"
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except TimeoutError:
            status = "timeout"
            await self._send_error(websocket, "全双工语音会话已达到最长时限")
        except (
            RealtimeVoiceConfigurationError,
            RealtimeVoiceProviderError,
            RealtimeVoiceError,
            MediaOutputError,
            ValueError,
        ) as exc:
            status = "failed"
            logger.warning("全双工语音会话失败: session=%s error=%s", session_id, exc)
            await self._send_error(websocket, str(exc))
        except Exception:
            status = "failed"
            logger.exception("全双工语音会话异常: session=%s", session_id)
            await self._send_error(websocket, "全双工语音会话异常中断")
        finally:
            if connection is not None:
                async with self._connections_lock:
                    self._connections.discard(connection)
                with suppress(Exception):
                    await connection.close()
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            obs_log(
                "百炼全双工语音会话结束",
                task_id=session_id,
                source="voice_realtime",
                level="error" if status == "failed" else "info",
                event="voice_realtime_done" if status == "completed" else "voice_realtime_end",
                data={
                    "username": username,
                    "model": config.model if config else "",
                    "status": status,
                    "elapsed_ms": elapsed_ms,
                    **counters,
                },
            )
            logger.info(
                "全双工语音会话结束: session=%s status=%s elapsed=%sms input=%s output=%s",
                session_id,
                status,
                elapsed_ms,
                counters["input_bytes"],
                counters["output_bytes"],
            )

    @staticmethod
    async def _send_error(websocket: WebSocket, message: str) -> None:
        if websocket.client_state != WebSocketState.CONNECTED:
            return
        with suppress(Exception):
            await websocket.send_text(
                _event_text({"type": "session.error", "message": message})
            )

    async def close(self) -> None:
        async with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        await asyncio.gather(
            *(connection.close() for connection in connections),
            return_exceptions=True,
        )


_realtime_voice_service = RealtimeVoiceSessionService()


def get_realtime_voice_service() -> RealtimeVoiceSessionService:
    return _realtime_voice_service


async def shutdown_realtime_voice_service() -> None:
    await _realtime_voice_service.close()
