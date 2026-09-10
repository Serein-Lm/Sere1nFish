"""Authenticated node protocol for the distributed scan control plane."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from api.dao import distributed_work as work_dao
from api.db.mongodb import get_db
from api.schemas.distributed_scan import (
    NodeHeartbeat,
    NodeLeaseRequest,
    NodeRegister,
    WorkCompleted,
    WorkFailed,
    WorkLeaseRenew,
    WorkProgress,
    WorkStarted,
)
from api.services.distributed_scan.node_registry import (
    NodeAuthenticationError,
    NodeRegistrationError,
    authenticate_node_request,
    heartbeat_node,
    register_node,
)
from api.services.distributed_scan.work_service import (
    WorkCommitError,
    WorkLeaseError,
    complete_work,
    fail_work,
    lease_work,
    renew_work_lease,
    start_work,
    update_work_progress,
)


router = APIRouter(prefix="/distributed-scan/node", tags=["分布式扫描节点"])


async def _authenticated_node(
    request: Request,
    node_id: Annotated[str, Header(alias="X-Scan-Node-ID")],
    timestamp: Annotated[str, Header(alias="X-Scan-Node-Timestamp")],
    nonce: Annotated[str, Header(alias="X-Scan-Node-Nonce")],
    signature: Annotated[str, Header(alias="X-Scan-Node-Signature")],
) -> dict[str, Any]:
    try:
        return await authenticate_node_request(
            get_db(),
            node_id=node_id.strip(),
            method=request.method,
            path=request.url.path,
            timestamp=timestamp,
            nonce=nonce,
            signature=signature,
            body=await request.body(),
        )
    except NodeAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/register", status_code=201)
async def register_scan_node(body: NodeRegister) -> dict[str, Any]:
    """Exchange a single-use bootstrap token for a persistent node identity."""

    try:
        return await register_node(
            get_db(),
            bootstrap_token=body.bootstrap_token,
            display_name=body.display_name,
            capabilities=body.capabilities,
            capacity=body.capacity,
            labels=body.labels,
            version=body.version,
        )
    except NodeRegistrationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/heartbeat")
async def node_heartbeat(
    body: NodeHeartbeat,
    node: Annotated[dict[str, Any], Depends(_authenticated_node)],
) -> dict[str, Any]:
    try:
        updated = await heartbeat_node(
            get_db(),
            node=node,
            usage=body.usage,
            capabilities=body.capabilities,
            capacity=body.capacity,
            version=body.version,
        )
    except (NodeAuthenticationError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    cancelled = await work_dao.cancelled_for_node(
        get_db(),
        str(node["node_id"]),
    )
    active = set(body.active_work_item_ids)
    return {
        "ok": True,
        "node": updated,
        "cancel_work_item_ids": [work_id for work_id in cancelled if work_id in active],
    }


@router.post("/lease")
async def lease_scan_work(
    body: NodeLeaseRequest,
    node: Annotated[dict[str, Any], Depends(_authenticated_node)],
) -> dict[str, Any]:
    allowed = set((node.get("capabilities") or {}).keys())
    requested = list(dict.fromkeys(item.strip() for item in body.kinds if item.strip()))
    if any(kind not in allowed for kind in requested):
        raise HTTPException(status_code=400, detail="请求了节点未声明的能力")
    leased = await lease_work(
        get_db(),
        node=node,
        kinds=requested or sorted(allowed),
        wait_seconds=body.wait_seconds,
    )
    return leased or {"work": None}


@router.post("/work-items/{work_item_id}/started")
async def work_started(
    work_item_id: str,
    body: WorkStarted,
    node: Annotated[dict[str, Any], Depends(_authenticated_node)],
) -> dict[str, Any]:
    try:
        work = await start_work(
            get_db(),
            node_id=str(node["node_id"]),
            work_item_id=work_item_id,
            lease_token=body.lease_token,
            event_seq=body.event_seq,
        )
    except WorkLeaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"work": work}


@router.post("/work-items/{work_item_id}/renew")
async def work_renewed(
    work_item_id: str,
    body: WorkLeaseRenew,
    node: Annotated[dict[str, Any], Depends(_authenticated_node)],
) -> dict[str, bool]:
    try:
        await renew_work_lease(
            get_db(),
            node_id=str(node["node_id"]),
            work_item_id=work_item_id,
            lease_token=body.lease_token,
            lease_seconds=body.lease_seconds,
        )
    except WorkLeaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/work-items/{work_item_id}/progress")
async def work_progress(
    work_item_id: str,
    body: WorkProgress,
    node: Annotated[dict[str, Any], Depends(_authenticated_node)],
) -> dict[str, Any]:
    try:
        work = await update_work_progress(
            get_db(),
            node_id=str(node["node_id"]),
            work_item_id=work_item_id,
            lease_token=body.lease_token,
            event_seq=body.event_seq,
            progress=body.progress,
        )
    except (WorkLeaseError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"work": work}


@router.post("/work-items/{work_item_id}/completed")
async def work_completed(
    work_item_id: str,
    body: WorkCompleted,
    node: Annotated[dict[str, Any], Depends(_authenticated_node)],
) -> dict[str, Any]:
    try:
        work = await complete_work(
            get_db(),
            node_id=str(node["node_id"]),
            work_item_id=work_item_id,
            lease_token=body.lease_token,
            event_seq=body.event_seq,
            result=body.result,
            artifacts=body.artifacts,
        )
    except (WorkLeaseError, WorkCommitError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"work": work}


@router.post("/work-items/{work_item_id}/failed")
async def work_failed(
    work_item_id: str,
    body: WorkFailed,
    node: Annotated[dict[str, Any], Depends(_authenticated_node)],
) -> dict[str, Any]:
    try:
        work = await fail_work(
            get_db(),
            node_id=str(node["node_id"]),
            work_item_id=work_item_id,
            lease_token=body.lease_token,
            event_seq=body.event_seq,
            error_code=body.error_code,
            error=body.error,
            retryable=body.retryable,
            retry_delay_seconds=body.retry_delay_seconds,
        )
    except WorkLeaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"work": work}
