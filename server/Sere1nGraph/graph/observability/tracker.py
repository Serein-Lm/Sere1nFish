"""
TokenTracker — 多层级 Token 观测 Callback

层级：全局 → 项目 → 任务 → 流程阶段 → Agent
一个 callback 实例注入 LLM，自动追踪所有层级。
业务代码通过 push_context / pop_context 管理当前层级。

使用方式：
    tracker = get_global_tracker()

    # 注入到 LLM
    llm = ChatOpenAI(..., callbacks=[tracker.callback])

    # 管理层级
    tracker.push_context(project_id="proj_1", task_id="task_1")
    tracker.push_context(phase="scenario")
    tracker.push_context(agent="web_tagging")
    # ... LLM 调用自动记录 ...
    tracker.pop_context()  # 退出 agent
    tracker.pop_context()  # 退出 phase
    tracker.pop_context()  # 退出 task

    # 查询
    tracker.get_stats()                    # 全局
    tracker.get_stats(project_id="proj_1") # 项目级
    tracker.get_stats(task_id="task_1")    # 任务级
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Optional
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from .pricing import calc_cost

_COLLECTION = "token_usage_records"


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default)) or default))
    except Exception:
        return default


def _float_env(name: str, default: float, *, minimum: float = 0.1) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default)) or default))
    except Exception:
        return default


# ── 层级上下文 ──

@dataclass
class ObservabilityContext:
    """当前观测上下文 — 标识 LLM 调用属于哪个层级"""
    project_id: str = ""
    task_id: str = ""
    task_type: str = ""  # 任务场景: url_scan/xhs_search/web_tagging/fofa_collect/...
    turn_id: str = ""     # 对话/任务轮次，用于全局细粒度 token 观测
    phase: str = ""      # pipeline 阶段: scenario/script/objection/finalize/scan/...
    agent: str = ""      # agent 名: web_tagging/xhs_profile/copywriting/...


# 每个 async task 独立的上下文栈
_context_stack: ContextVar[list[ObservabilityContext]] = ContextVar(
    "obs_context_stack", default=[]
)


def _current_context() -> ObservabilityContext:
    """获取当前上下文（栈顶），没有则返回空上下文"""
    stack = _context_stack.get()
    if stack:
        return stack[-1]
    return ObservabilityContext()


# ── 统计数据结构 ──

@dataclass
class UsageRecord:
    """单次 LLM 调用记录"""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_yuan: float = 0.0
    duration_ms: float = 0.0
    timestamp: float = 0.0
    # 层级标识
    project_id: str = ""
    task_id: str = ""
    task_type: str = ""
    turn_id: str = ""
    run_id: str = ""
    phase: str = ""
    agent: str = ""
    # LangGraph 节点（自动从 metadata 提取）
    langgraph_node: str = ""


@dataclass
class AggregatedStats:
    """聚合统计"""
    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost_yuan: float = 0.0
    total_duration_ms: float = 0.0
    by_model: dict[str, dict] = field(default_factory=lambda: defaultdict(lambda: {
        "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_yuan": 0.0,
    }))
    by_phase: dict[str, dict] = field(default_factory=lambda: defaultdict(lambda: {
        "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_yuan": 0.0,
    }))
    by_agent: dict[str, dict] = field(default_factory=lambda: defaultdict(lambda: {
        "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_yuan": 0.0,
    }))
    by_task_type: dict[str, dict] = field(default_factory=lambda: defaultdict(lambda: {
        "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_yuan": 0.0,
    }))

    def record(self, rec: UsageRecord):
        self.total_calls += 1
        self.total_input_tokens += rec.input_tokens
        self.total_output_tokens += rec.output_tokens
        self.total_tokens += rec.input_tokens + rec.output_tokens
        self.total_cost_yuan += rec.cost_yuan
        self.total_duration_ms += rec.duration_ms

        for key, bucket in [
            (rec.model, self.by_model),
            (rec.phase, self.by_phase),
            (rec.agent, self.by_agent),
            (rec.task_type, self.by_task_type),
        ]:
            if key:
                d = bucket[key]
                d["calls"] = d.get("calls", 0) + 1
                d["input_tokens"] = d.get("input_tokens", 0) + rec.input_tokens
                d["output_tokens"] = d.get("output_tokens", 0) + rec.output_tokens
                d["cost_yuan"] = d.get("cost_yuan", 0.0) + rec.cost_yuan

    def to_dict(self) -> dict:
        return {
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_yuan": round(self.total_cost_yuan, 6),
            "total_duration_ms": round(self.total_duration_ms, 1),
            "by_model": dict(self.by_model),
            "by_phase": dict(self.by_phase),
            "by_agent": dict(self.by_agent),
            "by_task_type": dict(self.by_task_type),
        }


# ── LangChain Callback Handler ──

class _TokenCallbackHandler(BaseCallbackHandler):
    """
    LangChain Callback — 自动提取 token 用量

    token 提取优先级（同 token_cost_guide.md）：
    1. response.llm_output.token_usage (非流式标准)
    2. generations[].message.usage_metadata (LangChain 标准)
    3. generations[].message.response_metadata (兜底)
    """

    def __init__(self, tracker: "TokenTracker"):
        super().__init__()
        self._tracker = tracker
        self._run_models: dict[UUID, str] = {}
        self._run_start_times: dict[UUID, float] = {}
        self._run_metadata: dict[UUID, dict[str, Any]] = {}

    def on_llm_start(self, serialized: dict, prompts: list[str], *,
                     run_id: UUID, **kwargs: Any) -> None:
        self._run_start_times[run_id] = time.time()
        # 提取 model_name
        model = (
            kwargs.get("invocation_params", {}).get("model_name")
            or kwargs.get("invocation_params", {}).get("model")
            or serialized.get("kwargs", {}).get("model_name")
            or serialized.get("kwargs", {}).get("model")
            or "unknown"
        )
        self._run_models[run_id] = model
        self._run_metadata[run_id] = self._metadata(kwargs.get("metadata"))

    def on_chat_model_start(self, serialized: dict, messages: list, *,
                            run_id: UUID, **kwargs: Any) -> None:
        self._run_start_times[run_id] = time.time()
        model = (
            kwargs.get("invocation_params", {}).get("model_name")
            or kwargs.get("invocation_params", {}).get("model")
            or serialized.get("kwargs", {}).get("model_name")
            or serialized.get("kwargs", {}).get("model")
            or "unknown"
        )
        self._run_models[run_id] = model
        self._run_metadata[run_id] = self._metadata(kwargs.get("metadata"))

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        model = self._run_models.pop(run_id, "unknown")
        start_time = self._run_start_times.pop(run_id, 0)
        start_metadata = self._run_metadata.pop(run_id, {})
        duration_ms = (time.time() - start_time) * 1000 if start_time else 0

        input_tokens, output_tokens = self._extract_tokens(response)
        if input_tokens == 0 and output_tokens == 0:
            return

        cost = calc_cost(model, input_tokens, output_tokens)
        ctx = _current_context()

        # 从 LangGraph metadata 提取节点名
        metadata = {**start_metadata, **self._metadata(kwargs.get("metadata"))}
        langgraph_node = str(metadata.get("langgraph_node") or "")
        turn_id = (
            ctx.turn_id
            or str(metadata.get("turn_id") or "")
            or str(metadata.get("conversation_id") or "")
            or str(metadata.get("thread_id") or "")
            or str(metadata.get("step_id") or "")
        )

        record = UsageRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_yuan=cost,
            duration_ms=duration_ms,
            timestamp=time.time(),
            project_id=ctx.project_id,
            task_id=ctx.task_id,
            task_type=ctx.task_type,
            turn_id=turn_id,
            run_id=str(run_id),
            phase=ctx.phase,
            agent=ctx.agent or langgraph_node,
            langgraph_node=langgraph_node,
        )
        self._tracker._record(record)

    @staticmethod
    def _metadata(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _extract_tokens(response: LLMResult) -> tuple[int, int]:
        """三条路径提取 token，优先级从高到低"""
        # 路径 1: llm_output.token_usage
        if response.llm_output:
            usage = response.llm_output.get("token_usage") or response.llm_output.get("usage")
            if usage:
                inp = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                out = usage.get("completion_tokens") or usage.get("output_tokens") or 0
                if inp or out:
                    return inp, out

        # 路径 2/3: generations[].message
        for gen_list in (response.generations or []):
            for gen in gen_list:
                msg = getattr(gen, "message", None)
                if msg:
                    # 路径 2: usage_metadata
                    um = getattr(msg, "usage_metadata", None)
                    if um:
                        inp = um.get("input_tokens", 0)
                        out = um.get("output_tokens", 0)
                        if inp or out:
                            return inp, out
                    # 路径 3: response_metadata
                    rm = getattr(msg, "response_metadata", None)
                    if rm:
                        usage = rm.get("token_usage") or rm.get("usage") or {}
                        inp = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                        out = usage.get("completion_tokens") or usage.get("output_tokens") or 0
                        if inp or out:
                            return inp, out
                # 路径 3 兜底: generation_info
                gi = getattr(gen, "generation_info", None) or {}
                usage = gi.get("token_usage") or gi.get("usage") or {}
                if usage:
                    inp = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                    out = usage.get("completion_tokens") or usage.get("output_tokens") or 0
                    if inp or out:
                        return inp, out

        return 0, 0


# ── TokenTracker 主类 ──

class TokenTracker:
    """
    多层级 Token 观测器

    层级：全局 → 项目 → 任务 → 流程阶段 → Agent

    所有 Agent 只需要注入 tracker.callback 到 LLM 的 callbacks 列表，
    层级管理通过 push_context / pop_context 完成。
    """

    def __init__(self, db=None):
        self._lock = Lock()
        self._max_records = _int_env("TOKEN_TRACKER_MAX_RECORDS", 5000, minimum=100)
        self._history_limit = _int_env("TOKEN_TRACKER_HISTORY_LIMIT", self._max_records, minimum=100)
        self._records = deque(maxlen=self._max_records)
        self._stats_cache: dict[str, AggregatedStats] = {}
        self._callback = _TokenCallbackHandler(self)
        self._db = db
        self._pending: list[UsageRecord] = []
        self._flush_task = None
        self._flush_interval = _float_env("TOKEN_TRACKER_FLUSH_INTERVAL_SECONDS", 1.5, minimum=0.2)
        self._stop_flush = False

    def set_db(self, db):
        """设置 MongoDB 连接（延迟注入，因为 db 在应用启动后才可用）。"""
        self._db = db

    async def load_history_from_db(self):
        """从 MongoDB 加载最近的历史记录到内存，保证重启后看板能回填数据。"""
        if self._db is None:
            return None

        try:
            collection = self._db[_COLLECTION]
            await collection.create_index("timestamp")
            await collection.create_index("project_id")
            await collection.create_index("task_id")
            await collection.create_index("task_type")
            await collection.create_index("turn_id")
            await collection.create_index("run_id")
            await collection.create_index([("project_id", 1), ("task_id", 1)])

            limit = min(self._history_limit, self._max_records)
            cursor = collection.find({}, {"_id": 0}).sort("timestamp", -1).limit(limit)
            docs = await cursor.to_list(limit)
            if not docs:
                return None

            loaded = 0
            with self._lock:
                existing = {self._record_key(item) for item in self._records}
                for doc in reversed(docs):
                    rec = self._doc_to_record(doc)
                    key = self._record_key(rec)
                    if key in existing:
                        continue
                    self._records.append(rec)
                    existing.add(key)
                    loaded += 1
                if loaded:
                    self._stats_cache.clear()

            logging.getLogger("observability").info(
                "[TokenTracker] 从 MongoDB 加载 %s 条历史记录", loaded
            )
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("observability").warning(
                "[TokenTracker] 加载历史记录失败: %s", exc
            )
        return None

    def start_flusher(self):
        """启动后台批量落库任务（应用启动后、有 event loop 时调用，幂等）。"""
        import asyncio

        if self._flush_task is not None and not self._flush_task.done():
            return
        self._stop_flush = False
        try:
            loop = asyncio.get_running_loop()
            self._flush_task = loop.create_task(self._flush_loop())
        except RuntimeError:
            self._flush_task = None

    async def _flush_loop(self):
        """周期性把待写队列批量写入 MongoDB。"""
        import asyncio

        while not self._stop_flush:
            await asyncio.sleep(self._flush_interval)
            await self._flush_once()

    async def _flush_once(self):
        """把当前待写队列一次性批量写入 MongoDB；失败则回队等待重试。"""
        if self._db is None:
            return
        with self._lock:
            batch = self._pending
            self._pending = []
        if not batch:
            return
        docs = [self._record_to_doc(item) for item in batch]
        try:
            await self._db[_COLLECTION].insert_many(docs, ordered=False)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._pending = batch + self._pending
            logging.getLogger("observability").warning(
                "[TokenTracker] 批量落库失败（%s 条已回队）: %s", len(batch), exc
            )

    async def flush_pending(self):
        """立即落库一次待写队列。"""
        await self._flush_once()

    async def drain(self):
        """停止后台 flusher 并落库剩余待写记录（应用关闭时调用）。"""
        self._stop_flush = True
        if self._flush_task is not None:
            import asyncio

            try:
                await asyncio.wait_for(self._flush_task, timeout=max(5.0, self._flush_interval + 1.0))
            except Exception:  # noqa: BLE001
                self._flush_task.cancel()
        await self._flush_once()

    @staticmethod
    def _record_to_doc(rec: UsageRecord) -> dict:
        """UsageRecord → MongoDB 文档。"""
        return {
            "model": rec.model,
            "input_tokens": rec.input_tokens,
            "output_tokens": rec.output_tokens,
            "total_tokens": rec.input_tokens + rec.output_tokens,
            "cost_yuan": rec.cost_yuan,
            "duration_ms": rec.duration_ms,
            "project_id": rec.project_id,
            "task_id": rec.task_id,
            "task_type": rec.task_type,
            "turn_id": rec.turn_id,
            "run_id": rec.run_id,
            "phase": rec.phase,
            "agent": rec.agent,
            "langgraph_node": rec.langgraph_node,
            "timestamp": rec.timestamp,
        }

    @staticmethod
    def _doc_to_record(doc: dict[str, Any]) -> UsageRecord:
        return UsageRecord(
            model=str(doc.get("model") or ""),
            input_tokens=int(doc.get("input_tokens") or 0),
            output_tokens=int(doc.get("output_tokens") or 0),
            cost_yuan=float(doc.get("cost_yuan") or 0.0),
            duration_ms=float(doc.get("duration_ms") or 0.0),
            timestamp=float(doc.get("timestamp") or 0.0),
            project_id=str(doc.get("project_id") or ""),
            task_id=str(doc.get("task_id") or ""),
            task_type=str(doc.get("task_type") or ""),
            turn_id=str(doc.get("turn_id") or ""),
            run_id=str(doc.get("run_id") or ""),
            phase=str(doc.get("phase") or ""),
            agent=str(doc.get("agent") or ""),
            langgraph_node=str(doc.get("langgraph_node") or ""),
        )

    @staticmethod
    def _record_key(rec: UsageRecord) -> tuple[Any, ...]:
        if rec.run_id:
            return ("run", rec.run_id)
        return (
            "raw",
            rec.timestamp,
            rec.project_id,
            rec.task_id,
            rec.model,
            rec.input_tokens,
            rec.output_tokens,
            rec.phase,
            rec.agent,
        )

    @staticmethod
    def _dict_key(row: dict[str, Any]) -> tuple[Any, ...]:
        run_id = str(row.get("run_id") or "")
        if run_id:
            return ("run", run_id)
        return (
            "raw",
            float(row.get("timestamp") or 0),
            str(row.get("project_id") or ""),
            str(row.get("task_id") or ""),
            str(row.get("model") or ""),
            int(row.get("input_tokens") or 0),
            int(row.get("output_tokens") or 0),
            str(row.get("phase") or ""),
            str(row.get("agent") or ""),
        )

    @property
    def callback(self) -> BaseCallbackHandler:
        """LangChain callback handler — 注入到 LLM 即可"""
        return self._callback

    # ── 层级上下文管理 ──

    @staticmethod
    def push_context(
        project_id: str = "",
        task_id: str = "",
        turn_id: str = "",
        phase: str = "",
        agent: str = "",
        task_type: str = "",
    ):
        """
        压入一层上下文

        可以只设置部分字段，未设置的继承上一层。
        """
        stack = _context_stack.get()
        parent = stack[-1] if stack else ObservabilityContext()
        new_ctx = ObservabilityContext(
            project_id=project_id or parent.project_id,
            task_id=task_id or parent.task_id,
            task_type=task_type or parent.task_type,
            turn_id=turn_id or parent.turn_id,
            phase=phase or parent.phase,
            agent=agent or parent.agent,
        )
        new_stack = stack + [new_ctx]
        _context_stack.set(new_stack)

    @staticmethod
    def pop_context():
        """弹出一层上下文"""
        stack = _context_stack.get()
        if stack:
            _context_stack.set(stack[:-1])

    # ── 记录 ──

    def _record(self, rec: UsageRecord):
        """内部方法：记录一条 usage。

        同步写入进程内环形缓冲，并放入批量落库队列。写 MongoDB 由后台
        flusher 完成，不阻塞 LLM callback。
        """
        with self._lock:
            self._records.append(rec)
            self._stats_cache.clear()
            self._pending.append(rec)

    # ── 查询 ──

    def get_stats(
        self,
        project_id: str = "",
        task_id: str = "",
        phase: str = "",
        agent: str = "",
        task_type: str = "",
    ) -> dict:
        """
        查询聚合统计

        不传参数 = 全局统计
        传 project_id = 项目级统计
        传 task_id = 任务级统计
        传 phase = 阶段级统计
        传 agent = Agent 级统计
        传 task_type = 任务场景级统计
        可组合：task_id + phase = 某任务某阶段的统计
        """
        cache_key = f"{project_id}|{task_id}|{phase}|{agent}|{task_type}"

        with self._lock:
            if cache_key in self._stats_cache:
                return self._stats_cache[cache_key].to_dict()

            stats = AggregatedStats()
            for rec in self._records:
                if project_id and rec.project_id != project_id:
                    continue
                if task_id and rec.task_id != task_id:
                    continue
                if phase and rec.phase != phase:
                    continue
                if agent and rec.agent != agent:
                    continue
                if task_type and rec.task_type != task_type:
                    continue
                stats.record(rec)

            self._stats_cache[cache_key] = stats
            return stats.to_dict()

    def get_records(
        self,
        project_id: str = "",
        task_id: str = "",
        limit: int = 50,
    ) -> list[dict]:
        """获取原始记录（仅进程内环形缓冲）。"""
        with self._lock:
            filtered = list(self._records)
            if project_id:
                filtered = [r for r in filtered if r.project_id == project_id]
            if task_id:
                filtered = [r for r in filtered if r.task_id == task_id]

            return [self._record_to_dict(r) for r in filtered[-limit:]]

    def evict_records(
        self,
        project_id: str = "",
        task_id: str = "",
    ) -> int:
        """
        从内存中清除指定项目/任务的记录。
        
        删除项目或任务时调用，只清理当前进程内环形缓冲。
        """
        def _keep(r: UsageRecord) -> bool:
            return not (
                (project_id and r.project_id == project_id)
                or (task_id and r.task_id == task_id)
            )

        with self._lock:
            before = len(self._records)
            self._records = deque((r for r in self._records if _keep(r)), maxlen=self._max_records)
            self._pending = [r for r in self._pending if _keep(r)]
            evicted = before - len(self._records)
            if evicted > 0:
                self._stats_cache.clear()
            return evicted

    async def get_records_async(
        self,
        project_id: str = "",
        task_id: str = "",
        limit: int = 50,
    ) -> list[dict]:
        """异步获取记录，MongoDB 优先，合并未落库内存记录。"""
        if self._db is None:
            return self.get_records(project_id, task_id, limit)

        try:
            await self._flush_once()
            query: dict[str, Any] = {}
            if project_id:
                query["project_id"] = project_id
            if task_id:
                query["task_id"] = task_id
            cursor = self._db[_COLLECTION].find(query, {"_id": 0}).sort("timestamp", -1).limit(limit)
            docs = await cursor.to_list(limit)
            rows = [self._record_to_dict(self._doc_to_record(doc)) for doc in docs]

            with self._lock:
                memory_rows = [
                    self._record_to_dict(rec)
                    for rec in self._records
                    if (not project_id or rec.project_id == project_id)
                    and (not task_id or rec.task_id == task_id)
                ]
            by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
            for row in rows + memory_rows:
                by_key[self._dict_key(row)] = row
            merged = sorted(by_key.values(), key=lambda x: float(x.get("timestamp") or 0), reverse=True)
            return merged[:limit]
        except Exception:
            return self.get_records(project_id, task_id, limit)

    async def get_stats_async(
        self,
        project_id: str = "",
        task_id: str = "",
        phase: str = "",
        agent: str = "",
        task_type: str = "",
    ) -> dict:
        """异步获取统计，MongoDB 优先，失败时回退到内存。"""
        if self._db is None:
            return self.get_stats(project_id, task_id, phase, agent, task_type)

        try:
            await self._flush_once()
            match: dict[str, Any] = {}
            if project_id:
                match["project_id"] = project_id
            if task_id:
                match["task_id"] = task_id
            if phase:
                match["phase"] = phase
            if agent:
                match["agent"] = agent
            if task_type:
                match["task_type"] = task_type

            total_pipeline = [
                {"$match": match} if match else {"$match": {}},
                {
                    "$group": {
                        "_id": None,
                        "total_calls": {"$sum": 1},
                        "total_input_tokens": {"$sum": "$input_tokens"},
                        "total_output_tokens": {"$sum": "$output_tokens"},
                        "total_cost_yuan": {"$sum": "$cost_yuan"},
                        "total_duration_ms": {"$sum": "$duration_ms"},
                    }
                },
            ]
            total = await self._db[_COLLECTION].aggregate(total_pipeline).to_list(1)
            if not total:
                return self.get_stats(project_id, task_id, phase, agent, task_type)

            row = total[0]
            row.pop("_id", None)
            row["total_input_tokens"] = int(row.get("total_input_tokens") or 0)
            row["total_output_tokens"] = int(row.get("total_output_tokens") or 0)
            row["total_calls"] = int(row.get("total_calls") or 0)
            row["total_tokens"] = row["total_input_tokens"] + row["total_output_tokens"]
            row["total_cost_yuan"] = round(float(row.get("total_cost_yuan") or 0), 6)
            row["total_duration_ms"] = round(float(row.get("total_duration_ms") or 0), 1)
            row["by_model"] = await self._db_group_by(match, "model")
            row["by_phase"] = await self._db_group_by(match, "phase")
            row["by_agent"] = await self._db_group_by(match, "agent")
            row["by_task_type"] = await self._db_group_by(match, "task_type")
            return row
        except Exception:
            return self.get_stats(project_id, task_id, phase, agent, task_type)

    async def _db_group_by(self, match: dict, field: str) -> dict:
        """MongoDB 按字段分组聚合。"""
        if self._db is None:
            return {}
        pipeline = [
            {"$match": match} if match else {"$match": {}},
            {"$match": {field: {"$ne": ""}}},
            {
                "$group": {
                    "_id": f"${field}",
                    "calls": {"$sum": 1},
                    "input_tokens": {"$sum": "$input_tokens"},
                    "output_tokens": {"$sum": "$output_tokens"},
                    "cost_yuan": {"$sum": "$cost_yuan"},
                }
            },
        ]
        try:
            rows = await self._db[_COLLECTION].aggregate(pipeline).to_list(200)
            return {
                str(row["_id"]): {
                    "calls": int(row.get("calls") or 0),
                    "input_tokens": int(row.get("input_tokens") or 0),
                    "output_tokens": int(row.get("output_tokens") or 0),
                    "cost_yuan": round(float(row.get("cost_yuan") or 0), 6),
                }
                for row in rows
                if row.get("_id")
            }
        except Exception:
            return {}

    async def get_hierarchy_async(self, project_id: str = "") -> dict:
        """获取层级视图；MongoDB 可用时从历史数据聚合。"""
        if self._db is None:
            return self.get_hierarchy(project_id)

        try:
            await self._flush_once()
            result: dict[str, Any] = {"global": await self.get_stats_async()}
            query: dict[str, Any] = {"project_id": {"$ne": ""}}
            if project_id:
                query["project_id"] = project_id
            project_ids = await self._db[_COLLECTION].distinct("project_id", query)
            projects: dict[str, Any] = {}
            for pid in sorted(str(item) for item in project_ids if item):
                proj_stats = await self.get_stats_async(project_id=pid)
                task_ids = await self._db[_COLLECTION].distinct(
                    "task_id",
                    {"project_id": pid, "task_id": {"$ne": ""}},
                )
                tasks: dict[str, Any] = {}
                for tid in sorted(str(item) for item in task_ids if item):
                    task_stats = await self.get_stats_async(project_id=pid, task_id=tid)
                    phases = await self._db[_COLLECTION].distinct(
                        "phase",
                        {"project_id": pid, "task_id": tid, "phase": {"$ne": ""}},
                    )
                    phase_stats = {
                        str(phase): await self.get_stats_async(project_id=pid, task_id=tid, phase=str(phase))
                        for phase in sorted(item for item in phases if item)
                    }
                    tasks[tid] = {"stats": task_stats, "phases": phase_stats}
                projects[pid] = {"stats": proj_stats, "tasks": tasks}
            result["projects"] = projects
            return result
        except Exception:
            return self.get_hierarchy(project_id)

    # ── 逐层列表查询（前端按需加载）──

    async def list_projects_async(self) -> list[dict]:
        """看板首页：每个项目一行摘要（含模型分布和时间）"""
        if self._db is not None:
            try:
                await self._flush_once()
                project_ids = await self._db[_COLLECTION].distinct("project_id", {"project_id": {"$ne": ""}})
                return [
                    {"project_id": pid, **await self.get_stats_async(project_id=pid)}
                    for pid in sorted(str(item) for item in project_ids if item)
                ]
            except Exception:
                pass
        with self._lock:
            pids = set(r.project_id for r in self._records if r.project_id)
        return [{"project_id": p, **self.get_stats(project_id=p)} for p in sorted(pids)]

    async def list_tasks_async(self, project_id: str) -> list[dict]:
        """项目详情页：每个任务一行摘要（含模型分布和时间）"""
        if self._db is not None:
            try:
                await self._flush_once()
                task_ids = await self._db[_COLLECTION].distinct(
                    "task_id",
                    {"project_id": project_id, "task_id": {"$ne": ""}},
                )
                return [
                    {"task_id": tid, **await self.get_stats_async(project_id=project_id, task_id=tid)}
                    for tid in sorted(str(item) for item in task_ids if item)
                ]
            except Exception:
                pass
        with self._lock:
            tids = set(r.task_id for r in self._records if r.project_id == project_id and r.task_id)
        return [{"task_id": t, **self.get_stats(project_id=project_id, task_id=t)} for t in sorted(tids)]

    async def list_agents_async(self, task_id: str) -> list[dict]:
        """任务详情页：每个 agent 一行"""
        if self._db is not None:
            try:
                await self._flush_once()
                agents = await self._db[_COLLECTION].distinct(
                    "agent",
                    {"task_id": task_id, "agent": {"$ne": ""}},
                )
                return [
                    {"agent": agent, **await self.get_stats_async(task_id=task_id, agent=agent)}
                    for agent in sorted(str(item) for item in agents if item)
                ]
            except Exception:
                pass
        with self._lock:
            agents = set(r.agent for r in self._records if r.task_id == task_id and r.agent)
        return [{"agent": a, **self.get_stats(task_id=task_id, agent=a)} for a in sorted(agents)]

    @staticmethod
    def _record_to_dict(r: UsageRecord) -> dict:
        return {
            "model": r.model,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "total_tokens": r.input_tokens + r.output_tokens,
            "cost_yuan": round(r.cost_yuan, 6),
            "duration_ms": round(r.duration_ms, 1),
            "project_id": r.project_id,
            "task_id": r.task_id,
            "task_type": r.task_type,
            "turn_id": r.turn_id,
            "run_id": r.run_id,
            "phase": r.phase,
            "agent": r.agent,
            "langgraph_node": r.langgraph_node,
            "timestamp": r.timestamp,
        }

    def get_turns(self, project_id: str = "", task_id: str = "", limit: int = 100) -> list[dict]:
        """按轮次聚合 token 用量；无 turn_id 时退化为单次 LLM run。"""
        with self._lock:
            records = list(self._records)
        return self._turns_from_records(records, project_id=project_id, task_id=task_id, limit=limit)

    async def get_turns_async(self, project_id: str = "", task_id: str = "", limit: int = 100) -> list[dict]:
        """按轮次聚合 token 用量，MongoDB 可用时从历史记录读取。"""
        if self._db is None:
            return self.get_turns(project_id=project_id, task_id=task_id, limit=limit)

        try:
            await self._flush_once()
            query: dict[str, Any] = {}
            if project_id:
                query["project_id"] = project_id
            if task_id:
                query["task_id"] = task_id
            record_limit = min(max(limit * 25, 500), max(self._history_limit, limit))
            cursor = self._db[_COLLECTION].find(query, {"_id": 0}).sort("timestamp", -1).limit(record_limit)
            docs = await cursor.to_list(record_limit)
            records = [self._doc_to_record(doc) for doc in docs]

            with self._lock:
                records.extend(
                    rec
                    for rec in self._records
                    if (not project_id or rec.project_id == project_id)
                    and (not task_id or rec.task_id == task_id)
                )
            by_key = {self._record_key(rec): rec for rec in records}
            return self._turns_from_records(
                list(by_key.values()),
                project_id=project_id,
                task_id=task_id,
                limit=limit,
            )
        except Exception:
            return self.get_turns(project_id=project_id, task_id=task_id, limit=limit)

    def _turns_from_records(
        self,
        records: list[UsageRecord],
        *,
        project_id: str = "",
        task_id: str = "",
        limit: int = 100,
    ) -> list[dict]:
        groups: dict[str, dict[str, Any]] = {}
        for rec in records:
            if project_id and rec.project_id != project_id:
                continue
            if task_id and rec.task_id != task_id:
                continue

            key = rec.turn_id or rec.run_id or f"{rec.timestamp:.6f}"
            group = groups.setdefault(
                key,
                {
                    "turn_key": key,
                    "turn_id": rec.turn_id,
                    "project_id": rec.project_id,
                    "task_id": rec.task_id,
                    "task_type": rec.task_type,
                    "started_at": rec.timestamp,
                    "ended_at": rec.timestamp,
                    "total_calls": 0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_tokens": 0,
                    "total_cost_yuan": 0.0,
                    "total_duration_ms": 0.0,
                    "calls": [],
                    "by_model": defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_yuan": 0.0}),
                    "by_phase": defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_yuan": 0.0}),
                    "by_agent": defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_yuan": 0.0}),
                    "by_task_type": defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_yuan": 0.0}),
                },
            )
            group["started_at"] = min(group["started_at"], rec.timestamp)
            group["ended_at"] = max(group["ended_at"], rec.timestamp)
            group["total_calls"] += 1
            group["total_input_tokens"] += rec.input_tokens
            group["total_output_tokens"] += rec.output_tokens
            group["total_tokens"] += rec.input_tokens + rec.output_tokens
            group["total_cost_yuan"] += rec.cost_yuan
            group["total_duration_ms"] += rec.duration_ms
            group["calls"].append(
                {
                    "model": rec.model,
                    "input_tokens": rec.input_tokens,
                    "output_tokens": rec.output_tokens,
                    "total_tokens": rec.input_tokens + rec.output_tokens,
                    "cost_yuan": round(rec.cost_yuan, 6),
                    "duration_ms": round(rec.duration_ms, 1),
                    "project_id": rec.project_id,
                    "task_id": rec.task_id,
                    "task_type": rec.task_type,
                    "turn_id": rec.turn_id,
                    "run_id": rec.run_id,
                    "phase": rec.phase,
                    "agent": rec.agent,
                    "langgraph_node": rec.langgraph_node,
                    "timestamp": rec.timestamp,
                }
            )

            for key_name, bucket_name in (
                (rec.model, "by_model"),
                (rec.phase, "by_phase"),
                (rec.agent, "by_agent"),
                (rec.task_type, "by_task_type"),
            ):
                if not key_name:
                    continue
                bucket = group[bucket_name][key_name]
                bucket["calls"] += 1
                bucket["input_tokens"] += rec.input_tokens
                bucket["output_tokens"] += rec.output_tokens
                bucket["cost_yuan"] += rec.cost_yuan

        rows = []
        for group in groups.values():
            item = dict(group)
            item["total_cost_yuan"] = round(item["total_cost_yuan"], 6)
            item["total_duration_ms"] = round(item["total_duration_ms"], 1)
            item["calls"] = sorted(item["calls"], key=lambda x: x["timestamp"])
            for idx, call in enumerate(item["calls"], start=1):
                call["call_index"] = idx
            for field in ("by_model", "by_phase", "by_agent", "by_task_type"):
                item[field] = {
                    k: {
                        "calls": v["calls"],
                        "input_tokens": v["input_tokens"],
                        "output_tokens": v["output_tokens"],
                        "cost_yuan": round(v["cost_yuan"], 6),
                    }
                    for k, v in item[field].items()
                }
            rows.append(item)

        rows.sort(key=lambda x: x["ended_at"], reverse=True)
        return rows[:limit]

    def get_hierarchy(self, project_id: str = "") -> dict:
        """
        获取层级视图 — 前端看板用

        返回：
        {
            "global": {...},
            "projects": {
                "proj_1": {
                    "stats": {...},
                    "tasks": {
                        "task_1": {
                            "stats": {...},
                            "phases": {
                                "scenario": {"stats": {...}},
                                ...
                            }
                        }
                    }
                }
            }
        }
        """
        result: dict[str, Any] = {"global": self.get_stats()}

        # 收集所有 project_id
        with self._lock:
            project_ids = set(r.project_id for r in self._records if r.project_id)
            if project_id:
                project_ids = {project_id} & project_ids

        projects: dict = {}
        for pid in sorted(project_ids):
            proj_stats = self.get_stats(project_id=pid)
            # 收集该项目的 task_id
            with self._lock:
                task_ids = set(
                    r.task_id for r in self._records
                    if r.project_id == pid and r.task_id
                )
            tasks: dict = {}
            for tid in sorted(task_ids):
                task_stats = self.get_stats(project_id=pid, task_id=tid)
                # 收集该任务的 phase
                with self._lock:
                    phases = set(
                        r.phase for r in self._records
                        if r.task_id == tid and r.phase
                    )
                phase_stats = {
                    p: self.get_stats(task_id=tid, phase=p)
                    for p in sorted(phases)
                }
                tasks[tid] = {"stats": task_stats, "phases": phase_stats}
            projects[pid] = {"stats": proj_stats, "tasks": tasks}

        result["projects"] = projects
        return result


# ── 全局单例 ──

_global_tracker: Optional[TokenTracker] = None


def get_global_tracker() -> TokenTracker:
    """获取全局 TokenTracker 单例"""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = TokenTracker()
    return _global_tracker
