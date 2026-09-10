"""SOCKS5 profile lifecycle, leasing and protocol health checks."""

from __future__ import annotations

import asyncio
import ipaddress
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import proxy_profiles as proxy_dao

from .validation import normalize_labels, validate_proxy_endpoint


class ProxyUnavailableError(RuntimeError):
    pass


async def save_proxy_profile(
    db: AsyncIOMotorDatabase,
    *,
    profile_id: str,
    name: str,
    endpoint: str | None,
    username: str | None,
    password: str | None,
    dns_mode: str,
    max_concurrency: int,
    failure_threshold: int,
    cooldown_seconds: int,
    bypass_classes: list[str],
    labels: dict[str, str],
    status: str,
    updated_by: str,
) -> dict[str, Any]:
    normalized_id = profile_id.strip() or "proxy_" + uuid.uuid4().hex
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("代理名称不能为空")
    normalized_endpoint = (
        validate_proxy_endpoint(endpoint, dns_mode=dns_mode)
        if endpoint is not None and endpoint.strip()
        else None
    )
    return await proxy_dao.upsert_profile(
        db,
        profile_id=normalized_id,
        name=normalized_name,
        endpoint=normalized_endpoint,
        username=username,
        password=password,
        dns_mode=dns_mode,
        max_concurrency=max(1, min(max_concurrency, 500)),
        failure_threshold=max(1, min(failure_threshold, 20)),
        cooldown_seconds=max(10, min(cooldown_seconds, 86400)),
        bypass_classes=list(dict.fromkeys(value.strip() for value in bypass_classes if value.strip()))[:50],
        labels=normalize_labels(labels),
        status=status,
        updated_by=updated_by,
    )


async def lease_proxy_for_work(
    db: AsyncIOMotorDatabase,
    *,
    work: dict[str, Any],
    node_id: str,
) -> dict[str, Any] | None:
    profile_id = str(work.get("proxy_profile_id") or "").strip()
    mode = str(work.get("proxy_mode") or "none")
    if mode == "none":
        return None
    if not profile_id:
        if mode == "required":
            raise ProxyUnavailableError("工作项要求代理，但没有指定 SOCKS5 配置")
        return None
    lease = work.get("lease") or {}
    expires_at = lease.get("expires_at")
    if not isinstance(expires_at, datetime):
        raise ProxyUnavailableError("工作项缺少有效租约到期时间")
    acquired = await proxy_dao.acquire_lease(
        db,
        lease_id="pxl_" + uuid.uuid4().hex,
        profile_id=profile_id,
        node_id=node_id,
        work_item_id=str(work["work_item_id"]),
        work_attempt=max(1, int(work.get("attempt") or 1)),
        sticky_key=str(work.get("affinity_key") or work.get("target_id") or ""),
        expires_at=expires_at,
    )
    if not acquired:
        if mode == "required":
            raise ProxyUnavailableError("指定 SOCKS5 代理不可用或容量已满")
        return None
    profile = acquired["profile"]
    return {
        "lease_id": acquired["lease"]["lease_id"],
        "profile_id": profile_id,
        "type": "socks5",
        "endpoint": profile.get("endpoint", ""),
        "username": profile.get("username", ""),
        "password": profile.get("password", ""),
        "dns_mode": profile.get("dns_mode", "remote"),
        "bypass_classes": profile.get("bypass_classes", []),
        "expires_at": expires_at,
    }


async def release_proxy_for_work(
    db: AsyncIOMotorDatabase,
    *,
    work_item_id: str,
    ok: bool | None = None,
    error_code: str = "",
) -> None:
    lease = await proxy_dao.get_work_lease(db, work_item_id)
    if not lease:
        return
    await proxy_dao.release_work_lease(db, work_item_id=work_item_id)
    if ok is not None:
        await proxy_dao.report_result(
            db,
            profile_id=str(lease["profile_id"]),
            ok=ok,
            error_code=error_code,
        )


async def test_proxy_profile(
    db: AsyncIOMotorDatabase,
    *,
    profile_id: str,
    target_host: str = "www.aliyun.com",
    target_port: int = 443,
    timeout: float = 10.0,
) -> dict[str, Any]:
    profile = await proxy_dao.get_profile(db, profile_id, reveal=True)
    if not profile:
        raise LookupError("代理配置不存在")
    started = asyncio.get_running_loop().time()
    try:
        await asyncio.wait_for(
            _socks5_connect_test(
                endpoint=str(profile.get("endpoint") or ""),
                username=str(profile.get("username") or ""),
                password=str(profile.get("password") or ""),
                target_host=target_host,
                target_port=target_port,
            ),
            timeout=max(2.0, min(timeout, 30.0)),
        )
    except Exception as exc:
        await proxy_dao.report_result(
            db,
            profile_id=profile_id,
            ok=False,
            error_code=type(exc).__name__,
        )
        return {
            "ok": False,
            "profile_id": profile_id,
            "latency_ms": round((asyncio.get_running_loop().time() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }
    await proxy_dao.report_result(db, profile_id=profile_id, ok=True)
    return {
        "ok": True,
        "profile_id": profile_id,
        "latency_ms": round((asyncio.get_running_loop().time() - started) * 1000),
        "target": f"{target_host}:{target_port}",
    }


async def _socks5_connect_test(
    *,
    endpoint: str,
    username: str,
    password: str,
    target_host: str,
    target_port: int,
) -> None:
    parsed = urlsplit(endpoint)
    if not parsed.hostname or not parsed.port:
        raise ValueError("SOCKS5 endpoint 无效")
    reader, writer = await asyncio.open_connection(parsed.hostname, parsed.port)
    try:
        methods = b"\x00\x02" if username or password else b"\x00"
        writer.write(bytes((5, len(methods))) + methods)
        await writer.drain()
        version, method = await reader.readexactly(2)
        if version != 5 or method == 0xFF:
            raise ConnectionError("SOCKS5 服务拒绝认证方式")
        if method == 0x02:
            user = username.encode("utf-8")
            secret = password.encode("utf-8")
            if len(user) > 255 or len(secret) > 255:
                raise ValueError("SOCKS5 用户名或密码过长")
            writer.write(bytes((1, len(user))) + user + bytes((len(secret),)) + secret)
            await writer.drain()
            auth_version, auth_status = await reader.readexactly(2)
            if auth_version != 1 or auth_status != 0:
                raise PermissionError("SOCKS5 认证失败")
        elif method != 0x00:
            raise ConnectionError(f"SOCKS5 返回未知认证方式 {method}")

        try:
            ip = ipaddress.ip_address(target_host)
            atyp = 1 if ip.version == 4 else 4
            address = ip.packed
        except ValueError:
            encoded = target_host.encode("idna")
            if len(encoded) > 255:
                raise ValueError("目标域名过长")
            atyp = 3
            address = bytes((len(encoded),)) + encoded
        writer.write(b"\x05\x01\x00" + bytes((atyp,)) + address + int(target_port).to_bytes(2, "big"))
        await writer.drain()
        version, reply, _, reply_type = await reader.readexactly(4)
        if version != 5 or reply != 0:
            raise ConnectionError(f"SOCKS5 CONNECT 失败，代码 {reply}")
        if reply_type == 1:
            await reader.readexactly(4)
        elif reply_type == 4:
            await reader.readexactly(16)
        elif reply_type == 3:
            length = (await reader.readexactly(1))[0]
            await reader.readexactly(length)
        else:
            raise ConnectionError("SOCKS5 返回未知地址类型")
        await reader.readexactly(2)
    finally:
        writer.close()
        await writer.wait_closed()
