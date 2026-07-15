"""配置字段加密/脱敏工具。

system_config 里的敏感字段写入前加密，读取给后端使用时透明解密。
"""

from __future__ import annotations

import base64
import json
import os
from copy import deepcopy
from hashlib import sha256
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

_MARKER = "__sere1nfish_encrypted__"
_SCHEME = "fernet:v1"
_VALUE = "value"

_EXACT_SENSITIVE_KEYS = {
    "api_key",
    "secret_key",
    "access_token",
    "refresh_token",
    "client_secret",
    "private_key",
    "public_key",
    "password",
    "secret",
    "token",
    "login_key",
    "network_secret",
    "access_key_id",
    "access_key_secret",
    "cookie",
    "cookies",
}
_SENSITIVE_SUFFIXES = (
    "_api_key",
    "_secret_key",
    "_access_token",
    "_refresh_token",
    "_token",
    "_client_secret",
    "_private_key",
    "_public_key",
    "_password",
    "_secret",
    "_access_key_id",
    "_access_key_secret",
)


def _secret_material() -> str:
    explicit = os.getenv("CONFIG_ENCRYPTION_KEY")
    if explicit:
        return explicit
    from api.config import get_settings

    return get_settings().SECRET_KEY


def _fernet_key() -> bytes:
    material = _secret_material().encode("utf-8")
    return base64.urlsafe_b64encode(sha256(b"Sere1nFish.config.v1:" + material).digest())


def _fernet() -> Fernet:
    return Fernet(_fernet_key())


def is_sensitive_key(key: str) -> bool:
    name = str(key or "").strip().lower()
    return name in _EXACT_SENSITIVE_KEYS or any(name.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def is_encrypted_value(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get(_MARKER) is True
        and value.get("scheme") == _SCHEME
        and isinstance(value.get(_VALUE), str)
    )


def encrypt_value(value: Any) -> dict[str, Any]:
    if is_encrypted_value(value):
        return value
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    token = _fernet().encrypt(payload).decode("ascii")
    return {_MARKER: True, "scheme": _SCHEME, _VALUE: token}


def decrypt_value(value: Any) -> Any:
    if not is_encrypted_value(value):
        return value
    try:
        payload = _fernet().decrypt(value[_VALUE].encode("ascii"))
        return json.loads(payload.decode("utf-8"))
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
        return None


def encrypt_config(config: Any, *, parent_key: str = "") -> Any:
    """递归加密敏感字段。非敏感字段保持原结构。"""
    if is_encrypted_value(config):
        return config
    if parent_key and is_sensitive_key(parent_key):
        return encrypt_value(config)
    if isinstance(config, dict):
        return {key: encrypt_config(value, parent_key=str(key)) for key, value in config.items()}
    if isinstance(config, list):
        return [encrypt_config(item) for item in config]
    return deepcopy(config)


def decrypt_config(config: Any) -> Any:
    """递归解密配置。明文字段与旧数据保持兼容。"""
    if is_encrypted_value(config):
        return decrypt_value(config)
    if isinstance(config, dict):
        return {key: decrypt_config(value) for key, value in config.items()}
    if isinstance(config, list):
        return [decrypt_config(item) for item in config]
    return deepcopy(config)


def mask_secret(value: Any) -> Any:
    if value is None or value == "":
        return value
    if not isinstance(value, str):
        return "***"
    if len(value) < 12:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def mask_sensitive_config(config: Any, *, parent_key: str = "") -> Any:
    """递归脱敏，用于 API 输出。"""
    if is_encrypted_value(config):
        return "***"
    if parent_key and is_sensitive_key(parent_key):
        return mask_secret(config)
    if isinstance(config, dict):
        return {key: mask_sensitive_config(value, parent_key=str(key)) for key, value in config.items()}
    if isinstance(config, list):
        return [mask_sensitive_config(item) for item in config]
    return deepcopy(config)
