"""
认证路由 - 使用 MongoDB 持久化
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.auth import (
    Token, User, UserLogin, UserRole, UserCreate, UserUpdate, 
    PasswordChange, LoginKeyChange,
    create_access_token, get_current_active_user, 
    revoke_access_token, get_raw_token, require_admin,
    verify_password,
)
from api.config import get_settings
from api.db.mongodb import get_db
from api.dao import users as users_dao

router = APIRouter()
settings = get_settings()


@router.post("/login", response_model=Token)
async def login(user_login: UserLogin):
    """JSON 格式登录"""
    db = get_db()
    
    # 验证 login key
    current_key = await users_dao.get_login_key(db)
    if user_login.key != current_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="key 错误",
        )
    
    # 验证用户名密码
    user_doc = await users_dao.get_user(db, user_login.username)
    if not user_doc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    
    if not verify_password(user_login.password, user_doc["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    
    if user_doc.get("disabled", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户已禁用",
        )
    
    access_token = create_access_token(
        data={"sub": user_doc["username"]},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return Token(access_token=access_token)


@router.post("/logout")
async def logout(
    current_user: Annotated[User, Depends(get_current_active_user)],
    raw_token: Annotated[str, Depends(get_raw_token)],
):
    revoke_access_token(raw_token)
    return {"status": "ok"}


@router.get("/me")
async def me(current_user: Annotated[User, Depends(get_current_active_user)]):
    """获取当前用户信息（包含权限）"""
    return {
        "username": current_user.username,
        "role": current_user.role,
        "is_admin": current_user.is_admin,
        "disabled": current_user.disabled,
        "permissions": {
            "system_management": current_user.is_admin,
            "user_management": current_user.is_admin,
        }
    }


# ============ 用户自助 API ============

@router.post("/change-password")
async def change_my_password(
    body: PasswordChange,
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """修改自己的密码（需验证原密码）"""
    db = get_db()
    try:
        await users_dao.change_password(
            db, current_user.username, body.old_password, body.new_password
        )
        return {"status": "ok", "message": "密码修改成功"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============ 系统管理 API（仅管理员） ============

@router.get("/users", tags=["系统管理"])
async def get_users(admin: Annotated[User, Depends(require_admin)]):
    """获取所有用户列表（仅管理员）"""
    db = get_db()
    users = await users_dao.list_users(db)
    return {
        "users": [
            {"username": u["username"], "role": u.get("role", "user"), "disabled": u.get("disabled", False)} 
            for u in users
        ]
    }


@router.post("/users", tags=["系统管理"])
async def create_new_user(
    user_data: UserCreate,
    admin: Annotated[User, Depends(require_admin)]
):
    """创建新用户（仅管理员）"""
    db = get_db()
    try:
        new_user = await users_dao.create_user(
            db, user_data.username, user_data.password, user_data.role.value
        )
        return {"status": "ok", "user": {"username": new_user["username"], "role": new_user["role"]}}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/users/{username}", tags=["系统管理"])
async def update_existing_user(
    username: str,
    user_data: UserUpdate,
    admin: Annotated[User, Depends(require_admin)]
):
    """更新用户信息（仅管理员）- 可修改用户名、密码、角色、禁用状态"""
    db = get_db()
    try:
        updated_user = await users_dao.update_user(
            db,
            username,
            new_username=user_data.new_username,
            password=user_data.password,
            role=user_data.role.value if user_data.role else None,
            disabled=user_data.disabled,
        )
        return {"status": "ok", "user": {"username": updated_user["username"], "role": updated_user["role"]}}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/users/{username}", tags=["系统管理"])
async def delete_existing_user(
    username: str,
    admin: Annotated[User, Depends(require_admin)]
):
    """删除用户（仅管理员）"""
    db = get_db()
    try:
        await users_dao.delete_user(db, username)
        return {"status": "ok", "message": f"用户 {username} 已删除"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class ResetPasswordRequest(BaseModel):
    """重置密码请求"""
    new_password: str


@router.post("/users/{username}/reset-password", tags=["系统管理"])
async def reset_user_password(
    username: str,
    body: ResetPasswordRequest,
    admin: Annotated[User, Depends(require_admin)]
):
    """
    重置用户密码（仅管理员）
    
    管理员可以直接为用户设置新密码，无需验证原密码。
    """
    db = get_db()
    try:
        await users_dao.update_user(db, username, password=body.new_password)
        return {"status": "ok", "message": f"用户 {username} 的密码已重置"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/change-login-key", tags=["系统管理"])
async def change_login_key(
    body: LoginKeyChange,
    admin: Annotated[User, Depends(require_admin)]
):
    """修改登录 Key（仅管理员，需验证原 Key）"""
    db = get_db()
    
    current_key = await users_dao.get_login_key(db)
    if body.old_key != current_key:
        raise HTTPException(status_code=400, detail="原 Key 错误")
    
    if not body.new_key or len(body.new_key) < 6:
        raise HTTPException(status_code=400, detail="新 Key 长度至少 6 位")
    
    await users_dao.set_login_key(db, body.new_key)
    return {"status": "ok", "message": "登录 Key 已更新"}


@router.get("/login-key", tags=["系统管理"])
async def get_current_login_key(admin: Annotated[User, Depends(require_admin)]):
    """获取当前登录 Key（仅管理员）"""
    db = get_db()
    key = await users_dao.get_login_key(db)
    return {"key": key}
