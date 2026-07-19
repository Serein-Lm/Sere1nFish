"""
浏览器提供者 - 透明代理层

独立于 MediaCrawler 的浏览器生命周期管理。
- DockerProvider: 通过 Docker 动态创建/管理 Chrome 容器
- LocalProvider: 走原有的本地 Chrome 启动逻辑（兼容开发环境）

业务代码只需要调用 get_browser_provider().get_cdp_endpoint() 即可获取 CDP 地址，
不需要关心浏览器是本地的还是 Docker 容器里的。
"""

import asyncio
import logging
import os
import time
import uuid
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import httpx

from core.async_limiter import ResizableLimiter
from core.logger import get_logger

logger = get_logger("browser_manager")

_BULK_BROWSER_PURPOSES = frozenset({"url_scan"})
MAX_CHROME_CONTAINERS = 80


# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class ContainerInfo:
    """Docker 容器信息"""
    container_id: str
    container_name: str
    cdp_host: str
    cdp_port: int
    api_port: int
    vnc_port: int
    novnc_port: int
    status: str = "starting"  # starting | idle | busy | stopping | unhealthy
    task_id: Optional[str] = None
    purpose: str = "general"  # url_scan | xhs | xhs_screenshot | general
    created_at: datetime = field(default_factory=datetime.now)
    last_used_at: datetime = field(default_factory=datetime.now)
    # 健康监控
    memory_usage_mb: float = 0.0
    unhealthy_reason: str = ""
    consecutive_errors: int = 0
    cdp_healthy: bool = True
    recovery_in_progress: bool = False
    last_recovery_at: Optional[datetime] = None

    @property
    def cdp_url(self) -> str:
        return f"http://{self.cdp_host}:{self.cdp_port}"

    @property
    def cdp_ws_url(self) -> str:
        return f"ws://{self.cdp_host}:{self.cdp_port}"

    @property
    def api_url(self) -> str:
        return f"http://{self.cdp_host}:{self.api_port}"

    @property
    def novnc_url(self) -> str:
        return f"http://{self.cdp_host}:{self.novnc_port}/vnc.html?autoconnect=true"


@dataclass
class ChromeDockerConfig:
    """Docker Chrome 配置"""
    enabled: bool = True  # 默认启用 Docker 模式
    image: str = "chrome-browser:latest"
    max_containers: int = 5
    idle_timeout: int = 300  # 秒
    shm_size: str = "2g"
    screen_width: int = 1920
    screen_height: int = 1080
    timezone: str = "Asia/Shanghai"
    network: str = "bridge"
    # 端口范围（动态分配）
    cdp_port_start: int = 9222
    api_port_start: int = 8250
    vnc_port_start: int = 5900
    novnc_port_start: int = 6080
    # VNC 鉴权
    vnc_password: str = "chrome@2026"  # VNC 访问密码
    api_token: str = ""  # 容器控制 API 的 Token（空则不鉴权）
    enable_vnc: bool = False  # 是否启用 VNC/noVNC（默认关闭，省性能）
    # 健康监控
    memory_unhealthy_mb: int = 1500  # 内存超过此值标记 unhealthy（MB）
    memory_restart_mb: int = 800  # 内存超过此值才重启 Chrome（动态策略）
    health_check_interval: int = 30  # 健康检查间隔（秒）
    wechat_article_lease_timeout: int = 240  # 公众号单次浏览器租约上限（秒）
    generic_busy_lease_timeout: int = 1800  # 其他浏览器租约异常告警/恢复上限
    # 预热池
    warm_pool_size: int = 1  # 预热池大小（启动时预创建的空闲容器数）
    reserved_non_bulk_containers: int = 2  # 为公众号/学者等非批量任务保留容量
    container_create_concurrency: int = 4  # Docker API 创建并发，避免启动风暴
    container_health_concurrency: int = 12  # stats/CDP 健康检查并发
    # 容器热切换
    max_consecutive_errors: int = 3  # 连续错误超过此值触发容器热切换
    # 主机资源保护；只限制新建容器，不中断已有租约
    host_memory_floor_mb: int = 8192
    host_load_per_cpu_limit: float = 1.5
    recent_cdp_failure_limit: int = 12
    recent_cdp_failure_window_seconds: int = 300

    @classmethod
    def from_dict(cls, data: dict) -> "ChromeDockerConfig":
        normalized = dict(data or {})
        legacy_aliases = {
            "min_available_memory_mb": "host_memory_floor_mb",
            "max_host_load_ratio": "host_load_per_cpu_limit",
            "max_recoveries_per_minute": "recent_cdp_failure_limit",
        }
        for legacy_key, current_key in legacy_aliases.items():
            if current_key not in normalized and legacy_key in normalized:
                normalized[current_key] = normalized[legacy_key]
        if (
            "max_recoveries_per_minute" in normalized
            and "recent_cdp_failure_window_seconds" not in normalized
        ):
            normalized["recent_cdp_failure_window_seconds"] = 60
        config = cls(
            **{
                key: value
                for key, value in normalized.items()
                if key in cls.__dataclass_fields__
            }
        )
        config.max_containers = max(
            1, min(int(config.max_containers), MAX_CHROME_CONTAINERS)
        )
        config.warm_pool_size = max(
            0, min(int(config.warm_pool_size), config.max_containers)
        )
        config.reserved_non_bulk_containers = max(
            0,
            min(
                int(config.reserved_non_bulk_containers),
                config.max_containers - 1,
            ),
        )
        config.container_create_concurrency = max(
            1, min(int(config.container_create_concurrency), 16)
        )
        config.container_health_concurrency = max(
            1, min(int(config.container_health_concurrency), 32)
        )
        config.host_memory_floor_mb = max(1024, int(config.host_memory_floor_mb))
        config.host_load_per_cpu_limit = max(
            0.5, min(float(config.host_load_per_cpu_limit), 8.0)
        )
        config.recent_cdp_failure_limit = max(
            1, min(int(config.recent_cdp_failure_limit), 100)
        )
        config.recent_cdp_failure_window_seconds = max(
            30, min(int(config.recent_cdp_failure_window_seconds), 3600)
        )
        return config

    @property
    def normalized_reserved_non_bulk_containers(self) -> int:
        return max(
            0,
            min(
                int(self.reserved_non_bulk_containers),
                max(0, int(self.max_containers) - 1),
            ),
        )

    @property
    def bulk_container_limit(self) -> int:
        return max(
            1,
            int(self.max_containers) - self.normalized_reserved_non_bulk_containers,
        )


# ── 抽象基类 ──────────────────────────────────────────────

class BrowserProvider(ABC):
    """浏览器提供者基类"""

    async def start(self) -> None:
        """启动 Provider 后台维护；无状态实现可保持空操作。"""
        return None

    @abstractmethod
    async def get_cdp_endpoint(self, task_id: Optional[str] = None, purpose: str = "general") -> Optional[str]:
        """
        获取一个可用的 CDP WebSocket 地址。
        返回 None 表示走本地模式。
        """
        ...

    @abstractmethod
    async def release_cdp_endpoint(self, task_id: Optional[str] = None):
        """释放 CDP 连接"""
        ...

    @abstractmethod
    async def shutdown(self):
        """关闭所有管理的资源"""
        ...

    async def get_pool_status(self) -> list[dict]:
        """获取连接池状态"""
        return []

    async def report_error(self, task_id: Optional[str] = None, error_msg: str = "") -> None:
        """上报容器错误（用于触发热切换判断）"""
        pass

    async def recover_task_container(
        self,
        task_id: Optional[str] = None,
        reason: str = "",
    ) -> bool:
        """Recover the browser currently leased by a task when supported."""
        return False

    async def hot_swap_container(self, task_id: Optional[str] = None, purpose: str = "general") -> Optional[str]:
        """
        容器热切换：释放当前容器 → 从预热池或新建获取新容器。
        返回新的 CDP WS URL，失败返回 None。
        """
        return None

    async def get_container_memory_mb(self, task_id: Optional[str] = None) -> float:
        """获取任务对应容器的内存使用量（MB），不支持则返回 0"""
        return 0.0


# ── 本地模式 ──────────────────────────────────────────────

class LocalProvider(BrowserProvider):
    """本地模式：返回 None，让 CDPBrowserManager 走原有逻辑"""

    async def get_cdp_endpoint(self, task_id: Optional[str] = None, purpose: str = "general") -> Optional[str]:
        return None

    async def release_cdp_endpoint(self, task_id: Optional[str] = None):
        pass

    async def shutdown(self):
        pass


# ── Docker 模式 ───────────────────────────────────────────

class DockerProvider(BrowserProvider):
    """
    Docker 模式：动态创建和管理 Chrome 容器

    - 按需创建容器（不预启动）
    - 任务完成后标记空闲
    - 空闲超时自动销毁
    - 支持并发任务分配不同容器
    """

    def __init__(self, config: ChromeDockerConfig):
        self.config = config
        self.containers: dict[str, ContainerInfo] = {}  # container_id → info
        self.task_map: dict[str, str] = {}  # task_id → container_id
        self._lock = asyncio.Lock()
        self._reaper_task: Optional[asyncio.Task] = None
        self._health_checker_task: Optional[asyncio.Task] = None
        self._warm_pool_task: Optional[asyncio.Task] = None
        self._docker_client = None
        self._port_counter = 0
        self._pending_creates = 0
        self._bulk_slot_limit = config.bulk_container_limit
        self._bulk_slots = ResizableLimiter(self._bulk_slot_limit)
        self._create_slots = ResizableLimiter(
            config.container_create_concurrency
        )
        self._health_slots = ResizableLimiter(
            config.container_health_concurrency
        )
        self._bulk_slot_owners: set[str] = set()
        self._recent_cdp_failures: deque[float] = deque()
        self._last_resource_guard: dict[str, Any] = {
            "restricted": False,
            "reason": "",
        }
        self._warm_pool_ready = asyncio.Event()  # 预热池就绪信号
        self._orphan_cleanup_done = False
        self._closing = False
        self._container_create_tasks: set[asyncio.Task[Any]] = set()

        logger.info(
            "[DockerProvider] 工作负载配额 | max=%s bulk=%s "
            "reserved_non_bulk=%s create=%s health=%s",
            config.max_containers,
            self._bulk_slot_limit,
            int(config.max_containers) - self._bulk_slot_limit,
            config.container_create_concurrency,
            config.container_health_concurrency,
        )

    @staticmethod
    def _is_assignable(info: ContainerInfo) -> bool:
        return bool(
            info.status == "idle"
            and not info.unhealthy_reason
            and info.cdp_healthy
            and not info.recovery_in_progress
        )

    async def _acquire_workload_slot(self, task_id: str, purpose: str) -> None:
        """限制批量网站 worker 的全局占用，避免挤压公众号等并行链路。"""
        if purpose not in _BULK_BROWSER_PURPOSES or task_id in self._bulk_slot_owners:
            return
        if self._bulk_slots.locked():
            logger.info(
                "[DockerProvider] 批量浏览器任务等待配额 | task=%s purpose=%s limit=%s",
                task_id,
                purpose,
                self._bulk_slot_limit,
            )
        await self._bulk_slots.acquire()
        if task_id in self._bulk_slot_owners:
            self._bulk_slots.release()
            return
        self._bulk_slot_owners.add(task_id)

    def _release_workload_slot(self, task_id: str | None) -> None:
        if not task_id or task_id not in self._bulk_slot_owners:
            return
        self._bulk_slot_owners.remove(task_id)
        self._bulk_slots.release()

    def _get_docker_client(self):
        """延迟初始化 docker client"""
        if self._docker_client is None:
            try:
                import docker
                self._docker_client = docker.from_env(timeout=30)
            except ImportError:
                raise RuntimeError(
                    "docker-py 未安装。请运行: pip install docker"
                )
            except Exception as e:
                raise RuntimeError(f"无法连接 Docker daemon: {e}")
        return self._docker_client

    async def start(self) -> None:
        """服务启动时立即启动健康检查、回收器和预热池。"""
        self._closing = False
        if not self._orphan_cleanup_done:
            try:
                await asyncio.to_thread(self._cleanup_orphan_containers)
            finally:
                self._orphan_cleanup_done = True
        self._ensure_background_tasks()

    async def reconfigure(self, config: ChromeDockerConfig) -> dict[str, Any]:
        """Apply capacity and guard settings without replacing active leases."""
        previous = self.config
        self.config = config
        self._bulk_slot_limit = config.bulk_container_limit
        self._bulk_slots.resize(self._bulk_slot_limit)
        self._create_slots.resize(config.container_create_concurrency)
        self._health_slots.resize(config.container_health_concurrency)
        self._warm_pool_ready.clear()
        self._ensure_background_tasks()
        logger.notice(
            "[DockerProvider] 运行时配置已更新 | max=%s->%s bulk=%s->%s "
            "reserve=%s warm=%s create=%s health=%s",
            previous.max_containers,
            config.max_containers,
            previous.bulk_container_limit,
            config.bulk_container_limit,
            config.normalized_reserved_non_bulk_containers,
            config.warm_pool_size,
            config.container_create_concurrency,
            config.container_health_concurrency,
        )
        return self.capacity_status()

    def _prune_recent_cdp_failures(self) -> None:
        cutoff = time.monotonic() - self.config.recent_cdp_failure_window_seconds
        while self._recent_cdp_failures and self._recent_cdp_failures[0] < cutoff:
            self._recent_cdp_failures.popleft()

    def _record_cdp_failure(self) -> None:
        self._recent_cdp_failures.append(time.monotonic())
        self._prune_recent_cdp_failures()

    def _host_resource_snapshot(self) -> dict[str, Any]:
        try:
            import psutil

            available_mb = int(psutil.virtual_memory().available / (1024 * 1024))
            cpu_count = max(1, int(os.cpu_count() or 1))
            load_1m = float(os.getloadavg()[0])
            load_per_cpu = load_1m / cpu_count
        except Exception:  # pragma: no cover - deployment fallback
            available_mb = -1
            cpu_count = 1
            load_1m = 0.0
            load_per_cpu = 0.0
        return {
            "available_memory_mb": available_mb,
            "memory_floor_mb": self.config.host_memory_floor_mb,
            "cpu_count": cpu_count,
            "load_1m": round(load_1m, 2),
            "load_per_cpu": round(load_per_cpu, 3),
            "load_per_cpu_limit": self.config.host_load_per_cpu_limit,
        }

    def _effective_container_limit(self) -> int:
        self._prune_recent_cdp_failures()
        snapshot = self._host_resource_snapshot()
        healthy_active = sum(
            1
            for container in self.containers.values()
            if container.status in {"busy", "idle", "starting"}
            and not container.unhealthy_reason
            and container.cdp_healthy
        ) + self._pending_creates
        reasons: list[str] = []
        available_mb = int(snapshot["available_memory_mb"])
        if 0 <= available_mb < self.config.host_memory_floor_mb:
            reasons.append("available_memory_below_floor")
        if float(snapshot["load_per_cpu"]) > self.config.host_load_per_cpu_limit:
            reasons.append("host_load_above_limit")
        if len(self._recent_cdp_failures) >= self.config.recent_cdp_failure_limit:
            reasons.append("recent_cdp_failures")
        effective = (
            max(1, min(self.config.max_containers, healthy_active))
            if reasons
            else self.config.max_containers
        )
        self._last_resource_guard = {
            **snapshot,
            "restricted": bool(reasons),
            "reason": ",".join(reasons),
            "recent_cdp_failures": len(self._recent_cdp_failures),
            "recent_cdp_failure_limit": self.config.recent_cdp_failure_limit,
            "effective_max_containers": effective,
        }
        return effective

    def capacity_status(self) -> dict[str, Any]:
        effective = self._effective_container_limit()
        return {
            "configured_max_containers": self.config.max_containers,
            "effective_max_containers": effective,
            "bulk_limit": self._bulk_slot_limit,
            "bulk_in_use": self._bulk_slots.in_use,
            "bulk_waiting": self._bulk_slots.waiting,
            "reserved_non_bulk_containers": (
                self.config.normalized_reserved_non_bulk_containers
            ),
            "pending_creates": self._pending_creates,
            "create_limit": self._create_slots.limit,
            "create_in_use": self._create_slots.in_use,
            "create_waiting": self._create_slots.waiting,
            "health_limit": self._health_slots.limit,
            "health_in_use": self._health_slots.in_use,
            "health_waiting": self._health_slots.waiting,
            "resource_guard": dict(self._last_resource_guard),
        }

    def _cleanup_orphan_containers(self):
        """
        启动时清理上次遗留的孤儿容器。
        
        场景：测试 Ctrl+C 中断、进程崩溃等导致容器没被正确销毁，
        下次启动时端口冲突。通过容器名前缀 "chrome-" 识别并清理。
        """
        try:
            client = self._get_docker_client()
            # 仅清理由本 Provider 命名且使用同一镜像的临时容器。
            containers = [
                container
                for container in client.containers.list(
                    all=True,
                    filters={"ancestor": self.config.image},
                )
                if str(container.name or "").startswith("chrome-")
            ]
            if not containers:
                return

            orphan_count = 0
            for container in containers:
                try:
                    container.remove(force=True)
                    orphan_count += 1
                    logger.info(
                        f"[DockerProvider] 清理孤儿容器: {container.name} (id={container.id[:12]})"
                    )
                except Exception as e:
                    logger.warning(
                        f"[DockerProvider] 清理孤儿容器失败: {container.name}: {e}"
                    )

            if orphan_count > 0:
                logger.info(f"[DockerProvider] 共清理 {orphan_count} 个孤儿容器")

        except Exception as e:
            logger.debug(f"[DockerProvider] 孤儿容器检查跳过: {e}")

    def _allocate_ports(self) -> dict[str, int]:
        """分配端口（递增，跳过已占用的端口）"""
        import socket

        def _is_port_free(port: int) -> bool:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.5)
                    s.bind(("0.0.0.0", port))
                    return True
            except OSError:
                return False

        max_attempts = 20
        for _ in range(max_attempts):
            offset = self._port_counter
            self._port_counter += 1
            ports = {
                "cdp": self.config.cdp_port_start + offset,
                "api": self.config.api_port_start + offset,
                "vnc": self.config.vnc_port_start + offset,
                "novnc": self.config.novnc_port_start + offset,
            }
            # 检查所有端口是否可用
            if all(_is_port_free(p) for p in ports.values()):
                return ports
            logger.debug(
                f"[DockerProvider] 端口组 offset={offset} 被占用，跳过: "
                f"{list(ports.values())}"
            )

        raise RuntimeError(f"无法分配可用端口（尝试 {max_attempts} 次）")

    async def get_cdp_endpoint(self, task_id: Optional[str] = None, purpose: str = "general") -> Optional[str]:
        """
        获取一个 CDP 地址，必要时动态创建容器。
        
        Args:
            task_id: 任务 ID（用于追踪容器归属）
            purpose: 用途标签，同一 purpose 的任务共享容器
                - "url_scan": URL 扫描（chrome-devtools-mcp）
                - "xhs": 小红书爬虫（Playwright）
                - "xhs_screenshot": 小红书截屏
                - "general": 通用
        """
        task_id = task_id or str(uuid.uuid4())
        logger.info(f"[DockerProvider] 请求 CDP 端点 | task_id={task_id} purpose={purpose}")

        await self._acquire_workload_slot(task_id, purpose)
        try:
            endpoint = await self._allocate_cdp_endpoint(task_id, purpose)
        except BaseException:
            self._release_workload_slot(task_id)
            raise
        if endpoint is None:
            self._release_workload_slot(task_id)
        return endpoint

    async def _allocate_cdp_endpoint(
        self,
        task_id: str,
        purpose: str,
    ) -> Optional[str]:
        """在工作负载配额通过后分配或创建容器。"""
        claimed: ContainerInfo | None = None
        wait_for_idle = False
        create_required = False
        async with self._lock:
            # 1. 优先复用同 purpose 的空闲容器（跳过 unhealthy）
            for cid, info in self.containers.items():
                if self._is_assignable(info) and getattr(info, 'purpose', 'general') == purpose:
                    info.status = "busy"
                    info.task_id = task_id
                    info.last_used_at = datetime.now()
                    self.task_map[task_id] = cid
                    logger.info(
                        f"[DockerProvider] 复用同类容器 {info.container_name} (purpose={purpose}) → task {task_id}"
                    )
                    claimed = info
                    break

            # 2. 复用任意空闲容器（跳过 unhealthy）
            if claimed is None:
                for cid, info in self.containers.items():
                    if self._is_assignable(info):
                        info.status = "busy"
                        info.task_id = task_id
                        info.purpose = purpose
                        info.last_used_at = datetime.now()
                        self.task_map[task_id] = cid
                        logger.info(
                            f"[DockerProvider] 复用空闲容器 {info.container_name} → task {task_id} (purpose={purpose})"
                        )
                        claimed = info
                        break

            # 3. 检查是否达到上限（unhealthy 的不计入活跃数）
            if claimed is None:
                active_count = sum(
                    1 for c in self.containers.values()
                    if c.status in ("busy", "idle", "starting")
                    and c.unhealthy_reason == ""
                    and c.cdp_healthy
                ) + self._pending_creates
                effective_limit = self._effective_container_limit()
                logger.info(
                    f"[DockerProvider] 无空闲容器 | 活跃容器数={active_count}/{effective_limit} "
                    f"(configured={self.config.max_containers})"
                )
                if active_count >= effective_limit:
                    logger.warning(
                        f"[DockerProvider] 容器数已达当前有效上限 ({effective_limit})，等待空闲容器..."
                    )
                    wait_for_idle = True
                else:
                    logger.info(f"[DockerProvider] 为 task {task_id} 创建新容器 (purpose={purpose})...")
                    self._pending_creates += 1
                    create_required = True

        if wait_for_idle:
            return await self._wait_for_idle(task_id, purpose=purpose, timeout=60)
        if create_required:
            info: ContainerInfo | None = None
            registered = False
            try:
                info = await self._create_container()
                async with self._lock:
                    self.containers[info.container_id] = info
                    info.status = "busy"
                    info.task_id = task_id
                    info.purpose = purpose
                    self.task_map[task_id] = info.container_id
                    registered = True
                logger.info(
                    f"[DockerProvider] 新容器 {info.container_name} 已分配给 task {task_id} | "
                    f"CDP={info.cdp_port}, VNC={info.vnc_port}, noVNC={info.novnc_port}"
                )
                self._ensure_background_tasks()
                claimed = info
            except BaseException:
                if info is not None and not registered:
                    await self._remove_docker_container(
                        info.container_id,
                        info.container_name,
                    )
                raise
            finally:
                async with self._lock:
                    self._pending_creates = max(0, self._pending_creates - 1)
        if claimed is None:
            return None
        try:
            return await self._get_ws_url(claimed)
        except asyncio.CancelledError:
            await self.release_cdp_endpoint(task_id)
            raise
        except Exception as exc:
            self._record_cdp_failure()
            claimed.cdp_healthy = False
            claimed.unhealthy_reason = f"CDP 端点获取失败: {exc}"[:300]
            await self.recover_task_container(
                task_id=task_id,
                reason="Chrome CDP 端点连续获取失败",
            )
            await self.release_cdp_endpoint(task_id)
            raise

    async def release_cdp_endpoint(self, task_id: Optional[str] = None):
        """释放任务占用的容器"""
        if not task_id or task_id not in self.task_map:
            logger.debug(f"[DockerProvider] 释放请求忽略 | task_id={task_id} (未找到)")
            self._release_workload_slot(task_id)
            return

        async with self._lock:
            cid = self.task_map.pop(task_id, None)
            if cid and cid in self.containers:
                info = self.containers[cid]
                info.status = "idle"
                info.task_id = None
                info.last_used_at = datetime.now()
                info.recovery_in_progress = False
                logger.info(
                    f"[DockerProvider] 释放容器 {info.container_name} | task={task_id} | "
                    f"状态→idle | 当前容器池: {len(self.containers)} 个"
                )
        self._release_workload_slot(task_id)

    async def shutdown(self):
        """销毁所有容器和后台任务"""
        self._closing = True
        logger.info(
            "[DockerProvider] 开始关闭 | tracked=%s pending_creates=%s",
            len(self.containers),
            self._pending_creates,
        )

        background_tasks = [
            task
            for task in (
                self._reaper_task,
                self._health_checker_task,
                self._warm_pool_task,
            )
            if task is not None
        ]
        for task in background_tasks:
            if not task.done():
                task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        self._reaper_task = None
        self._health_checker_task = None
        self._warm_pool_task = None

        current = asyncio.current_task()
        create_tasks = [
            task
            for task in tuple(self._container_create_tasks)
            if task is not current and not task.done()
        ]
        for task in create_tasks:
            if task.cancelling() == 0:
                task.cancel()
        if create_tasks:
            await asyncio.gather(*create_tasks, return_exceptions=True)

        destroy_limit = max(
            2,
            min(self.config.container_create_concurrency * 2, 16),
        )
        destroy_slots = asyncio.Semaphore(destroy_limit)

        async def _destroy(container_id: str) -> None:
            async with destroy_slots:
                await self._destroy_container(container_id)

        container_ids = list(self.containers.keys())
        container_count = len(container_ids)
        if container_ids:
            await asyncio.gather(
                *(_destroy(container_id) for container_id in container_ids),
                return_exceptions=True,
            )

        logger.info(f"[DockerProvider] 所有容器已销毁 (共 {container_count} 个)")

    async def get_pool_status(self) -> list[dict]:
        """获取连接池状态（含内存信息）"""
        result = []
        now = datetime.now()
        for info in self.containers.values():
            result.append({
                "container_id": info.container_id[:12],
                "container_name": info.container_name,
                "status": info.status,
                "task_id": info.task_id,
                "purpose": info.purpose,
                "cdp_url": info.cdp_url,
                "novnc_url": info.novnc_url,
                "memory_mb": round(info.memory_usage_mb, 1),
                "unhealthy_reason": info.unhealthy_reason,
                "consecutive_errors": info.consecutive_errors,
                "cdp_healthy": info.cdp_healthy,
                "busy_seconds": round(
                    (now - info.last_used_at).total_seconds(),
                    1,
                ) if info.status == "busy" else 0,
                "recovery_in_progress": info.recovery_in_progress,
                "created_at": info.created_at.isoformat(),
                "last_used_at": info.last_used_at.isoformat(),
            })
        return result

    # ── 内部方法 ──────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        """生成容器 API 鉴权 headers"""
        if self.config.api_token:
            return {"Authorization": f"Bearer {self.config.api_token}"}
        return {}

    async def _create_container(self) -> ContainerInfo:
        """Create one container within the global Docker API budget."""
        if self._closing:
            raise RuntimeError("Chrome 容器池正在关闭")
        task = asyncio.current_task()
        if task is not None:
            self._container_create_tasks.add(task)
        acquired = False
        try:
            await self._create_slots.acquire()
            acquired = True
            if self._closing:
                raise RuntimeError("Chrome 容器池正在关闭")
            return await self._create_container_unlimited()
        finally:
            if acquired:
                self._create_slots.release()
            if task is not None:
                self._container_create_tasks.discard(task)

    async def _create_container_unlimited(self) -> ContainerInfo:
        """通过 docker-py 创建新容器"""
        client = self._get_docker_client()
        ports = self._allocate_ports()
        name = f"chrome-{uuid.uuid4().hex[:8]}"

        logger.info(
            f"[DockerProvider] 开始创建容器 {name} | "
            f"镜像={self.config.image} | 端口分配: CDP={ports['cdp']}, "
            f"API={ports['api']}, VNC={ports['vnc']}, noVNC={ports['novnc']}"
        )

        env = {
            "SCREEN_WIDTH": str(self.config.screen_width),
            "SCREEN_HEIGHT": str(self.config.screen_height),
            "CDP_PORT": "9222",
            "VNC_PORT": "5900",
            "NOVNC_PORT": "6080",
            "API_PORT": "8250",
            "TZ": self.config.timezone,
            "ENABLE_VNC": "true" if self.config.enable_vnc else "false",
        }

        # VNC 鉴权（仅 VNC 启用时有意义）
        if self.config.enable_vnc and self.config.vnc_password:
            env["VNC_PASSWORD"] = self.config.vnc_password
            logger.info(f"[DockerProvider] VNC 鉴权已启用 (密码长度={len(self.config.vnc_password)})")

        if not self.config.enable_vnc:
            logger.info("[DockerProvider] VNC/noVNC 已禁用（省性能）")

        # 容器 API Token
        if self.config.api_token:
            env["API_TOKEN"] = self.config.api_token
            logger.info("[DockerProvider] 容器控制 API Token 已启用")

        use_internal_network = self.config.network not in ("", "bridge", "host")

        # Compose 内部网络模式不发布 Chrome 端口到宿主机，避免对外暴露额外服务。
        # 本机直接运行后端时继续使用 127.0.0.1 端口映射，便于调试。
        port_bindings = None
        if not use_internal_network:
            port_bindings = {
                "9222/tcp": ("127.0.0.1", ports["cdp"]),
                "8250/tcp": ("127.0.0.1", ports["api"]),
            }
            if self.config.enable_vnc:
                port_bindings["5900/tcp"] = ("127.0.0.1", ports["vnc"])
                port_bindings["6080/tcp"] = ("127.0.0.1", ports["novnc"])

        run_kwargs = {
            "image": self.config.image,
            "name": name,
            "detach": True,
            "shm_size": self.config.shm_size,
            "environment": env,
            "labels": {"sere1nfish.browser.managed": "true"},
        }
        if port_bindings:
            run_kwargs["ports"] = port_bindings
        if use_internal_network:
            run_kwargs["network"] = self.config.network

        t_start = time.time()
        run_task = asyncio.create_task(
            asyncio.to_thread(client.containers.run, **run_kwargs)
        )
        try:
            container = await asyncio.shield(run_task)
        except asyncio.CancelledError:
            # Docker SDK calls cannot be cancelled once dispatched to a thread.
            # Drain the call and remove any container it created before exiting.
            try:
                orphan = await asyncio.shield(run_task)
            except Exception:
                pass
            else:
                try:
                    await asyncio.to_thread(orphan.remove, force=True)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[DockerProvider] 取消创建后清理容器失败 %s: %s",
                        name,
                        exc,
                    )
            raise
        t_created = time.time()
        logger.info(
            f"[DockerProvider] 容器 {name} 已创建 (id={container.id[:12]}) | "
            f"docker run 耗时 {t_created - t_start:.1f}s"
        )

        info = ContainerInfo(
            container_id=container.id,
            container_name=name,
            cdp_host=name if use_internal_network else "localhost",
            cdp_port=9222 if use_internal_network else ports["cdp"],
            api_port=8250 if use_internal_network else ports["api"],
            vnc_port=5900 if use_internal_network else ports["vnc"],
            novnc_port=6080 if use_internal_network else ports["novnc"],
            status="starting",
        )
        try:
            await self._wait_healthy(info, timeout=30)
        except BaseException:
            await self._remove_docker_container(container.id, name)
            raise
        t_ready = time.time()
        logger.info(
            f"[DockerProvider] 容器 {name} 就绪 | "
            f"健康检查耗时 {t_ready - t_created:.1f}s | "
            f"总启动耗时 {t_ready - t_start:.1f}s"
        )

        return info

    async def _remove_docker_container(self, container_id: str, container_name: str) -> None:
        """在线程中停止并删除容器，避免 Docker SDK 阻塞事件循环。"""
        try:
            client = self._get_docker_client()

            def _remove() -> None:
                container = client.containers.get(container_id)
                container.remove(force=True)

            await asyncio.to_thread(_remove)
        except Exception as exc:
            logger.warning(
                f"[DockerProvider] 删除容器失败: {container_name} "
                f"(id={container_id[:12]}): {exc}"
            )

    async def _wait_healthy(self, info: ContainerInfo, timeout: int = 30):
        """等待容器 CDP 端口可达"""
        start = time.time()
        attempt = 0
        last_error = ""
        while time.time() - start < timeout:
            attempt += 1
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(f"{info.api_url}/health", timeout=3)
                    if resp.status_code == 200:
                        elapsed = time.time() - start
                        logger.info(
                            f"[DockerProvider] 容器 {info.container_name} 健康检查通过 | "
                            f"尝试 {attempt} 次 | 耗时 {elapsed:.1f}s"
                        )
                        return
                    else:
                        last_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
            except httpx.ConnectError:
                last_error = "连接被拒绝（容器可能还在启动）"
            except httpx.TimeoutException:
                last_error = "请求超时"
            except Exception as e:
                last_error = str(e)

            if attempt % 5 == 0:
                elapsed = time.time() - start
                logger.debug(
                    f"[DockerProvider] 容器 {info.container_name} 健康检查中... "
                    f"第 {attempt} 次 | 已等待 {elapsed:.1f}s | 最后错误: {last_error}"
                )
            await asyncio.sleep(1)

        raise TimeoutError(
            f"容器 {info.container_name} 启动超时 ({timeout}s) | "
            f"尝试 {attempt} 次 | 最后错误: {last_error}"
        )

    async def _get_ws_url(self, info: ContainerInfo, max_retries: int = 10) -> str:
        """
        从容器获取 CDP WebSocket URL（带重试）。

        通过容器控制 API（/cdp/info）获取 WS URL，然后返回代理地址。
        在 Apple Silicon + QEMU 模拟下，宿主机直连 CDP 端口不通，
        所以使用容器 API 端口上的 /cdp-proxy WebSocket 代理。
        """
        logger.info(
            f"[DockerProvider] 获取 CDP WS URL | 容器={info.container_name} | "
            f"API 地址={info.api_url}"
        )
        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{info.api_url}/cdp/info",
                        timeout=5,
                        headers=self._auth_headers(),
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        browser_version = data.get("browser", "unknown")
                        ws_url = data.get("ws_url", "")
                        if ws_url:
                            # 使用 API 端口上的 CDP 代理，绕过 CDP 端口映射问题
                            proxy_url = f"ws://{info.cdp_host}:{info.api_port}/cdp-proxy"
                            logger.info(
                                f"[DockerProvider] CDP WS URL 获取成功 | "
                                f"Browser={browser_version} | "
                                f"原始 WS={ws_url} | 代理 WS={proxy_url} | 第 {attempt} 次"
                            )
                            return proxy_url
                    last_error = f"API 返回异常: HTTP {resp.status_code}"
            except Exception as e:
                last_error = str(e)

            if attempt < max_retries:
                logger.debug(
                    f"[DockerProvider] 获取 WS URL 第 {attempt} 次失败: {last_error}，1s 后重试..."
                )
                await asyncio.sleep(1)

        raise RuntimeError(
            f"获取 CDP WS URL 失败（重试 {max_retries} 次）| "
            f"容器={info.container_name} | 最后错误: {last_error}"
        )

    async def _wait_for_idle(
        self,
        task_id: str,
        *,
        purpose: str = "general",
        timeout: int = 60,
    ) -> Optional[str]:
        """等待一个空闲容器"""
        start = time.time()
        while time.time() - start < timeout:
            claimed: ContainerInfo | None = None
            async with self._lock:
                for cid, info in self.containers.items():
                    if self._is_assignable(info):
                        info.status = "busy"
                        info.task_id = task_id
                        info.purpose = purpose
                        info.last_used_at = datetime.now()
                        self.task_map[task_id] = cid
                        claimed = info
                        break
            if claimed is not None:
                try:
                    return await self._get_ws_url(claimed)
                except BaseException:
                    await self.release_cdp_endpoint(task_id)
                    raise
            await asyncio.sleep(1)

        logger.error(f"[DockerProvider] 等待空闲容器超时 ({timeout}s)")
        return None

    async def _destroy_container(self, container_id: str):
        """销毁容器"""
        async with self._lock:
            info = self.containers.pop(container_id, None)
            stale_tasks = [
                task_id for task_id, cid in self.task_map.items() if cid == container_id
            ]
            for task_id in stale_tasks:
                self.task_map.pop(task_id, None)
                self._release_workload_slot(task_id)
        if not info:
            return

        info.status = "stopping"
        logger.info(f"[DockerProvider] 开始销毁容器 {info.container_name} (id={container_id[:12]})")
        await self._remove_docker_container(container_id, info.container_name)
        logger.info(f"[DockerProvider] 容器 {info.container_name} 已销毁")

    async def _idle_reaper(self):
        """后台协程：定期检查并销毁超时的空闲容器（保留预热池数量）"""
        while True:
            try:
                await asyncio.sleep(30)
                now = datetime.now()
                to_destroy = []

                async with self._lock:
                    # 统计当前空闲且健康的容器数
                    idle_healthy = [
                        cid for cid, info in self.containers.items()
                        if self._is_assignable(info)
                    ]

                    for cid, info in self.containers.items():
                        if info.status == "idle":
                            idle_seconds = (now - info.last_used_at).total_seconds()
                            if idle_seconds > self.config.idle_timeout:
                                # 保留预热池数量的空闲容器不销毁
                                if len(idle_healthy) > self.config.warm_pool_size:
                                    info.status = "stopping"
                                    to_destroy.append(cid)
                                    idle_healthy = [c for c in idle_healthy if c != cid]
                                    logger.info(
                                        f"[DockerProvider] 容器 {info.container_name} "
                                        f"空闲 {idle_seconds:.0f}s > {self.config.idle_timeout}s，准备销毁"
                                    )

                        # unhealthy 且无任务的容器直接销毁
                        if (
                            info.unhealthy_reason
                            and not info.recovery_in_progress
                            and info.status not in {"busy", "stopping"}
                        ):
                            info.status = "stopping"
                            to_destroy.append(cid)
                            logger.info(
                                f"[DockerProvider] 销毁 unhealthy 容器 {info.container_name}: "
                                f"{info.unhealthy_reason}"
                            )

                for cid in to_destroy:
                    await self._destroy_container(cid)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[DockerProvider] idle_reaper 异常: {e}")

    # ── 新增：后台任务管理 ────────────────────────────────

    def _ensure_background_tasks(self):
        """确保所有后台任务都在运行"""
        if self._closing:
            return
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._idle_reaper())
        if self._health_checker_task is None or self._health_checker_task.done():
            self._health_checker_task = asyncio.create_task(self._health_checker())
        if self._warm_pool_task is None or self._warm_pool_task.done():
            self._warm_pool_task = asyncio.create_task(self._warm_pool_filler())

    # ── 新增：容器健康监控 ────────────────────────────────

    async def _health_checker(self):
        """
        Check memory, control API/CDP health, and stale busy leases.

        A busy container is never silently removed from task_map. Restarting its
        Chrome process breaks the blocked CDP call so the caller can run cleanup,
        release the lease, and retry on another healthy container.
        """
        logger.info(
            f"[DockerProvider] 健康监控已启动 | "
            f"间隔={self.config.health_check_interval}s | "
            f"unhealthy 阈值={self.config.memory_unhealthy_mb}MB | "
            f"公众号租约上限={self.config.wechat_article_lease_timeout}s"
        )
        while True:
            try:
                await asyncio.sleep(self.config.health_check_interval)
                await asyncio.gather(
                    *(
                        self._inspect_container_health_guarded(cid, info)
                        for cid, info in list(self.containers.items())
                        if info.status != "stopping"
                    ),
                    return_exceptions=True,
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[DockerProvider] health_checker 异常: {e}")

    async def _inspect_container_health_guarded(
        self,
        container_id: str,
        info: ContainerInfo,
    ) -> None:
        await self._health_slots.acquire()
        try:
            await self._inspect_container_health(container_id, info)
        finally:
            self._health_slots.release()

    async def _inspect_container_health(
        self,
        container_id: str,
        info: ContainerInfo,
    ) -> None:
        try:
            memory_result, health_result = await asyncio.gather(
                self._query_container_memory(container_id),
                self._query_cdp_health(info),
                return_exceptions=True,
            )
            if not isinstance(memory_result, BaseException):
                info.memory_usage_mb = float(memory_result)
            cdp_healthy = (
                bool(health_result[0])
                if isinstance(health_result, tuple)
                else False
            )
            health_error = (
                str(health_result[1])
                if isinstance(health_result, tuple)
                else str(health_result)
            )
            info.cdp_healthy = cdp_healthy

            if info.memory_usage_mb > self.config.memory_unhealthy_mb:
                info.unhealthy_reason = (
                    f"内存 {info.memory_usage_mb:.0f}MB > "
                    f"{self.config.memory_unhealthy_mb}MB"
                )
            elif info.unhealthy_reason.startswith("内存 "):
                info.unhealthy_reason = ""

            busy_seconds = (
                (datetime.now() - info.last_used_at).total_seconds()
                if info.status == "busy"
                else 0
            )
            lease_limit = (
                self.config.wechat_article_lease_timeout
                if info.purpose == "wechat_article"
                else self.config.generic_busy_lease_timeout
            )
            stale_lease = info.status == "busy" and busy_seconds > max(60, lease_limit)
            if stale_lease:
                await self._restart_chrome_for_recovery(
                    info,
                    reason=(
                        f"{info.purpose} 租约持续 {busy_seconds:.0f}s，"
                        f"超过上限 {lease_limit}s"
                    ),
                )
            elif not cdp_healthy:
                await self._restart_chrome_for_recovery(
                    info,
                    reason=f"CDP 健康检查失败: {health_error}",
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[HealthCheck] 容器检查失败 container=%s error=%s",
                info.container_name,
                exc,
            )

    async def _query_cdp_health(self, info: ContainerInfo) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{info.api_url}/health",
                    headers=self._auth_headers(),
                    timeout=5,
                )
            payload = response.json() if response.content else {}
            healthy = bool(
                response.status_code == 200
                and payload.get("chrome") is not False
                and payload.get("cdp") is not False
            )
            return healthy, "" if healthy else f"HTTP {response.status_code} {payload}"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    async def _wait_cdp_recovered(
        self,
        info: ContainerInfo,
        *,
        timeout: float = 30.0,
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            healthy, _error = await self._query_cdp_health(info)
            if healthy:
                return True
            await asyncio.sleep(1)
        return False

    async def _restart_chrome_for_recovery(
        self,
        info: ContainerInfo,
        *,
        reason: str,
    ) -> bool:
        if info.recovery_in_progress:
            return False
        if info.last_recovery_at and (
            datetime.now() - info.last_recovery_at
        ).total_seconds() < max(30, self.config.health_check_interval):
            return False

        info.recovery_in_progress = True
        info.last_recovery_at = datetime.now()
        task_id = info.task_id or ""
        logger.error(
            "[BrowserRecovery] 重启异常 Chrome | container=%s task=%s purpose=%s reason=%s",
            info.container_name,
            task_id,
            info.purpose,
            reason,
        )
        recovered = False
        try:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{info.api_url}/chrome/restart",
                        headers=self._auth_headers(),
                        timeout=20,
                    )
                if response.status_code == 200:
                    recovered = await self._wait_cdp_recovered(info, timeout=20)
            except Exception:
                recovered = False

            if not recovered:
                client = self._get_docker_client()

                def _restart_container() -> None:
                    client.containers.get(info.container_id).restart(timeout=5)

                await asyncio.to_thread(_restart_container)
                recovered = await self._wait_cdp_recovered(info, timeout=30)

            info.cdp_healthy = recovered
            if recovered:
                info.unhealthy_reason = ""
                info.consecutive_errors = 0
                info.last_used_at = datetime.now()
            else:
                info.unhealthy_reason = f"浏览器自动恢复失败: {reason}"[:300]

            from api.services.notifications import notify_event_background

            purpose_label = (
                "公众号文章读取"
                if info.purpose == "wechat_article"
                else info.purpose or "浏览器任务"
            )
            reason_label = (
                "浏览器租约超时"
                if "租约持续" in reason
                else "Chrome CDP 不可用"
            )
            notify_event_background(
                event=(
                    "browser.container.recovered"
                    if recovered
                    else "browser.container.recovery_failed"
                ),
                title=("浏览器异常已自动恢复" if recovered else "浏览器异常恢复失败"),
                content=(
                    "**结论**\n"
                    f"- {'已自动恢复，任务将重试' if recovered else '自动恢复失败，需要检查'}\n\n"
                    "**影响**\n"
                    f"- 用途：{purpose_label}\n"
                    f"- 原因：{reason_label}"
                ),
                level="warning" if recovered else "critical",
                source="browser_manager",
                task_id=task_id or None,
                context={
                    "container": info.container_name,
                    "purpose": info.purpose,
                    "recovered": recovered,
                },
                dedupe_key=info.purpose or "browser",
                cooldown_seconds=600 if recovered else 300,
            )
            return recovered
        except Exception as exc:  # noqa: BLE001
            info.cdp_healthy = False
            info.unhealthy_reason = f"浏览器自动恢复异常: {exc}"[:300]
            logger.error(
                "[BrowserRecovery] 恢复异常 container=%s error=%s",
                info.container_name,
                exc,
            )
            from api.services.notifications import notify_event_background

            notify_event_background(
                event="browser.container.recovery_failed",
                title="浏览器异常恢复失败",
                content=(
                    "**结论**\n- 自动恢复失败，需要检查\n\n"
                    f"**影响**\n- 用途：{info.purpose or '浏览器任务'}"
                ),
                level="critical",
                source="browser_manager",
                task_id=task_id or None,
                context={"container": info.container_name, "purpose": info.purpose},
                dedupe_key=info.purpose or "browser",
                cooldown_seconds=300,
            )
            return False
        finally:
            info.recovery_in_progress = False

    async def _query_container_memory(self, container_id: str) -> float:
        """通过 Docker API 查询容器内存使用量（MB）"""
        client = self._get_docker_client()

        def _stats() -> dict[str, Any]:
            container = client.containers.get(container_id)
            return container.stats(stream=False)

        stats = await asyncio.to_thread(_stats)
        mem_stats = stats.get("memory_stats", {})
        usage = mem_stats.get("usage", 0)
        # 减去 cache（不算真实内存占用）
        cache = mem_stats.get("stats", {}).get("cache", 0)
        real_usage = usage - cache
        return max(real_usage / (1024 * 1024), 0)

    # ── 新增：预热池 ─────────────────────────────────────

    async def _create_warm_container(self) -> None:
        """完成一个已预留容量的预热容器创建。"""
        info: ContainerInfo | None = None
        registered = False
        try:
            info = await self._create_container()
            async with self._lock:
                self.containers[info.container_id] = info
                info.status = "idle"
                info.last_used_at = datetime.now()
                registered = True
            logger.info(f"[WarmPool] 预热容器 {info.container_name} 就绪")
        except asyncio.CancelledError:
            if info is not None and not registered:
                await self._remove_docker_container(info.container_id, info.container_name)
            raise
        except Exception as exc:
            if info is not None and not registered:
                await self._remove_docker_container(info.container_id, info.container_name)
            logger.warning(f"[WarmPool] 预创建容器失败: {exc}")
        finally:
            async with self._lock:
                self._pending_creates = max(0, self._pending_creates - 1)

    async def _warm_pool_filler(self):
        """
        后台协程：维持预热池中有 N 个空闲容器。
        容器被取走后异步补充新的，Worker 需要切换时直接从池里拿，省 4-5s。
        """
        logger.info(f"[DockerProvider] 预热池已启动 | 目标大小={self.config.warm_pool_size}")
        # 首次启动等 5s，让主流程先跑
        await asyncio.sleep(5)

        while True:
            try:
                async with self._lock:
                    effective_limit = self._effective_container_limit()
                    idle_healthy_count = sum(
                        1 for info in self.containers.values()
                        if self._is_assignable(info)
                    )
                    total_active = sum(
                        1 for info in self.containers.values()
                        if info.status in ("busy", "idle", "starting")
                        and info.unhealthy_reason == ""
                        and info.cdp_healthy
                    )
                    need = self.config.warm_pool_size - idle_healthy_count
                    can_create = (
                        effective_limit
                        - total_active
                        - self._pending_creates
                    )
                    # Only enqueue one Docker creation wave. Business requests
                    # arriving while the pool refills can join the next wave.
                    to_create = max(
                        0,
                        min(
                            need,
                            can_create,
                            self.config.container_create_concurrency,
                        ),
                    )
                    self._pending_creates += to_create

                if to_create > 0:
                    logger.info(
                        f"[WarmPool] 空闲容器不足 ({idle_healthy_count}/{self.config.warm_pool_size})，"
                        f"预创建 {to_create} 个"
                    )
                    await asyncio.gather(
                        *(self._create_warm_container() for _ in range(to_create))
                    )

                await asyncio.sleep(1 if to_create > 0 else 10)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[DockerProvider] warm_pool_filler 异常: {e}")
                await asyncio.sleep(10)

    # ── 新增：错误上报 + 容器热切换 ──────────────────────

    async def report_error(self, task_id: Optional[str] = None, error_msg: str = "") -> None:
        """
        上报容器错误。累计连续错误数，超过阈值后标记 unhealthy。
        Worker 可据此决定是否触发热切换。
        
        Returns: None（调用方通过 info.consecutive_errors 判断是否需要热切换）
        """
        if not task_id or task_id not in self.task_map:
            return

        cid = self.task_map.get(task_id)
        if not cid or cid not in self.containers:
            return

        info = self.containers[cid]
        self._record_cdp_failure()
        info.consecutive_errors += 1
        logger.warning(
            f"[DockerProvider] 容器 {info.container_name} 错误上报 | "
            f"连续错误={info.consecutive_errors}/{self.config.max_consecutive_errors} | "
            f"错误: {error_msg[:100]}"
        )

        if info.consecutive_errors >= self.config.max_consecutive_errors:
            info.unhealthy_reason = (
                f"连续 {info.consecutive_errors} 次错误: {error_msg[:80]}"
            )
            logger.error(
                f"[DockerProvider] 容器 {info.container_name} 标记 unhealthy: "
                f"{info.unhealthy_reason}"
            )

    async def recover_task_container(
        self,
        task_id: Optional[str] = None,
        reason: str = "",
    ) -> bool:
        if not task_id:
            return False
        container_id = self.task_map.get(task_id)
        if not container_id:
            return False
        info = self.containers.get(container_id)
        if not info:
            return False
        return await self._restart_chrome_for_recovery(
            info,
            reason=reason or "任务上报浏览器异常",
        )

    async def reset_error_count(self, task_id: Optional[str] = None) -> None:
        """重置容器的连续错误计数（工具调用成功时调用）"""
        if not task_id or task_id not in self.task_map:
            return
        cid = self.task_map.get(task_id)
        if cid and cid in self.containers:
            self.containers[cid].consecutive_errors = 0

    async def should_hot_swap(self, task_id: Optional[str] = None) -> bool:
        """判断是否应该触发容器热切换"""
        if not task_id or task_id not in self.task_map:
            return False
        cid = self.task_map.get(task_id)
        if not cid or cid not in self.containers:
            return False
        info = self.containers[cid]
        return info.consecutive_errors >= self.config.max_consecutive_errors

    async def hot_swap_container(self, task_id: Optional[str] = None, purpose: str = "general") -> Optional[str]:
        """
        容器热切换：释放当前容器 → 异步销毁 → 从预热池或新建获取新容器。
        
        旧容器异步销毁不阻塞 Worker，新容器优先从预热池拿。
        返回新的 CDP WS URL，失败返回 None。
        """
        if not task_id:
            return None

        old_cid = self.task_map.get(task_id)
        old_name = ""
        if old_cid and old_cid in self.containers:
            old_name = self.containers[old_cid].container_name

        logger.warning(
            f"[HotSwap] 开始容器热切换 | task={task_id} | "
            f"旧容器={old_name} | purpose={purpose}"
        )

        # 1. 解除旧容器与 task 的绑定
        async with self._lock:
            old_cid = self.task_map.pop(task_id, None)
            if old_cid and old_cid in self.containers:
                old_info = self.containers[old_cid]
                old_info.status = "idle"
                old_info.task_id = None
                old_info.unhealthy_reason = old_info.unhealthy_reason or "热切换淘汰"

        # 2. 异步销毁旧容器（不阻塞）
        if old_cid:
            from core.background import spawn_background

            spawn_background(
                self._destroy_container(old_cid),
                name=f"chrome-destroy:{old_cid[:12]}",
            )

        # 3. 获取新容器（走正常流程，会优先从预热池拿空闲容器）
        try:
            new_ws_url = await self.get_cdp_endpoint(task_id=task_id, purpose=purpose)
            if new_ws_url:
                # 重置新容器的错误计数
                new_cid = self.task_map.get(task_id)
                if new_cid and new_cid in self.containers:
                    self.containers[new_cid].consecutive_errors = 0
                logger.info(
                    f"[HotSwap] 热切换成功 | task={task_id} | "
                    f"旧容器={old_name} → 新容器 | WS={new_ws_url}"
                )
                return new_ws_url
            else:
                logger.error(f"[HotSwap] 热切换失败：无法获取新容器 | task={task_id}")
                return None
        except Exception as e:
            logger.error(f"[HotSwap] 热切换异常: {e}")
            return None

    async def get_container_memory_mb(self, task_id: Optional[str] = None) -> float:
        """获取任务对应容器的内存使用量（MB）"""
        if not task_id or task_id not in self.task_map:
            return 0.0
        cid = self.task_map.get(task_id)
        if not cid or cid not in self.containers:
            return 0.0
        return self.containers[cid].memory_usage_mb

    async def should_restart_chrome(self, task_id: Optional[str] = None) -> bool:
        """
        基于内存的动态重启策略：
        - 内存低于 memory_restart_mb → 不重启（省时间）
        - 内存超过 memory_restart_mb → 需要重启
        """
        mem = await self.get_container_memory_mb(task_id)
        if mem <= 0:
            return True  # 无法获取内存信息时，保守策略：重启
        return mem > self.config.memory_restart_mb


# ── 全局单例 ──────────────────────────────────────────────

_provider: Optional[BrowserProvider] = None
_docker_config_data: dict[str, Any] = {}


def configure_browser_provider(config: dict[str, Any] | None) -> None:
    """注入前端/Mongo 管理的 Chrome Docker 配置。"""
    global _docker_config_data
    _docker_config_data = dict(config or {})


async def reconfigure_browser_provider(
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Inject config and apply compatible changes to the live provider."""
    configure_browser_provider(config)
    next_config = _load_docker_config()
    provider = get_browser_provider()
    if isinstance(provider, DockerProvider) and next_config.enabled:
        return await provider.reconfigure(next_config)
    return {
        "restart_required": True,
        "reason": "浏览器 Provider 模式切换需重启服务",
    }


def _apply_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """环境变量只作为部署兜底覆盖，不再读取 config.json。"""
    result = dict(config)
    if os.getenv("CHROME_DOCKER_ENABLED"):
        result["enabled"] = os.environ["CHROME_DOCKER_ENABLED"].lower() in {"1", "true", "yes"}
    if os.getenv("CHROME_DOCKER_NETWORK"):
        result["network"] = os.environ["CHROME_DOCKER_NETWORK"]
    if os.getenv("CHROME_DOCKER_WARM_POOL_SIZE"):
        result["warm_pool_size"] = int(os.environ["CHROME_DOCKER_WARM_POOL_SIZE"])
    return result


def _load_docker_config() -> ChromeDockerConfig:
    """从 Mongo 注入配置加载 Docker Chrome 配置。"""
    try:
        return ChromeDockerConfig.from_dict(_apply_env_overrides(_docker_config_data))
    except Exception as e:
        logger.warning(f"[browser_manager] Chrome Docker 配置无效，使用默认值: {e}")
        return ChromeDockerConfig.from_dict(_apply_env_overrides({}))


def get_browser_provider() -> BrowserProvider:
    """
    获取全局 BrowserProvider 单例。
    根据前端/Mongo 中 chrome_docker.enabled 决定返回 DockerProvider 还是 LocalProvider。
    """
    global _provider
    if _provider is not None:
        return _provider

    config = _load_docker_config()

    if config.enabled:
        logger.info("[browser_manager] Docker 模式已启用")
        _provider = DockerProvider(config)
    else:
        logger.info("[browser_manager] 本地模式（Docker 未启用）")
        _provider = LocalProvider()

    return _provider


async def shutdown_provider():
    """关闭 provider（应用退出时调用）"""
    global _provider
    if _provider:
        await _provider.shutdown()
        _provider = None
