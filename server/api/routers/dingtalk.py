"""钉钉群 @机器人 入站回调路由。

暴露钉钉 outgoing 机器人的回调地址。安全性由入站 HMAC 验签保证
（见 api.services.dingtalk_bridge / crawler_tools.dingtalk_bot），
而非平台登录态——钉钉回调无法携带本系统 JWT，故本路由不挂登录依赖。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request

from api.services import dingtalk_bridge

router = APIRouter()


@router.post("/callback")
async def dingtalk_callback(
    request: Request,
    timestamp: str = Header(default=""),
    sign: str = Header(default=""),
) -> dict[str, Any]:
    """接收钉钉群 @机器人 的消息回调。

    立即返回 {}（满足钉钉 5s 响应要求）；实际的 AI 中枢推理与回推
    在后台异步执行，通过回调 body 中的 sessionWebhook 发回来源会话。
    """
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = {}

    if isinstance(payload, dict):
        await dingtalk_bridge.handle_inbound(payload, timestamp, sign)

    # 无论受理与否都回 200 + 空体，避免钉钉重试风暴；失败原因记录在服务端日志
    return {}
