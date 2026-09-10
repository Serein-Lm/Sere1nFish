"""Publication-time policy for mobile list candidates."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from api.dao import mobile_collect as collect_dao


_PUBLISH_TIME_FIELDS = (
    "publish_time",
    "published_at",
    "publish_date",
    "published_time",
    "发布时间",
)


def candidate_publish_time(candidate: dict[str, Any]) -> datetime | None:
    fields = dict(candidate.get("fields") or {})
    value = next(
        (
            fields.get(key)
            for key in _PUBLISH_TIME_FIELDS
            if fields.get(key) not in (None, "")
        ),
        None,
    )
    return collect_dao.parse_record_publish_time(value)


def candidate_age_rejection(
    candidate: dict[str, Any],
    *,
    max_age_days: int,
    reference: datetime | None = None,
) -> str | None:
    if max_age_days <= 0:
        return None
    published_at = candidate_publish_time(candidate)
    if published_at is None:
        return None
    now = reference or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now.astimezone(timezone.utc) - timedelta(days=max_age_days)
    if published_at < cutoff:
        return f"发布时间早于最近 {max_age_days} 天窗口"
    return None
