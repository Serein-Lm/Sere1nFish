"""Administrator and operator API for distributed scan infrastructure."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.errors import DuplicateKeyError

from api.auth import User, require_permission
from api.dao import distributed_work as work_dao
from api.dao import proxy_profiles as proxy_dao
from api.dao import scan_nodes as nodes_dao
from api.db.mongodb import get_db
from api.schemas.distributed_scan import (
    NodeBootstrapCreate,
    NodeStatusUpdate,
    ProxyProfileUpsert,
    WorkCreate,
)
from api.services.authorization import Permissions
from api.services.distributed_scan.contracts import WorkSpec
from api.services.distributed_scan.node_registry import (
    issue_bootstrap_token,
    rotate_node_token,
)
from api.services.distributed_scan.proxy_service import (
    save_proxy_profile,
    test_proxy_profile,
)
from api.services.distributed_scan.settings import get_distributed_scan_settings
from api.services.distributed_scan.work_service import cancel_work, enqueue_work


router = APIRouter(prefix="/distributed-scan", tags=["分布式扫描"])
_read_infrastructure = require_permission(Permissions.OBSERVABILITY_READ)
_manage_infrastructure = require_permission(Permissions.CONFIG_MANAGE)


@router.get("/overview")
async def overview(
    _user: Annotated[User, Depends(_read_infrastructure)],
) -> dict:
    db = get_db()
    node_stats = await nodes_dao.get_stats(db)
    work_stats = await work_dao.get_stats(db)
    proxy_stats = await proxy_dao.get_stats(db)
    runtime = await get_distributed_scan_settings()
    return {
        "nodes": node_stats,
        "work": work_stats,
        "proxies": proxy_stats,
        "runtime": {
            "enabled": runtime["enabled"],
            "kinds": runtime["kinds"],
            "project_ids": runtime["project_ids"],
            "fallback_local": runtime["fallback_local"],
            "dispatch_concurrency": runtime["dispatch_concurrency"],
            "wait_seconds": runtime["wait_seconds"],
            "batch_sizes": runtime["batch_sizes"],
            "proxy_mode": str(runtime.get("proxy_mode") or "none"),
            "has_default_proxy": bool(runtime["default_proxy_profile_id"]),
        },
    }


@router.get("/capabilities")
async def capabilities(
    _user: Annotated[User, Depends(_read_infrastructure)],
) -> dict:
    return {
        "items": [
            {"name": "http_probe", "label": "HTTP 探活", "payload_version": 1},
            {"name": "browser_probe", "label": "浏览器探活", "payload_version": 1},
        ]
    }


@router.post("/nodes/bootstrap", status_code=201)
async def create_node_bootstrap(
    body: NodeBootstrapCreate,
    current: Annotated[User, Depends(_manage_infrastructure)],
) -> dict:
    try:
        return await issue_bootstrap_token(
            get_db(),
            display_name=body.display_name,
            allowed_capabilities=body.allowed_capabilities,
            labels=body.labels,
            expires_minutes=body.expires_minutes,
            issued_by=current.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/nodes")
async def list_nodes(
    _user: Annotated[User, Depends(_read_infrastructure)],
    status: str = "",
) -> dict:
    items = await nodes_dao.list_nodes(get_db(), status=status.strip())
    return {"items": items, "total": len(items)}


@router.put("/nodes/{node_id}/status")
async def update_node_status(
    node_id: str,
    body: NodeStatusUpdate,
    _user: Annotated[User, Depends(_manage_infrastructure)],
) -> dict:
    node = await nodes_dao.set_status(
        get_db(),
        node_id=node_id,
        status=body.status,
    )
    if not node:
        raise HTTPException(status_code=404, detail="扫描节点不存在")
    return {"node": node}


@router.post("/nodes/{node_id}/rotate-token")
async def rotate_node_credentials(
    node_id: str,
    _user: Annotated[User, Depends(_manage_infrastructure)],
) -> dict:
    try:
        token = await rotate_node_token(get_db(), node_id=node_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"node_id": node_id, "node_token": token}


@router.get("/work-items")
async def list_work_items(
    _user: Annotated[User, Depends(_read_infrastructure)],
    status: str = "",
    kind: str = "",
    project_id: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict:
    items, total = await work_dao.list_work(
        get_db(),
        status=status.strip(),
        kind=kind.strip(),
        project_id=project_id.strip(),
        skip=(page - 1) * page_size,
        limit=page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/work-items/{work_item_id}")
async def get_work_item(
    work_item_id: str,
    _user: Annotated[User, Depends(_read_infrastructure)],
) -> dict:
    work = await work_dao.get_work(get_db(), work_item_id)
    if not work:
        raise HTTPException(status_code=404, detail="工作项不存在")
    return work


@router.post("/work-items", status_code=201)
async def create_work_item(
    body: WorkCreate,
    _user: Annotated[User, Depends(_manage_infrastructure)],
) -> dict:
    try:
        work, created = await enqueue_work(
            get_db(),
            WorkSpec(
                kind=body.kind,
                payload=body.payload,
                project_id=body.project_id,
                task_id=body.task_id,
                target_id=body.target_id,
                requirements=(body.kind,),
                proxy_profile_id=body.proxy_profile_id,
                proxy_mode=body.proxy_mode,
                affinity_key=body.affinity_key,
                priority=body.priority,
                max_attempts=body.max_attempts,
                idempotency_key=body.idempotency_key,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"work": work, "created": created}


@router.post("/work-items/{work_item_id}/cancel")
async def cancel_work_item(
    work_item_id: str,
    current: Annotated[User, Depends(_manage_infrastructure)],
) -> dict:
    try:
        work = await cancel_work(
            get_db(),
            work_item_id=work_item_id,
            reason=f"管理员 {current.username} 取消",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"work": work}


@router.get("/proxy-profiles")
async def list_proxy_profiles(
    _user: Annotated[User, Depends(_read_infrastructure)],
    status: str = "",
) -> dict:
    items = await proxy_dao.list_profiles(get_db(), status=status.strip())
    return {"items": items, "total": len(items)}


@router.post("/proxy-profiles", status_code=201)
async def create_proxy_profile(
    body: ProxyProfileUpsert,
    current: Annotated[User, Depends(_manage_infrastructure)],
) -> dict:
    return await _save_proxy("", body, current)


@router.put("/proxy-profiles/{profile_id}")
async def update_proxy_profile(
    profile_id: str,
    body: ProxyProfileUpsert,
    current: Annotated[User, Depends(_manage_infrastructure)],
) -> dict:
    if not await proxy_dao.get_profile(get_db(), profile_id):
        raise HTTPException(status_code=404, detail="代理配置不存在")
    return await _save_proxy(profile_id, body, current)


async def _save_proxy(
    profile_id: str,
    body: ProxyProfileUpsert,
    current: User,
) -> dict:
    try:
        profile = await save_proxy_profile(
            get_db(),
            profile_id=profile_id,
            name=body.name,
            endpoint=body.endpoint,
            username=body.username,
            password=body.password,
            dns_mode=body.dns_mode,
            max_concurrency=body.max_concurrency,
            failure_threshold=body.failure_threshold,
            cooldown_seconds=body.cooldown_seconds,
            bypass_classes=body.bypass_classes,
            labels=body.labels,
            status=body.status,
            updated_by=current.username,
        )
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="代理配置名称已存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"profile": profile}


@router.post("/proxy-profiles/{profile_id}/test")
async def test_proxy(
    profile_id: str,
    _user: Annotated[User, Depends(_manage_infrastructure)],
) -> dict:
    try:
        result = await test_proxy_profile(get_db(), profile_id=profile_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result)
    return result


@router.delete("/proxy-profiles/{profile_id}")
async def delete_proxy_profile(
    profile_id: str,
    _user: Annotated[User, Depends(_manage_infrastructure)],
) -> dict:
    deleted = await proxy_dao.delete_profile(get_db(), profile_id)
    if not deleted:
        if await proxy_dao.get_profile(get_db(), profile_id):
            raise HTTPException(status_code=409, detail="代理仍有活动租约，无法删除")
        raise HTTPException(status_code=404, detail="代理配置不存在")
    return {"deleted": True, "profile_id": profile_id}
