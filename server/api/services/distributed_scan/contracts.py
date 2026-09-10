"""Stable control-plane contracts shared by services and node clients."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal


WorkKind = Literal["http_probe", "browser_probe"]
ProxyMode = Literal["none", "best_effort", "required"]
LocalExecutor = Callable[[], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class WorkSpec:
    kind: str
    payload: dict[str, Any]
    project_id: str = ""
    task_id: str = ""
    target_id: str = ""
    requirements: tuple[str, ...] = ()
    proxy_profile_id: str = ""
    proxy_mode: ProxyMode = "none"
    affinity_key: str = ""
    priority: int = 50
    max_attempts: int = 3
    idempotency_key: str = ""


@dataclass(frozen=True, slots=True)
class GatewayPolicy:
    enabled: bool = False
    kinds: frozenset[str] = field(default_factory=frozenset)
    project_ids: frozenset[str] = field(default_factory=frozenset)
    fallback_local: bool = True
    wait_seconds: float = 60.0
    poll_seconds: float = 0.5
    batch_size: int = 20
    dispatch_concurrency: int = 8
    proxy_profile_id: str = ""
    proxy_mode: ProxyMode = "none"

    def permits(self, *, kind: str, project_id: str) -> bool:
        if not self.enabled or kind not in self.kinds:
            return False
        return not self.project_ids or project_id in self.project_ids
