"""Bounded queue orchestration for batches of project tasks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from api.services.info_collection.streaming import (
    make_stream_items,
    run_stream_pipeline,
    stream_stage,
)
from core.logger import get_logger
from core.observability import obs_log
from core.stream import Context, Item, Stage


logger = get_logger("project_task_batch")

MAX_COMPANY_SCAN_BATCH_SIZE = 200


@dataclass(frozen=True)
class ProjectTaskJob:
    task_id: str
    project_id: str
    task_type: str
    params: dict[str, Any]


TaskExecutor = Callable[[str, str, str, dict[str, Any]], Awaitable[None]]


def parse_company_names(value: Any) -> list[str]:
    """Normalize a line-oriented company list while preserving input order."""
    if isinstance(value, str):
        candidates: Iterable[Any] = value.splitlines()
    elif isinstance(value, Iterable):
        candidates = value
    else:
        candidates = []

    names: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        name = str(item or "").strip()
        identity = name.casefold()
        if not name or identity in seen:
            continue
        seen.add(identity)
        names.append(name)
    return names


class ProjectTaskBatchStage(Stage):
    """Run independent project tasks through a bounded worker queue."""

    name = "execute_project_task"

    def __init__(self, executor: TaskExecutor, *, concurrency: int) -> None:
        self._executor = executor
        super().__init__(concurrency=max(1, int(concurrency)))

    async def handle(self, item: Item, _ctx: Context) -> None:
        job = item.payload
        if not isinstance(job, ProjectTaskJob):
            raise TypeError("批量项目任务必须使用 ProjectTaskJob")
        await self._executor(
            job.task_id,
            job.project_id,
            job.task_type,
            job.params,
        )


async def _notify_company_batch_completion(
    *,
    batch_id: str,
    project_id: str,
) -> None:
    from api.dao import tasks as tasks_dao
    from api.db.mongodb import get_db
    from api.services.notifications import notify_company_scan_batch_completed

    db = get_db()
    claim = await tasks_dao.claim_completed_batch_notification(db, batch_id=batch_id)
    if not claim:
        return
    task_documents, owner_task_id, claim_token = claim
    try:
        dispatched = await notify_company_scan_batch_completed(
            project_id=project_id,
            batch_id=batch_id,
            tasks=task_documents,
        )
        if dispatched.ok or dispatched.skipped:
            await tasks_dao.complete_batch_notification_claim(
                db,
                owner_task_id=owner_task_id,
                claim_token=claim_token,
            )
            return
        await tasks_dao.release_batch_notification_claim(
            db,
            owner_task_id=owner_task_id,
            claim_token=claim_token,
        )
        logger.warning("批次完成通知发送失败，已释放重试锁 | batch=%s", batch_id)
    except Exception:
        await tasks_dao.release_batch_notification_claim(
            db,
            owner_task_id=owner_task_id,
            claim_token=claim_token,
        )
        logger.exception("批次完成通知异常，已释放重试锁 | batch=%s", batch_id)


async def run_project_task_batch(
    *,
    batch_id: str,
    project_id: str,
    jobs: list[ProjectTaskJob],
    executor: TaskExecutor,
    concurrency: int,
    dispatch_concurrency: int | None = None,
    aggregate_notification: bool = False,
) -> None:
    """Execute a task batch with queue backpressure and bounded concurrency."""
    if not jobs:
        return

    core_concurrency = max(1, min(int(concurrency or 1), len(jobs)))
    bounded_concurrency = max(
        1,
        min(
            int(dispatch_concurrency or core_concurrency),
            len(jobs),
            MAX_COMPANY_SCAN_BATCH_SIZE,
        ),
    )
    obs_log(
        "批量项目任务启动",
        project_id=project_id,
        source="project_task_batch",
        level="notice",
        event="batch_start",
        data={
            "batch_id": batch_id,
            "task_count": len(jobs),
            "concurrency": bounded_concurrency,
            "core_concurrency": core_concurrency,
        },
    )
    logger.notice(
        "批量项目任务启动 | batch=%s project=%s tasks=%s dispatch=%s core=%s",
        batch_id,
        project_id,
        len(jobs),
        bounded_concurrency,
        core_concurrency,
    )

    await run_stream_pipeline(
        stages=[
            stream_stage(
                ProjectTaskBatchStage(
                    executor,
                    concurrency=bounded_concurrency,
                )
            )
        ],
        seeds=make_stream_items(
            jobs,
            indexed=True,
            meta_builder=lambda job, _idx, _total: {
                "task_id": job.task_id,
                "batch_id": batch_id,
            },
        ),
        entry=ProjectTaskBatchStage.name,
        state={"batch_id": batch_id, "project_id": project_id},
        pipeline_id=f"task-batch:{batch_id}",
    )

    if aggregate_notification:
        await _notify_company_batch_completion(
            batch_id=batch_id,
            project_id=project_id,
        )

    obs_log(
        "批量项目任务结束",
        project_id=project_id,
        source="project_task_batch",
        level="notice",
        event="batch_done",
        data={"batch_id": batch_id, "task_count": len(jobs)},
    )
    logger.notice(
        "批量项目任务结束 | batch=%s project=%s tasks=%s",
        batch_id,
        project_id,
        len(jobs),
    )
