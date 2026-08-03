"""Process-wide LLM concurrency control and quota circuit breaking."""
from __future__ import annotations

import asyncio
import contextvars
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from core.async_limiter import ResizableLimiter
from core.logger import get_logger
from core.stream.errors import PipelineAbortError


logger = get_logger("llm_capacity")

_QUOTA_MARKERS = (
    "insufficient_quota",
    "exceeded your current quota",
    "token-limit",
    "rate limit",
    "ratelimiterror",
    "额度不足",
    "配额不足",
)


class LLMCapacityUnavailableError(PipelineAbortError):
    """The configured model provider cannot currently accept more work."""

    def __init__(
        self,
        *,
        retry_after_seconds: float,
        incident_id: int,
        detail: str = "",
    ) -> None:
        self.retry_after_seconds = max(1.0, float(retry_after_seconds))
        self.incident_id = max(1, int(incident_id))
        self.detail = str(detail or "")[:300]
        super().__init__(
            "模型额度或限流暂不可用，扫描已暂停并等待自动恢复 "
            f"({self.retry_after_seconds:.0f}s 后重试)"
        )


def _walk_errors(error: BaseException) -> list[BaseException]:
    pending = [error]
    visited: set[int] = set()
    values: list[BaseException] = []
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in visited:
            continue
        visited.add(identity)
        values.append(current)
        nested = getattr(current, "exceptions", None)
        if nested:
            pending.extend(item for item in nested if isinstance(item, BaseException))
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if isinstance(cause, BaseException):
            pending.append(cause)
        if isinstance(context, BaseException):
            pending.append(context)
    return values


def is_llm_capacity_error(error: BaseException) -> bool:
    """Recognize provider quota/rate-limit failures through wrapped exceptions."""
    if isinstance(error, LLMCapacityUnavailableError):
        return True
    for current in _walk_errors(error):
        if type(current).__name__ == "RateLimitError":
            return True
        message = f"{type(current).__name__}: {current}".casefold()
        if any(marker in message for marker in _QUOTA_MARKERS):
            return True
        if "429" in message and ("quota" in message or "limit" in message):
            return True
    return False


def find_llm_capacity_error(
    error: BaseException,
) -> LLMCapacityUnavailableError | None:
    """Return an already translated capacity error from a wrapped task group."""
    for current in _walk_errors(error):
        if isinstance(current, LLMCapacityUnavailableError):
            return current
    return None


LLM_CAPACITY_PRIORITY_STANDARD = "standard"
LLM_CAPACITY_PRIORITY_INTERACTIVE = "interactive"


_CAPACITY_PRIORITY: contextvars.ContextVar[str] = contextvars.ContextVar(
    "llm_capacity_priority",
    default=LLM_CAPACITY_PRIORITY_STANDARD,
)


@contextmanager
def llm_capacity_priority(priority: str):
    """Propagate the model-work priority to child tasks created in this scope."""
    normalized = str(priority or "").strip().casefold()
    if normalized != LLM_CAPACITY_PRIORITY_INTERACTIVE:
        normalized = LLM_CAPACITY_PRIORITY_STANDARD
    token = _CAPACITY_PRIORITY.set(normalized)
    try:
        yield
    finally:
        _CAPACITY_PRIORITY.reset(token)


@dataclass
class _LeaseState:
    is_probe: bool
    incident_id: int
    owner_task: asyncio.Task[Any] | None = None
    standard_slot: bool = False
    released: bool = False


_ACTIVE_LEASE: contextvars.ContextVar[_LeaseState | None] = contextvars.ContextVar(
    "llm_capacity_active_lease",
    default=None,
)


class LLMCapacityGuard:
    """Bound concurrent model work and half-open one probe after quota failures."""

    def __init__(
        self,
        *,
        max_concurrency: int = 12,
        cooldown_seconds: int = 120,
        max_cooldown_seconds: int = 900,
        interactive_reserve: int = 0,
        clock: Any = time.monotonic,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._interactive_reserve = max(0, int(interactive_reserve))
        self._slots = ResizableLimiter(max_concurrency)
        self._standard_slots = ResizableLimiter(
            self._standard_concurrency(max_concurrency)
        )
        self._cooldown_seconds = max(1, int(cooldown_seconds))
        self._max_cooldown_seconds = max(
            self._cooldown_seconds,
            int(max_cooldown_seconds),
        )
        self._clock = clock
        self._sleep = sleep
        self._open_until = 0.0
        self._failure_streak = 0
        self._incident_id = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._probe_lock: asyncio.Lock | None = None

    def _standard_concurrency(self, max_concurrency: int) -> int:
        total = max(1, int(max_concurrency))
        reserve = min(self._interactive_reserve, max(0, total - 1))
        return max(1, total - reserve)

    def configure(
        self,
        *,
        max_concurrency: int,
        cooldown_seconds: int,
        max_cooldown_seconds: int,
    ) -> None:
        normalized_concurrency = max(1, int(max_concurrency))
        self._slots.resize(normalized_concurrency)
        self._standard_slots.resize(
            self._standard_concurrency(normalized_concurrency)
        )
        self._cooldown_seconds = max(1, int(cooldown_seconds))
        self._max_cooldown_seconds = max(
            self._cooldown_seconds,
            int(max_cooldown_seconds),
        )

    def _get_probe_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._probe_lock is None:
            self._loop = loop
            self._probe_lock = asyncio.Lock()
        return self._probe_lock

    def _retry_after(self) -> float:
        return max(1.0, self._open_until - self._clock())

    def _record_capacity_failure(
        self,
        error: BaseException,
        *,
        is_probe: bool,
    ) -> LLMCapacityUnavailableError:
        if isinstance(error, LLMCapacityUnavailableError):
            return error
        new_incident = self._open_until == 0
        if new_incident or is_probe:
            self._failure_streak += 1
            cooldown = min(
                self._cooldown_seconds * (2 ** (self._failure_streak - 1)),
                self._max_cooldown_seconds,
            )
            self._open_until = self._clock() + cooldown
            if new_incident:
                self._incident_id += 1
            logger.warning(
                "模型容量熔断已开启 | incident=%s cooldown=%ss concurrency=%s error=%s",
                self._incident_id,
                cooldown,
                self._slots.limit,
                str(error)[:300],
            )
        return LLMCapacityUnavailableError(
            retry_after_seconds=self._retry_after(),
            incident_id=max(1, self._incident_id),
            detail=str(error),
        )

    def _record_probe_success(self, state: _LeaseState) -> None:
        if not state.is_probe:
            return
        self._open_until = 0.0
        self._failure_streak = 0
        logger.notice("模型容量探测恢复 | incident=%s", state.incident_id)

    async def wait_for_retry_window(self, incident_id: int = 0) -> None:
        """Wait until the current circuit cooldown expires so one caller can probe."""
        while self._open_until > self._clock():
            await self._sleep(self._retry_after())

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[dict[str, Any]]:
        """Acquire one model-work lease; nested model calls reuse the outer lease."""
        active = _ACTIVE_LEASE.get()
        current_task = asyncio.current_task()
        if (
            active is not None
            and not active.released
            and active.owner_task is current_task
        ):
            try:
                yield {
                    "nested": True,
                    "is_probe": active.is_probe,
                    "incident_id": active.incident_id,
                }
            except BaseException as error:
                if is_llm_capacity_error(error):
                    raise self._record_capacity_failure(
                        error,
                        is_probe=active.is_probe,
                    ) from error
                raise
            return

        probe_lock: asyncio.Lock | None = None
        state = _LeaseState(
            is_probe=False,
            incident_id=self._incident_id,
            owner_task=current_task,
        )
        standard_slot_acquired = False
        try:
            while True:
                retry_after = self._open_until - self._clock()
                if retry_after > 0:
                    await self._sleep(retry_after)
                    continue

                if self._open_until > 0:
                    probe_lock = self._get_probe_lock()
                    await probe_lock.acquire()
                    if self._open_until == 0:
                        probe_lock.release()
                        probe_lock = None
                        continue
                    retry_after = self._open_until - self._clock()
                    if retry_after > 0:
                        probe_lock.release()
                        probe_lock = None
                        await self._sleep(retry_after)
                        continue
                    state.is_probe = True
                    state.incident_id = max(1, self._incident_id)

                is_interactive = (
                    _CAPACITY_PRIORITY.get()
                    == LLM_CAPACITY_PRIORITY_INTERACTIVE
                )
                if not is_interactive:
                    await self._standard_slots.acquire()
                    standard_slot_acquired = True
                try:
                    await self._slots.acquire()
                except BaseException:
                    if standard_slot_acquired:
                        self._standard_slots.release()
                        standard_slot_acquired = False
                    raise
                if not state.is_probe and self._open_until > self._clock():
                    self._slots.release()
                    if standard_slot_acquired:
                        self._standard_slots.release()
                        standard_slot_acquired = False
                    continue
                state.standard_slot = standard_slot_acquired
                break
        except BaseException:
            if probe_lock is not None and probe_lock.locked():
                probe_lock.release()
            raise

        _ACTIVE_LEASE.set(state)
        try:
            yield {
                "nested": False,
                "is_probe": state.is_probe,
                "incident_id": state.incident_id,
            }
        except BaseException as error:
            if is_llm_capacity_error(error):
                raise self._record_capacity_failure(
                    error,
                    is_probe=state.is_probe,
                ) from error
            raise
        else:
            self._record_probe_success(state)
        finally:
            # Async-generator finalizers may run in a different Context. A token
            # reset would then raise before releasing the limiter and permanently
            # consume one model slot. Marking the shared state released also makes
            # stale copied contexts harmless.
            state.released = True
            if _ACTIVE_LEASE.get() is state:
                _ACTIVE_LEASE.set(None)
            try:
                self._slots.release()
            finally:
                if state.standard_slot:
                    self._standard_slots.release()
                if probe_lock is not None and probe_lock.locked():
                    probe_lock.release()

    def status(self) -> dict[str, Any]:
        interactive_reserve = max(
            0,
            self._slots.limit - self._standard_slots.limit,
        )
        return {
            "max_concurrency": self._slots.limit,
            "in_use": self._slots.in_use,
            "waiting": self._slots.waiting,
            "interactive_reserve": interactive_reserve,
            "standard_limit": self._standard_slots.limit,
            "standard_in_use": self._standard_slots.in_use,
            "standard_waiting": self._standard_slots.waiting,
            "circuit_open": self._open_until > self._clock(),
            "retry_after_seconds": (
                round(self._retry_after(), 1)
                if self._open_until > self._clock()
                else 0.0
            ),
            "incident_id": self._incident_id,
            "failure_streak": self._failure_streak,
        }


_GLOBAL_GUARD = LLMCapacityGuard(interactive_reserve=2)


def get_global_llm_capacity_guard() -> LLMCapacityGuard:
    return _GLOBAL_GUARD


def configure_global_llm_capacity(
    *,
    max_concurrency: int,
    cooldown_seconds: int,
    max_cooldown_seconds: int,
) -> None:
    _GLOBAL_GUARD.configure(
        max_concurrency=max_concurrency,
        cooldown_seconds=cooldown_seconds,
        max_cooldown_seconds=max_cooldown_seconds,
    )
