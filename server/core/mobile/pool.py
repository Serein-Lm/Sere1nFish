"""
系统1 — 手机资源池 + 网络组网接入。

- 资源池:聚合「已连接 + mDNS 自动发现」设备,叠加占用状态;
- 组网接入:复用 AutoGLM device_manager 的 WiFi/远程接入能力
  (connect_wifi_manual / connect_wifi / add_remote_device / discover_remote_devices);
  easytier 把远程手机 adb 组进同网后,用 connect_wifi_manual(ip, port) 即可纳入池;
- 独占:同一设备同一时刻只允许一个 owner 占用(申请/释放),供 AI 任务/自动聊天排他使用。
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import os
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from AutoGLM_GUI.device_manager import DeviceManager

from core.mobile.manager import MobileDeviceManager
from core.mobile.identity import resolve_device_key


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _easytier_config() -> dict[str, Any]:
    try:
        from core.mobile.easytier import get_easytier_runtime_config

        return get_easytier_runtime_config()
    except Exception:
        return {}


def _easytier_value(key: str, env_name: str, default: str = "") -> Any:
    config = _easytier_config()
    value = config.get(key)
    if value is not None and value != "":
        return value
    return os.getenv(env_name, default).strip()


def _bool_config(key: str, env_name: str, default: bool) -> bool:
    value = _easytier_value(key, env_name, "")
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _int_config(key: str, env_name: str, default: int) -> int:
    value = _easytier_value(key, env_name, "")
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_config(key: str, env_name: str, default: float) -> float:
    value = _easytier_value(key, env_name, "")
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class PoolError(RuntimeError):
    """资源池操作错误(如设备已被占用)。"""


@dataclass
class Reservation:
    device_key: str  # 稳定 key（ro.serialno），掉线重连不变
    owner: str
    since: float = field(default_factory=time.time)
    note: str = ""
    device_id: str = ""  # 最近一次的 adb id（仅参考）


class DevicePool:
    """设备资源池(单例)。"""

    _instance: "DevicePool | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._mgr = MobileDeviceManager()
        self._mgr.start_polling()
        self._reservations: dict[str, Reservation] = {}
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "DevicePool":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @staticmethod
    def _dm() -> DeviceManager:
        return DeviceManager.get_instance()

    # ── 资源池全景 ──

    def list_pool(self) -> list[dict[str, Any]]:
        """读取资源池缓存:设备 + 稳定 key + 在线/占用状态。

        占用按稳定 key 匹配,因此 WiFi 设备 ip:port 变化重连后占用仍能对应。
        设备管理器已在后台轮询；列表请求不能同步强刷 ADB，否则单台失联设备会
        用探测超时阻塞整个设备池。主动发现和重连由 auto-connect/keepalive 负责。
        """
        devices = self._mgr.list_devices()
        # 先在锁外解析 key(含 adb getprop,已缓存),避免阻塞占用申请/释放
        keyed = [
            (
                d,
                resolve_device_key(d.device_id)
                if d.status == "device"
                else d.device_id,
            )
            for d in devices
        ]
        connected_ips = {
            d.device_id.split(":", 1)[0]
            for d in devices
            if d.status == "device"
            and ":" in d.device_id
            and not d.device_id.startswith("easytier:")
        }
        connected_ips.update(self._connected_adb_ips())
        easytier_peers = self._easytier_peers()
        peer_by_ip = {
            str(peer["ipv4"]): peer
            for peer in easytier_peers
            if peer.get("ipv4")
        }
        pairing_candidates = [
            peer for peer in easytier_peers if peer.get("ipv4") not in connected_ips
        ]
        linked = []
        for d, key in keyed:
            endpoint = self._mgr.resolve_adb_device_id(d.device_id)
            network_ip = self._endpoint_ipv4(endpoint)
            linked.append((d, key, network_ip, peer_by_ip.get(network_ip or "")))
        out: list[dict[str, Any]] = []
        with self._lock:
            for d, key, network_ip, peer in linked:
                r = self._reservations.get(key)
                item = {
                    "device_id": d.device_id,
                    "device_key": key,
                    "status": d.status,
                    "model": d.model,
                    "connection_type": d.connection_type,
                    "online": d.status == "device",
                    "reserved": r is not None,
                    "owner": r.owner if r else None,
                    "since": r.since if r else None,
                    "note": r.note if r else None,
                }
                if peer:
                    item["network_ip"] = network_ip
                    item["easytier_peer"] = peer
                out.append(item)
            for peer in pairing_candidates:
                key = f"easytier:{peer['ipv4']}"
                r = self._reservations.get(key)
                out.append(
                    {
                        "device_id": key,
                        "device_key": key,
                        "status": "pairing_required",
                        "model": peer.get("hostname") or peer["ipv4"],
                        "connection_type": "easytier",
                        "online": False,
                        "reserved": r is not None,
                        "owner": r.owner if r else None,
                        "since": r.since if r else None,
                        "note": r.note if r else None,
                        "network_ip": peer["ipv4"],
                        "pairing_required": True,
                        "pairing_available": True,
                        "easytier_peer": peer,
                    }
                )
        return out

    def reservation_of(self, device_key: str) -> Reservation | None:
        with self._lock:
            return self._reservations.get(device_key)

    # ── 申请 / 释放(独占,按稳定 key)──

    def acquire(
        self, device_key: str, owner: str, note: str = "", *, device_id: str = ""
    ) -> Reservation:
        """申请独占一个设备(按稳定 key)。已被他人占用则抛 PoolError。"""
        with self._lock:
            existing = self._reservations.get(device_key)
            if existing and existing.owner != owner:
                raise PoolError(
                    f"设备 {device_id or device_key} 已被 {existing.owner} 占用"
                )
            res = Reservation(
                device_key=device_key, owner=owner, note=note, device_id=device_id
            )
            self._reservations[device_key] = res
            return res

    def acquire_for_task(
        self,
        device_key: str,
        owner: str,
        *,
        initiated_by: str = "",
        note: str = "",
        device_id: str = "",
    ) -> Reservation:
        """原子申请后台任务租约。

        未占用时直接申请；若设备由本次任务的发起用户占用，则把交互租约
        原子转交给后台 owner。其他用户或其他后台任务的租约不会被抢占。
        """
        with self._lock:
            existing = self._reservations.get(device_key)
            allowed_owners = {owner}
            if initiated_by:
                allowed_owners.add(initiated_by)
            if existing and existing.owner not in allowed_owners:
                raise PoolError(
                    f"设备 {device_id or device_key} 已被 {existing.owner} 占用"
                )
            res = Reservation(
                device_key=device_key,
                owner=owner,
                note=note,
                device_id=device_id,
            )
            self._reservations[device_key] = res
            return res

    def release(self, device_key: str, owner: str, *, force: bool = False) -> bool:
        """释放占用(按稳定 key)。非本人且非 force 时抛 PoolError。"""
        with self._lock:
            existing = self._reservations.get(device_key)
            if existing is None:
                return False
            if not force and existing.owner != owner:
                raise PoolError(
                    f"设备 {device_key} 属于 {existing.owner},无权释放"
                )
            self._reservations.pop(device_key, None)
            return True

    def ensure_owner(self, device_key: str, owner: str) -> None:
        """校验 owner 持有该设备(供 AI 任务/自动聊天前置检查)。未占用视为允许。"""
        with self._lock:
            existing = self._reservations.get(device_key)
            if existing and existing.owner != owner:
                raise PoolError(
                    f"设备 {device_key} 已被 {existing.owner} 占用,请先申请"
                )

    # ── 组网接入(复用 AutoGLM)──

    def connect_wifi_manual(self, ip: str, port: int = 5555) -> dict[str, Any]:
        """直接以 ip:port 接入(easytier 组网后的远程手机走这里)。"""
        ok, msg, addr = self._dm().connect_wifi_manual(ip, port)
        self._mgr.refresh()
        return {"ok": ok, "message": msg, "address": addr}

    def connect_wifi_from_usb(self, device_id: str, port: int = 5555) -> dict[str, Any]:
        """把已连 USB 的设备切到 WiFi(便于拔线后远程)。"""
        ok, msg, addr = self._dm().connect_wifi(device_id, port)
        self._mgr.refresh()
        return {"ok": ok, "message": msg, "address": addr}

    def disconnect(self, device_id: str) -> dict[str, Any]:
        ok, msg = self._dm().disconnect_wifi(device_id)
        self._mgr.refresh()
        return {"ok": ok, "message": msg}

    def discover_remote(self, base_url: str) -> dict[str, Any]:
        """从远程 Device Agent Server 发现设备。"""
        ok, msg, devices = self._dm().discover_remote_devices(base_url)
        return {"ok": ok, "message": msg, "devices": devices}

    def add_remote(self, base_url: str, device_id: str) -> dict[str, Any]:
        """添加远程 HTTP 代理设备到池。"""
        ok, msg, serial = self._dm().add_remote_device(base_url, device_id)
        self._mgr.refresh()
        return {"ok": ok, "message": msg, "serial": serial}

    def remove_remote(self, serial: str) -> dict[str, Any]:
        ok, msg = self._dm().remove_remote_device(serial)
        self._mgr.refresh()
        return {"ok": ok, "message": msg}

    # ── 自动接入(闭环：发现 → 自动 connect) ──

    @staticmethod
    def _easytier_targets() -> tuple[list[str], list[dict[str, Any]]]:
        """Build the EasyTier virtual-CIDR scan target list."""
        cidr = str(_easytier_value("virtual_cidr", "EASYTIER_VIRTUAL_CIDR", "10.144.144.0/24")).strip()
        max_hosts = _int_config("adb_scan_max_hosts", "EASYTIER_ADB_SCAN_MAX_HOSTS", 512)
        backend_ipv4 = str(
            _easytier_value("backend_peer_ipv4", "EASYTIER_BACKEND_IPV4", "10.144.144.1")
        ).strip().split("/", 1)[0]
        errors: list[dict[str, Any]] = []

        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            return [], [
                {
                    "source": "easytier-scan",
                    "address": cidr,
                    "ok": False,
                    "message": f"EASYTIER_VIRTUAL_CIDR 无效: {exc}",
                }
            ]

        if network.version != 4:
            return [], [
                {
                    "source": "easytier-scan",
                    "address": cidr,
                    "ok": False,
                    "message": "仅支持 IPv4 EasyTier 网段扫描",
                }
            ]
        if network.num_addresses > max_hosts + 2:
            return [], [
                {
                    "source": "easytier-scan",
                    "address": cidr,
                    "ok": False,
                    "message": (
                        f"EasyTier 网段过大({network.num_addresses} 地址)，"
                        f"请调高 EASYTIER_ADB_SCAN_MAX_HOSTS 或缩小 EASYTIER_VIRTUAL_CIDR"
                    ),
                }
            ]

        excluded: set[ipaddress.IPv4Address] = set()
        try:
            excluded.add(ipaddress.ip_address(backend_ipv4))  # type: ignore[arg-type]
        except ValueError:
            errors.append(
                {
                    "source": "easytier-scan",
                    "address": backend_ipv4,
                    "ok": False,
                    "message": "EASYTIER_BACKEND_IPV4 无效，扫描时未排除后端 peer",
                }
            )

        targets = [str(host) for host in network.hosts() if host not in excluded]
        return targets, errors

    @staticmethod
    def _tcp_port_open(ip: str, port: int, timeout: float) -> bool:
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return True
        except OSError:
            return False

    @staticmethod
    def _easytier_peers() -> list[dict[str, Any]]:
        try:
            from core.mobile.easytier import list_easytier_peer_candidates

            return list_easytier_peer_candidates()
        except Exception:
            return []

    @classmethod
    def _easytier_pairing_candidates(
        cls, exclude_ips: set[str] | None = None
    ) -> list[dict[str, Any]]:
        exclude = exclude_ips or set()
        return [
            peer
            for peer in cls._easytier_peers()
            if peer.get("ipv4") and peer.get("ipv4") not in exclude
        ]

    @staticmethod
    def _endpoint_ipv4(device_id: str) -> str | None:
        host = str(device_id or "").rsplit(":", 1)[0]
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return None
        return str(address) if address.version == 4 else None

    def _connected_adb_ips(self) -> set[str]:
        ips: set[str] = set()
        try:
            for dev in self._dm().get_connected_devices():
                if not getattr(dev, "online", False):
                    continue
                for conn in getattr(dev, "connections", []) or []:
                    if getattr(conn, "status", "") != "device":
                        continue
                    endpoint = getattr(conn, "device_id", "") or ""
                    if ":" in endpoint:
                        ips.add(endpoint.split(":", 1)[0])
        except Exception:
            return ips
        return ips

    def has_unconnected_easytier_peers(self) -> bool:
        """Return whether an EasyTier phone is joined but not attached to ADB."""
        connected_ips = self._connected_adb_ips()
        return any(
            str(peer.get("ipv4") or "") not in connected_ips
            for peer in self._easytier_peers()
            if peer.get("ipv4")
        )

    def _scan_easytier_adb(self) -> dict[str, Any]:
        """Scan the EasyTier virtual CIDR for open wireless-ADB endpoints."""
        if not _bool_config("enabled", "EASYTIER_ENABLED", True):
            return {
                "enabled": False,
                "connected": [],
                "errors": [],
                "scanned": 0,
                "open": 0,
                "cidr": str(_easytier_value("virtual_cidr", "EASYTIER_VIRTUAL_CIDR", "10.144.144.0/24")).strip(),
                "port": _int_config("adb_port", "MOBILE_AGENT_ADB_PORT", 5555),
            }
        if not _bool_config("auto_scan_enabled", "EASYTIER_AUTO_SCAN_ENABLED", True):
            return {
                "enabled": False,
                "connected": [],
                "errors": [],
                "scanned": 0,
                "open": 0,
                "cidr": str(_easytier_value("virtual_cidr", "EASYTIER_VIRTUAL_CIDR", "10.144.144.0/24")).strip(),
                "port": _int_config("adb_port", "MOBILE_AGENT_ADB_PORT", 5555),
            }

        port = _int_config("adb_port", "MOBILE_AGENT_ADB_PORT", 5555)
        cidr = str(_easytier_value("virtual_cidr", "EASYTIER_VIRTUAL_CIDR", "10.144.144.0/24")).strip()
        timeout = _float_config("adb_scan_timeout", "EASYTIER_ADB_SCAN_TIMEOUT", 0.25)
        workers = max(1, _int_config("adb_scan_workers", "EASYTIER_ADB_SCAN_WORKERS", 128))
        targets, errors = self._easytier_targets()
        if not targets:
            return {
                "enabled": True,
                "connected": [],
                "errors": errors,
                "scanned": 0,
                "open": 0,
                "cidr": cidr,
                "port": port,
            }

        open_hosts: list[str] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(workers, len(targets))
        ) as executor:
            futures = {
                executor.submit(self._tcp_port_open, ip, port, timeout): ip
                for ip in targets
            }
            for future in concurrent.futures.as_completed(futures):
                ip = futures[future]
                try:
                    if future.result():
                        open_hosts.append(ip)
                except Exception as exc:  # noqa: BLE001
                    errors.append(
                        {
                            "source": "easytier-scan",
                            "address": f"{ip}:{port}",
                            "ok": False,
                            "message": str(exc),
                        }
                    )

        connected: list[dict[str, Any]] = []
        connect_errors: list[dict[str, Any]] = []
        for ip in sorted(open_hosts, key=lambda item: ipaddress.ip_address(item)):
            addr = f"{ip}:{port}"
            try:
                res = self.connect_wifi_manual(ip, port)
                entry = {
                    "source": "easytier-scan",
                    "serial": None,
                    "address": addr,
                    **res,
                }
                (connected if res.get("ok") else connect_errors).append(entry)
            except Exception as exc:  # noqa: BLE001
                connect_errors.append(
                    {
                        "source": "easytier-scan",
                        "serial": None,
                        "address": addr,
                        "ok": False,
                        "message": str(exc),
                    }
                )

        return {
            "enabled": True,
            "connected": connected,
            "errors": [*errors, *connect_errors],
            "scanned": len(targets),
            "open": len(open_hosts),
            "cidr": cidr,
            "port": port,
        }

    def auto_connect_discovered(self) -> dict[str, Any]:
        """把 mDNS 和 EasyTier 发现的可用设备自动 adb connect 接入资源池。

        实现“自动发现 adb 接入资源池”的闭环。手机入 EasyTier 后不需要
        手工填写虚拟 IP；自动接入会扫描虚拟网段上的无线 ADB 端口。
        """
        from core.mobile.easytier import ensure_easytier_healthy

        network_health = ensure_easytier_healthy()
        self._mgr.refresh()
        dm = self._dm()
        connected: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        if network_health.get("enabled") and network_health.get("healthy") is False:
            errors.append(
                {
                    "source": "easytier-health",
                    "address": None,
                    "ok": False,
                    "message": str(
                        network_health.get("error")
                        or network_health.get("reason")
                        or "EasyTier 组网尚未恢复"
                    ),
                }
            )
        sources = {"mdns": 0, "easytier_scan": 0}
        for dev in dm.get_devices():
            state_name = getattr(getattr(dev, "state", None), "name", "")
            if state_name != "AVAILABLE_MDNS":
                continue
            addr = None
            for conn in getattr(dev, "connections", []) or []:
                cid = getattr(conn, "device_id", "") or ""
                if ":" in cid:
                    addr = cid
                    break
            if not addr:
                continue
            ip, _, port = addr.partition(":")
            try:
                res = self.connect_wifi_manual(ip, int(port or 5555))
                entry = {
                    "source": "mdns",
                    "serial": getattr(dev, "serial", None),
                    "address": addr,
                    **res,
                }
                if res.get("ok"):
                    connected.append(entry)
                    sources["mdns"] += 1
                else:
                    errors.append(entry)
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "source": "mdns",
                        "serial": getattr(dev, "serial", None),
                        "address": addr,
                        "ok": False,
                        "message": str(exc),
                    }
                )

        scan = self._scan_easytier_adb()
        scan_connected = scan.get("connected", [])
        connected.extend(scan_connected)
        errors.extend(scan.get("errors", []))
        sources["easytier_scan"] = len(scan_connected)
        pairing_candidates = self._easytier_pairing_candidates(
            {
                str(entry.get("address", "")).split(":", 1)[0]
                for entry in connected
                if entry.get("address")
            }
        )

        return {
            "connected": connected,
            "errors": errors,
            "count": len(connected),
            "sources": sources,
            "scan": {
                "enabled": scan.get("enabled", False),
                "scanned": scan.get("scanned", 0),
                "open": scan.get("open", 0),
                "cidr": scan.get("cidr"),
                "port": scan.get("port"),
                "pairing_candidates": len(pairing_candidates),
            },
            "pairing_candidates": pairing_candidates,
        }

    # ── 独占持久化恢复 ──

    def load_reservations(self, items: list[dict[str, Any]]) -> int:
        """从持久化记录恢复内存预约(启动时调用)。按稳定 key 恢复。"""
        n = 0
        with self._lock:
            for it in items:
                dk = it.get("device_key")
                owner = it.get("owner")
                if not dk or not owner:
                    continue  # 旧版仅含 device_id 的记录无稳定 key,跳过
                self._reservations[dk] = Reservation(
                    device_key=dk,
                    owner=owner,
                    since=it.get("since") or time.time(),
                    note=it.get("note") or "",
                    device_id=it.get("device_id") or "",
                )
                n += 1
        return n

    # ── 唤醒 / 保持唤醒 ──

    @staticmethod
    def _adb_shell(
        device_id: str, args: list[str], timeout: int = 10
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["adb", "-s", device_id, "shell", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def wake(self, device_id: str, *, stay_on: bool = False) -> dict[str, Any]:
        """唤醒屏幕(KEYCODE_WAKEUP=224)。可选 stay_on 充电时常亮。

        说明:手机无法像 PC 那样 Wake-on-LAN 真正“远程开机”;
        这里是唤醒亮屏 + 保持唤醒,配合无线 ADB 常连即可远程操作。
        """
        adb_device_id = self._mgr.resolve_adb_device_id(device_id)
        out: dict[str, Any] = {}
        try:
            r = self._adb_shell(adb_device_id, ["input", "keyevent", "224"])
            out["wake"] = {"ok": r.returncode == 0, "stderr": r.stderr.strip()}
        except Exception as exc:  # noqa: BLE001
            out["wake"] = {"ok": False, "error": str(exc)}
        if stay_on:
            try:
                r2 = self._adb_shell(
                    adb_device_id, ["svc", "power", "stayon", "true"]
                )
                out["stay_on"] = {"ok": r2.returncode == 0, "stderr": r2.stderr.strip()}
            except Exception as exc:  # noqa: BLE001
                out["stay_on"] = {"ok": False, "error": str(exc)}
        out["ok"] = bool(out.get("wake", {}).get("ok"))
        return out

    def wake_and_unlock(
        self, device_id: str, *, pin: str | None = None, stay_on: bool = True
    ) -> dict[str, Any]:
        """唤醒并尝试滑开锁屏；可选输入一次性 PIN。

        限制: 设备必须已开机且 ADB/Agent 在线。该方法不会保存 PIN，也不能绕过
        Android 安全策略；生物识别、强安全锁或企业策略拦截时仍需人工/Agent 权限。
        """
        out = self.wake(device_id, stay_on=stay_on)
        adb_device_id = self._mgr.resolve_adb_device_id(device_id)
        steps: list[dict[str, Any]] = []

        time.sleep(0.2)
        locked = self._keyguard_showing(adb_device_id)
        out["lock_state"] = {"checked": locked is not None, "locked": locked}
        if locked is False:
            out["unlock"] = steps
            out["unlocked"] = True
            return out

        commands = [
            ("menu", ["input", "keyevent", "82"]),
            ("swipe_unlock", ["input", "swipe", "500", "1800", "500", "300", "250"]),
        ]
        if pin:
            commands.extend(
                [
                    ("pin", ["input", "text", pin]),
                    ("enter", ["input", "keyevent", "66"]),
                ]
            )

        for name, args in commands:
            try:
                r = self._adb_shell(adb_device_id, args)
                steps.append(
                    {
                        "step": name,
                        "ok": r.returncode == 0,
                        "stderr": r.stderr.strip(),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                steps.append({"step": name, "ok": False, "error": str(exc)})
        out["unlock"] = steps
        time.sleep(0.2)
        final_locked = self._keyguard_showing(adb_device_id)
        out["unlocked"] = final_locked is False if final_locked is not None else None
        out["ok"] = bool(out.get("wake", {}).get("ok")) and all(
            step.get("ok") for step in steps
        )
        return out

    def _keyguard_showing(self, device_id: str) -> bool | None:
        """Return the Android keyguard state without assuming an OEM UI layout."""
        try:
            result = self._adb_shell(device_id, ["dumpsys", "window", "policy"])
        except Exception:
            return None
        if result.returncode != 0:
            return None
        output = result.stdout or ""
        delegate = re.search(
            r"KeyguardServiceDelegate.*?^\s*showing=(true|false)\s*$",
            output,
            flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        if delegate:
            return delegate.group(1).lower() == "true"
        legacy = re.search(
            r"(?:mShowingLockscreen|mDreamingLockscreen|isStatusBarKeyguard)=(true|false)",
            output,
            flags=re.IGNORECASE,
        )
        if legacy:
            return legacy.group(1).lower() == "true"
        return None

    def set_stay_awake(self, device_id: str, on: bool) -> dict[str, Any]:
        """设置充电时是否常亮(svc power stayon)。"""
        adb_device_id = self._mgr.resolve_adb_device_id(device_id)
        try:
            r = self._adb_shell(
                adb_device_id, ["svc", "power", "stayon", "true" if on else "false"]
            )
            return {"ok": r.returncode == 0, "stderr": r.stderr.strip()}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    # ── 保活(心跳 / 断线重连 / 屏幕常亮)──

    def _adb_ping(self, adb_device_id: str, timeout: int = 5) -> bool:
        """心跳探测:adb shell echo ok。比 adb get-state 更能反映真实可用性。"""
        try:
            r = self._adb_shell(adb_device_id, ["echo", "ok"], timeout=timeout)
            return r.returncode == 0 and "ok" in (r.stdout or "")
        except Exception:  # noqa: BLE001
            return False

    def _reconnect_ports(self, base_port: int) -> list[int]:
        """重连候选端口:旧端口 + easytier 固定端口 + 常见无线调试端口。

        Android 11+ 无线调试重连端口随机,单一端口重连易失败,故按候选集尝试。
        """
        adb_port = _int_config("adb_port", "MOBILE_AGENT_ADB_PORT", 5555)
        ports: list[int] = []
        for p in (base_port, adb_port, 5555, 5556, 5557, 37000, 39000, 40000):
            if p and p not in ports:
                ports.append(p)
        return ports

    def _try_reconnect(self, device_id: str, timeout: float = 1.0) -> bool:
        """按稳定序列号或无线端点重连。

        端口可能因断线重连而变化:先探测候选端口是否开放,再逐个 connect,
        任一成功即视为重连成功。
        """
        if not device_id:
            return False

        endpoints: list[str] = []
        if device_id.startswith("easytier:"):
            endpoints.append(
                f"{device_id.split(':', 1)[1]}:"
                f"{_int_config('adb_port', 'MOBILE_AGENT_ADB_PORT', 5555)}"
            )
        elif ":" in device_id:
            endpoints.append(device_id)
        try:
            managed = self._dm().get_device_by_device_id(device_id)
            if managed is None:
                managed = self._dm().get_device_by_serial(device_id)
            for connection in getattr(managed, "connections", []) or []:
                endpoint = str(getattr(connection, "device_id", "") or "")
                if ":" in endpoint and endpoint not in endpoints:
                    endpoints.append(endpoint)
        except Exception:  # noqa: BLE001
            pass

        attempted_ips: set[str] = set()
        for endpoint in endpoints:
            ip, _, raw_port = endpoint.rpartition(":")
            if not ip:
                continue
            attempted_ips.add(ip)
            try:
                base_port = int(raw_port or 5555)
            except (TypeError, ValueError):
                base_port = 5555
            for port in self._reconnect_ports(base_port):
                if not self._tcp_port_open(ip, port, timeout):
                    continue
                try:
                    res = self.connect_wifi_manual(ip, port)
                    if res.get("ok"):
                        self._mgr.refresh()
                        return True
                except Exception:  # noqa: BLE001
                    continue

        # 端口全变或旧端点未知:回退到 EasyTier 网段扫描。
        try:
            scan = self._scan_easytier_adb()
            for entry in scan.get("connected", []):
                addr = str(entry.get("address", ""))
                if entry.get("ok") and (
                    not attempted_ips
                    or self._endpoint_ipv4(addr) in attempted_ips
                ):
                    self._mgr.refresh()
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def ensure_connected(self, device_id: str, *, timeout: int = 5) -> str | None:
        """Return a live ADB endpoint, reconnecting a known wireless device once."""
        endpoint = self._mgr.resolve_adb_device_id(device_id)
        if self._adb_ping(endpoint, timeout=timeout):
            return endpoint
        if not self._try_reconnect(device_id):
            return None
        self._mgr.refresh()
        endpoint = self._mgr.resolve_adb_device_id(device_id)
        return endpoint if self._adb_ping(endpoint, timeout=timeout) else None

    def _apply_screen_on(self, adb_device_id: str) -> bool:
        """保持屏幕常亮:充电常亮 + 关闭自动熄屏 + 唤醒一次。"""
        ok = True
        for args in (
            ["svc", "power", "stayon", "true"],
            ["settings", "put", "system", "screen_off_timeout", "2147483647"],
            ["input", "keyevent", "224"],
        ):
            try:
                r = self._adb_shell(adb_device_id, args, timeout=8)
                ok = ok and r.returncode == 0
            except Exception:  # noqa: BLE001
                ok = False
        return ok

    def ensure_screen_on(self, device_id: str) -> dict[str, Any]:
        """对单台设备立即应用屏幕常亮(供 API/任务前置调用)。"""
        adb_device_id = self._mgr.resolve_adb_device_id(device_id)
        ok = self._apply_screen_on(adb_device_id)
        return {"ok": ok, "device_id": device_id, "adb_device_id": adb_device_id}

    @staticmethod
    def _active_ai_device_ids() -> set[str]:
        """收集正在被 AI 自动聊天占用的设备 id(保活须跳过,避免干扰)。"""
        ids: set[str] = set()
        try:
            from core.mobile.auto_chat import AutoChatManager

            for s in AutoChatManager.get_instance().status() or []:
                if s.get("running") and s.get("device_id"):
                    ids.add(str(s["device_id"]))
        except Exception:  # noqa: BLE001
            pass
        return ids

    def keepalive_once(
        self,
        *,
        screen_always_on: bool = True,
        reconnect: bool = True,
        probe_timeout: int = 5,
    ) -> dict[str, Any]:
        """执行一轮保活:探测全部设备并尝试无界面重连。

        心跳和 ADB reconnect 不改变手机前台界面，因此租约期间也会执行；只有保持
        屏幕常亮这类会改变设备状态的动作会跳过已预约或正在执行 AI 任务的设备。
        """
        ai_busy = self._active_ai_device_ids()
        checked = pinged = reconnected = screen_on = 0
        skipped_reserved = skipped_ai = 0
        details: list[dict[str, Any]] = []
        for item in self.list_pool():
            device_id = item.get("device_id") or ""
            if not device_id or device_id.startswith("easytier:"):
                continue
            reserved = bool(item.get("reserved"))
            ai_active = device_id in ai_busy
            if reserved:
                skipped_reserved += 1
            if ai_active:
                skipped_ai += 1
            if not item.get("online"):
                # 心跳与 ADB reconnect 不改变前台界面，可在租约期间执行。
                if reconnect and self._try_reconnect(device_id):
                    reconnected += 1
                    details.append({"device_id": device_id, "action": "reconnected"})
                continue
            checked += 1
            adb_device_id = self._mgr.resolve_adb_device_id(device_id)
            alive = self._adb_ping(adb_device_id, timeout=probe_timeout)
            if alive:
                pinged += 1
            elif reconnect and self._try_reconnect(device_id):
                reconnected += 1
                alive = True
                details.append({"device_id": device_id, "action": "reconnected"})
            if alive and screen_always_on and not reserved and not ai_active:
                adb_device_id = self._mgr.resolve_adb_device_id(device_id)
                if self._apply_screen_on(adb_device_id):
                    screen_on += 1
        return {
            "checked": checked,
            "alive": pinged,
            "reconnected": reconnected,
            "screen_on": screen_on,
            "skipped_reserved": skipped_reserved,
            "skipped_ai": skipped_ai,
            "details": details,
        }
