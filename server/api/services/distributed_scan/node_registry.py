"""Node registration, authentication, health and lifecycle service."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import scan_nodes as nodes_dao

from .security import (
    generate_secret,
    request_signature,
    secret_digest,
    signature_matches,
)
from .validation import (
    normalize_capabilities,
    normalize_capacity,
    normalize_labels,
    normalize_usage,
)


class NodeAuthenticationError(PermissionError):
    pass


class NodeRegistrationError(ValueError):
    pass


async def issue_bootstrap_token(
    db: AsyncIOMotorDatabase,
    *,
    display_name: str,
    allowed_capabilities: list[str],
    labels: dict[str, str],
    expires_minutes: int,
    issued_by: str,
) -> dict[str, Any]:
    normalized_name = display_name.strip()
    if not normalized_name:
        raise ValueError("节点名称不能为空")
    capabilities = normalize_capabilities(allowed_capabilities)
    token = generate_secret("snb")
    bootstrap_id = "snb_" + uuid.uuid4().hex
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=max(5, min(expires_minutes, 1440))
    )
    stored = await nodes_dao.create_bootstrap(
        db,
        bootstrap_id=bootstrap_id,
        token_digest=secret_digest(token),
        display_name=normalized_name,
        allowed_capabilities=sorted(capabilities),
        labels=normalize_labels(labels),
        expires_at=expires_at,
        issued_by=issued_by,
    )
    return {**stored, "bootstrap_token": token}


async def register_node(
    db: AsyncIOMotorDatabase,
    *,
    bootstrap_token: str,
    display_name: str,
    capabilities: dict[str, str],
    capacity: dict[str, int],
    labels: dict[str, str],
    version: str,
) -> dict[str, Any]:
    bootstrap_digest = secret_digest(bootstrap_token)
    bootstrap = await nodes_dao.get_active_bootstrap(
        db, token_digest=bootstrap_digest
    )
    if not bootstrap:
        raise NodeRegistrationError("节点注册凭据无效、已使用或已过期")
    allowed = set(bootstrap.get("allowed_capabilities") or [])
    normalized_capabilities = normalize_capabilities(capabilities, allowed=allowed)
    normalized_capacity = normalize_capacity(capacity, set(normalized_capabilities))
    normalized_labels = normalize_labels(labels)
    consumed = await nodes_dao.consume_bootstrap(
        db,
        token_digest=bootstrap_digest,
    )
    if not consumed:
        raise NodeRegistrationError("节点注册凭据无效、已使用或已过期")
    bootstrap = consumed
    node_token = generate_secret("snn")
    node_id = "node_" + uuid.uuid4().hex
    merged_labels = {
        **normalize_labels(bootstrap.get("labels") or {}),
        **normalized_labels,
    }
    node = await nodes_dao.register_node(
        db,
        node_id=node_id,
        bootstrap_id=str(bootstrap["bootstrap_id"]),
        display_name=display_name.strip() or str(bootstrap.get("display_name") or node_id),
        token_digest=secret_digest(node_token),
        token_secret=node_token,
        identity_fingerprint="sha256:" + secret_digest(node_token),
        capabilities=normalized_capabilities,
        capacity=normalized_capacity,
        labels=merged_labels,
        version=version.strip() or "unknown",
    )
    return {"node": node, "node_token": node_token}


async def authenticate_node_request(
    db: AsyncIOMotorDatabase,
    *,
    node_id: str,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    signature: str,
    body: bytes,
) -> dict[str, Any]:
    if not node_id or not timestamp or not nonce or not signature:
        raise NodeAuthenticationError("缺少节点签名")
    if len(nonce) < 16 or len(nonce) > 128 or not all(
        character.isalnum() or character in {"-", "_"} for character in nonce
    ):
        raise NodeAuthenticationError("节点 nonce 无效")
    try:
        signed_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    except (TypeError, ValueError, OSError) as exc:
        raise NodeAuthenticationError("节点签名时间无效") from exc
    now = datetime.now(timezone.utc)
    if abs((now - signed_at).total_seconds()) > 120:
        raise NodeAuthenticationError("节点签名已过期")
    authenticated = await nodes_dao.get_node_signing_secret(db, node_id=node_id)
    if not authenticated:
        raise NodeAuthenticationError("节点身份无效或已禁用")
    node, secret = authenticated
    expected = request_signature(
        secret,
        method=method,
        path=path,
        timestamp=timestamp,
        nonce=nonce,
        body=body,
    )
    if not signature_matches(expected, signature):
        raise NodeAuthenticationError("节点请求签名无效")
    consumed = await nodes_dao.consume_request_nonce(
        db,
        node_id=node_id,
        nonce=nonce,
        expires_at=now + timedelta(minutes=5),
    )
    if not consumed:
        raise NodeAuthenticationError("节点请求已重放")
    public = dict(node)
    for key in ("token_digest", "token_secret", "bootstrap_id"):
        public.pop(key, None)
    return public


async def heartbeat_node(
    db: AsyncIOMotorDatabase,
    *,
    node: dict[str, Any],
    usage: dict[str, int],
    capabilities: dict[str, str] | None,
    capacity: dict[str, int] | None,
    version: str,
) -> dict[str, Any]:
    current_capabilities = dict(node.get("capabilities") or {})
    normalized_capabilities = None
    normalized_capacity = None
    if capabilities is not None:
        normalized_capabilities = normalize_capabilities(
            capabilities,
            allowed=set(current_capabilities),
        )
    if capacity is not None:
        normalized_capacity = normalize_capacity(
            capacity,
            set(normalized_capabilities or current_capabilities),
        )
    updated = await nodes_dao.heartbeat(
        db,
        node_id=str(node["node_id"]),
        usage=normalize_usage(usage),
        capabilities=normalized_capabilities,
        capacity=normalized_capacity,
        version=version,
    )
    if not updated:
        raise NodeAuthenticationError("节点已禁用或不存在")
    return updated


async def rotate_node_token(
    db: AsyncIOMotorDatabase,
    *,
    node_id: str,
) -> str:
    token = generate_secret("snn")
    updated = await nodes_dao.rotate_token(
        db,
        node_id=node_id,
        token_digest=secret_digest(token),
        token_secret=token,
        identity_fingerprint="sha256:" + secret_digest(token),
    )
    if not updated:
        raise LookupError("扫描节点不存在")
    return token
