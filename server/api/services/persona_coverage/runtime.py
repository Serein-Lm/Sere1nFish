"""Leased, recoverable queue for automatic industry and persona research."""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import timedelta

from api.dao import persona_coverage as dao
from api.dao import persona_research_tasks as task_dao
from core.background import spawn_background
from core.logger import get_logger
from .catalog import default_background
from .organizations import research_organizations
from .service import coverage, profile_ready

logger = get_logger("persona_coverage")
_workers: list[asyncio.Task] = []


async def _generate(db, app_config, job: dict, owner: str) -> list[str]:
    from api.services.persona_collect import generate_personas
    people = await dao.industry_people(db, job["industry_code"])
    ready = [item["person_id"] for item in people if profile_ready(item)]
    missing = max(0, job["minimum_personas"] - len(ready))
    if not missing:
        return ready
    task_id = job["job_id"] + "_" + uuid.uuid4().hex[:12]
    await task_dao.create_task(db, task_id=task_id, task_type="generate", requested_count=missing)
    await dao.update_job(db, job["job_id"], owner, research_task_id=task_id)
    try:
        result = await generate_personas(db, app_config, background=default_background([job["industry_name"]]), count=missing,
            industries=[job["industry_name"]], extra=f"本轮所有人设的 industry 必须为 {job['industry_name']}，对应行业大类 {job['industry_code']}。已经覆盖 {len(ready)} 条，本轮补齐不同岗位，保留至少 4 个核验来源。", task_id=task_id, source="synthetic_research:industry_coverage")
        people = [item for item in result.get("items", []) if profile_ready(item) and str(item.get("industry") or "").strip() == job["industry_name"]]
        ids = [item["person_id"] for item in people]
        await dao.classify_persons(db, ids, industry_code=job["industry_code"], sector_code=job["sector_code"], job_id=job["job_id"])
        await task_dao.update_task(db, task_id, status="completed", stage="completed", completed_count=len(ids), result_person_ids=ids, failed_count=max(0, missing - len(ids)), message=f"行业补采已生成 {len(ids)} 条完整人设")
        return [*ready, *ids]
    except BaseException as exc:
        with contextlib.suppress(Exception):
            await task_dao.update_task(db, task_id, status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed", stage="interrupted" if isinstance(exc, asyncio.CancelledError) else "failed", error=str(exc)[:500], message="行业研究保留进度，由覆盖队列重试")
        raise


async def _execute(db, app_config, job: dict, owner: str) -> None:
    await dao.update_job(db, job["job_id"], owner, stage="researching", message="正在研究行业人设、机构与公开办公联系方式")
    async def personas():
        ids = await _generate(db, app_config, job, owner)
        await dao.update_job(db, job["job_id"], owner, person_ids=ids)
        return ids
    async def organizations():
        facts = await dao.list_facts(db, job["industry_code"], limit=100)
        if facts["items"] and any(item.get("office_phone") for item in facts["items"]):
            return [item["fact_id"] for item in facts["items"]]
        ids = await research_organizations(db, app_config, job)
        await dao.update_job(db, job["job_id"], owner, fact_ids=ids)
        return ids
    results = await asyncio.gather(personas(), organizations(), return_exceptions=True)
    errors = [str(item)[:600] for item in results if isinstance(item, Exception)]
    report = await coverage(db)
    row = next(item for item in report["items"] if item["code"] == job["industry_code"])
    gaps = row["gaps"]
    retry = bool(gaps) and job["attempts"] < 3
    await dao.update_job(db, job["job_id"], owner, status="completed" if not gaps else "retry" if retry else "needs_sources", stage="completed" if not gaps else "awaiting_sources", gaps=gaps, errors=errors, message="行业资料已达到覆盖要求" if not gaps else "；".join(gaps), completed_at=dao.now() if not gaps else None, next_attempt_at=dao.now() + (timedelta(minutes=15 * job["attempts"]) if retry else timedelta(days=1)))


async def _run_leased(db, app_config, job: dict, owner: str) -> None:
    async def renew():
        while True:
            await asyncio.sleep(30)
            if not await dao.heartbeat(db, job["job_id"], owner):
                raise RuntimeError("行业研究租约已失效")
    work = spawn_background(_execute(db, app_config, job, owner), name=f"coverage:{job['job_id']}")
    renewal = spawn_background(renew(), name=f"coverage_lease:{job['job_id']}")
    try:
        done, _ = await asyncio.wait({work, renewal}, timeout=1800, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            raise TimeoutError("行业研究超过 30 分钟，将保存进度并重试")
        for task in done:
            task.result()
    except asyncio.CancelledError:
        await dao.update_job(db, job["job_id"], owner, status="queued", stage="interrupted", message="服务重启后自动继续", next_attempt_at=dao.now(), lease_until=dao.now())
        raise
    except Exception as exc:
        await dao.update_job(db, job["job_id"], owner, status="retry" if job["attempts"] < 3 else "needs_sources", stage="awaiting_sources", errors=[str(exc)[:600]], message="自动研究暂未完成，已保留进度", next_attempt_at=dao.now() + (timedelta(minutes=15) if job["attempts"] < 3 else timedelta(days=1)))
    finally:
        for task in (work, renewal):
            if not task.done():
                task.cancel()
        await asyncio.gather(work, renewal, return_exceptions=True)


async def _worker() -> None:
    from api.db.mongodb import get_db
    from api.services.runtime_config import get_runtime_app_config, get_runtime_config_section
    owner = uuid.uuid4().hex
    while True:
        try:
            config = await get_runtime_config_section("persona_coverage")
            if config.get("enabled"):
                await dao.requeue_sources_due(get_db())
            job = await dao.claim(get_db(), owner) if config.get("enabled") else None
            if job:
                await _run_leased(get_db(), await get_runtime_app_config(), job, owner)
            else:
                await asyncio.sleep(15)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("行业覆盖队列运行失败: %s", exc)
            await asyncio.sleep(30)


def start_workers() -> None:
    if not _workers:
        _workers.extend(spawn_background(_worker(), name=f"persona_coverage_worker:{index}") for index in range(2))


async def stop_workers() -> None:
    for worker in _workers:
        worker.cancel()
    await asyncio.gather(*_workers, return_exceptions=True)
    _workers.clear()
