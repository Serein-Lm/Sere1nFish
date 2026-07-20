"""Stable URL identities shared by discovery, scans, and project queries."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def endpoint_identity(
    value: str,
    *,
    include_path: bool = True,
    include_query: bool = False,
) -> str:
    """Return a scheme-independent endpoint identity.

    HTTP and HTTPS default ports represent the same logical target. Explicit
    non-default ports and distinct paths remain separate targets.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").strip().lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return ""
    if not host:
        return ""
    if port in {None, 80, 443}:
        authority = host
    else:
        authority = f"{host}:{port}"
    if not include_path:
        return authority
    path = "/" + "/".join(part for part in parsed.path.split("/") if part)
    identity = authority + ("" if path == "/" else path.rstrip("/"))
    if include_query and parsed.query:
        identity += f"?{parsed.query}"
    return identity


def prefer_https_url(current: str, candidate: str) -> str:
    """Choose HTTPS when two URLs identify the same endpoint."""
    current = str(current or "").strip()
    candidate = str(candidate or "").strip()
    if not current:
        return candidate
    if not candidate:
        return current
    if endpoint_identity(current) != endpoint_identity(candidate):
        return current
    current_scheme = urlsplit(current).scheme.lower()
    candidate_scheme = urlsplit(candidate).scheme.lower()
    if candidate_scheme == "https" and current_scheme != "https":
        return candidate
    return current


def canonical_display_url(value: str) -> str:
    """Normalize casing, fragments and a trailing root slash for display."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            return ""
        port = parsed.port
    except ValueError:
        return ""
    authority = host
    if port and not (parsed.scheme == "http" and port == 80) and not (
        parsed.scheme == "https" and port == 443
    ):
        authority = f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), authority, path, parsed.query, ""))
