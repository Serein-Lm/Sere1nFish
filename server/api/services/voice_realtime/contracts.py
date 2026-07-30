"""Provider-neutral contracts for full-duplex voice sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


TurnDetectionMode = Literal["smart_turn", "server_vad", "manual"]

SYSTEM_VOICES: tuple[tuple[str, str], ...] = (
    ("longanqian", "龙安芊"),
    ("longanlingxin", "龙安聆心"),
    ("longanlingxi", "龙安聆希"),
    ("longanxiaoxin", "龙安小新"),
    ("longanlufeng", "龙安鹿风"),
)


@dataclass(frozen=True, slots=True)
class RealtimeVoiceConfig:
    provider: str
    api_key: str
    model: str
    endpoint: str
    workspace_id: str | None
    region: str
    default_voice: str
    default_mode: TurnDetectionMode
    default_instructions: str
    max_history_turns: int
    vad_threshold: float
    vad_silence_ms: int
    connect_timeout_seconds: float
    session_timeout_seconds: float
    max_audio_chunk_bytes: int
    max_provider_message_bytes: int


@dataclass(frozen=True, slots=True)
class RealtimeSessionOptions:
    voice: str
    mode: TurnDetectionMode
    instructions: str
    max_history_turns: int
    vad_threshold: float
    vad_silence_ms: int


@dataclass(frozen=True, slots=True)
class RealtimeProviderEvent:
    type: str
    payload: dict[str, Any]
    audio: bytes | None = None


class RealtimeVoiceConnection(Protocol):
    async def send_audio(self, audio: bytes) -> None: ...

    async def send_control(self, event_type: str) -> None: ...

    async def receive(self) -> RealtimeProviderEvent: ...

    async def close(self) -> None: ...


class RealtimeVoiceProvider(Protocol):
    name: str

    async def connect(
        self,
        options: RealtimeSessionOptions,
    ) -> RealtimeVoiceConnection: ...
