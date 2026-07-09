"""
API 配置模块
"""

from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """API 配置"""
    
    # JWT 配置 - 使用固定密钥（可通过 .env 覆盖）
    SECRET_KEY: str = "CHANGE_ME"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 小时
    
    # 服务配置
    HOST: str = "127.0.0.1"  # 只允许本地访问
    PORT: int = 8000
    # 优雅关闭超时（秒）：WS/SSE 长连接不会自然断开，超时后强制断开，
    # 避免 reload/停机卡在 "Waiting for connections to close"。
    GRACEFUL_SHUTDOWN_TIMEOUT: int = 5

    # 登录额外校验 key 默认值（实际值存储在 MongoDB）
    LOGIN_KEY: str = "accesskey"

    # MongoDB bootstrap connection. Runtime business config is stored in MongoDB;
    # these values only let the process connect to MongoDB before config is loaded.
    MONGODB_URI: str = "mongodb://127.0.0.1:27017"
    MONGODB_DATABASE: str = "Sere1nG0Fish"
    MONGODB_USERNAME: str | None = None
    MONGODB_PASSWORD: str | None = None
    MONGODB_AUTH_SOURCE: str = "admin"
    MONGODB_DIRECT: bool = False
    MONGODB_APPNAME: str = "Sere1nFishServer"
    MONGODB_MAX_POOL_SIZE: int = 200
    MONGODB_MIN_POOL_SIZE: int = 0
    MONGODB_MAX_IDLE_TIME_MS: int = 60000
    MONGODB_SERVER_SELECTION_TIMEOUT_MS: int = 5000
    MONGODB_CONNECT_TIMEOUT_MS: int = 10000
    
    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()
