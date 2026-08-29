from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_legacy_roles_resolve_without_explicit_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import rbac as rbac_dao
    from api.services.authorization import AuthorizationService, BUILTIN_ROLES

    role_by_id = {role["role_id"]: role for role in BUILTIN_ROLES}

    async def no_bindings(_db: Any, _subjects: list[dict[str, str]]) -> list[dict]:
        return []

    async def roles(_db: Any, role_ids: list[str]) -> list[dict]:
        return [dict(role_by_id[role_id]) for role_id in role_ids]

    monkeypatch.setattr(rbac_dao, "find_subject_bindings", no_bindings)
    monkeypatch.setattr(rbac_dao, "get_roles", roles)
    service = AuthorizationService(cache_ttl_seconds=0)

    operator = await service.resolve(
        object(), username="alice", legacy_role="user", auth_source="local"
    )
    admin = await service.resolve(
        object(), username="admin", legacy_role="admin", auth_source="local"
    )

    assert operator.role_ids == ("operator",)
    assert operator.allows("scans.execute")
    assert not operator.allows("config.manage")
    assert admin.role_ids == ("admin",)
    assert admin.allows("anything.future")


@pytest.mark.asyncio
async def test_explicit_user_binding_replaces_legacy_operator_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import rbac as rbac_dao
    from api.services.authorization import AuthorizationService, BUILTIN_ROLES

    role_by_id = {role["role_id"]: role for role in BUILTIN_ROLES}

    async def bindings(_db: Any, _subjects: list[dict[str, str]]) -> list[dict]:
        return [
            {
                "binding_id": "binding-viewer",
                "subject_type": "user",
                "subject_id": "alice",
                "role_ids": ["viewer"],
            }
        ]

    async def roles(_db: Any, role_ids: list[str]) -> list[dict]:
        return [dict(role_by_id[role_id]) for role_id in role_ids]

    monkeypatch.setattr(rbac_dao, "find_subject_bindings", bindings)
    monkeypatch.setattr(rbac_dao, "get_roles", roles)
    context = await AuthorizationService(cache_ttl_seconds=0).resolve(
        object(), username="alice", legacy_role="user", auth_source="local"
    )

    assert context.role_ids == ("viewer",)
    assert context.allows("projects.read")
    assert not context.allows("scans.execute")


@pytest.mark.asyncio
async def test_disabled_explicit_user_binding_does_not_restore_legacy_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import rbac as rbac_dao
    from api.services.authorization import AuthorizationService

    async def bindings(_db: Any, _subjects: list[dict[str, str]]) -> list[dict]:
        return [
            {
                "binding_id": "binding-disabled",
                "subject_type": "user",
                "subject_id": "alice",
                "role_ids": ["operator"],
                "enabled": False,
            }
        ]

    async def roles(_db: Any, _role_ids: list[str]) -> list[dict]:
        return []

    monkeypatch.setattr(rbac_dao, "find_subject_bindings", bindings)
    monkeypatch.setattr(rbac_dao, "get_roles", roles)
    context = await AuthorizationService(cache_ttl_seconds=0).resolve(
        object(), username="alice", legacy_role="user", auth_source="local"
    )

    assert context.role_ids == ()
    assert not context.permissions


@pytest.mark.asyncio
async def test_sso_group_claim_maps_to_role_and_unmapped_identity_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import rbac as rbac_dao
    from api.services.authorization import (
        AuthorizationService,
        BUILTIN_ROLES,
        ExternalIdentity,
    )

    role_by_id = {role["role_id"]: role for role in BUILTIN_ROLES}

    async def bindings(_db: Any, subjects: list[dict[str, str]]) -> list[dict]:
        if any(
            subject["subject_type"] == "sso_group"
            and subject["subject_id"] == "security-team"
            for subject in subjects
        ):
            return [
                {
                    "binding_id": "binding-security",
                    "subject_type": "sso_group",
                    "subject_id": "security-team",
                    "role_ids": ["analyst"],
                }
            ]
        return []

    async def roles(_db: Any, role_ids: list[str]) -> list[dict]:
        return [dict(role_by_id[role_id]) for role_id in role_ids]

    monkeypatch.setattr(rbac_dao, "find_subject_bindings", bindings)
    monkeypatch.setattr(rbac_dao, "get_roles", roles)
    service = AuthorizationService(cache_ttl_seconds=0)
    mapped = await service.resolve(
        object(),
        username="sso:alice",
        auth_source="sso",
        external_identity=ExternalIdentity(
            issuer="https://sso.example.com",
            subject="alice-id",
            groups=("security-team",),
        ),
    )
    unmapped = await service.resolve(
        object(),
        username="sso:bob",
        auth_source="sso",
        external_identity=ExternalIdentity(
            issuer="https://sso.example.com",
            subject="bob-id",
        ),
    )

    assert mapped.role_ids == ("analyst",)
    assert mapped.allows("ai.use")
    assert unmapped.role_ids == ()
    assert not unmapped.permissions


@pytest.mark.asyncio
async def test_external_identity_never_uses_local_operator_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import rbac as rbac_dao
    from api.services.authorization import AuthorizationService, ExternalIdentity

    async def no_bindings(_db: Any, _subjects: list[dict[str, str]]) -> list[dict]:
        return []

    async def no_roles(_db: Any, _role_ids: list[str]) -> list[dict]:
        return []

    monkeypatch.setattr(rbac_dao, "find_subject_bindings", no_bindings)
    monkeypatch.setattr(rbac_dao, "get_roles", no_roles)
    context = await AuthorizationService(cache_ttl_seconds=0).resolve(
        object(),
        username="linked-user",
        legacy_role="user",
        auth_source="local",
        external_identity=ExternalIdentity(
            issuer="https://sso.example.com",
            subject="linked-subject",
        ),
    )

    assert context.role_ids == ()
    assert not context.permissions


def test_permission_dependency_uses_resolved_permissions() -> None:
    from api.auth import User, require_permission

    checker = require_permission("config.manage")
    assert checker(User(username="ops", permission_codes=["config.manage"])).username == "ops"
    with pytest.raises(HTTPException) as exc_info:
        checker(User(username="viewer", permission_codes=["projects.read"]))
    assert exc_info.value.status_code == 403
    assert checker(User(username="root", permission_codes=["system.admin"]))


@pytest.mark.asyncio
async def test_active_user_rejects_unmapped_identity() -> None:
    from api.auth import User, get_current_active_user

    with pytest.raises(HTTPException) as exc_info:
        await get_current_active_user(User(username="unmapped", auth_source="sso"))
    assert exc_info.value.status_code == 403

    active = User(username="viewer", permission_codes=["projects.read"])
    assert await get_current_active_user(active) == active


def test_rbac_binding_identity_is_stable_and_issuer_scoped() -> None:
    from api.dao.rbac import binding_id

    first = binding_id(
        subject_type="sso_group",
        issuer="https://sso-a.example.com",
        subject_id="security-team",
    )
    assert first == binding_id(
        subject_type="sso_group",
        issuer="https://sso-a.example.com",
        subject_id="security-team",
    )
    assert first != binding_id(
        subject_type="sso_group",
        issuer="https://sso-b.example.com",
        subject_id="security-team",
    )
