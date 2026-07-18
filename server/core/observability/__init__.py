"""观测层（core）。

通用结构化日志 / 事件接入点。任意模块：
    from core.observability import obs_log
    obs_log("...", task_id=..., source="...", level="info", event="...", data={...})

Token / 费用观测见 Sere1nGraph.graph.observability.TokenTracker。
两者由 api/routers/observability.py 汇聚为统一观测看板。
"""

from core.observability.logs import (
    LEVELS,
    TASK_LOGS_COLLECTION,
    ObservabilityLogger,
    get_obs_logger,
    obs_log,
)
from core.observability.context import observation_context
from core.observability.usage import record_llm_usage

__all__ = [
    "LEVELS",
    "TASK_LOGS_COLLECTION",
    "ObservabilityLogger",
    "get_obs_logger",
    "obs_log",
    "observation_context",
    "record_llm_usage",
]
