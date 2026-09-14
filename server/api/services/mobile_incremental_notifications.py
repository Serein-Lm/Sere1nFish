"""One bulletin and notification entry for live collection and source recovery."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from api.dao import mobile_incremental_events as dao
from api.services.mobile_incremental import utc_time
from api.services.notifications import notify_event
from core.mobile.collect.candidate_time import candidate_publish_time, candidate_window_status


def _time(value) -> str:
    parsed = utc_time(value)
    return parsed.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S") if parsed else "未提供"


def _plain(value, limit: int = 300) -> str:
    return str(value or "").replace("\n", " ").strip()[:limit]


def _content_summary(fields: dict) -> str:
    """A title, date or success flag alone is not collected information."""
    empty_values = {"", "无", "暂无", "暂无信息", "无信息", "暂无内容", "暂无数据", "无新增", "未发现新增", "未找到", "未知", "未提供", "未获取", "none", "null", "n/a", "na", "-", "--", "采集内容"}
    for key in ("summary", "摘要", "content", "正文", "background", "项目背景", "contact", "联系方式", "phone", "电话", "联系电话", "email", "邮箱", "wechat", "微信号"):
        value = fields.get(key)
        if not isinstance(value, str):
            continue
        text = _plain(value, 600)
        if text.rstrip("。.!！：: ").casefold() not in empty_values:
            return text
    return ""


def build_event(*, payload: dict, state: dict, now: datetime | None = None) -> dict | None:
    fields = dict(payload.get("fields") or {})
    summary = _content_summary(fields)
    if not summary:
        return None
    target = state.get("target") or {}
    target_id = str(payload.get("target_id") or target.get("target_id") or "")
    window = state.get("incremental_window")
    if payload.get("source_archive_status") in {"pending", "rejected", "failed", "error"}:
        return None
    if window and not (payload.get("source_document_id") and payload.get("source_document_version_id") and payload.get("source_archive_status") == "ready"):
        return None
    if window and candidate_window_status(payload, window, target_id) != "inside":
        return None
    if payload.get("kind") not in {"new", "changed"}:
        return None
    version = payload.get("source_document_version_id") or hashlib.sha256(
        json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()
    project_id = str(state.get("project_id") or "")
    identity = [project_id, target_id, payload.get("source_document_id") or payload["record_id"], version]
    event_id = "mie_" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]
    source_url = str(payload.get("source_url") or "")
    try:
        if urlsplit(source_url).scheme not in {"http", "https"}:
            source_url = ""
    except ValueError:
        source_url = ""
    scope = (window or {}).get("targets", {}).get(target_id, {})
    return {
        "event_id": event_id, "project_id": project_id, "target_id": target_id,
        "target_name": _plain(payload.get("target_name") or target.get("canonical_name") or state.get("task_name"), 200),
        "task_def_id": state["task_def_id"], "run_task_id": state["run_task_id"],
        "record_id": payload["record_id"], "kind": payload["kind"],
        "title": _plain(fields.get("title") or fields.get("标题") or fields.get("article_title") or summary[:60]),
        "summary": summary,
        "source_url": source_url, "source_document_version_id": str(version),
        "published_at": candidate_publish_time(payload),
        "published_label": _plain(next((fields[key] for key in ("publish_time", "published_at", "publish_date", "published_time", "发布时间") if fields.get(key)), "")),
        "detected_at": now or datetime.now(timezone.utc),
        "window_since": utc_time(scope.get("since")), "window_until": utc_time((window or {}).get("until")),
        "delivery_status": "pending",
    }


def notification_content(event: dict, totals: dict) -> dict:
    label = "新增" if event["kind"] == "new" else "内容变化"
    counts = " · ".join(f"{name} **{totals[key]}** 条" for key, name in (("new", "新增"), ("changed", "变化")) if totals.get(key, 0) > 0)
    lines = [f"### 本轮{counts}", "",
             f"**单位：{event['target_name']}**", f"**本条{label}：{event['title']}**", "",
             f"- 文章发布时间：{event['published_label'] or '来源未提供'}",
             f"- 发现时间：{_time(event['detected_at'])}（北京时间）"]
    if event.get("window_since"):
        lines.append(f"- 本轮时间范围：{_time(event['window_since'])} 至 {_time(event['window_until'])}")
    if event["summary"]:
        lines.extend(["", event["summary"]])
    if event["source_url"]:
        url = event["source_url"].replace("(", "%28").replace(")", "%29").replace("\n", "")
        lines.extend(["", f"[打开{label}资料]({url})"])
    return {"title": f"🔔【增量通报 · {label}】{event['target_name']}", "content": "\n".join(lines)}


async def record_increment(db, *, payload: dict, state: dict) -> dict | None:
    """Persist before the keyword checkpoint can acknowledge its data."""
    event = build_event(payload=payload, state=state)
    if not event:
        return None
    await dao.insert_event(db, event)
    return event


async def publish_increment(db, *, payload: dict, state: dict) -> dict | None:
    event = await record_increment(db, payload=payload, state=state)
    if not event:
        return None
    claimed = await dao.claim_delivery(db, event["event_id"])
    if not claimed:
        return event
    totals = await dao.counts(db, {"project_id": claimed["project_id"], "run_task_id": claimed["run_task_id"], "target_id": claimed["target_id"]})
    try:
        result = await notify_event(
            event="mobile_collect_incremental", **notification_content(claimed, totals),
            level="notice", source="mobile_collect", project_id=claimed["project_id"],
            task_id=claimed["run_task_id"], context={"event_id": claimed["event_id"], "target_id": claimed["target_id"], "record_id": claimed["record_id"]},
        )
        channels = getattr(result, "results", [])
        skipped = result.skipped or (bool(channels) and all(item.skipped for item in channels))
        status = "skipped" if skipped else "sent" if result.ok else "failed"
    except Exception:
        await dao.finish_delivery(db, event["event_id"], status="failed")
        raise
    await dao.finish_delivery(db, event["event_id"], status=status)
    return claimed


async def list_incremental_feed(db, *, project_id: str = "", after=None, limit: int = 50) -> dict:
    return await dao.feed(db, project_id=project_id.strip(), after=utc_time(after), limit=limit)


async def publish_recovered_increment(db, *, previous: dict, result: dict, stored: dict, task_def: dict) -> None:
    """A recovered pending link becomes a new, verified item after archival."""
    from api.dao.mobile_incremental import get_run_window

    first_archive = not previous.get("source_document_version_id")
    kind = "new" if first_archive else "changed"
    if not first_archive and not stored.get("is_changed"):
        return
    if task_def.get("notify_on") not in {kind, "both"}:
        return
    run_id = str(previous.get("latest_run_task_id") or "")
    window = await get_run_window(db, run_id) if task_def.get("incremental_by_time") else None
    if task_def.get("incremental_by_time") and not window:
        return
    await publish_increment(db, payload={
        **previous, "fields": {**dict(previous.get("fields") or {}), **dict(result.get("fields") or {})},
        "record_id": stored["record_id"], "kind": kind,
        "source_archive_status": "ready", "source_document_id": result.get("document_id"),
        "source_document_version_id": result.get("version_id"),
    }, state={
        "project_id": task_def.get("project_id") or previous.get("project_id") or "",
        "task_def_id": task_def["task_def_id"], "run_task_id": run_id,
        "task_name": task_def.get("name"), "incremental_window": window,
    })
