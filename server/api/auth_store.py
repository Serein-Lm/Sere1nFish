from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from typing import Optional

import redis


class RedisTokenStore:
    """基于 Redis 的 Token 存储。

    特性：
    - 服务重启后 token 仍然有效
    - 多进程/多副本部署时共享 token
    - 自动过期清理
    """

    KEY_PREFIX = "auth:token:"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        username: Optional[str] = None,
        password: Optional[str] = None,
        db: int = 0,
    ):
        self.redis = redis.Redis(
            host=host,
            port=port,
            username=username,
            password=password,
            db=db,
            decode_responses=True,
        )

    @classmethod
    def from_config(cls, config: dict) -> "RedisTokenStore":
        """从配置创建实例"""
        redis_config = config.get("redis", {})
        addr = os.getenv("REDIS_ADDR") or redis_config.get("addr", "localhost:6379")
        host, port = addr.split(":") if ":" in addr else (addr, "6379")
        return cls(
            host=host,
            port=int(port),
            username=os.getenv("REDIS_USERNAME") or redis_config.get("username"),
            password=os.getenv("REDIS_PASSWORD") or redis_config.get("password"),
            db=int(os.getenv("REDIS_DB") or redis_config.get("db", 0)),
        )

    def _key(self, token: str) -> str:
        return f"{self.KEY_PREFIX}{token}"

    def issue(self, username: str, expires_at: datetime) -> str:
        """签发 token"""
        token = secrets.token_urlsafe(48)
        key = self._key(token)
        
        # 计算 TTL（秒）
        now = datetime.now(timezone.utc)
        ttl = int((expires_at - now).total_seconds())
        if ttl <= 0:
            ttl = 1  # 至少 1 秒
        
        # 存储 token 数据
        data = {
            "username": username,
            "issued_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "revoked": "false",
        }
        self.redis.hset(key, mapping=data)
        self.redis.expire(key, ttl)  # 自动过期
        
        return token

    def revoke(self, token: str) -> None:
        """撤销 token"""
        key = self._key(token)
        if self.redis.exists(key):
            self.redis.hset(key, "revoked", "true")

    def get_username(self, token: str) -> Optional[str]:
        """获取 token 对应的用户名"""
        key = self._key(token)
        data = self.redis.hgetall(key)
        
        if not data:
            return None
        if data.get("revoked") == "true":
            return None
        
        # 检查过期（Redis TTL 已经处理，这里双重检查）
        expires_at_str = data.get("expires_at")
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str)
            if datetime.now(timezone.utc) >= expires_at:
                return None
        
        return data.get("username")

    def cleanup(self) -> None:
        """清理过期 token（Redis TTL 自动处理，此方法可选）"""
        pass  # Redis TTL 自动过期


def _init_token_store() -> RedisTokenStore:
    """Initialize token store from environment bootstrap settings."""
    return RedisTokenStore.from_config({})


TOKEN_STORE = _init_token_store()
