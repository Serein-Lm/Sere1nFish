"""Environment-backed node configuration and protected identity storage."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _as_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _as_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _labels() -> dict[str, str]:
    try:
        value = json.loads(os.getenv("SCAN_NODE_LABELS", "").strip() or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("SCAN_NODE_LABELS 必须是 JSON 对象") from exc
    if not isinstance(value, dict):
        raise ValueError("SCAN_NODE_LABELS 必须是 JSON 对象")
    return {
        str(key)[:64]: str(item)[:200]
        for key, item in list(value.items())[:30]
        if str(key).strip() and str(item).strip()
    }


@dataclass(frozen=True, slots=True)
class NodeConfig:
    control_plane_url: str
    bootstrap_token: str
    display_name: str
    state_path: Path
    labels: dict[str, str]
    http_slots: int
    browser_slots: int
    heartbeat_seconds: int
    request_timeout_seconds: int
    verify_tls: bool | str

    @classmethod
    def from_env(cls) -> "NodeConfig":
        control_plane_url = os.getenv("SCAN_CONTROL_PLANE_URL", "").strip().rstrip("/")
        if not control_plane_url.startswith("https://"):
            raise ValueError("SCAN_CONTROL_PLANE_URL 必须使用 HTTPS")
        ca_bundle = os.getenv("SCAN_NODE_CA_BUNDLE", "").strip()
        verify_tls: bool | str = ca_bundle or not _as_bool("SCAN_NODE_INSECURE_TLS")
        return cls(
            control_plane_url=control_plane_url,
            bootstrap_token=os.getenv("SCAN_NODE_BOOTSTRAP_TOKEN", "").strip(),
            display_name=(os.getenv("SCAN_NODE_NAME", "").strip() or socket.gethostname())[:120],
            state_path=Path(
                os.getenv(
                    "SCAN_NODE_STATE_PATH",
                    "/var/lib/sere1nfish-node/identity.json",
                )
            ),
            labels=_labels(),
            http_slots=_as_int("SCAN_NODE_HTTP_SLOTS", 32, 0, 500),
            browser_slots=_as_int("SCAN_NODE_BROWSER_SLOTS", 4, 0, 100),
            heartbeat_seconds=_as_int("SCAN_NODE_HEARTBEAT_SECONDS", 20, 5, 60),
            request_timeout_seconds=_as_int("SCAN_NODE_REQUEST_TIMEOUT_SECONDS", 40, 10, 300),
            verify_tls=verify_tls,
        )

    @property
    def capabilities(self) -> dict[str, str]:
        result: dict[str, str] = {}
        if self.http_slots:
            result["http_probe"] = "1"
        if self.browser_slots:
            result["browser_probe"] = "1"
        if not result:
            raise ValueError("HTTP 与浏览器 slot 不能同时为 0")
        return result

    @property
    def capacity(self) -> dict[str, int]:
        return {
            "http_probe_slots": self.http_slots,
            "browser_probe_slots": self.browser_slots,
        }


def load_identity(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    stat = path.stat()
    if stat.st_mode & 0o077:
        raise PermissionError(f"节点身份文件权限必须为 0600: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not value.get("node_id") or not value.get("node_token"):
        raise ValueError("节点身份文件内容无效")
    return value


def save_identity(path: Path, identity: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(identity, handle, ensure_ascii=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
