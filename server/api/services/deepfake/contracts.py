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
    realtime_max_width: int


@dataclass(slots=True)
class ImageSwapResult:
    content: bytes
    content_type: str
    inference_ms: float


class DeepfakeStream(Protocol):
    async def send(self, message: bytes | str) -> None: ...

    async def recv(self) -> bytes | str: ...


class DeepfakeProvider(Protocol):
    name: str

    async def status(self) -> dict[str, Any]: ...

    async def swap_image(
        self,
        *,
        source: bytes,
        source_name: str,
        target: bytes,
        target_name: str,
        max_width: int,
    ) -> ImageSwapResult: ...

    async def create_session(
        self,
        *,
        source: bytes,
        source_name: str,
        max_width: int,
    ) -> dict[str, Any]: ...

    async def session_status(self, session_id: str) -> dict[str, Any]: ...

    async def delete_session(self, session_id: str) -> dict[str, Any]: ...

    def open_stream(self, session_id: str) -> AbstractAsyncContextManager[DeepfakeStream]: ...
