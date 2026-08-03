"""Process-wide concurrency boundary for scholar collection providers."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from core.async_limiter import ResizableLimiter
from core.logger import get_logger


logger = get_logger("scholar_contact_runtime")
_MAX_EXTERNAL_PROVIDER_CONCURRENCY = 4
_limiter: ResizableLimiter | None = None
_limiter_loop: asyncio.AbstractEventLoop | None = None


def _get_limiter(capacity: int) -> ResizableLimiter:
    global _limiter, _limiter_loop
    loop = asyncio.get_running_loop()
    safe_capacity = max(1, min(int(capacity or 1), _MAX_EXTERNAL_PROVIDER_CONCURRENCY))
    if _limiter is None or _limiter_loop is not loop:
        _limiter = ResizableLimiter(safe_capacity)
        _limiter_loop = loop
    elif _limiter.limit != safe_capacity:
        _limiter.resize(safe_capacity)
    return _limiter


@asynccontextmanager
async def scholar_collection_slot(db, *, task_id: str = ""):
    """Bound all root, descendant and standalone scans through one limiter."""
    from api.services.info_collection.tuning import get_collection_runtime_tuning

    tuning = await get_collection_runtime_tuning(db)
    limiter = _get_limiter(tuning.company_scan_concurrency)
    await limiter.acquire()
    logger.info(
        "学者采集资源已分配 | task=%s active=%s/%s waiting=%s",
        task_id,
        limiter.in_use,
        limiter.limit,
        limiter.waiting,
    )
    try:
        yield
    finally:
        limiter.release()
