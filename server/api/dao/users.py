"""
用户管理 DAO - MongoDB 持久化
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import bcrypt
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import USERS_COLLECTION, SYSTEM_CONFIG_COLLECTION
from api.utils.config_crypto import decrypt_value, encrypt_value


# ============ 用户管理 ============

async def get_user(db: AsyncIOMotorDatabase, username: str) -> Optional[dict]:
    """获取用户"""
    return await db[USERS_COLLECTION].find_one({"username": username})


async def list_users(db: AsyncIOMotorDatabase) -> list[dict]:
    """列出所有用户"""
    cursor = db[USERS_COLLECTION].find({}, {"hashed_password": 0})  # 不返回密码
    return await cursor.to_list(length=1000)


async def create_user(
    db: AsyncIOMotorDatabase,
    username: str,
    password: str,
    role: str = "user",
) -> dict:
    """创建用户"""
    # 检查用户是否已存在
    existing = await get_user(db, username)
    if existing:
        raise ValueError(f"用户 {username} 已存在")
    
    hashed_password = bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(rounds=12)
    ).decode("utf-8")
    
    now = datetime.now(timezone.utc)
    user_doc = {
        "username": username,
        "hashed_password": hashed_password,
        "role": role,
        "disabled": False,
        "created_at": now,
        "updated_at": now,
    }
    
    await db[USERS_COLLECTION].insert_one(user_doc)
    return user_doc


async def update_user(
    db: AsyncIOMotorDatabase,
    username: str,
    new_username: Optional[str] = None,
    password: Optional[str] = None,
    role: Optional[str] = None,
    disabled: Optional[bool] = None,
) -> dict:
    """更新用户"""
    user = await get_user(db, username)
    if not user:
        raise ValueError(f"用户 {username} 不存在")
    
    update_fields = {"updated_at": datetime.now(timezone.utc)}
    
    if password is not None:
        update_fields["hashed_password"] = bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt(rounds=12)
        ).decode("utf-8")
    
    if role is not None:
        update_fields["role"] = role
    
    if disabled is not None:
        update_fields["disabled"] = disabled
    
    # 修改用户名
    if new_username is not None and new_username != username:
        existing = await get_user(db, new_username)
        if existing:
            raise ValueError(f"用户名 {new_username} 已存在")
        if username == "admin":
            raise ValueError("不能修改默认管理员的用户名")
        update_fields["username"] = new_username
    
    await db[USERS_COLLECTION].update_one(
        {"username": username},
        {"$set": update_fields}
    )
    
    # 返回更新后的用户
    final_username = new_username if new_username else username
    return await get_user(db, final_username)


async def delete_user(db: AsyncIOMotorDatabase, username: str) -> bool:
    """删除用户"""
    if username == "admin":
        raise ValueError("不能删除默认管理员账户")
    
    result = await db[USERS_COLLECTION].delete_one({"username": username})
    if result.deleted_count == 0:
        raise ValueError(f"用户 {username} 不存在")
    return True


async def verify_password(db: AsyncIOMotorDatabase, username: str, password: str) -> bool:
    """验证密码"""
    user = await get_user(db, username)
    if not user:
        return False
    
    return bcrypt.checkpw(
        password.encode("utf-8"),
        user["hashed_password"].encode("utf-8")
    )


async def change_password(
    db: AsyncIOMotorDatabase,
    username: str,
    old_password: str,
    new_password: str,
) -> bool:
    """修改密码（需验证原密码）"""
    if not await verify_password(db, username, old_password):
        raise ValueError("原密码错误")
    
    await update_user(db, username, password=new_password)
    return True


# ============ 初始化默认用户 ============

async def ensure_default_users(db: AsyncIOMotorDatabase) -> None:
    """检查是否有用户存在（仅用于日志提示）"""
    # 数据库中已有用户，无需自动创建
    pass


# ============ 系统配置（Login Key 等） ============

CONFIG_KEY_LOGIN_KEY = "login_key"
CONFIG_KEY_CONFIG_REVEAL_PASSWORD = "config_reveal_password"


async def get_login_key(db: AsyncIOMotorDatabase) -> str:
    """获取登录 Key"""
    query = {"category": "auth", "key": CONFIG_KEY_LOGIN_KEY}
    doc = await db[SYSTEM_CONFIG_COLLECTION].find_one(query)
    if doc:
        value = decrypt_value(doc.get("value"))
        return value or "accesskey"
    legacy = await db[SYSTEM_CONFIG_COLLECTION].find_one({
        "key": CONFIG_KEY_LOGIN_KEY,
        "category": {"$exists": False},
    })
    if legacy:
        value = decrypt_value(legacy.get("value")) or "accesskey"
        await set_login_key(db, value)
        await db[SYSTEM_CONFIG_COLLECTION].delete_one({"_id": legacy["_id"]})
        return value
    return "accesskey"  # 默认值


async def set_login_key(db: AsyncIOMotorDatabase, new_key: str) -> None:
    """设置登录 Key"""
    await db[SYSTEM_CONFIG_COLLECTION].update_one(
        {"category": "auth", "key": CONFIG_KEY_LOGIN_KEY},
        {
            "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            "$set": {
                "category": "auth",
                "key": CONFIG_KEY_LOGIN_KEY,
                "value": encrypt_value(new_key),
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )


async def has_config_reveal_password(db: AsyncIOMotorDatabase) -> bool:
    """配置明文查看二级密码是否已设置。"""
    doc = await db[SYSTEM_CONFIG_COLLECTION].find_one(
        {"category": "auth", "key": CONFIG_KEY_CONFIG_REVEAL_PASSWORD}
    )
    return bool(doc and decrypt_value(doc.get("value")))


async def verify_config_reveal_password(
    db: AsyncIOMotorDatabase,
    password: str,
    *,
    fallback_username: str | None = None,
) -> bool:
    """验证配置明文查看二级密码。

    若尚未设置二级密码，允许管理员用自己的登录密码作为首次兜底验证。
    """
    doc = await db[SYSTEM_CONFIG_COLLECTION].find_one(
        {"category": "auth", "key": CONFIG_KEY_CONFIG_REVEAL_PASSWORD}
    )
    hashed = decrypt_value(doc.get("value")) if doc else None
    if hashed:
        return bcrypt.checkpw(password.encode("utf-8"), str(hashed).encode("utf-8"))
    if fallback_username:
        return await verify_password(db, fallback_username, password)
    return False


async def set_config_reveal_password(db: AsyncIOMotorDatabase, new_password: str) -> None:
    """设置配置明文查看二级密码。"""
    hashed = bcrypt.hashpw(
        new_password.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("utf-8")
    await db[SYSTEM_CONFIG_COLLECTION].update_one(
        {"category": "auth", "key": CONFIG_KEY_CONFIG_REVEAL_PASSWORD},
        {
            "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            "$set": {
                "category": "auth",
                "key": CONFIG_KEY_CONFIG_REVEAL_PASSWORD,
                "value": encrypt_value(hashed),
                "updated_at": datetime.now(timezone.utc),
            },
        },
        upsert=True,
    )


async def ensure_default_config(db: AsyncIOMotorDatabase) -> None:
    """检查配置是否存在（仅用于日志提示）"""
    # 数据库中已有配置，无需自动创建
    pass
