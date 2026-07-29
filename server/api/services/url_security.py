"""Shared public-URL validation for outbound browser and downloader adapters."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


async def assert_public_http_url(url: str) -> None:
    """Reject credentials, local names, and non-global DNS answers."""
    parsed = urlsplit(str(url or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("仅允许无凭据的 HTTP/HTTPS 公网地址")
    if parsed.hostname.lower() == "localhost":
        raise ValueError("不允许访问本机地址")

    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(
        parsed.hostname,
        parsed.port or (443 if parsed.scheme == "https" else 80),
        type=socket.SOCK_STREAM,
    )
    addresses = {item[4][0].split("%", 1)[0] for item in infos}
    if not addresses or any(
        not ipaddress.ip_address(address).is_global for address in addresses
    ):
        raise ValueError("目标地址不是公网地址")
