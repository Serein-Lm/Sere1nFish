"""稳定设备身份解析（与 AutoGLM 解耦）。

设备分组 / 备注 / 标签等元数据需要一个**掉线重连后不变**的 key，否则 WiFi 设备
重连（`ip:port` 变化）后元数据就对应不上。这里用硬件序列号 `ro.serialno` 作为
稳定 key：无论 USB 还是 WiFi 接入、地址如何变化，同一台手机的 `ro.serialno` 不变。

实现仅依赖 adb（不引用 AutoGLM 任何模块），保证设备元数据层与 AutoGLM 解耦。
"""

from __future__ import annotations

import subprocess
import threading

# adb device_id（可能是 serial 或 ip:port）-> 稳定 key（ro.serialno）
_key_cache: dict[str, str] = {}
_lock = threading.Lock()


def resolve_device_key(device_id: str, *, timeout: int = 5) -> str:
    """解析设备稳定 key（硬件序列号）。

    成功返回 `ro.serialno`；设备离线 / 取值失败时回退为 `device_id`。
    结果按 `device_id` 缓存，避免重复 adb 调用；WiFi 重连后新 `device_id`
    会重新解析并映射到同一 `ro.serialno`，从而让元数据自动对应回原设备。
    """
    with _lock:
        cached = _key_cache.get(device_id)
    if cached:
        return cached

    key = device_id
    try:
        r = subprocess.run(
            ["adb", "-s", device_id, "shell", "getprop", "ro.serialno"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (r.stdout or "").strip()
        if out:
            key = out
    except Exception:
        pass  # 离线/无 adb：回退 device_id

    with _lock:
        _key_cache[device_id] = key
    return key


def forget(device_id: str | None = None) -> None:
    """清除 key 缓存（device_id=None 清全部）。设备物理更换后可调用。"""
    with _lock:
        if device_id is None:
            _key_cache.clear()
        else:
            _key_cache.pop(device_id, None)
