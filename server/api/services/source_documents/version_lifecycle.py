"""Own the durable attempt and its heartbeat without downgrading ready evidence."""
from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Any
from uuid import uuid4

from api.dao import source_documents as source_dao
from api.dao.source_document_versions import ATTEMPT_LEASE_SECONDS, renew_version_attempt
from core.logger import get_logger

logger = get_logger("source_documents.lifecycle")


class SourceVersionAttempt:
    def __init__(self, db: Any, *, version_id: str, document_id: str,
                 content_hash: str, source_type: str, project_id: str = "", task_id: str = ""):
        self.db = db
        self.version_id = version_id
        self.attempt_id = uuid4().hex
        self.arguments = dict(version_id=version_id, document_id=document_id, content_hash=content_hash,
                              source_type=source_type, project_id=project_id, task_id=task_id, attempt_id=self.attempt_id)
        self.started = False
        self.committed = False
        self._heartbeat: asyncio.Task | None = None
        self._owner: asyncio.Task | None = None

    async def start(self) -> None:
        self._owner = asyncio.current_task()
        # Retain and await the atomic claim even if cancellation arrives while
        # Mongo is acknowledging it, then close the successfully acquired claim.
        write = asyncio.create_task(source_dao.begin_version(self.db, **self.arguments))
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError as exc:
            try:
                await write
                self.started = True
                await self.fail(exc)
            except Exception:
                logger.exception("来源归档认领中断 | version_id=%s", self.version_id)
            raise
        self.started = True
        self._heartbeat = asyncio.create_task(self._renew(), name=f"source-version:{self.attempt_id}")

    async def _renew(self) -> None:
        confirmed = time.monotonic()
        while True:
            await asyncio.sleep(30)
            try:
                owned = await renew_version_attempt(self.db, version_id=self.version_id, attempt_id=self.attempt_id)
                if owned:
                    confirmed = time.monotonic()
                    continue
            except Exception:
                logger.exception("来源归档心跳失败 | version_id=%s task_id=%s", self.version_id, self.arguments["task_id"])
                if time.monotonic() - confirmed < ATTEMPT_LEASE_SECONDS:
                    continue
            if self._owner and not self._owner.done():
                self._owner.cancel()
            return

    async def commit(self, payload: dict[str, Any]) -> dict[str, Any]:
        version = await source_dao.mark_version_ready(
            self.db, version_id=self.version_id, payload=payload, attempt_id=self.attempt_id,
        )
        self.committed = True
        await self.close()
        return version

    async def fail(self, error: BaseException) -> None:
        if not self.started or self.committed:
            return
        try:
            await source_dao.mark_version_error(
                self.db, self.version_id, str(error) or "来源归档被取消",
                attempt_id=self.attempt_id, interrupted=isinstance(error, asyncio.CancelledError),
            )
        except Exception:
            # Keep the original failure/cancellation; an expired durable lease
            # can be reclaimed if storage is unavailable during shutdown.
            logger.exception("来源归档收口失败 | version_id=%s task_id=%s", self.version_id, self.arguments["task_id"])

    async def close(self) -> None:
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat
            self._heartbeat = None
