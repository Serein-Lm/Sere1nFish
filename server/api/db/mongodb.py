from __future__ import annotations

import asyncio
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase


_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


def init_mongo(app_config: Any | None = None, *, force: bool = False) -> None:
    """初始化全局 Motor 客户端。

    幂等：多个路由在 import 期重复调用时只建立一次连接，避免重复创建
    AsyncIOMotorClient 导致连接池泄漏。传 force=True 可强制重建。
    """
    global _client, _db

    if _client is not None and not force:
        return

    mongodb = getattr(app_config, "mongodb", None) if app_config is not None else None
    if mongodb is None:
        from api.config import get_settings

        settings = get_settings()
        uri = settings.MONGODB_URI
        username = settings.MONGODB_USERNAME
        password = settings.MONGODB_PASSWORD
        auth_source = settings.MONGODB_AUTH_SOURCE
        database_name = settings.MONGODB_DATABASE
        direct = settings.MONGODB_DIRECT
        appname = settings.MONGODB_APPNAME
        max_pool_size = settings.MONGODB_MAX_POOL_SIZE
        min_pool_size = settings.MONGODB_MIN_POOL_SIZE
        max_idle_time_ms = settings.MONGODB_MAX_IDLE_TIME_MS
        server_selection_timeout_ms = settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS
        connect_timeout_ms = settings.MONGODB_CONNECT_TIMEOUT_MS
    else:
        uri = getattr(mongodb, "uri", None)
        username = getattr(mongodb, "username", None)
        password = getattr(mongodb, "password", None)
        auth_source = getattr(mongodb, "auth_source", None) or "admin"
        database_name = getattr(mongodb, "database_name", None)
        direct = bool(getattr(mongodb, "direct", False))
        appname = getattr(mongodb, "appname", None) or "Sere1nFishServer"
        max_pool_size = int(getattr(mongodb, "max_pool_size", 0) or 200)
        min_pool_size = int(getattr(mongodb, "min_pool_size", 0) or 0)
        max_idle_time_ms = int(getattr(mongodb, "max_idle_time_ms", 0) or 60000)
        server_selection_timeout_ms = int(getattr(mongodb, "server_selection_timeout_ms", 0) or 5000)
        connect_timeout_ms = int(getattr(mongodb, "connect_timeout_ms", 0) or 10000)

    if not uri or not database_name:
        raise ValueError("mongodb.uri / mongodb.database_name 不能为空")

    # 连接池 / 超时调优（可经 config 覆盖，给出健壮默认值）。
    # 注意：不设置 socketTimeoutMS，避免长查询/聚合被误杀。
    kwargs: dict[str, Any] = {
        "authSource": auth_source,
        "appname": appname,
        "maxPoolSize": max_pool_size,
        "minPoolSize": min_pool_size,
        "maxIdleTimeMS": max_idle_time_ms,
        "serverSelectionTimeoutMS": server_selection_timeout_ms,
        "connectTimeoutMS": connect_timeout_ms,
    }

    if direct:
        kwargs["directConnection"] = True

    if username:
        kwargs["username"] = username
    if password:
        kwargs["password"] = password

    # 重建时先关闭旧连接
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass

    _client = AsyncIOMotorClient(uri, **kwargs)
    _db = _client[database_name]


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("MongoDB 未初始化：请先调用 init_mongo(app_config)")
    return _db


def get_io_loop() -> Any | None:
    """返回全局 Motor 客户端绑定的事件循环（未初始化时返回 None）。

    供同步工具封装把 DB 协程调度回拥有连接池的事件循环，
    避免在其它线程/新循环里操作 Motor 触发 "attached to a different loop"。
    """
    if _client is None:
        return None
    try:
        return _client.get_io_loop()
    except Exception:
        return None


def close_mongo() -> None:
    """关闭全局 Motor 客户端（应用关闭时调用）。"""
    global _client, _db
    if _client is not None:
        try:
            _client.close()
        finally:
            _client = None
            _db = None


async def ping() -> dict[str, Any]:
    db = get_db()
    result = await db.command("ping")
    return dict(result)


async def health_check(timeout: float = 2.0) -> dict[str, Any]:
    """带超时的连通性探测，供 /health 使用，不抛异常。"""
    if _db is None:
        return {"ok": False, "error": "未初始化"}
    try:
        await asyncio.wait_for(_db.command("ping"), timeout=timeout)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001 — 健康检查需吞掉所有异常
        return {"ok": False, "error": str(e)}
