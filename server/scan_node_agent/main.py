"""Long-running pull worker for distributed scan work items."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections import Counter
from typing import Any

from .client import ControlPlaneClient, ControlPlaneError
from .config import NodeConfig
from .workers import WorkerFailure, WorkerRegistry


logger = logging.getLogger("sere1nfish.scan_node")


class NodeRuntime:
    def __init__(self, config: NodeConfig) -> None:
        self.config = config
        self.client = ControlPlaneClient(config)
        self.workers = WorkerRegistry()
        self.stop_event = asyncio.Event()
        self.active: dict[str, tuple[str, asyncio.Task[None]]] = {}

    def request_stop(self) -> None:
        self.stop_event.set()

    def _available_kinds(self) -> list[str]:
        counts = Counter(kind for kind, _task in self.active.values())
        limits = {
            "http_probe": self.config.http_slots,
            "browser_probe": self.config.browser_slots,
        }
        return [kind for kind, limit in limits.items() if counts[kind] < limit]

    def _usage(self) -> dict[str, int]:
        counts = Counter(kind for kind, _task in self.active.values())
        return {
            "http_probe_slots": counts["http_probe"],
            "browser_probe_slots": counts["browser_probe"],
            "active_work_items": len(self.active),
        }

    async def run(self) -> None:
        identity = await self.client.register()
        logger.info("节点已就绪: node_id=%s name=%s", identity["node_id"], self.config.display_name)
        loops = [
            asyncio.create_task(self._heartbeat_loop(), name="node-heartbeat"),
            asyncio.create_task(self._dispatch_loop(), name="node-dispatch"),
        ]
        try:
            done, _pending = await asyncio.wait(loops, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                exception = task.exception()
                if exception:
                    raise exception
        finally:
            self.stop_event.set()
            for task in loops:
                task.cancel()
            await asyncio.gather(*loops, return_exceptions=True)
            active = [task for _kind, task in self.active.values()]
            for task in active:
                task.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)
            await self.workers.close()
            await self.client.close()

    async def _heartbeat_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                response = await self.client.heartbeat(
                    usage=self._usage(),
                    active_work_item_ids=list(self.active),
                )
                for work_item_id in response.get("cancel_work_item_ids") or []:
                    active = self.active.get(str(work_item_id))
                    if active:
                        active[1].cancel()
            except ControlPlaneError as exc:
                if exc.status_code == 401:
                    raise
                logger.warning("节点心跳失败: %s", exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("节点心跳失败: %s", exc)
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.config.heartbeat_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def _dispatch_loop(self) -> None:
        backoff = 1.0
        while not self.stop_event.is_set():
            kinds = self._available_kinds()
            if not kinds:
                await asyncio.sleep(0.1)
                continue
            try:
                leased = await self.client.lease(kinds, wait_seconds=10)
                backoff = 1.0
            except ControlPlaneError as exc:
                if exc.status_code == 401:
                    raise
                logger.warning("领取工作失败: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 20.0)
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("领取工作失败: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 20.0)
                continue
            if not leased:
                continue
            work = leased["work"]
            work_item_id = str(work["work_item_id"])
            kind = str(work["kind"])
            task = asyncio.create_task(
                self._execute_work(leased),
                name=f"scan-work:{work_item_id}",
            )
            self.active[work_item_id] = (kind, task)

    async def _execute_work(self, leased: dict[str, Any]) -> None:
        work = leased["work"]
        work_item_id = str(work["work_item_id"])
        lease_token = str(leased["lease_token"])
        event_seq = int(work.get("last_event_seq") or 0) + 1
        execution: asyncio.Task[dict[str, Any]] | None = None
        renewal: asyncio.Task[None] | None = None
        try:
            await self.client.started(work_item_id, lease_token, event_seq)
            execution = asyncio.create_task(
                self.workers.get(str(work["kind"])).execute(
                    dict(work.get("payload") or {}),
                    leased.get("proxy"),
                ),
                name=f"worker:{work_item_id}",
            )
            renewal = asyncio.create_task(
                self._renew_loop(work_item_id, lease_token),
                name=f"lease-renew:{work_item_id}",
            )
            done, _pending = await asyncio.wait(
                {execution, renewal},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if renewal in done:
                renewal.result()
                raise RuntimeError("租约续期循环意外结束")
            result = execution.result()
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)
            await self.client.completed(
                work_item_id,
                lease_token,
                event_seq + 1,
                result,
            )
            logger.info("工作完成: work_item_id=%s kind=%s", work_item_id, work["kind"])
        except asyncio.CancelledError:
            await self._report_failure(
                work_item_id,
                lease_token,
                event_seq + 1,
                error_code="node_interrupted",
                error="节点执行被取消",
                retryable=True,
            )
            raise
        except WorkerFailure as exc:
            await self._report_failure(
                work_item_id,
                lease_token,
                event_seq + 1,
                error_code=exc.code,
                error=str(exc),
                retryable=exc.retryable,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("工作失败: work_item_id=%s error=%s", work_item_id, exc)
            await self._report_failure(
                work_item_id,
                lease_token,
                event_seq + 1,
                error_code=type(exc).__name__[:100],
                error=str(exc),
                retryable=True,
            )
        finally:
            for child in (execution, renewal):
                if child and not child.done():
                    child.cancel()
            await asyncio.gather(
                *(child for child in (execution, renewal) if child),
                return_exceptions=True,
            )
            self.active.pop(work_item_id, None)

    async def _renew_loop(self, work_item_id: str, lease_token: str) -> None:
        while True:
            await asyncio.sleep(25)
            await self.client.renew(work_item_id, lease_token)

    async def _report_failure(
        self,
        work_item_id: str,
        lease_token: str,
        event_seq: int,
        *,
        error_code: str,
        error: str,
        retryable: bool,
    ) -> None:
        try:
            await self.client.failed(
                work_item_id,
                lease_token,
                event_seq,
                error_code=error_code,
                error=error or error_code,
                retryable=retryable,
            )
        except Exception as report_error:  # noqa: BLE001
            logger.warning("工作失败状态回传失败: work_item_id=%s error=%s", work_item_id, report_error)


async def _async_main() -> None:
    config = NodeConfig.from_env()
    runtime = NodeRuntime(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, runtime.request_stop)
    run_task = asyncio.create_task(runtime.run(), name="scan-node-runtime")
    stop_task = asyncio.create_task(runtime.stop_event.wait(), name="scan-node-stop")
    done, _pending = await asyncio.wait(
        {run_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if stop_task in done and not run_task.done():
        run_task.cancel()
    await asyncio.gather(run_task, return_exceptions=False)
    stop_task.cancel()
    await asyncio.gather(stop_task, return_exceptions=True)


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
