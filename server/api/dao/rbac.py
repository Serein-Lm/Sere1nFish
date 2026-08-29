"""RBAC role and subject-binding persistence."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import RBAC_BINDINGS_COLLECTION, RBAC_ROLES_COLLECTION


def _now() -> datetime:
    return datetime.now(timezone.utc)


def binding_id(*, subject_type: str, subject_id: str, issuer: str = "") -> str:
    raw = f"{subject_type}:{issuer.strip()}:{subject_id.strip()}".encode("utf-8")
    return "rbb_" + hashlib.sha256(raw).hexdigest()[:24]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    roles = db[RBAC_ROLES_COLLECTION]
    bindings = db[RBAC_BINDINGS_COLLECTION]
    await roles.create_index("role_id", unique=True)
    await roles.create_index([("builtin", 1), ("name", 1)])
    await bindings.create_index("binding_id", unique=True)
    await bindings.create_index(
        [("subject_type", 1), ("issuer", 1), ("subject_id", 1)],
        unique=True,
    )
    await bindings.create_index("role_ids")
    await bindings.create_index([("enabled", 1), ("updated_at", -1)])


async def seed_builtin_roles(
    db: AsyncIOMotorDatabase,
    roles: list[dict[str, Any]],
) -> None:
    now = _now()
    for role in roles:
        role_id = str(role["role_id"])
        await db[RBAC_ROLES_COLLECTION].update_one(
            {"role_id": role_id},
            {
                "$set": {
                    **role,
                    "builtin": True,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )


async def list_roles(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    return await (
        db[RBAC_ROLES_COLLECTION]
        .find({}, {"_id": 0})
        .sort([("builtin", -1), ("role_id", 1)])
        .to_list(None)
    )


async def get_roles(
    db: AsyncIOMotorDatabase,
    role_ids: list[str],
) -> list[dict[str, Any]]:
    if not role_ids:
        return []
    return await db[RBAC_ROLES_COLLECTION].find(
        {"role_id": {"$in": list(dict.fromkeys(role_ids))}},
        {"_id": 0},
    ).to_list(None)


async def get_role(db: AsyncIOMotorDatabase, role_id: str) -> dict[str, Any] | None:
    return await db[RBAC_ROLES_COLLECTION].find_one(
        {"role_id": role_id},
        {"_id": 0},
    )


async def upsert_custom_role(
    db: AsyncIOMotorDatabase,
    *,
    role_id: str,
    name: str,
    description: str,
    permissions: list[str],
) -> dict[str, Any]:
    existing = await get_role(db, role_id)
    if existing and existing.get("builtin"):
        raise ValueError("内置角色不能通过自定义角色接口覆盖")
    now = _now()
    await db[RBAC_ROLES_COLLECTION].update_one(
        {"role_id": role_id},
        {
            "$set": {
                "role_id": role_id,
                "name": name,
                "description": description,
                "permissions": list(dict.fromkeys(permissions)),
                "builtin": False,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return (await get_role(db, role_id)) or {}


async def delete_custom_role(db: AsyncIOMotorDatabase, role_id: str) -> bool:
    role = await get_role(db, role_id)
    if not role:
        return False
    if role.get("builtin"):
        raise ValueError("内置角色不能删除")
    in_use = await db[RBAC_BINDINGS_COLLECTION].find_one(
        {"role_ids": role_id, "enabled": True},
        {"_id": 1},
    )
    if in_use:
        raise ValueError("角色仍被授权绑定引用，不能删除")
    result = await db[RBAC_ROLES_COLLECTION].delete_one({"role_id": role_id})
    return result.deleted_count > 0


async def list_bindings(
    db: AsyncIOMotorDatabase,
    *,
    subject_type: str = "",
    issuer: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if subject_type:
        query["subject_type"] = subject_type
    if issuer:
        query["issuer"] = issuer
    return await (
        db[RBAC_BINDINGS_COLLECTION]
        .find(query, {"_id": 0})
        .sort("updated_at", -1)
        .limit(max(1, min(int(limit or 500), 2000)))
        .to_list(None)
    )


async def find_subject_bindings(
    db: AsyncIOMotorDatabase,
    subjects: list[dict[str, str]],
) -> list[dict[str, Any]]:
    clauses = [
        {
            "subject_type": str(item.get("subject_type") or ""),
            "issuer": str(item.get("issuer") or ""),
            "subject_id": str(item.get("subject_id") or ""),
        }
        for item in subjects
        if item.get("subject_type") and item.get("subject_id")
    ]
    if not clauses:
        return []
    return await db[RBAC_BINDINGS_COLLECTION].find(
        {"$or": clauses},
        {"_id": 0},
    ).to_list(None)


async def upsert_binding(
    db: AsyncIOMotorDatabase,
    *,
    subject_type: str,
    subject_id: str,
    issuer: str,
    role_ids: list[str],
    enabled: bool,
    description: str = "",
    updated_by: str = "",
) -> dict[str, Any]:
    normalized_issuer = issuer.strip() if subject_type != "user" else ""
    stable_id = binding_id(
        subject_type=subject_type,
        subject_id=subject_id,
        issuer=normalized_issuer,
    )
    now = _now()
    await db[RBAC_BINDINGS_COLLECTION].update_one(
        {"binding_id": stable_id},
        {
            "$set": {
                "binding_id": stable_id,
                "subject_type": subject_type,
                "subject_id": subject_id.strip(),
                "issuer": normalized_issuer,
                "role_ids": list(dict.fromkeys(role_ids)),
                "enabled": bool(enabled),
                "description": description.strip(),
                "updated_by": updated_by,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return (
        await db[RBAC_BINDINGS_COLLECTION].find_one(
            {"binding_id": stable_id},
            {"_id": 0},
        )
        or {}
    )


async def delete_binding(db: AsyncIOMotorDatabase, binding_id_value: str) -> bool:
    result = await db[RBAC_BINDINGS_COLLECTION].delete_one(
        {"binding_id": binding_id_value}
    )
    return result.deleted_count > 0
