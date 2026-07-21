"""DingTalk bot configuration normalization service."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import config as config_dao


_CARD_TEMPLATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,255}$")


def normalize_webhook_access_token(value: str | None) -> str | None:
    """Accept either a raw access token or the complete DingTalk webhook URL."""
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return ""
    if not normalized.lower().startswith(("http://", "https://")):
        return normalized
    parsed = urlsplit(normalized)
    if parsed.hostname != "oapi.dingtalk.com" or parsed.path != "/robot/send":
        raise ValueError("钉钉 Webhook URL 格式不正确")
    token = str(parse_qs(parsed.query).get("access_token", [""])[0]).strip()
    if not token:
        raise ValueError("钉钉 Webhook URL 缺少 access_token")
    return token


def normalize_card_template_id(value: str | None) -> str | None:
    """Normalize an optional DingTalk card template identifier."""
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return ""
    if not _CARD_TEMPLATE_ID_RE.fullmatch(normalized):
        raise ValueError("钉钉 AI Card 模板 ID 格式不正确")
    return normalized


async def update_dingtalk_bot(
    db: AsyncIOMotorDatabase,
    *,
    bot_name: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    """Normalize independent Webhook/Stream settings and persist a partial update."""
    payload = dict(values)
    if "access_token" in payload:
        payload["access_token"] = normalize_webhook_access_token(
            payload.get("access_token")
        )
    if "ai_card_template_id" in payload:
        payload["ai_card_template_id"] = normalize_card_template_id(
            payload.get("ai_card_template_id")
        )
    return await config_dao.set_dingtalk_config(
        db,
        bot_name=bot_name,
        **payload,
    )
