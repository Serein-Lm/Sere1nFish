"""Provider-neutral object storage error classification."""

from __future__ import annotations

import re
from dataclasses import dataclass


_CONFIGURATION_ERROR_CODES = frozenset(
    {
        "accessdenied",
        "invalidaccesskeyid",
        "invalidbucketname",
        "invalidsecuritytoken",
        "nosuchbucket",
        "securitytokenexpired",
        "signaturedoesnotmatch",
    }
)
_ACCESS_KEY_PATTERN = re.compile(r"\bLTAI[A-Za-z0-9_-]{8,}\b")


@dataclass(frozen=True, slots=True)
class StorageErrorDetails:
    code: str = ""
    status_code: int = 0
    request_id: str = ""
    message: str = ""

    @property
    def configuration_error(self) -> bool:
        return self.code.casefold() in _CONFIGURATION_ERROR_CODES

    def safe_message(self) -> str:
        parts = ["对象存储请求失败"]
        if self.code:
            parts.append(f"code={self.code}")
        if self.status_code:
            parts.append(f"status={self.status_code}")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        if self.message:
            parts.append(_ACCESS_KEY_PATTERN.sub("LTAI***", self.message)[:500])
        return "; ".join(parts)


def _error_chain(error: BaseException):
    pending: list[object] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        yield current
        for name in ("_error", "__cause__", "__context__"):
            nested = getattr(current, name, None)
            if nested is not None:
                pending.append(nested)


def storage_error_details(error: BaseException) -> StorageErrorDetails:
    """Read stable fields from wrapped SDK errors without importing an SDK."""
    code = ""
    status_code = 0
    request_id = ""
    message = ""
    for current in _error_chain(error):
        code = code or str(
            getattr(current, "code", None)
            or getattr(current, "error_code", None)
            or ""
        ).strip()
        request_id = request_id or str(
            getattr(current, "request_id", None) or ""
        ).strip()
        message = message or str(
            getattr(current, "message", None) or ""
        ).strip()
        if not status_code:
            try:
                status_code = int(getattr(current, "status_code", None) or 0)
            except (TypeError, ValueError):
                status_code = 0
    if not message:
        message = str(error).strip()
    return StorageErrorDetails(
        code=code,
        status_code=status_code,
        request_id=request_id,
        message=message,
    )


def is_storage_configuration_error(error: BaseException) -> bool:
    """Return true only for durable credential, permission, or bucket errors."""
    return storage_error_details(error).configuration_error
