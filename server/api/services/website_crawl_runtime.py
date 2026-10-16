"""Crawl ownership, heartbeat and cancellation; discovery stays in its service."""
import asyncio
import time
from contextlib import suppress
from typing import Any
from uuid import uuid4

from api.dao import website_crawl as dao
from api.dao.website_crawl_runs import CRAWL_LEASE_SECONDS, interrupt_run
from core.logger import get_logger

logger = get_logger("website_crawl.runtime")


class WebsiteCrawlExecution:
    def __init__(self, db: Any, *, crawl_task_id: str, dry_run: bool):
        self.db = db
        self.crawl_task_id = crawl_task_id
        self.runtime_id = uuid4().hex
        self.dry_run = dry_run
        self.started = False
        self.finished = False
        self._owner: asyncio.Task | None = None
        self._heartbeat: asyncio.Task | None = None

    async def __aenter__(self):
        self._owner = asyncio.current_task()
        return self

    async def start(self, **arguments: Any) -> None:
        if self.dry_run:
            return
        claim = asyncio.create_task(dao.begin_task(
            self.db, crawl_task_id=self.crawl_task_id, runtime_id=self.runtime_id, **arguments,
        ))
        try:
            await asyncio.shield(claim)
        except asyncio.CancelledError:
            # Finish acknowledging an in-flight claim so context exit can close it.
            with suppress(Exception):
                await claim
                self.started = True
            raise
        except Exception:
            # Claim may have succeeded before page recovery failed. The owner
            # predicate makes cleanup safe even if another process owns the run.
            self.started = True
            raise
        self.started = True
        self._heartbeat = asyncio.create_task(self._renew(), name=f"website-crawl:{self.runtime_id}")

    def cancel(self) -> None:
        if self._owner and not self._owner.done():
            self._owner.cancel()

    async def _renew(self) -> None:
        confirmed = time.monotonic()
        while True:
            await asyncio.sleep(30)
            try:
                if await dao.heartbeat_task(self.db, crawl_task_id=self.crawl_task_id, runtime_id=self.runtime_id):
                    confirmed = time.monotonic()
                    continue
            except Exception:
                logger.exception("官网采集心跳失败 task_id=%s runtime_id=%s", self.crawl_task_id, self.runtime_id)
                if time.monotonic() - confirmed < CRAWL_LEASE_SECONDS:
                    continue
            self.cancel()
            return

    async def finish(self, **arguments: Any) -> None:
        await dao.finish_task(self.db, crawl_task_id=self.crawl_task_id, runtime_id=self.runtime_id, **arguments)
        self.finished = True

    async def drain(self, queue: asyncio.Queue, workers: list[asyncio.Task]) -> None:
        drained = asyncio.create_task(queue.join())
        try:
            done, _ = await asyncio.wait([drained, *workers], return_when=asyncio.FIRST_COMPLETED)
            for worker in done:
                if worker is not drained:
                    await worker  # Propagate fatal persistence failures instead of hanging join().
                    raise RuntimeError("官网采集 worker 意外退出")
            await drained
        finally:
            for task in [drained, *workers]:
                task.cancel()
            await asyncio.gather(drained, *workers, return_exceptions=True)

    async def __aexit__(self, exc_type, exc, traceback):
        if self._heartbeat:
            self._heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat
        if self.started and not self.finished:
            try:
                await interrupt_run(self.db, crawl_task_id=self.crawl_task_id, runtime_id=self.runtime_id,
                                    error=str(exc or "官网采集被中断"))
            except Exception:
                logger.exception("官网采集收口失败 task_id=%s runtime_id=%s", self.crawl_task_id, self.runtime_id)
