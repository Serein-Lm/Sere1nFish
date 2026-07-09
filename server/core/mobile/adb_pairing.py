"""Wireless ADB pairing helpers.

Android 11+ wireless debugging uses TLS pairing. Pairing code mode can be
completed with ``adb pair ip:port code``. QR mode starts from a host-generated
ADB QR payload; after the phone scans it, the phone advertises an
``_adb-tls-pairing._tcp`` mDNS service that the host can pair with.
"""

from __future__ import annotations

import os
import random
import re
import secrets
import string
import subprocess
import time
from dataclasses import dataclass
from typing import Any


_QR_CHARS = string.ascii_letters + string.digits
_PAIR_CODE_RE = re.compile(r"^\d{6}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


@dataclass(frozen=True)
class AdbMdnsService:
    name: str
    service_type: str
    address: str
    host: str
    port: int


def _adb_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("ADB_MDNS", "1")
    return env


def _run_adb(args: list[str], *, timeout: float = 30.0) -> dict[str, Any]:
    cmd = ["adb", *args]
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=_adb_env(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "command": cmd,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or f"adb command timed out after {timeout:.1f}s",
            "returncode": None,
        }

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    return {
        "ok": proc.returncode == 0,
        "command": cmd,
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
        "returncode": proc.returncode,
    }


def _validate_host(host: str) -> str:
    value = host.strip()
    if not value or not _HOST_RE.match(value):
        raise ValueError("ADB 主机地址无效")
    return value


def _validate_port(port: int) -> int:
    if port < 1 or port > 65535:
        raise ValueError("ADB 端口必须在 1-65535 之间")
    return port


def _target(host: str, port: int) -> str:
    return f"{_validate_host(host)}:{_validate_port(port)}"


def adb_capabilities() -> dict[str, Any]:
    version = _run_adb(["version"], timeout=5)
    help_result = _run_adb(["help"], timeout=5)
    help_text = f"{help_result.get('stdout', '')}\n{help_result.get('stderr', '')}"
    return {
        "adb_version": version,
        "supports_pair": " pair " in help_text or "\npair " in help_text,
        "supports_mdns": " mdns " in help_text or "\nmdns " in help_text,
    }


def generate_qr_pairing_session() -> dict[str, str]:
    suffix = "".join(random.SystemRandom().choice(_QR_CHARS) for _ in range(10))
    service_name = f"studio-{suffix}"
    password = "".join(secrets.choice(_QR_CHARS) for _ in range(16))
    payload = f"WIFI:T:ADB;S:{service_name};P:{password};;"
    return {
        "service_name": service_name,
        "password": password,
        "qr_payload": payload,
    }


def parse_mdns_services(output: str) -> list[AdbMdnsService]:
    services: list[AdbMdnsService] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("List of"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        name, service_type, address = parts[0], parts[1].rstrip("."), parts[-1]
        if ":" not in address:
            continue
        host, _, port_raw = address.rpartition(":")
        try:
            port = int(port_raw)
        except ValueError:
            continue
        services.append(
            AdbMdnsService(
                name=name,
                service_type=service_type,
                address=address,
                host=host,
                port=port,
            )
        )
    return services


def adb_mdns_services() -> dict[str, Any]:
    result = _run_adb(["mdns", "services"], timeout=8)
    services = [service.__dict__ for service in parse_mdns_services(result.get("stdout", ""))]
    return {**result, "services": services}


def adb_pair_with_code(
    *,
    host: str,
    pairing_port: int,
    pairing_code: str,
    connect_port: int | None = None,
) -> dict[str, Any]:
    code = pairing_code.strip()
    if not _PAIR_CODE_RE.match(code):
        raise ValueError("配对码必须是 6 位数字")

    pair = _run_adb(["pair", _target(host, pairing_port), code], timeout=30)
    connect = None
    if pair.get("ok") and connect_port:
        connect = adb_connect(host=host, port=connect_port)

    return {"ok": bool(pair.get("ok")) and (connect is None or bool(connect.get("ok"))), "pair": pair, "connect": connect}


def adb_connect(*, host: str, port: int) -> dict[str, Any]:
    result = _run_adb(["connect", _target(host, port)], timeout=15)
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")
    connected = "connected to" in stdout.lower() or "already connected" in stdout.lower()
    return {**result, "ok": bool(result.get("ok")) and connected, "connected": connected, "address": _target(host, port)}


def complete_qr_pairing(
    *,
    service_name: str,
    password: str,
    timeout_seconds: float = 60.0,
    connect_after_pair: bool = True,
) -> dict[str, Any]:
    wanted = service_name.strip()
    secret = password.strip()
    if not wanted.startswith("studio-"):
        raise ValueError("二维码 service_name 必须以 studio- 开头")
    if not secret:
        raise ValueError("二维码 password 不能为空")

    deadline = time.monotonic() + max(5.0, min(timeout_seconds, 180.0))
    last_mdns: dict[str, Any] | None = None
    pairing_service: AdbMdnsService | None = None

    while time.monotonic() < deadline:
        mdns = adb_mdns_services()
        last_mdns = mdns
        for service in parse_mdns_services(mdns.get("stdout", "")):
            if service.service_type == "_adb-tls-pairing._tcp" and service.name == wanted:
                pairing_service = service
                break
        if pairing_service:
            break
        time.sleep(1.0)

    if not pairing_service:
        return {
            "ok": False,
            "message": "未发现手机二维码配对服务；如果 EasyTier 不转发 mDNS，请改用配对码模式。",
            "mdns": last_mdns,
        }

    pair = _run_adb(["pair", pairing_service.address, secret], timeout=30)
    connect = None
    if pair.get("ok") and connect_after_pair:
        connect = _connect_first_tls_service(pairing_service.host)
    return {
        "ok": bool(pair.get("ok")) and (connect is None or bool(connect.get("ok"))),
        "pairing_service": pairing_service.__dict__,
        "pair": pair,
        "connect": connect,
    }


def _connect_first_tls_service(host: str) -> dict[str, Any] | None:
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        mdns = adb_mdns_services()
        for service in parse_mdns_services(mdns.get("stdout", "")):
            if service.service_type == "_adb-tls-connect._tcp" and service.host == host:
                return adb_connect(host=service.host, port=service.port)
        time.sleep(1.0)
    return None
