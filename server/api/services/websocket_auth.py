"""Shared authentication helpers for browser WebSocket endpoints."""

from __future__ import annotations

from fastapi import WebSocket

from api.auth_store import TOKEN_STORE
from api.dao import users as users_dao
from api.db.mongodb import get_db


PUBLIC_SUBPROTOCOL = "sere1nfish"
AUTH_SUBPROTOCOL_PREFIX = "sere1nfish.auth."


def websocket_bearer_token(websocket: WebSocket) -> str:
    """Read the bearer token from the browser-safe subprotocol transport."""
    protocols = websocket.headers.get("sec-websocket-protocol", "").split(",")
    for protocol in protocols:
        value = protocol.strip()
        if value.startswith(AUTH_SUBPROTOCOL_PREFIX):
            return value[len(AUTH_SUBPROTOCOL_PREFIX) :]
    return ""


async def authenticated_websocket_username(websocket: WebSocket) -> str:
    """Return an active username or an empty string for an invalid session."""
    token = websocket_bearer_token(websocket)
    username = TOKEN_STORE.get_username(token) if token else None
    if not username:
        return ""
    user = await users_dao.get_user(get_db(), username)
    if not user or user.get("disabled"):
        return ""
    return str(user.get("username") or "")
