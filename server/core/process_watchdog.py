"""Process-level health watchdog for reload-based server deployments."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from http.client import HTTPConnection
from threading import Thread
from typing import Callable

from core.logger import get_logger


logger = get_logger("process_watchdog")


@dataclass(frozen=True)
class ProcessWatchdogConfig:
    startup_grace_seconds: float = 90.0
    interval_seconds: float = 10.0
    probe_timeout_seconds: float = 8.0
    failure_threshold: int = 6

    def normalized(self) -> "ProcessWatchdogConfig":
        return ProcessWatchdogConfig(
            startup_grace_seconds=max(0.0, float(self.startup_grace_seconds)),
            interval_seconds=max(1.0, float(self.interval_seconds)),
            probe_timeout_seconds=max(1.0, float(self.probe_timeout_seconds)),
            failure_threshold=max(1, int(self.failure_threshold)),
        )


@dataclass
class ConsecutiveFailureState:
    failure_threshold: int
    consecutive_failures: int = 0

    def record(self, healthy: bool) -> bool:
        if healthy:
            self.consecutive_failures = 0
            return False
        self.consecutive_failures += 1
        return self.consecutive_failures >= max(1, self.failure_threshold)


def probe_local_health(port: int, timeout_seconds: float) -> bool:
    """Probe the local API without honoring outbound proxy settings."""
    connection = HTTPConnection("127.0.0.1", int(port), timeout=timeout_seconds)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        payload = response.read(4096)
        if response.status != 200:
            return False
        data = json.loads(payload.decode("utf-8"))
        return bool(data.get("status") == "ok" and data.get("mongodb", {}).get("ok"))
    except Exception:
        return False
    finally:
        connection.close()


def _watchdog_loop(
    *,
    port: int,
    config: ProcessWatchdogConfig,
    terminate: Callable[[int], None],
) -> None:
    time.sleep(config.startup_grace_seconds)
    state = ConsecutiveFailureState(config.failure_threshold)

    while True:
        healthy = probe_local_health(port, config.probe_timeout_seconds)
        should_terminate = state.record(healthy)
        if not healthy:
            logger.warning(
                "API 健康探测失败: %s/%s",
                state.consecutive_failures,
                config.failure_threshold,
            )
        elif state.consecutive_failures == 0:
            logger.debug("API 健康探测正常")

        if should_terminate:
            logger.critical(
                "API 连续健康失败，退出热重载父进程以触发容器恢复: failures=%s",
                state.consecutive_failures,
            )
            terminate(70)
            return
        time.sleep(config.interval_seconds)


def start_process_health_watchdog(
    *,
    port: int,
    config: ProcessWatchdogConfig,
    terminate: Callable[[int], None] = os._exit,
) -> Thread:
    """Start one daemon watchdog in the uvicorn reload parent process."""
    normalized = config.normalized()
    thread = Thread(
        target=_watchdog_loop,
        kwargs={"port": port, "config": normalized, "terminate": terminate},
        name="api-process-watchdog",
        daemon=True,
    )
    thread.start()
    logger.info(
        "API 进程看门狗已启动: grace=%ss interval=%ss threshold=%s",
        normalized.startup_grace_seconds,
        normalized.interval_seconds,
        normalized.failure_threshold,
    )
    return thread
