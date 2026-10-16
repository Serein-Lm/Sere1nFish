"""Process identity and credential filtering for log sinks only."""
from __future__ import annotations

import logging
import os
import re
import time
import uuid
from typing import Any


_PROCESS_IDS: dict[int, str] = {}
_SECRET_KEYS = frozenset({
    "token", "access_token", "refresh_token", "id_token", "api_key", "apikey",
    "api_token", "password", "passwd", "secret", "secret_key", "client_secret",
    "authorization", "cookie", "set_cookie", "x_api_key", "signature",
    "x_oss_signature", "ossaccesskeyid", "security_token", "x_oss_security_token",
})
_ASSIGNMENT = re.compile(
    r'''(?i)(\b(?:access[_-]?token|refresh[_-]?token|api[_-]?key|api[_-]?token|token|password|passwd|secret|secret[_-]?key|client[_-]?secret|signature|x-oss-signature|ossaccesskeyid|security[_-]?token)\b["']?\s*[:=]\s*["']?)([^\s&,;"'<>}]+)'''
)
_BEARER = re.compile(r"(?i)(\b(?:Bearer|Basic)\s+)[A-Za-z0-9._~+/=-]+")
_COOKIE_HEADER = re.compile(r"(?im)(\b(?:authorization|set-cookie|cookie)\s*:\s*)[^\r\n]+")
_URL_PASSWORD = re.compile(r"(://[^/\s:@]+:)[^@/\s]+(@)")


def process_identity() -> dict[str, Any]:
    pid = os.getpid()
    runtime_id = _PROCESS_IDS.get(pid)
    if runtime_id is None:
        runtime_id = _PROCESS_IDS.setdefault(pid, uuid.uuid4().hex)
    return {"run_kind": os.getenv("SEREINFISH_RUN_KIND", "unspecified"),
            "process_id": pid, "runtime_id": runtime_id}


def redact_log_text(value: str) -> str:
    value = _BEARER.sub(r"\1[REDACTED_SECRET]", str(value))
    value = _ASSIGNMENT.sub(r"\1[REDACTED_SECRET]", value)
    value = _URL_PASSWORD.sub(r"\1[REDACTED_SECRET]\2", value)
    return _COOKIE_HEADER.sub(r"\1[REDACTED_SECRET]", value)


def redact_log_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): "[REDACTED_SECRET]" if str(k).lower().replace("-", "_") in _SECRET_KEYS
                else redact_log_data(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_log_data(v) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_log_text(str(value))


class RuntimeLogFormatter(logging.Formatter):
    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        for key, value in process_identity().items():
            setattr(record, key, value)
        return redact_log_text(super().format(record))
