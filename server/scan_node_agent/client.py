"""Typed HTTPS client for the node control protocol."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any

import httpx

from . import __version__
from .config import NodeConfig, load_identity, save_identity


class ControlPlaneError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class ControlPlaneClient:
    def __init__(self, config: NodeConfig) -> None:
        self.config = config
        self.identity = load_identity(config.state_path)
        self._client = httpx.AsyncClient(
            base_url=config.control_plane_url,
            verify=config.verify_tls,
            timeout=httpx.Timeout(config.request_timeout_seconds),
            headers={"User-Agent": f"Sere1nFish-ScanNode/{__version__}"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _signed_headers(
        self,
        *,
        method: str,
        path: str,
        body: bytes,
    ) -> dict[str, str]:
        if not self.identity:
            raise RuntimeError("节点尚未注册")
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        canonical = "\n".join(
            (
                method.upper(),
                path,
                timestamp,
                nonce,
                hashlib.sha256(body).hexdigest(),
            )
        ).encode("utf-8")
        signature = hmac.new(
            str(self.identity["node_token"]).encode("utf-8"),
            canonical,
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-Scan-Node-ID": str(self.identity["node_id"]),
            "X-Scan-Node-Timestamp": timestamp,
            "X-Scan-Node-Nonce": nonce,
            "X-Scan-Node-Signature": signature,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any],
        node_auth: bool = False,
    ) -> dict[str, Any]:
        body = json.dumps(
            json_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request = self._client.build_request(
            method,
            path,
            content=body,
            headers={"Content-Type": "application/json"},
        )
        if node_auth:
            request.headers.update(
                self._signed_headers(
                    method=method,
                    path=request.url.path,
                    body=body,
                )
            )
        response = await self._client.send(request)
        if response.is_error:
            message = response.text[:1000]
            try:
                payload = response.json()
                if isinstance(payload, dict) and payload.get("detail"):
                    message = str(payload["detail"])
            except ValueError:
                pass
            raise ControlPlaneError(response.status_code, message)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ControlPlaneError(response.status_code, "控制面返回格式无效")
        return payload

    async def register(self) -> dict[str, Any]:
        if self.identity:
            return self.identity
        if not self.config.bootstrap_token:
            raise RuntimeError("首次启动缺少 SCAN_NODE_BOOTSTRAP_TOKEN")
        payload = await self._request(
            "POST",
            "/distributed-scan/node/register",
            json_body={
                "bootstrap_token": self.config.bootstrap_token,
                "display_name": self.config.display_name,
                "capabilities": self.config.capabilities,
                "capacity": self.config.capacity,
                "labels": self.config.labels,
                "version": __version__,
            },
        )
        node = payload.get("node") or {}
        identity = {
            "node_id": node.get("node_id"),
            "node_token": payload.get("node_token"),
        }
        if not all(identity.values()):
            raise RuntimeError("控制面未返回完整节点身份")
        save_identity(self.config.state_path, identity)
        self.identity = identity
        return identity

    async def heartbeat(
        self,
        *,
        usage: dict[str, int],
        active_work_item_ids: list[str],
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/distributed-scan/node/heartbeat",
            node_auth=True,
            json_body={
                "usage": usage,
                "capacity": self.config.capacity,
                "active_work_item_ids": active_work_item_ids,
                "version": __version__,
            },
        )

    async def lease(self, kinds: list[str], *, wait_seconds: int = 10) -> dict[str, Any] | None:
        payload = await self._request(
            "POST",
            "/distributed-scan/node/lease",
            node_auth=True,
            json_body={"kinds": kinds, "wait_seconds": wait_seconds},
        )
        return payload if payload.get("work") else None

    async def started(self, work_item_id: str, lease_token: str, event_seq: int) -> None:
        await self._request(
            "POST",
            f"/distributed-scan/node/work-items/{work_item_id}/started",
            node_auth=True,
            json_body={"lease_token": lease_token, "event_seq": event_seq},
        )

    async def renew(self, work_item_id: str, lease_token: str) -> None:
        await self._request(
            "POST",
            f"/distributed-scan/node/work-items/{work_item_id}/renew",
            node_auth=True,
            json_body={"lease_token": lease_token, "lease_seconds": 90},
        )

    async def completed(
        self,
        work_item_id: str,
        lease_token: str,
        event_seq: int,
        result: dict[str, Any],
    ) -> None:
        await self._request(
            "POST",
            f"/distributed-scan/node/work-items/{work_item_id}/completed",
            node_auth=True,
            json_body={
                "lease_token": lease_token,
                "event_seq": event_seq,
                "result": result,
                "artifacts": [],
            },
        )

    async def failed(
        self,
        work_item_id: str,
        lease_token: str,
        event_seq: int,
        *,
        error_code: str,
        error: str,
        retryable: bool,
    ) -> None:
        await self._request(
            "POST",
            f"/distributed-scan/node/work-items/{work_item_id}/failed",
            node_auth=True,
            json_body={
                "lease_token": lease_token,
                "event_seq": event_seq,
                "error_code": error_code,
                "error": error[:4000],
                "retryable": retryable,
                "retry_delay_seconds": 5,
            },
        )
