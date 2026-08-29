"""Unified RBAC authorization and future SSO-claim role resolution."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import rbac as rbac_dao


class Permissions:
    SYSTEM_ADMIN = "system.admin"
    USERS_MANAGE = "users.manage"
    RBAC_MANAGE = "rbac.manage"
    CONFIG_MANAGE = "config.manage"
    PROJECTS_READ = "projects.read"
    PROJECTS_MANAGE = "projects.manage"
    SCANS_EXECUTE = "scans.execute"
    FINDINGS_READ = "findings.read"
    MOBILE_READ = "mobile.read"
    MOBILE_CONTROL = "mobile.control"
    AI_USE = "ai.use"
    ARTIFACTS_READ = "artifacts.read"
    OBSERVABILITY_READ = "observability.read"


PERMISSION_CATALOG: tuple[dict[str, str], ...] = (
    {"code": Permissions.SYSTEM_ADMIN, "name": "系统超级管理"},
    {"code": Permissions.USERS_MANAGE, "name": "用户管理"},
    {"code": Permissions.RBAC_MANAGE, "name": "角色与授权管理"},
    {"code": Permissions.CONFIG_MANAGE, "name": "系统配置管理"},
    {"code": Permissions.PROJECTS_READ, "name": "读取项目"},
    {"code": Permissions.PROJECTS_MANAGE, "name": "管理项目"},
    {"code": Permissions.SCANS_EXECUTE, "name": "执行扫描"},
    {"code": Permissions.FINDINGS_READ, "name": "读取 Finding"},
    {"code": Permissions.MOBILE_READ, "name": "读取手机状态"},
    {"code": Permissions.MOBILE_CONTROL, "name": "控制手机"},
    {"code": Permissions.AI_USE, "name": "使用 AI 中枢"},
    {"code": Permissions.ARTIFACTS_READ, "name": "读取产物"},
    {"code": Permissions.OBSERVABILITY_READ, "name": "读取观测数据"},
)

KNOWN_PERMISSIONS = frozenset(item["code"] for item in PERMISSION_CATALOG)


BUILTIN_ROLES: tuple[dict[str, Any], ...] = (
    {
        "role_id": "admin",
        "name": "系统管理员",
        "description": "完整系统权限；兼容原 admin 角色。",
        "permissions": ["*"],
    },
    {
        "role_id": "operator",
        "name": "业务操作员",
        "description": "兼容原 user 角色，可运行项目、扫描、手机和 AI 工作流。",
        "permissions": [
            Permissions.PROJECTS_READ,
            Permissions.PROJECTS_MANAGE,
            Permissions.SCANS_EXECUTE,
            Permissions.FINDINGS_READ,
            Permissions.MOBILE_READ,
            Permissions.MOBILE_CONTROL,
            Permissions.AI_USE,
            Permissions.ARTIFACTS_READ,
            Permissions.OBSERVABILITY_READ,
        ],
    },
    {
        "role_id": "analyst",
        "name": "分析员",
        "description": "读取项目与情报、使用 AI 和产物，不管理系统配置。",
        "permissions": [
            Permissions.PROJECTS_READ,
            Permissions.FINDINGS_READ,
            Permissions.MOBILE_READ,
            Permissions.AI_USE,
            Permissions.ARTIFACTS_READ,
            Permissions.OBSERVABILITY_READ,
        ],
    },
    {
        "role_id": "viewer",
        "name": "只读用户",
        "description": "只读项目、Finding、设备状态和产物。",
        "permissions": [
            Permissions.PROJECTS_READ,
            Permissions.FINDINGS_READ,
            Permissions.MOBILE_READ,
            Permissions.ARTIFACTS_READ,
        ],
    },
)


@dataclass(frozen=True)
class ExternalIdentity:
    """Provider-neutral identity claims emitted by a future OIDC/SAML adapter."""

    issuer: str
    subject: str
    groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuthorizationContext:
    role_ids: tuple[str, ...] = ()
    permissions: frozenset[str] = field(default_factory=frozenset)
    binding_ids: tuple[str, ...] = ()

    def allows(self, permission: str) -> bool:
        if (
            "*" in self.permissions
            or Permissions.SYSTEM_ADMIN in self.permissions
            or permission in self.permissions
        ):
            return True
        namespace = permission.partition(".")[0]
        return f"{namespace}.*" in self.permissions


def validate_permissions(values: list[str]) -> list[str]:
    normalized = list(
        dict.fromkeys(
            str(value).strip() for value in values if str(value).strip()
        )
    )
    invalid = [
        value
        for value in normalized
        if value != "*" and value not in KNOWN_PERMISSIONS
    ]
    if invalid:
        raise ValueError(f"未知权限代码: {', '.join(invalid)}")
    return normalized


class AuthorizationService:
    """Resolve roles once per short TTL while keeping revocation reasonably fresh."""

    def __init__(
        self,
        *,
        cache_ttl_seconds: float = 10.0,
        cache_max_entries: int = 4096,
    ) -> None:
        self.cache_ttl_seconds = max(0.0, float(cache_ttl_seconds))
        self.cache_max_entries = max(1, int(cache_max_entries))
        self._cache: dict[tuple[Any, ...], tuple[float, AuthorizationContext]] = {}
        self._lock = asyncio.Lock()
        self._generation = 0

    def invalidate(self) -> None:
        self._generation += 1
        self._cache.clear()

    @staticmethod
    def _cache_key(
        *,
        username: str,
        legacy_role: str,
        auth_source: str,
        external_identity: ExternalIdentity | None,
    ) -> tuple[Any, ...]:
        return (
            username,
            legacy_role,
            auth_source,
            external_identity.issuer if external_identity else "",
            external_identity.subject if external_identity else "",
            tuple(sorted(external_identity.groups)) if external_identity else (),
        )

    async def resolve(
        self,
        db: AsyncIOMotorDatabase,
        *,
        username: str,
        legacy_role: str = "user",
        auth_source: str = "local",
        external_identity: ExternalIdentity | None = None,
    ) -> AuthorizationContext:
        key = self._cache_key(
            username=username,
            legacy_role=legacy_role,
            auth_source=auth_source,
            external_identity=external_identity,
        )
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and cached[0] > now:
            return cached[1]

        async with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            generation = self._generation
            context = await self._resolve_uncached(
                db,
                username=username,
                legacy_role=legacy_role,
                auth_source=auth_source,
                external_identity=external_identity,
            )
            if self.cache_ttl_seconds > 0 and generation == self._generation:
                expires_at = time.monotonic() + self.cache_ttl_seconds
                if len(self._cache) >= self.cache_max_entries:
                    expired_keys = [
                        cache_key
                        for cache_key, cached_value in self._cache.items()
                        if cached_value[0] <= time.monotonic()
                    ]
                    for cache_key in expired_keys:
                        self._cache.pop(cache_key, None)
                if len(self._cache) >= self.cache_max_entries:
                    oldest_key = min(
                        self._cache,
                        key=lambda cache_key: self._cache[cache_key][0],
                    )
                    self._cache.pop(oldest_key, None)
                self._cache[key] = (expires_at, context)
            return context

    @staticmethod
    async def _resolve_uncached(
        db: AsyncIOMotorDatabase,
        *,
        username: str,
        legacy_role: str,
        auth_source: str,
        external_identity: ExternalIdentity | None,
    ) -> AuthorizationContext:
        subjects = [
            {"subject_type": "user", "issuer": "", "subject_id": username}
        ]
        if external_identity:
            subjects.append(
                {
                    "subject_type": "sso_subject",
                    "issuer": external_identity.issuer,
                    "subject_id": external_identity.subject,
                }
            )
            subjects.extend(
                {
                    "subject_type": "sso_group",
                    "issuer": external_identity.issuer,
                    "subject_id": group,
                }
                for group in external_identity.groups
                if group
            )
        matched_bindings = await rbac_dao.find_subject_bindings(db, subjects)
        direct_user_binding = any(
            binding.get("subject_type") == "user" for binding in matched_bindings
        )
        bindings = [
            binding
            for binding in matched_bindings
            if bool(binding.get("enabled", True))
        ]
        role_ids = {
            str(role_id).strip()
            for binding in bindings
            for role_id in binding.get("role_ids") or []
            if str(role_id).strip()
        }

        # Existing local accounts retain their old behavior until an explicit
        # user binding is created. A disabled explicit binding still suppresses
        # that fallback, and SSO accounts fail closed when unmapped.
        if legacy_role == "admin":
            role_ids.add("admin")
        elif (
            auth_source == "local"
            and external_identity is None
            and not direct_user_binding
        ):
            role_ids.add("operator")

        role_docs = await rbac_dao.get_roles(db, sorted(role_ids))
        permissions = frozenset(
            str(permission).strip()
            for role in role_docs
            for permission in role.get("permissions") or []
            if str(permission).strip()
        )
        return AuthorizationContext(
            role_ids=tuple(sorted(role_ids)),
            permissions=permissions,
            binding_ids=tuple(
                sorted(
                    str(binding.get("binding_id") or "").strip()
                    for binding in bindings
                    if str(binding.get("binding_id") or "").strip()
                )
            ),
        )


_service = AuthorizationService()


def get_authorization_service() -> AuthorizationService:
    return _service


async def initialize_authorization(db: AsyncIOMotorDatabase) -> None:
    await rbac_dao.ensure_indexes(db)
    await rbac_dao.seed_builtin_roles(db, [dict(role) for role in BUILTIN_ROLES])
    _service.invalidate()
