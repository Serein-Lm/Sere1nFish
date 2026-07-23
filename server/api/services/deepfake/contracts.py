"""Provider contracts for replaceable Deepfake GPU runtimes."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True, frozen=True)
class DeepfakeConfig:
    provider: str
    base_url: str
    api_token: str
    ca_certificate: str
    timeout_seconds: float
    max_image_bytes: int
    max_source_images: int
    realtime_max_width: int


@dataclass(slots=True, frozen=True)
class SourceImage:
    content: bytes
    filename: str


@dataclass(slots=True)
class ImageSwapResult:
    content: bytes
    content_type: str
    inference_ms: float
    quality_profile: str = ""
    source_count: int = 1
    source_consistency: float = 1.0
    effective_max_width: int = 0


class DeepfakeStream(Protocol):
    async def send(self, message: bytes | str) -> None: ...

    async def recv(self) -> bytes | str: ...


class DeepfakeProvider(Protocol):
    name: str

    async def status(self) -> dict[str, Any]: ...

    async def swap_image(
        self,
        *,
        sources: list[SourceImage],
        target: bytes,
        target_name: str,
        max_width: int,
        profile: str,
    ) -> ImageSwapResult: ...

    async def create_session(
        self,
        *,
        sources: list[SourceImage],
        max_width: int,
        profile: str,
    ) -> dict[str, Any]: ...

    async def session_status(self, session_id: str) -> dict[str, Any]: ...

    async def delete_session(self, session_id: str) -> dict[str, Any]: ...

    def open_stream(self, session_id: str) -> AbstractAsyncContextManager[DeepfakeStream]: ...
