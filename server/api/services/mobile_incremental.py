"""Resolve and commit publication-time windows for the mobile runtime."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from api.dao import mobile_incremental as dao


def utc_time(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def target_scopes(seed_specs: list[dict], channel: str) -> dict[str, str]:
    keywords: dict[str, set[str]] = {}
    for spec in seed_specs:
        target_id = str(spec.get("target_id") or (spec.get("target") or {}).get("target_id") or "")
        if not target_id:
            raise ValueError("按时间增量采集必须关联稳定 Target")
        keywords.setdefault(target_id, set()).add(str(spec.get("keyword") or ""))
    return {
        target_id: hashlib.sha256(json.dumps([channel, sorted(words)], ensure_ascii=False).encode()).hexdigest()[:24]
        for target_id, words in keywords.items()
    }


async def prepare_window(plan: Any, seeds: Any, *, now: datetime | None = None) -> dict | None:
    if not plan.task_def.get("incremental_by_time"):
        return None
    if not plan.project_id:
        raise ValueError("按时间增量采集必须关联项目")
    checkpoint_key = f"time-window-v1:{seeds.definition_fingerprint}"
    if not plan.dry_run:
        saved = await dao.get_window(plan.db, plan.run_task_id, checkpoint_key)
        if saved:
            return saved
    until = utc_time(now or datetime.now(timezone.utc))
    scopes = target_scopes(seeds.seed_specs, str(plan.task_def.get("source_link_strategy") or plan.task_def.get("app_name") or "mobile"))
    states = await asyncio.gather(*(dao.get_target_state(plan.db, plan.project_id, tid) for tid in scopes))
    targets = {}
    for (tid, scope), state in zip(scopes.items(), states):
        baseline = dict(state.get("mobile_incremental_baseline") or {})
        cursor = (state.get("mobile_incremental_cursors") or {}).get(scope) or {}
        since = utc_time(cursor.get("through_at") or baseline.get("since") or plan.task_def.get("incremental_since"))
        if since is None:
            since = until - timedelta(days=45)
            baseline = {"reason": "first_run_45_day_window"}
        if since > until:
            raise ValueError("增量起点不能晚于本轮采集时间")
        targets[tid] = {"scope_key": scope, "since": since, "baseline": baseline}
    window = {
        "run_task_id": plan.run_task_id, "checkpoint_key": checkpoint_key,
        "task_def_id": plan.task_def_id, "project_id": plan.project_id,
        "kind": "publication_time_window", "version": 1, "until": until,
        "overlap_hours": int(plan.task_def.get("incremental_overlap_hours", 24)),
        "targets": targets,
    }
    return window if plan.dry_run else await dao.save_window(plan.db, window)


async def complete_window(execution: Any) -> bool:
    state, plan = execution.state, execution.plan
    window = state.get("incremental_window")
    counters = state.get("counters") or {}
    if (not window or plan.dry_run or execution.timed_out or state["stop_event"].is_set()
        or counters.get("failed") or counters.get("persist_failed") or counters.get("screen_errors")
        or counters.get("time_unverified")
        or counters.get("time_coverage_incomplete")
        or int(state.get("keywords_completed") or 0) < int(state.get("keyword_total") or 0)):
        return False
    # Also inspect completed checkpoints on resume, so earlier partial screens or
    # unknown dates cannot disappear when process-local counters are rebuilt.
    if not await completed_without_gaps(plan.db, plan.run_task_id, execution.seeds):
        return False
    await asyncio.gather(*(dao.advance_cursor(
        plan.db, project_id=plan.project_id, target_id=tid, scope_key=target["scope_key"],
        through_at=utc_time(window["until"]), run_task_id=plan.run_task_id,
    ) for tid, target in window["targets"].items()))
    return True


async def completed_without_gaps(db: Any, run_task_id: str, seeds: Any) -> bool:
    rows = await asyncio.gather(*(dao.get_window(db, run_task_id, spec["checkpoint_key"]) for spec in seeds.seed_specs))
    return bool(rows) and all(
        row and row.get("status") == "completed"
        and not (row.get("stats") or {}).get("screen_errors")
        and not (row.get("stats") or {}).get("time_unverified")
        and not (row.get("stats") or {}).get("time_coverage_incomplete")
        for row in rows
    )
