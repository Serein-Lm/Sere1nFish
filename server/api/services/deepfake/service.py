"""Domain service for Deepfake inference and realtime session ownership."""

from __future__ import annotations

import asyncio
import re
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from api.services.runtime_config import get_runtime_config_section

from .contracts import DeepfakeConfig, DeepfakeProvider, DeepfakeStream, ImageSwapResult, SourceImage
from .factory import DeepfakeProviderFactory


class DeepfakeConfigurationError(RuntimeError):
    pass


@dataclass(slots=True)
class SessionOwner:
    username: str


_SESSION_OWNERS: dict[str, SessionOwner] = {}
_SESSION_OWNERS_LOCK = asyncio.Lock()


def _parse_config(raw: dict[str, Any]) -> DeepfakeConfig:
    provider = str(raw.get("provider") or "facefusion_gateway").strip()
    base_url = str(raw.get("base_url") or "").strip().rstrip("/")
    api_token = str(raw.get("api_token") or "").strip()
    ca_certificate = str(raw.get("ca_certificate") or "").strip()
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise DeepfakeConfigurationError("deepfake.base_url must be a valid HTTPS URL")
    if len(api_token) < 32:
        raise DeepfakeConfigurationError("deepfake.api_token is not configured")
    try:
        timeout_seconds = min(120.0, max(3.0, float(raw.get("timeout_seconds") or 15)))
        max_image_bytes = min(30 * 1024 * 1024, max(1024 * 1024, int(raw.get("max_image_bytes") or 12 * 1024 * 1024)))
        max_source_images = min(8, max(1, int(raw.get("max_source_images") or 4)))
        realtime_max_width = min(1280, max(320, int(raw.get("realtime_max_width") or 960)))
    except (TypeError, ValueError) as exc:
        raise DeepfakeConfigurationError("deepfake numeric configuration is invalid") from exc
    return DeepfakeConfig(
        provider=provider,
        base_url=base_url,
        api_token=api_token,
        ca_certificate=ca_certificate,
        timeout_seconds=timeout_seconds,
        max_image_bytes=max_image_bytes,
        max_source_images=max_source_images,
        realtime_max_width=realtime_max_width,
    )


class DeepfakeService:
    def __init__(self, config: DeepfakeConfig, provider: DeepfakeProvider) -> None:
        self.config = config
        self.provider = provider

    async def status(self) -> dict[str, Any]:
        payload = await self.provider.status()
        payload["provider"] = self.provider.name
        return payload

    def validate_upload(self, data: bytes, *, label: str) -> None:
        if not data:
            raise ValueError(f"{label} image is empty")
        if len(data) > self.config.max_image_bytes:
            raise ValueError(f"{label} image exceeds the configured size limit")

    def validate_sources(self, sources: list[SourceImage]) -> None:
        if not 1 <= len(sources) <= self.config.max_source_images:
            raise ValueError(f"source image count must be between 1 and {self.config.max_source_images}")
        for index, source in enumerate(sources, start=1):
            self.validate_upload(source.content, label=f"source {index}")

    @staticmethod
    def normalize_profile(profile: str) -> str:
        value = str(profile or "").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value):
            raise ValueError("quality profile is invalid")
        return value

    async def swap_image(
        self,
        *,
        sources: list[SourceImage],
        target: bytes,
        target_name: str,
        max_width: int,
        profile: str,
    ) -> ImageSwapResult:
        self.validate_sources(sources)
        self.validate_upload(target, label="target")
        return await self.provider.swap_image(
            sources=sources,
            target=target,
            target_name=target_name,
            max_width=min(1920, max(320, max_width)),
            profile=self.normalize_profile(profile),
        )

    async def create_session(
        self,
        *,
        username: str,
        sources: list[SourceImage],
        max_width: int | None,
        profile: str,
    ) -> dict[str, Any]:
        self.validate_sources(sources)
        payload = await self.provider.create_session(
            sources=sources,
            max_width=min(1280, max(320, max_width or self.config.realtime_max_width)),
            profile=self.normalize_profile(profile),
        )
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            raise RuntimeError("GPU gateway did not return a session ID")
        async with _SESSION_OWNERS_LOCK:
            _SESSION_OWNERS[session_id] = SessionOwner(username=username)
        payload.pop("ticket", None)
        payload["stream_path"] = f"/api/v1/deepfake/sessions/{session_id}/stream"
        return payload

    async def _require_owner(self, session_id: str, username: str) -> None:
        async with _SESSION_OWNERS_LOCK:
            owner = _SESSION_OWNERS.get(session_id)
        if not owner or owner.username != username:
            raise PermissionError("Deepfake session not found")

    async def session_status(self, session_id: str, username: str) -> dict[str, Any]:
        await self._require_owner(session_id, username)
        return await self.provider.session_status(session_id)

    async def delete_session(self, session_id: str, username: str) -> dict[str, Any]:
        await self._require_owner(session_id, username)
        payload = await self.provider.delete_session(session_id)
        async with _SESSION_OWNERS_LOCK:
            _SESSION_OWNERS.pop(session_id, None)
        return payload

    async def open_stream(
        self,
        session_id: str,
        username: str,
    ) -> AbstractAsyncContextManager[DeepfakeStream]:
        await self._require_owner(session_id, username)
        return self.provider.open_stream(session_id)


async def get_deepfake_service() -> DeepfakeService:
    raw = await get_runtime_config_section("deepfake")
    config = _parse_config(raw)
    return DeepfakeService(config, DeepfakeProviderFactory.create(config))
