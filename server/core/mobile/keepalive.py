"""手机保活后台循环。

远程手机在低负载时 ADB 容易断连,必须常驻保活:
- 定时心跳探测(adb shell echo ok),失败则断线重连(端口变化也能恢复);
- 保持接入手机屏幕常亮,防止熄屏导致断联;
- 跳过正在被 AI 操控的手机和已预约(独占)的手机,不干扰运行中的任务。

统一通过 DevicePool 领域方法执行,循环只负责调度与观测上报。
"""

from __future__ import annotations

import asyncio
from typing import Any


class MobileKeepAlive:
    """手机保活循环管理器(单例)。"""

    _instance: "MobileKeepAlive | None" = None

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._last_result: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._rounds = 0
        self._interval = 45
        self._screen_always_on = True
        self._reconnect = True
        self._probe_timeout = 5

    @classmethod
    def get_instance(cls) -> "MobileKeepAlive":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def _load_config(self) -> bool:
        """从运行时配置读取保活参数,返回是否启用。"""
        try:
            from api.services.runtime_config import get_runtime_app_config

            cfg = await get_runtime_app_config()
            mobile = getattr(cfg, "mobile", None)
            if mobile is None:
                return True
            self._interval = int(getattr(mobile, "keepalive_interval_seconds", 45) or 45)
            self._screen_always_on = bool(getattr(mobile, "keepalive_screen_always_on", True))
            self._reconnect = bool(getattr(mobile, "keepalive_reconnect", True))
            self._probe_timeout = int(getattr(mobile, "keepalive_probe_timeout", 5) or 5)
            return bool(getattr(mobile, "keepalive_enabled", True))
        except Exception:  # noqa: BLE001
            # 读配置失败时按默认开启,保证断连兜底能力
            return True

    async def _run_once(self) -> None:
        from core.mobile.pool import DevicePool
        from core.mobile.easytier import ensure_easytier_healthy
        from core.observability import obs_log

        # 组网自检自愈:backend 重启后 backend-peer 绑失效命名空间会断网,
        # et0 缺失则重启 backend-peer 恢复 EasyTier(adb 扫描/配对依赖它)
        heal = await asyncio.to_thread(ensure_easytier_healthy)
        if heal.get("healed"):
            self._last_result = {"easytier_heal": heal, "skipped": "healing"}
            self._rounds += 1
            try:
                obs_log(
                    "EasyTier 组网自愈:重启 backend-peer",
                    source="mobile_keepalive",
                    event="easytier_heal",
                    level="warning",
                    data=heal,
                )
            except Exception:  # noqa: BLE001
                pass
            # 组网刚恢复,本轮跳过扫描,下一轮再保活
            return

        pool = DevicePool.get_instance()
        result = await asyncio.to_thread(
            pool.keepalive_once,
            screen_always_on=self._screen_always_on,
            reconnect=self._reconnect,
            probe_timeout=self._probe_timeout,
        )
        result["easytier"] = heal
        self._last_result = result
        self._last_error = None
        self._rounds += 1
        try:
            obs_log(
                "手机保活巡检",
                source="mobile_keepalive",
                event="keepalive_round",
                level="info",
                data=result,
            )
        except Exception:  # noqa: BLE001
            pass

    async def _loop(self) -> None:
        while self._running:
            try:
                enabled = await self._load_config()
                if enabled:
                    await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
            await asyncio.sleep(max(5, self._interval))

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "rounds": self._rounds,
            "interval_seconds": self._interval,
            "screen_always_on": self._screen_always_on,
            "reconnect": self._reconnect,
            "probe_timeout": self._probe_timeout,
            "last_result": self._last_result,
            "last_error": self._last_error,
        }
