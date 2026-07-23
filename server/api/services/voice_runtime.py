"""Unified Bailian voice cloning and realtime synthesis runtime."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import dashscope
from dashscope.audio.tts_v2 import (
    AudioFormat,
    ResultCallback,
    SpeechSynthesizer,
    SpeechSynthesizerObjectPool,
    VoiceEnrollmentService,
)
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import voice as voice_dao
from api.services.bailian_aigc import (
    BailianAPIError,
    normalize_bailian_http_base,
    normalize_bailian_ws_base,
)
from api.services.runtime_config import (
    get_runtime_app_config,
    get_runtime_config_section,
)
from core.logger import get_logger


logger = get_logger("api.services.voice_runtime")

LATEST_REALTIME_VOICE_MODEL = "qwen-audio-3.0-tts-flash"
DEFAULT_STREAM_SAMPLE_RATE = 24000
SUPPORTED_VOICE_MODELS = {
    "qwen-audio-3.0-tts-flash",
    "qwen-audio-3.0-tts-plus",
    "cosyvoice-v3.5-flash",
    "cosyvoice-v3.5-plus",
    "cosyvoice-v3-flash",
    "cosyvoice-v3-plus",
    "cosyvoice-v2",
}
SUPPORTED_LANGUAGE_HINTS = {
    "zh",
    "en",
    "ja",
    "ko",
    "fr",
    "de",
    "ru",
    "pt",
    "th",
    "id",
    "vi",
    "it",
    "es",
    "ms",
    "fil",
    "ar",
}


class VoiceRuntimeError(RuntimeError):
    """Base voice runtime failure."""


class VoiceConfigurationError(VoiceRuntimeError):
    """Voice runtime configuration is incomplete or invalid."""


class VoiceProviderError(VoiceRuntimeError):
    """Bailian rejected or failed a voice operation."""


class VoiceModelMismatchError(VoiceRuntimeError):
    """A cloned voice was used with a different synthesis model."""


def _stored_source_url(url: str) -> str:
    """Remove short-lived query credentials before persisting a source URL."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        return url
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


@dataclass(frozen=True)
class VoiceRuntimeConfig:
    api_key: str
    model: str
    region: str
    workspace_id: str | None
    prefix: str
    language_hints: list[str]
    max_prompt_audio_length: float
    enable_preprocess: bool
    base_http: str
    base_ws: str
    pool_size: int
    stream_sample_rate: int

    @property
    def fingerprint(self) -> str:
        payload = {
            "api_key_hash": hashlib.sha256(self.api_key.encode()).hexdigest(),
            "model": self.model,
            "workspace_id": self.workspace_id,
            "base_http": self.base_http,
            "base_ws": self.base_ws,
            "pool_size": self.pool_size,
            "stream_sample_rate": self.stream_sample_rate,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()


@dataclass(frozen=True)
class VoiceEnrollmentResult:
    voice_id: str
    model: str
    request_id: str | None


@dataclass(frozen=True)
class SynthesisResult:
    audio: bytes
    model: str
    request_id: str | None
    first_package_delay_ms: int


@dataclass(frozen=True)
class VoiceStreamHandle:
    record_id: str
    model: str
    sample_rate: int
    chunks: AsyncIterator[bytes]


async def load_voice_runtime_config() -> VoiceRuntimeConfig:
    app_config = await get_runtime_app_config()
    cosyvoice = await get_runtime_config_section("cosyvoice")
    bailian = await get_runtime_config_section("bailian")
    runtime = app_config.runtime
    api_key = cosyvoice.get("api_key") or bailian.get("api_key") or runtime.api_key
    if not api_key:
        raise VoiceConfigurationError(
            "数据库 cosyvoice.api_key/bailian.api_key/runtime.api_key 未配置"
        )

    region = str(
        cosyvoice.get("region") or bailian.get("region") or "beijing"
    ).strip().lower()
    workspace_id = cosyvoice.get("workspace_id") or bailian.get("workspace_id")
    base_http = cosyvoice.get("base_http") or bailian.get("base_http")
    base_ws = cosyvoice.get("base_ws") or bailian.get("base_ws")

    if not base_http or not base_ws:
        if workspace_id and region == "singapore":
            base_http = (
                base_http
                or f"https://{workspace_id}.ap-southeast-1.maas.aliyuncs.com/api/v1"
            )
            base_ws = (
                base_ws
                or f"wss://{workspace_id}.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/inference"
            )
        elif workspace_id:
            base_http = (
                base_http
                or f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com/api/v1"
            )
            base_ws = (
                base_ws
                or f"wss://{workspace_id}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
            )
        elif region == "singapore":
            base_http = base_http or "https://dashscope-intl.aliyuncs.com/api/v1"
            base_ws = (
                base_ws
                or "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference"
            )
        else:
            base_http = base_http or "https://dashscope.aliyuncs.com/api/v1"
            base_ws = (
                base_ws
                or "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
            )

    try:
        base_http = normalize_bailian_http_base(str(base_http))
        base_ws = normalize_bailian_ws_base(str(base_ws))
    except BailianAPIError as exc:
        raise VoiceConfigurationError(str(exc)) from exc

    model = str(
        cosyvoice.get("model") or LATEST_REALTIME_VOICE_MODEL
    ).strip()
    if model not in SUPPORTED_VOICE_MODELS:
        raise VoiceConfigurationError(f"不支持的实时声音复刻模型: {model}")

    try:
        pool_size = max(1, min(8, int(cosyvoice.get("pool_size", 2))))
        sample_rate = int(
            cosyvoice.get("stream_sample_rate", DEFAULT_STREAM_SAMPLE_RATE)
        )
        max_prompt_audio_length = float(
            cosyvoice.get("max_prompt_audio_length", 20.0)
        )
    except (TypeError, ValueError) as exc:
        raise VoiceConfigurationError("CosyVoice 数值配置无效") from exc
    if sample_rate not in {16000, 24000, 48000}:
        raise VoiceConfigurationError(
            "stream_sample_rate 仅支持 16000、24000 或 48000"
        )
    configured_hints = cosyvoice.get("language_hints") or ["zh"]
    if isinstance(configured_hints, str):
        configured_hints = [
            item.strip()
            for item in configured_hints.split(",")
            if item.strip()
        ]
    if not isinstance(configured_hints, (list, tuple, set)):
        raise VoiceConfigurationError("language_hints 必须是语种代码列表")
    language_hints = list(dict.fromkeys(str(item).strip() for item in configured_hints))
    if not language_hints or any(
        hint not in SUPPORTED_LANGUAGE_HINTS for hint in language_hints
    ):
        raise VoiceConfigurationError(
            "language_hints 包含不支持的语种代码"
        )
    prefix = str(cosyvoice.get("prefix") or "sere1nfish").strip()
    if (
        not prefix
        or len(prefix) > 10
        or not prefix.isascii()
        or not prefix.isalnum()
        or prefix.lower() != prefix
    ):
        raise VoiceConfigurationError(
            "声音复刻 prefix 仅支持 1-10 位小写字母和数字"
        )

    return VoiceRuntimeConfig(
        api_key=str(api_key),
        model=model,
        region=region,
        workspace_id=str(workspace_id) if workspace_id else None,
        prefix=prefix,
        language_hints=language_hints,
        max_prompt_audio_length=max(3.0, min(60.0, max_prompt_audio_length)),
        enable_preprocess=bool(cosyvoice.get("enable_preprocess", False)),
        base_http=base_http,
        base_ws=base_ws,
        pool_size=pool_size,
        stream_sample_rate=sample_rate,
    )


def _pcm_format(sample_rate: int) -> AudioFormat:
    formats = {
        16000: AudioFormat.PCM_16000HZ_MONO_16BIT,
        24000: AudioFormat.PCM_24000HZ_MONO_16BIT,
        48000: AudioFormat.PCM_48000HZ_MONO_16BIT,
    }
    return formats[sample_rate]


class _StreamCallback(ResultCallback):
    def __init__(self, stream: "DashScopePCMStream") -> None:
        self._stream = stream

    def on_data(self, data: bytes) -> None:
        self._stream.on_data(bytes(data))

    def on_complete(self) -> None:
        self._stream.on_complete()

    def on_error(self, message: str) -> None:
        self._stream.on_error(str(message))


class DashScopePCMStream:
    """Bridge DashScope callback threads into an async PCM iterator."""

    def __init__(
        self,
        *,
        adapter: "DashScopeVoiceAdapter",
        text: str,
        model: str,
        voice_id: str,
        instruction: str | None,
    ) -> None:
        self.adapter = adapter
        self.text = text
        self.model = model
        self.voice_id = voice_id
        self.instruction = instruction
        self.sample_rate = adapter.config.stream_sample_rate
        self.queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self.loop = asyncio.get_running_loop()
        self.synthesizer: SpeechSynthesizer | None = None
        self.worker: asyncio.Task[None] | None = None
        self.started_at = 0.0
        self.first_package_delay_ms = 0
        self.total_elapsed_ms = 0
        self.audio_bytes = 0
        self.request_id: str | None = None
        self.completed = False
        self.cancelled = False
        self.failed = False
        self._terminal_lock = threading.Lock()
        self._terminal_emitted = False

    async def start(self) -> None:
        callback = _StreamCallback(self)
        self.synthesizer = await asyncio.to_thread(
            self.adapter.borrow_synthesizer,
            model=self.model,
            voice_id=self.voice_id,
            audio_format=_pcm_format(self.sample_rate),
            instruction=self.instruction,
            callback=callback,
        )
        self.started_at = time.perf_counter()
        self.worker = asyncio.create_task(
            asyncio.to_thread(self._run_sync),
            name=f"voice-stream-{self.voice_id[-12:]}",
        )

    def _run_sync(self) -> None:
        assert self.synthesizer is not None
        try:
            self.synthesizer.streaming_call(self.text)
            self.synthesizer.streaming_complete()
            self.on_complete()
        except Exception as exc:  # noqa: BLE001 - SDK errors are provider failures
            self.on_error(str(exc))
        finally:
            self.request_id = self.synthesizer.get_last_request_id()
            self.total_elapsed_ms = int(
                max(0.0, (time.perf_counter() - self.started_at) * 1000)
            )
            self.adapter.return_synthesizer(self.synthesizer)

    def _emit(self, kind: str, payload: Any = None) -> None:
        self.loop.call_soon_threadsafe(self.queue.put_nowait, (kind, payload))

    def _emit_terminal(self, kind: str, payload: Any = None) -> None:
        with self._terminal_lock:
            if self._terminal_emitted:
                return
            self._terminal_emitted = True
        self._emit(kind, payload)

    def on_data(self, data: bytes) -> None:
        if not data or self.cancelled:
            return
        if not self.first_package_delay_ms:
            self.first_package_delay_ms = int(
                max(0.0, (time.perf_counter() - self.started_at) * 1000)
            )
        self.audio_bytes += len(data)
        self._emit("data", data)

    def on_complete(self) -> None:
        self.completed = True
        self._emit_terminal("done")

    def on_error(self, message: str) -> None:
        self.failed = True
        self._emit_terminal("error", message or "百炼流式语音合成失败")

    async def iter_chunks(self) -> AsyncIterator[bytes]:
        while True:
            kind, payload = await self.queue.get()
            if kind == "data":
                yield payload
            elif kind == "error":
                raise VoiceProviderError(str(payload))
            else:
                break

    async def cancel(self) -> None:
        if self.completed or self.cancelled:
            return
        self.cancelled = True
        synthesizer = self.synthesizer
        if synthesizer is not None:
            try:
                await asyncio.to_thread(synthesizer.streaming_cancel, 5000)
            except Exception:
                logger.debug("取消百炼语音流失败", exc_info=True)

    async def wait_closed(self) -> None:
        if self.worker is not None:
            await self.worker

    @property
    def audio_duration_ms(self) -> int:
        bytes_per_second = self.sample_rate * 2
        return int(self.audio_bytes / bytes_per_second * 1000)

    @property
    def rtf(self) -> float:
        if self.audio_duration_ms <= 0:
            return 0.0
        return round(self.total_elapsed_ms / self.audio_duration_ms, 4)


class DashScopeVoiceAdapter:
    """Encapsulates DashScope SDK globals, enrollment and connection pooling."""

    def __init__(self, config: VoiceRuntimeConfig) -> None:
        self.config = config
        self.pool: SpeechSynthesizerObjectPool | None = None

    async def start(self) -> None:
        await asyncio.to_thread(self._start_sync)

    def _start_sync(self) -> None:
        # The SDK pool scans once per second and reconnects idle sockets. Keep
        # provider maintenance noise out of the project's DEBUG application log.
        for logger_name in ("dashscope", "websocket", "websockets.client"):
            logging.getLogger(logger_name).setLevel(logging.WARNING)
        dashscope.api_key = self.config.api_key
        dashscope.base_http_api_url = self.config.base_http
        dashscope.base_websocket_api_url = self.config.base_ws
        self.pool = SpeechSynthesizerObjectPool(
            max_size=self.config.pool_size,
            workspace=self.config.workspace_id,
            url=self.config.base_ws,
        )

    async def close(self) -> None:
        pool, self.pool = self.pool, None
        if pool is not None:
            await asyncio.to_thread(pool.shutdown)

    def _enrollment_service(self) -> VoiceEnrollmentService:
        return VoiceEnrollmentService(
            api_key=self.config.api_key,
            workspace=self.config.workspace_id,
        )

    async def create_voice(
        self,
        *,
        model: str,
        prefix: str,
        url: str,
        language_hints: list[str],
        max_prompt_audio_length: float,
        enable_preprocess: bool,
    ) -> VoiceEnrollmentResult:
        service = self._enrollment_service()
        try:
            voice_id = await asyncio.to_thread(
                service.create_voice,
                target_model=model,
                prefix=prefix,
                url=url,
                language_hints=language_hints,
                max_prompt_audio_length=max_prompt_audio_length,
                enable_preprocess=enable_preprocess,
            )
        except Exception as exc:
            raise VoiceProviderError(f"创建音色失败: {exc}") from exc
        return VoiceEnrollmentResult(
            voice_id=voice_id,
            model=model,
            request_id=service.get_last_request_id(),
        )

    async def query_voice(self, voice_id: str) -> Any:
        service = self._enrollment_service()
        try:
            return await asyncio.to_thread(service.query_voice, voice_id=voice_id)
        except Exception as exc:
            raise VoiceProviderError(str(exc)) from exc

    async def update_voice(self, voice_id: str, url: str) -> str | None:
        service = self._enrollment_service()
        try:
            await asyncio.to_thread(
                service.update_voice,
                voice_id=voice_id,
                url=url,
            )
        except Exception as exc:
            raise VoiceProviderError(str(exc)) from exc
        return service.get_last_request_id()

    async def delete_voice(self, voice_id: str) -> None:
        service = self._enrollment_service()
        try:
            await asyncio.to_thread(service.delete_voice, voice_id=voice_id)
        except Exception as exc:
            raise VoiceProviderError(str(exc)) from exc

    def borrow_synthesizer(
        self,
        *,
        model: str,
        voice_id: str,
        audio_format: AudioFormat,
        instruction: str | None,
        callback: ResultCallback | None,
    ) -> SpeechSynthesizer:
        if self.pool is None:
            raise VoiceProviderError("语音 WebSocket 连接池未初始化")
        return self.pool.borrow_synthesizer(
            model=model,
            voice=voice_id,
            format=audio_format,
            instruction=instruction,
            callback=callback,
        )

    def return_synthesizer(self, synthesizer: SpeechSynthesizer) -> None:
        if self.pool is not None:
            self.pool.return_synthesizer(synthesizer)

    async def synthesize(
        self,
        *,
        text: str,
        model: str,
        voice_id: str,
        instruction: str | None = None,
    ) -> SynthesisResult:
        def run() -> SynthesisResult:
            synthesizer = self.borrow_synthesizer(
                model=model,
                voice_id=voice_id,
                audio_format=AudioFormat.MP3_24000HZ_MONO_256KBPS,
                instruction=instruction,
                callback=None,
            )
            try:
                audio = synthesizer.call(text)
                if not audio:
                    raise VoiceProviderError("语音合成返回空数据")
                delay = synthesizer.get_first_package_delay()
                return SynthesisResult(
                    audio=audio,
                    model=model,
                    request_id=synthesizer.get_last_request_id(),
                    first_package_delay_ms=max(0, int(delay or 0)),
                )
            finally:
                self.return_synthesizer(synthesizer)

        try:
            return await asyncio.to_thread(run)
        except VoiceProviderError:
            raise
        except Exception as exc:
            raise VoiceProviderError(f"语音合成失败: {exc}") from exc

    async def stream(
        self,
        *,
        text: str,
        model: str,
        voice_id: str,
        instruction: str | None = None,
    ) -> DashScopePCMStream:
        stream = DashScopePCMStream(
            adapter=self,
            text=text,
            model=model,
            voice_id=voice_id,
            instruction=instruction,
        )
        await stream.start()
        return stream


class VoiceRuntimeService:
    def __init__(self) -> None:
        self._adapter: DashScopeVoiceAdapter | None = None
        self._fingerprint = ""
        self._lock = asyncio.Lock()

    async def _get_runtime(
        self,
    ) -> tuple[VoiceRuntimeConfig, DashScopeVoiceAdapter]:
        config = await load_voice_runtime_config()
        async with self._lock:
            if self._adapter is None or self._fingerprint != config.fingerprint:
                old_adapter = self._adapter
                self._adapter = None
                self._fingerprint = ""
                if old_adapter is not None:
                    await old_adapter.close()
                adapter = DashScopeVoiceAdapter(config)
                await adapter.start()
                self._adapter = adapter
                self._fingerprint = config.fingerprint
                logger.info(
                    "百炼语音连接池已就绪: model=%s pool=%s endpoint=%s",
                    config.model,
                    config.pool_size,
                    config.base_ws,
                )
            return config, self._adapter

    async def warmup(self) -> VoiceRuntimeConfig:
        config, _ = await self._get_runtime()
        return config

    async def close(self) -> None:
        async with self._lock:
            adapter, self._adapter = self._adapter, None
            self._fingerprint = ""
        if adapter is not None:
            await adapter.close()

    async def create_voice(
        self,
        db: AsyncIOMotorDatabase,
        *,
        url: str,
        prefix: str | None,
        language_hints: list[str] | None,
        max_prompt_audio_length: float | None,
        enable_preprocess: bool | None,
        authorized_by: str,
    ) -> VoiceEnrollmentResult:
        config, adapter = await self._get_runtime()
        result = await adapter.create_voice(
            model=config.model,
            prefix=prefix or config.prefix,
            url=url,
            language_hints=language_hints or config.language_hints,
            max_prompt_audio_length=(
                max_prompt_audio_length or config.max_prompt_audio_length
            ),
            enable_preprocess=(
                enable_preprocess
                if enable_preprocess is not None
                else config.enable_preprocess
            ),
        )
        await voice_dao.save_clone(
            db,
            voice_id=result.voice_id,
            model=result.model,
            prefix=prefix or config.prefix,
            url=_stored_source_url(url),
            language_hints=language_hints or config.language_hints,
            request_id=result.request_id,
            authorized_by=authorized_by,
        )
        return result

    async def get_voice_detail(
        self,
        db: AsyncIOMotorDatabase,
        voice_id: str,
    ) -> dict[str, Any] | None:
        local = await voice_dao.get_clone(db, voice_id)
        _, adapter = await self._get_runtime()
        try:
            remote = await adapter.query_voice(voice_id)
        except VoiceProviderError:
            remote = None
        if not local and not remote:
            return None
        return {"local": local, "remote": remote}

    async def update_voice(
        self,
        db: AsyncIOMotorDatabase,
        *,
        voice_id: str,
        url: str,
    ) -> str | None:
        _, adapter = await self._get_runtime()
        request_id = await adapter.update_voice(voice_id, url)
        await voice_dao.update_clone_source(
            db,
            voice_id,
            url=_stored_source_url(url),
            request_id=request_id,
        )
        return request_id

    async def delete_voice(
        self,
        db: AsyncIOMotorDatabase,
        voice_id: str,
    ) -> None:
        _, adapter = await self._get_runtime()
        try:
            await adapter.delete_voice(voice_id)
        except VoiceProviderError as exc:
            error = str(exc).lower()
            if "not found" not in error and "not exist" not in error:
                raise
        await voice_dao.delete_clone(db, voice_id)

    async def resolve_model(
        self,
        db: AsyncIOMotorDatabase,
        *,
        voice_id: str,
        requested_model: str | None,
        default_model: str | None = None,
    ) -> str:
        if default_model is None:
            config, _ = await self._get_runtime()
            default_model = config.model
        clone = await voice_dao.get_clone(db, voice_id)
        clone_model = str(clone.get("model")) if clone and clone.get("model") else None
        if requested_model and clone_model and requested_model != clone_model:
            raise VoiceModelMismatchError(
                f"音色 {voice_id} 绑定模型 {clone_model}，不能使用 {requested_model}"
            )
        return requested_model or clone_model or default_model

    async def synthesize(
        self,
        db: AsyncIOMotorDatabase,
        *,
        text: str,
        voice_id: str,
        requested_model: str | None,
        instruction: str | None,
    ) -> tuple[str, SynthesisResult]:
        config, adapter = await self._get_runtime()
        model = await self.resolve_model(
            db,
            voice_id=voice_id,
            requested_model=requested_model,
            default_model=config.model,
        )
        record_id = await voice_dao.create_synthesis_record(
            db,
            voice_id=voice_id,
            text=text,
            model=model,
            streaming=False,
            audio_format="mp3",
            sample_rate=24000,
        )
        started = time.perf_counter()
        try:
            result = await adapter.synthesize(
                text=text,
                model=model,
                voice_id=voice_id,
                instruction=instruction,
            )
            total_ms = int((time.perf_counter() - started) * 1000)
            await voice_dao.complete_synthesis_record(
                db,
                record_id,
                audio_bytes=len(result.audio),
                first_pkg_delay_ms=result.first_package_delay_ms,
                total_latency_ms=total_ms,
                request_id=result.request_id,
            )
            return record_id, result
        except Exception as exc:
            await voice_dao.fail_synthesis_record(db, record_id, str(exc))
            raise

    async def stream_synthesis(
        self,
        db: AsyncIOMotorDatabase,
        *,
        text: str,
        voice_id: str,
        requested_model: str | None,
        instruction: str | None,
    ) -> VoiceStreamHandle:
        config, adapter = await self._get_runtime()
        model = await self.resolve_model(
            db,
            voice_id=voice_id,
            requested_model=requested_model,
            default_model=config.model,
        )
        record_id = await voice_dao.create_synthesis_record(
            db,
            voice_id=voice_id,
            text=text,
            model=model,
            streaming=True,
            audio_format="pcm_s16le",
            sample_rate=config.stream_sample_rate,
        )
        try:
            stream = await adapter.stream(
                text=text,
                model=model,
                voice_id=voice_id,
                instruction=instruction,
            )
        except Exception as exc:
            await voice_dao.fail_synthesis_record(db, record_id, str(exc))
            raise

        async def chunks() -> AsyncIterator[bytes]:
            completed = False
            record_finalized = False
            try:
                async for chunk in stream.iter_chunks():
                    yield chunk
                await stream.wait_closed()
                await voice_dao.complete_synthesis_record(
                    db,
                    record_id,
                    audio_bytes=stream.audio_bytes,
                    first_pkg_delay_ms=stream.first_package_delay_ms,
                    total_latency_ms=stream.total_elapsed_ms,
                    audio_duration_ms=stream.audio_duration_ms,
                    rtf=stream.rtf,
                    request_id=stream.request_id,
                )
                completed = True
                record_finalized = True
                logger.info(
                    "流式语音完成: record=%s model=%s ttfa=%sms rtf=%.4f",
                    record_id,
                    model,
                    stream.first_package_delay_ms,
                    stream.rtf,
                )
            except (asyncio.CancelledError, GeneratorExit):
                await stream.cancel()
                record_finalized = True
                await voice_dao.cancel_synthesis_record(
                    db,
                    record_id,
                    "客户端已断开流式播放",
                )
                raise
            except Exception as exc:
                record_finalized = True
                await voice_dao.fail_synthesis_record(db, record_id, str(exc))
                raise
            finally:
                if not completed:
                    await stream.cancel()
                if not completed and not record_finalized:
                    await voice_dao.cancel_synthesis_record(
                        db,
                        record_id,
                        "流式响应已提前结束",
                    )
                await stream.wait_closed()

        return VoiceStreamHandle(
            record_id=record_id,
            model=model,
            sample_rate=config.stream_sample_rate,
            chunks=chunks(),
        )


_voice_runtime_service = VoiceRuntimeService()


def get_voice_runtime_service() -> VoiceRuntimeService:
    return _voice_runtime_service


async def warmup_voice_runtime() -> VoiceRuntimeConfig:
    return await _voice_runtime_service.warmup()


async def shutdown_voice_runtime() -> None:
    await _voice_runtime_service.close()
