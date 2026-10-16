"""Recover expired persistence leases without scheduling or resuming user tasks."""
import asyncio

from api.dao.source_document_versions import expire_abandoned_attempts
from api.dao.website_crawl_runs import expire_abandoned_runs
from core.background import spawn_background
from core.logger import get_logger
from core.observability import obs_log

logger = get_logger("collection_lease_recovery")


class CollectionLeaseRecovery:
    def __init__(self):
        self._task = None

    async def run_once(self, db):
        sources, crawls = await asyncio.gather(expire_abandoned_attempts(db), expire_abandoned_runs(db))
        if sources or crawls:
            obs_log("已收口过期采集租约", source="collection_recovery", event="collection_leases_expired",
                    level="warning", data={"source_attempts": sources, "website_crawls": crawls})
        return sources, crawls

    def start(self, db):
        if self._task is None or self._task.done():
            self._task = spawn_background(self._run(db), name="collection-lease-recovery")

    async def _run(self, db):
        while True:
            try:
                await self.run_once(db)
            except Exception:
                logger.exception("采集租约恢复失败，将在下一轮重试")
            await asyncio.sleep(30)

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
