"""认证模块，用户由 MongoDB 持久化，授权交给统一 RBAC 服务。"""

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import bcrypt
from pydantic import BaseModel, Field

from api.config import get_settings
from api.auth_store import TOKEN_STORE

settings = get_settings()
# tokenUrl 指向真实登录前缀（路由挂载于 /api/v1/auth）；
# 用作 Bearer 提取与 Swagger Authorize 入口。
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=True)


# ============ 角色枚举 ============

class UserRole(str, Enum):
    """用户角色"""
    USER = "user"      # 普通用户
    ADMIN = "admin"    # 管理员


# ============ 数据模型 ============

class Token(BaseModel):
    """Token 响应"""
    access_token: str
    token_type: str = "bearer"
    server_token: str | None = None


class TokenData(BaseModel):
    """Token 数据"""
    username: str | None = None


class User(BaseModel):
    """用户"""
    username: str
    role: UserRole = UserRole.USER
    disabled: bool = False
    roles: list[str] = Field(default_factory=list)
    permission_codes: list[str] = Field(default_factory=list)
    auth_source: str = "local"
    
    @property
    def is_admin(self) -> bool:
        """是否是管理员"""
        return self.role == UserRole.ADMIN or self.has_permission("system.admin")

    def has_permission(self, permission: str) -> bool:
        """Check one resolved RBAC permission without another database lookup."""
        if self.role == UserRole.ADMIN:
            return True
        permissions = set(self.permission_codes)
        if (
            "*" in permissions
            or "system.admin" in permissions
            or permission in permissions
        ):
            return True
        namespace = permission.partition(".")[0]
        return f"{namespace}.*" in permissions


class UserInDB(User):
    """数据库中的用户"""
    hashed_password: str


class UserLogin(BaseModel):
    """登录请求"""
    username: str
    password: str
    key: str


class UserCreate(BaseModel):
    """创建用户请求"""
    username: str
    password: str
    role: UserRole = UserRole.USER


class UserUpdate(BaseModel):
    """更新用户请求（管理员用）"""
    new_username: str | None = None
    password: str | None = None
    role: UserRole | None = None
    disabled: bool | None = None


class PasswordChange(BaseModel):
    """修改密码请求（用户自己）"""
    old_password: str
    new_password: str


class LoginKeyChange(BaseModel):
    """修改登录 Key 请求（管理员用）"""
    old_key: str
    new_key: str


# ============ 密码工具函数 ============

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


def hash_password(password: str) -> str:
    """哈希密码"""
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(rounds=12)
    ).decode("utf-8")


# ============ Token 相关 ============

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """创建 access token（服务端存储）。"""
    username = data.get("sub")
    if not username:
        raise ValueError("data.sub 不能为空")
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=15))
    return TOKEN_STORE.issue(username=username, expires_at=expire)


def revoke_access_token(token: str) -> None:
    TOKEN_STORE.revoke(token)


# ============ 依赖注入 ============

async def get_raw_token(token: Annotated[str, Depends(oauth2_scheme)]) -> str:
    return token


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> User:
    """获取当前用户（所有 API 都需要鉴权）"""
    from api.db.mongodb import get_db
    from api.dao import users as users_dao
    
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的认证凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    username = TOKEN_STORE.get_username(token)
    if not username:
        raise credentials_exception
    
    db = get_db()
    user_doc = await users_dao.get_user(db, username)
    if user_doc is None:
        raise credentials_exception

    legacy_role_value = str(user_doc.get("role") or "user")
    try:
        legacy_role = UserRole(legacy_role_value)
    except ValueError:
        # RBAC role IDs live in bindings, not in the legacy two-value field.
        legacy_role = UserRole.USER

    from api.services.authorization import (
        ExternalIdentity,
        get_authorization_service,
    )

    identity_doc = user_doc.get("external_identity")
    external_identity = None
    if isinstance(identity_doc, dict):
        issuer = str(identity_doc.get("issuer") or "").strip()
        subject = str(identity_doc.get("subject") or "").strip()
        if issuer and subject:
            raw_groups = identity_doc.get("groups") or []
            group_values = (
                raw_groups
                if isinstance(raw_groups, (list, tuple, set))
                else [raw_groups]
            )
            external_identity = ExternalIdentity(
                issuer=issuer,
                subject=subject,
                groups=tuple(
                    str(group).strip()
                    for group in group_values
                    if str(group).strip()
                ),
            )
    auth_source = (
        str(user_doc.get("auth_source") or "local").strip().lower() or "local"
    )
    authorization = await get_authorization_service().resolve(
        db,
        username=str(user_doc["username"]),
        legacy_role=legacy_role.value,
        auth_source=auth_source,
        external_identity=external_identity,
    )

    return User(
        username=user_doc["username"],
        role=legacy_role,
        disabled=user_doc.get("disabled", False),
        roles=list(authorization.role_ids),
        permission_codes=sorted(authorization.permissions),
        auth_source=auth_source,
    )


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)]
) -> User:
    """获取当前活跃用户"""
    if current_user.disabled:
        raise HTTPException(status_code=400, detail="用户已禁用")
    if not current_user.permission_codes and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前身份未映射任何有效权限",
        )
    return current_user


# ============ 权限检查依赖 ============

def require_admin(
    current_user: Annotated[User, Depends(get_current_active_user)]
) -> User:
    """要求管理员权限"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )
    return current_user


def require_permission(*permissions: str, any_of: bool = False):
    """Require resolved RBAC permissions while preserving legacy admin access."""
    required = tuple(
        dict.fromkeys(
            str(permission).strip()
            for permission in permissions
            if str(permission).strip()
        )
    )
    if not required:
        raise ValueError("至少需要一个权限代码")

    def checker(
        current_user: Annotated[User, Depends(get_current_active_user)],
    ) -> User:
        decisions = [
            current_user.has_permission(permission) for permission in required
        ]
        allowed = any(decisions) if any_of else all(decisions)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": "权限不足",
                    "required": list(required),
                    "mode": "any" if any_of else "all",
                },
            )
        return current_user

    return checker


def require_role(*roles: UserRole):
    """要求特定角色（可传入多个角色）"""
    def checker(current_user: Annotated[User, Depends(get_current_active_user)]) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要以下角色之一: {[r.value for r in roles]}"
            )
        return current_user
    return checker
