from __future__ import annotations

import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from api.dao import distributed_work as distributed_work_dao
from api.dao.proxy_profiles import acquire_lease
from api.db.collections import PROXY_LEASES_COLLECTION, PROXY_PROFILES_COLLECTION
from api.schemas.distributed_scan import NodeRegister
from api.services.distributed_scan.contracts import GatewayPolicy, WorkSpec
from api.services.distributed_scan.execution import DistributedExecutionGateway
from api.services.distributed_scan.proxy_service import (
    ProxyUnavailableError,
    lease_proxy_for_work,
)
from api.services.distributed_scan.node_registry import (
    NodeAuthenticationError,
    authenticate_node_request,
    register_node,
)
from api.services.distributed_scan.security import request_signature
from api.services.distributed_scan.settings import normalize_runtime_config
from api.services.distributed_scan.validation import (
    validate_work_payload,
    validate_work_result,
)
from api.services.distributed_scan.work_service import enqueue_work
from scan_node_agent.config import load_identity, save_identity
from scan_node_agent.workers import _safe_error


def test_runtime_config_is_bounded_and_survives_invalid_values() -> None:
    value = normalize_runtime_config(
        {
            "enabled": "true",
            "kinds": ["http_probe", "not-supported"],
            "wait_seconds": "invalid",
            "poll_seconds": 100,
            "dispatch_concurrency": 9999,
            "batch_sizes": {"http_probe": 999, "browser_probe": 0},
            "proxy_mode": "invalid",
        }
    )

    assert value["enabled"] is True
    assert value["kinds"] == ["http_probe"]
    assert value["wait_seconds"] == 60.0
    assert value["poll_seconds"] == 5.0
    assert value["dispatch_concurrency"] == 32
    assert value["batch_sizes"] == {"http_probe": 50, "browser_probe": 1}
    assert value["proxy_mode"] == "none"


def test_work_payload_rejects_non_http_urls() -> None:
    with pytest.raises(ValueError, match=r"HTTP\(S\)"):
        validate_work_payload("http_probe", {"urls": ["file:///etc/passwd"]})


def test_browser_html_work_rejects_multiple_urls() -> None:
    with pytest.raises(ValueError, match="最多包含 1 个 URL"):
        validate_work_payload(
            "browser_probe",
            {
                "urls": ["https://one.example", "https://two.example"],
                "include_html": True,
            },
        )


def test_node_registration_rejects_empty_capabilities_before_token_use() -> None:
    with pytest.raises(ValidationError):
        NodeRegister(
            bootstrap_token="x" * 32,
            capabilities={},
        )


@pytest.mark.asyncio
async def test_invalid_node_capability_does_not_consume_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services.distributed_scan import node_registry

    consume = AsyncMock()
    monkeypatch.setattr(
        node_registry.nodes_dao,
        "get_active_bootstrap",
        AsyncMock(
            return_value={
                "bootstrap_id": "bootstrap-1",
                "allowed_capabilities": ["http_probe"],
            }
        ),
    )
    monkeypatch.setattr(node_registry.nodes_dao, "consume_bootstrap", consume)

    with pytest.raises(ValueError, match="未授权能力"):
        await register_node(
            object(),
            bootstrap_token="bootstrap-token",
            display_name="node-1",
            capabilities={"browser_probe": "1"},
            capacity={"browser_probe_slots": 1},
            labels={},
            version="test",
        )

    consume.assert_not_awaited()


def test_work_result_rejects_urls_not_present_in_payload() -> None:
    with pytest.raises(ValueError, match="URL 集合"):
        validate_work_result(
            "http_probe",
            {"urls": ["https://example.com"]},
            {"items": {"https://attacker.example": {"is_alive": True}}},
        )


@pytest.mark.asyncio
async def test_gateway_disabled_preserves_local_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services.distributed_scan import execution

    async def policy(_kind: str) -> GatewayPolicy:
        return GatewayPolicy(enabled=False, kinds=frozenset({"http_probe"}))

    monkeypatch.setattr(execution, "get_gateway_policy", policy)
    calls: list[list[str]] = []

    async def local(urls: list[str]) -> dict[str, dict[str, Any]]:
        calls.append(urls)
        return {url: {"is_alive": True} for url in urls}

    result = await DistributedExecutionGateway(object()).execute_url_batches(
        kind="http_probe",
        urls=["https://example.com"],
        project_id="project-1",
        task_id="task-1",
        target_id="target-1",
        timeout=5,
        concurrency=4,
        local_batch=local,
    )

    assert calls == [["https://example.com"]]
    assert result["https://example.com"]["is_alive"] is True


@pytest.mark.asyncio
async def test_required_proxy_never_silently_falls_back_to_direct() -> None:
    with pytest.raises(ProxyUnavailableError, match="没有指定"):
        await lease_proxy_for_work(
            object(),
            work={"work_item_id": "work-1", "proxy_mode": "required"},
            node_id="node-1",
        )


@pytest.mark.asyncio
async def test_proxy_lease_record_is_reused_across_work_retries() -> None:
    profile_collection = MagicMock()
    profile_collection.find_one_and_update = AsyncMock(
        return_value={
            "profile_id": "proxy-1",
            "endpoint": "socks5h://proxy.example:1080",
            "username": "scan-user",
            "password": "scan-password",
        }
    )
    profile_collection.update_one = AsyncMock()
    lease_collection = MagicMock()
    lease_collection.find_one = AsyncMock(return_value=None)
    lease_collection.find_one_and_update = AsyncMock(
        return_value={
            "lease_id": "new-lease",
            "profile_id": "proxy-1",
            "node_id": "node-1",
            "work_item_id": "work-1",
            "work_attempt": 2,
            "status": "active",
        }
    )
    db = MagicMock()
    db.__getitem__.side_effect = lambda name: (
        profile_collection if name == PROXY_PROFILES_COLLECTION else lease_collection
    )

    result = await acquire_lease(
        db,
        lease_id="new-lease",
        profile_id="proxy-1",
        node_id="node-1",
        work_item_id="work-1",
        work_attempt=2,
        sticky_key="target-1",
        expires_at=datetime.now(timezone.utc),
    )

    assert result is not None
    assert result["lease"]["work_attempt"] == 2
    assert result["profile"]["password"] == "scan-password"
    lease_collection.find_one_and_update.assert_awaited_once()
    query = lease_collection.find_one_and_update.await_args.args[0]
    update = lease_collection.find_one_and_update.await_args.args[1]
    assert query == {"work_item_id": "work-1", "status": {"$ne": "active"}}
    assert update["$set"]["work_attempt"] == 2
    assert lease_collection.find_one_and_update.await_args.kwargs["upsert"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attempt", "max_attempts", "expected_status"),
    ((1, 2, "retry"), (2, 2, "failed")),
)
async def test_abandoned_work_stops_after_max_attempts(
    attempt: int,
    max_attempts: int,
    expected_status: str,
) -> None:
    collection = MagicMock()
    collection.find_one = AsyncMock(
        return_value={"attempt": attempt, "max_attempts": max_attempts}
    )
    collection.update_one = AsyncMock(
        return_value=MagicMock(modified_count=1)
    )
    db = MagicMock()
    db.__getitem__.return_value = collection

    changed = await distributed_work_dao.abandon_lease(
        db,
        work_item_id="work-1",
        node_id="node-1",
        reason="proxy unavailable",
        delay_seconds=0,
    )

    assert changed is True
    update = collection.update_one.await_args.args[1]
    assert update["$set"]["status"] == expected_status
    assert update["$unset"] == {"lease": ""}
    assert ("failed_at" in update["$set"]) is (expected_status == "failed")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token_digest", "expected_duplicate"),
    (("lease-token-digest", True), ("different-token-digest", False)),
)
async def test_completed_work_replay_requires_original_lease_token(
    token_digest: str,
    expected_duplicate: bool,
) -> None:
    collection = MagicMock()
    collection.find_one = AsyncMock(
        return_value={
            "work_item_id": "work-1",
            "status": "completed",
            "completion_digest": "result-digest",
            "lease": {
                "node_id": "node-1",
                "token_digest": "lease-token-digest",
            },
        }
    )
    collection.find_one_and_update = AsyncMock()
    db = MagicMock()
    db.__getitem__.return_value = collection

    work, duplicate = await distributed_work_dao.complete(
        db,
        work_item_id="work-1",
        node_id="node-1",
        token_digest=token_digest,
        event_seq=2,
        result={"items": {}},
        artifacts=[],
        completion_digest="result-digest",
    )

    assert duplicate is expected_duplicate
    assert (work is not None) is expected_duplicate
    collection.find_one_and_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_work_idempotency_key_is_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services.distributed_scan import work_service

    documents: list[dict[str, Any]] = []

    async def fake_enqueue(_db: Any, document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        documents.append(document)
        return document, True

    monkeypatch.setattr(work_service.work_dao, "enqueue", fake_enqueue)
    spec = WorkSpec(
        kind="http_probe",
        payload={"urls": ["https://example.com"], "timeout": 5},
        project_id="project-1",
        task_id="task-1",
        target_id="target-1",
    )

    await enqueue_work(object(), spec)
    await enqueue_work(object(), spec)

    assert documents[0]["idempotency_key"] == documents[1]["idempotency_key"]
    assert documents[0]["idempotency_key"].startswith("dwi:")


def test_node_identity_file_is_written_with_owner_only_permissions(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    identity = {"node_id": "node-1", "node_token": "secret-token"}

    save_identity(path, identity)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_identity(path) == identity


def test_node_identity_rejects_overly_permissive_file(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    save_identity(path, {"node_id": "node-1", "node_token": "secret-token"})
    path.chmod(0o644)

    with pytest.raises(PermissionError, match="0600"):
        load_identity(path)


def test_node_worker_error_redacts_proxy_credentials() -> None:
    message = _safe_error(
        RuntimeError(
            "connect socks5://scan-user:scan-password@proxy.example:1080 failed"
        ),
        {
            "endpoint": "socks5://proxy.example:1080",
            "username": "scan-user",
            "password": "scan-password",
        },
    )

    assert "scan-user" not in message
    assert "scan-password" not in message
    assert "proxy.example" not in message
    assert "***" in message


@pytest.mark.asyncio
async def test_signed_node_request_rejects_nonce_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services.distributed_scan import node_registry

    used: set[str] = set()

    async def signing_secret(_db: Any, *, node_id: str) -> tuple[dict[str, Any], str]:
        return {"node_id": node_id, "status": "online"}, "node-secret"

    async def consume_nonce(_db: Any, *, node_id: str, nonce: str, expires_at: Any) -> bool:
        del node_id, expires_at
        if nonce in used:
            return False
        used.add(nonce)
        return True

    monkeypatch.setattr(node_registry.nodes_dao, "get_node_signing_secret", signing_secret)
    monkeypatch.setattr(node_registry.nodes_dao, "consume_request_nonce", consume_nonce)
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    body = b'{"usage":{}}'
    signature = request_signature(
        "node-secret",
        method="POST",
        path="/api/v1/distributed-scan/node/heartbeat",
        timestamp=timestamp,
        nonce="nonce_abcdefghijklmnop",
        body=body,
    )
    arguments = {
        "node_id": "node-1",
        "method": "POST",
        "path": "/api/v1/distributed-scan/node/heartbeat",
        "timestamp": timestamp,
        "nonce": "nonce_abcdefghijklmnop",
        "signature": signature,
        "body": body,
    }

    node = await authenticate_node_request(object(), **arguments)
    assert node["node_id"] == "node-1"
    with pytest.raises(NodeAuthenticationError, match="重放"):
        await authenticate_node_request(object(), **arguments)
