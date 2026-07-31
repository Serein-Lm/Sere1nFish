"""Secure PCM bridge between OBS audio and a local conversion provider."""

from __future__ import annotations

import asyncio
import json
import os
import ssl
from dataclasses import asdict, dataclass
from typing import Any

import websockets

from .subprocesses import terminate_subprocess


class MediaVoiceBridgeError(RuntimeError):
    pass


class MediaVoiceBridgeInputUnavailable(MediaVoiceBridgeError):
    """Signals that OBS has not published a readable microphone track yet."""


@dataclass(slots=True)
class MediaVoiceBridgeStats:
    state: str = "disabled"
    provider: str = ""
    sample_rate: int = 0
    input_bytes: int = 0
    output_bytes: int = 0
    buffered_ms: int = 0
    reconnects: int = 0
    last_event: str = ""
    last_error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PcmOutputBuffer:
    """Bounded latest-audio buffer used to prevent stale speech latency."""

    def __init__(self, *, sample_rate: int = 16000, max_seconds: float = 1.0) -> None:
        self.sample_rate = sample_rate
        self.max_bytes = int(sample_rate * 2 * max_seconds)
        self._data = bytearray()

    @property
    def buffered_ms(self) -> int:
        return round(len(self._data) * 1000 / (self.sample_rate * 2))

    def append(self, payload: bytes) -> None:
        if not payload:
            return
        aligned = payload[: len(payload) - (len(payload) % 2)]
        self._data.extend(aligned)
        overflow = len(self._data) - self.max_bytes
        if overflow > 0:
            drop = overflow + (overflow % 2)
            del self._data[:drop]

    def take_or_silence(self, size: int) -> bytes:
        size -= size % 2
        available = min(size, len(self._data))
        available -= available % 2
        chunk = bytes(self._data[:available])
        if available:
            del self._data[:available]
        if available < size:
            chunk += bytes(size - available)
        return chunk

    def clear(self) -> None:
        self._data.clear()


class MediaVoiceBridge:
    """Reconnectable OBS-audio to conversion provider bridge."""

    def __init__(
        self,
        *,
        input_url: str,
        websocket_url: str,
        token: str,
        ca_file: str,
        provider: str,
        output_sample_rate: int,
        output_buffer: PcmOutputBuffer,
        stats: MediaVoiceBridgeStats,
    ) -> None:
        self.input_url = input_url
        self.websocket_url = websocket_url
        self.token = token
        self.ca_file = ca_file
        self.provider = provider
        self.output_sample_rate = output_sample_rate
        self.output_buffer = output_buffer
        self.stats = stats
        self.stats.provider = provider
        self.stats.sample_rate = output_sample_rate
        self._stopping = asyncio.Event()
        self._decoder: asyncio.subprocess.Process | None = None

    async def run(self) -> None:
        while not self._stopping.is_set():
            self.stats.state = "waiting_audio"
            self.stats.last_error = ""
            self.output_buffer.clear()
            try:
                await self._run_once()
                if not self._stopping.is_set():
                    raise MediaVoiceBridgeError("Voice bridge stopped unexpectedly")
            except asyncio.CancelledError:
                raise
            except MediaVoiceBridgeInputUnavailable:
                self.stats.state = "waiting_audio"
                self.stats.last_error = ""
                await self._terminate_decoder()
                await self._sleep_or_stop(1.0)
            except Exception as exc:
                was_live = self.stats.state == "live"
                self.stats.state = "waiting_audio"
                self.stats.last_error = str(exc)[:300]
                if was_live:
                    self.stats.reconnects += 1
                await self._terminate_decoder()
                await self._sleep_or_stop(1.0)
        self.stats.state = "stopped"

    async def stop(self) -> None:
        self._stopping.set()
        await self._terminate_decoder()

    async def _run_once(self) -> None:
        decoder = await self._start_audio_decoder()
        self._decoder = decoder
        try:
            first_chunk = await asyncio.wait_for(
                self._read_audio_chunk(decoder, waiting_for_input=True),
                timeout=5,
            )
        except TimeoutError as exc:
            raise MediaVoiceBridgeInputUnavailable from exc
        self.stats.state = "connecting" if not self.stats.reconnects else "reconnecting"
        secure = self.websocket_url.startswith("wss://")
        context = ssl.create_default_context(cafile=self.ca_file) if secure else None
        try:
            async with websockets.connect(
                self.websocket_url,
                additional_headers={"Authorization": f"Bearer {self.token}"},
                ssl=context,
                open_timeout=10,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_size=4 * 1024 * 1024,
                compression=None,
                proxy=None,
            ) as websocket:
                ready = await asyncio.wait_for(websocket.recv(), timeout=20)
                if not isinstance(ready, str):
                    raise MediaVoiceBridgeError("Voice bridge returned an invalid ready event")
                try:
                    ready_event = json.loads(ready)
                except (TypeError, ValueError) as exc:
                    raise MediaVoiceBridgeError("Voice bridge returned invalid JSON") from exc
                if ready_event.get("type") != "session.ready":
                    message = ready_event.get("message") or "Voice bridge did not become ready"
                    raise MediaVoiceBridgeError(str(message))
                ready_sample_rate = int(ready_event.get("sample_rate") or self.output_sample_rate)
                if ready_sample_rate != self.output_sample_rate:
                    raise MediaVoiceBridgeError("Voice provider returned an unexpected sample rate")

                self.stats.state = "live"
                self.stats.last_event = "session.ready"
                tasks = {
                    asyncio.create_task(
                        self._upload_audio(websocket, decoder, first_chunk),
                        name="voice-upload",
                    ),
                    asyncio.create_task(self._download_audio(websocket), name="voice-download"),
                }
                try:
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await self._terminate_decoder()

    async def _upload_audio(
        self,
        websocket: Any,
        decoder: asyncio.subprocess.Process,
        first_chunk: bytes,
    ) -> None:
        await websocket.send(first_chunk)
        self.stats.input_bytes += len(first_chunk)
        while not self._stopping.is_set():
            chunk = await self._read_audio_chunk(decoder)
            await websocket.send(chunk)
            self.stats.input_bytes += len(chunk)

    @staticmethod
    async def _read_audio_chunk(
        decoder: asyncio.subprocess.Process,
        *,
        waiting_for_input: bool = False,
    ) -> bytes:
        assert decoder.stdout is not None
        chunk_size = 16000 * 2 * 40 // 1000
        chunk = await decoder.stdout.read(chunk_size)
        if not chunk:
            if waiting_for_input:
                raise MediaVoiceBridgeInputUnavailable
            raise MediaVoiceBridgeError("OBS input has no readable audio track")
        aligned = chunk[: len(chunk) - (len(chunk) % 2)]
        if not aligned:
            raise MediaVoiceBridgeError("OBS input audio chunk is not 16-bit aligned")
        return aligned

    async def _download_audio(self, websocket: Any) -> None:
        while not self._stopping.is_set():
            message = await websocket.recv()
            if isinstance(message, bytes):
                self.output_buffer.append(message)
                self.stats.output_bytes += len(message)
                self.stats.buffered_ms = self.output_buffer.buffered_ms
                continue
            try:
                event = json.loads(message)
            except (TypeError, ValueError):
                continue
            event_type = str(event.get("type") or "")
            self.stats.last_event = event_type
            if event_type in {"input_audio_buffer.speech_started", "response.cancelled"}:
                self.output_buffer.clear()
                self.stats.buffered_ms = 0
            if event_type in {"session.error", "error"}:
                error = event.get("error")
                detail = error.get("message") if isinstance(error, dict) else event.get("message")
                raise MediaVoiceBridgeError(str(detail or "Voice bridge returned an error"))

    async def _start_audio_decoder(self) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-analyzeduration",
            "100000",
            "-probesize",
            "32768",
            "-i",
            self.input_url,
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "pipe:1",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=64 * 1024,
        )

    async def _terminate_decoder(self) -> None:
        decoder = self._decoder
        self._decoder = None
        await terminate_subprocess(decoder)

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        except TimeoutError:
            pass


def validate_voice_bridge_environment(websocket_url: str, token: str, ca_file: str) -> None:
    from urllib.parse import urlsplit

    parsed = urlsplit(websocket_url)
    if (
        parsed.scheme not in {"ws", "wss"}
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or not parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Voice bridge URL must use a local WebSocket")
    if len(token) < 32:
        raise ValueError("Voice bridge token is invalid")
    if parsed.scheme == "wss" and (not ca_file or not os.path.isfile(ca_file)):
        raise ValueError("Voice bridge CA file is unavailable")
