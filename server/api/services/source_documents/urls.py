"""来源 URL 规范化。"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_WECHAT_IDENTITY_QUERY_KEYS = {"__biz", "mid", "idx", "sn"}


def canonicalize_source_url(url: str) -> str:
    value = str(url or "").strip()
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("来源链接必须是有效的 HTTP/HTTPS URL")
    scheme = "https" if parts.scheme == "https" else "http"
    host = parts.hostname.lower()
    if parts.port and parts.port not in {80, 443}:
        host = f"{host}:{parts.port}"
    path = parts.path or "/"
    query = parts.query
    if host == "mp.weixin.qq.com":
        scheme = "https"
        if path.startswith("/s/"):
            query = ""
        elif path == "/s":
            identity = [
                (key, value)
                for key, value in parse_qsl(parts.query, keep_blank_values=False)
                if key in _WECHAT_IDENTITY_QUERY_KEYS
            ]
            query = urlencode(sorted(identity))
    return urlunsplit((scheme, host, path, query, ""))
