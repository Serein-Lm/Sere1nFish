"""Private voice-conversion provider adapter for the GPU media plane."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlsplit, urlunsplit

import httpx
import websockets


class VoiceConversionError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class VoiceConversionSession:
    provider: str
    session_id: str
    token: str
    websocket_url: str
    sample_rate: int
    chunk_ms: int


class MeanVCVoiceConverter:
    provider = "meanvc"

    def __init__(self, *, base_url: str, api_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("MeanVC runtime URL must use local HTTP")
        if parsed.query or parsed.fragment:
            raise ValueError("MeanVC runtime URL cannot contain a query or fragment")
        if len(self.api_token) < 32:
            raise ValueError("MeanVC runtime token is invalid")

    @classmethod
    def from_environment(cls) -> MeanVCVoiceConverter | None:
        base_url = os.getenv("DEEPFAKE_VOICE_CONVERTER_BASE_URL", "").strip()
        token_file = Path(
            os.getenv(
                "DEEPFAKE_VOICE_CONVERTER_TOKEN_FILE",
                "/run/secrets/meanvc_api_token",
            )
        )
        if not base_url:
            return None
        try:
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"Unable to read MeanVC runtime token: {token_file}") from exc
        return cls(base_url=base_url, api_token=token)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_token}"}

    def _websocket_url(self, path: str) -> str:
        parsed = urlsplit(f"{self.base_url}{path}")
        return urlunsplit(("ws", parsed.netloc, parsed.path, parsed.query, ""))

    @staticmethod
    def _response_error(response: httpx.Response) -> VoiceConversionError:
        message = response.text.strip() or f"MeanVC runtime returned HTTP {response.status_code}"
        try:
            payload = response.json()
            if isinstance(payload, dict) and payload.get("detail"):
                message = str(payload["detail"])
        except json.JSONDecodeError:
            pass
        return VoiceConversionError(message)

    async def status(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=3, follow_redirects=False) as client:
                response = await client.get(
                    f"{self.base_url}/v1/status",
                    headers=self.headers,
                )
        except (httpx.HTTPError, OSError) as exc:
            return {
                "ok": False,
                "provider": self.provider,
                "last_error": str(exc)[:300],
            }
        if response.is_error:
            return {
                "ok": False,
                "provider": self.provider,
                "last_error": str(self._response_error(response))[:300],
            }
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return {
                "ok": False,
                "provider": self.provider,
                "last_error": "MeanVC runtime returned invalid JSON",
            }
        return payload if isinstance(payload, dict) else {"ok": False, "provider": self.provider}

    async def create_session(
        self,
        *,
        reference: bytes,
        filename: str,
        content_type: str,
        steps: int,
    ) -> VoiceConversionSession:
        files = {
            "reference": (
                filename or "target-voice.wav",
                reference,
                content_type or "application/octet-stream",
            )
        }
        data = {
            "authorized_use": "true",
            "steps": str(steps),
        }
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                response = await client.post(
                    f"{self.base_url}/v1/sessions",
                    headers=self.headers,
                    files=files,
                    data=data,
                )
        except (httpx.HTTPError, OSError) as exc:
            raise VoiceConversionError(f"MeanVC runtime is unavailable: {exc}") from exc
        if response.is_error:
            raise self._response_error(response)
        try:
            payload = response.json()
            session_id = str(payload["session_id"])
            token = str(payload["token"])
            websocket_path = str(payload["websocket_path"])
            sample_rate = int(payload["sample_rate"])
            chunk_ms = int(payload["chunk_ms"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise VoiceConversionError("MeanVC runtime returned an invalid session") from exc
        if not session_id or len(token) < 32 or not websocket_path.startswith("/"):
            raise VoiceConversionError("MeanVC runtime returned incomplete credentials")
        return VoiceConversionSession(
            provider=self.provider,
            session_id=session_id,
            token=token,
            websocket_url=self._websocket_url(websocket_path),
            sample_rate=sample_rate,
            chunk_ms=chunk_ms,
        )

    async def delete_session(self, session_id: str) -> None:
        if not session_id:
            return
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                response = await client.delete(
                    f"{self.base_url}/v1/sessions/{session_id}",
                    headers=self.headers,
                )
        except (httpx.HTTPError, OSError):
            return
        if response.status_code not in {200, 404, 409}:
            raise self._response_error(response)

    @asynccontextmanager
    async def open_stream(self, session: VoiceConversionSession) -> AsyncIterator[Any]:
        try:
            async with websockets.connect(
                session.websocket_url,
                additional_headers={"Authorization": f"Bearer {session.token}"},
                open_timeout=10,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_size=4 * 1024 * 1024,
                compression=None,
                proxy=None,
            ) as websocket:
                yield websocket
        except VoiceConversionError:
            raise
        except Exception as exc:
            raise VoiceConversionError(f"MeanVC stream is unavailable: {exc}") from exc
