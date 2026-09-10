"""Feature-gated local/remote execution gateway for scan pipeline stages."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import distributed_work as work_dao
from api.dao import scan_nodes as nodes_dao
from core.logger import get_logger

from .contracts import GatewayPolicy, WorkSpec
from .settings import get_gateway_policy
from .work_service import cancel_work, enqueue_work


logger = get_logger("distributed_scan.gateway")
BatchExecutor = Callable[[list[str]], Awaitable[dict[str, dict[str, Any]]]]


class DistributedExecutionGateway:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self.db = db

    async def _policy(self, *, kind: str) -> GatewayPolicy:
        try:
            return await get_gateway_policy(kind)
        except Exception as exc:  # noqa: BLE001
            # Distributed execution is an optional acceleration layer. A config
            # outage must not stop the established local collection path.
            logger.warning("分布式扫描配置读取失败，保持本机执行: %s", exc)
            return GatewayPolicy(enabled=False, kinds=frozenset({kind}))

    async def execute_url_batches(
        self,
        *,
        kind: str,
        urls: list[str],
        project_id: str,
        task_id: str,
        target_id: str,
        timeout: float,
        concurrency: int,
        local_batch: BatchExecutor,
    ) -> dict[str, dict[str, Any]]:
        if not urls:
            return {}
        policy = await self._policy(kind=kind)
        if not policy.permits(kind=kind, project_id=project_id):
            return await local_batch(urls)
        if not await nodes_dao.has_eligible_node(self.db, capability=kind):
            if policy.proxy_mode == "required" or not policy.fallback_local:
                raise RuntimeError(f"没有可用的分布式 {kind} 节点")
            return await local_batch(urls)

        batches = [
            urls[index : index + policy.batch_size]
            for index in range(0, len(urls), policy.batch_size)
        ]
        limiter = asyncio.Semaphore(policy.dispatch_concurrency)

        async def _execute(batch: list[str]) -> dict[str, dict[str, Any]]:
            async with limiter:
                return await self._execute_batch(
                    policy=policy,
                    kind=kind,
                    urls=batch,
                    project_id=project_id,
                    task_id=task_id,
                    target_id=target_id,
                    timeout=timeout,
                    concurrency=concurrency,
                    local_batch=local_batch,
                )

        rows = await asyncio.gather(*(_execute(batch) for batch in batches))
        merged: dict[str, dict[str, Any]] = {}
        for row in rows:
            merged.update(row)
        return merged

    async def _execute_batch(
        self,
        *,
        policy: GatewayPolicy,
        kind: str,
        urls: list[str],
        project_id: str,
        task_id: str,
        target_id: str,
        timeout: float,
        concurrency: int,
        local_batch: BatchExecutor,
    ) -> dict[str, dict[str, Any]]:
        work, _ = await enqueue_work(
            self.db,
            WorkSpec(
                kind=kind,
                payload={
                    "urls": urls,
                    "timeout": timeout,
                    "concurrency": concurrency,
                    "verify_tls": False,
                    "ignore_https_errors": True,
                },
                project_id=project_id,
                task_id=task_id,
                target_id=target_id,
                requirements=(kind,),
                proxy_profile_id=policy.proxy_profile_id,
                proxy_mode=policy.proxy_mode,
                affinity_key=f"target:{target_id}" if target_id else "",
            ),
        )
        work_item_id = str(work["work_item_id"])
        deadline = asyncio.get_running_loop().time() + policy.wait_seconds
        while True:
            current = await work_dao.get_work(self.db, work_item_id)
            if not current:
                return await self._fallback_or_raise(
                    policy, local_batch, urls, "分布式工作项已丢失"
                )
            status = str(current.get("status") or "")
            if status == "completed":
                result = current.get("result") or {}
                items = result.get("items") if isinstance(result, dict) else None
                if isinstance(items, dict):
                    return {
                        str(key): dict(value)
                        for key, value in items.items()
                        if isinstance(value, dict)
                    }
                return await self._fallback_or_raise(
                    policy, local_batch, urls, "分布式节点返回格式无效"
                )
            if status in {"failed", "cancelled"}:
                error = current.get("last_error") or current.get("cancel_reason") or status
                return await self._fallback_or_raise(
                    policy, local_batch, urls, f"分布式工作失败: {error}"
                )
            if asyncio.get_running_loop().time() >= deadline:
                try:
                    await cancel_work(
                        self.db,
                        work_item_id=work_item_id,
                        reason="中心等待远端结果超时",
                    )
                except Exception:
                    pass
                return await self._fallback_or_raise(
                    policy, local_batch, urls, "等待分布式节点超时"
                )
            await asyncio.sleep(policy.poll_seconds)

    @staticmethod
    async def _fallback_or_raise(
        policy: GatewayPolicy,
        local_batch: BatchExecutor,
        urls: list[str],
        reason: str,
    ) -> dict[str, dict[str, Any]]:
        if policy.fallback_local and policy.proxy_mode != "required":
            logger.warning("%s，回退本机执行", reason)
            return await local_batch(urls)
        raise RuntimeError(reason)
