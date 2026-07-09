"""观测层 · 通用结构化日志 / 事件接入点（与上层框架解耦）。

设计目标（长生命周期系统的可扩展观测层）：
- **统一入口**：任意模块只需 `from core.observability import obs_log` 即可写观测日志/事件，
  无需关心存储与并发细节 —— 这是「新模块快速接入观测层」的唯一约定。
- **本地文件 + 内存索引**：日志写入本地 JSONL，并保留进程内环形索引用于 API 快速查询；
  不写 MongoDB，避免高频运行日志打爆业务数据库。
- **接口隔离**：业务只依赖 obs_log；具体存储由 LogStore 实现承接。
- **低开销**：写入是同步入队和本地 append，不依赖 MongoDB 或 event loop。

文档见 docs/OBSERVABILITY_API.md「接入新模块」。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Optional, Protocol

# 旧日志集合名，仅保留给历史数据清理逻辑复用；运行时不再读写 DB。
TASK_LOGS_COLLECTION = "task_logs"

# 级别由低到高（用于 min_level / 内存记录阈值过滤）
LEVELS: tuple[str, ...] = ("debug", "info", "notice", "warning", "error")


def _level_idx(level: str, default: str = "info") -> int:
    """级别 → 序号；非法级别回退 default。"""
    return LEVELS.index(level) if level in LEVELS else LEVELS.index(default)


class LogStore(Protocol):
    """Persistence interface for structured observation logs."""

    def append(self, row: dict[str, Any]) -> None:
        """Persist one log row."""

    def load_recent(self, limit: int) -> list[dict[str, Any]]:
        """Load recent rows for in-memory indexing."""


class LocalJsonlLogStore:
    """Append-only local JSONL log store.

    Files live under ``logs/observability`` by default.  The project gitignore
    excludes ``logs/``, so runtime logs stay local and are never committed.
    """

    def __init__(self, root: Path | None = None) -> None:
        configured = os.getenv("OBS_LOG_DIR")
        if root is not None:
            self.root = root.resolve()
        elif configured:
            self.root = Path(configured).resolve()
        else:
            self.root = (Path.cwd() / "logs" / "observability").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _file_for_ts(self, ts: float) -> Path:
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        return self.root / f"{day}.jsonl"

    def append(self, row: dict[str, Any]) -> None:
        payload = json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str)
        path = self._file_for_ts(float(row.get("ts") or time.time()))
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(payload)
                fh.write("\n")

    def load_recent(self, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        rows: deque[dict[str, Any]] = deque(maxlen=limit)
        for path in sorted(self.root.glob("*.jsonl")):
            try:
                with path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            value = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(value, dict):
                            rows.append(value)
            except FileNotFoundError:
                continue
        return list(rows)


class ObservabilityLogger:
    """进程内单例的观测日志收集器（本地持久化 + 线程安全内存索引）。"""

    _instance: "ObservabilityLogger | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, db: Any = None) -> None:
        self._db = None
        self._lock = threading.Lock()
        self._max_records = self._int_env("OBS_LOG_MAX_RECORDS", 10000, minimum=100)
        self._records = deque(maxlen=self._max_records)
        self._pending: list[dict[str, Any]] = []
        self._flush_task = None
        self._stop_flush = False
        # 内存记录级别阈值：低于此级别不进入观测 API（默认 info，丢弃 debug）。
        self._min_record_idx = _level_idx(os.getenv("OBS_LOG_MIN_LEVEL", "info"))
        self._store: LogStore | None = None
        if os.getenv("OBS_LOG_LOCAL_ENABLED", "1").lower() not in {"0", "false", "no"}:
            self._store = LocalJsonlLogStore()
            for row in self._store.load_recent(self._max_records):
                level = str(row.get("level") or "")
                if level in LEVELS and _level_idx(level) >= self._min_record_idx:
                    self._records.append(row)

    @staticmethod
    def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
        try:
            return max(minimum, int(os.getenv(name, str(default)) or default))
        except Exception:
            return default

    @classmethod
    def get_instance(cls) -> "ObservabilityLogger":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def set_db(self, db: Any) -> None:
        """兼容旧调用：观测日志运行时不再使用 MongoDB。"""
        self._db = None

    # ── 写入（业务/各模块唯一约定的接入点）──

    def log(
        self,
        message: str,
        *,
        task_id: str = "",
        project_id: str = "",
        source: str = "",
        level: str = "info",
        event: str = "",
        data: Optional[dict[str, Any]] = None,
        phase: str = "",
        agent: str = "",
        ts: Optional[float] = None,
    ) -> str:
        """记录一条观测日志/事件，返回 log_id。线程安全、不阻塞、不抛错。

        约定字段：
        - source：来源模块名（如 'task_runner' / 'mobile_agent' / 'xhs_pipeline'），便于按模块筛选
        - level：debug/info/notice/warning/error
        - event：可选事件类型（如 'task_start' / 'task_done'），便于结构化筛选
        - data：任意附加结构化字段
        """
        lvl = level if level in LEVELS else "info"
        # 低于内存记录阈值直接丢弃；控制台日志仍由标准 logger 负责。
        if _level_idx(lvl) < self._min_record_idx:
            return ""
        doc = {
            "log_id": uuid.uuid4().hex[:16],
            "ts": float(ts) if ts is not None else time.time(),
            "level": lvl,
            "source": source,
            "event": event,
            "message": str(message),
            "data": data or {},
            "project_id": project_id,
            "task_id": task_id,
            "phase": phase,
            "agent": agent,
        }
        with self._lock:
            self._records.append(doc)
        if self._store is not None:
            try:
                self._store.append(doc)
            except Exception:
                # Observability must never break business execution.
                pass
        return doc["log_id"]

    # ── 查询（只读当前进程内环形缓冲）──

    def query_logs(
        self,
        *,
        project_id: str = "",
        task_id: str = "",
        source: str = "",
        level: str = "",
        min_level: str = "",
        event: str = "",
        since: float | None = None,
        limit: int = 100,
        skip: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """分页查询内存日志，返回 (items, total)。按 ts 倒序。"""
        with self._lock:
            rows = list(self._records)

        def _keep(row: dict[str, Any]) -> bool:
            if project_id and row.get("project_id") != project_id:
                return False
            if task_id and row.get("task_id") != task_id:
                return False
            if source and row.get("source") != source:
                return False
            if event and row.get("event") != event:
                return False
            if level and row.get("level") != level:
                return False
            if not level and min_level in LEVELS:
                if _level_idx(str(row.get("level") or "")) < _level_idx(min_level):
                    return False
            if since is not None and float(row.get("ts") or 0) < since:
                return False
            return True

        filtered = [row for row in rows if _keep(row)]
        filtered.sort(key=lambda x: float(x.get("ts") or 0), reverse=True)
        total = len(filtered)
        page = filtered[max(0, skip):max(0, skip) + max(0, limit)]
        return [dict(item) for item in page], total

    def count_by_level(self, *, project_id: str = "", task_id: str = "") -> dict[str, int]:
        """按级别统计当前内存日志。"""
        rows, _ = self.query_logs(project_id=project_id, task_id=task_id, limit=self._max_records)
        counts: dict[str, int] = {}
        for row in rows:
            level = str(row.get("level") or "")
            if level:
                counts[level] = counts.get(level, 0) + 1
        return counts

    def evict_logs(
        self,
        *,
        project_id: str = "",
        task_id: str = "",
        task_ids: list[str] | None = None,
    ) -> int:
        """删除当前进程内指定项目/任务的日志。"""
        task_id_set = set(task_ids or [])

        def _keep(row: dict[str, Any]) -> bool:
            if project_id and row.get("project_id") == project_id:
                return False
            if task_id and row.get("task_id") == task_id:
                return False
            if task_id_set and row.get("task_id") in task_id_set:
                return False
            return True

        with self._lock:
            before = len(self._records)
            self._records = deque((r for r in self._records if _keep(r)), maxlen=self._max_records)
            return before - len(self._records)

    # ── 旧后台批量落库 API，占位兼容 ──

    def start_flusher(self) -> None:
        """兼容旧调用：内存环形缓冲无需后台落库。"""
        self._flush_task = None

    async def _flush_loop(self) -> None:
        """兼容旧调用：不执行任何 DB 写入。"""
        return None

    async def _flush_once(self) -> None:
        with self._lock:
            self._pending.clear()

    async def flush_pending(self) -> None:
        """兼容旧调用：清空旧待写队列占位，不落库。"""
        await self._flush_once()

    async def drain(self) -> None:
        """兼容旧调用：停止占位 flusher，不落库。"""
        self._stop_flush = True
        await self._flush_once()


def get_obs_logger() -> ObservabilityLogger:
    """获取全局观测日志收集器单例。"""
    return ObservabilityLogger.get_instance()


def obs_log(message: str, **kwargs: Any) -> str:
    """便捷写入观测日志/事件 —— 任意模块的统一接入点。

    示例：
        from core.observability import obs_log
        obs_log("任务启动", task_id=tid, project_id=pid, source="task_runner",
                level="notice", event="task_start", data={"type": task_type})
    """
    return ObservabilityLogger.get_instance().log(message, **kwargs)
