"""Evidence-based legacy repair; dry-run first, no requeue of project tasks."""
import asyncio
from datetime import datetime

from api.dao import collection_reconciliation as dao
from api.services.source_documents.recovery_proof import verify_archived_version


async def build_reconciliation_plan(db, storage, *, cutoff: datetime):
    candidates = await dao.legacy_candidates(db, cutoff=cutoff)
    actions, skipped = [], []
    for task in candidates["mobile"]:
        if not isinstance(task.get("result"), dict):
            skipped.append({"kind": "mobile", "id": task["task_id"], "reason": "missing_result"})
            continue
        actions.append({"kind": "mobile_partial", "id": task["task_id"], "snapshot": task})
    for crawl in candidates["crawls"]:
        parent = await dao.find_crawl_parent(db, crawl)
        if not parent or parent.get("status") not in {"paused", "error", "completed", "cancelled"}:
            skipped.append({"kind": "crawl", "id": crawl["crawl_task_id"], "reason": "parent_not_terminal"})
            continue
        actions.append({"kind": "crawl_interrupted", "id": crawl["crawl_task_id"], "snapshot": crawl,
                        "cutoff": cutoff,
                        "parent": {key: parent.get(key) for key in ("task_id", "status", "updated_at")}})
    semaphore = asyncio.Semaphore(4)
    async def verify(version):
        async with semaphore:
            proof = await verify_archived_version(version, storage)
        return {"kind": "version_readable" if proof["readable"] else "version_interrupted",
                "id": version["version_id"], "snapshot": version, "proof": proof}
    actions.extend(await asyncio.gather(*(verify(v) for v in candidates["versions"])))
    return {"actions": actions, "skipped": skipped}


def public_plan(plan):
    return {"actions": [{key: value for key, value in action.items() if key != "snapshot"}
                        for action in plan["actions"]], "skipped": plan["skipped"]}
