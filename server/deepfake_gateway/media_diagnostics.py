"""Sanitized runtime diagnostics for short-lived WHIP publishers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class WhipPublishDiagnostics:
    attempts: int = 0
    authorized_attempts: int = 0
    last_authorized: bool | None = None
    last_attempt_at: str = ""
    last_ip: str = ""
    last_protocol: str = ""

    def record(self, payload: dict[str, Any], *, authorized: bool) -> None:
        self.attempts += 1
        if authorized:
            self.authorized_attempts += 1
        self.last_authorized = authorized
        self.last_attempt_at = _now_iso()
        self.last_ip = str(payload.get("ip") or "")[:64]
        self.last_protocol = str(payload.get("protocol") or "")[:32]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
