"""EasyTier access profile for remote mobile devices.

The backend does not operate the phone-side agent directly.  It publishes the
authenticated access profile, including an EasyTier TOML config file that the
phone GUI can import to join the same EasyTier network.
"""

from __future__ import annotations

import json
import os
import ipaddress
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EasyTierAccessProfile:
    enabled: bool
    public_host: str
    network_name: str
    network_secret: str
    hostname: str
    virtual_cidr: str
    adb_port: int
    backend_peer_hostname: str
    backend_peer_ipv4: str
    phone_ipv4_cidr: str
    auto_scan_enabled: bool
    listeners: list[str]
    peers: list[str]
    agent_download_url: str
    android_download_url: str
    docs_url: str
    server_command: str
    phone_command: str
    config_filename: str
    config_toml: str
    config_payload: dict[str, Any]
    qr_payload: dict[str, Any]
    warnings: list[str]


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


_CONFIG_LOCK = threading.Lock()
_EASYTIER_CONFIG: dict[str, Any] = {}


def set_easytier_runtime_config(config: dict[str, Any] | None) -> None:
    """Inject Mongo-backed EasyTier config for sync mobile helpers."""
    global _EASYTIER_CONFIG
    with _CONFIG_LOCK:
        _EASYTIER_CONFIG = dict(config or {})


def get_easytier_runtime_config() -> dict[str, Any]:
    with _CONFIG_LOCK:
        return dict(_EASYTIER_CONFIG)


def _bool_env(name: str, default: bool = True) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _to_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_easytier_config_from_env(request_host: str | None = None) -> dict[str, Any]:
    """Build the persisted EasyTier config shape from current environment."""
    base_port = _to_int(_env("EASYTIER_PORT", "11010"), 11010)
    return {
        "enabled": _bool_env("EASYTIER_ENABLED", True),
        "public_host": _env("EASYTIER_PUBLIC_HOST") or _public_host_from_request_host(request_host),
        "network_name": _env("EASYTIER_NETWORK_NAME", "sere1nfish-mobile"),
        "network_secret": _env("EASYTIER_NETWORK_SECRET", "change-me-before-production"),
        "hostname": _env("EASYTIER_HOSTNAME", "sere1nfish-public-server"),
        "virtual_cidr": _env("EASYTIER_VIRTUAL_CIDR", "10.144.144.0/24"),
        "backend_peer_hostname": _env("EASYTIER_BACKEND_HOSTNAME", "sere1nfish-backend-peer"),
        "backend_peer_ipv4": _env("EASYTIER_BACKEND_IPV4", "10.144.144.1"),
        "backend_dev": _env("EASYTIER_BACKEND_DEV", "et0"),
        "backend_peer_container": _env("EASYTIER_BACKEND_PEER_CONTAINER", ""),
        "phone_ipv4_cidr": _env("EASYTIER_PHONE_IPV4_CIDR") or _env("EASYTIER_VIRTUAL_CIDR", "10.144.144.0/24"),
        "auto_scan_enabled": _bool_env("EASYTIER_AUTO_SCAN_ENABLED", True),
        "adb_scan_timeout": _to_float(_env("EASYTIER_ADB_SCAN_TIMEOUT", "0.25"), 0.25),
        "adb_scan_workers": _to_int(_env("EASYTIER_ADB_SCAN_WORKERS", "128"), 128),
        "adb_scan_max_hosts": _to_int(_env("EASYTIER_ADB_SCAN_MAX_HOSTS", "512"), 512),
        "port": base_port,
        "ws_port": _to_int(_env("EASYTIER_WS_PORT", str(base_port + 1)), base_port + 1),
        "wss_port": _to_int(_env("EASYTIER_WSS_PORT", str(base_port + 2)), base_port + 2),
        "wg_port": _to_int(_env("EASYTIER_WG_PORT", str(base_port + 3)), base_port + 3),
        "android_download_url": _env(
            "EASYTIER_ANDROID_DOWNLOAD_URL",
            "/api/v1/downloads/mobile/easytier/f4d0f795c2dc283fff573e29690cec54/easytier-v2.6.4-arm64.apk",
        ),
        "agent_download_url": _env("MOBILE_AGENT_ANDROID_URL", ""),
        "adb_port": _to_int(_env("MOBILE_AGENT_ADB_PORT", "5555"), 5555),
    }


def _effective_config(
    config: dict[str, Any] | None = None,
    request_host: str | None = None,
) -> dict[str, Any]:
    merged = build_easytier_config_from_env(request_host)
    merged.update(
        {
            key: value
            for key, value in get_easytier_runtime_config().items()
            if value is not None and value != ""
        }
    )
    if config:
        merged.update({key: value for key, value in config.items() if value is not None and value != ""})
    if not merged.get("public_host"):
        merged["public_host"] = _public_host_from_request_host(request_host)
    return merged


def _public_host_from_request_host(request_host: str | None) -> str:
    if not request_host:
        return ""
    return request_host.split(":", 1)[0]


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _safe_config_filename(name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    return f"{safe_name or 'easytier'}-android.toml"


def _strip_prefix(ipv4: str) -> str:
    return ipv4.strip().split("/", 1)[0]


_HEAL_LOCK = threading.Lock()
_LAST_HEAL_TS = 0.0
_HEAL_MIN_INTERVAL_SECONDS = 60.0


def _find_backend_peer_container(client: Any) -> Any:
    """按配置容器名或名称包含 easytier-backend-peer 定位 peer 容器。"""
    wanted = str(_effective_config().get("backend_peer_container") or "").strip()
    containers = client.containers.list()
    if wanted:
        for item in containers:
            if item.name == wanted:
                return item
    for item in containers:
        if "easytier-backend-peer" in item.name:
            return item
    return None


def easytier_network_healthy() -> bool:
    """组网健康 = backend 命名空间内 EasyTier 虚拟网卡(默认 et0)存在。

    backend 重启后 backend-peer 仍绑在失效旧命名空间时,新命名空间里该网卡消失,
    据此判定组网断开。
    """
    dev = str(_effective_config().get("backend_dev") or "et0").strip() or "et0"
    return os.path.exists(f"/sys/class/net/{dev}")


def ensure_easytier_healthy() -> dict[str, Any]:
    """自愈:组网断开(et0 缺失)时重启 backend-peer 容器。

    根因:backend-peer 用 network_mode: service:backend 共享 backend 网络命名空间,
    backend 重启后 peer 绑在失效旧命名空间且不会自行退出,Docker restart 策略救不了,
    需主动重启 backend-peer,使其重新绑定新命名空间、重建 et0、重连 easytier-server。
    带最小重启间隔防抖,避免误判时频繁重启。
    """
    global _LAST_HEAL_TS
    cfg = _effective_config()
    if not _to_bool(cfg.get("enabled"), True):
        return {"enabled": False, "healthy": None, "healed": False}
    dev = str(cfg.get("backend_dev") or "et0").strip() or "et0"
    if easytier_network_healthy():
        return {"enabled": True, "healthy": True, "healed": False, "dev": dev}
    now = time.time()
    with _HEAL_LOCK:
        if now - _LAST_HEAL_TS < _HEAL_MIN_INTERVAL_SECONDS:
            return {"enabled": True, "healthy": False, "healed": False, "reason": "cooldown", "dev": dev}
        _LAST_HEAL_TS = now
    try:
        import docker  # type: ignore[import-untyped]

        client = docker.from_env()
        container = _find_backend_peer_container(client)
        if container is None:
            return {"enabled": True, "healthy": False, "healed": False, "error": "backend-peer 容器未找到", "dev": dev}
        container.restart(timeout=10)
        return {"enabled": True, "healthy": False, "healed": True, "container": container.name, "dev": dev}
    except Exception as exc:  # noqa: BLE001
        return {"enabled": True, "healthy": False, "healed": False, "error": str(exc), "dev": dev}


def _run_easytier_cli_json(command: str) -> list[dict[str, Any]]:
    args = ["easytier-cli", "-o", "json", "--no-trunc", command]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=8, check=False)
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            return data if isinstance(data, list) else []
    except FileNotFoundError:
        pass
    except Exception:
        return []

    try:
        import docker  # type: ignore[import-untyped]

        client = docker.from_env()
        container = _find_backend_peer_container(client)
        if container is None:
            return []

        result = container.exec_run(args, stdout=True, stderr=True)
        output = result.output.decode("utf-8", errors="replace") if isinstance(result.output, bytes) else str(result.output)
        if result.exit_code != 0 or not output.strip():
            return []
        data = json.loads(output)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _valid_ipv4_cidr(cidr: str) -> bool:
    try:
        return ipaddress.ip_network(cidr, strict=False).version == 4
    except ValueError:
        return False


def _cidr_contains_ipv4(cidr: str, ipv4: str) -> bool:
    try:
        return ipaddress.ip_address(_strip_prefix(ipv4)) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


def list_easytier_peer_candidates() -> list[dict[str, Any]]:
    """Return EasyTier peers that have joined the virtual mobile network.

    The backend container does not contain easytier-cli, while the EasyTier peer
    container does.  We first support a local CLI for dev, then fall back to the
    Docker socket mounted in the backend service.
    """
    cfg = _effective_config()
    if not _to_bool(cfg.get("enabled"), True):
        return []

    backend_ipv4 = _strip_prefix(str(cfg.get("backend_peer_ipv4") or "10.144.144.1"))
    backend_hostname = str(cfg.get("backend_peer_hostname") or "sere1nfish-backend-peer")
    public_hostname = str(cfg.get("hostname") or "sere1nfish-public-server")
    virtual_cidr = str(cfg.get("virtual_cidr") or "10.144.144.0/24")
    adb_port = _to_int(cfg.get("adb_port"), 5555)

    try:
        network = ipaddress.ip_network(virtual_cidr, strict=False)
    except ValueError:
        network = None

    candidates: list[dict[str, Any]] = []
    for item in _run_easytier_cli_json("peer"):
        cidr = str(item.get("cidr") or item.get("ipv4") or "").strip()
        ip = _strip_prefix(cidr)
        hostname = str(item.get("hostname") or "").strip()
        if not ip:
            continue
        if ip == backend_ipv4 or hostname in {backend_hostname, public_hostname}:
            continue
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if network is not None and address not in network:
            continue
        candidates.append(
            {
                "id": str(item.get("id") or ""),
                "hostname": hostname or ip,
                "ipv4": ip,
                "cidr": cidr,
                "adb_port": adb_port,
                "cost": item.get("cost"),
                "lat_ms": item.get("lat_ms"),
                "loss_rate": item.get("loss_rate"),
                "rx_bytes": item.get("rx_bytes"),
                "tx_bytes": item.get("tx_bytes"),
                "tunnel_proto": item.get("tunnel_proto"),
                "nat_type": item.get("nat_type"),
                "version": item.get("version"),
                "source": "easytier-peer",
            }
        )
    return candidates


def _build_phone_config_toml(
    *,
    network_name: str,
    network_secret: str,
    hostname: str,
    ipv4_cidr: str,
    peers: list[str],
) -> str:
    lines = [
        "# Sere1nFish EasyTier Android import config.",
        "# 这个文件可以直接导入 EasyTier；导入后仍需要在 App 内保存并启动。",
        "# 如果 GUI 导入成功但字段没有写入，请使用自定义/文本配置模式粘贴本文件。",
        "",
        "# instance_name: 本机 EasyTier 实例名，可按手机修改，但不要留空。",
        f"instance_name = {_toml_string('sere1nfish-mobile')}",
        "# hostname: 手机节点名称，建议每台手机不同，例如 test1、test2。",
        f"hostname = {_toml_string(hostname)}",
        "# ipv4: EasyTier DHCP 地址池。这里指定固定 C 段，不是单个固定 IP。",
        f"ipv4 = {_toml_string(ipv4_cidr)}",
        "# dhcp: 必须保留 true，手机会从上面的 ipv4 网段里自动分配地址。",
        "dhcp = true",
        "",
        "# listeners: 手机侧不需要公网入站监听，保持空列表即可。",
        "listeners = []",
        "# tcp_whitelist/udp_whitelist: 空列表表示不额外开放本机端口白名单。",
        "tcp_whitelist = []",
        "udp_whitelist = []",
        "",
        "# [network_identity] 不能删除。",
        "# network_name 和 network_secret 必须与服务器完全一致，否则不会进入同一个网络。",
        "[network_identity]",
        f"network_name = {_toml_string(network_name)}",
        f"network_secret = {_toml_string(network_secret)}",
    ]

    for index, peer in enumerate(peers, start=1):
        lines.extend(
            [
                "",
                f"# peer {index}: 公网 EasyTier 节点。[[peer]] 段不能全部删除，至少保留一个可访问的 peer。",
                "[[peer]]",
                f"uri = {_toml_string(peer)}",
            ]
        )

    lines.extend(
        [
            "",
            "# flags: EasyTier 可选参数段，当前无需填写。",
            "[flags]",
            "",
        ]
    )
    return "\n".join(lines)


def build_easytier_access_profile(
    request_host: str | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> EasyTierAccessProfile:
    cfg = _effective_config(config, request_host)
    public_host = str(cfg.get("public_host") or "")
    network_name = str(cfg.get("network_name") or "sere1nfish-mobile")
    network_secret = str(cfg.get("network_secret") or "change-me-before-production")
    hostname = str(cfg.get("hostname") or "sere1nfish-public-server")
    virtual_cidr = str(cfg.get("virtual_cidr") or "10.144.144.0/24")
    adb_port = _to_int(cfg.get("adb_port"), 5555)
    backend_peer_hostname = str(cfg.get("backend_peer_hostname") or "sere1nfish-backend-peer")
    backend_peer_ipv4 = str(cfg.get("backend_peer_ipv4") or "10.144.144.1")
    phone_ipv4_cidr = str(cfg.get("phone_ipv4_cidr") or virtual_cidr)
    auto_scan_enabled = _to_bool(cfg.get("auto_scan_enabled"), True)
    base_port = _to_int(cfg.get("port"), 11010)
    ws_port = _to_int(cfg.get("ws_port"), base_port + 1)
    wss_port = _to_int(cfg.get("wss_port"), base_port + 2)
    wg_port = _to_int(cfg.get("wg_port"), base_port + 3)
    docs_url = "https://easytier.cn/guide/network/host-public-server.html"
    android_download_url = str(
        cfg.get("android_download_url")
        or "/api/v1/downloads/mobile/easytier/f4d0f795c2dc283fff573e29690cec54/easytier-v2.6.4-arm64.apk"
    )
    agent_download_url = str(cfg.get("agent_download_url") or "")

    listeners = [
        f"tcp://0.0.0.0:{base_port}",
        f"udp://0.0.0.0:{base_port}",
        f"ws://0.0.0.0:{ws_port}",
        f"wss://0.0.0.0:{wss_port}",
        f"wg://0.0.0.0:{wg_port}",
    ]
    peers = (
        [
            f"tcp://{public_host}:{base_port}",
            f"udp://{public_host}:{base_port}",
            f"ws://{public_host}:{ws_port}",
            f"wss://{public_host}:{wss_port}",
            f"wg://{public_host}:{wg_port}",
        ]
        if public_host
        else []
    )

    server_command = (
        "easytier-core "
        f"--listeners tcp://0.0.0.0:{base_port} "
        f"--listeners udp://0.0.0.0:{base_port} "
        f"--listeners ws://0.0.0.0:{ws_port} "
        f"--listeners wss://0.0.0.0:{wss_port} "
        f"--listeners wg://0.0.0.0:{wg_port} "
        f"--private-mode true --hostname {hostname} "
        f"--network-name {network_name} --network-secret {network_secret} "
        f"--relay-network-whitelist {network_name} --relay-all-peer-rpc"
    )
    phone_command = (
        f"easytier-core -d -i {phone_ipv4_cidr} "
        f"--network-name {network_name} --network-secret {network_secret} "
        f"-p {peers[0] if peers else 'tcp://<PUBLIC_IP>:11010'}"
    )
    config_payload = {
        "schema": "sere1nfish.mobile.easytier.v1",
        "network": {
            "name": network_name,
            "secret": network_secret,
            "virtual_cidr": virtual_cidr,
            "phone_ipv4_cidr": phone_ipv4_cidr,
            "peers": peers,
            "adb_port": adb_port,
        },
        "discovery": {
            "modes": ["mdns", "easytier_adb_scan"] if auto_scan_enabled else ["mdns"],
            "adb_port": adb_port,
            "backend_peer_ipv4": backend_peer_ipv4,
            "auto_scan": auto_scan_enabled,
        },
        "agent": {
            "download_url": agent_download_url,
            "android_download_url": android_download_url,
            "post_join_action": "enable_agent_discovery_wireless_adb_or_adb_scan",
        },
        "server": {
            "public_host": public_host,
            "hostname": hostname,
            "backend_peer_hostname": backend_peer_hostname,
            "backend_peer_ipv4": backend_peer_ipv4,
            "docs_url": docs_url,
        },
    }
    config_filename = _safe_config_filename(network_name)
    config_toml = _build_phone_config_toml(
        network_name=network_name,
        network_secret=network_secret,
        hostname="sere1nfish-android",
        ipv4_cidr=phone_ipv4_cidr,
        peers=peers,
    )

    warnings: list[str] = []
    if not public_host:
        warnings.append("未配置 EASYTIER_PUBLIC_HOST，配置文件中的公网 peer 为空。")
    if network_secret == "change-me-before-production":
        warnings.append("EASYTIER_NETWORK_SECRET 仍为默认值，生产环境必须改为高强度密钥。")
    if not _valid_ipv4_cidr(phone_ipv4_cidr):
        warnings.append("EASYTIER_PHONE_IPV4_CIDR 不是有效 IPv4 网段，手机 DHCP 分配可能失败。")
    if phone_ipv4_cidr != virtual_cidr:
        warnings.append("EASYTIER_PHONE_IPV4_CIDR 与 EASYTIER_VIRTUAL_CIDR 不一致，后端自动扫描可能发现不了手机。")
    if not _cidr_contains_ipv4(phone_ipv4_cidr, backend_peer_ipv4):
        warnings.append("EASYTIER_BACKEND_IPV4 不在手机 DHCP 网段内，请确认后端 peer 与手机处于同一虚拟网段。")
    if not agent_download_url:
        warnings.append("未配置 MOBILE_AGENT_ANDROID_URL，前端只能展示 EasyTier 下载页，无法提供项目 Agent APK 直链。")

    return EasyTierAccessProfile(
        enabled=_to_bool(cfg.get("enabled"), True),
        public_host=public_host,
        network_name=network_name,
        network_secret=network_secret,
        hostname=hostname,
        virtual_cidr=virtual_cidr,
        adb_port=adb_port,
        backend_peer_hostname=backend_peer_hostname,
        backend_peer_ipv4=backend_peer_ipv4,
        phone_ipv4_cidr=phone_ipv4_cidr,
        auto_scan_enabled=auto_scan_enabled,
        listeners=listeners,
        peers=peers,
        agent_download_url=agent_download_url,
        android_download_url=android_download_url,
        docs_url=docs_url,
        server_command=server_command,
        phone_command=phone_command,
        config_filename=config_filename,
        config_toml=config_toml,
        config_payload=config_payload,
        qr_payload=config_payload,
        warnings=warnings,
    )
