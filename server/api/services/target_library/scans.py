"""Join actual runs to institutions without confusing schedules and executions."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from api.dao.targets import normalize_target_name


def time_key(value) -> str:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.astimezone(timezone.utc).isoformat()
    return str(value or "")


def scan_index(index: dict, tasks: list[dict], definitions: list[dict], windows: list[dict]) -> dict:
    mapping = index["mapping"]
    definitions_by_id = {item["task_def_id"]: item for item in definitions}
    windows_by_run = {item["run_task_id"]: item for item in windows}
    associations: dict[str, set[str]] = defaultdict(set)
    names: dict[str, set[str]] = defaultdict(set)
    for item in index["targets"]:
        for name in [item.get("canonical_name"), *(item.get("identity_aliases") or [])]:
            if name:
                names[normalize_target_name(name)].add(mapping[item["target_id"]])
    for anchor, relations in index["relations"].items():
        for relation in relations:
            for run_id in relation.get("run_task_ids") or []:
                associations[run_id].add(anchor)
    result: dict[str, list] = defaultdict(list)
    for task in tasks:
        params = task.get("params") or {}
        definition = definitions_by_id.get(params.get("task_def_id"), {})
        window = windows_by_run.get(task["task_id"]) or (task.get("result") or {}).get("incremental_window") or {}
        ids = {params.get("target_id"), definition.get("target_id"), ((task.get("result") or {}).get("identity") or {}).get("target_id"), *(definition.get("target_ids") or []), *(window.get("targets") or {})} - {None, ""}
        anchors = {mapping[key] for key in ids if key in mapping} | associations[task["task_id"]]
        basis = "stable_target_id"
        if not anchors and params.get("company_name"):
            candidates = names.get(normalize_target_name(params["company_name"]), set())
            scoped = {key for key in candidates if any(item["project_id"] == task.get("project_id") for item in index["rows"][key]["projects"])}
            if len(scoped or candidates) == 1:
                anchors = scoped or candidates
                basis = "unambiguous_complete_name"
        for anchor in anchors:
            scoped = {key: value for key, value in (window.get("targets") or {}).items() if mapping.get(key) == anchor}
            result[anchor].append({
                **{key: value for key, value in task.items() if key not in {"params", "result"}},
                "incremental": bool(window or params.get("incremental_scan") or definition.get("incremental_by_time")),
                "target_resolution_basis": basis,
                "incremental_until": window.get("until"), "incremental_targets": scoped,
                "cursor_advanced": (task.get("result") or {}).get("incremental_cursor_advanced") is True,
            })
    for runs in result.values():
        runs.sort(key=lambda item: (time_key(item.get("created_at")), item["task_id"]), reverse=True)
    return result


def scan_summary(row: dict, relations: list[dict], runs: list[dict]) -> dict:
    starts = [item["started_at"] for item in runs if item.get("started_at") and (item.get("progress") or {}).get("stage") not in {"waiting_mobile", "waiting_core", "queued"}]
    completed = [item["completed_at"] for item in runs if item.get("completed_at") and item.get("status") == "completed"]
    incremental = [item for item in runs if item["incremental"]]
    scopes = []
    for relation in relations:
        baseline = relation.get("mobile_incremental_baseline") or {}
        cursors = relation.get("mobile_incremental_cursors") or {}
        for key, cursor in cursors.items():
            scopes.append({"project_id": relation.get("project_id"), "target_id": relation["target_id"], "scope_key": key, "baseline_since": baseline.get("since"), **cursor})
        if baseline and not cursors:
            scopes.append({"project_id": relation.get("project_id"), "target_id": relation["target_id"], "baseline_since": baseline.get("since")})
    return {
        **row, "scan_count": len(runs),
        "first_scan_at": min(starts, key=time_key) if starts else None,
        "last_scan_at": max(starts, key=time_key) if starts else None,
        "last_success_at": max(completed, key=time_key) if completed else None,
        "latest_run": runs[0] if runs else None,
        "latest_incremental_run": incremental[0] if incremental else None,
        "incremental_scopes": scopes,
    }
