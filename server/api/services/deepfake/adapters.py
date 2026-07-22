"""Deepfake provider adapters."""

from __future__ import annotations

import json
import ssl
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlsplit, urlunsplit

import httpx
import websockets

from .contracts import DeepfakeConfig, DeepfakeStream, ImageSwapResult


class DeepfakeProviderError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class FaceFusionGatewayProvider:
    name = "facefusion_gateway"

    def __init__(self, config: DeepfakeConfig) -> None:
        self.config = config
        self._ssl_context = self._create_ssl_context(config.ca_certificate)

    @staticmethod
    def _create_ssl_context(ca_certificate: str) -> ssl.SSLContext:
        try:
            if ca_certificate.strip():
                return ssl.create_default_context(cadata=ca_certificate)
            return ssl.create_default_context()
        except ssl.SSLError as exc:
            raise ValueError("deepfake.ca_certificate is not a valid CA certificate") from exc

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.api_token}"}

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}{path}"

    def _websocket_url(self, path: str) -> str:
        parsed = urlsplit(self._url(path))
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, ""))

    async def _json_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                verify=self._ssl_context,
                timeout=self.config.timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.request(method, self._url(path), headers=self._headers, **kwargs)
        except (httpx.HTTPError, OSError) as exc:
            raise DeepfakeProviderError(f"GPU gateway is unavailable: {exc}") from exc
        if response.is_error:
            raise self._response_error(response)
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise DeepfakeProviderError("GPU gateway returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise DeepfakeProviderError("GPU gateway returned an invalid response")
        return payload

    @staticmethod
    def _response_error(response: httpx.Response) -> DeepfakeProviderError:
        message = response.text.strip() or f"GPU gateway returned HTTP {response.status_code}"
        try:
            payload = response.json()
            if isinstance(payload, dict) and payload.get("detail"):
                message = str(payload["detail"])
        except json.JSONDecodeError:
            pass
        status_code = response.status_code if 400 <= response.status_code < 500 else 502
        return DeepfakeProviderError(message, status_code=status_code)

    async def status(self) -> dict[str, Any]:
        return await self._json_request("GET", "/v1/status")

    async def swap_image(
        self,
        *,
        source: bytes,
        source_name: str,
        target: bytes,
        target_name: str,
        max_width: int,
    ) -> ImageSwapResult:
        files = {
            "source": (source_name or "source.jpg", source, "application/octet-stream"),
            "target": (target_name or "target.jpg", target, "application/octet-stream"),
        }
        data = {"authorized_use": "true", "max_width": str(max_width)}
        try:
            async with httpx.AsyncClient(
                verify=self._ssl_context,
                timeout=max(60.0, self.config.timeout_seconds),
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    self._url("/v1/swap/image"),
                    headers=self._headers,
                    files=files,
                    data=data,
                )
        except (httpx.HTTPError, OSError) as exc:
            raise DeepfakeProviderError(f"GPU image inference failed: {exc}") from exc
        if response.is_error:
            raise self._response_error(response)
        return ImageSwapResult(
            content=response.content,
            content_type=response.headers.get("content-type", "image/jpeg"),
            inference_ms=float(response.headers.get("x-inference-ms") or 0),
        )

    async def create_session(
        self,
        *,
        source: bytes,
        source_name: str,
        max_width: int,
    ) -> dict[str, Any]:
        return await self._json_request(
            "POST",
            "/v1/sessions",
            files={"source": (source_name or "source.jpg", source, "application/octet-stream")},
            data={"authorized_use": "true", "max_width": str(max_width)},
        )

    async def session_status(self, session_id: str) -> dict[str, Any]:
        return await self._json_request("GET", f"/v1/sessions/{session_id}")

    async def delete_session(self, session_id: str) -> dict[str, Any]:
        return await self._json_request("DELETE", f"/v1/sessions/{session_id}")

    @asynccontextmanager
    async def open_stream(self, session_id: str) -> AsyncIterator[DeepfakeStream]:
        try:
            async with websockets.connect(
                self._websocket_url(f"/v1/realtime/{session_id}"),
                additional_headers=self._headers,
                subprotocols=["sere1nfish"],
                ssl=self._ssl_context,
                open_timeout=self.config.timeout_seconds,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_size=8 * 1024 * 1024,
            ) as websocket:
                yield websocket
        except DeepfakeProviderError:
            raise
        except Exception as exc:
            raise DeepfakeProviderError(f"GPU realtime stream failed: {exc}") from exc
