"""
Redis Cookie 存储

用于存储和管理小红书登录 Cookie
"""
import json
import os
from typing import Any, Optional

import redis


class RedisCookieStore:
    """Redis Cookie 存储管理器"""
    
    # Redis Key 前缀
    KEY_PREFIX = "xhs:cookie:"
    ACTIVE_KEY = "xhs:cookie:active"
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        username: Optional[str] = None,
        password: Optional[str] = None,
        db: int = 0,
    ):
        """
        初始化 Redis 连接
        
        Args:
            host: Redis 主机地址
            port: Redis 端口
            username: Redis 用户名
            password: Redis 密码
            db: Redis 数据库编号
        """
        self.redis = redis.Redis(
            host=host,
            port=port,
            username=username,
            password=password,
            db=db,
            decode_responses=True,
        )
    
    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "RedisCookieStore":
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
    
    def _key(self, account_name: str) -> str:
        """生成 Cookie Key"""
        return f"{self.KEY_PREFIX}{account_name}"
    
    # ==================== Cookie CRUD ====================
    
    def save_cookie(
        self,
        account_name: str,
        cookie_string: str,
        is_valid: Optional[bool] = None,
        ttl: Optional[int] = None,  # 过期时间（秒），None 表示永不过期
    ) -> bool:
        """
        保存 Cookie
        
        Args:
            account_name: 账号名
            cookie_string: Cookie 字符串
            is_valid: 是否有效
            ttl: 过期时间（秒）
            
        Returns:
            是否成功
        """
        key = self._key(account_name)
        data = {
            "account_name": account_name,
            "cookie_string": cookie_string,
            "is_valid": is_valid,
        }
        
        try:
            self.redis.hset(key, mapping=data)
            if ttl:
                self.redis.expire(key, ttl)
            return True
        except Exception:
            return False
    
    def get_cookie(self, account_name: str) -> Optional[dict[str, Any]]:
        """获取 Cookie"""
        key = self._key(account_name)
        data = self.redis.hgetall(key)
        
        if not data:
            return None
        
        # 转换类型
        return {
            "account_name": data.get("account_name"),
            "cookie_string": data.get("cookie_string"),
            "is_valid": data.get("is_valid") == "True" if data.get("is_valid") else None,
        }
    
    def get_cookie_string(self, account_name: str) -> Optional[str]:
        """直接获取 Cookie 字符串"""
        key = self._key(account_name)
        return self.redis.hget(key, "cookie_string")
    
    def delete_cookie(self, account_name: str) -> bool:
        """删除 Cookie"""
        key = self._key(account_name)
        return self.redis.delete(key) > 0
    
    def list_cookies(self) -> list[dict[str, Any]]:
        """列出所有 Cookie"""
        pattern = f"{self.KEY_PREFIX}*"
        keys = self.redis.keys(pattern)
        
        # 排除 active key
        keys = [k for k in keys if k != self.ACTIVE_KEY]
        
        cookies = []
        for key in keys:
            data = self.redis.hgetall(key)
            if data:
                cookies.append({
                    "account_name": data.get("account_name"),
                    "cookie_string": data.get("cookie_string"),
                    "is_valid": data.get("is_valid") == "True" if data.get("is_valid") else None,
                    "is_active": self.get_active_account() == data.get("account_name"),
                })
        
        return cookies
    
    def set_valid(self, account_name: str, is_valid: bool) -> bool:
        """设置 Cookie 有效性"""
        key = self._key(account_name)
        if not self.redis.exists(key):
            return False
        self.redis.hset(key, "is_valid", str(is_valid))
        return True
    
    # ==================== 激活账号管理 ====================
    
    def activate(self, account_name: str) -> bool:
        """激活账号"""
        key = self._key(account_name)
        if not self.redis.exists(key):
            return False
        self.redis.set(self.ACTIVE_KEY, account_name)
        return True
    
    def get_active_account(self) -> Optional[str]:
        """获取激活的账号名"""
        return self.redis.get(self.ACTIVE_KEY)
    
    def get_active_cookie(self) -> Optional[dict[str, Any]]:
        """获取激活账号的 Cookie"""
        active_account = self.get_active_account()
        if not active_account:
            return None
        return self.get_cookie(active_account)
    
    def get_active_cookie_string(self) -> Optional[str]:
        """获取激活账号的 Cookie 字符串"""
        active_account = self.get_active_account()
        if not active_account:
            return None
        return self.get_cookie_string(active_account)
    
    # ==================== 工具方法 ====================
    
    def ping(self) -> bool:
        """检查 Redis 连接"""
        try:
            return self.redis.ping()
        except Exception:
            return False
    
    def clear_all(self) -> int:
        """清除所有 Cookie（危险操作）"""
        pattern = f"{self.KEY_PREFIX}*"
        keys = self.redis.keys(pattern)
        if keys:
            return self.redis.delete(*keys)
        return 0


# 全局实例
_cookie_store: Optional[RedisCookieStore] = None


def init_cookie_store(config: dict[str, Any]) -> RedisCookieStore:
    """初始化 Cookie 存储"""
    global _cookie_store
    _cookie_store = RedisCookieStore.from_config(config)
    return _cookie_store


def get_cookie_store() -> RedisCookieStore:
    """获取 Cookie 存储实例"""
    if _cookie_store is None:
        raise RuntimeError("Cookie store not initialized. Call init_cookie_store() first.")
    return _cookie_store
