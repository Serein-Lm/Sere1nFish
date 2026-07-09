"""
XHS Cookie 管理 API

通过 Redis 管理小红书登录 Cookie
"""
import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_active_user
from api.services.runtime_config import get_runtime_config_section
from api.db.redis_cookie import RedisCookieStore


router = APIRouter(prefix="/xhs/cookies", tags=["XHS Cookie管理"])


# ==================== Schemas ====================

class CookieCreate(BaseModel):
    """创建/更新 Cookie"""
    account_name: str
    cookie_string: str
    is_valid: Optional[bool] = None


class CookieActivate(BaseModel):
    """激活账号"""
    account_name: str


class CookieResponse(BaseModel):
    """Cookie 响应"""
    account_name: str
    cookie_string: str
    is_valid: Optional[bool] = None
    is_active: bool = False


class MessageResponse(BaseModel):
    """消息响应"""
    success: bool
    message: str


# ==================== Dependencies ====================

async def get_cookie_store() -> RedisCookieStore:
    """获取 Redis Cookie 存储"""
    redis_config = await get_runtime_config_section("redis")
    return RedisCookieStore.from_config({"redis": redis_config})


# ==================== API Endpoints ====================

@router.get("", response_model=list[CookieResponse])
async def list_cookies(
    store: RedisCookieStore = Depends(get_cookie_store),
    _user: dict = Depends(get_current_active_user),
) -> list[CookieResponse]:
    """列出所有 Cookie"""
    cookies = store.list_cookies()
    return [CookieResponse(**c) for c in cookies]


@router.post("", response_model=MessageResponse)
async def save_cookie(
    data: CookieCreate,
    store: RedisCookieStore = Depends(get_cookie_store),
    _user: dict = Depends(get_current_active_user),
) -> MessageResponse:
    """保存 Cookie"""
    success = store.save_cookie(
        account_name=data.account_name,
        cookie_string=data.cookie_string,
        is_valid=data.is_valid,
    )
    if success:
        return MessageResponse(success=True, message=f"Cookie {data.account_name} 已保存")
    return MessageResponse(success=False, message="保存失败")


@router.get("/{account_name}", response_model=CookieResponse)
async def get_cookie(
    account_name: str,
    store: RedisCookieStore = Depends(get_cookie_store),
    _user: dict = Depends(get_current_active_user),
) -> CookieResponse:
    """获取指定账号的 Cookie"""
    cookie = store.get_cookie(account_name)
    if not cookie:
        raise HTTPException(status_code=404, detail=f"账号 {account_name} 不存在")
    
    cookie["is_active"] = store.get_active_account() == account_name
    return CookieResponse(**cookie)


@router.delete("/{account_name}", response_model=MessageResponse)
async def delete_cookie(
    account_name: str,
    store: RedisCookieStore = Depends(get_cookie_store),
    _user: dict = Depends(get_current_active_user),
) -> MessageResponse:
    """删除 Cookie"""
    success = store.delete_cookie(account_name)
    if success:
        return MessageResponse(success=True, message=f"账号 {account_name} 已删除")
    return MessageResponse(success=False, message=f"账号 {account_name} 不存在或删除失败")


@router.post("/activate", response_model=MessageResponse)
async def activate_cookie(
    data: CookieActivate,
    store: RedisCookieStore = Depends(get_cookie_store),
    _user: dict = Depends(get_current_active_user),
) -> MessageResponse:
    """激活账号"""
    success = store.activate(data.account_name)
    if success:
        return MessageResponse(success=True, message=f"账号 {data.account_name} 已激活")
    return MessageResponse(success=False, message=f"账号 {data.account_name} 不存在")


@router.get("/active/current", response_model=CookieResponse)
async def get_active_cookie(
    store: RedisCookieStore = Depends(get_cookie_store),
    _user: dict = Depends(get_current_active_user),
) -> CookieResponse:
    """获取当前激活账号的 Cookie"""
    cookie = store.get_active_cookie()
    if not cookie:
        raise HTTPException(status_code=404, detail="没有激活的账号")
    
    cookie["is_active"] = True
    return CookieResponse(**cookie)


@router.get("/health/ping", response_model=MessageResponse)
async def ping_redis(
    store: RedisCookieStore = Depends(get_cookie_store),
    _user: dict = Depends(get_current_active_user),
) -> MessageResponse:
    """检查 Redis 连接"""
    if store.ping():
        return MessageResponse(success=True, message="Redis 连接正常")
    return MessageResponse(success=False, message="Redis 连接失败")
