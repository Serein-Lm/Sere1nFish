"""RBAC management API and SSO role-mapping preview."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import User, require_permission
from api.dao import rbac as rbac_dao
from api.dao import users as users_dao
from api.db.mongodb import get_db
from api.services.authorization import (
    ExternalIdentity,
    PERMISSION_CATALOG,
    Permissions,
    get_authorization_service,
    validate_permissions,
)


router = APIRouter()
_ROLE_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_manage_rbac = require_permission(Permissions.RBAC_MANAGE)


class RoleUpsertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    permissions: list[str] = Field(default_factory=list, max_length=100)


class BindingUpsertRequest(BaseModel):
    subject_type: Literal["user", "sso_subject", "sso_group"]
    subject_id: str = Field(min_length=1, max_length=300)
    issuer: str = Field(default="", max_length=500)
    role_ids: list[str] = Field(min_length=1, max_length=50)
    enabled: bool = True
    description: str = Field(default="", max_length=500)


class SsoResolvePreviewRequest(BaseModel):
    issuer: str = Field(min_length=1, max_length=500)
    subject: str = Field(min_length=1, max_length=300)
    groups: list[str] = Field(default_factory=list, max_length=200)
    username: str = Field(default="", max_length=120)


def _validate_role_id(role_id: str) -> str:
    normalized = role_id.strip().lower()
    if not _ROLE_ID_RE.fullmatch(normalized):
        raise HTTPException(
            status_code=422,
            detail="role_id 仅允许小写字母、数字、点、下划线和连字符",
        )
    return normalized


async def _require_known_roles(role_ids: list[str]) -> list[str]:
    normalized = list(dict.fromkeys(_validate_role_id(value) for value in role_ids))
    existing = await rbac_dao.get_roles(get_db(), normalized)
    existing_ids = {str(role.get("role_id") or "") for role in existing}
    missing = [role_id for role_id in normalized if role_id not in existing_ids]
    if missing:
        raise HTTPException(status_code=422, detail=f"角色不存在: {', '.join(missing)}")
    return normalized


@router.get("/permissions")
async def list_permissions(
    _current: Annotated[User, Depends(_manage_rbac)],
) -> dict:
    return {"permissions": list(PERMISSION_CATALOG)}


@router.get("/roles")
async def list_roles(
    _current: Annotated[User, Depends(_manage_rbac)],
) -> dict:
    roles = await rbac_dao.list_roles(get_db())
    return {"roles": roles, "total": len(roles)}


@router.put("/roles/{role_id}")
async def upsert_role(
    role_id: str,
    body: RoleUpsertRequest,
    _current: Annotated[User, Depends(_manage_rbac)],
) -> dict:
    normalized_role_id = _validate_role_id(role_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="角色名称不能为空")
    try:
        permissions = validate_permissions(body.permissions)
        role = await rbac_dao.upsert_custom_role(
            get_db(),
            role_id=normalized_role_id,
            name=name,
            description=body.description,
            permissions=permissions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    get_authorization_service().invalidate()
    return {"status": "ok", "role": role}


@router.delete("/roles/{role_id}")
async def delete_role(
    role_id: str,
    _current: Annotated[User, Depends(_manage_rbac)],
) -> dict:
    try:
        deleted = await rbac_dao.delete_custom_role(
            get_db(), _validate_role_id(role_id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="角色不存在")
    get_authorization_service().invalidate()
    return {"status": "ok"}


@router.get("/bindings")
async def list_bindings(
    _current: Annotated[User, Depends(_manage_rbac)],
    subject_type: Literal["user", "sso_subject", "sso_group"] | None = None,
    issuer: str = "",
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict:
    bindings = await rbac_dao.list_bindings(
        get_db(),
        subject_type=subject_type or "",
        issuer=issuer.strip(),
        limit=limit,
    )
    return {"bindings": bindings, "total": len(bindings)}


@router.put("/bindings")
async def upsert_binding(
    body: BindingUpsertRequest,
    current_user: Annotated[User, Depends(_manage_rbac)],
) -> dict:
    subject_id = body.subject_id.strip()
    issuer = body.issuer.strip()
    if not subject_id:
        raise HTTPException(status_code=422, detail="subject_id 不能为空")
    if body.subject_type != "user" and not issuer:
        raise HTTPException(status_code=422, detail="SSO 绑定必须填写 issuer")
    if body.subject_type == "user":
        if not await users_dao.get_user(get_db(), subject_id):
            raise HTTPException(status_code=404, detail="本地用户不存在")
        issuer = ""
    role_ids = await _require_known_roles(body.role_ids)
    binding = await rbac_dao.upsert_binding(
        get_db(),
        subject_type=body.subject_type,
        subject_id=subject_id,
        issuer=issuer,
        role_ids=role_ids,
        enabled=body.enabled,
        description=body.description,
        updated_by=current_user.username,
    )
    get_authorization_service().invalidate()
    return {"status": "ok", "binding": binding}


@router.delete("/bindings/{binding_id}")
async def delete_binding(
    binding_id: str,
    _current: Annotated[User, Depends(_manage_rbac)],
) -> dict:
    deleted = await rbac_dao.delete_binding(get_db(), binding_id.strip())
    if not deleted:
        raise HTTPException(status_code=404, detail="授权绑定不存在")
    get_authorization_service().invalidate()
    return {"status": "ok"}


@router.post("/resolve-preview")
async def resolve_sso_preview(
    body: SsoResolvePreviewRequest,
    _current: Annotated[User, Depends(_manage_rbac)],
) -> dict:
    """Preview OIDC/SAML claim mapping without issuing a login token."""
    issuer = body.issuer.strip()
    subject = body.subject.strip()
    if not issuer or not subject:
        raise HTTPException(status_code=422, detail="issuer 和 subject 不能为空")
    identity = ExternalIdentity(
        issuer=issuer,
        subject=subject,
        groups=tuple(
            dict.fromkeys(group.strip() for group in body.groups if group.strip())
        ),
    )
    context = await get_authorization_service().resolve(
        get_db(),
        username=body.username.strip() or f"sso:{identity.subject}",
        legacy_role="user",
        auth_source="sso",
        external_identity=identity,
    )
    return {
        "mapped": bool(context.permissions),
        "roles": list(context.role_ids),
        "permissions": sorted(context.permissions),
        "binding_ids": list(context.binding_ids),
    }
