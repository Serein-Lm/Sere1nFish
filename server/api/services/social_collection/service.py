"""Social place collection orchestration built on the mobile collection runtime."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from api.dao import mobile_collect as mobile_collect_dao
from api.dao import projects as projects_dao
from api.dao import social_collection as social_dao
from api.db.collections import TASKS_COLLECTION
from api.models.mobile_collect import CollectTaskDef
from api.models.social_collection import SocialCollectionRequest
from api.services.social_collection.platforms import SocialPlatformRegistry
from core.background import spawn_background
from core.logger import get_logger


logger = get_logger("social_collection")
_TASK_TYPE = "social_media_collect"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _platform_result_status(result: dict[str, Any]) -> str:
    partial = bool(
        result.get("timed_out")
        or result.get("stopped")
        or int(result.get("media_failed") or 0) > 0
    )
    return "partial" if partial else "completed"


def compile_social_collection_plan(
    request: SocialCollectionRequest,
) -> dict[str, Any]:
    """Compile platform-specific task definitions without DB or device access."""
    tasks: list[dict[str, Any]] = []
    for platform in request.platforms:
        adapter = SocialPlatformRegistry.resolve(platform)
        validated = CollectTaskDef(**adapter.build_task(request)).model_dump()
        tasks.append(
            {
                "platform": platform,
                "label": adapter.label,
                "app_name": adapter.app_name,
                "keywords": list(validated.get("keywords") or []),
                "task_definition": validated,
            }
        )
    return {
        "project_id": request.project_id,
        "place_name": request.place_name,
        "device_id": request.device_id,
        "platforms": list(request.platforms),
        "task_count": len(tasks),
        "tasks": tasks,
        "execution": "sequential_on_one_device",
        "storage": {
            "context": "private OSS full mobile screenshot",
            "media": "private OSS lossless crop",
            "capture_fidelity": "screen_render_crop",
            "source_original_available": False,
        },
        "will_operate_device": False,
    }


async def _validate_runtime_references(
    db: AsyncIOMotorDatabase,
    request: SocialCollectionRequest,
) -> None:
    if not await projects_dao.get_project(db, request.project_id):
        raise ValueError(f"项目不存在: {request.project_id}")
    if request.target_id:
        from api.dao import targets as targets_dao

        if not await targets_dao.get_target(db, request.target_id):
            raise ValueError(f"Target 不存在: {request.target_id}")
    from api.services.mobile_status import get_mobile_device_status

    device = await get_mobile_device_status(db, request.device_id)
    if not device:
        raise ValueError(
            f"设备不存在: {request.device_id}；请先调用 list_mobile_devices 获取稳定标识"
        )
    if not bool(device.get("online")):
        raise ValueError(f"设备当前离线: {request.device_id}")


async def create_social_collection_job(
    db: AsyncIOMotorDatabase,
    request: SocialCollectionRequest,
    *,
    requested_by: str,
    start: bool = True,
) -> dict[str, Any]:
    """Persist one idempotent parent job and optionally enqueue it."""
    if request.request_key:
        existing = await social_dao.find_active_job_by_request_key(
            db,
            request.request_key,
            project_id=request.project_id,
        )
        if existing:
            return {**existing, "reused": True}
    await _validate_runtime_references(db, request)

    plan = compile_social_collection_plan(request)
    job_id = "scj_" + uuid.uuid4().hex[:20]
    parent_task_id = uuid.uuid4().hex[:12]
    platform_tasks: list[dict[str, Any]] = []
    created_definitions: list[str] = []
    job_created = False
    try:
        for item in plan["tasks"]:
            definition = {
                **dict(item["task_definition"]),
                "social_collection_job_id": job_id,
                "parent_task_id": parent_task_id,
            }
            task_def = await mobile_collect_dao.create_task_def(db, definition)
            task_def_id = str(task_def.get("task_def_id") or "")
            created_definitions.append(task_def_id)
            platform_tasks.append(
                {
                    "platform": item["platform"],
                    "label": item["label"],
                    "app_name": item["app_name"],
                    "task_def_id": task_def_id,
                    "keywords": item["keywords"],
                    "status": "pending",
                }
            )

        job = await social_dao.create_job(
            db,
            job_id=job_id,
            payload=request.model_dump(),
            platform_tasks=platform_tasks,
            parent_task_id=parent_task_id,
            requested_by=requested_by,
        )
        job_created = True
        params = {
            "job_id": job_id,
            "_requested_by": requested_by,
        }
        now = _now()
        await db[TASKS_COLLECTION].insert_one(
            {
                "task_id": parent_task_id,
                "project_id": request.project_id,
                "task_type": _TASK_TYPE,
                "params": {"job_id": job_id},
                "requested_by": requested_by,
                "status": "pending",
                "progress": {
                    "stage": "queued",
                    "message": "社交地点图片采集已排队，等待设备租约",
                },
                "created_at": now,
                "updated_at": now,
            }
        )
    except Exception as exc:
        await db[TASKS_COLLECTION].delete_one({"task_id": parent_task_id})
        if job_created:
            await social_dao.delete_job(db, job_id)
        for task_def_id in created_definitions:
            try:
                await mobile_collect_dao.delete_task_def(db, task_def_id)
            except Exception:  # noqa: BLE001
                pass
        if isinstance(exc, DuplicateKeyError) and request.request_key:
            existing = await social_dao.find_active_job_by_request_key(
                db,
                request.request_key,
                project_id=request.project_id,
            )
            if existing:
                return {**existing, "reused": True}
        raise

    if start:
        from api.services.project_task_runtime import execute_project_task

        spawn_background(
            execute_project_task(
                parent_task_id,
                request.project_id,
                _TASK_TYPE,
                params,
            ),
            name=f"social-collection:{parent_task_id}",
        )
    return {**job, "task_id": parent_task_id, "reused": False}


async def execute_social_collection_job(
    task_id: str,
    project_id: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Task dispatcher: serialize platform definitions on the selected device."""
    from api.db.mongodb import get_db
    from api.services.mobile_collect_pipeline import run_mobile_collect_definition

    db = get_db()
    job_id = str(params.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("社交采集任务缺少 job_id")
    job = await social_dao.set_job_running(
        db,
        job_id=job_id,
        parent_task_id=task_id,
    )
    if not job:
        existing = await social_dao.get_job(db, job_id)
        if existing and existing.get("status") in {"completed", "partial"}:
            return dict(existing.get("result") or {})
        raise ValueError(f"社交采集 Job 不存在或不可运行: {job_id}")

    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    partial_platforms: list[str] = []
    try:
        for platform_task in job.get("platform_tasks") or []:
            platform = str(platform_task.get("platform") or "")
            task_def_id = str(platform_task.get("task_def_id") or "")
            if platform_task.get("status") in {"completed", "partial"}:
                if platform_task.get("status") == "partial":
                    partial_platforms.append(platform)
                results.append(
                    {
                        "platform": platform,
                        "platform_status": platform_task.get("status"),
                        **dict(platform_task.get("result") or {}),
                    }
                )
                continue
            await social_dao.update_platform_status(
                db,
                job_id=job_id,
                platform=platform,
                status="running",
            )
            child_run_id = f"{task_id}_{platform}"
            try:
                result = await run_mobile_collect_definition(
                    db,
                    run_task_id=child_run_id,
                    project_id=project_id,
                    task_def_id=task_def_id,
                    runtime_overrides={
                        "social_collection_job_id": job_id,
                        "parent_task_id": task_id,
                    },
                    requested_by=str(
                        params.get("_requested_by") or job.get("requested_by") or ""
                    ),
                    queue_priority="normal",
                )
                platform_status = _platform_result_status(result)
                if platform_status == "partial":
                    partial_platforms.append(platform)
                item = {
                    "platform": platform,
                    "platform_status": platform_status,
                    **result,
                }
                results.append(item)
                await social_dao.update_platform_status(
                    db,
                    job_id=job_id,
                    platform=platform,
                    status=platform_status,
                    result=result,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "社交平台采集失败 | job=%s platform=%s error=%s",
                    job_id,
                    platform,
                    exc,
                )
                failures.append({"platform": platform, "error": str(exc)})
                await social_dao.update_platform_status(
                    db,
                    job_id=job_id,
                    platform=platform,
                    status="error",
                    error=str(exc),
                )
    except asyncio.CancelledError:
        media_count = await social_dao.count_job_media(db, job_id)
        await social_dao.mark_job_paused(
            db,
            job_id=job_id,
            media_count=media_count,
        )
        raise
    except Exception as exc:
        media_count = int((job.get("progress") or {}).get("media_count") or 0)
        try:
            media_count = await social_dao.count_job_media(db, job_id)
        except Exception as count_exc:  # noqa: BLE001
            logger.warning(
                "社交采集异常终结时统计媒体失败 | job=%s error=%s",
                job_id,
                count_exc,
            )
        failure = {"platform": "orchestration", "error": str(exc)[:2000]}
        failures.append(failure)
        terminal_status = "partial" if results else "error"
        terminal_result = {
            "job_id": job_id,
            "status": terminal_status,
            "place_name": job.get("place_name") or "",
            "platform_results": results,
            "failures": failures,
            "partial_platforms": list(dict.fromkeys(partial_platforms)),
            "media_count": media_count,
        }
        try:
            await social_dao.finish_job(
                db,
                job_id=job_id,
                status=terminal_status,
                result=terminal_result,
            )
        except Exception as finalize_exc:  # noqa: BLE001
            logger.error(
                "社交采集异常终结写入失败 | job=%s task=%s error=%s",
                job_id,
                task_id,
                finalize_exc,
            )
        raise

    media_count = await social_dao.count_job_media(db, job_id)
    status = (
        "error"
        if not results
        else ("partial" if failures or partial_platforms else "completed")
    )
    result = {
        "job_id": job_id,
        "status": status,
        "place_name": job.get("place_name") or "",
        "platform_results": results,
        "failures": failures,
        "partial_platforms": list(dict.fromkeys(partial_platforms)),
        "media_count": media_count,
    }
    await social_dao.finish_job(db, job_id=job_id, status=status, result=result)
    if status == "error":
        raise RuntimeError(
            "社交地点图片采集全部失败: "
            + "; ".join(item["error"] for item in failures)
        )
    return result
