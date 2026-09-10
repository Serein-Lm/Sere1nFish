"""Domain service for scheduling, leasing and committing distributed work."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import distributed_work as work_dao
from api.dao import proxy_profiles as proxy_dao
from api.dao import storage_objects as storage_dao
from core.observability import obs_log

from .contracts import WorkSpec
from .proxy_service import (
    ProxyUnavailableError,
    lease_proxy_for_work,
    release_proxy_for_work,
)
from .security import generate_secret, secret_digest, stable_payload_digest
from .validation import (
    bounded_json,
    normalize_artifacts,
    validate_work_payload,
    validate_work_result,
)


class WorkLeaseError(RuntimeError):
    pass


class WorkCommitError(RuntimeError):
    pass


def _work_idempotency_key(spec: WorkSpec, payload_bytes: bytes) -> str:
    if spec.idempotency_key:
        return spec.idempotency_key[:256]
    identity = "\0".join(
        (spec.task_id, spec.project_id, spec.target_id, spec.kind)
    ).encode("utf-8") + b"\0" + payload_bytes
    return "dwi:" + stable_payload_digest(identity)


async def enqueue_work(
    db: AsyncIOMotorDatabase,
    spec: WorkSpec,
) -> tuple[dict[str, Any], bool]:
    payload = validate_work_payload(spec.kind, spec.payload)
    payload_bytes = bounded_json(payload, max_bytes=256 * 1024, label="工作参数")
    requirements = sorted(set(spec.requirements or (spec.kind,)))
    document = {
        "work_item_id": "work_" + uuid.uuid4().hex,
        "task_id": spec.task_id,
        "project_id": spec.project_id,
        "target_id": spec.target_id,
        "kind": spec.kind,
        "payload_version": 1,
        "payload": payload,
        "requirements": {"capabilities": requirements},
        "proxy_profile_id": spec.proxy_profile_id,
        "proxy_mode": spec.proxy_mode,
        "affinity_key": spec.affinity_key,
        "priority": max(0, min(spec.priority, 100)),
        "max_attempts": max(1, min(spec.max_attempts, 10)),
        "idempotency_key": _work_idempotency_key(spec, payload_bytes),
    }
    work, created = await work_dao.enqueue(db, document)
    if created:
        obs_log(
            "分布式工作项已排队",
            project_id=spec.project_id,
            task_id=spec.task_id,
            source="distributed_scan",
            event="work_queued",
            data={
                "work_item_id": work["work_item_id"],
                "target_id": spec.target_id,
                "kind": spec.kind,
            },
        )
    return work, created


async def lease_work(
    db: AsyncIOMotorDatabase,
    *,
    node: dict[str, Any],
    kinds: list[str],
    wait_seconds: int,
    lease_seconds: int = 90,
) -> dict[str, Any] | None:
    if node.get("status") != "online":
        return None
    capabilities = set((node.get("capabilities") or {}).keys())
    deadline = asyncio.get_running_loop().time() + max(0, min(wait_seconds, 25))
    while True:
        lease_token = generate_secret("wlt")
        work = await work_dao.lease_next(
            db,
            node_id=str(node["node_id"]),
            capabilities=capabilities,
            token_digest=secret_digest(lease_token),
            lease_seconds=lease_seconds,
            kinds=kinds,
        )
        if work:
            try:
                proxy = await lease_proxy_for_work(
                    db,
                    work=work,
                    node_id=str(node["node_id"]),
                )
            except ProxyUnavailableError as exc:
                await work_dao.abandon_lease(
                    db,
                    work_item_id=str(work["work_item_id"]),
                    node_id=str(node["node_id"]),
                    reason=str(exc),
                    delay_seconds=10,
                )
                if asyncio.get_running_loop().time() >= deadline:
                    return None
                await asyncio.sleep(0.25)
                continue
            if (
                proxy is None
                and work.get("proxy_mode") == "best_effort"
                and work.get("proxy_profile_id")
            ):
                obs_log(
                    "分布式工作代理不可用，按策略回退直连",
                    project_id=str(work.get("project_id") or ""),
                    task_id=str(work.get("task_id") or ""),
                    source="distributed_scan",
                    level="warning",
                    event="proxy_fallback",
                    data={
                        "work_item_id": work["work_item_id"],
                        "node_id": node["node_id"],
                        "target_id": work.get("target_id", ""),
                        "proxy_profile_id": work.get("proxy_profile_id", ""),
                    },
                )
            obs_log(
                "分布式工作项已租用",
                project_id=str(work.get("project_id") or ""),
                task_id=str(work.get("task_id") or ""),
                source="distributed_scan",
                event="work_leased",
                data={
                    "work_item_id": work["work_item_id"],
                    "node_id": node["node_id"],
                    "target_id": work.get("target_id", ""),
                    "kind": work.get("kind", ""),
                },
            )
            return {"work": work, "lease_token": lease_token, "proxy": proxy}
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(0.5)


async def start_work(
    db: AsyncIOMotorDatabase,
    *,
    node_id: str,
    work_item_id: str,
    lease_token: str,
    event_seq: int,
) -> dict[str, Any]:
    work = await work_dao.mark_started(
        db,
        work_item_id=work_item_id,
        node_id=node_id,
        token_digest=secret_digest(lease_token),
        event_seq=event_seq,
    )
    if not work:
        raise WorkLeaseError("工作租约无效、已过期或事件序号重复")
    await work_dao.record_event(
        db,
        work=work,
        node_id=node_id,
        event_seq=event_seq,
        event_type="started",
    )
    return work


async def renew_work_lease(
    db: AsyncIOMotorDatabase,
    *,
    node_id: str,
    work_item_id: str,
    lease_token: str,
    lease_seconds: int,
) -> None:
    renewed = await work_dao.renew_lease(
        db,
        work_item_id=work_item_id,
        node_id=node_id,
        token_digest=secret_digest(lease_token),
        lease_seconds=lease_seconds,
    )
    if not renewed:
        raise WorkLeaseError("工作租约无效或已过期")
    await proxy_dao.renew_work_lease(
        db,
        work_item_id=work_item_id,
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=max(30, min(lease_seconds, 300))),
    )


async def update_work_progress(
    db: AsyncIOMotorDatabase,
    *,
    node_id: str,
    work_item_id: str,
    lease_token: str,
    event_seq: int,
    progress: dict[str, Any],
) -> dict[str, Any]:
    bounded_json(progress, max_bytes=32 * 1024, label="进度")
    work = await work_dao.record_progress(
        db,
        work_item_id=work_item_id,
        node_id=node_id,
        token_digest=secret_digest(lease_token),
        event_seq=event_seq,
        progress=progress,
    )
    if not work:
        raise WorkLeaseError("工作租约无效、已过期或事件序号重复")
    await work_dao.record_event(
        db,
        work=work,
        node_id=node_id,
        event_seq=event_seq,
        event_type="progress",
        data=progress,
    )
    return work


async def _validate_artifact_objects(
    db: AsyncIOMotorDatabase,
    *,
    work: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> None:
    for artifact in artifacts:
        stored = await storage_dao.get_object(db, artifact["storage_object_id"])
        if not stored or stored.get("status") != "ready":
            raise WorkCommitError(
                f"产物对象不存在或未就绪: {artifact['storage_object_id']}"
            )
        expected_hash = artifact.get("sha256")
        if expected_hash and stored.get("sha256") != expected_hash:
            raise WorkCommitError(
                f"产物校验和不一致: {artifact['storage_object_id']}"
            )
        project_id = str(work.get("project_id") or "")
        stored_project = str(stored.get("project_id") or "")
        if project_id and stored_project != project_id:
            raise WorkCommitError("产物不属于当前 Project")
        expected_size = int(artifact.get("size") or 0)
        if expected_size and int(stored.get("size") or 0) != expected_size:
            raise WorkCommitError(
                f"产物大小不一致: {artifact['storage_object_id']}"
            )


async def complete_work(
    db: AsyncIOMotorDatabase,
    *,
    node_id: str,
    work_item_id: str,
    lease_token: str,
    event_seq: int,
    result: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    current = await work_dao.get_work(db, work_item_id)
    if not current:
        raise WorkCommitError("工作项不存在")
    normalized_result = validate_work_result(
        str(current.get("kind") or ""),
        dict(current.get("payload") or {}),
        result,
    )
    result_bytes = bounded_json(
        normalized_result,
        max_bytes=1024 * 1024,
        label="工作结果",
    )
    normalized_artifacts = normalize_artifacts(artifacts)
    await _validate_artifact_objects(db, work=current, artifacts=normalized_artifacts)
    completion_digest = stable_payload_digest(
        result_bytes
        + b"\0"
        + bounded_json(normalized_artifacts, max_bytes=128 * 1024, label="产物清单")
    )
    work, duplicate = await work_dao.complete(
        db,
        work_item_id=work_item_id,
        node_id=node_id,
        token_digest=secret_digest(lease_token),
        event_seq=event_seq,
        result=normalized_result,
        artifacts=normalized_artifacts,
        completion_digest=completion_digest,
    )
    if not work:
        raise WorkCommitError("完成事件与当前租约不匹配")
    if not duplicate:
        await release_proxy_for_work(db, work_item_id=work_item_id, ok=True)
        await work_dao.record_event(
            db,
            work=work,
            node_id=node_id,
            event_seq=event_seq,
            event_type="completed",
            data={"artifact_count": len(normalized_artifacts)},
        )
        obs_log(
            "分布式工作项已完成",
            project_id=str(work.get("project_id") or ""),
            task_id=str(work.get("task_id") or ""),
            source="distributed_scan",
            event="work_completed",
            data={
                "work_item_id": work_item_id,
                "node_id": node_id,
                "target_id": work.get("target_id", ""),
                "kind": work.get("kind", ""),
            },
        )
    return work


async def fail_work(
    db: AsyncIOMotorDatabase,
    *,
    node_id: str,
    work_item_id: str,
    lease_token: str,
    event_seq: int,
    error_code: str,
    error: str,
    retryable: bool,
    retry_delay_seconds: int,
) -> dict[str, Any]:
    work = await work_dao.fail(
        db,
        work_item_id=work_item_id,
        node_id=node_id,
        token_digest=secret_digest(lease_token),
        event_seq=event_seq,
        error_code=error_code,
        error=error,
        retryable=retryable,
        retry_delay_seconds=retry_delay_seconds,
    )
    if not work:
        raise WorkLeaseError("失败事件与当前租约不匹配")
    await release_proxy_for_work(
        db,
        work_item_id=work_item_id,
        ok=False,
        error_code=error_code,
    )
    await work_dao.record_event(
        db,
        work=work,
        node_id=node_id,
        event_seq=event_seq,
        event_type="failed",
        data={"code": error_code, "retryable": retryable},
    )
    obs_log(
        "分布式工作项执行失败",
        project_id=str(work.get("project_id") or ""),
        task_id=str(work.get("task_id") or ""),
        source="distributed_scan",
        level="warning",
        event="work_failed",
        data={
            "work_item_id": work_item_id,
            "node_id": node_id,
            "target_id": work.get("target_id", ""),
            "kind": work.get("kind", ""),
            "error_code": error_code,
        },
    )
    return work


async def cancel_work(
    db: AsyncIOMotorDatabase,
    *,
    work_item_id: str,
    reason: str,
) -> dict[str, Any]:
    work = await work_dao.cancel(db, work_item_id=work_item_id, reason=reason)
    if not work:
        existing = await work_dao.get_work(db, work_item_id)
        if not existing:
            raise LookupError("工作项不存在")
        if existing.get("status") in work_dao.TERMINAL_STATUSES:
            return existing
        raise RuntimeError("工作项无法取消")
    await release_proxy_for_work(db, work_item_id=work_item_id)
    return work
